"""Keeps each account's data between visits when the app runs on a host with no permanent disk.

The app stores everything in SQLite files (accounts.db and one shop_<id>.db per account). On a
host like Vercel those files vanish when the server restarts, so a copy of each file is kept in a
small file store in Supabase (Postgres) and brought back before a request needs it:

* before a request, the local file is refreshed if the stored copy is newer;
* after a request that changed a file, the new copy is stored.

Turned on only when RSD_STORE_URL, RSD_STORE_KEY and RSD_STORE_TOKEN are set. The token is the
secret that lets this app (and nothing else) call the store's functions; the key is Supabase's
public API key, which on its own can do nothing with the stored files.
"""
import base64
import hashlib
import json
import os
import urllib.error
import urllib.request
import zlib

TIMEOUT_SECONDS = 25


class StoreError(RuntimeError):
    pass


class SupabaseStore:
    """The remote side: four functions called over HTTPS (see the SQL in README)."""

    def __init__(self, url, key, token):
        self.url, self.key, self.token = url.rstrip("/"), key, token

    def _call(self, fn, **args):
        body = json.dumps({"p_token": self.token, **args}).encode()
        req = urllib.request.Request(f"{self.url}/rest/v1/rpc/{fn}", data=body, method="POST",
                                     headers={"apikey": self.key, "Authorization": f"Bearer {self.key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:   # nosec - fixed https endpoint
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise StoreError(f"Storage returned {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise StoreError("Storage is not reachable") from e
        return json.loads(raw) if raw else None

    def version(self, name):
        return self._call("rsd_version", p_name=name)

    def get(self, name):
        r = self._call("rsd_get", p_name=name)
        return (r["version"], zlib.decompress(base64.b64decode(r["data"]))) if r else (None, None)

    def put(self, name, data):
        return self._call("rsd_put", p_name=name, p_data=base64.b64encode(zlib.compress(data, 6)).decode())

    def delete(self, name):
        self._call("rsd_delete", p_name=name)


def from_env(env=None):
    env = os.environ if env is None else env
    url, key, token = ((env.get(name) or "").strip().strip("\"'") for name in
                       ("RSD_STORE_URL", "RSD_STORE_KEY", "RSD_STORE_TOKEN"))   # pasted values often carry spaces
    return Sync(SupabaseStore(url, key, token)) if url and key and token else None


class Sync:
    """Local file <-> stored copy. `known` remembers what this server process last saw."""

    def __init__(self, store):
        self.store = store
        self.known = {}                        # name -> (remote version, sha256 of the local bytes)

    @staticmethod
    def _digest(path):
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def ensure(self, name, path):
        """Make the local file match the stored copy. Returns False when nothing is stored yet."""
        remote = self.store.version(name)
        if remote is None:
            return False
        if os.path.exists(path) and self.known.get(name, (None,))[0] == remote:
            return True
        _, data = self.store.get(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        for suffix in ("-wal", "-shm", "-journal"):
            if os.path.exists(path + suffix):
                os.remove(path + suffix)
        self.known[name] = (remote, self._digest(path))
        return True

    def commit(self, name, path):
        """Store the local file if it changed since it was fetched or last stored."""
        if not os.path.exists(path):
            return False
        if name not in self.known and self.store.version(name) is not None:
            return False                       # never fetched here: our file may be stale, do not overwrite
        digest = self._digest(path)
        if self.known.get(name, (None, None))[1] == digest:
            return False
        with open(path, "rb") as f:
            self.known[name] = (self.store.put(name, f.read()), digest)
        return True

    def forget(self, name):
        self.known.pop(name, None)
        self.store.delete(name)
