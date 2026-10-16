"""Run history and metrics: every pipeline run records what it did, so health is visible, not just failures.

Four tables in the raw schema, written by run.py (and the Airflow DAG, and the GitHub Actions run):

    raw.pipeline_runs     one row per run: trigger, target, status, duration, files found/loaded/rejected,
                          rows loaded, dbt models and tests, source freshness, the error if it failed
    raw.pipeline_steps    one row per step of a run, with its duration and status
    raw.dbt_results       every dbt node (seed, model, test) of the run, with its duration and status,
                          read from dbt's target/run_results.json
    raw.source_freshness  dbt source freshness for each raw table: newest _loaded_at, its age, pass/warn/error

The Warehouse page reads summary() of these (live, or from the snapshot's observability.json).
"""
import json
import os
import socket
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from warehouse import config, load

RUN_COLUMNS = ["run_id", "trigger", "target", "started_at", "finished_at", "seconds", "status", "files_found",
               "files_loaded", "files_rejected", "rows_loaded", "dbt_models", "dbt_tests", "dbt_tests_failed",
               "freshness", "error"]
HISTORY = 30


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def trigger_name():
    if os.environ.get("PIPELINE_TRIGGER"):
        return os.environ["PIPELINE_TRIGGER"]
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return "github-actions"
    if os.environ.get("AIRFLOW_CTX_DAG_ID"):
        return "airflow"
    return "command line"


def target_name(warehouse_path):
    return "MotherDuck" if config.is_motherduck(warehouse_path or config.WAREHOUSE_PATH) else "DuckDB file"


class Run:
    """Records one pipeline run. Opens a short connection for each write, so dbt can use the file in between."""

    def __init__(self, warehouse_path=None, run_id=None, trigger=None):
        self.warehouse_path = warehouse_path
        self.run_id = run_id or f"{now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
        self.trigger = trigger or trigger_name()
        self.counts = {}
        self.started = time.time()

    def _write(self, sql, params):
        con = load.connect(self.warehouse_path)
        try:
            con.execute(sql, params)
        finally:
            con.close()

    def start(self):
        """Adds the run as 'running'. Safe to call again for the same run_id (an Airflow retry)."""
        self._write("INSERT INTO raw.pipeline_runs (run_id, trigger, target, started_at, status) "
                    "SELECT ?, ?, ?, ?, 'running' WHERE NOT EXISTS (SELECT 1 FROM raw.pipeline_runs WHERE run_id = ?)",
                    [self.run_id, self.trigger, f"{target_name(self.warehouse_path)} on {socket.gethostname()}"[:120],
                     now(), self.run_id])
        return self

    def save(self, **counts):
        """Writes counts now (Airflow runs each step in its own process, so nothing is kept in memory)."""
        self.counts.update(counts)
        cols = [c for c in self.counts if c in RUN_COLUMNS]
        if cols:
            self._write(f"UPDATE raw.pipeline_runs SET {', '.join(f'{c} = ?' for c in cols)} WHERE run_id = ?",
                        [self.counts[c] for c in cols] + [self.run_id])

    @contextmanager
    def step(self, name):
        info = {"detail": None}
        began, t0 = now(), time.time()
        try:
            yield info
        except Exception as e:
            self._step_row(name, began, t0, "failed", info["detail"] or f"{type(e).__name__}: {e}"[:500])
            raise
        self._step_row(name, began, t0, "success", info["detail"])

    def _step_row(self, name, began, t0, status, detail):
        self._write("INSERT INTO raw.pipeline_steps VALUES (?, ?, ?, ?, ?, ?)",
                    [self.run_id, name, began, round(time.time() - t0, 2), status, detail])

    def record_dbt(self, results_path=None):
        """Copies dbt's run_results.json into raw.dbt_results. Returns (models, tests, failed tests)."""
        results = read_run_results(results_path)
        if not results:
            return None
        con = load.connect(self.warehouse_path)
        try:
            con.executemany("INSERT INTO raw.dbt_results VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [[self.run_id, r["unique_id"], r["unique_id"].split(".")[0], r.get("status"),
                              round(r.get("execution_time") or 0, 3), (r.get("adapter_response") or {}).get("rows_affected"),
                              (r.get("message") or "")[:300]] for r in results])
        finally:
            con.close()
        models = sum(1 for r in results if r["unique_id"].startswith("model."))
        tests = [r for r in results if r["unique_id"].startswith("test.")]
        failed = sum(1 for r in tests if r.get("status") not in ("pass", "success"))
        self.counts.update(dbt_models=models, dbt_tests=len(tests), dbt_tests_failed=failed)
        return models, len(tests), failed

    def record_freshness(self, checks):
        """checks: [{source_table, max_loaded_at, age_hours, status}] from transform.freshness()."""
        if not checks:
            self.counts["freshness"] = "not checked"
            return "not checked"
        con = load.connect(self.warehouse_path)
        try:
            con.executemany("INSERT INTO raw.source_freshness VALUES (?, ?, ?, ?, ?, ?)",
                            [[self.run_id, now(), c["source_table"], c.get("max_loaded_at"), c.get("age_hours"),
                              c.get("status")] for c in checks])
        finally:
            con.close()
        worst = next((s for s in ("error", "warn") if any(c.get("status") == s for c in checks)), "pass")
        self.counts["freshness"] = worst
        return worst

    def finish(self, status, error=None):
        sets = dict(self.counts, status=status, error=(error or None) and str(error)[:1000])
        cols = [c for c in sets if c in RUN_COLUMNS]
        end = now()
        self._write(f"UPDATE raw.pipeline_runs SET {', '.join(f'{c} = ?' for c in cols)}, finished_at = ?, "
                    "seconds = round(date_diff('millisecond', started_at, ?) / 1000.0, 1) WHERE run_id = ?",
                    [sets[c] for c in cols] + [end, end, self.run_id])


def read_run_results(path=None):
    path = path or os.path.join(config.DBT_DIR, "target", "run_results.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("results", [])


def rows(con, sql):
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, (plain(v) for v in r))) for r in cur.fetchall()]


def plain(v):
    if hasattr(v, "isoformat"):
        return v.isoformat(sep=" ")[:19] if hasattr(v, "hour") else v.isoformat()
    if hasattr(v, "as_tuple"):
        return float(v)
    return v


def summary(con):
    """Everything the page's Pipeline health card shows. con is a DuckDB connection to the warehouse."""
    try:
        runs = rows(con, f"SELECT * FROM raw.pipeline_runs ORDER BY started_at DESC LIMIT {HISTORY}")
    except Exception:  # pylint: disable=broad-exception-caught
        return {"runs": []}           # a warehouse from before run history existed
    out = {"runs": runs}
    if not runs:
        return out
    last = runs[0]
    out["steps"] = rows(con, "SELECT step, seconds, status, detail FROM raw.pipeline_steps "
                             f"WHERE run_id = '{last['run_id']}' ORDER BY started_at")
    fresh_run = con.execute("SELECT run_id FROM raw.source_freshness ORDER BY checked_at DESC LIMIT 1").fetchone()
    out["freshness"] = rows(con, "SELECT source_table, max_loaded_at, round(age_hours, 1) AS age_hours, status "
                                 f"FROM raw.source_freshness WHERE run_id = '{fresh_run[0]}' ORDER BY source_table"
                            ) if fresh_run else []
    dbt_run = con.execute("SELECT run_id FROM raw.dbt_results ORDER BY run_id DESC LIMIT 1").fetchone()
    if dbt_run:
        out["slowest_models"] = rows(con, "SELECT replace(unique_id, 'model.retail_warehouse.', '') AS model, "
                                          "seconds, rows_affected FROM raw.dbt_results "
                                          f"WHERE run_id = '{dbt_run[0]}' AND resource_type = 'model' "
                                          "ORDER BY seconds DESC LIMIT 5")
    finished = [r for r in runs if r["status"] != "running"]
    out["success_rate"] = round(100 * sum(r["status"] == "success" for r in finished) / len(finished)) if finished else None
    ok = [r for r in finished if r["status"] == "success"]
    out["last_success"] = ok[0]["finished_at"] if ok else None
    return out
