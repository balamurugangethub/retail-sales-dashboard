"""Retail Sales Dashboard - Flask + SQLite backend.

Run:  python app.py        then open http://127.0.0.1:5000
"""
import csv
import io
import os
import sqlite3
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import Flask, Response, g, jsonify, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
REQUIRED_COLUMNS = ["date", "store", "category", "product", "orders", "units", "revenue", "cost"]
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,            -- ISO date YYYY-MM-DD
    store TEXT NOT NULL,
    category TEXT NOT NULL,
    product TEXT NOT NULL,
    orders INTEGER NOT NULL,
    units INTEGER NOT NULL,
    revenue_cents INTEGER NOT NULL,
    cost_cents INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sales_day ON sales(day);
CREATE INDEX IF NOT EXISTS idx_sales_store ON sales(store);
CREATE INDEX IF NOT EXISTS idx_sales_category ON sales(category);
"""


def seed_database(db_path):
    import seed
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    con.execute("DELETE FROM sales")
    con.executemany(
        "INSERT INTO sales(day, store, category, product, orders, units, revenue_cents, cost_cents)"
        " VALUES (?,?,?,?,?,?,?,?)", seed.generate_rows())
    con.commit()
    con.close()


def create_app(db_path=None, seed_if_missing=True):
    app = Flask(__name__, static_folder=os.path.join(HERE, "static"), static_url_path="/static")
    app.config["DB_PATH"] = db_path or os.path.join(HERE, "retail.db")
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    if seed_if_missing and not os.path.exists(app.config["DB_PATH"]):
        seed_database(app.config["DB_PATH"])
    else:
        con = sqlite3.connect(app.config["DB_PATH"]); con.executescript(SCHEMA); con.close()

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DB_PATH"])
            g.db.row_factory = sqlite3.Row
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        con = g.pop("db", None)
        if con is not None:
            con.close()

    # ---- helpers -------------------------------------------------------
    def parse_date(value, default):
        if not value:
            return default
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ApiError(f"Invalid date '{value}', expected YYYY-MM-DD")

    def bounds():
        row = db().execute("SELECT MIN(day) lo, MAX(day) hi FROM sales").fetchone()
        if row["lo"] is None:
            return None, None
        return date.fromisoformat(row["lo"]), date.fromisoformat(row["hi"])

    def filters(with_dates=True, override=None):
        """Returns (where_sql, params, start, end) from the query string."""
        lo, hi = bounds()
        if lo is None:
            return "1=0", [], date.today(), date.today()
        start = parse_date(request.args.get("start"), lo)
        end = parse_date(request.args.get("end"), hi)
        if override:
            start, end = override
        if start > end:
            raise ApiError("start must not be after end")
        where, params = [], []
        if with_dates:
            where += ["day >= ?", "day <= ?"]
            params += [start.isoformat(), end.isoformat()]
        for col, arg in (("store", "store"), ("category", "category")):
            v = request.args.get(arg)
            if v:
                where.append(f"{col} = ?")
                params.append(v)
        return " AND ".join(where) or "1=1", params, start, end

    def money(cents):
        return round((cents or 0) / 100, 2)

    def totals(where, params):
        r = db().execute(
            f"SELECT COALESCE(SUM(revenue_cents),0) rev, COALESCE(SUM(cost_cents),0) cost,"
            f" COALESCE(SUM(orders),0) orders FROM sales WHERE {where}", params).fetchone()
        profit = r["rev"] - r["cost"]
        basket = (r["rev"] / 100 / r["orders"]) if r["orders"] else 0
        return {"sales": money(r["rev"]), "profit": money(profit), "orders": r["orders"],
                "basket": round(basket, 2)}

    def prev_period(start, end):
        """Whole calendar months compare with the preceding whole months; otherwise equal-length days."""
        next_day = end + timedelta(days=1)
        if start.day == 1 and next_day.day == 1:
            months = (end.year - start.year) * 12 + end.month - start.month + 1
            idx = start.year * 12 + start.month - 1 - months
            return date(idx // 12, idx % 12 + 1, 1), start - timedelta(days=1)
        length = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        return prev_end - timedelta(days=length - 1), prev_end

    def pct_change(cur, prev):
        return None if not prev else round((cur - prev) / prev * 100, 1)

    # ---- pages ---------------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    # ---- API -----------------------------------------------------------
    @app.get("/api/meta")
    def meta():
        lo, hi = bounds()
        d = db()
        return jsonify({
            "min_date": lo.isoformat() if lo else None,
            "max_date": hi.isoformat() if hi else None,
            "stores": [r[0] for r in d.execute("SELECT DISTINCT store FROM sales ORDER BY store")],
            "categories": [r[0] for r in d.execute("SELECT DISTINCT category FROM sales ORDER BY category")],
            "rows": d.execute("SELECT COUNT(*) FROM sales").fetchone()[0],
        })

    @app.get("/api/kpis")
    def kpis():
        where, params, start, end = filters()
        cur = totals(where, params)
        prev_start, prev_end = prev_period(start, end)
        pw, pp, _, _ = filters(override=(prev_start, prev_end))
        prev = totals(pw, pp)
        return jsonify({
            "period": {"start": start.isoformat(), "end": end.isoformat(),
                       "prev_start": prev_start.isoformat(), "prev_end": prev_end.isoformat()},
            "current": cur, "previous": prev,
            "change_pct": {k: pct_change(cur[k], prev[k]) for k in cur},
        })

    @app.get("/api/by-store")
    def by_store():
        where, params, _, _ = filters()
        rows = db().execute(
            f"SELECT store, SUM(revenue_cents) rev, SUM(cost_cents) cost, SUM(orders) orders"
            f" FROM sales WHERE {where} GROUP BY store ORDER BY store", params).fetchall()
        return jsonify([{"store": r["store"], "sales": money(r["rev"]),
                         "profit": money(r["rev"] - r["cost"]), "orders": r["orders"]} for r in rows])

    @app.get("/api/trend")
    def trend():
        grain = request.args.get("grain", "month")
        fmt = {"month": "%Y-%m", "week": "%Y-W%W", "day": "%Y-%m-%d"}.get(grain)
        if not fmt:
            raise ApiError("grain must be month, week or day")
        where, params, _, _ = filters()
        rows = db().execute(
            f"SELECT strftime('{fmt}', day) period, SUM(revenue_cents) rev, SUM(cost_cents) cost,"
            f" SUM(orders) orders FROM sales WHERE {where} GROUP BY period ORDER BY period", params).fetchall()
        return jsonify([{"period": r["period"], "sales": money(r["rev"]),
                         "profit": money(r["rev"] - r["cost"]), "orders": r["orders"]} for r in rows])

    @app.get("/api/categories")
    def categories():
        where, params, _, _ = filters()
        rows = db().execute(
            f"SELECT category, SUM(revenue_cents) rev FROM sales WHERE {where}"
            f" GROUP BY category ORDER BY rev DESC", params).fetchall()
        return jsonify([{"category": r["category"], "sales": money(r["rev"])} for r in rows])

    @app.get("/api/top-products")
    def top_products():
        try:
            limit = max(1, min(int(request.args.get("limit", 8)), 50))
        except ValueError:
            raise ApiError("limit must be an integer")
        where, params, _, _ = filters()
        rows = db().execute(
            f"SELECT product, category, SUM(revenue_cents) rev, SUM(cost_cents) cost, SUM(units) units"
            f" FROM sales WHERE {where} GROUP BY product, category ORDER BY rev DESC LIMIT ?",
            params + [limit]).fetchall()
        return jsonify([{"product": r["product"], "category": r["category"], "sales": money(r["rev"]),
                         "profit": money(r["rev"] - r["cost"]), "units": r["units"]} for r in rows])

    @app.get("/api/heatmap")
    def heatmap():
        where, params, _, _ = filters()
        rows = db().execute(
            f"SELECT store, strftime('%Y-%m', day) period, SUM(revenue_cents) rev FROM sales"
            f" WHERE {where} GROUP BY store, period ORDER BY store, period", params).fetchall()
        return jsonify([{"store": r["store"], "period": r["period"], "sales": money(r["rev"])} for r in rows])

    @app.get("/api/insights")
    def insights():
        where, params, start, end = filters()
        d = db()
        out = []
        stores = d.execute(
            f"SELECT store, SUM(revenue_cents) rev FROM sales WHERE {where} GROUP BY store ORDER BY rev DESC",
            params).fetchall()
        if not stores:
            return jsonify([])
        total = sum(r["rev"] for r in stores)
        if len(stores) > 1:
            top, low = stores[0], stores[-1]
            gap = (top["rev"] - low["rev"]) / top["rev"] * 100
            out.append({"level": "info", "text":
                        f"{top['store']} leads with ${top['rev']/100000:.1f}K ({top['rev']/total*100:.0f}% of sales). "
                        f"{low['store']} is lowest at ${low['rev']/100000:.1f}K, {gap:.0f}% below the leader."})
            avg = total / len(stores)
            for r in stores:
                if r["rev"] < 0.85 * avg:
                    out.append({"level": "warn", "text":
                                f"{r['store']} is {100 - r['rev']/avg*100:.0f}% below the store average "
                                f"(${r['rev']/100000:.1f}K vs ${avg/100000:.1f}K). Review staffing and promotions."})
        # growth vs previous period, per store
        prev_start, prev_end = prev_period(start, end)
        pw, pp, _, _ = filters(override=(prev_start, prev_end))
        prev = {r["store"]: r["rev"] for r in d.execute(
            f"SELECT store, SUM(revenue_cents) rev FROM sales WHERE {pw} GROUP BY store", pp)}
        growth = [(r["store"], pct_change(r["rev"], prev.get(r["store"]))) for r in stores]
        growth = [(s, g_) for s, g_ in growth if g_ is not None]
        if growth:
            best, worst = max(growth, key=lambda x: x[1]), min(growth, key=lambda x: x[1])
            out.append({"level": "good", "text": f"Fastest growth vs previous period: {best[0]} ({best[1]:+.1f}%)."})
            if worst[1] < 0:
                out.append({"level": "warn", "text": f"{worst[0]} is declining vs previous period ({worst[1]:+.1f}%)."})
        cat = d.execute(
            f"SELECT category, SUM(revenue_cents) rev FROM sales WHERE {where} GROUP BY category ORDER BY rev DESC LIMIT 1",
            params).fetchone()
        if cat:
            out.append({"level": "info", "text": f"{cat['category']} is the top category at {cat['rev']/total*100:.0f}% of sales."})
        peak = d.execute(
            f"SELECT strftime('%Y-%m', day) p, SUM(revenue_cents) rev FROM sales WHERE {where}"
            f" GROUP BY p ORDER BY rev DESC LIMIT 1", params).fetchone()
        if peak:
            out.append({"level": "info", "text": f"Peak month: {peak['p']} with ${peak['rev']/100000:.1f}K in sales."})
        return jsonify(out)

    @app.get("/api/export.csv")
    def export_csv():
        where, params, _, _ = filters()
        rows = db().execute(
            f"SELECT day, store, category, product, orders, units, revenue_cents, cost_cents"
            f" FROM sales WHERE {where} ORDER BY day, store, product", params).fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(REQUIRED_COLUMNS)
        for r in rows:
            w.writerow([r["day"], r["store"], r["category"], r["product"], r["orders"], r["units"],
                        f"{r['revenue_cents']/100:.2f}", f"{r['cost_cents']/100:.2f}"])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=retail_sales_export.csv"})

    @app.post("/api/upload")
    def upload():
        f = request.files.get("file")
        if not f:
            raise ApiError("No file uploaded (field name must be 'file')")
        mode = request.form.get("mode", "replace")
        if mode not in ("replace", "append"):
            raise ApiError("mode must be replace or append")
        try:
            text = f.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ApiError("File must be UTF-8 encoded CSV")
        reader = csv.DictReader(io.StringIO(text))
        header = [h.strip().lower() for h in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise ApiError("Missing columns: " + ", ".join(missing))
        good, errors = [], []
        for n, raw in enumerate(reader, start=2):
            r = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
            try:
                day = date.fromisoformat(r["date"]).isoformat()
                if not (r["store"] and r["category"] and r["product"]):
                    raise ValueError("store, category and product are required")
                orders, units = int(r["orders"]), int(r["units"])
                rev, cost = Decimal(r["revenue"]), Decimal(r["cost"])
                if orders < 0 or units < 0 or rev < 0 or cost < 0:
                    raise ValueError("numbers must not be negative")
                good.append((day, r["store"], r["category"], r["product"], orders, units,
                             int(rev * 100), int(cost * 100)))
            except (ValueError, InvalidOperation) as e:
                errors.append(f"line {n}: {e}")
        if errors:
            raise ApiError(f"{len(errors)} invalid row(s); nothing imported. " + "; ".join(errors[:5]))
        if not good:
            raise ApiError("File has no data rows")
        d = db()
        with d:
            if mode == "replace":
                d.execute("DELETE FROM sales")
            d.executemany(
                "INSERT INTO sales(day, store, category, product, orders, units, revenue_cents, cost_cents)"
                " VALUES (?,?,?,?,?,?,?,?)", good)
        return jsonify({"imported": len(good), "mode": mode})

    @app.post("/api/reset")
    def reset():
        seed_database(app.config["DB_PATH"])
        return jsonify({"ok": True})

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"error": "File too large (max 5 MB)"}), 413

    @app.errorhandler(ApiError)
    def api_error(e):
        return jsonify({"error": str(e)}), 400

    return app


class ApiError(Exception):
    pass


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=False)
