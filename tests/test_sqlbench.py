import io

import pytest

from app import create_app

CSV = ("Visit date,Ward,Patients,Wait (min)\n"
       + "".join(f"2026-01-{1 + d % 28:02d},{'A' if d % 2 else 'B'},{10 + d % 7},{30 + d % 5}\n" for d in range(60)))


@pytest.fixture()
def client(tmp_path):
    return create_app(db_path=str(tmp_path / "t.db")).test_client()


def upload(client, text=CSV, name="Ward Visits.csv"):
    r = client.post("/api/datasets", data={"file": (io.BytesIO(text.encode()), name)},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["id"]


def table_names(client):
    return [t["name"] for t in client.get("/api/sql/schema").get_json()["tables"] if t["name"] != "sales"]


def run(client, sql, **extra):
    return client.post("/api/sql/run", json={"sql": sql, **extra})


def test_schema_uses_readable_names(client):
    upload(client)
    tables = [t for t in client.get("/api/sql/schema").get_json()["tables"] if t["name"] != "sales"]
    assert [t["name"] for t in tables] == ["ward_visits"]
    assert [c["name"] for c in tables[0]["columns"]] == ["visit_date", "ward", "patients", "wait_min"]
    assert [c["type"] for c in tables[0]["columns"]] == ["date", "text", "number", "number"]


def test_runs_ordinary_sql(client):
    upload(client)
    out = run(client, "SELECT ward, COUNT(*) AS n, ROUND(AVG(wait_min), 1) AS wait "
                 "FROM ward_visits GROUP BY ward ORDER BY ward;").get_json()
    assert out["columns"] == ["ward", "n", "wait"]
    assert [r[:2] for r in out["rows"]] == [["A", 30], ["B", 30]]
    assert out["truncated"] is False


def test_views_do_not_linger_between_requests(client):
    upload(client)
    run(client, "SELECT 1")
    assert table_names(client) == ["ward_visits"]          # not ward_visits_2


def test_two_files_with_the_same_name_get_different_table_names(client):
    upload(client)
    upload(client)
    assert table_names(client) == ["ward_visits", "ward_visits_2"]


@pytest.mark.parametrize("sql", [
    "DROP TABLE ward_visits",
    "DELETE FROM ward_visits",
    "INSERT INTO ward_visits VALUES (1,2,3,4)",
    "UPDATE ward_visits SET ward = 'x'",
    "CREATE TABLE x (a)",
    "PRAGMA table_info(datasets)",
    "ATTACH DATABASE '/tmp/x.db' AS x",
    "SELECT * FROM sqlite_master",
    "SELECT * FROM datasets",
    "SELECT * FROM dashboards",
    "SELECT 1; SELECT 2",
    "DROP TABLE ds_1",
])
def test_nothing_but_select_on_your_tables(client, sql):
    upload(client)
    r = run(client, sql)
    assert r.status_code == 400, sql
    assert client.get("/api/datasets").get_json()["datasets"][0]["rows"] == 60      # data untouched


def test_runaway_query_is_stopped(client, monkeypatch):
    import sqlbench
    monkeypatch.setattr(sqlbench, "MAX_SECONDS", 0.2)
    upload(client)
    r = run(client, "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n")
    assert r.status_code == 400 and "longer than" in r.get_json()["error"]


def test_rows_are_capped(client):
    upload(client)
    out = run(client, "SELECT * FROM ward_visits", limit=10).get_json()
    assert len(out["rows"]) == 10 and out["truncated"] is True


def test_sales_table_is_available_when_there_is_sales_data(tmp_path):
    c = create_app(db_path=str(tmp_path / "s.db")).test_client()        # seeded sample sales
    names = [t["name"] for t in c.get("/api/sql/schema").get_json()["tables"]]
    assert names[0] == "sales"
    out = c.post("/api/sql/run", json={"sql": "SELECT COUNT(*) FROM sales"}).get_json()
    assert out["rows"][0][0] > 0


def test_read_only_demo_can_still_query(tmp_path):
    c = create_app(db_path=str(tmp_path / "r.db"), read_only=True).test_client()
    assert c.post("/api/sql/run", json={"sql": "SELECT COUNT(*) FROM sales"}).status_code == 200
    assert c.post("/api/sql/run", json={"sql": "DELETE FROM sales"}).status_code == 400


def test_empty_and_huge_queries(client):
    assert run(client, "  ;  ").status_code == 400
    assert run(client, "SELECT '" + "x" * 6000 + "'").status_code == 400
