import io

import pytest

import quality
from app import create_app


@pytest.fixture()
def client(tmp_path):
    return create_app(db_path=str(tmp_path / "t.db")).test_client()


def upload(client, text, name="t.csv"):
    r = client.post("/api/datasets", data={"file": (io.BytesIO(text.encode()), name)}, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    return r.get_json()["id"]


def report(client, text):
    return client.get(f"/api/datasets/{upload(client, text)}/quality").get_json()


def kinds(rep):
    return {(i["kind"], i["column"]) for i in rep["issues"]}


def test_clean_file_scores_high(client):
    rep = report(client, "store,sales\nA,10\nB,12\nC,11\nD,13\nE,12\nF,11\nG,10\nH,12\nI,13\nJ,11\n")
    assert rep["score"] >= 95 and rep["grade"] == "good"
    assert rep["issues"] == []


def test_missing_values_are_reported(client):
    rep = report(client, "store,sales\nA,10\nB,\nC,\nD,13\n")
    assert ("missing", "sales") in kinds(rep)
    assert rep["completeness_pct"] == 75.0
    assert next(i for i in rep["issues"] if i["kind"] == "missing")["severity"] == "high"


def test_duplicate_rows_are_counted(client):
    rep = report(client, "a,b\n1,x\n1,x\n1,x\n2,y\n")
    assert rep["duplicate_rows"] == 2
    assert ("duplicates", None) in kinds(rep)


def test_outlier_is_flagged(client):
    body = "id,amount\n" + "".join(f"{i},{100 + i % 5}\n" for i in range(20)) + "99,100000\n"
    rep = report(client, body)
    assert ("outliers", "amount") in kinds(rep)


def test_spelling_variants(client):
    rep = report(client, "store,sales\nStore A,1\nstore a,2\nSTORE A,3\nStore B,4\n")
    assert ("variants", "store") in kinds(rep)


def test_constant_column(client):
    rep = report(client, "country,n\nUK,1\nUK,2\nUK,3\n")
    assert ("constant", "country") in kinds(rep)


def test_future_dates(client):
    rep = report(client, "day,n\n2020-01-01,1\n2999-01-01,2\n")
    assert ("future", "day") in kinds(rep)


def test_score_is_bounded_and_worse_for_messy_data(client):
    clean = report(client, "a,b\n1,x\n2,y\n3,z\n4,w\n")
    messy = report(client, "a,b\n1,x\n1,x\n,\n,\n")
    assert 0 <= messy["score"] < clean["score"] <= 100


def test_unknown_dataset_is_404(client):
    assert client.get("/api/datasets/999/quality").status_code in (400, 404)


def test_quality_module_handles_few_numbers():
    assert quality.pct(1, 0) == 0.0
