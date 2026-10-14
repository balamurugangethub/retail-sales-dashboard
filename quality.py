"""Data quality checks for an uploaded table.

Reads one ds_<id> table and reports, in plain English: missing values, repeated rows, constant
columns, numbers far outside the usual range, and spellings that differ only
by case or spacing. Everything is read-only; nothing in the data is changed.
"""
import statistics
from datetime import date

OUTLIER_FENCE = 3.0          # how many interquartile ranges beyond the quartiles counts as far outside
MIN_NUMBERS = 8              # fewer numbers than this and "far outside" means little
MAX_VARIANT_EXAMPLES = 3
WEIGHTS = {"complete": 0.5, "unique": 0.3, "regular": 0.2}
ISSUE_COST = 4               # points lost for each text or constant-column problem, capped below
ISSUE_COST_CAP = 20
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def issue(severity, column, kind, message):
    return {"severity": severity, "column": column, "kind": kind, "message": message}


def pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0.0


def duplicate_rows(con, table, ncols):
    cols = ", ".join(f"c{i}" for i in range(ncols))
    row = con.execute(f"SELECT COALESCE(SUM(n - 1), 0) FROM (SELECT COUNT(*) n FROM {table} GROUP BY {cols} HAVING n > 1)").fetchone()
    return int(row[0])


def number_outliers(con, table, c):
    values = sorted(r[0] for r in con.execute(f"SELECT {c} FROM {table} WHERE {c} IS NOT NULL"))
    if len(values) < MIN_NUMBERS:
        return 0, None
    q1, _, q3 = statistics.quantiles(values, n=4)
    spread = q3 - q1
    if spread == 0:
        return 0, None
    lo, hi = q1 - OUTLIER_FENCE * spread, q3 + OUTLIER_FENCE * spread
    far = [v for v in values if v < lo or v > hi]
    return len(far), (lo, hi)


def spelling_variants(con, table, c):
    variants = []
    for key, n in con.execute(f"SELECT LOWER(TRIM({c})), COUNT(DISTINCT {c}) FROM {table} "
                              f"WHERE {c} IS NOT NULL GROUP BY LOWER(TRIM({c})) HAVING COUNT(DISTINCT {c}) > 1 LIMIT 50"):
        shown = [r[0] for r in con.execute(f"SELECT DISTINCT {c} FROM {table} WHERE LOWER(TRIM({c})) = ? LIMIT {MAX_VARIANT_EXAMPLES}", (key,))]
        variants.append(shown)
    return variants


def future_dates(con, table, c):
    return con.execute(f"SELECT COUNT(*) FROM {table} WHERE {c} > ?", (date.today().isoformat(),)).fetchone()[0]


def check(con, meta):
    table, rows, columns = f"ds_{meta['id']}", meta["rows"], meta["columns"]
    issues, filled_cells, outlier_cells, number_cells, flagged_columns = [], 0, 0, 0, 0
    for i, col in enumerate(columns):
        c, name = f"c{i}", col["name"]
        filled, distinct = con.execute(f"SELECT COUNT({c}), COUNT(DISTINCT {c}) FROM {table}").fetchone()
        filled_cells += filled
        missing = rows - filled
        if missing:
            share = pct(missing, rows)
            level = "high" if share >= 30 else "medium" if share >= 5 else "low"
            issues.append(issue(level, name, "missing", f"{name}: {missing:,} of {rows:,} values are empty ({share}%)."))
        if filled > 1 and distinct == 1:
            flagged_columns += 1
            issues.append(issue("low", name, "constant", f"{name}: every filled value is the same, so it tells you nothing."))
        if col["type"] == "number" and filled:
            number_cells += filled
            far, fence = number_outliers(con, table, c)
            outlier_cells += far
            if far:
                issues.append(issue("medium", name, "outliers",
                                    f"{name}: {far:,} value{'s are' if far != 1 else ' is'} far outside the usual range ({fence[0]:,.4g} to {fence[1]:,.4g}). Check for typing mistakes."))
        elif col["type"] == "date" and filled:
            future = future_dates(con, table, c)
            if future:
                issues.append(issue("low", name, "future", f"{name}: {future:,} dates are in the future."))
        elif col["type"] == "text" and filled:
            variants = spelling_variants(con, table, c)
            if variants:
                flagged_columns += 1
                example = " / ".join(f'"{v}"' for v in variants[0])
                issues.append(issue("medium", name, "variants",
                                    f"{name}: {len(variants)} spellings differ only by capital letters or spacing, for example {example}."))
    dupes = duplicate_rows(con, table, len(columns))
    if dupes:
        issues.append(issue("high" if pct(dupes, rows) >= 5 else "medium", None, "duplicates",
                            f"{dupes:,} rows are exact copies of another row ({pct(dupes, rows)}%)."))
    cells = rows * len(columns)
    parts = {"complete": filled_cells / cells if cells else 1.0,
             "unique": 1 - dupes / rows if rows else 1.0,
             "regular": 1 - outlier_cells / number_cells if number_cells else 1.0}
    score = 100 * sum(WEIGHTS[k] * v for k, v in parts.items()) - min(ISSUE_COST_CAP, ISSUE_COST * flagged_columns)
    issues.sort(key=lambda x: (SEVERITY_ORDER[x["severity"]], x["column"] or ""))
    score = max(0, min(100, round(score)))
    return {"score": score, "grade": "good" if score >= 90 else "fair" if score >= 70 else "poor",
            "rows": rows, "columns": len(columns),
            "completeness_pct": round(100 * parts["complete"], 1), "duplicate_rows": dupes,
            "issues": issues}
