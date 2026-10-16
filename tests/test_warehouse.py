"""The warehouse pipeline: generator, validation (built on quality.py), raw loading, alerts, dbt and the
Warehouse page that reads the marts. The dbt test runs only where dbt is installed (the warehouse CI job)."""
import csv
import json
import os
import shutil
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from warehouse import alerts, config, generate, run, transform
from warehouse.validate import ValidationError, validate_file

duckdb = pytest.importorskip("duckdb")
from warehouse import load  # noqa: E402  (needs duckdb)

DAY = date(2026, 3, 14)


def has_dbt():
    try:
        transform.dbt_command()
        return True
    except transform.TransformError:
        return False


@pytest.fixture
def landing(tmp_path):
    return str(tmp_path / "landing")


def write(landing, day=DAY, **kw):
    return generate.write_day(day, landing, generate.load_products(), **kw)[0]


def test_generator_is_repeatable_and_covers_every_store(landing):
    rows = generate.day_rows(DAY, generate.load_products())
    assert rows == generate.day_rows(DAY, generate.load_products())
    assert {r[3] for r in rows} == set(generate.STORE_ORDERS)
    assert all(r[2].startswith("2026-03-14") for r in rows)
    with open(write(landing), newline="", encoding="utf-8") as f:
        assert next(csv.reader(f)) == config.COLUMNS


def test_default_run_writes_over_a_million_rows():
    products = generate.load_products()
    total = sum(len(generate.day_rows(generate.DEFAULT_START.fromordinal(generate.DEFAULT_START.toordinal() + i), products))
                for i in range(generate.DEFAULT_DAYS))
    assert total > 1_000_000


def test_next_continues_after_the_newest_file(landing):
    write(landing)
    generate.main(["--next", "2", "--out", landing])
    assert generate.landed_days(landing)[-1] == date(2026, 3, 16)


def test_clean_file_passes_with_the_app_quality_score(landing):
    report = validate_file(write(landing))
    assert report["ok"], report["failures"]
    assert report["score"] >= config.MIN_QUALITY_SCORE


def test_broken_file_fails_on_quality_checks(landing):
    report = validate_file(write(landing, corrupt=True))
    assert not report["ok"]
    text = " ".join(report["failures"])
    assert "exact copies" in text and "sku" in text and "empty" in text


def test_wrong_day_and_missing_columns_fail(landing):
    path = write(landing)
    os.rename(path, os.path.join(landing, config.file_name(date(2026, 3, 15))))
    report = validate_file(os.path.join(landing, config.file_name(date(2026, 3, 15))))
    assert any("not dated 2026-03-15" in f for f in report["failures"])
    bad = os.path.join(landing, config.file_name(DAY))
    with open(bad, "w", encoding="utf-8") as f:
        f.write("order_id,sold_at\nA,2026-03-14\n")
    assert "missing columns" in validate_file(bad)["failures"][0]


def test_alert_goes_to_the_log_without_a_webhook(tmp_path, monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    log = str(tmp_path / "alerts.log")
    assert alerts.send("File rejected", "sku empty", log_path=log) == "log"
    with open(log, encoding="utf-8") as f:
        assert "File rejected" in f.read()


def test_alert_posts_to_a_webhook(tmp_path):
    got = []

    class Hook(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=server.handle_request, daemon=True).start()
    where = alerts.send("dbt build failed", "a test failed", webhook_url=f"http://127.0.0.1:{server.server_port}/",
                        log_path=str(tmp_path / "a.log"))
    server.server_close()
    assert where == "webhook"
    assert "dbt build failed" in got[0]["text"] and got[0]["content"] == got[0]["text"]


def test_loading_a_file_twice_replaces_it(tmp_path, landing):
    path = write(landing)
    con = load.connect(str(tmp_path / "wh.duckdb"))
    n = load.load_file(con, path, {"score": 100})
    assert load.load_file(con, path, {"score": 100}) == n
    assert con.execute("SELECT COUNT(*) FROM raw.sales_lines").fetchone()[0] == n
    assert load.new_files(con, landing) == []
    assert con.execute("SELECT COUNT(*) FROM raw.load_audit WHERE status = 'loaded'").fetchone()[0] == 2
    con.close()


def test_a_bad_file_stops_the_run_and_is_quarantined(tmp_path, landing):
    good = write(landing)
    bad = write(landing, day=date(2026, 3, 15), corrupt=True)
    wh = str(tmp_path / "wh.duckdb")
    with pytest.raises(ValidationError) as err:
        run.validate_files(run.find_new_files(wh, landing), wh)
    assert "sales_2026-03-15.csv" in str(err.value)
    assert not os.path.exists(bad) and os.path.exists(os.path.join(landing, "quarantine", os.path.basename(bad)))
    assert os.path.exists(good)
    con = load.connect(wh)
    assert con.execute("SELECT status FROM raw.load_audit").fetchall() == [("rejected",)]
    assert con.execute("SELECT COUNT(*) FROM raw.sales_lines").fetchone()[0] == 0     # nothing was loaded
    con.close()
    title, details = run.describe_failure(err.value)
    assert "quarantine" in title and "exact copies" in details


def test_the_dag_has_the_pipeline_steps():
    with open(os.path.join(config.HERE, "airflow", "dags", "retail_daily.py"), encoding="utf-8") as f:
        src = f.read()
    for step in ("land_files", "find_new_files", "validate", "load_raw", "dbt_build", "export_snapshot", "alert_on_failure"):
        assert f"def {step}(" in src


def test_warehouse_page_reads_the_committed_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("WAREHOUSE_PATH", str(tmp_path / "missing.duckdb"))
    import app as app_module
    client = app_module.create_app(db_path=str(tmp_path / "app.db")).test_client()
    d = client.get("/api/warehouse").get_json()
    assert d["source"]["kind"] == "snapshot"
    with open(os.path.join(config.SNAPSHOT_DIR, "mart_store_daily.csv"), newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert d["kpis"]["revenue"] == pytest.approx(sum(float(r["revenue"]) for r in rows), abs=1)
    assert d["kpis"]["orders"] == sum(int(float(r["orders"])) for r in rows)
    assert d["source"]["rows"]["fact"] > 1_000_000
    one = client.get("/api/warehouse?store=Store A&start=2026-01&end=2026-06").get_json()
    assert [s["store"] for s in one["stores"]] == ["Store A"] and len(one["monthly"]) == 6
    assert client.get("/api/warehouse?start=2026").status_code == 400
    assert client.get("/api/warehouse?store=Nope").status_code == 400
    assert client.get("/warehouse").status_code == 200


@pytest.mark.skipif(not has_dbt(), reason="dbt is not installed (pip install -r warehouse/requirements.txt)")
def test_end_to_end_with_dbt(tmp_path, landing):
    for i in range(3):
        write(landing, day=date(2026, 3, 14 + i), scale=0.3)
    wh = str(tmp_path / "wh.duckdb")
    summary = run.run_pipeline(wh, landing, with_snapshot=False)
    assert summary["files"] == 3
    con = duckdb.connect(wh, read_only=True)
    fact = con.execute("SELECT COUNT(*), SUM(net_amount) FROM marts.fct_sales").fetchone()
    tables = {r[0] for r in con.execute("SELECT table_schema || '.' || table_name FROM information_schema.tables").fetchall()}
    con.close()
    assert {"raw.sales_lines", "staging.stg_sales_lines", "marts.fct_sales", "marts.dim_store", "marts.dim_product",
            "marts.dim_date", "marts.mart_store_daily"} <= tables
    out = str(tmp_path / "snap")
    shutil.copytree(config.SNAPSHOT_DIR, out)
    manifest = __import__("warehouse.snapshot", fromlist=["export"]).export(wh, out)
    assert manifest["rows"]["fact"] == fact[0] and manifest["files_loaded"] == 3
    import marts
    d = marts.summary(marts.Marts(warehouse_path=wh).connection()[0])
    assert d["kpis"]["revenue"] == pytest.approx(float(fact[1]), abs=0.05)
    # a new day only adds its own lines to the incremental fact table
    write(landing, day=date(2026, 3, 17), scale=0.3)
    assert run.run_pipeline(wh, landing, with_snapshot=False)["files"] == 1
