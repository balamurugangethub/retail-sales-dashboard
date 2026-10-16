"""Copies the small reporting marts out of the warehouse into warehouse/snapshot/ as CSV files.

The live site on Vercel has no warehouse and no long-running server, so it reads this snapshot
(committed to git). Locally the dashboard reads the DuckDB warehouse itself when it exists.
"""
import csv
import json
import os
from datetime import datetime, timezone

from warehouse import config, load

MARTS = ["mart_store_daily", "mart_category_monthly", "mart_product_monthly"]
AUDIT_ROWS = 30
TESTS_FILE = os.path.join(config.DBT_DIR, "target", "run_results.json")


def dbt_test_summary():
    if not os.path.exists(TESTS_FILE):
        return None
    with open(TESTS_FILE, encoding="utf-8") as f:
        results = json.load(f).get("results", [])
    tests = [r for r in results if r.get("unique_id", "").startswith("test.")]
    return {"tests": len(tests), "passed": sum(1 for r in tests if r.get("status") == "pass")}


def layer_counts(con):
    tables = {"raw": "raw.sales_lines", "staging": "staging.stg_sales_lines", "fact": "marts.fct_sales"}
    return {k: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for k, t in tables.items()}


def write_csv(con, sql, path):
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(names)
        for row in cur.fetchall():
            w.writerow([round(float(v), 2) if hasattr(v, "as_tuple") else v for v in row])


def export(warehouse_path=None, out_dir=None):
    out_dir = out_dir or config.SNAPSHOT_DIR
    os.makedirs(out_dir, exist_ok=True)
    con = load.connect(warehouse_path, read_only=True)
    try:
        for mart in MARTS:
            write_csv(con, f"SELECT * FROM marts.{mart} ORDER BY ALL", os.path.join(out_dir, f"{mart}.csv"))
        write_csv(con, f"SELECT * FROM raw.load_audit ORDER BY run_at DESC, file_name DESC LIMIT {AUDIT_ROWS}",
                  os.path.join(out_dir, "load_audit.csv"))
        lo, hi = con.execute("SELECT MIN(date_day), MAX(date_day) FROM marts.mart_store_daily").fetchone()
        manifest = {"exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "first_day": str(lo), "last_day": str(hi), "rows": layer_counts(con),
                    "files_loaded": con.execute("SELECT COUNT(DISTINCT _source_file) FROM raw.sales_lines").fetchone()[0],
                    "dbt": dbt_test_summary()}
    finally:
        con.close()
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return manifest
