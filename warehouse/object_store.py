"""Where the daily files land: a local folder, or a bucket in Supabase Storage (free, no card needed).

The cloud run (cloud_run.py) treats the bucket as the landing zone, the way S3 or GCS would be used:

    <bucket>/landing/<file>.csv       new and loaded files
    <bucket>/quarantine/<file>.csv    files that failed validation

Supabase Storage is called over its REST API with the project's secret key, using only the standard
library. Set SUPABASE_URL (https://<project>.supabase.co), SUPABASE_SECRET_KEY (Project Settings >
API Keys > Secret keys) and optionally SUPABASE_BUCKET (default warehouse-landing).
"""
import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

LANDING, QUARANTINE = "landing", "quarantine"
PAGE = 1000
WORKERS = 8
TIMEOUT = 60


class StoreError(RuntimeError):
    pass


class LocalFolder:
    """The landing folder itself; files stay where they are. Used when no bucket is configured."""

    kind = "local folder"

    def __init__(self, landing_dir):
        self.landing_dir = landing_dir
        self.quarantine_dir = os.path.join(landing_dir, "quarantine")

    def ensure(self):
        os.makedirs(self.landing_dir, exist_ok=True)

    def names(self, area=LANDING):
        folder = self.landing_dir if area == LANDING else self.quarantine_dir
        return sorted(n for n in os.listdir(folder) if n.endswith(".csv")) if os.path.isdir(folder) else []

    def upload(self, paths):
        for path in paths:
            if os.path.dirname(os.path.abspath(path)) != os.path.abspath(self.landing_dir):
                shutil.copy(path, self.landing_dir)
        return len(paths)

    def download(self, names, out_dir):  # pylint: disable=unused-argument
        return 0                    # already in the landing folder

    def quarantine(self, names):  # pylint: disable=unused-argument
        return 0                    # load.reject already moved them


class SupabaseBucket:
    kind = "Supabase Storage bucket"

    def __init__(self, url, key, bucket="warehouse-landing"):
        self.url, self.key, self.bucket = url.rstrip("/"), key.strip(), bucket

    def headers(self, extra=None):
        h = {"apikey": self.key, "User-Agent": "retail-warehouse"}
        if self.key.startswith("eyJ"):               # a legacy service_role JWT also goes in Authorization
            h["Authorization"] = f"Bearer {self.key}"
        h.update(extra or {})
        return h

    def _call(self, method, path, body=None, content_type="application/json", ok=(200,)):
        data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        req = urllib.request.Request(f"{self.url}/storage/v1/{path}", data=data, method=method,
                                     headers=self.headers({"Content-Type": content_type} if data is not None else None))
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:   # nosec - the project's own https URL
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            text = e.read()[:300].decode(errors="replace")
            if e.code in ok:
                return e.code, text.encode()
            raise StoreError(f"Supabase Storage {method} {path.split('?')[0]} returned {e.code}: {text}") from e
        except (urllib.error.URLError, OSError) as e:
            raise StoreError(f"Supabase Storage is not reachable: {e}") from e

    def ensure(self):
        """Creates the private bucket the first time. An existing bucket is fine."""
        status, text = self._call("POST", "bucket", {"id": self.bucket, "name": self.bucket, "public": False},
                                  ok=(200, 400, 409))
        if status == 400 and b"exist" not in text.lower() and b"duplicate" not in text.lower():
            raise StoreError(f"Could not create bucket {self.bucket}: {text.decode(errors='replace')}")

    def names(self, area=LANDING):
        names, offset = [], 0
        while True:
            _, raw = self._call("POST", f"object/list/{self.bucket}",
                                {"prefix": area, "limit": PAGE, "offset": offset, "sortBy": {"column": "name", "order": "asc"}})
            page = json.loads(raw or b"[]")
            names += [o["name"] for o in page if o.get("name", "").endswith(".csv")]
            if len(page) < PAGE:
                return sorted(names)
            offset += PAGE

    def key_for(self, area, name):
        return f"{area}/{urllib.parse.quote(name)}"

    def _upload_one(self, path):
        with open(path, "rb") as f:
            data = f.read()
        key = self.key_for(LANDING, os.path.basename(path))
        req = urllib.request.Request(f"{self.url}/storage/v1/object/{self.bucket}/{key}", data=data, method="POST",
                                     headers=self.headers({"Content-Type": "text/csv", "x-upsert": "true"}))
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT):   # nosec - the project's own https URL
                return 1
        except urllib.error.HTTPError as e:
            raise StoreError(f"Upload of {os.path.basename(path)} returned {e.code}: {e.read()[:200]!r}") from e

    def upload(self, paths):
        with ThreadPoolExecutor(WORKERS) as pool:
            return sum(pool.map(self._upload_one, paths))

    def _download_one(self, job):
        name, out_dir = job
        _, data = self._call("GET", f"object/{self.bucket}/{self.key_for(LANDING, name)}")
        tmp = os.path.join(out_dir, name + ".part")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, os.path.join(out_dir, name))
        return 1

    def download(self, names, out_dir):
        os.makedirs(out_dir, exist_ok=True)
        with ThreadPoolExecutor(WORKERS) as pool:
            return sum(pool.map(self._download_one, [(n, out_dir) for n in names]))

    def quarantine(self, names):
        for name in names:
            self._call("POST", "object/move", {"bucketId": self.bucket, "sourceKey": f"{LANDING}/{name}",
                                               "destinationKey": f"{QUARANTINE}/{name}"})
        return len(names)


def from_env(landing_dir):
    url, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    if url and key:
        return SupabaseBucket(url, key, os.environ.get("SUPABASE_BUCKET", "").strip() or "warehouse-landing")
    return LocalFolder(landing_dir)
