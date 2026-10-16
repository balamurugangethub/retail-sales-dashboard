"""Loads checked daily files into the raw layer of the warehouse (a DuckDB file, or MotherDuck in the cloud).

The raw layer keeps every value exactly as it arrived (all text), plus where and when it came from.
Each feed has its own raw table (see feeds.py). Loading a file twice replaces its rows instead of
doubling them, so a re-run is always safe. Every load and every rejected file is written to
raw.load_audit, which the dashboard shows.
"""
import os
import shutil
from datetime import datetime, timezone

import duckdb

from warehouse import config, feeds

RAW_SCHEMA = "CREATE SCHEMA IF NOT EXISTS raw;\n" + "\n".join(f"""
CREATE TABLE IF NOT EXISTS raw.{feed.table} (
    {", ".join(f"{c} VARCHAR" for c in feed.columns)},
    _source_file VARCHAR NOT NULL,
    _file_date DATE NOT NULL,
    _loaded_at TIMESTAMP NOT NULL
);""" for feed in feeds.FEEDS) + """
CREATE TABLE IF NOT EXISTS raw.load_audit (
    run_at TIMESTAMP NOT NULL,
    file_name VARCHAR NOT NULL,
    file_date DATE,
    status VARCHAR NOT NULL,          -- loaded | rejected
    rows_in_file INTEGER,
    quality_score INTEGER,
    message VARCHAR
);
ALTER TABLE raw.load_audit ADD COLUMN IF NOT EXISTS feed VARCHAR;
"""


RUN_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw.pipeline_runs (
    run_id VARCHAR NOT NULL,
    trigger VARCHAR,
    target VARCHAR,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    seconds DOUBLE,
    status VARCHAR NOT NULL,          -- running | success | failed
    files_found INTEGER,
    files_loaded INTEGER,
    files_rejected INTEGER,
    rows_loaded BIGINT,
    dbt_models INTEGER,
    dbt_tests INTEGER,
    dbt_tests_failed INTEGER,
    freshness VARCHAR,                -- pass | warn | error | not checked
    error VARCHAR
);
CREATE TABLE IF NOT EXISTS raw.pipeline_steps (
    run_id VARCHAR NOT NULL,
    step VARCHAR NOT NULL,
    started_at TIMESTAMP NOT NULL,
    seconds DOUBLE,
    status VARCHAR NOT NULL,
    detail VARCHAR
);
CREATE TABLE IF NOT EXISTS raw.dbt_results (
    run_id VARCHAR NOT NULL,
    unique_id VARCHAR NOT NULL,
    resource_type VARCHAR,
    status VARCHAR,
    seconds DOUBLE,
    rows_affected BIGINT,
    message VARCHAR
);
CREATE TABLE IF NOT EXISTS raw.source_freshness (
    run_id VARCHAR NOT NULL,
    checked_at TIMESTAMP NOT NULL,
    source_table VARCHAR NOT NULL,
    max_loaded_at TIMESTAMP,
    age_hours DOUBLE,
    status VARCHAR
);
"""


def connect(path=None, read_only=False):
    path = path or config.WAREHOUSE_PATH
    if config.is_motherduck(path):
        # md:<database>; the token comes from the MOTHERDUCK_TOKEN environment variable
        database = path[3:].split("?")[0] or "retail_warehouse"
        config.share_motherduck_token()
        con = duckdb.connect("md:")
        con.execute(f'CREATE DATABASE IF NOT EXISTS "{database}"')
        con.execute(f'USE "{database}"')
    else:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        con = duckdb.connect(path, read_only=read_only)
    if not read_only:
        con.execute(RAW_SCHEMA)
        con.execute(RUN_HISTORY_SCHEMA)     # see observe.py
    return con


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def loaded_files(con):
    done = set()
    for feed in feeds.FEEDS:
        done |= {r[0] for r in con.execute(f"SELECT DISTINCT _source_file FROM raw.{feed.table}").fetchall()}
    return done


def new_files(con, landing_dir=None):
    """Daily files of any feed in the landing folder that are not in the warehouse yet, oldest first per feed."""
    landing_dir = landing_dir or config.LANDING_DIR
    if not os.path.isdir(landing_dir):
        return []
    done = loaded_files(con)
    names = sorted(n for n in os.listdir(landing_dir) if feeds.feed_for(n) and n not in done)
    return [os.path.join(landing_dir, n) for n in names]


def load_file(con, path, report):
    """Replaces this file's rows in its feed's raw table. Returns the number of rows loaded."""
    name = os.path.basename(path)
    feed = feeds.feed_for(name)
    day = feed.day_of(name).isoformat()
    cols = ", ".join(feed.columns)
    con.execute("BEGIN")
    try:
        con.execute(f"DELETE FROM raw.{feed.table} WHERE _source_file = ?", [name])
        con.execute(f"INSERT INTO raw.{feed.table} SELECT {cols}, ?, CAST(? AS DATE), ? "
                    f"FROM read_csv(?, header = true, all_varchar = true)", [name, day, now(), path])
        n = con.execute(f"SELECT COUNT(*) FROM raw.{feed.table} WHERE _source_file = ?", [name]).fetchone()[0]
        audit(con, name, day, "loaded", n, report.get("score"), f"{n:,} rows loaded", feed.name)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return n


def audit(con, name, day, status, rows, score, message, feed_name=None):
    con.execute("INSERT INTO raw.load_audit (run_at, file_name, file_date, status, rows_in_file, quality_score,"
                " message, feed) VALUES (?, ?, TRY_CAST(? AS DATE), ?, ?, ?, ?, ?)",
                [now(), name, day, status, rows, score, message, feed_name])


def reject(con, path, report, quarantine_dir=None):
    """Records a failed file and moves it out of the landing folder, so it is not picked up again."""
    name = os.path.basename(path)
    feed = feeds.feed_for(name) or feeds.POS
    audit(con, name, name[len(feed.prefix):-4], "rejected", report.get("rows"), report.get("score"),
          " ".join(report.get("failures", []))[:1000], feed.name)
    quarantine_dir = quarantine_dir or os.path.join(os.path.dirname(path), "quarantine")
    os.makedirs(quarantine_dir, exist_ok=True)
    target = os.path.join(quarantine_dir, name)
    shutil.move(path, target)
    return target
