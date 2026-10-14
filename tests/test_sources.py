import io
import sqlite3

import pytest

import sources
from app import create_app

CSV_V1 = "day,store,sales\n2026-01-01,A,10\n2026-01-02,B,12\n2026-01-03,A,14\n"
CSV_V2 = CSV_V1 + "2026-01-04,B,16\n"


@pytest.fixture()
def client(tmp_path):
    return create_app(db_path=str(tmp_path / "t.db")).test_client()


@pytest.fixture()
def web(monkeypatch):
    """A fake web: url -> file text. Replaces the real download."""
    pages = {"https://example.com/sales.csv": CSV_V1}

    def fake_fetch(url):
        if url not in pages:
            raise sources.SourceError("The link answered with an error (404).")
        return pages[url].encode(), url.rsplit("/", maxsplit=1)[-1]
    monkeypatch.setattr(sources, "fetch", fake_fetch)
    return pages


def open_link(client, url="https://example.com/sales.csv", hours=24):
    return client.post("/api/datasets/from-url", json={"url": url, "hours": hours})


def test_open_from_link_creates_dataset_with_source(client, web):
    r = open_link(client)
    assert r.status_code == 200
    ds = client.get(f"/api/datasets/{r.get_json()['id']}").get_json()
    assert ds["rows"] == 3 and ds["name"] == "sales"
    assert ds["source"]["url"] == "https://example.com/sales.csv" and ds["source"]["hours"] == 24
    assert ds["source"]["due"] is False
    run = client.get("/api/pipeline").get_json()["datasets"][0]
    assert run["source"]["url"].endswith("sales.csv") and run["filename"].endswith("sales.csv")


def test_refresh_replaces_rows_and_records_a_run(client, web):
    ds = open_link(client).get_json()["id"]
    web["https://example.com/sales.csv"] = CSV_V2
    r = client.post(f"/api/datasets/{ds}/refresh")
    assert r.get_json() == {"rows_before": 3, "rows": 4}
    assert client.get(f"/api/datasets/{ds}").get_json()["rows"] == 4
    steps = client.get("/api/pipeline").get_json()["datasets"][0]["steps"]
    assert "3 rows before, 4 now" in steps[-1]["detail"]


def test_refresh_refuses_changed_columns(client, web):
    ds = open_link(client).get_json()["id"]
    web["https://example.com/sales.csv"] = "day,store,revenue\n2026-01-01,A,10\n"
    r = client.post(f"/api/datasets/{ds}/refresh")
    assert r.status_code == 400 and "columns" in r.get_json()["error"]
    assert client.get(f"/api/datasets/{ds}").get_json()["rows"] == 3


def test_due_when_older_than_the_refresh_hours(client, web):
    ds = open_link(client, hours=1).get_json()["id"]
    c = sqlite3.connect(client.application.config["DB_PATH"])
    c.execute("UPDATE dataset_sources SET refreshed = refreshed - 7200")
    c.commit()
    c.close()
    assert client.get(f"/api/datasets/{ds}").get_json()["source"]["due"] is True
    client.post(f"/api/datasets/{ds}/refresh")
    assert client.get(f"/api/datasets/{ds}").get_json()["source"]["due"] is False


def test_never_refresh_is_never_due(client, web):
    ds = open_link(client, hours=0).get_json()["id"]
    assert client.get(f"/api/datasets/{ds}").get_json()["source"]["due"] is False


def test_bad_refresh_choice_and_unlinked_dataset(client, web):
    assert open_link(client, hours=5).status_code == 400
    plain = client.post("/api/datasets", data={"file": (io.BytesIO(b"a,b\n1,2\n"), "x.csv")}, content_type="multipart/form-data")
    r = client.post(f"/api/datasets/{plain.get_json()['id']}/refresh")
    assert r.status_code == 400 and "not opened from a link" in r.get_json()["error"]


def test_link_errors_are_friendly(client, web):
    r = open_link(client, url="https://example.com/missing.csv")
    assert r.status_code == 400 and "404" in r.get_json()["error"]


def test_delete_removes_the_source(client, web):
    ds = open_link(client).get_json()["id"]
    assert client.delete(f"/api/datasets/{ds}").status_code == 200
    assert client.post(f"/api/datasets/{ds}/refresh").status_code == 400


def test_read_only_site_refuses_links(tmp_path, web):
    ro = create_app(db_path=str(tmp_path / "r.db"), read_only=True).test_client()
    assert open_link(ro).status_code == 403


def test_source_tables_are_not_queryable(client, web):
    open_link(client)
    assert client.post("/api/sql/run", json={"sql": "SELECT * FROM dataset_sources"}).status_code == 400


# ---- the address checks that keep the server from being used against itself ----------
@pytest.mark.parametrize("url", ["", "ftp://example.com/a.csv", "file:///etc/passwd", "javascript:alert(1)",
                                 "https://user:pw@example.com/a.csv", "https://example.com:8080/a.csv", "//example.com/a.csv"])
def test_bad_links_are_refused(url):
    with pytest.raises(sources.SourceError):
        sources.check_url(url)


def test_good_link_is_split_up():
    assert sources.check_url("https://example.com/a/b.csv?x=1") == ("https", "example.com", 443, "/a/b.csv?x=1")


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "[::1]", "0.0.0.0"])
def test_private_addresses_are_refused(host):
    with pytest.raises(sources.SourceError):
        sources.fetch(f"http://{host}/data.csv")


def test_redirect_to_private_address_is_refused(monkeypatch):
    class Resp:
        status = 302

        @staticmethod
        def getheader(_name):
            return "http://169.254.169.254/latest/meta-data"

    class Conn:
        def __init__(self, *_a, **_k):
            pass

        def request(self, *_a, **_k):
            pass

        @staticmethod
        def getresponse():
            return Resp()

        @staticmethod
        def close():
            pass
    real = sources.public_address
    monkeypatch.setattr(sources, "public_address", lambda host: "93.184.216.34" if host == "example.com" else real(host))
    monkeypatch.setattr(sources, "PinnedHTTPS", Conn)
    monkeypatch.setattr(sources, "PinnedHTTP", Conn)
    with pytest.raises(sources.SourceError):
        sources.fetch("https://example.com/redirect")
