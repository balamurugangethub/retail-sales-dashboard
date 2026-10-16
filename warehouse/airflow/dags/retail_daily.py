"""Airflow DAG: every morning, load the stores' new till files into the warehouse.

    land_files -> find_new_files -> validate -> load_raw -> dbt_build -> source_freshness -> export_snapshot

Each task calls the same functions as `python -m warehouse.run`, so the pipeline behaves the same
with or without Airflow. Any failed task sends an alert (Discord/Slack webhook, or the alert log).
A rejected file is moved to warehouse/data/landing/quarantine/ and the run stops before loading.
Every run and step is recorded in raw.pipeline_runs and raw.pipeline_steps (see warehouse/observe.py),
under the Airflow run id, so the Warehouse page shows Airflow runs next to the cloud ones.
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

from warehouse import alerts, config, observe, run, snapshot, transform  # noqa: E402

SIMULATE_ARRIVALS = os.environ.get("SIMULATE_ARRIVALS") == "1"
# "1" also lands the real UCI Online Retail II dataset once (needs internet the first time)
PUBLIC_DATASET = os.environ.get("PUBLIC_DATASET") == "1"


def recorder(context):
    return observe.Run(run_id=f"airflow-{context['run_id']}", trigger="airflow")


def alert_on_failure(context):
    ti = context["task_instance"]
    error = context.get("exception")
    title, details = run.describe_failure(error) if error else (f"Task {ti.task_id} failed", "")
    alerts.send(f"{ti.dag_id}.{ti.task_id} failed: {title}", details)
    try:
        recorder(context).finish("failed", f"{ti.task_id}: {title}")
    except Exception:  # pylint: disable=broad-exception-caught
        pass                                # the alert matters more than the run history row


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
    def land_files(**context):
        """Stands in for the stores' tills: two years of history on the first run, then one new day per run."""
        rec = recorder(context).start()
        with rec.step("land_files") as s:
            s["detail"] = land()
        return s["detail"]

    def land():
        notes = []
        if PUBLIC_DATASET:
            from warehouse import online_retail
            if not online_retail.landed_days(config.LANDING_DIR):
                files, rows = online_retail.land(config.LANDING_DIR)
                notes.append(f"landed Online Retail II ({files} days, {rows:,} lines)")
        if not SIMULATE_ARRIVALS:
            return "; ".join(notes + ["waiting for the stores' files"])
        from warehouse import generate
        if not generate.landed_days(config.LANDING_DIR) and not run.find_new_files():
            rows = generate.generate(out_dir=config.LANDING_DIR)
            return "; ".join(notes + [f"landed two years of history ({rows:,} rows)"])
        generate.main(["--next", "1", "--out", config.LANDING_DIR])
        return "; ".join(notes + ["landed one new day"])

    @task
    def find_new_files(_landed, **context):
        rec = recorder(context)
        with rec.step("find_new_files") as s:
            files = run.find_new_files()
            s["detail"] = f"{len(files)} new file(s)"
        rec.save(files_found=len(files))
        if not files:
            rec.finish("success")
            raise AirflowSkipException("No new files in the landing folder")
        return files

    @task(retries=0)                        # a bad file stays bad; fail at once and alert
    def validate(files, **context):
        rec = recorder(context)
        with rec.step("validate") as s:
            try:
                scores = run.validate_files(files)
            except run.ValidationError as e:
                rec.save(files_rejected=e.rejected)
                raise
            s["detail"] = f"{len(scores)} file(s) passed"
        rec.save(files_rejected=0)
        return scores

    @task
    def load_raw(scores, **context):
        rec = recorder(context)
        with rec.step("load_raw") as s:
            rows = run.load_files(scores)
            s["detail"] = f"{rows:,} rows"
        rec.save(files_loaded=len(scores), rows_loaded=rows)
        return rows

    @task
    def dbt_build(_rows, **context):
        rec = recorder(context)
        with rec.step("dbt_build"):
            try:
                transform.build()
            finally:
                rec.record_dbt()
                rec.save()

    @task
    def source_freshness(_built, **context):
        rec = recorder(context)
        with rec.step("source_freshness") as s:
            s["detail"] = rec.record_freshness(transform.freshness())
        rec.save()
        if s["detail"] == "error":
            alerts.send("Source data is stale", "dbt source freshness is past its error limit.")
        rec.finish("success")

    @task
    def export_snapshot(_checked):
        return snapshot.export()

    export_snapshot(source_freshness(dbt_build(load_raw(validate(find_new_files(land_files()))))))


retail_daily_warehouse()
