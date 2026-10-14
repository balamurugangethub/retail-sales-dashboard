import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

import accounts
from app import create_app


def make(tmp_path):
    return create_app(db_path=str(tmp_path / "t.db"), auth=True, consent=False).test_client()


@pytest.fixture()
def client(tmp_path):
    return make(tmp_path)


def test_demo_gives_a_full_workspace(client):
    assert client.get("/api/auth/me").get_json()["demo_available"] is True
    r = client.post("/api/auth/demo")
    assert r.status_code == 200 and r.get_json()["user"]["demo"] is True
    assert client.get("/api/meta").get_json()["rows"] > 1000
    names = [d["name"] for d in client.get("/api/datasets").get_json()["datasets"]]
    assert sorted(names) == ["Hospital visits", "Retail chain", "School results"]
    assert len(client.get("/api/pipeline").get_json()["datasets"]) == 3
    tables = [t["name"] for t in client.get("/api/sql/schema").get_json()["tables"]]
    assert "hospital_visits" in tables and "sales" in tables


def test_each_demo_is_separate(tmp_path):
    a, b = make(tmp_path), make(tmp_path)
    a.post("/api/auth/demo")
    b.post("/api/auth/demo")
    ds = a.get("/api/datasets").get_json()["datasets"][0]["id"]
    assert a.delete(f"/api/datasets/{ds}").status_code == 200
    assert len(a.get("/api/datasets").get_json()["datasets"]) == 2
    assert len(b.get("/api/datasets").get_json()["datasets"]) == 3


def test_demo_account_can_be_deleted_without_a_password(client):
    client.post("/api/auth/demo")
    assert client.post("/api/account/delete", json={}).status_code == 200
    assert client.get("/api/meta").status_code == 401


def test_a_normal_account_still_needs_its_password(client):
    client.post("/api/auth/register", json={"email": "a@b.co", "name": "A", "password": "password1", "accept_policy": True})
    assert client.get("/api/auth/me").get_json()["user"]["demo"] is False
    assert client.post("/api/account/delete", json={}).status_code == 401


def test_old_demo_accounts_are_removed(tmp_path):
    client = make(tmp_path)
    client.post("/api/auth/demo")
    folder = tmp_path / "userdata"
    old = (datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=accounts.DEMO_HOURS + 1)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(folder / "accounts.db") as c:
        c.execute("UPDATE users SET created = ?", (old,))
    assert len(list(folder.glob("shop_*.db"))) == 1
    make(tmp_path).post("/api/auth/demo")
    with sqlite3.connect(folder / "accounts.db") as c:
        assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert len(list(folder.glob("shop_*.db"))) == 1


def test_demo_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(accounts, "DEMO_PER_HOUR", 2)
    codes = [client.post("/api/auth/demo").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_no_demo_without_sign_in_feature(tmp_path):
    plain = create_app(db_path=str(tmp_path / "p.db")).test_client()
    assert plain.post("/api/auth/demo").status_code == 400


def test_old_accounts_database_gets_the_demo_column(tmp_path):
    folder = tmp_path / "userdata"
    folder.mkdir()
    with sqlite3.connect(folder / "accounts.db") as c:
        c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL,"
                  " shop TEXT NOT NULL DEFAULT '', password_hash TEXT NOT NULL, created TEXT NOT NULL DEFAULT (datetime('now')),"
                  " policy_version TEXT NOT NULL, policy_accepted_at TEXT NOT NULL DEFAULT (datetime('now')))")
    client = make(tmp_path)
    assert client.post("/api/auth/demo").status_code == 200
