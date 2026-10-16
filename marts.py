"""The Warehouse page: KPIs and charts read from the dbt marts, not from the app's own sales table.

Where the data comes from, in order:
1. The DuckDB warehouse (warehouse/data/warehouse.duckdb, or WAREHOUSE_PATH) when it exists and the
   duckdb package is installed: the marts are read live, so a pipeline run shows up on refresh.
2. The snapshot the scheduled cloud run publishes to GitHub Pages (WAREHOUSE_SNAPSHOT_URL; on Vercel
   it defaults to this repository's Pages site), fetched again every few minutes.
3. Otherwise the snapshot of the same marts committed in warehouse/snapshot/.
The small reporting marts are copied into an in-memory SQLite database, so every query below is the
same whichever source was used.
"""
import csv
import json
import os
import sqlite3
import tempfile
import threading
import time
import urllib.error
import urllib.request

from flask import jsonify, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_DIR = os.path.join(HERE, "warehouse", "snapshot")
DEFAULT_WAREHOUSE = os.path.join(HERE, "warehouse", "data", "warehouse.duckdb")
TABLES = ["mart_store_daily", "mart_category_monthly", "mart_product_monthly", "load_audit",
          "mart_online_daily", "mart_online_country_monthly", "mart_online_top_products"]
NUMERIC = {"orders", "order_lines", "units", "revenue", "cost", "profit", "discounts", "rows_in_file", "quality_score",
           "customers", "sales", "returns", "net_sales"}
SNAPSHOT_FILES = [f"{t}.csv" for t in TABLES] + ["manifest.json", "observability.json"]
REQUIRED_FILES = {"mart_store_daily.csv", "manifest.json"}
PAGES_URL = "https://balamurugangethub.github.io/retail-sales-dashboard/data/"
CATALOG_URL = "https://balamurugangethub.github.io/retail-sales-dashboard/"
REMOTE_TTL = 600             # seconds between fetches of the published snapshot
REMOTE_TIMEOUT = 8
TOP_PRODUCTS = 10
TOP_COUNTRIES = 10
AUDIT_ROWS = 12


def default_snapshot_url():
    url = os.environ.get("WAREHOUSE_SNAPSHOT_URL", "").strip()
    if url.lower() in ("off", "none", "0"):
        return None
    return url or (PAGES_URL if os.environ.get("VERCEL") else None)


class Marts:
    """Holds the marts in memory and reloads them when the warehouse file changes."""

    def __init__(self, warehouse_path=None, snapshot_dir=SNAPSHOT_DIR, snapshot_url=None):
        self.warehouse_path = warehouse_path or os.environ.get("WAREHOUSE_PATH") or DEFAULT_WAREHOUSE
        self.snapshot_dir = snapshot_dir
        self.snapshot_url = snapshot_url if snapshot_url is not None else default_snapshot_url()
        self.lock = threading.Lock()
        self.con, self.source, self.stamp = None, None, None

    def connection(self):
        with self.lock:
            if os.path.exists(self.warehouse_path):
                stamp = os.path.getmtime(self.warehouse_path)
            else:
                stamp = ("remote", int(time.time() // REMOTE_TTL)) if self.snapshot_url else None
            if self.con is None or stamp != self.stamp:
                self.con, self.source = self.load(stamp)
                self.stamp = stamp
            return self.con, self.source

    @staticmethod
    def fresh_db():
        con = sqlite3.connect(":memory:", check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    def load(self, stamp):
        con = self.fresh_db()
        note = None
        if isinstance(stamp, tuple):
            try:
                with tempfile.TemporaryDirectory() as folder:
                    fetch_snapshot(self.snapshot_url, folder)
                    return con, dict(self.from_snapshot(con, folder), kind="published",
                                     label="Published by the scheduled cloud run")
            except Exception as e:  # pylint: disable=broad-exception-caught
                # not published yet, or GitHub Pages is not switched on: the committed copy still works
                note = f"The published snapshot could not be fetched ({type(e).__name__}), so the committed one is shown."
        elif stamp is not None:
            try:
                return con, self.from_duckdb(con)
            except ImportError:
                note = "The warehouse file exists but the duckdb package is not installed, so the snapshot is shown."
            except Exception as e:  # pylint: disable=broad-exception-caught
                # for example the file is locked while a pipeline run writes to it
                note = f"The warehouse could not be read ({type(e).__name__}), so the snapshot is shown."
        con.close()
        con = self.fresh_db()
        source = self.from_snapshot(con)
        if note:
            source["note"] = note
        return con, source

    def from_duckdb(self, con):
        import duckdb
        from warehouse import feeds, observe, snapshot
        wh = duckdb.connect(self.warehouse_path, read_only=True)
        try:
            for table in TABLES:
                schema = "raw" if table == "load_audit" else "marts"
                cur = wh.execute(f"SELECT * FROM {schema}.{table}")
                copy_rows(con, table, [d[0] for d in cur.description], cur.fetchall())
            counts = snapshot.layer_counts(wh)
            files = wh.execute("SELECT COUNT(DISTINCT _source_file) FROM raw.sales_lines").fetchone()[0]
            online = {"rows": snapshot.layer_counts(wh, "online_retail"),
                      "files_loaded": snapshot.files_loaded(wh, feeds.ONLINE_RETAIL),
                      "customers": wh.execute("SELECT COUNT(*) FROM marts.dim_customer").fetchone()[0]}
            health = observe.summary(wh)
        finally:
            wh.close()
        last = next((r for r in health.get("runs", []) if r.get("dbt_tests")), None)
        dbt = {"tests": last["dbt_tests"], "passed": last["dbt_tests"] - (last["dbt_tests_failed"] or 0)} if last else None
        return dict(self.days(con), kind="warehouse", label="Live DuckDB warehouse", rows=counts, files_loaded=files,
                    dbt=dbt or self.manifest().get("dbt"), online_retail=online, health=health)

    def from_snapshot(self, con, folder=None):
        folder = folder or self.snapshot_dir
        for table in TABLES:
            path = os.path.join(folder, f"{table}.csv")
            if not os.path.exists(path):
                continue
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader)
                copy_rows(con, table, header, [[number(h, v) for h, v in zip(header, row)] for row in reader])
        m = self.manifest(folder)
        return dict(self.days(con), kind="snapshot", label="Snapshot of the warehouse marts",
                    exported_at=m.get("exported_at"), rows=m.get("rows"), files_loaded=m.get("files_loaded"),
                    dbt=m.get("dbt"), target=m.get("target"), online_retail=m.get("online_retail"),
                    health=read_json(os.path.join(folder, "observability.json")))

    def manifest(self, folder=None):
        return read_json(os.path.join(folder or self.snapshot_dir, "manifest.json"))

    @staticmethod
    def days(con):
        lo, hi = con.execute("SELECT MIN(date_day), MAX(date_day) FROM mart_store_daily").fetchone()
        return {"first_day": str(lo)[:10] if lo else None, "last_day": str(hi)[:10] if hi else None}


def read_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fetch_snapshot(url, folder):
    """Downloads the published snapshot files into folder. Raises if a required one is missing; a mart
    that a failed dbt test skipped is simply left out of that run's snapshot."""
    base = url if url.endswith("/") else url + "/"
    for name in SNAPSHOT_FILES:
        req = urllib.request.Request(base + name, headers={"User-Agent": "retail-sales-dashboard"})
        try:
            with urllib.request.urlopen(req, timeout=REMOTE_TIMEOUT) as r:   # nosec - fixed https URL from settings
                data = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 and name not in REQUIRED_FILES:
                continue
            raise
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)


def number(column, value):
    if column in NUMERIC and value != "":
        return float(value)
    return value if value != "" else None


def copy_rows(con, table, columns, rows):
    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(f"CREATE TABLE {table} ({', '.join(columns)})")
    con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(columns))})",
                    [[str(v) if hasattr(v, "isoformat") else (float(v) if hasattr(v, "as_tuple") else v) for v in r]
                     for r in rows])


def money(v):
    return round(v or 0, 2)


def summary(con, store=None, start=None, end=None):
    """Everything the Warehouse page shows. start and end are months (YYYY-MM), both included."""
    where, params = ["1=1"], []
    if store:
        where.append("store_name = ?")
        params.append(store)
    if start:
        where.append("year_month >= ?")
        params.append(start)
    if end:
        where.append("year_month <= ?")
        params.append(end)
    w = " AND ".join(where)
    k = con.execute(f"SELECT SUM(revenue) revenue, SUM(profit) profit, SUM(orders) orders, SUM(units) units,"
                    f" SUM(discounts) discounts FROM mart_store_daily WHERE {w}", params).fetchone()
    revenue, orders = k["revenue"] or 0, k["orders"] or 0
    kpis = {"revenue": money(revenue), "profit": money(k["profit"]), "orders": int(orders), "units": int(k["units"] or 0),
            "basket": money(revenue / orders if orders else 0),
            "margin_pct": round(100 * (k["profit"] or 0) / revenue, 1) if revenue else 0,
            "discounts": money(k["discounts"])}
    monthly = [{"month": r["year_month"], "revenue": money(r["rev"]), "profit": money(r["prof"]), "orders": int(r["ord"])}
               for r in con.execute(f"SELECT year_month, SUM(revenue) rev, SUM(profit) prof, SUM(orders) ord"
                                    f" FROM mart_store_daily WHERE {w} GROUP BY year_month ORDER BY year_month", params)]
    stores = [{"store": r["store_name"], "region": r["region"], "revenue": money(r["rev"]), "profit": money(r["prof"]),
               "orders": int(r["ord"])}
              for r in con.execute(f"SELECT store_name, region, SUM(revenue) rev, SUM(profit) prof, SUM(orders) ord"
                                   f" FROM mart_store_daily WHERE {w} GROUP BY store_name, region ORDER BY store_name", params)]
    categories = [{"category": r["category"], "revenue": money(r["rev"]), "profit": money(r["prof"])}
                  for r in con.execute(f"SELECT category, SUM(revenue) rev, SUM(profit) prof FROM mart_category_monthly"
                                       f" WHERE {w} GROUP BY category ORDER BY rev DESC", params)]
    products = [{"product": r["product_name"], "category": r["category"], "revenue": money(r["rev"]),
                 "units": int(r["units"])}
                for r in con.execute(f"SELECT product_name, category, SUM(revenue) rev, SUM(units) units"
                                     f" FROM mart_product_monthly WHERE {w} GROUP BY product_name, category"
                                     f" ORDER BY rev DESC LIMIT {TOP_PRODUCTS}", params)]
    return {"kpis": kpis, "monthly": monthly, "stores": stores, "categories": categories, "top_products": products}


def has_table(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def online_summary(con, customers=None):
    """The real-data section: UCI Online Retail II, from the online marts. None until that feed is loaded."""
    if not has_table(con, "mart_online_daily") or not con.execute("SELECT COUNT(*) FROM mart_online_daily").fetchone()[0]:
        return None
    k = con.execute("SELECT SUM(sales) sales, SUM(returns) returns, SUM(net_sales) net, SUM(orders) orders,"
                    " SUM(units) units, MIN(date_day) lo, MAX(date_day) hi FROM mart_online_daily").fetchone()
    orders, sales = k["orders"] or 0, k["sales"] or 0
    countries = con.execute("SELECT COUNT(DISTINCT country) FROM mart_online_country_monthly").fetchone()[0]
    kpis = {"net_sales": money(k["net"]), "sales": money(sales), "returns": money(k["returns"]), "orders": int(orders),
            "units": int(k["units"] or 0), "order_value": money((k["net"] or 0) / orders if orders else 0),
            "return_pct": round(100 * (k["returns"] or 0) / sales, 1) if sales else 0, "countries": countries,
            "customers": customers, "first_day": str(k["lo"])[:10], "last_day": str(k["hi"])[:10]}
    monthly = [{"month": r["year_month"], "net_sales": money(r["net"]), "returns": money(r["ret"]), "orders": int(r["ord"])}
               for r in con.execute("SELECT year_month, SUM(net_sales) net, SUM(returns) ret, SUM(orders) ord"
                                    " FROM mart_online_daily GROUP BY year_month ORDER BY year_month")]
    top = [{"country": r["country"], "net_sales": money(r["net"]), "orders": int(r["ord"])}
           for r in con.execute("SELECT country, SUM(net_sales) net, SUM(orders) ord FROM mart_online_country_monthly"
                                f" GROUP BY country ORDER BY net DESC LIMIT {TOP_COUNTRIES}")]
    products = []
    if has_table(con, "mart_online_top_products"):
        products = [{"product": (r["description"] or r["stock_code"]).title(), "code": r["stock_code"],
                     "units": int(r["units"] or 0), "net_sales": money(r["net_sales"])}
                    for r in con.execute(f"SELECT * FROM mart_online_top_products ORDER BY net_sales DESC LIMIT {TOP_PRODUCTS}")]
    return {"kpis": kpis, "monthly": monthly, "countries": top, "top_products": products}


def audit(con):
    cols = "*" if "feed" in [r[1] for r in con.execute("PRAGMA table_info(load_audit)")] else "*, NULL AS feed"
    return [{"run_at": str(r["run_at"])[:19], "file": r["file_name"], "status": r["status"], "feed": r["feed"],
             "rows": int(r["rows_in_file"] or 0), "score": r["quality_score"], "message": r["message"]}
            for r in con.execute(f"SELECT {cols} FROM load_audit ORDER BY run_at DESC LIMIT {AUDIT_ROWS}")]


def register(app, error_cls, marts=None):
    marts = marts or Marts()

    @app.get("/warehouse")
    def warehouse_page():
        return send_from_directory(app.static_folder, "warehouse.html")

    @app.get("/api/warehouse")
    def warehouse_api():
        try:
            con, source = marts.connection()
        except (OSError, sqlite3.Error) as e:
            raise error_cls(f"The warehouse marts could not be read: {e}") from e
        args = {k: (request.args.get(k) or "").strip() or None for k in ("store", "start", "end")}
        for k in ("start", "end"):
            v = args[k]
            if v and not (len(v) == 7 and v[4] == "-" and v.replace("-", "").isdigit()):
                raise error_cls(f"{k} must be a month like 2026-01")
        months = [r[0] for r in con.execute("SELECT DISTINCT year_month FROM mart_store_daily ORDER BY 1")]
        store_names = [r[0] for r in con.execute("SELECT DISTINCT store_name FROM mart_store_daily ORDER BY 1")]
        if args["store"] and args["store"] not in store_names:
            raise error_cls(f"Unknown store '{args['store']}'")
        public = {k: v for k, v in source.items() if k != "health"}
        online = online_summary(con, (source.get("online_retail") or {}).get("customers"))
        return jsonify(dict(summary(con, **args), source=public, months=months, store_names=store_names,
                            audit=audit(con), online=online, health=source.get("health") or {"runs": []},
                            catalog_url=os.environ.get("WAREHOUSE_CATALOG_URL", CATALOG_URL)))
