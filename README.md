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

- **KPI tiles:** sales, profit, orders, average basket, each with % change vs the previous period
  (whole calendar months compare with the preceding months).
- **Click to filter:** click a store bar, a category slice or a heatmap store name to filter every
  chart, table and insight. Click again to clear.
- **Filters:** period presets or custom dates, store, category.
- **Metric switch:** Sales / Profit / Orders on the store and trend charts; monthly, weekly or daily trend.
- **Insights:** best and weakest store, stores more than 15% below average, fastest growing or
  declining store, top category, peak month.
- **Upload your own data:** CSV with columns `date, store, category, product, orders, units, revenue, cost`
  (replace or append). Files are validated, and a bad row rejects the whole file with line numbers.
- **Export:** download the currently filtered data as CSV.
- **Reset:** restore the sample data.

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
| `POST /api/upload` (form: `file`, `mode`) | validated import |
| `POST /api/reset` | restore sample data |

Note: `/api/upload` and `/api/reset` have no login. This is a local demo app; add authentication
before exposing it on a network.

## Tests

```bash
python -m pytest -q
```

Tests check that totals match the presentation, filters apply everywhere, bad input is rejected,
SQL injection is harmless, and upload/reset behave correctly.

## Files

```
app.py            Flask API + SQLite queries
seed.py           sample-data generator (calibrated to the deck)
data/targets.csv  store x month targets used by the generator
static/           index.html, app.js, style.css, vendor/chart.umd.min.js
tests/            pytest suite
```

## Ideas for next steps

Login and roles, connect to a real POS database instead of CSV, scheduled email of the weekly
summary, forecasting, and deployment to a server or cloud host.
