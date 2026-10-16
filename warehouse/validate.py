"""Checks one daily file before it is loaded. A file that fails stops the run.

Three kinds of check, in order:
1. Shape: the file has the expected columns and enough rows.
2. Business rules: every number parses, every sale is dated on the file's day, every store is known.
3. The app's own data quality checks (quality.py, the same ones the Explore page shows): the file
   is copied into a small in-memory table in the layout quality.check expects, and the run fails if
   the score is below MIN_QUALITY_SCORE or any problem is rated high (for example 30%+ missing values).
"""
import csv
import os
import sqlite3
import sys
from datetime import date

from warehouse import config

sys.path.insert(0, config.REPO)
import quality  # noqa: E402  (the app's module, reused as a pipeline step)

KNOWN_STORES = {"S01", "S02", "S03", "S04", "S05"}


class ValidationError(Exception):
    def __init__(self, report):
        super().__init__(f"{report['file']} failed validation: " + "; ".join(report["failures"]))
        self.report = report


def file_day(path):
    name = os.path.basename(path)
    return date.fromisoformat(name[len(config.FILE_PREFIX):-4])


def as_number(value):
    try:
        return float(value)
    except ValueError:
        return None


def quality_report(header, rows):
    """Runs quality.check on the rows, laid out as the ds_<id> table it reads (columns c0..cN)."""
    con = sqlite3.connect(":memory:")
    cols = [{"name": name, "type": config.COLUMN_TYPES.get(name, "text")} for name in header]
    con.execute(f"CREATE TABLE ds_0 ({', '.join(f'c{i}' for i in range(len(cols)))})")
    typed = []
    for row in rows:
        typed.append([(as_number(v) if c["type"] == "number" else v) if v != "" else None for c, v in zip(cols, row)])
    con.executemany(f"INSERT INTO ds_0 VALUES ({', '.join('?' * len(cols))})", typed)
    report = quality.check(con, {"id": 0, "rows": len(rows), "columns": cols})
    con.close()
    return report


def validate_file(path, min_rows=config.MIN_ROWS, min_score=config.MIN_QUALITY_SCORE):
    """Returns a report dict. report['ok'] is False when the file must not be loaded."""
    report = {"file": os.path.basename(path), "rows": 0, "score": None, "failures": [], "issues": []}
    fail = report["failures"].append
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = [h.strip().lower() for h in next(reader, [])]
        rows = [r for r in reader if any(r)]
    report["rows"] = len(rows)
    missing = [c for c in config.COLUMNS if c not in header]
    if missing:
        fail("missing columns: " + ", ".join(missing))
        report["ok"] = False
        return report
    if len(rows) < min_rows:
        fail(f"only {len(rows)} rows (expected at least {min_rows})")
    rows = [r for r in rows if len(r) == len(header)] if rows else rows
    idx = {name: header.index(name) for name in config.COLUMNS}
    day = file_day(path).isoformat()
    bad_numbers = sum(1 for r in rows for c, t in config.COLUMN_TYPES.items()
                      if t == "number" and r[idx[c]] != "" and as_number(r[idx[c]]) is None)
    wrong_day = sum(1 for r in rows if not r[idx["sold_at"]].startswith(day))
    unknown_store = sum(1 for r in rows if r[idx["store_id"]].strip().upper() not in KNOWN_STORES)
    negative = sum(1 for r in rows if (as_number(r[idx["quantity"]]) or 0) < 0 or (as_number(r[idx["unit_price"]]) or 0) < 0)
    cells = max(1, len(rows))
    if bad_numbers * 100 / cells > config.MAX_BAD_VALUE_PCT:
        fail(f"{bad_numbers:,} values are not numbers")
    for count, what in ((wrong_day, f"sales are not dated {day}"), (unknown_store, "rows have an unknown store"),
                        (negative, "rows have a negative quantity or price")):
        if count:
            fail(f"{count:,} {what}")
    if rows:
        q = quality_report(header, rows)
        report["score"], report["issues"] = q["score"], q["issues"]
        if q["score"] < min_score:
            fail(f"quality score {q['score']} is below {min_score}")
        for i in q["issues"]:
            if i["severity"] == "high":
                fail(i["message"])
    report["ok"] = not report["failures"]
    return report


def validate_or_raise(path, **kw):
    report = validate_file(path, **kw)
    if not report["ok"]:
        raise ValidationError(report)
    return report
