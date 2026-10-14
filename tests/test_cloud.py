import io
import os

import pytest

import cloud
from app import create_app
from cloud import StoreError

ONE_ROW = "date,store,category,product,orders,units,revenue,cost\n2026-01-01,Kiosk,Snacks,Gum,1,1,2,1\n"


class MemoryStore:
    """Stands in for Supabase: name -> (version, bytes)."""

    def __init__(self):
        self.files, self.down = {}, False

    def _check(self):
        if self.down:
            raise StoreError("down")

    def version(self, name):
        self._check()
        return self.files.get(name, (None,))[0]

    def get(self, name):
        self._check()
        return self.files.get(name, (None, None))

    def put(self, name, data):
        self._check()
        v = self.files.get(name, (0,))[0] + 1
        self.files[name] = (v, data)
        return v

    def delete(self, name):
        self._check()
        self.files.pop(name, None)


def server(folder, store):
    """A fresh server process with an empty temporary disk, sharing one online store."""
    os.makedirs(folder, exist_ok=True)
    app = create_app(db_path=str(folder / "main.db"), consent=True, auth=True, sync=cloud.Sync(store))
    c = app.test_client()
    c.post("/api/consent", json={"accept": True})
    return c


def sign_up(c):
    return c.post("/api/auth/register", json={"email": "o@shop.com", "name": "O", "shop": "S",
                                               "password": "correct horse", "accept_policy": True})


def test_account_and_data_survive_a_server_restart(tmp_path):
    store = MemoryStore()
    first = server(tmp_path / "one", store)
    assert sign_up(first).status_code == 200
    up = first.post("/api/upload", data={"file": (io.BytesIO(ONE_ROW.encode()), "x.csv"), "mode": "replace"},
                    content_type="multipart/form-data")
    assert up.status_code == 200
    assert {"accounts.db", "shop_1.db"} <= set(store.files)

    second = server(tmp_path / "two", store)                      # new disk, nothing local
    login = second.post("/api/auth/login", json={"email": "o@shop.com", "password": "correct horse"})
    assert login.status_code == 200
    assert second.get("/api/meta").get_json()["rows"] == 1


def test_newer_copy_from_another_server_is_picked_up(tmp_path):
    store = MemoryStore()
    a, b = server(tmp_path / "a", store), server(tmp_path / "b", store)
    sign_up(a)
    b.post("/api/auth/login", json={"email": "o@shop.com", "password": "correct horse"})
    assert b.get("/api/meta").get_json()["rows"] == 0
    a.post("/api/upload", data={"file": (io.BytesIO(ONE_ROW.encode()), "x.csv"), "mode": "replace"},
           content_type="multipart/form-data")
    assert b.get("/api/meta").get_json()["rows"] == 1


def test_reads_do_not_write_and_failures_say_so(tmp_path):
    store = MemoryStore()
    c = server(tmp_path / "a", store)
    sign_up(c)
    versions = {k: v[0] for k, v in store.files.items()}
    c.get("/api/meta"), c.get("/api/kpis")
    assert {k: v[0] for k, v in store.files.items()} == versions
    store.down = True
    r = c.get("/api/meta")
    assert r.status_code == 503 and "try again" in r.get_json()["error"].lower()
    store.down = False
    assert c.get("/api/meta").status_code == 200


def test_failed_save_is_reported(tmp_path):
    store = MemoryStore()
    c = server(tmp_path / "a", store)
    sign_up(c)
    original_put = store.put

    def broken(*_a):
        raise StoreError("down")
    store.put = broken
    r = c.post("/api/upload", data={"file": (io.BytesIO(ONE_ROW.encode()), "x.csv"), "mode": "replace"},
               content_type="multipart/form-data")
    assert r.status_code == 503 and "could not be saved" in r.get_json()["error"]
    store.put = original_put


def test_delete_account_removes_stored_files(tmp_path):
    store = MemoryStore()
    c = server(tmp_path / "a", store)
    sign_up(c)
    assert c.post("/api/account/delete", json={"password": "correct horse"}).status_code == 200
    assert "shop_1.db" not in store.files


def test_only_turns_on_when_all_three_settings_exist():
    assert cloud.from_env({}) is None
    assert cloud.from_env({"RSD_STORE_URL": "https://x.supabase.co", "RSD_STORE_KEY": "k"}) is None
    assert cloud.from_env({"RSD_STORE_URL": "https://x.supabase.co", "RSD_STORE_KEY": "k", "RSD_STORE_TOKEN": "t"})


def test_a_fresh_server_never_overwrites_stored_data_with_its_empty_files(tmp_path):
    store = MemoryStore()
    first = server(tmp_path / "one", store)
    sign_up(first)
    before = {k: v for k, v in store.files.items()}
    server(tmp_path / "two", store)                               # only accepts cookies, no sign-in
    assert store.files == before


def test_status_reports_switched_on_features(tmp_path):
    from app import create_app
    plain = create_app(db_path=str(tmp_path / "t.db")).test_client()
    assert plain.get("/api/status").get_json() == {"online_storage": False, "sign_in": False, "read_only": False}


def test_settings_pasted_with_spaces_or_quotes_still_work():
    sync = cloud.from_env({"RSD_STORE_URL": " https://x.supabase.co/ \n", "RSD_STORE_KEY": "k ", "RSD_STORE_TOKEN": ' "tok"\n'})
    assert (sync.store.url, sync.store.key, sync.store.token) == ("https://x.supabase.co", "k", "tok")
    assert cloud.from_env({"RSD_STORE_URL": "u", "RSD_STORE_KEY": "k", "RSD_STORE_TOKEN": "  "}) is None


def test_explore_uploads_survive_sign_out_sign_in_and_a_restart(tmp_path):
    store = MemoryStore()
    first = server(tmp_path / "one", store)
    assert sign_up(first).status_code == 200
    up = first.post("/api/datasets", data={"file": (io.BytesIO(b"ward,wait\nA,10\nB,20\n"), "Ward Visits.csv")},
                    content_type="multipart/form-data")
    assert up.status_code == 200
    dataset_id = up.get_json()["id"]

    assert first.post("/api/auth/logout").status_code == 200
    login = first.post("/api/auth/login", json={"email": "o@shop.com", "password": "correct horse"})
    assert login.status_code == 200
    assert first.get(f"/api/datasets/{dataset_id}").get_json()["rows"] == 2

    second = server(tmp_path / "two", store)                      # new disk, nothing local
    second.post("/api/auth/login", json={"email": "o@shop.com", "password": "correct horse"})
    assert second.get(f"/api/datasets/{dataset_id}").get_json()["rows"] == 2
    run = second.post("/api/sql/run", json={"sql": "SELECT COUNT(*) AS n FROM ward_visits"})
    assert run.get_json()["rows"] == [[2]]
