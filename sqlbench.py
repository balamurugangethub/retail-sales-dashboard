"""SQL workbench: run read-only SQL on your own tables.

Every uploaded file lives in a table with generated column names (ds_<id>: c0, c1, ...). For the
workbench each file is shown as a temporary view with a readable name and readable column names
(for example `ward_visits.wait_min`), so people can write ordinary SQL. A query may only read:
the authorizer below refuses everything except SELECT on those views, the sales table and the
raw ds_ tables, and a time and row limit stop runaway queries.
"""
import json
import re
import sqlite3
import time

from flask import jsonify, request, send_from_directory

MAX_SQL_CHARS = 5000
MAX_ROWS = 1000
MAX_SECONDS = 5
MAX_CELL_BYTES = 1_000_000
PROGRESS_STEPS = 10_000

ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
RESERVED_TABLES = {"sales", "datasets", "dashboards", "staged_imports", "sqlite_master", "sqlite_temp_master"}
KEYWORDS = {"select", "from", "where", "group", "order", "by", "limit", "table", "index", "values", "insert",
            "update", "delete", "join", "on", "as", "and", "or", "not", "null", "case", "when", "then", "else",
            "end", "in", "is", "like", "union", "all", "distinct", "having", "create", "drop", "default",
            "primary", "key", "check", "to", "set", "with", "between", "exists", "into", "offset", "desc", "asc"}
SALES_COLUMNS = [("day", "date"), ("store", "text"), ("category", "text"), ("product", "text"),
                 ("orders", "number"), ("units", "number"), ("revenue_cents", "number"), ("cost_cents", "number")]


class SqlError(ValueError):
    pass


def readable(name, fallback, taken):
    """A lower_snake_case SQL name that is not already in `taken` and is not a SQL keyword."""
    base = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")[:40] or fallback
    if base[0].isdigit():
        base = f"{fallback}_{base}"
    if base in KEYWORDS:
        base += "_"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def make_views(con):
    """Create one temporary view per dataset; returns what the schema panel shows."""
    taken, tables = set(RESERVED_TABLES), []
    for row in con.execute("SELECT id, name, columns, rows FROM datasets ORDER BY id").fetchall():
        names, shown = set(), []
        for i, col in enumerate(json.loads(row["columns"])):
            shown.append({"name": readable(col["name"], "column", names), "type": col["type"], "source": f"c{i}"})
        view = readable(row["name"], "table", taken)
        picks = ", ".join(f'{c["source"]} AS "{c["name"]}"' for c in shown)
        con.execute(f'CREATE TEMP VIEW "{view}" AS SELECT {picks} FROM ds_{row["id"]}')
        tables.append({"name": view, "dataset_id": row["id"], "title": row["name"], "rows": row["rows"],
                       "columns": [{"name": c["name"], "type": c["type"]} for c in shown]})
    return tables


def drop_views(con, tables):
    for t in tables:
        con.execute(f'DROP VIEW IF EXISTS temp."{t["name"]}"')


def schema(con):
    tables = make_views(con)
    drop_views(con, tables)
    count = con.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    if count:
        tables.insert(0, {"name": "sales", "dataset_id": None, "title": "Sales", "rows": count,
                          "columns": [{"name": n, "type": t} for n, t in SALES_COLUMNS]})
    return tables


INTERNAL_TABLES = {"sqlite_master", "sqlite_schema", "sqlite_temp_master", "sqlite_temp_schema",
                   "sqlite_sequence", "sqlite_stat1"}


def authorizer(con, allowed_tables):
    """Refuse everything but SELECT, and reading any stored table that is not one of the user's."""
    stored = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    off_limits = ((stored | INTERNAL_TABLES) - allowed_tables)

    def check(action, arg1, _arg2, _db, _source):
        if action not in ALLOWED_ACTIONS:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ and arg1 in off_limits and not re.fullmatch(r"ds_\d+", arg1 or ""):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return check


def cell(v):
    return f"<{len(v)} bytes>" if isinstance(v, bytes) else v


def run(con, sql, limit=MAX_ROWS):
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise SqlError("Type a query first.")
    if len(sql) > MAX_SQL_CHARS:
        raise SqlError(f"Queries are limited to {MAX_SQL_CHARS:,} characters.")
    limit = max(1, min(int(limit or MAX_ROWS), MAX_ROWS))
    tables = make_views(con)
    allowed = {t["name"] for t in tables} | {"sales"}
    started = time.monotonic()
    con.execute("PRAGMA query_only = ON")
    con.set_authorizer(authorizer(con, allowed))
    con.set_progress_handler(lambda: 1 if time.monotonic() - started > MAX_SECONDS else 0, PROGRESS_STEPS)
    old_limit = con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_CELL_BYTES)
    try:
        cur = con.execute(sql)
        if cur.description is None:
            raise SqlError("Only SELECT queries are allowed.")
        columns = [d[0] for d in cur.description]
        rows = [[cell(v) for v in r] for r in cur.fetchmany(limit + 1)]
    except sqlite3.DatabaseError as e:
        raise SqlError(explain(e, started)) from e
    finally:
        con.set_progress_handler(None, 0)
        con.set_authorizer(None)
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, old_limit)
        con.execute("PRAGMA query_only = OFF")
        drop_views(con, tables)
    return {"columns": columns, "rows": rows[:limit], "truncated": len(rows) > limit,
            "ms": round((time.monotonic() - started) * 1000)}


def explain(error, started):
    text = str(error)
    if "interrupted" in text:
        return f"The query took longer than {MAX_SECONDS} seconds and was stopped. Add a filter or a LIMIT."
    if "not authorized" in text or "authoriz" in text:
        return "That query is not allowed here. The workbench can only read your tables with SELECT."
    if "only execute one statement" in text:
        return "Run one statement at a time."
    if "string or blob too big" in text:
        return "A value in that query is too large."
    return text[:300]


def register(app, db, error_cls):
    ApiError = error_cls  # pylint: disable=invalid-name

    @app.get("/sql")
    def sql_page():
        return send_from_directory(app.static_folder, "sql.html")

    @app.get("/api/sql/schema")
    def sql_schema():
        return jsonify({"tables": schema(db()), "max_rows": MAX_ROWS})

    @app.post("/api/sql/run")
    def sql_run():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ApiError("Expected a JSON object body")
        try:
            return jsonify(run(db(), data.get("sql"), data.get("limit")))
        except SqlError as e:
            raise ApiError(str(e)) from e
