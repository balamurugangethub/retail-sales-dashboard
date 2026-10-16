"""Copies the small reporting marts out of the warehouse into warehouse/snapshot/ as CSV files.

The live site on Vercel has no warehouse and no long-running server, so it reads this snapshot
(committed to git). Locally the dashboard reads the DuckDB warehouse itself when it exists.
"""
import csv
import json
import os
from datetime import datetime, timezone

from warehouse import config, feeds, load, observe

MARTS = ["mart_store_daily", "mart_category_monthly", "mart_product_monthly",
         "mart_online_daily", "mart_online_country_monthly", "mart_online_top_products"]
AUDIT_ROWS = 30
TESTS_FILE = os.path.join(config.DBT_DIR, "target", "run_results.json")


def dbt_test_summary():
    if not os.path.exists(TESTS_FILE):
        return None
    with open(TESTS_FILE, encoding="utf-8") as f:
        results = json.load(f).get("results", [])
    tests = [r for r in results if r.get("unique_id", "").startswith("test.")]
    return {"tests": len(tests), "passed": sum(1 for r in tests if r.get("status") == "pass")}


LAYERS = {"pos": {"raw": "raw.sales_lines", "staging": "staging.stg_sales_lines", "fact": "marts.fct_sales"},
          "online_retail": {"raw": "raw.online_retail_lines", "staging": "staging.stg_online_retail_lines",
                            "fact": "marts.fct_online_sales"}}


def has_table(con, qualified):
    schema, name = qualified.split(".")
    return con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                       [schema, name]).fetchone()[0] > 0


def count(con, qualified):
    return con.execute(f"SELECT COUNT(*) FROM {qualified}").fetchone()[0] if has_table(con, qualified) else 0


def layer_counts(con, feed="pos"):
    return {k: count(con, t) for k, t in LAYERS[feed].items()}


def day_range(con, mart):
    if not has_table(con, f"marts.{mart}"):
        return None, None
    lo, hi = con.execute(f"SELECT MIN(date_day), MAX(date_day) FROM marts.{mart}").fetchone()
    return lo and str(lo), hi and str(hi)


def files_loaded(con, feed):
    if not has_table(con, f"raw.{feed.table}"):
        return 0
    return con.execute(f"SELECT COUNT(DISTINCT _source_file) FROM raw.{feed.table}").fetchone()[0]


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
            # a failed dbt test skips the marts built on it; publish the rest so the failure is visible
            if not has_table(con, f"marts.{mart}"):
                continue
            write_csv(con, f"SELECT * FROM marts.{mart} ORDER BY ALL", os.path.join(out_dir, f"{mart}.csv"))
        write_csv(con, f"SELECT * FROM raw.load_audit ORDER BY run_at DESC, file_name DESC LIMIT {AUDIT_ROWS}",
                  os.path.join(out_dir, "load_audit.csv"))
        lo, hi = day_range(con, "mart_store_daily")
        olo, ohi = day_range(con, "mart_online_daily")
        manifest = {"exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "target": observe.target_name(warehouse_path),
                    "first_day": lo, "last_day": hi, "rows": layer_counts(con),
                    "files_loaded": files_loaded(con, feeds.POS),
                    "online_retail": {"first_day": olo, "last_day": ohi,
                                      "rows": layer_counts(con, "online_retail"),
                                      "files_loaded": files_loaded(con, feeds.ONLINE_RETAIL),
                                      "customers": count(con, "marts.dim_customer")},
                    "dbt": dbt_test_summary()}
        health = observe.summary(con)
        last = next((r for r in health["runs"] if r.get("dbt_tests")), None)
        if last:                            # run_results.json is overwritten by later dbt commands such as docs
            manifest["dbt"] = {"tests": last["dbt_tests"], "passed": last["dbt_tests"] - (last["dbt_tests_failed"] or 0)}
    finally:
        con.close()
    with open(os.path.join(out_dir, "observability.json"), "w", encoding="utf-8") as f:
        json.dump(health, f, indent=1, default=str)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return manifest
