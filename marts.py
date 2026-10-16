"""The Warehouse page: KPIs and charts read from the dbt marts, not from the app's own sales table.

Where the data comes from, in order:
1. The DuckDB warehouse (warehouse/data/warehouse.duckdb, or WAREHOUSE_PATH) when it exists and the
   duckdb package is installed: the marts are read live, so a pipeline run shows up on refresh.
2. Otherwise the snapshot of the same marts committed in warehouse/snapshot/ (this is what the live
   site on Vercel uses: it has no warehouse and no always-on server).
The small reporting marts are copied into an in-memory SQLite database, so every query below is the
same whichever source was used.
"""
import csv
import json
import os
import sqlite3
import threading

from flask import jsonify, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_DIR = os.path.join(HERE, "warehouse", "snapshot")
DEFAULT_WAREHOUSE = os.path.join(HERE, "warehouse", "data", "warehouse.duckdb")
TABLES = ["mart_store_daily", "mart_category_monthly", "mart_product_monthly", "load_audit"]
NUMERIC = {"orders", "order_lines", "units", "revenue", "cost", "profit", "discounts", "rows_in_file", "quality_score"}
TOP_PRODUCTS = 10
AUDIT_ROWS = 12


class Marts:
    """Holds the marts in memory and reloads them when the warehouse file changes."""

    def __init__(self, warehouse_path=None, snapshot_dir=SNAPSHOT_DIR):
        self.warehouse_path = warehouse_path or os.environ.get("WAREHOUSE_PATH") or DEFAULT_WAREHOUSE
        self.snapshot_dir = snapshot_dir
        self.lock = threading.Lock()
        self.con, self.source, self.stamp = None, None, None

    def connection(self):
        with self.lock:
            stamp = os.path.getmtime(self.warehouse_path) if os.path.exists(self.warehouse_path) else None
            if self.con is None or stamp != self.stamp:
                self.con, self.source = self.load(stamp)
                self.stamp = stamp
            return self.con, self.source

    def load(self, stamp):
        con = sqlite3.connect(":memory:", check_same_thread=False)
        con.row_factory = sqlite3.Row
        note = None
        if stamp is not None:
            try:
                return con, self.from_duckdb(con)
            except ImportError:
                note = "The warehouse file exists but the duckdb package is not installed, so the snapshot is shown."
            except Exception as e:  # pylint: disable=broad-exception-caught
                # for example the file is locked while a pipeline run writes to it
                note = f"The warehouse could not be read ({type(e).__name__}), so the snapshot is shown."
            con.close()
            con = sqlite3.connect(":memory:", check_same_thread=False)
            con.row_factory = sqlite3.Row
        source = self.from_snapshot(con)
        if note:
            source["note"] = note
        return con, source

    def from_duckdb(self, con):
        import duckdb
        wh = duckdb.connect(self.warehouse_path, read_only=True)
        try:
            for table in TABLES:
                schema = "raw" if table == "load_audit" else "marts"
                cur = wh.execute(f"SELECT * FROM {schema}.{table}")
                copy_rows(con, table, [d[0] for d in cur.description], cur.fetchall())
            counts = {k: wh.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for k, t in
                      (("raw", "raw.sales_lines"), ("staging", "staging.stg_sales_lines"), ("fact", "marts.fct_sales"))}
            files = wh.execute("SELECT COUNT(DISTINCT _source_file) FROM raw.sales_lines").fetchone()[0]
        finally:
            wh.close()
        return dict(self.days(con), kind="warehouse", label="Live DuckDB warehouse", rows=counts, files_loaded=files,
                    dbt=self.manifest().get("dbt"))

    def from_snapshot(self, con):
        for table in TABLES:
            path = os.path.join(self.snapshot_dir, f"{table}.csv")
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader)
                copy_rows(con, table, header, [[number(h, v) for h, v in zip(header, row)] for row in reader])
        m = self.manifest()
        return dict(self.days(con), kind="snapshot", label="Snapshot of the warehouse marts",
                    exported_at=m.get("exported_at"), rows=m.get("rows"), files_loaded=m.get("files_loaded"),
                    dbt=m.get("dbt"))

    def manifest(self):
        path = os.path.join(self.snapshot_dir, "manifest.json")
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def days(con):
        lo, hi = con.execute("SELECT MIN(date_day), MAX(date_day) FROM mart_store_daily").fetchone()
        return {"first_day": str(lo)[:10] if lo else None, "last_day": str(hi)[:10] if hi else None}


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


def audit(con):
    return [{"run_at": str(r["run_at"])[:19], "file": r["file_name"], "status": r["status"],
             "rows": int(r["rows_in_file"] or 0), "score": r["quality_score"], "message": r["message"]}
            for r in con.execute(f"SELECT * FROM load_audit ORDER BY run_at DESC LIMIT {AUDIT_ROWS}")]


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
        return jsonify(dict(summary(con, **args), source=source, months=months, store_names=store_names,
                            audit=audit(con)))
