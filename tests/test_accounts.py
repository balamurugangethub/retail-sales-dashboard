import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from accounts import safe_next  # noqa: E402
from app import create_app  # noqa: E402

ONE_ROW = "date,store,category,product,orders,units,revenue,cost\n2026-01-01,Kiosk,Snacks,Gum,1,1,2,1\n"


@pytest.fixture()
def make_client(tmp_path):
    app = create_app(db_path=str(tmp_path / "main.db"), consent=True, auth=True)
    return app.test_client


def accept(c):
    assert c.post("/api/consent", json={"accept": True}).status_code == 200
    return c


def register(c, email="owner@shop.com", password="correct horse", **extra):
    body = {"email": email, "name": "Owner", "shop": "Corner Shop", "password": password, "accept_policy": True}
    return c.post("/api/auth/register", json=dict(body, **extra))


def rows(c):
    return c.get("/api/meta").get_json()["rows"]


def test_consent_gate_blocks_until_accepted(make_client):
    c = make_client()
    r = c.get("/builder")
    assert r.status_code == 302 and r.headers["Location"] == "/welcome?next=/builder"
    assert c.get("/api/kpis").status_code == 401
    for open_path in ("/welcome", "/privacy", "/static/style.css"):
        assert c.get(open_path).status_code == 200
    me = c.get("/api/auth/me").get_json()
    assert me["consent"] is False and me["user"] is None

    declined = c.post("/api/consent", json={"accept": False})
    assert "rsd_consent=;" in declined.headers.get("Set-Cookie", "")      # cleared, nothing stored
    assert c.get("/").status_code == 302

    accept(c)
    assert c.get("/api/auth/me").get_json()["consent"] is True
    assert c.get("/").headers["Location"] == "/login?next=/"              # now it asks for sign-in


def test_register_validates_and_needs_policy(make_client):
    c = accept(make_client())
    assert "privacy policy" in register(c, accept_policy=False).get_json()["error"]
    assert "8 characters" in register(c, password="short").get_json()["error"]
    assert "valid email" in register(c, email="nope").get_json()["error"]
    r = register(c)
    assert r.status_code == 200 and r.get_json()["user"]["shop"] == "Corner Shop"
    assert r.get_json()["user"]["policy_version"]
    assert "already exists" in register(accept(make_client()), email="OWNER@shop.com").get_json()["error"]
    assert c.get("/").status_code == 200 and c.get("/account").status_code == 200


def test_each_account_has_its_own_data(make_client):
    a, b = accept(make_client()), accept(make_client())
    register(a, "a@shop.com"), register(b, "b@shop.com")
    start = rows(a)
    assert start > 1000 and rows(b) == start                                # both start with sample data
    up = a.post("/api/upload", data={"file": (io.BytesIO(ONE_ROW.encode()), "x.csv"), "mode": "replace"},
                content_type="multipart/form-data")
    assert up.status_code == 200
    assert rows(a) == 1 and rows(b) == start                                # B never sees A's change
    assert a.post("/api/dashboards", json={"name": "Mine", "spec": {"widgets": []}}).status_code in (200, 201)
    assert [d["name"] for d in a.get("/api/dashboards").get_json()] == ["Mine"]
    assert b.get("/api/dashboards").get_json() == []


def test_login_logout_and_lockout(make_client):
    register(accept(make_client()))
    c = accept(make_client())
    assert c.post("/api/auth/login", json={"email": "owner@shop.com", "password": "wrong pass"}).status_code == 401
    r = c.post("/api/auth/login", json={"email": "Owner@Shop.com", "password": "correct horse"})
    assert r.status_code == 200 and rows(c) > 1000
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/meta").status_code == 401

    for _ in range(5):
        c.post("/api/auth/login", json={"email": "owner@shop.com", "password": "guess guess"})
    locked = c.post("/api/auth/login", json={"email": "owner@shop.com", "password": "correct horse"})
    assert locked.status_code == 429


def test_delete_account_removes_everything(make_client, tmp_path):
    c = accept(make_client())
    register(c)
    assert os.path.exists(tmp_path / "userdata" / "shop_1.db")
    assert c.post("/api/account/delete", json={"password": "nope"}).status_code == 401
    assert c.post("/api/account/delete", json={"password": "correct horse"}).status_code == 200
    assert not os.path.exists(tmp_path / "userdata" / "shop_1.db")
    assert c.get("/api/meta").status_code == 401
    assert c.post("/api/auth/login", json={"email": "owner@shop.com", "password": "correct horse"}).status_code == 401


def test_read_only_demo_has_consent_but_no_accounts(tmp_path):
    c = create_app(db_path=str(tmp_path / "ro.db"), read_only=True, consent=True).test_client()
    assert c.get("/").status_code == 302
    assert c.post("/api/consent", json={"accept": False}).status_code == 200
    accept(c)
    assert c.get("/").status_code == 200 and c.get("/api/meta").status_code == 200
    me = c.get("/api/auth/me").get_json()
    assert me["auth_enabled"] is False and me["read_only"] is True
    assert c.post("/api/auth/register", json={}).status_code == 403


def test_safe_next_only_allows_local_paths():
    assert safe_next("/builder?x=1") == "/builder?x=1"
    for bad in ("https://evil.example", "//evil.example", "/\\evil", "", None):
        assert safe_next(bad) == "/"
