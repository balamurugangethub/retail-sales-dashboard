"""Field catalog and the generic query behind every dashboard-builder widget.

A widget asks for up to two dimensions (store, month, ...) and up to four measures
(sales, profit, ...). Column names and SQL fragments come only from the whitelists
below, never from the request, so the query is safe to build as a string.
"""

DIMENSIONS = {
    "store": {"label": "Store", "sql": "store", "time": False},
    "category": {"label": "Category", "sql": "category", "time": False},
    "product": {"label": "Product", "sql": "product", "time": False},
    "year": {"label": "Year", "sql": "strftime('%Y', day)", "time": True},
    "quarter": {"label": "Quarter", "time": True,
                "sql": "strftime('%Y', day) || '-Q' || ((CAST(strftime('%m', day) AS INTEGER) + 2) / 3)"},
    "month": {"label": "Month", "sql": "strftime('%Y-%m', day)", "time": True},
    "week": {"label": "Week", "sql": "strftime('%Y-W%W', day)", "time": True},
    "day": {"label": "Day", "sql": "day", "time": True},
    "weekday": {"label": "Day of week", "sql": "CAST(strftime('%w', day) AS INTEGER)", "time": False},
}
WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

MEASURES = {
    "sales": {"label": "Sales", "sql": "SUM(revenue_cents) / 100.0", "format": "money"},
    "profit": {"label": "Profit", "sql": "SUM(revenue_cents - cost_cents) / 100.0", "format": "money"},
    "cost": {"label": "Cost", "sql": "SUM(cost_cents) / 100.0", "format": "money"},
    "orders": {"label": "Orders", "sql": "SUM(orders)", "format": "number"},
    "units": {"label": "Units sold", "sql": "SUM(units)", "format": "number"},
    "margin": {"label": "Profit margin %", "format": "pct",
               "sql": "CASE WHEN SUM(revenue_cents) = 0 THEN 0 ELSE "
                      "SUM(revenue_cents - cost_cents) * 100.0 / SUM(revenue_cents) END"},
    "basket": {"label": "Avg. basket", "format": "money",
               "sql": "CASE WHEN SUM(orders) = 0 THEN 0 ELSE SUM(revenue_cents) / 100.0 / SUM(orders) END"},
    "avg_price": {"label": "Avg. unit price", "format": "money",
                  "sql": "CASE WHEN SUM(units) = 0 THEN 0 ELSE SUM(revenue_cents) / 100.0 / SUM(units) END"},
}

CHART_TYPES = ["bar", "hbar", "line", "area", "pie", "kpi", "table", "stacked"]
FILTER_FIELDS = ["store", "category", "product"]
MAX_DIMS, MAX_MEASURES, MAX_LIMIT = 2, 4, 500


class QueryError(ValueError):
    pass


def catalog():
    return {
        "dimensions": [{"id": k, "label": v["label"], "time": v["time"]} for k, v in DIMENSIONS.items()],
        "measures": [{"id": k, "label": v["label"], "format": v["format"]} for k, v in MEASURES.items()],
        "charts": CHART_TYPES,
    }


def validate_spec(dims, measures):
    dims = [d for d in dims if d]
    measures = [m for m in measures if m]
    bad = [d for d in dims if d not in DIMENSIONS] + [m for m in measures if m not in MEASURES]
    if bad:
        raise QueryError("Unknown field(s): " + ", ".join(bad))
    if len(dims) > MAX_DIMS:
        raise QueryError(f"At most {MAX_DIMS} dimensions per widget")
    if not measures:
        raise QueryError("Pick at least one measure (e.g. sales)")
    if len(measures) > MAX_MEASURES:
        raise QueryError(f"At most {MAX_MEASURES} measures per widget")
    if len(set(dims)) != len(dims) or len(set(measures)) != len(measures):
        raise QueryError("Each field can be used once per widget")
    return dims, measures


def run_query(con, dims, measures, where="1=1", params=(), sort=None, limit=None):
    """Returns {"columns": [...], "rows": [[...]]}. sort is a field id; prefix '-' = descending."""
    dims, measures = validate_spec(dims, measures)
    select = [f"{DIMENSIONS[d]['sql']} AS d{i}" for i, d in enumerate(dims)]
    select += [f"{MEASURES[m]['sql']} AS m{i}" for i, m in enumerate(measures)]
    sql = f"SELECT {', '.join(select)} FROM sales WHERE {where}"
    if dims:
        sql += " GROUP BY " + ", ".join(f"d{i}" for i in range(len(dims)))
    order = []
    if sort:
        desc = sort.startswith("-")
        key = sort.lstrip("+-")
        if key in dims:
            order.append(f"d{dims.index(key)}")
        elif key in measures:
            order.append(f"m{measures.index(key)}")
        else:
            raise QueryError(f"Cannot sort by '{key}': it is not in this widget")
        order[-1] += " DESC" if desc else " ASC"
    order += [f"d{i}" for i in range(len(dims))]
    if dims:
        sql += " ORDER BY " + ", ".join(order)
    args = list(params)
    if limit:
        sql += " LIMIT ?"
        args.append(max(1, min(int(limit), MAX_LIMIT)))
    elif dims:
        sql += f" LIMIT {MAX_LIMIT}"
    rows = []
    for r in con.execute(sql, args):
        r = list(r)
        for i, d in enumerate(dims):
            if d == "weekday" and r[i] is not None:
                r[i] = WEEKDAYS[r[i]]
        for j in range(len(dims), len(r)):
            r[j] = round(r[j] or 0, 2)
        rows.append(r)
    return {
        "columns": [{"id": d, "label": DIMENSIONS[d]["label"], "kind": "dimension"} for d in dims]
        + [{"id": m, "label": MEASURES[m]["label"], "kind": "measure", "format": MEASURES[m]["format"]}
           for m in measures],
        "rows": rows,
    }
