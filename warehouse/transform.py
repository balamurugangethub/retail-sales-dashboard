"""Runs dbt against the warehouse: `dbt build` (seeds, models, every test) and `dbt source freshness`."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys

from warehouse import config


class TransformError(Exception):
    pass


def dbt_command():
    """How to start dbt, as a command list.

    dbt installed in this same Python is run as `python -c ...` rather than through dbt.exe: Windows Smart
    App Control and other application control policies block the unsigned dbt.exe that pip creates
    (WinError 4551), but allow python.exe."""
    if os.environ.get("DBT_EXECUTABLE"):                # the Airflow image keeps dbt in its own virtualenv
        return [os.environ["DBT_EXECUTABLE"]]
    spec = importlib.util.find_spec("dbt")             # dbt is a namespace package; look without importing it
    if spec and any(os.path.isfile(os.path.join(p, "cli", "main.py")) for p in spec.submodule_search_locations or []):
        return [sys.executable, "-c", "import sys; from dbt.cli.main import cli; sys.exit(cli())"]
    found = shutil.which("dbt")
    if found:
        return [found]
    raise TransformError("dbt is not installed. Run: pip install -r warehouse/requirements.txt")


def dbt_env(warehouse_path=None):
    config.share_motherduck_token()
    env = dict(os.environ)
    path = warehouse_path or config.WAREHOUSE_PATH
    env["WAREHOUSE_PATH"] = path if config.is_motherduck(path) else os.path.abspath(path)
    env.setdefault("DBT_SEND_ANONYMOUS_USAGE_STATS", "false")
    return env


def run_dbt(args, warehouse_path=None, check=True):
    cmd = [*dbt_command(), *args, "--project-dir", config.DBT_DIR, "--profiles-dir", config.DBT_DIR]
    result = subprocess.run(cmd, cwd=config.DBT_DIR, env=dbt_env(warehouse_path), capture_output=True,
                            text=True, check=False)
    sys.stdout.write(result.stdout)
    if result.returncode != 0 and "UnserializableField" in result.stderr:
        raise TransformError("dbt does not start on this Python version yet (its mashumaro package is too old for "
                             "Python 3.14). Fix: pip install --no-deps \"mashumaro[msgpack]==3.17\"")
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
