"""Airflow DAG: every morning, load the stores' new till files into the warehouse.

    land_files -> find_new_files -> validate -> load_raw -> dbt_build -> export_snapshot

Each task calls the same functions as `python -m warehouse.run`, so the pipeline behaves the same
with or without Airflow. Any failed task sends an alert (Discord/Slack webhook, or the alert log).
A rejected file is moved to warehouse/data/landing/quarantine/ and the run stops before loading.
"""
# pylint: disable=import-error,no-name-in-module
import os
import sys

import pendulum

try:                                        # Airflow 3
    from airflow.sdk import dag, task
    from airflow.exceptions import AirflowSkipException
except ImportError:                         # Airflow 2
    from airflow.decorators import dag, task
    from airflow.exceptions import AirflowSkipException

sys.path.insert(0, os.environ.get("RETAIL_REPO", "/opt/airflow/repo"))

from warehouse import alerts, config, run, snapshot, transform  # noqa: E402

SIMULATE_ARRIVALS = os.environ.get("SIMULATE_ARRIVALS") == "1"


def alert_on_failure(context):
    ti = context["task_instance"]
    error = context.get("exception")
    title, details = run.describe_failure(error) if error else (f"Task {ti.task_id} failed", "")
    alerts.send(f"{ti.dag_id}.{ti.task_id} failed: {title}", details)


@dag(
    dag_id="retail_daily_warehouse",
    description="Validate and load the daily till files, then rebuild the marts with dbt",
    schedule="0 6 * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,                      # DuckDB allows one writer at a time
    default_args={"retries": 1, "retry_delay": pendulum.duration(minutes=2), "on_failure_callback": alert_on_failure},
    tags=["retail", "warehouse"],
)
def retail_daily_warehouse():

    @task
    def land_files():
        """Stands in for the stores' tills: two years of history on the first run, then one new day per run."""
        if not SIMULATE_ARRIVALS:
            return "waiting for the stores' files"
        from warehouse import generate
        if not generate.landed_days(config.LANDING_DIR) and not run.find_new_files():
            rows = generate.generate(out_dir=config.LANDING_DIR)
            return f"landed two years of history ({rows:,} rows)"
        generate.main(["--next", "1", "--out", config.LANDING_DIR])
        return "landed one new day"

    @task
    def find_new_files(_landed):
        files = run.find_new_files()
        if not files:
            raise AirflowSkipException("No new files in the landing folder")
        return files

    @task(retries=0)                        # a bad file stays bad; fail at once and alert
    def validate(files):
        return run.validate_files(files)

    @task
    def load_raw(scores):
        return run.load_files(scores)

    @task
    def dbt_build(_rows):
        transform.build()

    @task
    def export_snapshot(_built):
        return snapshot.export()

    export_snapshot(dbt_build(load_raw(validate(find_new_files(land_files())))))


retail_daily_warehouse()
