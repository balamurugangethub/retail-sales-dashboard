"""Runs dbt against the warehouse: `dbt build` (seeds, models, every test) and `dbt source freshness`."""
import json
import os
import shutil
import subprocess
import sys

from warehouse import config


class TransformError(Exception):
    pass


def dbt_executable():
    if os.environ.get("DBT_EXECUTABLE"):                # the Airflow image keeps dbt in its own virtualenv
        return os.environ["DBT_EXECUTABLE"]
    found = shutil.which("dbt")
    if found:
        return found
    folder = os.path.dirname(sys.executable)          # the dbt that was installed next to this Python
    for name in ("dbt", "dbt.exe"):
        if os.path.exists(os.path.join(folder, name)):
            return os.path.join(folder, name)
    raise TransformError("dbt is not installed. Run: pip install -r warehouse/requirements.txt")


def dbt_env(warehouse_path=None):
    config.share_motherduck_token()
    env = dict(os.environ)
    path = warehouse_path or config.WAREHOUSE_PATH
    env["WAREHOUSE_PATH"] = path if config.is_motherduck(path) else os.path.abspath(path)
    env.setdefault("DBT_SEND_ANONYMOUS_USAGE_STATS", "false")
    return env


def run_dbt(args, warehouse_path=None, check=True):
    cmd = [dbt_executable(), *args, "--project-dir", config.DBT_DIR, "--profiles-dir", config.DBT_DIR]
    result = subprocess.run(cmd, cwd=config.DBT_DIR, env=dbt_env(warehouse_path), capture_output=True,
                            text=True, check=False)
    sys.stdout.write(result.stdout)
    if check and result.returncode != 0:
        failed = [line.strip() for line in result.stdout.splitlines() if "FAIL" in line or "ERROR" in line]
        raise TransformError("dbt " + args[0] + " failed:\n" + "\n".join(failed[-10:] or [result.stderr[-1500:]]))
    return result.stdout


def build(warehouse_path=None, full_refresh=False):
    """dbt build = seed + run + test, in dependency order. A failing test stops the models after it."""
    return run_dbt(["build"] + (["--full-refresh"] if full_refresh else []), warehouse_path)


FRESHNESS_FILE = os.path.join(config.DBT_DIR, "target", "sources.json")


def freshness(warehouse_path=None):
    """`dbt source freshness` for every raw table that has a freshness rule (see _sources.yml).

    Never raises for stale data: a stale source is a warning or an alert, not a reason to throw away a
    run that loaded fine. Returns [{source_table, max_loaded_at, age_hours, status}]."""
    if os.path.exists(FRESHNESS_FILE):
        os.remove(FRESHNESS_FILE)
    run_dbt(["source", "freshness"], warehouse_path, check=False)
    if not os.path.exists(FRESHNESS_FILE):
        return []
    with open(FRESHNESS_FILE, encoding="utf-8") as f:
        results = json.load(f).get("results", [])
    return [{"source_table": ".".join(r["unique_id"].split(".")[-2:]),
             "max_loaded_at": (r.get("max_loaded_at") or "")[:19].replace("T", " ") or None,
             "age_hours": None if r.get("max_loaded_at_time_ago_in_s") is None
             else round(r["max_loaded_at_time_ago_in_s"] / 3600, 2),
             "status": r.get("status")} for r in results]


def docs(warehouse_path=None):
    """`dbt docs generate --static`: one self-contained HTML page with every model, column and the lineage graph."""
    run_dbt(["docs", "generate", "--static"], warehouse_path)
    return os.path.join(config.DBT_DIR, "target", "static_index.html")
