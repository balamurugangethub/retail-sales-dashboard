"""Signed-in accounts have their own database; every page that reads uploads must work in it."""
import io
import sqlite3

import pytest

from app import create_app


@pytest.fixture()
def shop(tmp_path):
    client = create_app(db_path=str(tmp_path / "t.db"), auth=True, consent=False).test_client()
    r = client.post("/api/auth/register", json={"email": "a@b.co", "name": "A", "password": "password1", "accept_policy": True})
    assert r.status_code == 200
    return client, tmp_path


def upload(client, text="a,b\n1,x\n2,y\n3,x\n"):
    r = client.post("/api/datasets", data={"file": (io.BytesIO(text.encode()), "t.csv")}, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["id"]


def test_new_account_can_use_uploads_everywhere(shop):
    client, _ = shop
    assert client.get("/api/datasets").get_json()["datasets"] == []
    assert client.get("/api/sql/schema").status_code == 200
    assert client.get("/api/pipeline").get_json()["datasets"] == []
    ds = upload(client)
    assert client.get(f"/api/datasets/{ds}/quality").status_code == 200
    assert client.get("/api/pipeline").get_json()["datasets"][0]["id"] == ds
    assert client.post("/api/sql/run", json={"sql": "SELECT COUNT(*) AS n FROM t"}).get_json()["rows"] == [[3]]


def test_account_made_before_uploads_existed_is_upgraded(shop):
    client, tmp_path = shop
    path = next((tmp_path / "userdata").glob("shop_*.db"))
    con = sqlite3.connect(path)
    con.executescript("DROP TABLE IF EXISTS datasets;")
    con.close()
    assert client.get("/api/datasets").status_code == 200
    assert upload(client)
