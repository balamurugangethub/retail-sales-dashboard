# Retail warehouse pipeline

A batch pipeline with two daily feeds: the 5-store chain's tills (about **1.05 million synthetic order lines** over
two years) and a real public dataset, **UCI Online Retail II** (about **1.07 million invoice lines** of a UK online
shop). Files arrive as one CSV per day, are validated, loaded into a warehouse with **raw, staging and mart** layers,
modelled by **dbt** into two star schemas and checked for freshness. It runs **in the cloud every morning** (GitHub
Actions, a Supabase Storage bucket and MotherDuck, all free) and **on a laptop** (Airflow in Docker, or one command).
Every run is recorded, and the dbt docs are published as the data catalog. The web app's Warehouse page reads the marts.

The architecture diagram and the "what I'd do at 100x scale" notes are in the [main README](../README.md#data-warehouse-pipeline).

## Folder

```
warehouse/
  generate.py      synthetic till files (seeded, ~1.05M rows by default, some dirty rows on purpose)
  online_retail.py downloads UCI Online Retail II and writes one file per day (the real-data feed)
  feeds.py         the two feeds: columns, file names and each one's validation rules
  validate.py      checks each file; reuses the app's quality.py score and fails on high-severity problems
  load.py          raw layer: loads a file (re-loading replaces it), rejects to quarantine, writes raw.load_audit
  transform.py     runs `dbt build`, `dbt source freshness` and `dbt docs generate`
  observe.py       run history: raw.pipeline_runs, pipeline_steps, dbt_results, source_freshness
  snapshot.py      copies the reporting marts and run history to snapshot/ for the hosted site
  alerts.py        Discord or Slack webhook (ALERT_WEBHOOK_URL), always also written to data/alerts.log
  run.py           the whole pipeline once, from the command line
  object_store.py  the landing zone: a folder, or a Supabase Storage bucket
  cloud_run.py     the cloud run: land, sync, run, publish the catalog (GitHub Actions calls this)
  dbt/             dbt project: staging views, two star schemas, reporting marts, 51 tests
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

Add the real dataset (a 45 MB download, then about five minutes to split and load):

```powershell
python -m warehouse.online_retail
```

```powershell
python -m warehouse.run
```

If the download is blocked, download the zip from https://archive.ics.uci.edu/dataset/502/online+retail+ii in your
browser and run `python -m warehouse.online_retail --file "$HOME\Downloads\online+retail+ii.zip"`.

## Run it with Airflow (Docker Desktop)

```powershell
docker compose -f warehouse\airflow\docker-compose.yml up --build
```

Open http://localhost:8080 (no password locally), switch on **retail_daily_warehouse** and press the play button.
The first run lands two years of history and loads it; every later run lands one new day first
(`SIMULATE_ARRIVALS=1`), so there is always a new file to load. It is scheduled for 06:00 UTC each day.
Stop it with `Ctrl+C`.

The tasks are `land_files -> find_new_files -> validate -> load_raw -> dbt_build -> source_freshness -> export_snapshot`.
Any failed task calls `alert_on_failure`. `validate` never retries, because a bad file stays bad. The first run also
lands the real Online Retail II dataset (`PUBLIC_DATASET=1` in `docker-compose.yml`; set it to `0` to skip).
Each task writes its step to the run history under the Airflow run id.

![The DAG in Airflow, with one run stopped by a broken file](../docs/screenshots/airflow.png)

## Run it in the cloud (free, no credit card)

`.github/workflows/cloud-pipeline.yml` runs `python -m warehouse.cloud_run` every day at 06:15 UTC, and whenever
`warehouse/` changes on `main`. It:

1. lands every till day up to yesterday that is not in the landing zone yet (the first run lands the history from
   2024-07-01), and the Online Retail II dataset the first time;
2. downloads the files the warehouse has not loaded;
3. validates, loads, runs `dbt build` and `dbt source freshness`, and records the run;
4. moves rejected files to `quarantine/` in the bucket;
5. publishes the data catalog and the marts to GitHub Pages (also after a failed run, so the failure shows up).

With no accounts set up it still works: the warehouse is a DuckDB file and the landing zone a folder, both kept
between runs by the Actions cache. With the secrets below it uses real cloud services. Pull requests never get the
secrets, so a pull request can't change the real warehouse.

### One-time setup

**1. Turn on GitHub Pages** (the data catalog). GitHub > the repository > **Settings** > **Pages** > under
**Build and deployment**, set **Source** to **GitHub Actions**. Nothing else to fill in.

**2. MotherDuck** (the cloud warehouse). Sign up at https://motherduck.com with Google or GitHub (the free plan asks
for no card). In https://app.motherduck.com click your organization name at the top left > **Settings** >
**+ Create token**. Name it `github-actions`, keep **Read/Write**, choose no expiry, click **Create token** and copy it.

**3. Supabase Storage** (the landing zone). Use the existing **student-notes** project. Supabase > student-notes >
**Project Settings** (the gear) > **API Keys** > under **Secret keys**, copy the `sb_secret_...` key (create one if
the list is empty). The project URL is on the same settings area under **Data API**, like
`https://bpcacfmpdcqpvscuglis.supabase.co`. The pipeline creates its private bucket, `warehouse-landing`, by itself.

**4. Add the secrets to GitHub.** The repository > **Settings** > **Secrets and variables** > **Actions** >
**New repository secret**, once for each:

| Name | Value |
|---|---|
| `MOTHERDUCK_TOKEN` | the MotherDuck token |
| `SUPABASE_URL` | the Supabase project URL |
| `SUPABASE_SECRET_KEY` | the `sb_secret_...` key |
| `ALERT_WEBHOOK_URL` | optional: a Discord or Slack webhook URL |

**5. Run it.** The repository > **Actions** > **Cloud pipeline** > **Run workflow**. The first run lands and loads
about 2.3 million rows and takes about half an hour; later runs take a few minutes. Afterwards the catalog is at
https://balamurugangethub.github.io/retail-sales-dashboard/ and the live site's Warehouse page says
**Published daily**.

The secret key can read and write everything in that Supabase project, so it lives only in GitHub secrets. Never
put it in a file in the repository.

GitHub pauses scheduled workflows in a repository with no activity for 60 days; **Run workflow** (or any push)
starts them again.

## Run history, freshness and the data catalog

Every run, from the command line, Airflow or GitHub Actions, writes to four tables in `raw`:

| Table | One row per | What it holds |
|---|---|---|
| `pipeline_runs` | run | trigger, warehouse, start, end, seconds, status, files found / loaded / rejected, rows loaded, dbt models and tests, freshness, the error |
| `pipeline_steps` | step of a run | step name, seconds, status, detail (for example `51 of 51 tests passed`) |
| `dbt_results` | dbt node of a run | every seed, model and test with its seconds, rows and status |
| `source_freshness` | raw table per run | newest `_loaded_at`, its age in hours, pass / warn / error |

`dbt source freshness` warns when the till feed has had no new lines for 30 hours and errors after 72; an error sends
an alert but does not throw away the run. The Online Retail II feed is historical, so it has no freshness rule. Query
the history in the SQL tool of your choice, for example:

```sql
select started_at, status, seconds, rows_loaded, files_rejected, dbt_tests - dbt_tests_failed as tests_passed, freshness
from raw.pipeline_runs order by started_at desc limit 10;
```

The data catalog is dbt's own documentation: every source, model and column has a description in the `.yml` files,
and `dbt docs generate --static` writes one page with them, the column types and the lineage graph. The cloud run
publishes it; locally, run `dbt docs generate --profiles-dir .` and `dbt docs serve --profiles-dir .` in `warehouse\dbt`.

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

The real Online Retail II files have their own rules (`feeds.py`), because some of their oddities are real business:

| Check | Fails when |
|---|---|
| Columns, dates, numbers | as above, dated on the file's day |
| Rows | fewer than 5 |
| Negative numbers | more than 1% of lines have a negative quantity that is not a cancellation (invoice `C...`) or a negative price that is not a bad-debt adjustment (code `B`) |
| Country | any line has no country |
| `quality.py` | the score is below 70, or 30%+ of a required column is empty. `customer_id` (blank for guest checkouts) and `description` are not scored, and repeated lines are removed in staging instead of rejecting the day |

## dbt models and tests

| Layer | Models |
|---|---|
| raw (loaded by Python, plus seeds) | `raw.sales_lines`, `raw.online_retail_lines`, `raw.load_audit`, the run history tables, `raw.stores`, `raw.products` |
| staging (views) | `stg_sales_lines` (types, trims, de-duplicates), `stg_online_retail_lines` (types, cancellation and product flags, de-duplicates), `stg_stores`, `stg_products` |
| marts: star schemas | `fct_sales` (incremental, one row per order line), `dim_date`, `dim_store`, `dim_product`; `fct_online_sales` (one row per invoice line), `dim_customer` |
| marts: reporting | `mart_store_daily`, `mart_category_monthly`, `mart_product_monthly`, `mart_online_daily`, `mart_online_country_monthly`, `mart_online_top_products` |

Tests: unique and not-null keys, relationships from each fact to every dimension, accepted values, and six
custom tests: no negative amounts, the fact table matches staging row for row and in total, every reporting mart
adds up to its fact table (both feeds), every store has sales on every loaded day, and cancellations never add units.

Run dbt by hand: `cd warehouse\dbt`, set `$env:WAREHOUSE_PATH = "..\data\warehouse.duckdb"`, then
`dbt build --profiles-dir .`, or `dbt docs generate --profiles-dir .` followed by `dbt docs serve --profiles-dir .`
for the lineage graph.
