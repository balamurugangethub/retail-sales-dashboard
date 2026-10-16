"""Loads checked daily files into the raw layer of the DuckDB warehouse.

The raw layer keeps every value exactly as it arrived (all text), plus where and when it came from.
Loading a file twice replaces its rows instead of doubling them, so a re-run is always safe.
Every load and every rejected file is written to raw.load_audit, which the dashboard shows.
"""
import os
import shutil
from datetime import datetime, timezone

import duckdb

from warehouse import config

RAW_SCHEMA = f"""
CREATE SCHEMA IF NOT EXISTS raw;
CREATE TABLE IF NOT EXISTS raw.sales_lines (
    {", ".join(f"{c} VARCHAR" for c in config.COLUMNS)},
    _source_file VARCHAR NOT NULL,
    _file_date DATE NOT NULL,
    _loaded_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS raw.load_audit (
    run_at TIMESTAMP NOT NULL,
    file_name VARCHAR NOT NULL,
    file_date DATE,
    status VARCHAR NOT NULL,          -- loaded | rejected
    rows_in_file INTEGER,
    quality_score INTEGER,
    message VARCHAR
);
"""


def connect(path=None, read_only=False):
    path = path or config.WAREHOUSE_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = duckdb.connect(path, read_only=read_only)
    if not read_only:
        con.execute(RAW_SCHEMA)
    return con


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def loaded_files(con):
    return {r[0] for r in con.execute("SELECT DISTINCT _source_file FROM raw.sales_lines").fetchall()}


def new_files(con, landing_dir=None):
    """Daily files in the landing folder that are not in the warehouse yet, oldest first."""
    landing_dir = landing_dir or config.LANDING_DIR
    if not os.path.isdir(landing_dir):
        return []
    done = loaded_files(con)
    names = sorted(n for n in os.listdir(landing_dir)
                   if n.startswith(config.FILE_PREFIX) and n.endswith(".csv") and n not in done)
    return [os.path.join(landing_dir, n) for n in names]


def load_file(con, path, report):
    """Replaces this file's rows in raw.sales_lines. Returns the number of rows loaded."""
    name = os.path.basename(path)
    day = name[len(config.FILE_PREFIX):-4]
    cols = ", ".join(config.COLUMNS)
    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM raw.sales_lines WHERE _source_file = ?", [name])
        con.execute(f"INSERT INTO raw.sales_lines SELECT {cols}, ?, CAST(? AS DATE), ? "
                    f"FROM read_csv(?, header = true, all_varchar = true)", [name, day, now(), path])
        n = con.execute("SELECT COUNT(*) FROM raw.sales_lines WHERE _source_file = ?", [name]).fetchone()[0]
        audit(con, name, day, "loaded", n, report.get("score"), f"{n:,} rows loaded")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return n


def audit(con, name, day, status, rows, score, message):
    con.execute("INSERT INTO raw.load_audit VALUES (?, ?, CAST(? AS DATE), ?, ?, ?, ?)",
                [now(), name, day, status, rows, score, message])


def reject(con, path, report, quarantine_dir=None):
    """Records a failed file and moves it out of the landing folder, so it is not picked up again."""
    name = os.path.basename(path)
    audit(con, name, name[len(config.FILE_PREFIX):-4], "rejected", report.get("rows"), report.get("score"),
          " ".join(report.get("failures", []))[:1000])
    quarantine_dir = quarantine_dir or os.path.join(os.path.dirname(path), "quarantine")
    os.makedirs(quarantine_dir, exist_ok=True)
    target = os.path.join(quarantine_dir, name)
    shutil.move(path, target)
    return target
