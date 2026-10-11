import io

import pytest

from app import create_app

CSV = ("Visit date,Ward,Patients,Wait (min),Patient ID\n"
       + "".join(f"2026-0{1 + d // 28}-{1 + d % 28:02d},{'A' if d % 2 else 'B'},{10 + d % 7},{30 + d % 5},{1000 + d}\n"
                 for d in range(84)))


@pytest.fixture()
def client(tmp_path):
    return create_app(db_path=str(tmp_path / "t.db")).test_client()


def upload(client, text=CSV, name="ward.csv"):
    r = client.post("/api/datasets", data={"file": (io.BytesIO(text.encode()), name)},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["id"]


def post(client, ds, path, body):
    return client.post(f"/api/datasets/{ds}/{path}", json=body)


def test_column_types_are_worked_out(client):
    ds = upload(client)
    cols = client.get(f"/api/datasets/{ds}").get_json()["columns"]
    assert [c["type"] for c in cols] == ["date", "text", "number", "number", "text"]   # ids stay text


def test_any_columns_any_layout(client):
    ds = upload(client, "Machine,Hours,Faults\nPress 1,10,2\nPress 2,7,0\nPress 1,5,1\n", "machines.csv")
    out = post(client, ds, "query", {"dims": [{"field": 0}], "measures": [{"field": 1, "agg": "sum"}]}).get_json()
    assert dict(map(tuple, out["rows"])) == {"Press 1": 15.0, "Press 2": 7.0}


def test_query_grains_filters_and_pivot(client):
    ds = upload(client)
    out = post(client, ds, "query", {"dims": [{"field": 0, "grain": "month"}, {"field": 1}],
                                    "measures": [{"field": 2, "agg": "avg"}],
                                    "filters": [{"field": 3, "op": "ge", "value": "31"}]}).get_json()
    assert out["dims"] == 2 and out["rows"] and len(out["rows"][0]) == 3
    assert post(client, ds, "query", {"dims": [], "measures": [{"agg": "rows"}]}).get_json()["rows"] == [[84]]


def test_bad_requests_are_rejected_not_executed(client):
    ds = upload(client)
    for body in ({"dims": [{"field": 99}], "measures": [{"agg": "rows"}]},
                 {"dims": [], "measures": [{"field": 1, "agg": "sum"}]},                 # sum of text
                 {"dims": [], "measures": [{"field": 2, "agg": "drop table"}]},
                 {"dims": [], "measures": [{"agg": "rows"}], "filters": [{"field": 2, "op": "gt", "value": "x"}]},
                 {"dims": [{"field": 1}], "measures": [{"agg": "rows"}], "sort": {"by": "d1; DROP TABLE x"}}):
        assert post(client, ds, "query", body).status_code == 400
    assert client.get(f"/api/datasets/{ds}").status_code == 200


def test_filter_text_is_never_sql(client):
    ds = upload(client)
    body = {"dims": [], "measures": [{"agg": "rows"}], "filters": [{"field": 1, "op": "eq", "value": "A' OR '1'='1"}]}
    assert post(client, ds, "query", body).get_json()["rows"] == [[0]]


def test_forecast_and_too_little_history(client):
    ds = upload(client)
    out = post(client, ds, "forecast", {"date": 0, "measure": 2, "agg": "sum"}).get_json()
    assert out["ok"] and len(out["forecast"]) == out["horizon"]
    short = upload(client, "d,v\n2026-01-01,1\n2026-01-02,2\n", "short.csv")
    assert post(client, short, "forecast", {"date": 0, "measure": 1, "agg": "sum"}).get_json()["ok"] is False
    assert post(client, ds, "forecast", {"date": 1, "measure": 2, "agg": "sum"}).status_code == 400


def test_delete_and_read_only(tmp_path):
    c = create_app(db_path=str(tmp_path / "a.db")).test_client()
    ds = upload(c)
    assert c.delete(f"/api/datasets/{ds}").status_code == 200
    assert c.get("/api/datasets").get_json()["datasets"] == []
    ro = create_app(db_path=str(tmp_path / "b.db"), read_only=True).test_client()
    assert ro.post("/api/datasets", data={"file": (io.BytesIO(b"a,b\n1,2\n"), "x.csv")},
                   content_type="multipart/form-data").status_code == 403


def test_empty_and_wide_files_give_a_message(client):
    r = client.post("/api/datasets", data={"file": (io.BytesIO(b"a,b\n"), "x.csv")}, content_type="multipart/form-data")
    assert r.status_code == 400 and "error" in r.get_json()
