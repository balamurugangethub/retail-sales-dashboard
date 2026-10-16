# Retail warehouse pipeline

A batch pipeline for the 5-store chain: about **1.05 million order lines** over two years arrive as one CSV per
day, are validated, loaded into a **DuckDB** warehouse with **raw, staging and mart** layers, modelled by **dbt**
into a star schema, and scheduled by **Airflow** (Docker Compose). The web app's Warehouse page reads the marts.

The architecture diagram and the "what I'd do at 100x scale" notes are in the [main README](../README.md#data-warehouse-pipeline).

## Folder

```
warehouse/
  generate.py      synthetic till files (seeded, ~1.05M rows by default, some dirty rows on purpose)
  validate.py      checks each file; reuses the app's quality.py score and fails on high-severity problems
  load.py          raw layer: loads a file (re-loading replaces it), rejects to quarantine, writes raw.load_audit
  transform.py     runs `dbt build` (seeds, models and tests)
  snapshot.py      copies the reporting marts to snapshot/ for the hosted site
  alerts.py        Discord or Slack webhook (ALERT_WEBHOOK_URL), always also written to data/alerts.log
  run.py           the whole pipeline once, from the command line
  dbt/             dbt project: staging views, star schema, reporting marts, 30 tests
  airflow/         Dockerfile, docker-compose.yml and the DAG
  snapshot/        committed copy of the marts (what the live site shows)
  data/            landing files, quarantine, warehouse.duckdb, alerts.log (not committed)
```

## Run it without Airflow (Windows PowerShell)

Run each line on its own, from the `retail-sales-dashboard` folder.

```powershell
pip install -r warehouse\requirements.txt
```

```powershell
python -m warehouse.generate
```

```powershell
python -m warehouse.run
```

The first run loads all 730 files and takes about two minutes. Then start the app (`python app.py`) and open
http://127.0.0.1:5000/warehouse: the badge says **Live warehouse** because it is reading `warehouse\data\warehouse.duckdb`.

To see a failure, write a broken file and run again. The run stops, the file moves to
`warehouse\data\landing\quarantine`, and the alert appears in `warehouse\data\alerts.log`:

```powershell
python -m warehouse.generate --corrupt 2026-07-01
```

```powershell
python -m warehouse.run
```

Add one more normal day with `python -m warehouse.generate --next 1`.

## Run it with Airflow (Docker Desktop)

```powershell
docker compose -f warehouse\airflow\docker-compose.yml up --build
```

Open http://localhost:8080 (no password locally), switch on **retail_daily_warehouse** and press the play button.
The first run lands two years of history and loads it; every later run lands one new day first
(`SIMULATE_ARRIVALS=1`), so there is always a new file to load. It is scheduled for 06:00 UTC each day.
Stop it with `Ctrl+C`.

The tasks are `land_files -> find_new_files -> validate -> load_raw -> dbt_build -> export_snapshot`. Any failed
task calls `alert_on_failure`. `validate` never retries, because a bad file stays bad.

![The DAG in Airflow, with one run stopped by a broken file](../docs/screenshots/airflow.png)

## Alerts in Discord or Slack (free)

Create an incoming webhook (Discord: channel settings, Integrations, Webhooks; Slack: an app with Incoming Webhooks),
then put the URL in a file named `.env` next to `docker-compose.yml`:

```
ALERT_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

For `python -m warehouse.run`, set it in PowerShell first: `$env:ALERT_WEBHOOK_URL = "https://..."`.
Never commit the URL.

## What validation checks

| Check | Fails when |
|---|---|
| Columns | any expected column is missing |
| Row count | fewer than 50 rows |
| Dates | a sale is not dated on the file's day |
| Stores and numbers | an unknown store, a negative quantity or price, or more than 0.5% of numbers that don't parse |
| `quality.py` (the same checks as the Explore page) | the score is below 85, or any problem is rated high, such as 30%+ empty values or 5%+ repeated rows |

Lower-severity problems (a few repeated lines, a blank payment method) pass validation and are fixed in staging.

## dbt models and tests

| Layer | Models |
|---|---|
| raw (loaded by Python, plus seeds) | `raw.sales_lines`, `raw.load_audit`, `raw.stores`, `raw.products` |
| staging (views) | `stg_sales_lines` (types, trims, de-duplicates), `stg_stores`, `stg_products` |
| marts: star schema | `fct_sales` (incremental, one row per order line), `dim_date`, `dim_store`, `dim_product` |
| marts: reporting | `mart_store_daily`, `mart_category_monthly`, `mart_product_monthly` |

Tests: unique and not-null keys, relationships from the fact to every dimension, accepted values, and four
custom tests: no negative amounts, the fact table matches staging row for row and in total, every reporting mart
adds up to the fact table, and every store has sales on every loaded day.

Run dbt by hand: `cd warehouse\dbt`, set `$env:WAREHOUSE_PATH = "..\data\warehouse.duckdb"`, then
`dbt build --profiles-dir .`, or `dbt docs generate --profiles-dir .` followed by `dbt docs serve --profiles-dir .`
for the lineage graph.
