"""The second feed (UCI Online Retail II), run history and freshness, the Supabase Storage landing zone,
the cloud run and the published snapshot the hosted Warehouse page reads. No real accounts are used:
Supabase Storage is a small fake server, and the workbook is a tiny one written here."""
import functools
import json
import os
import random
import shutil
import threading
import urllib.parse
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from warehouse import config, feeds, generate, online_retail, transform
from warehouse.validate import ValidationError, validate_file

duckdb = pytest.importorskip("duckdb")
openpyxl = pytest.importorskip("openpyxl")
from warehouse import cloud_run, load, object_store, observe, run, snapshot  # noqa: E402  (need duckdb)

HEADERS = ["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"]
START = datetime(2010, 11, 28, 9)


def has_dbt():
    try:
        transform.dbt_command()
        return True
    except transform.TransformError:
        return False


def invoice_rows(first_day, last_day, rng):
    rows = []
    for d in range(first_day, last_day):
        for inv in range(20):
            when = START + timedelta(days=d, minutes=7 * inv)
            cancelled = inv == 19
            for line in range(3):
                q = rng.randint(1, 12)
                rows.append([("C" if cancelled else "") + str(489000 + 100 * d + inv), f"8500{line}A" if line else "POST",
                             f"ITEM {line}", -q if cancelled else q, when, round(rng.uniform(0.5, 9), 2),
                             None if inv % 5 == 0 else float(12000 + inv), "France" if inv % 3 == 0 else "United Kingdom"])
    return rows


def workbook(path, days=6, overlap=(3, 5), bigger_second=False):
    """Two sheets like the real file; days overlap[0]..overlap[1]-1 are in both sheets."""
    rng = random.Random(1)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    first = wb.create_sheet("Year 2009-2010")
    first.append(HEADERS)
    for r in invoice_rows(0, overlap[1], rng):
        first.append(r)
    second = wb.create_sheet("Year 2010-2011")
    second.append(HEADERS)
    extra = invoice_rows(overlap[0], days, rng)
    if bigger_second:
        extra += invoice_rows(overlap[0], overlap[0] + 1, random.Random(9))
    for r in extra:
        second.append(r)
    wb.save(path)
    return path


@pytest.fixture
def landing(tmp_path):
    return str(tmp_path / "landing")


def test_online_retail_lands_one_file_per_day_without_double_counting_overlaps(tmp_path, landing):
    files, rows = online_retail.land(landing, workbook(str(tmp_path / "or.xlsx")))
    assert files == 6 and rows == 6 * 60
    assert online_retail.landed_days(landing) == [date(2010, 11, 28) + timedelta(days=i) for i in range(6)]
    name = feeds.ONLINE_RETAIL.file_name(date(2010, 11, 28))
    with open(os.path.join(landing, name), encoding="utf-8") as f:
        header, first, rest = f.readline().strip(), f.readline().strip(), f.read()
    assert header.split(",") == feeds.ONLINE_RETAIL.columns
    assert ",2010-11-28 09:00:00," in first
    assert ",12001," in rest                                  # 12001.0 is written as 12001
    # where the second sheet has more lines for a shared day, that sheet wins
    other = str(tmp_path / "other")
    online_retail.land(other, workbook(str(tmp_path / "or2.xlsx"), bigger_second=True))
    with open(os.path.join(other, feeds.ONLINE_RETAIL.file_name(date(2010, 12, 1))), encoding="utf-8") as f:
        assert sum(1 for _ in f) - 1 == 120
    assert online_retail.land(str(tmp_path / "two"), str(tmp_path / "or.xlsx"), date(2010, 11, 30), 2)[0] == 2


def test_online_files_pass_their_own_rules_and_fail_on_real_problems(tmp_path, landing):
    online_retail.land(landing, workbook(str(tmp_path / "or.xlsx")))
    path = os.path.join(landing, feeds.ONLINE_RETAIL.file_name(date(2010, 11, 29)))
    report = validate_file(path)
    assert report["ok"], report["failures"]
    assert report["feed"] == "online_retail" and report["score"] >= feeds.ONLINE_RETAIL.min_score
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    broken = [lines[0]] + [ln.replace(",France", ",").replace("ITEM 1,", "ITEM 1,-") if i % 2 else ln
                           for i, ln in enumerate(lines[1:])]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(broken) + "\n")
    failures = " ".join(validate_file(path)["failures"])
    assert "no country" in failures and "negative quantity or price" in failures


def test_feeds_are_told_apart_by_file_name():
    assert feeds.feed_for("sales_2026-01-01.csv") is feeds.POS
    assert feeds.feed_for("online_retail_2010-01-04.csv") is feeds.ONLINE_RETAIL
    assert feeds.feed_for("notes.csv") is None and feeds.feed_for("sales_2026-01-01.txt") is None


def test_a_failed_run_is_recorded_with_its_rejected_file(tmp_path, landing, monkeypatch):
    for name in ("PIPELINE_TRIGGER", "GITHUB_ACTIONS", "AIRFLOW_CTX_DAG_ID"):
        monkeypatch.delenv(name, raising=False)
    generate.write_day(date(2026, 3, 14), landing, generate.load_products(), corrupt=True)
    wh = str(tmp_path / "wh.duckdb")
    with pytest.raises(ValidationError):
        run.run_pipeline(wh, landing, with_snapshot=False)
    con = duckdb.connect(wh, read_only=True)
    h = observe.summary(con)
    con.close()
    last = h["runs"][0]
    assert last["status"] == "failed" and last["files_found"] == 1 and last["files_rejected"] == 1
    assert "rejected" in last["error"] and last["seconds"] is not None and last["trigger"] == "command line"
    assert [(s["step"], s["status"]) for s in h["steps"]] == [("find_new_files", "success"), ("validate", "failed")]
    assert h["success_rate"] == 0 and h["last_success"] is None


def test_run_history_summary_handles_an_old_warehouse(tmp_path):
    con = duckdb.connect(str(tmp_path / "old.duckdb"))
    assert observe.summary(con) == {"runs": []}
    con.close()


# --- Supabase Storage as the landing zone -------------------------------------------------------

class FakeStorage:
    """Just enough of Supabase Storage's REST API: buckets, list, upload, download and move."""

    def __init__(self, key="sb_secret_test"):
        self.key, self.buckets, self.objects, self.calls = key, set(), {}, []
        store = self

        class Handler(BaseHTTPRequestHandler):
            def reply(self, code, body=b"", kind="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def body(self):
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def handle_any(self, method):
                path = urllib.parse.unquote(self.path)
                store.calls.append((method, path))
                if self.headers.get("apikey") != store.key or "Authorization" in self.headers:
                    return self.reply(401, b'{"message":"bad key"}')
                if method == "POST" and path == "/storage/v1/bucket":
                    name = json.loads(self.body())["id"]
                    if name in store.buckets:
                        return self.reply(400, b'{"statusCode":"409","error":"Duplicate",'
                                               b'"message":"The resource already exists"}')
                    store.buckets.add(name)
                    return self.reply(200, b'{"name":"ok"}')
                if method == "POST" and path.startswith("/storage/v1/object/list/"):
                    q = json.loads(self.body())
                    names = sorted(k.split("/", 1)[1] for k in store.objects if k.startswith(q["prefix"] + "/"))
                    page = names[q["offset"]:q["offset"] + q["limit"]]
                    return self.reply(200, json.dumps([{"name": n} for n in page]).encode())
                if method == "POST" and path == "/storage/v1/object/move":
                    q = json.loads(self.body())
                    store.objects[q["destinationKey"]] = store.objects.pop(q["sourceKey"])
                    return self.reply(200, b'{"message":"Successfully moved"}')
                prefix = "/storage/v1/object/warehouse-landing/"
                if path.startswith(prefix):
                    key = path[len(prefix):]
                    if method == "POST":
                        store.objects[key] = self.body()
                        return self.reply(200, b'{"Key":"ok"}')
                    if key in store.objects:
                        return self.reply(200, store.objects[key], "text/csv")
                return self.reply(404, b'{"message":"not found"}')

            def do_GET(self):  # noqa: N802
                self.handle_any("GET")

            def do_POST(self):  # noqa: N802
                self.handle_any("POST")

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def storage():
    fake = FakeStorage()
    yield fake
    fake.close()


def test_supabase_bucket_lists_uploads_downloads_and_quarantines(tmp_path, storage, monkeypatch):
    monkeypatch.setattr(object_store, "PAGE", 2)              # make listing page through results
    bucket = object_store.SupabaseBucket(storage.url, storage.key)
    bucket.ensure()
    bucket.ensure()                                           # an existing bucket is fine
    paths = [generate.write_day(date(2026, 3, 14) + timedelta(days=i), str(tmp_path / "out"), generate.load_products(),
                                scale=0.1)[0] for i in range(3)]
    assert bucket.upload(paths) == 3
    names = bucket.names()
    assert names == [os.path.basename(p) for p in paths]
    assert bucket.download(names[:2], str(tmp_path / "down")) == 2
    with open(paths[0], "rb") as a, open(tmp_path / "down" / names[0], "rb") as b:
        assert a.read() == b.read()
    bucket.quarantine([names[2]])
    assert bucket.names() == names[:2] and bucket.names(object_store.QUARANTINE) == [names[2]]
    with pytest.raises(object_store.StoreError, match="401"):
        object_store.SupabaseBucket(storage.url, "wrong").names()


def test_store_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    assert isinstance(object_store.from_env(str(tmp_path)), object_store.LocalFolder)
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", " sb_secret_abc \n")
    store = object_store.from_env(str(tmp_path))
    assert isinstance(store, object_store.SupabaseBucket) and store.key == "sb_secret_abc"
    assert "Authorization" not in store.headers() and store.bucket == "warehouse-landing"
    monkeypatch.setenv("MOTHERDUCK_TOKEN", "t")
    assert cloud_run.warehouse_target() == "md:retail_warehouse"
    monkeypatch.delenv("MOTHERDUCK_TOKEN")
    assert cloud_run.warehouse_target() == config.WAREHOUSE_PATH


def test_missing_till_days_skips_landed_and_quarantined_days():
    have = ["sales_2026-03-14.csv", "sales_2026-03-16.csv", "online_retail_2010-01-04.csv"]
    assert cloud_run.missing_till_days(have, date(2026, 3, 17), date(2026, 3, 14)) == [date(2026, 3, 15), date(2026, 3, 17)]


def test_cloud_landing_puts_new_days_and_the_public_dataset_in_the_bucket_once(tmp_path, storage):
    bucket = object_store.SupabaseBucket(storage.url, storage.key)
    bucket.ensure()
    xlsx = workbook(str(tmp_path / "or.xlsx"))
    got = cloud_run.land(bucket, until=date(2026, 3, 16), since=date(2026, 3, 14), public_file=xlsx)
    assert got == {"pos": 3, "online_retail": 6}
    assert cloud_run.land(bucket, until=date(2026, 3, 17), since=date(2026, 3, 14), public_file=xlsx) == \
        {"pos": 1, "online_retail": 0}
    landing = str(tmp_path / "landing")
    wh = str(tmp_path / "wh.duckdb")
    assert cloud_run.sync(bucket, wh, landing) == 10
    assert cloud_run.sync(bucket, wh, landing) == 0            # already on the runner


def read(site, name):
    with open(os.path.join(site, "data", name), encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.skipif(not has_dbt(), reason="dbt is not installed (pip install -r warehouse/requirements.txt)")
def test_cloud_run_end_to_end_with_a_bucket_publishes_catalog_and_history(tmp_path, storage, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(config, "LANDING_DIR", str(data / "landing"))
    monkeypatch.setattr(config, "WAREHOUSE_PATH", str(data / "wh.duckdb"))
    monkeypatch.setattr(config, "ALERT_LOG", str(data / "alerts.log"))
    monkeypatch.setenv("SUPABASE_URL", storage.url)
    monkeypatch.setenv("SUPABASE_SECRET_KEY", storage.key)
    monkeypatch.delenv("MOTHERDUCK_TOKEN", raising=False)
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    xlsx = workbook(str(tmp_path / "or.xlsx"))
    site = str(tmp_path / "site")
    args = ["--site", site, "--since", "2026-03-14", "--until", "2026-03-15", "--public-file", xlsx]
    assert cloud_run.main(args) == 0
    assert os.path.getsize(os.path.join(site, "index.html")) > 100_000        # dbt docs, one static page
    with open(os.path.join(site, "index.html"), encoding="utf-8") as f:
        catalog = f.read()
    assert "fct_online_sales" in catalog and "mart_online_daily" in catalog
    manifest = read(site, "manifest.json")
    assert manifest["files_loaded"] == 2 and manifest["online_retail"]["files_loaded"] == 6
    assert manifest["online_retail"]["rows"]["fact"] == 360 and manifest["dbt"]["passed"] == manifest["dbt"]["tests"]
    health = read(site, "observability.json")
    assert health["runs"][0]["status"] == "success" and health["runs"][0]["freshness"] == "pass"
    assert {s["step"] for s in health["steps"]} >= {"validate", "load_raw", "dbt_build", "source_freshness"}
    assert [f["source_table"] for f in health["freshness"]] == ["raw.sales_lines"]
    assert health["slowest_models"]
    # the next day: one file, and a broken one is quarantined in the bucket and the run is recorded as failed
    generate.write_day(date(2026, 3, 16), str(tmp_path / "bad"), generate.load_products(), corrupt=True)
    object_store.SupabaseBucket(storage.url, storage.key).upload([str(tmp_path / "bad" / "sales_2026-03-16.csv")])
    assert cloud_run.main(["--site", site, "--since", "2026-03-14", "--until", "2026-03-16", "--public-file", xlsx]) == 1
    assert "quarantine/sales_2026-03-16.csv" in storage.objects
    health = read(site, "observability.json")
    assert [r["status"] for r in health["runs"][:2]] == ["failed", "success"] and health["success_rate"] == 50


# --- the hosted page reads the published snapshot ------------------------------------------------

@pytest.fixture
def published(tmp_path):
    folder = tmp_path / "pages" / "data"
    shutil.copytree(config.SNAPSHOT_DIR, folder)

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(tmp_path / "pages")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/data/", folder
    server.shutdown()
    server.server_close()


def test_page_reads_the_published_snapshot_and_falls_back_to_the_committed_one(tmp_path, published):
    import marts
    url, folder = published
    m = marts.Marts(warehouse_path=str(tmp_path / "none.duckdb"), snapshot_url=url)
    con, source = m.connection()
    assert source["kind"] == "published" and source["rows"]["fact"] > 1_000_000
    os.remove(folder / "manifest.json")
    m2 = marts.Marts(warehouse_path=str(tmp_path / "none.duckdb"), snapshot_url=url)
    _, source = m2.connection()
    assert source["kind"] == "snapshot" and "could not be fetched" in source["note"]
    assert marts.summary(con)["kpis"]["revenue"] > 0


def test_snapshot_url_defaults_to_pages_only_on_vercel(monkeypatch):
    import marts
    monkeypatch.delenv("WAREHOUSE_SNAPSHOT_URL", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    assert marts.default_snapshot_url() is None
    monkeypatch.setenv("VERCEL", "1")
    assert marts.default_snapshot_url() == marts.PAGES_URL
    monkeypatch.setenv("WAREHOUSE_SNAPSHOT_URL", "off")
    assert marts.default_snapshot_url() is None


def test_api_has_health_online_and_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("WAREHOUSE_PATH", str(tmp_path / "missing.duckdb"))
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("WAREHOUSE_SNAPSHOT_URL", raising=False)
    import app as app_module
    d = app_module.create_app(db_path=str(tmp_path / "app.db")).test_client().get("/api/warehouse").get_json()
    assert d["catalog_url"].startswith("https://") and "runs" in d["health"]
    assert "health" not in d["source"]
    assert d["online"] is None or d["online"]["kpis"]["orders"] > 0
    assert all("feed" in a for a in d["audit"])


def test_the_dag_records_runs_and_checks_freshness():
    with open(os.path.join(config.HERE, "airflow", "dags", "retail_daily.py"), encoding="utf-8") as f:
        src = f.read()
    assert "def source_freshness(" in src and "observe.Run(" in src and "PUBLIC_DATASET" in src


def test_snapshot_layers_cover_both_feeds():
    assert set(snapshot.LAYERS) == {f.name for f in feeds.FEEDS}
    assert all(f"raw.{f.table}" == snapshot.LAYERS[f.name]["raw"] for f in feeds.FEEDS)
    assert "raw.pipeline_runs" in load.RUN_HISTORY_SCHEMA
    assert load.RAW_SCHEMA.count("CREATE TABLE IF NOT EXISTS raw.") == len(feeds.FEEDS) + 1
