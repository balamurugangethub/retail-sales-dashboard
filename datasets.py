"""Open any table of data: upload it, look at it, chart it and forecast it, with no fixed columns.

Each upload becomes its own SQLite table (ds_<id>) with columns c0, c1, ... so a column name from
the file never reaches SQL. A column is a number, a date or text, worked out from its values.
Queries come from a small whitelist (aggregations, date grains, filter operators), like the
sales dashboard builder but for whatever fields the file has.
"""
import json
import math
import re
import statistics
import time
from datetime import date, timedelta

from flask import jsonify, request, send_from_directory

import cleaning
import forecast
import quality
import sources

MAX_COLUMNS = 80
MAX_NAME = 80
MAX_DIMS, MAX_MEASURES, MAX_FILTERS, MAX_LIMIT = 2, 4, 5, 1000
SAMPLE_ROWS = 15
TYPE_SHARE = 0.9            # share of filled cells that must parse for a column to count as number/date
AVERAGE_LIKE = re.compile(r"avg|average|mean|score|rate|%|percent|wait|price|satisfaction|ratio|age|temperature|grade", re.I)
YEAR_NAME = re.compile(r"year|yr", re.I)
ID_NAME = re.compile(r"(^|[^a-z])(id|code|zip|postal|pin|phone|mobile|sku|barcode)([^a-z]|$)", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created REAL NOT NULL,
    columns TEXT NOT NULL,        -- JSON [{"name": ..., "type": "number|date|text"}]
    rows INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_sources (
    dataset_id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    hours INTEGER NOT NULL,       -- refresh when older than this many hours; 0 means only when asked
    refreshed REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    dataset_id INTEGER PRIMARY KEY,
    created REAL NOT NULL,
    filename TEXT,
    steps TEXT NOT NULL           -- JSON [{"step": ..., "detail": ..., "rows": ..., "ms": ...}]
);
"""

GRAINS = {
    "day": "{c}",
    "week": "date({c}, 'weekday 0', '-6 days')",
    "month": "strftime('%Y-%m', {c})",
    "quarter": "strftime('%Y', {c}) || '-Q' || ((CAST(strftime('%m', {c}) AS INTEGER) + 2) / 3)",
    "year": "strftime('%Y', {c})",
}
AGGS = {
    "sum": ("Sum", "SUM({c})", {"number"}),
    "avg": ("Average", "AVG({c})", {"number"}),
    "min": ("Smallest", "MIN({c})", {"number", "date"}),
    "max": ("Largest", "MAX({c})", {"number", "date"}),
    "count": ("Count of filled", "COUNT({c})", {"number", "date", "text"}),
    "distinct": ("Count of different", "COUNT(DISTINCT {c})", {"number", "date", "text"}),
    "rows": ("Number of rows", "COUNT(*)", None),
}
OPS = {"eq": "=", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}


class DatasetError(ValueError):
    pass


# ---- reading a file into typed columns ------------------------------------------------
def unique_names(header):
    seen, out = {}, []
    for h in header:
        base = (h or "Column").strip()[:MAX_NAME] or "Column"
        n = seen.get(base.lower(), 0)
        seen[base.lower()] = n + 1
        out.append(base if n == 0 else f"{base} ({n + 1})")
    return out


def looks_like_years(name, sample):
    return bool(YEAR_NAME.search(name)) and all(re.fullmatch(r"(1[89]|2[01])\d\d", v) for v in sample)


def infer_type(name, values):
    filled = [v.strip() for v in values if v is not None and str(v).strip() != ""]
    if not filled:
        return "text"
    sample = filled[:3000]
    if looks_like_years(name, sample):
        return "date"                                       # 2011, 2012 ... are years, not amounts
    numbers = sum(1 for v in sample if cleaning.parse_number(v) is not None)
    if numbers >= TYPE_SHARE * len(sample):
        if ID_NAME.search(name) and all(re.fullmatch(r"\d+", v) for v in sample):
            return "text"                                   # ids, phone numbers, postcodes
        return "number"
    texty = [v for v in sample if cleaning.parse_number(v) is None]
    if not texty:
        return "text"
    dates = sum(1 for v in texty if cleaning.parse_date(v) is not None)
    return "date" if dates >= TYPE_SHARE * len(texty) and dates >= 0.5 * len(sample) else "text"


def typed_rows(header, rows):
    """Returns (columns, rows) with numbers as floats, dates as ISO text, blanks as None."""
    if len(header) > MAX_COLUMNS:
        raise DatasetError(f"The file has {len(header)} columns; the limit is {MAX_COLUMNS}.")
    keep = [i for i, h in enumerate(header) if not (re.fullmatch(r"Column \d+", h or "")
            and sum(1 for r in rows if r[i].strip()) < 0.01 * len(rows))]       # blank-header columns that are empty
    header, rows = [header[i] for i in keep], [[r[i] for i in keep] for r in rows]
    names = unique_names(header)
    types = [infer_type(n, [r[i] for r in rows]) for i, n in enumerate(names)]
    day_first = {i: cleaning.detect_day_first([r[i] for r in rows]) for i, t in enumerate(types) if t == "date"}
    out = []
    for r in rows:
        row = []
        for i, t in enumerate(types):
            v = str(r[i]).strip()
            if v == "":
                row.append(None)
            elif t == "number":
                n = cleaning.parse_number(v)
                row.append(float(n) if n is not None else None)
            elif t == "date" and re.fullmatch(r"(1[89]|2[01])\d\d", v):
                row.append(f"{v}-01-01")                     # a bare year
            elif t == "date":
                d = cleaning.parse_date(v, day_first[i])
                row.append(d.isoformat() if d else None)
            else:
                row.append(v[:500])
        out.append(row)
    return [{"name": n, "type": t} for n, t in zip(names, types)], out


def source_cells(rows):
    return sum(1 for r in rows for v in r if str(v).strip() != "")


def load_table(filename, data):  # pylint: disable=too-many-locals
    """Reads a file into typed columns. Returns (columns, rows, steps) where steps describe what was done."""
    steps, t0 = [], time.perf_counter()

    def lap(step, detail, rows):
        nonlocal t0
        now = time.perf_counter()
        steps.append({"step": step, "detail": detail, "rows": rows, "ms": round((now - t0) * 1000)})
        t0 = now
    try:
        header, rows = cleaning.read_table(filename, data)
    except cleaning.CleaningError as e:
        raise DatasetError(str(e)) from e
    if not rows:
        raise DatasetError("The file has a header but no rows.")
    lap("Read", f"{len(data):,} bytes, {len(header)} columns found", len(rows))
    columns, body = typed_rows(header, rows)
    kinds = {k: sum(1 for c in columns if c["type"] == k) for k in ("number", "date", "text")}
    dropped = len(header) - len(columns)
    lap("Detect types", f"columns: {kinds['number']} number, {kinds['date']} date, {kinds['text']} text"
        + (f"; {dropped} empty column{'s' if dropped != 1 else ''} dropped" if dropped else ""), len(body))
    unreadable = source_cells(rows) - sum(1 for r in body for v in r if v is not None)
    lap("Clean", "numbers, dates and blanks converted"
        + (f"; {unreadable:,} values could not be read and were left empty" if unreadable > 0 else "; every value was readable"), len(body))
    return columns, body, steps


def load_step(detail, rows, started):
    return {"step": "Load", "detail": detail, "rows": rows, "ms": round((time.perf_counter() - started) * 1000)}


def store_run(con, ds_id, filename, steps):
    con.execute("INSERT OR REPLACE INTO pipeline_runs(dataset_id, created, filename, steps) VALUES (?,?,?,?)",
                (ds_id, time.time(), (filename or "")[:200], json.dumps(steps)))


def create_dataset(con, filename, data, name=None, source=None):
    """Stores an upload as table ds_<id>. source is (url, refresh hours) for a file opened from a link."""
    columns, body, steps = load_table(filename, data)
    started = time.perf_counter()
    label = (name or re.sub(r"\.[A-Za-z0-9]+$", "", filename or "") or "Dataset").strip()[:MAX_NAME] or "Dataset"
    with con:
        cur = con.execute("INSERT INTO datasets(name, created, columns, rows) VALUES (?,?,?,?)",
                          (label, time.time(), json.dumps(columns), len(body)))
        ds_id = cur.lastrowid
        defs = ", ".join(f"c{i} {'REAL' if c['type'] == 'number' else 'TEXT'}" for i, c in enumerate(columns))
        con.execute(f"CREATE TABLE ds_{ds_id} ({defs})")
        marks = ", ".join("?" for _ in columns)
        con.executemany(f"INSERT INTO ds_{ds_id} VALUES ({marks})", body)
        steps.append(load_step(f"stored as table ds_{ds_id}, ready for charts, forecasts and SQL", len(body), started))
        store_run(con, ds_id, source[0] if source else filename, steps)
        if source:
            con.execute("INSERT INTO dataset_sources(dataset_id, url, hours, refreshed) VALUES (?,?,?,?)",
                        (ds_id, source[0], source[1], time.time()))
    return ds_id


REFRESH_CHOICES = (0, 1, 6, 24)


def open_link(con, url, hours=24, name=None):
    hours = int(hours or 0)
    if hours not in REFRESH_CHOICES:
        raise DatasetError("Choose how often to refresh: never, every hour, every 6 hours or every day.")
    try:
        data, filename = sources.fetch(url)
    except sources.SourceError as e:
        raise DatasetError(str(e)) from e
    return create_dataset(con, filename, data, name, source=(str(url).strip()[:500], hours))


def refresh_dataset(con, ds_id):
    """Download the linked file again and replace the rows. The columns must be the same as before."""
    link = con.execute("SELECT url FROM dataset_sources WHERE dataset_id = ?", (ds_id,)).fetchone()
    if not link:
        raise DatasetError("This dataset was not opened from a link, so there is nothing to refresh.")
    meta = load_meta(con, ds_id)
    try:
        data, filename = sources.fetch(link["url"])
    except sources.SourceError as e:
        raise DatasetError(str(e)) from e
    columns, body, steps = load_table(filename, data)
    if [(c["name"], c["type"]) for c in columns] != [(c["name"], c["type"]) for c in meta["columns"]]:
        raise DatasetError("The columns in the file have changed since you opened it. Open the link again as a new dataset.")
    started = time.perf_counter()
    with con:
        con.execute(f"DELETE FROM ds_{ds_id}")
        marks = ", ".join("?" for _ in columns)
        con.executemany(f"INSERT INTO ds_{ds_id} VALUES ({marks})", body)
        con.execute("UPDATE datasets SET rows = ? WHERE id = ?", (len(body), ds_id))
        con.execute("UPDATE dataset_sources SET refreshed = ? WHERE dataset_id = ?", (time.time(), ds_id))
        steps.append(load_step(f"refreshed table ds_{ds_id}: {meta['rows']:,} rows before, {len(body):,} now", len(body), started))
        store_run(con, ds_id, link["url"], steps)
    return {"rows_before": meta["rows"], "rows": len(body)}


def source_info(con, ds_id):
    row = con.execute("SELECT url, hours, refreshed FROM dataset_sources WHERE dataset_id = ?", (ds_id,)).fetchone()
    if not row:
        return None
    due = bool(row["hours"]) and time.time() - row["refreshed"] > row["hours"] * 3600
    return {"url": row["url"], "hours": row["hours"], "refreshed": row["refreshed"], "due": due}


def pipeline_overview(con):
    """Every upload with the steps it went through and a fresh quality score."""
    out = []
    for row in con.execute("SELECT d.id, d.name, d.rows, d.columns, d.created, p.filename, p.steps FROM datasets d "
                           "LEFT JOIN pipeline_runs p ON p.dataset_id = d.id ORDER BY d.created DESC"):
        meta = {"id": row["id"], "rows": row["rows"], "columns": json.loads(row["columns"])}
        report = quality.check(con, meta)
        out.append({"id": row["id"], "name": row["name"], "rows": row["rows"], "columns": len(meta["columns"]),
                    "created": row["created"], "filename": row["filename"], "source": source_info(con, row["id"]),
                    "steps": json.loads(row["steps"]) if row["steps"] else None,
                    "quality": {"score": report["score"], "grade": report["grade"], "issues": len(report["issues"])}})
    return out


def load_meta(con, ds_id):
    row = con.execute("SELECT id, name, created, columns, rows FROM datasets WHERE id = ?", (ds_id,)).fetchone()
    if not row:
        raise DatasetError("That dataset does not exist.")
    return {"id": row["id"], "name": row["name"], "created": row["created"], "rows": row["rows"],
            "columns": json.loads(row["columns"])}


# ---- profile and plain-English findings ---------------------------------------------
def profile(con, meta):
    t, out = f"ds_{meta['id']}", []
    for i, col in enumerate(meta["columns"]):
        c = f"c{i}"
        filled, distinct = con.execute(f"SELECT COUNT({c}), COUNT(DISTINCT {c}) FROM {t}").fetchone()
        p = {"name": col["name"], "type": col["type"], "filled": filled, "missing": meta["rows"] - filled,
             "distinct": distinct}
        if col["type"] == "number" and filled:
            lo, hi, mean, total = con.execute(f"SELECT MIN({c}), MAX({c}), AVG({c}), SUM({c}) FROM {t}").fetchone()
            p.update(min=lo, max=hi, mean=mean, sum=total)
        elif col["type"] == "date" and filled:
            p["min"], p["max"] = con.execute(f"SELECT MIN({c}), MAX({c}) FROM {t}").fetchone()
        elif col["type"] == "text" and filled:
            p["top"] = [[r[0], r[1]] for r in con.execute(
                f"SELECT {c}, COUNT(*) n FROM {t} WHERE {c} IS NOT NULL GROUP BY {c} ORDER BY n DESC, {c} LIMIT 5")]
        out.append(p)
    return out


def fmt(v):
    if v is None:
        return "–"
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}"


def insights(con, meta, prof):  # pylint: disable=too-many-locals
    t, found, cols = f"ds_{meta['id']}", [], meta["columns"]
    agg_name = lambda i: "AVG" if AVERAGE_LIKE.search(cols[i]["name"]) else "SUM"   # noqa: E731
    word = lambda i: ("average " if agg_name(i) == "AVG" else "") + cols[i]["name"]   # noqa: E731
    found.append(f"{meta['rows']:,} rows and {len(cols)} columns.")
    gaps = sorted((p for p in prof if p["missing"] / meta["rows"] > 0.05), key=lambda p: -p["missing"])
    if gaps:
        g = gaps[0]
        found.append(f"“{g['name']}” is empty in {g['missing'] / meta['rows']:.0%} of rows"
                     + (f" (and {len(gaps) - 1} other column{'s' if len(gaps) > 2 else ''} have gaps)." if len(gaps) > 1 else "."))
    num = next((i for i, c in enumerate(cols) if c["type"] == "number"), None)
    text = next((i for i, p in enumerate(prof) if p["type"] == "text" and 2 <= p["distinct"] <= 60), None)
    dt = next((i for i, c in enumerate(cols) if c["type"] == "date"), None)
    if num is not None and text is not None:
        rows = con.execute(f"SELECT c{text}, {agg_name(num)}(c{num}) s FROM {t} WHERE c{text} IS NOT NULL "
                           f"GROUP BY c{text} ORDER BY s DESC").fetchall()
        total = sum(r[1] or 0 for r in rows)
        if rows and total > 0 and rows[0][1] is not None:
            tail = (f", {rows[0][1] / total:.0%} of the total" if agg_name(num) == "SUM"
                    else f", against {fmt(rows[-1][1])} for “{rows[-1][0]}”")
            found.append(f"“{rows[0][0]}” has the highest {word(num)}: {fmt(rows[0][1])}{tail}, "
                         f"across {len(rows)} values of {cols[text]['name']}.")
    if num is not None and dt is not None:
        yearly = con.execute(f"SELECT COUNT(DISTINCT strftime('%m-%d', c{dt})) FROM {t}").fetchone()[0] == 1
        rows = con.execute(f"SELECT strftime('{'%Y' if yearly else '%Y-%m'}', c{dt}) m, {agg_name(num)}(c{num}) FROM {t} "
                           f"WHERE c{dt} IS NOT NULL GROUP BY m ORDER BY m").fetchall()
        if len(rows) >= 3:                      # leave the newest month out, it is often only partly there
            if yearly:                          # whole years only, so the newest one counts
                rows = rows + [rows[-1]]
            prev, last = rows[-3][1], rows[-2][1]
            if prev and last is not None and prev != 0:
                change = (last - prev) / abs(prev) * 100
                found.append(f"{word(num).capitalize()} in {rows[-2][0]} was {fmt(last)}, "
                             f"{'up' if change >= 0 else 'down'} {abs(change):.1f}% on {rows[-3][0]}.")
    for i, p in enumerate(prof):
        if p["type"] == "number" and p["filled"] >= 20:
            vals = [r[0] for r in con.execute(f"SELECT c{i} FROM {t} WHERE c{i} IS NOT NULL")]
            sd = statistics.pstdev(vals)
            if sd > 0:
                mean = statistics.fmean(vals)
                n = sum(1 for v in vals if abs(v - mean) > 3 * sd)
                if n:
                    found.append(f"{n} unusual value{'s' if n > 1 else ''} in “{p['name']}” "
                                 f"(far from the typical {fmt(mean)}). Worth a look.")
                    break
    return found


# ---- queries ----------------------------------------------------------------------
def col_index(meta, raw):
    try:
        i = int(raw)
    except (TypeError, ValueError) as e:
        raise DatasetError("Pick a field.") from e
    if not 0 <= i < len(meta["columns"]):
        raise DatasetError("Unknown field.")
    return i


def where_clause(meta, filters):
    parts, params = [], []
    for f in (filters or [])[:MAX_FILTERS]:
        if not isinstance(f, dict):
            raise DatasetError("Bad filter.")
        i, op, val = col_index(meta, f.get("field")), f.get("op", "eq"), f.get("value")
        c, typ = f"c{i}", meta["columns"][i]["type"]
        if op == "contains":
            parts.append(f"{c} LIKE ? ESCAPE '\\'")
            params.append("%" + str(val).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        elif op in OPS:
            if typ == "number":
                n = cleaning.parse_number(val)
                if n is None:
                    raise DatasetError(f"“{meta['columns'][i]['name']}” is a number; “{val}” is not.")
                val = float(n)
            parts.append(f"{c} {OPS[op]} ?")
            params.append(val if typ == "number" else str(val))
        elif op in ("blank", "filled"):
            parts.append(f"{c} IS {'NULL' if op == 'blank' else 'NOT NULL'}")
        else:
            raise DatasetError("Unknown filter type.")
    return " AND ".join(parts) or "1=1", params


def run_query(con, meta, spec):  # pylint: disable=too-many-locals
    dims_in, meas_in = spec.get("dims") or [], spec.get("measures") or []
    if len(dims_in) > MAX_DIMS or len(meas_in) > MAX_MEASURES:
        raise DatasetError(f"At most {MAX_DIMS} groupings and {MAX_MEASURES} measures.")
    if not meas_in:
        raise DatasetError("Pick what to measure.")
    select, group, labels, nonnull = [], [], [], []
    for k, d in enumerate(dims_in):
        i = col_index(meta, d.get("field"))
        col, c = meta["columns"][i], f"c{i}"
        if col["type"] == "date":
            grain = d.get("grain", "month")
            if grain not in GRAINS:
                raise DatasetError("Unknown date grouping.")
            expr = GRAINS[grain].format(c=c)
            nonnull.append(f"{c} IS NOT NULL")
            labels.append({"label": f"{col['name']} ({grain})", "kind": "date"})
        else:
            expr = f"COALESCE({c}, '(blank)')" if col["type"] == "text" else c
            labels.append({"label": col["name"], "kind": "text"})
        select.append(f"{expr} AS d{k}")
        group.append(f"d{k}")
    for k, m in enumerate(meas_in):
        agg = m.get("agg", "sum")
        if agg not in AGGS:
            raise DatasetError("Unknown calculation.")
        title, sql, kinds = AGGS[agg]
        if kinds is None:
            select.append(f"{sql} AS m{k}")
            labels.append({"label": title, "kind": "number"})
            continue
        i = col_index(meta, m.get("field"))
        col = meta["columns"][i]
        if col["type"] not in kinds:
            raise DatasetError(f"“{title}” cannot be used on “{col['name']}” ({col['type']}). "
                               f"Try “Count of filled”.")
        select.append(f"{sql.format(c=f'c{i}')} AS m{k}")
        kind = "number" if agg in ("sum", "avg", "count", "distinct") else col["type"]
        labels.append({"label": f"{title} of {col['name']}", "kind": kind})
    where, params = where_clause(meta, spec.get("filters"))
    if nonnull:
        where += " AND " + " AND ".join(nonnull)
    sql = f"SELECT {', '.join(select)} FROM ds_{meta['id']} WHERE {where}"
    if group:
        sql += " GROUP BY " + ", ".join(group)
    sort = spec.get("sort") or {}
    by = sort.get("by")
    if by is None and group:
        by = "d0" if labels[0]["kind"] == "date" else f"m0"
    if by:
        if not re.fullmatch(r"[dm]\d", str(by)) or int(by[1]) >= (len(dims_in) if by[0] == "d" else len(meas_in)):
            raise DatasetError("Unknown sort field.")
        default_dir = "asc" if by.startswith("d") else "desc"
        sql += f" ORDER BY {by} {'ASC' if sort.get('dir', default_dir) == 'asc' else 'DESC'}"
    try:
        limit = max(1, min(int(spec.get("limit") or 200), MAX_LIMIT))
    except (TypeError, ValueError) as e:
        raise DatasetError("Limit must be a number.") from e
    rows = [[None if isinstance(v, float) and math.isnan(v) else v for v in r]
            for r in con.execute(sql + f" LIMIT {limit}", params).fetchall()]
    return {"columns": labels, "rows": rows, "dims": len(dims_in)}


# ---- forecast any series --------------------------------------------------------------
def run_forecast(con, meta, spec):
    di, agg = col_index(meta, spec.get("date")), spec.get("agg", "sum")
    if meta["columns"][di]["type"] != "date":
        raise DatasetError("Pick a date column to forecast over.")
    if agg == "rows":
        value = "COUNT(*)"
    else:
        mi = col_index(meta, spec.get("measure"))
        if meta["columns"][mi]["type"] != "number" or agg not in ("sum", "avg"):
            raise DatasetError("Forecast needs a number column, totalled or averaged.")
        value = f"{AGGS[agg][1].format(c=f'c{mi}')}"
    where, params = where_clause(meta, spec.get("filters"))
    lo, hi = con.execute(f"SELECT MIN(c{di}), MAX(c{di}) FROM ds_{meta['id']} WHERE {where} AND c{di} IS NOT NULL",
                         params).fetchone()
    if lo is None:
        return {"ok": False, "message": "There are no dated rows to forecast from."}
    span = (date.fromisoformat(hi) - date.fromisoformat(lo)).days
    grain, step = ("week", 7) if span <= 900 else ("month", 30)
    bucket = GRAINS[grain].format(c=f"c{di}")
    series = {r[0]: r[1] or 0.0 for r in con.execute(
        f"SELECT {bucket} b, {value} FROM ds_{meta['id']} WHERE {where} AND c{di} IS NOT NULL GROUP BY b", params)}
    keys = sorted(series)
    if grain == "month":
        keys = keys[:-1] if len(keys) > 1 else keys              # the newest month may be partial
    else:                                                        # drop a week that has not finished yet
        last_day = date.fromisoformat(hi)
        if last_day.weekday() != 6 and len(keys) > 1:
            keys = keys[:-1]
    if len(keys) < forecast.MIN_WEEKS:
        return {"ok": False, "message": f"A forecast needs at least {forecast.MIN_WEEKS} full "
                                        f"{grain}s of dates; this selection has {len(keys)}."}
    vals = [series[k] for k in keys]
    horizon = 4 if grain == "week" else 3
    fc, rmse = forecast.holt(vals, horizon)
    band = forecast.Z90 * rmse
    last = date.fromisoformat(keys[-1] + "-01" if grain == "month" else keys[-1])
    names = []
    for k in range(1, horizon + 1):
        if grain == "week":
            names.append((last + timedelta(days=7 * k)).isoformat())
        else:
            y, m = divmod(last.year * 12 + last.month - 1 + k, 12)
            names.append(f"{y}-{m + 1:02d}")
    recent, nxt = sum(vals[-horizon:]), sum(fc)
    return {"ok": True, "grain": grain, "horizon": horizon,
            "history": [{"period": k, "value": round(series[k], 2)} for k in keys[-60:]],
            "forecast": [{"period": p, "value": round(v, 2), "low": round(max(0.0, v - band), 2),
                          "high": round(v + band, 2)} for p, v in zip(names, fc)],
            "recent_total": round(recent, 2), "next_total": round(nxt, 2),
            "change_pct": None if not recent else round((nxt - recent) / abs(recent) * 100, 1)}


def sample_rows(con, meta):
    cols = ", ".join(f"c{i}" for i in range(len(meta["columns"])))
    return [list(r) for r in con.execute(f"SELECT {cols} FROM ds_{meta['id']} LIMIT {SAMPLE_ROWS}")]


# ---- routes --------------------------------------------------------------------------
def register(app, db, error_cls):
    ApiError = error_cls  # pylint: disable=invalid-name

    with app.app_context():
        c = db()
        c.executescript(SCHEMA)
        c.commit()

    def guard(fn):
        try:
            return fn()
        except DatasetError as e:
            raise ApiError(str(e)) from e

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ApiError("Expected a JSON object body")
        return data

    def meta_or_404(ds_id):
        return guard(lambda: load_meta(db(), ds_id))

    @app.get("/explore")
    def explore_page():
        return send_from_directory(app.static_folder, "explore.html")

    @app.get("/api/datasets")
    def list_datasets():
        rows = db().execute("SELECT id, name, rows, columns FROM datasets ORDER BY created DESC").fetchall()
        return jsonify({"datasets": [{"id": r["id"], "name": r["name"], "rows": r["rows"],
                                      "columns": len(json.loads(r["columns"]))} for r in rows],
                        "read_only": app.config["READ_ONLY"]})

    @app.post("/api/datasets")
    def add_dataset():
        if app.config["READ_ONLY"]:
            raise ApiError("This hosted demo is read-only. Run the app on your computer to open your own data.")
        f = request.files.get("file")
        if not f:
            raise ApiError("No file uploaded (field name must be 'file')")
        ds_id = guard(lambda: create_dataset(db(), f.filename, f.read(), request.form.get("name")))
        return jsonify({"id": ds_id})

    def writable():
        if app.config["READ_ONLY"]:
            raise ApiError("This hosted demo is read-only. Run the app on your computer to open your own data.")

    @app.post("/api/datasets/from-url")
    def add_from_link():
        writable()
        data = body()
        ds_id = guard(lambda: open_link(db(), data.get("url"), data.get("hours", 24), data.get("name")))
        return jsonify({"id": ds_id})

    @app.post("/api/datasets/<int:ds_id>/refresh")
    def refresh(ds_id):
        writable()
        meta_or_404(ds_id)
        return jsonify(guard(lambda: refresh_dataset(db(), ds_id)))

    @app.get("/api/datasets/<int:ds_id>")
    def get_dataset(ds_id):
        meta = meta_or_404(ds_id)
        prof = profile(db(), meta)
        return jsonify({**meta, "source": source_info(db(), ds_id), "profile": prof, "insights": insights(db(), meta, prof),
                        "sample": sample_rows(db(), meta), "grains": list(GRAINS),
                        "aggs": [{"id": k, "label": v[0], "types": sorted(v[2]) if v[2] else None}
                                 for k, v in AGGS.items()]})

    @app.get("/api/datasets/<int:ds_id>/quality")
    def dataset_quality(ds_id):
        return jsonify(quality.check(db(), meta_or_404(ds_id)))

    @app.get("/api/pipeline")
    def pipeline():
        return jsonify({"datasets": pipeline_overview(db())})

    @app.get("/pipeline")
    def pipeline_page():
        return send_from_directory(app.static_folder, "pipeline.html")

    @app.delete("/api/datasets/<int:ds_id>")
    def delete_dataset(ds_id):
        if app.config["READ_ONLY"]:
            raise ApiError("This hosted demo is read-only.")
        meta_or_404(ds_id)
        d = db()
        with d:
            d.execute(f"DROP TABLE IF EXISTS ds_{ds_id}")
            d.execute("DELETE FROM datasets WHERE id = ?", (ds_id,))
            d.execute("DELETE FROM pipeline_runs WHERE dataset_id = ?", (ds_id,))
            d.execute("DELETE FROM dataset_sources WHERE dataset_id = ?", (ds_id,))
        return jsonify({"ok": True})

    @app.post("/api/datasets/<int:ds_id>/query")
    def query_dataset(ds_id):
        meta = meta_or_404(ds_id)
        return jsonify(guard(lambda: run_query(db(), meta, body())))

    @app.post("/api/datasets/<int:ds_id>/forecast")
    def forecast_dataset(ds_id):
        meta = meta_or_404(ds_id)
        return jsonify(guard(lambda: run_forecast(db(), meta, body())))
