import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app  # noqa: E402

H1 = "start=2026-01-01&end=2026-06-30"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("db") / "test.db")
    return create_app(db_path=path).test_client()


def get(client, url):
    r = client.get(url)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def test_kpis_match_presentation(client):
    k = get(client, f"/api/kpis?{H1}")
    assert k["current"]["sales"] == 482000.00
    assert k["current"]["profit"] == 96000.00
    assert k["current"]["orders"] == 12340
    assert k["current"]["basket"] == pytest.approx(39.06, abs=0.01)


def test_change_vs_previous_period(client):
    k = get(client, f"/api/kpis?{H1}")["change_pct"]
    assert k["sales"] == pytest.approx(8.4, abs=0.1)
    assert k["profit"] == pytest.approx(3.1, abs=0.1)
    assert k["orders"] == pytest.approx(-1.2, abs=0.1)


def test_sales_by_store_match_presentation(client):
    rows = get(client, f"/api/by-store?{H1}")
    assert {r["store"]: r["sales"] for r in rows} == {
        "Store A": 120000.0, "Store B": 98000.0, "Store C": 105000.0,
        "Store D": 82000.0, "Store E": 77000.0}


def test_monthly_trend_matches_presentation(client):
    rows = get(client, f"/api/trend?{H1}")
    assert [r["sales"] for r in rows] == [65000.0, 70000.0, 74000.0, 79000.0, 88000.0, 106000.0]


def test_store_filter_applies_to_every_endpoint(client):
    k = get(client, f"/api/kpis?{H1}&store=Store D")
    assert k["current"]["sales"] == 82000.00
    cats = get(client, f"/api/categories?{H1}&store=Store D")
    assert sum(c["sales"] for c in cats) == pytest.approx(82000.0, abs=0.01)
    prods = get(client, f"/api/top-products?{H1}&store=Store D&limit=3")
    assert len(prods) == 3 and prods[0]["sales"] >= prods[1]["sales"]


def test_insights_flag_weak_store(client):
    texts = " ".join(i["text"] for i in get(client, f"/api/insights?{H1}"))
    assert "Store A leads" in texts and "Store E is lowest" in texts


def test_bad_inputs_are_rejected(client):
    assert client.get("/api/kpis?start=nope").status_code == 400
    assert client.get("/api/kpis?start=2026-02-01&end=2026-01-01").status_code == 400
    assert client.get("/api/trend?grain=year").status_code == 400
    assert client.get("/api/top-products?limit=abc").status_code == 400


def test_sql_injection_attempt_is_harmless(client):
    r = client.get("/api/by-store?store=x' OR '1'='1")
    assert r.status_code == 200 and r.get_json() == []


def test_export_roundtrip_header(client):
    r = client.get(f"/api/export.csv?{H1}&store=Store A")
    assert r.status_code == 200
    first = r.get_data(as_text=True).splitlines()[0]
    assert first == "date,store,category,product,orders,units,revenue,cost"


def post_csv(client, text, mode="replace"):
    return client.post("/api/upload", data={"file": (io.BytesIO(text.encode()), "x.csv"), "mode": mode},
                       content_type="multipart/form-data")


def test_read_only_mode_blocks_writes(tmp_path):
    c = create_app(db_path=str(tmp_path / "ro.db"), read_only=True).test_client()
    assert get(c, "/api/meta")["read_only"] is True
    assert c.post("/api/reset").status_code == 403
    assert post_csv(c, "date,store,category,product,orders,units,revenue,cost\n").status_code == 403
    assert get(c, "/api/kpis")["current"]["sales"] > 0          # reads still work


def test_upload_validates_then_replaces_then_resets(tmp_path):
    c = create_app(db_path=str(tmp_path / "u.db")).test_client()
    bad = post_csv(c, "date,store,category,product,orders,units,revenue,cost\n2026-01-01,S,C,P,x,1,5,3\n")
    assert bad.status_code == 400 and "line 2" in bad.get_json()["error"]
    assert get(c, "/api/meta")["rows"] > 1000          # nothing was imported

    assert post_csv(c, "date,store\n").status_code == 400   # missing columns

    ok = post_csv(c, "date,store,category,product,orders,units,revenue,cost\n"
                     "2026-01-01,Shop 1,Food,Bread,3,4,12.50,8.00\n")
    assert ok.status_code == 200 and ok.get_json()["imported"] == 1
    k = get(c, "/api/kpis")["current"]
    assert (k["sales"], k["profit"], k["orders"]) == (12.5, 4.5, 3)

    assert c.post("/api/reset").status_code == 200
    assert get(c, "/api/meta")["rows"] > 1000
