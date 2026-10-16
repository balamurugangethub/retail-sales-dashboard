# Airflow with the warehouse's tools. dbt gets its own virtualenv so its packages never clash
# with Airflow's; the DAG finds it through DBT_EXECUTABLE.
FROM apache/airflow:3.1.0-python3.12
COPY requirements.txt /tmp/warehouse-requirements.txt
RUN pip install --no-cache-dir "duckdb==1.5.6" "openpyxl>=3.1" \
 && python -m venv /home/airflow/dbt-venv \
 && /home/airflow/dbt-venv/bin/pip install --no-cache-dir -r /tmp/warehouse-requirements.txt
ENV DBT_EXECUTABLE=/home/airflow/dbt-venv/bin/dbt
