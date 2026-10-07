# Retail Sales Dashboard

A web dashboard for a 5-store retail chain. It replaces the weekly "merge Excel files from every
store" routine with one live source of truth: KPIs, trends, store comparison, category mix, a
store x month heatmap, top products, and plain-English insights.

Built as the working prototype for the presentation problem statement:
*"A retail chain with 5 stores cannot see its sales clearly or quickly."*

## Run it

```bash
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 (Windows: double-click `run.bat`).
The database (`retail.db`) is created and filled with sample data on first start.
Charts are bundled in `static/vendor`, so it works offline.

## What it does

The app has three pages, linked from the header.

### Overview (`/`)
- **KPI tiles:** sales, profit, orders, average basket, each with % change vs the previous period.
- **Click to filter**, period/store/category filters, metric switch, store x month heatmap, top products.
- **Insights:** best and weakest store, stores more than 15% below average, growth, top category, peak month.

### Dashboard builder (`/builder`), Cognos-style
- **Field list:** measures (sales, profit, cost, orders, units, margin %, avg. basket, avg. unit price) and
  dimensions (store, category, product, year, quarter, month, week, day, day of week).
- **Drag a field onto the canvas** to create a chart, or **drag it onto a chart** to add it there.
- **Double-click a field** to add it to the selected chart (or to a new chart if none is selected).
  On a phone or tablet, a single tap does the same.
- The chart type is picked automatically (trend for time, pie for category mix, KPI for a single number)
  and can be switched: bar, horizontal bar, stacked bar, line, area, pie, KPI, table.
- Top 5 / 10 / 20 or bottom 5, four sizes, drag charts by their header to reorder, double-click a title to rename.
- Global filters (period, store, category) and **saved, named dashboards**.

### Data (`/data`)
- **Import with cleaning:** CSV, TSV or Excel (.xlsx). Column names don't have to match: "Branch",
  "Outlet", "Amount", "Qty", "Bill Date" and many more are recognised, and you can correct the mapping.
  The cleaner converts any common date format (it works out day/month order from the whole column),
  strips currency symbols and thousands separators, merges spelling variants ("store a", "STORE A"),
  removes exact duplicate rows (optional), calculates sales from price x units when needed, and fills
  sensible defaults for a single shop (no store column = "Main store").
  You see a report of every fix and every skipped row **before** anything is saved.
- **Edit in place:** search, filter, sort, double-click a cell to edit it, delete rows.
- **Bulk fixes:** tidy everything, rename or merge a store/category/product, delete all rows for one.
- **Export** to CSV and **restore** the sample data.

### AI assistant (IBM watsonx.ai)
A chat panel in the builder. Examples:
"build a dashboard for Store B", "top 5 products by profit", "profit by month as a line",
"which store is falling behind?", "what were total sales in Grocery?", "rename Store A to Downtown",
"merge Store E into Store D", "clean my data".

It adds charts directly. Anything that changes data (rename, delete, tidy) is shown as a card you must confirm.

With IBM credentials it uses **watsonx.ai (IBM Granite)**. The model gets the field list, your
store/category names and a summary of the numbers for the current filters, and must answer in a fixed
JSON format; the app checks every action against the real fields and values before using it.
Without credentials, or if watsonx.ai fails, a built-in rule engine answers instead, so the app
always works offline. The badge in the panel shows which one is active.

#### Connect IBM watsonx.ai
1. Create a free IBM Cloud account and a **watsonx.ai** project (https://dataplatform.cloud.ibm.com/wx).
2. Copy the project ID (Project > Manage > General) and create an **IBM Cloud API key**
   (https://cloud.ibm.com/iam/apikeys).
3. Set the environment variables before starting the app. In Windows PowerShell:

```powershell
$env:WATSONX_API_KEY = "your-api-key"
```

```powershell
$env:WATSONX_PROJECT_ID = "your-project-id"
```

```powershell
python app.py
```

Optional: `WATSONX_URL` (default `https://us-south.ml.cloud.ibm.com`; use your project's region, e.g.
`https://eu-de.ml.cloud.ibm.com`) and `WATSONX_MODEL_ID` (default `ibm/granite-3-8b-instruct`; any chat
model available in your region works). Never commit the key; on Vercel, add it under Project Settings > Environment Variables.

## Sample data matches the presentation

Jan-Jun 2026: sales **$482K**, profit **$96K**, **12,340** orders, avg basket **$39.06**;
stores A-E = 120 / 98 / 105 / 82 / 77 ($K); monthly 65 / 70 / 74 / 79 / 88 / 106 ($K).
Versus the previous 6 months: sales +8.4%, profit +3.1%, orders -1.2%, avg basket +9.7%.
The data is generated (`seed.py`, calibrated from `data/targets.csv`); it is not real company data.

## API

All GET endpoints accept `start`, `end` (YYYY-MM-DD), `store`, `category`.

| Endpoint | Returns |
|---|---|
| `GET /api/meta` | stores, categories, date range, row count |
| `GET /api/kpis` | current vs previous period totals and % change |
| `GET /api/by-store`, `/api/categories`, `/api/heatmap` | grouped sales |
| `GET /api/trend?grain=month\|week\|day` | time series |
| `GET /api/top-products?limit=8` | best sellers |
| `GET /api/insights` | generated findings |
| `GET /api/export.csv` | filtered rows as CSV |
| `POST /api/upload` (form: `file`, `mode`) | strict import (exact column names) |
| `POST /api/reset` | restore sample data |
| `GET /api/fields` | measures, dimensions, chart types, store/category/product names, assistant provider |
| `GET /api/query?dims=store,month&measures=sales,profit&sort=-sales&limit=10` | data for one builder chart |
| `GET/POST /api/dashboards`, `GET/PUT/DELETE /api/dashboards/<id>` | saved dashboards |
| `POST /api/import/preview` (form: `file`, optional `mapping` JSON, `drop_duplicates`) | cleaning report, nothing saved |
| `POST /api/import/commit` (`{"import_id", "mode"}`) | save a previewed import |
| `GET /api/rows`, `PATCH/DELETE /api/rows/<id>` | browse and edit rows |
| `POST /api/data/rename`, `/api/data/delete`, `/api/data/tidy` | bulk fixes |
| `POST /api/assistant` (`{"message", "filters"}`) | assistant reply + proposed actions |

Note: the write endpoints have no login. This is a local demo app; add authentication
before exposing it on a network.

## Tests

```bash
python -m pytest -q
```

Tests check that totals match the presentation, filters apply everywhere, bad input is rejected,
SQL injection is harmless, upload/reset behave correctly, messy files are cleaned as expected, the
builder query and saved dashboards work, and the assistant behaves with and without watsonx.ai
(a fake watsonx client is used, so no IBM key is needed).

## Files

```
app.py            Flask app, overview API + SQLite
workspace.py      builder, import/cleaning, editing and assistant routes
cleaning.py       reads CSV/Excel, guesses columns, cleans rows
queries.py        field catalog and the generic chart query
ai.py             watsonx.ai client, rule-based assistant, action validation
seed.py           sample-data generator (calibrated to the deck)
data/targets.csv  store x month targets used by the generator
static/           index/builder/data pages, JS, style.css, vendor/chart.umd.min.js
tests/            pytest suite
```

## Ideas for next steps

Logins per shop and roles, sales targets and alerts, forecasting, a hosted database (so the public
site can save uploads), connect to a real POS, and a scheduled weekly summary email.
