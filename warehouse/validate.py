"""Checks one daily file before it is loaded. A file that fails stops the run.

Three kinds of check, in order (the feed, from the file name, sets the rules: see feeds.py):
1. Shape: the file has the expected columns and enough rows.
2. Business rules: every number parses, every row is dated on the file's day, plus the feed's own
   rules (known stores for the tills; cancellations and adjustments for the online shop).
3. The app's own data quality checks (quality.py, the same ones the Explore page shows): the file
   is copied into a small in-memory table in the layout quality.check expects, and the run fails if
   the score is below the feed's minimum or a problem is rated high (for example 30%+ missing values).
"""
import csv
import os
import sqlite3
import sys

from warehouse import config, feeds
from warehouse.feeds import as_number

sys.path.insert(0, config.REPO)
import quality  # noqa: E402  (the app's module, reused as a pipeline step)

KNOWN_STORES = feeds.KNOWN_STORES


class ValidationError(Exception):
    def __init__(self, report, rejected=1):
        super().__init__(f"{report['file']} failed validation: " + "; ".join(report["failures"]))
        self.report = report
        self.rejected = rejected


def file_day(path):
    name = os.path.basename(path)
    return (feeds.feed_for(name) or feeds.POS).day_of(name)


def quality_report(header, rows, column_types=None, skip=()):
    """Runs quality.check on the rows, laid out as the ds_<id> table it reads (columns c0..cN)."""
    column_types = column_types or config.COLUMN_TYPES
    keep = [i for i, name in enumerate(header) if name not in skip]
    con = sqlite3.connect(":memory:")
    cols = [{"name": header[i], "type": column_types.get(header[i], "text")} for i in keep]
    con.execute(f"CREATE TABLE ds_0 ({', '.join(f'c{i}' for i in range(len(cols)))})")
    typed = []
    for row in rows:
        typed.append([(as_number(row[i]) if c["type"] == "number" else row[i]) if row[i] != "" else None
                      for c, i in zip(cols, keep)])
    con.executemany(f"INSERT INTO ds_0 VALUES ({', '.join('?' * len(cols))})", typed)
    report = quality.check(con, {"id": 0, "rows": len(rows), "columns": cols})
    con.close()
    return report


def validate_file(path, min_rows=None, min_score=None):
    """Returns a report dict. report['ok'] is False when the file must not be loaded."""
    name = os.path.basename(path)
    feed = feeds.feed_for(name) or feeds.POS
    min_rows = feed.min_rows if min_rows is None else min_rows
    min_score = feed.min_score if min_score is None else min_score
    report = {"file": name, "feed": feed.name, "rows": 0, "score": None, "failures": [], "issues": []}
    fail = report["failures"].append
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = [h.strip().lower() for h in next(reader, [])]
        rows = [r for r in reader if any(r)]
    report["rows"] = len(rows)
    missing = [c for c in feed.columns if c not in header]
    if missing:
        fail("missing columns: " + ", ".join(missing))
        report["ok"] = False
        return report
    if len(rows) < min_rows:
        fail(f"only {len(rows)} rows (expected at least {min_rows})")
    rows = [r for r in rows if len(r) == len(header)] if rows else rows
    idx = {c: header.index(c) for c in feed.columns}
    day = feed.day_of(name).isoformat()
    bad_numbers = sum(1 for r in rows for c, t in feed.column_types.items()
                      if t == "number" and r[idx[c]] != "" and as_number(r[idx[c]]) is None)
    if bad_numbers * 100 / max(1, len(rows)) > config.MAX_BAD_VALUE_PCT:
        fail(f"{bad_numbers:,} values are not numbers")
    wrong_day = sum(1 for r in rows if not r[idx[feed.date_column]].startswith(day))
    if wrong_day:
        fail(f"{wrong_day:,} rows are not dated {day}" if feed is not feeds.POS else f"{wrong_day:,} sales are not dated {day}")
    for failure in feed.rules(rows, idx) if feed.rules else []:
        fail(failure)
    if rows:
        q = quality_report(header, rows, feed.column_types, feed.quality_skip)
        report["score"], report["issues"] = q["score"], q["issues"]
        if q["score"] < min_score:
            fail(f"quality score {q['score']} is below {min_score}")
        for i in q["issues"]:
            if i["severity"] == "high" and (not feed.fail_on_high or i["kind"] in feed.fail_on_high):
                fail(i["message"])
    report["ok"] = not report["failures"]
    return report


def validate_or_raise(path, **kw):
    report = validate_file(path, **kw)
    if not report["ok"]:
        raise ValidationError(report)
    return report
