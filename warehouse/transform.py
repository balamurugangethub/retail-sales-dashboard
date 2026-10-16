"""Runs dbt (seeds, staging, marts and every test) against the warehouse file."""
import importlib.util
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
    env = dict(os.environ)
    env["WAREHOUSE_PATH"] = os.path.abspath(warehouse_path or config.WAREHOUSE_PATH)
    env.setdefault("DBT_SEND_ANONYMOUS_USAGE_STATS", "false")
    return env


def run_dbt(args, warehouse_path=None):
    cmd = [*dbt_command(), *args, "--project-dir", config.DBT_DIR, "--profiles-dir", config.DBT_DIR]
    result = subprocess.run(cmd, cwd=config.DBT_DIR, env=dbt_env(warehouse_path), capture_output=True,
                            text=True, check=False)
    sys.stdout.write(result.stdout)
    if result.returncode != 0 and "UnserializableField" in result.stderr:
        raise TransformError("dbt does not start on this Python version yet (its mashumaro package is too old for "
                             "Python 3.14). Fix: pip install --no-deps \"mashumaro[msgpack]==3.17\"")
    if result.returncode != 0:
        failed = [line.strip() for line in result.stdout.splitlines() if "FAIL" in line or "ERROR" in line]
        raise TransformError("dbt " + args[0] + " failed:\n" + "\n".join(failed[-10:] or [result.stderr[-1500:]]))
    return result.stdout


def build(warehouse_path=None, full_refresh=False):
    """dbt build = seed + run + test, in dependency order. A failing test stops the models after it."""
    return run_dbt(["build"] + (["--full-refresh"] if full_refresh else []), warehouse_path)
