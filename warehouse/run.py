"""Runs the whole pipeline once: find new daily files, validate, load raw, dbt build, export snapshot.

Airflow calls the same step functions (see airflow/dags/retail_daily.py). From the command line:

    python -m warehouse.run                 # load every new file in warehouse/data/landing
    python -m warehouse.run --no-snapshot   # don't rewrite warehouse/snapshot/
    python -m warehouse.run --full-refresh  # rebuild the fact table from the raw layer

Any failure sends an alert (see alerts.py) and exits with status 1.
"""
import argparse
import logging
import sys
import time

from warehouse import alerts, config, load, snapshot, transform
from warehouse.validate import ValidationError, validate_file

log = logging.getLogger("warehouse")


def find_new_files(warehouse_path=None, landing_dir=None):
    con = load.connect(warehouse_path)
    try:
        return load.new_files(con, landing_dir)
    finally:
        con.close()


def validate_files(paths, warehouse_path=None):
    """Checks every file. Bad files are moved to quarantine and recorded, then the run stops.

    Returns {path: quality score} for the files that passed."""
    passed, failed = {}, []
    for path in paths:
        report = validate_file(path)
        if report["ok"]:
            passed[path] = report["score"]
        else:
            failed.append(report)
            con = load.connect(warehouse_path)
            try:
                load.reject(con, path, report)
            finally:
                con.close()
    if failed:
        first = failed[0]
        if len(failed) > 1:
            first = dict(first, failures=first["failures"] + [f"and {len(failed) - 1} more file(s) failed"])
        raise ValidationError(first)
    return passed


def load_files(scores, warehouse_path=None):
    con = load.connect(warehouse_path)
    try:
        return sum(load.load_file(con, path, {"score": score}) for path, score in scores.items())
    finally:
        con.close()


def run_pipeline(warehouse_path=None, landing_dir=None, with_snapshot=True, full_refresh=False):
    started = time.time()
    paths = find_new_files(warehouse_path, landing_dir)
    log.info("%d new file(s) to load", len(paths))
    scores = validate_files(paths, warehouse_path)
    rows = load_files(scores, warehouse_path)
    log.info("Loaded %d rows into raw.sales_lines", rows)
    transform.build(warehouse_path, full_refresh=full_refresh)
    summary = {"files": len(paths), "rows": rows, "seconds": round(time.time() - started, 1)}
    if with_snapshot:
        summary["snapshot"] = snapshot.export(warehouse_path)
    return summary


def describe_failure(error):
    if isinstance(error, ValidationError):
        return (f"File {error.report['file']} was rejected and moved to quarantine. Nothing was loaded.",
                "\n".join(error.report["failures"]))
    if isinstance(error, transform.TransformError):
        return "dbt build failed: a model or data test did not pass.", str(error)
    return f"Pipeline step failed: {type(error).__name__}", str(error)


def main(argv=None):
    p = argparse.ArgumentParser(description="Load new daily till files into the warehouse.")
    p.add_argument("--no-snapshot", action="store_true", help="don't rewrite warehouse/snapshot/")
    p.add_argument("--full-refresh", action="store_true", help="rebuild incremental models from scratch")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        summary = run_pipeline(with_snapshot=not a.no_snapshot, full_refresh=a.full_refresh)
    except Exception as e:  # pylint: disable=broad-exception-caught
        # any failure at all must send an alert
        title, details = describe_failure(e)
        where = alerts.send(title, details)
        print(f"\nFAILED: {title}\n{details}\nAlert sent to: {where} (log file {config.ALERT_LOG})", file=sys.stderr)
        return 1
    print(f"\nDone: {summary['files']} file(s), {summary['rows']:,} rows loaded in {summary['seconds']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
