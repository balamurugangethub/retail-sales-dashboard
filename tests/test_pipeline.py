import io

import pytest

from app import create_app


@pytest.fixture()
def client(tmp_path):
    return create_app(db_path=str(tmp_path / "t.db")).test_client()


def upload(client, text, name="visits.csv"):
    r = client.post("/api/datasets", data={"file": (io.BytesIO(text.encode()), name)}, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    return r.get_json()["id"]


def test_empty_workspace(client):
    assert client.get("/api/pipeline").get_json() == {"datasets": []}
    assert client.get("/pipeline").status_code == 200


def steps_of(client):
    return client.get("/api/pipeline").get_json()["datasets"][0]["steps"]


def test_upload_records_each_step(client):
    ds = upload(client, "day,ward,n\n2026-01-01,A,5\n2026-01-02,B,6\n2026-01-03,A,7\n")
    run = client.get("/api/pipeline").get_json()["datasets"][0]
    assert run["id"] == ds and run["filename"] == "visits.csv"
    assert [s["step"] for s in run["steps"]] == ["Read", "Detect types", "Clean", "Load"]
    assert all(s["rows"] == 3 for s in run["steps"])
    assert run["steps"][1]["detail"] == "columns: 1 number, 1 date, 1 text"
    assert 0 <= run["quality"]["score"] <= 100


def test_clean_step_says_when_everything_was_readable(client):
    upload(client, "n,label\n1,a\n2,b\n3,c\n4,d\n")
    assert "every value was readable" in steps_of(client)[2]["detail"]


def test_clean_step_counts_values_it_could_not_read(client):
    upload(client, "day,n\n" + "".join(f"2026-01-{d:02d},{d}\n" for d in range(1, 21)) + "2026-01-21,oops\n")
    detail = steps_of(client)[2]["detail"]
    assert "1 values could not be read" in detail


def test_delete_removes_the_run(client):
    ds = upload(client, "a,b\n1,2\n3,4\n")
    assert client.delete(f"/api/datasets/{ds}").status_code == 200
    assert client.get("/api/pipeline").get_json()["datasets"] == []


def test_run_history_table_is_not_queryable_in_sql(client):
    upload(client, "a,b\n1,2\n3,4\n")
    assert client.post("/api/sql/run", json={"sql": "SELECT * FROM pipeline_runs"}).status_code == 400
