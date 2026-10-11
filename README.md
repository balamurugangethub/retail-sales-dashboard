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

## Accounts, privacy and cookies

1. **Welcome screen.** On the first visit, people see a short privacy summary with **Accept** and **Decline**.
   Declining stores nothing and keeps the app closed. The full policy is at `/privacy`.
2. **Sign in or create an account** (`/login`, `/register`). Creating an account requires ticking the privacy policy box.
   Passwords are stored as salted scrypt hashes. After 5 wrong passwords the email is locked for 5 minutes.
3. **Separate data per account.** Each account gets its own database (`userdata/shop_<id>.db`), which starts with the
   sample data. Uploads, edits and saved dashboards never mix between shops.
4. **Your account** (`/account`). Shows when the policy was accepted, and has buttons to download your data,
   sign out, or delete the account together with all of its data.

Only two cookies are used, and both are essential: `rsd_session` keeps you signed in for 14 days, and `rsd_consent`
remembers your choice for a year. There are no analytics or advertising cookies.

| Setting | What it does |
|---|---|
| `SECRET_KEY` | Signs the login cookie. If it is not set, a random key is created in `userdata/secret_key` |
| `PRIVACY_CONTACT` | Email address shown on the privacy page for questions |
| `AUTH=0` | Switches sign-in off for a single-user computer. The welcome screen still shows |

The public Vercel demo is read-only and resets on every cold start, so it shows the welcome screen but no sign-in.
Real accounts on a hosted site need a hosted database such as Supabase or Postgres.

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

### AI assistant
A chat panel in the builder. Examples:
"build a dashboard for Store B", "top 5 products by profit", "profit by month as a line",
"which store is falling behind?", "what were total sales in Grocery?", "rename Store A to Downtown",
"merge Store E into Store D", "clean my data".

It adds charts directly. Anything that changes data (rename, delete, tidy) is shown as a card you must confirm.

Out of the box a built-in rule engine answers, so the app works offline with no account. For smarter,
free-form answers, connect a **free AI model** (no credit card needed). The model gets the field list,
your store/category names and a summary of the numbers for the current filters, and must answer in a
fixed JSON format; the app checks every action against the real fields and values before using it.
If the AI service fails, the rule engine answers instead. The badge in the panel shows which one is active.

#### Connect a free AI model (Google Gemini, recommended)
1. Sign in at https://aistudio.google.com/apikey with a Google account and click **Create API key**.
   It's free and doesn't ask for a card.
2. In Windows PowerShell, in the app folder, run one at a time:

```powershell
$env:AI_API_KEY = "your-gemini-key"
```

```powershell
python app.py
```

The assistant badge now shows **Google Gemini**. Never commit the key; on Vercel add `AI_API_KEY` under
Project Settings > Environment Variables (note that public visitors would then use your free quota).

Other free options, set with `AI_PROVIDER`:

| `AI_PROVIDER` | Key | Notes |
|---|---|---|
| `gemini` (default when only `AI_API_KEY` is set) | https://aistudio.google.com/apikey | model `gemini-2.5-flash` |
| `groq` | https://console.groq.com/keys | model `llama-3.3-70b-versatile`, very fast |
| `ollama` | none | runs on your own computer after installing https://ollama.com and `ollama pull llama3.2` |

`AI_MODEL` picks a different model (for example if the default is retired), and `AI_BASE_URL` points to any
other OpenAI-compatible service.

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

| `POST /api/consent` (`{"accept": true/false}`) | accept or decline the privacy policy and cookies |
| `GET /api/auth/me` | consent state and the signed-in user |
| `POST /api/auth/register`, `/api/auth/login`, `/api/auth/logout` | accounts |
| `POST /api/account/delete` (`{"password"}`) | delete the account and all of its data |

Without consent, every other endpoint returns 401. Without sign-in, they also return 401.
Serve the app over HTTPS when it is reachable from other computers.

## Tests

```bash
python -m pytest -q
```

Tests check that totals match the presentation, filters apply everywhere, bad input is rejected,
SQL injection is harmless, upload/reset behave correctly, messy files are cleaned as expected, the
builder query and saved dashboards work, the consent gate and sign-in work, accounts never see each
other's data, and the assistant behaves with and without an AI model
(a fake AI client is used, so no key is needed).

## Files

```
app.py            Flask app, overview API + SQLite
accounts.py       privacy/cookie consent gate, sign-in, one database per account
workspace.py      builder, import/cleaning, editing and assistant routes
cleaning.py       reads CSV/Excel, guesses columns, cleans rows
queries.py        field catalog and the generic chart query
ai.py             AI chat client (Gemini/Groq/Ollama), rule-based assistant, action validation
seed.py           sample-data generator (calibrated to the deck)
data/targets.csv  store x month targets used by the generator
static/           index/builder/data, welcome/privacy/sign-in/account pages, JS, style.css, vendor/chart.umd.min.js
tests/            pytest suite
```

## Ideas for next steps

Staff roles within a shop, password reset by email, sales targets and alerts, forecasting, a hosted database (so the public
site can save uploads), connect to a real POS, and a scheduled weekly summary email.
