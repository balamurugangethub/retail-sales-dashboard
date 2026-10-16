"""Batch data warehouse for the 5-store retail chain.

Daily till files land in warehouse/data/landing, are checked (validate.py, which reuses the
app's quality checks), loaded into the raw layer of a DuckDB warehouse (load.py), and
transformed by dbt into staging views and a star schema of marts (dbt/). Airflow runs the
same steps on a schedule (airflow/dags/retail_daily.py); run.py runs them from the command line.
"""
