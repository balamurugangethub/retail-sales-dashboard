"""Routes for the dashboard builder, data import/cleaning, data editing and the AI assistant."""
import json
import re
import time
import uuid
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import jsonify, request, send_from_directory

import cleaning
from queries import CHART_TYPES, QueryError, catalog, run_query, validate_spec

TEXT_FIELDS = ["store", "category", "product"]
MAX_WIDGETS = 40
STAGING_TTL_SECONDS = 2 * 3600
FIELD_CHOICES = [[f, cleaning.FIELD_LABELS[f]] for f in cleaning.FIELDS]

SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboards (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    spec TEXT NOT NULL,           -- JSON {"widgets": [...], "filters": {...}}
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS staged_imports (
    id TEXT PRIMARY KEY,
    created REAL NOT NULL,
    filename TEXT NOT NULL,
    rows TEXT NOT NULL            -- JSON list of cleaned rows waiting for the user to confirm
);
"""


def clean_widget(w, error_cls):
    if not isinstance(w, dict):
        raise error_cls("Each widget must be an object")
    try:
        dims, measures = validate_spec(list(w.get("dims") or []), list(w.get("measures") or []))
    except QueryError as e:
        raise error_cls(str(e)) from e
    chart = w.get("chart") if w.get("chart") in CHART_TYPES else "bar"
    out = {"chart": chart, "dims": dims, "measures": measures,
           "title": str(w.get("title") or "")[:80], "span": w.get("span") if w.get("span") in (1, 2, 3, 4) else 2}
    if isinstance(w.get("limit"), int) and 0 < w["limit"] <= 500:
        out["limit"] = w["limit"]
    if isinstance(w.get("sort"), str) and w["sort"].lstrip("+-") in dims + measures:
        out["sort"] = w["sort"]
    if isinstance(w.get("filters"), dict):
        out["filters"] = {k: str(v)[:120] for k, v in w["filters"].items() if k in TEXT_FIELDS and v}
    return out


def canonical_names(counts):
    """{value: rows} -> {value: replacement} merging spacing/case variants into the most used spelling."""
    groups = {}
    for v, n in counts.items():
        tidy = re.sub(r"\s+", " ", v).strip()
        groups.setdefault(tidy.lower(), Counter())[v] += n
    out = {}
    for c in groups.values():
        best = re.sub(r"\s+", " ", cleaning.preferred_spelling(c)).strip()
        for v in c:
            if v != best:
                out[v] = best
    return out


def register(app, db, filters, insights, error_cls, assistant):  # pylint: disable=too-many-locals,too-many-statements
    ApiError = error_cls  # pylint: disable=invalid-name

    with app.app_context():
        c = db()
        c.executescript(SCHEMA)
        c.commit()

    def body_json():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ApiError("Expected a JSON object body")
        return data

    def values():
        d = db()
        out = {}
        for f in TEXT_FIELDS:
            limit = 300 if f == "product" else 1000
            out[f] = [r[0] for r in d.execute(
                f"SELECT {f} FROM sales GROUP BY {f} ORDER BY SUM(revenue_cents) DESC LIMIT {limit}")]
        return out

    def query_with(source, dims, measures, sort=None, limit=None):
        where, params, _, _ = filters(source=source)
        try:
            return run_query(db(), dims, measures, where, params, sort=sort, limit=limit)
        except QueryError as e:
            raise ApiError(str(e)) from e

    # ---- pages -----------------------------------------------------------------
    @app.get("/builder")
    def builder_page():
        return send_from_directory(app.static_folder, "builder.html")

    @app.get("/data")
    def data_page():
        return send_from_directory(app.static_folder, "data.html")

    # ---- builder ---------------------------------------------------------------
    @app.get("/api/fields")
    def fields():
        out = catalog()
        out["values"] = values()
        out["assistant"] = assistant.provider
        return jsonify(out)

    @app.get("/api/query")
    def query():
        dims = [x for x in request.args.get("dims", "").split(",") if x]
        measures = [x for x in request.args.get("measures", "").split(",") if x]
        limit = request.args.get("limit")
        if limit is not None:
            try:
                limit = int(limit)
            except ValueError as e:
                raise ApiError("limit must be an integer") from e
        return jsonify(query_with(None, dims, measures, request.args.get("sort") or None, limit))

    def load_spec(raw):
        spec = raw.get("spec") if isinstance(raw.get("spec"), dict) else {}
        widgets = spec.get("widgets") or []
        if not isinstance(widgets, list) or len(widgets) > MAX_WIDGETS:
            raise ApiError(f"A dashboard holds at most {MAX_WIDGETS} widgets")
        flt = spec.get("filters") if isinstance(spec.get("filters"), dict) else {}
        flt = {k: str(v)[:120] for k, v in flt.items() if k in TEXT_FIELDS + ["start", "end", "preset"] and v}
        return {"widgets": [clean_widget(w, ApiError) for w in widgets], "filters": flt}

    def dash_name(raw):
        name = str(raw.get("name") or "").strip()[:80]
        if not name:
            raise ApiError("Give the dashboard a name")
        return name

    def dash_row(r):
        return {"id": r["id"], "name": r["name"], "spec": json.loads(r["spec"]), "updated": r["updated"]}

    @app.get("/api/dashboards")
    def list_dashboards():
        rows = db().execute("SELECT id, name, updated FROM dashboards ORDER BY name COLLATE NOCASE").fetchall()
        return jsonify([dict(r) for r in rows])

    @app.get("/api/dashboards/<int:dash_id>")
    def get_dashboard(dash_id):
        r = db().execute("SELECT * FROM dashboards WHERE id = ?", (dash_id,)).fetchone()
        if not r:
            return jsonify({"error": "Dashboard not found"}), 404
        return jsonify(dash_row(r))

    @app.post("/api/dashboards")
    def create_dashboard():
        raw = body_json()
        name, spec = dash_name(raw), load_spec(raw)
        d = db()
        with d:
            cur = d.execute("INSERT INTO dashboards(name, spec, updated) VALUES (?,?,?)",
                            (name, json.dumps(spec), time.strftime("%Y-%m-%d %H:%M:%S")))
        return get_dashboard(cur.lastrowid)

    @app.put("/api/dashboards/<int:dash_id>")
    def update_dashboard(dash_id):
        raw = body_json()
        name, spec = dash_name(raw), load_spec(raw)
        d = db()
        with d:
            n = d.execute("UPDATE dashboards SET name = ?, spec = ?, updated = ? WHERE id = ?",
                          (name, json.dumps(spec), time.strftime("%Y-%m-%d %H:%M:%S"), dash_id)).rowcount
        if not n:
            return jsonify({"error": "Dashboard not found"}), 404
        return get_dashboard(dash_id)

    @app.delete("/api/dashboards/<int:dash_id>")
    def delete_dashboard(dash_id):
        d = db()
        with d:
            d.execute("DELETE FROM dashboards WHERE id = ?", (dash_id,))
        return jsonify({"ok": True})

    # ---- import with cleaning -----------------------------------------------------
    @app.post("/api/import/preview")
    def import_preview():
        f = request.files.get("file")
        if not f:
            raise ApiError("No file uploaded (field name must be 'file')")
        try:
            header, rows = cleaning.read_table(f.filename, f.read())
        except cleaning.CleaningError as e:
            raise ApiError(str(e)) from e
        mapping = cleaning.suggest_mapping(header)
        if request.form.get("mapping"):
            try:
                custom = json.loads(request.form["mapping"])
            except ValueError as e:
                raise ApiError("mapping must be JSON") from e
            if not isinstance(custom, dict):
                raise ApiError("mapping must be an object")
            mapping = {k: (custom.get(k) or None) for k in cleaning.FIELDS}
        drop_dupes = request.form.get("drop_duplicates", "1") != "0"
        try:
            report = cleaning.clean_rows(header, rows, mapping, drop_duplicates=drop_dupes)
        except cleaning.CleaningError as e:
            return jsonify({"columns": header, "mapping": mapping, "fields": FIELD_CHOICES,
                            "sample_raw": rows[:5], "error": str(e), "import_id": None}), 200
        import_id = None
        if not app.config["READ_ONLY"] and report["rows"]:
            import_id = uuid.uuid4().hex
            d = db()
            with d:
                d.execute("DELETE FROM staged_imports WHERE created < ?", (time.time() - STAGING_TTL_SECONDS,))
                d.execute("INSERT INTO staged_imports(id, created, filename, rows) VALUES (?,?,?,?)",
                          (import_id, time.time(), f.filename or "upload", json.dumps(report["rows"])))
        good = report["rows"]
        return jsonify({
            "import_id": import_id, "columns": header, "mapping": mapping, "fields": FIELD_CHOICES,
            "total_rows": len(rows), "clean_rows": len(good), "fixes": report["fixes"],
            "problems": [{"line": n, "reason": r} for n, r in report["problems"][:200]],
            "problem_count": len(report["problems"]), "warnings": report["warnings"],
            "date_order": "day first" if report["day_first"] else "month first",
            "sample_raw": rows[:5],
            "sample_clean": [[r[0], r[1], r[2], r[3], r[4], r[5], r[6] / 100, r[7] / 100] for r in good[:8]],
            "date_range": [min(r[0] for r in good), max(r[0] for r in good)] if good else None,
            "read_only": app.config["READ_ONLY"],
        })

    @app.post("/api/import/commit")
    def import_commit():
        raw = body_json()
        mode = raw.get("mode", "replace")
        if mode not in ("replace", "append"):
            raise ApiError("mode must be replace or append")
        d = db()
        r = d.execute("SELECT rows FROM staged_imports WHERE id = ?", (str(raw.get("import_id")),)).fetchone()
        if not r:
            raise ApiError("This preview has expired. Upload the file again.")
        rows = json.loads(r["rows"])
        with d:
            if mode == "replace":
                d.execute("DELETE FROM sales")
            d.executemany(
                "INSERT INTO sales(day, store, category, product, orders, units, revenue_cents, cost_cents)"
                " VALUES (?,?,?,?,?,?,?,?)", rows)
            d.execute("DELETE FROM staged_imports WHERE id = ?", (raw.get("import_id"),))
        return jsonify({"imported": len(rows), "mode": mode})

    # ---- browsing and editing rows -----------------------------------------------
    @app.get("/api/rows")
    def list_rows():
        where, params, _, _ = filters()
        q = (request.args.get("q") or "").strip()
        if q:
            where += " AND (store LIKE ? OR category LIKE ? OR product LIKE ?)"
            params += [f"%{q}%"] * 3
        try:
            page = max(1, int(request.args.get("page", 1)))
            size = max(1, min(int(request.args.get("size", 50)), 500))
        except ValueError as e:
            raise ApiError("page and size must be integers") from e
        sort_col = {"date": "day", "store": "store", "category": "category", "product": "product",
                    "orders": "orders", "units": "units", "revenue": "revenue_cents",
                    "cost": "cost_cents"}.get((request.args.get("sort") or "date").lstrip("-"), "day")
        direction = "DESC" if (request.args.get("sort") or "-date").startswith("-") else "ASC"
        d = db()
        total = d.execute(f"SELECT COUNT(*) FROM sales WHERE {where}", params).fetchone()[0]
        rows = d.execute(
            f"SELECT id, day, store, category, product, orders, units, revenue_cents, cost_cents FROM sales"
            f" WHERE {where} ORDER BY {sort_col} {direction}, id LIMIT ? OFFSET ?",
            params + [size, (page - 1) * size]).fetchall()
        return jsonify({"total": total, "page": page, "size": size, "rows": [
            {"id": r["id"], "date": r["day"], "store": r["store"], "category": r["category"],
             "product": r["product"], "orders": r["orders"], "units": r["units"],
             "revenue": r["revenue_cents"] / 100, "cost": r["cost_cents"] / 100} for r in rows]})

    def parse_edit(field, value):
        """Validates one edited cell; returns (column, stored value)."""
        v = str(value if value is not None else "").strip()
        if field == "date":
            try:
                return "day", date.fromisoformat(v).isoformat()
            except ValueError as e:
                raise ApiError("Date must be YYYY-MM-DD") from e
        if field in TEXT_FIELDS:
            v = re.sub(r"\s+", " ", v)
            if not v or len(v) > 120:
                raise ApiError(f"{field} must be 1-120 characters")
            return field, v
        if field in ("orders", "units"):
            if not v.isdigit():
                raise ApiError(f"{field} must be a whole number of 0 or more")
            return field, int(v)
        if field in ("revenue", "cost"):
            try:
                n = Decimal(v)
            except InvalidOperation as e:
                raise ApiError(f"{field} must be a number") from e
            if not n.is_finite() or n < 0:
                raise ApiError(f"{field} must be 0 or more")
            return field + "_cents", int((n * 100).to_integral_value())
        raise ApiError(f"Unknown field '{field}'")

    @app.patch("/api/rows/<int:row_id>")
    def edit_row(row_id):
        raw = body_json()
        if not raw:
            raise ApiError("Nothing to change")
        sets = [parse_edit(k, v) for k, v in raw.items()]
        d = db()
        with d:
            n = d.execute(f"UPDATE sales SET {', '.join(c + ' = ?' for c, _ in sets)} WHERE id = ?",
                          [v for _, v in sets] + [row_id]).rowcount
        if not n:
            return jsonify({"error": "Row not found"}), 404
        return jsonify({"ok": True})

    @app.delete("/api/rows/<int:row_id>")
    def delete_row(row_id):
        d = db()
        with d:
            n = d.execute("DELETE FROM sales WHERE id = ?", (row_id,)).rowcount
        if not n:
            return jsonify({"error": "Row not found"}), 404
        return jsonify({"ok": True})

    def text_field(raw):
        field = raw.get("field")
        if field not in TEXT_FIELDS:
            raise ApiError("field must be store, category or product")
        return field

    @app.post("/api/data/rename")
    def rename_value():
        raw = body_json()
        field = text_field(raw)
        _col, new = parse_edit(field, raw.get("to"))
        d = db()
        with d:
            n = d.execute(f"UPDATE sales SET {field} = ? WHERE {field} = ?", (new, str(raw.get("from")))).rowcount
        if not n:
            raise ApiError(f"No rows have {field} '{raw.get('from')}'")
        return jsonify({"updated": n})

    @app.post("/api/data/delete")
    def delete_value():
        raw = body_json()
        field = text_field(raw)
        d = db()
        with d:
            n = d.execute(f"DELETE FROM sales WHERE {field} = ?", (str(raw.get("value")),)).rowcount
        return jsonify({"deleted": n})

    @app.post("/api/data/tidy")
    def tidy():
        d = db()
        renamed = {}
        with d:
            for f in TEXT_FIELDS:
                counts = {r[0]: r[1] for r in d.execute(f"SELECT {f}, COUNT(*) FROM sales GROUP BY {f}")}
                for old, new in canonical_names(counts).items():
                    d.execute(f"UPDATE sales SET {f} = ? WHERE {f} = ?", (new, old))
                    renamed[f] = renamed.get(f, 0) + counts[old]
            dupes = d.execute(
                "DELETE FROM sales WHERE id NOT IN (SELECT MIN(id) FROM sales GROUP BY day, store, category,"
                " product, orders, units, revenue_cents, cost_cents)").rowcount
        return jsonify({"names_fixed": renamed, "duplicates_removed": dupes})

    # ---- assistant ---------------------------------------------------------------
    def summary(source):
        out = {}
        out["totals"] = {}
        for group in (["sales", "profit", "orders"], ["units", "margin", "basket"]):
            t = query_with(source, [], group)
            if t["rows"]:
                out["totals"].update(zip(group, t["rows"][0]))
        for dim, lim in (("store", 30), ("category", 20), ("month", 24), ("product", 10)):
            r = query_with(source, [dim], ["sales", "profit", "orders"], sort="-sales", limit=lim)
            out["by_" + dim] = r["rows"]
        out["insights"] = [i["text"] for i in insights(source)]
        return out

    @app.get("/api/assistant")
    def assistant_status():
        return jsonify({"provider": assistant.provider})

    @app.post("/api/assistant")
    def assistant_chat():
        raw = body_json()
        message = str(raw.get("message") or "").strip()
        if not message:
            raise ApiError("Type a message")
        source = {k: str(v) for k, v in (raw.get("filters") or {}).items()
                  if k in TEXT_FIELDS + ["start", "end"] and v} if isinstance(raw.get("filters"), dict) else {}
        widgets = [w for w in raw.get("widgets") or [] if isinstance(w, dict)][:MAX_WIDGETS]

        def ask(dims, measures, extra=None):
            return query_with(dict(source, **(extra or {})), dims, measures)

        ctx = {"values": values(), "widgets": widgets, "query": ask,
               "insights": lambda: insights(source)}
        if assistant.client:
            ctx["summary"] = summary(source)
        out = assistant.respond(message, ctx)
        out["read_only"] = app.config["READ_ONLY"]
        return jsonify(out)
