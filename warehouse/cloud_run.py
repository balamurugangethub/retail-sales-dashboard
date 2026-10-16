"""The pipeline in the cloud: what the scheduled GitHub Actions workflow runs every morning.

    land -> sync -> validate -> load raw -> dbt build -> source freshness -> publish site

1. Land: the tills' files for every day up to yesterday (UTC) that are not in the landing zone yet,
   and the real Online Retail II dataset the first time, are uploaded to the object store.
2. Sync: files in the landing zone that are not in the warehouse yet are downloaded to the runner.
3. The same run_pipeline as on a laptop validates, loads and runs dbt, recording the run.
   Rejected files are moved to the bucket's quarantine/ folder.
4. Publish: the reporting marts and the run history go to site/data/ and the dbt docs (the data
   catalog, with lineage) to site/index.html, which the workflow deploys to GitHub Pages.

Where things live is set by environment variables, so the same code runs with or without accounts:

    MOTHERDUCK_TOKEN               set: the warehouse is MotherDuck (md:retail_warehouse, free plan)
                                   not set: a DuckDB file, kept between runs by the workflow's cache
    SUPABASE_URL, SUPABASE_SECRET_KEY   set: the landing zone is a Supabase Storage bucket
                                   not set: the landing folder, kept by the workflow's cache

    python -m warehouse.cloud_run --site site
"""
import argparse
import logging
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

from warehouse import alerts, config, feeds, generate, load, object_store, online_retail, run, snapshot, transform

log = logging.getLogger("warehouse.cloud")


def warehouse_target():
    if os.environ.get("MOTHERDUCK_TOKEN", "").strip():
        return "md:" + (os.environ.get("MOTHERDUCK_DATABASE", "").strip() or "retail_warehouse")
    return config.WAREHOUSE_PATH


def yesterday():
    return datetime.now(timezone.utc).date() - timedelta(days=1)


def missing_till_days(have, until, start=generate.DEFAULT_START):
    """Every day from start to until that has no till file yet, landed or quarantined."""
    days = {feeds.POS.day_of(n) for n in have if feeds.POS.owns(n)}
    return [start + timedelta(days=i) for i in range((until - start).days + 1) if start + timedelta(days=i) not in days]


def land(store, until=None, with_public=True, since=generate.DEFAULT_START, public_file=None):
    """Puts new files in the landing zone. Returns {feed: files landed}."""
    have = set(store.names(object_store.LANDING)) | set(store.names(object_store.QUARANTINE))
    landed = {"pos": 0, "online_retail": 0}
    with tempfile.TemporaryDirectory() as tmp:
        out = store.landing_dir if isinstance(store, object_store.LocalFolder) else tmp
        products = generate.load_products()
        paths = [generate.write_day(day, out, products)[0] for day in missing_till_days(have, until or yesterday(), since)]
        landed["pos"] = store.upload(paths) if paths else 0
        if with_public and not any(feeds.ONLINE_RETAIL.owns(n) for n in have):
            try:
                public = os.path.join(tmp, "online_retail")
                online_retail.land(public, public_file)
                files = sorted(os.path.join(public, n) for n in os.listdir(public))
                landed["online_retail"] = store.upload(files)
            except Exception as e:  # pylint: disable=broad-exception-caught
                # the tills still load; the public dataset is tried again on the next run
                alerts.send("Could not land the Online Retail II dataset", f"{type(e).__name__}: {e}")
    return landed


def sync(store, warehouse_path, landing_dir):
    """Downloads landing-zone files that the warehouse has not loaded and the runner does not have."""
    if isinstance(store, object_store.LocalFolder):
        return 0
    con = load.connect(warehouse_path)
    try:
        done = load.loaded_files(con)
    finally:
        con.close()
    local = set(os.listdir(landing_dir)) if os.path.isdir(landing_dir) else set()
    wanted = [n for n in store.names(object_store.LANDING) if feeds.feed_for(n) and n not in done and n not in local]
    return store.download(wanted, landing_dir)


def quarantine(store, landing_dir):
    folder = os.path.join(landing_dir, "quarantine")
    names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
    in_bucket = set(store.names(object_store.LANDING)) if names and not isinstance(store, object_store.LocalFolder) else set()
    return store.quarantine([n for n in names if n in in_bucket])


def publish(warehouse_path, site_dir):
    """The data catalog (dbt docs) and the marts + run history the hosted Warehouse page reads."""
    os.makedirs(site_dir, exist_ok=True)
    manifest = snapshot.export(warehouse_path, os.path.join(site_dir, "data"))
    shutil.copy(transform.docs(warehouse_path), os.path.join(site_dir, "index.html"))
    with open(os.path.join(site_dir, ".nojekyll"), "w", encoding="utf-8"):
        pass                                 # serve files starting with _ as they are
    return manifest


def main(argv=None):
    p = argparse.ArgumentParser(description="Land, load and publish the warehouse in the cloud.")
    p.add_argument("--site", default=os.path.join(config.DATA_DIR, "site"), help="folder for the GitHub Pages site")
    p.add_argument("--until", type=date.fromisoformat, help="land till files up to this day (default yesterday, UTC)")
    p.add_argument("--since", type=date.fromisoformat, default=generate.DEFAULT_START,
                   help="first day of till history to land (default 2024-07-01)")
    p.add_argument("--no-public", action="store_true", help="don't land the Online Retail II dataset")
    p.add_argument("--public-file", help="use this Online Retail II .xlsx or .zip instead of downloading it")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    warehouse_path, landing_dir = warehouse_target(), config.LANDING_DIR
    store = object_store.from_env(landing_dir)
    print(f"Warehouse: {'MotherDuck' if config.is_motherduck(warehouse_path) else 'DuckDB file'}; "
          f"landing zone: {store.kind}")
    failed = None
    try:
        store.ensure()
        landed = land(store, a.until, not a.no_public, a.since, a.public_file)
        print(f"Landed {landed['pos']} till file(s) and {landed['online_retail']} Online Retail II file(s)")
        print(f"Downloaded {sync(store, warehouse_path, landing_dir)} file(s) from the landing zone")
        summary = run.run_pipeline(warehouse_path, landing_dir, with_snapshot=False)
        print(f"Loaded {summary['files']} file(s), {summary['rows']:,} rows; freshness {summary['freshness']}")
    except Exception as e:  # pylint: disable=broad-exception-caught
        failed = e
        title, details = run.describe_failure(e)
        alerts.send(title, details)
        print(f"FAILED: {title}\n{details}", file=sys.stderr)
    finally:
        try:
            quarantine(store, landing_dir)
        except object_store.StoreError as e:
            log.error("Could not move rejected files to quarantine: %s", e)
    try:                                     # publish even after a failure, so the failed run is visible
        manifest = publish(warehouse_path, a.site)
        print(f"Published the catalog and marts to {a.site} ({manifest['files_loaded']} till files, "
              f"{manifest['online_retail']['files_loaded']} online files)")
    except Exception as e:  # pylint: disable=broad-exception-caught
        alerts.send("Could not publish the data catalog", f"{type(e).__name__}: {e}")
        print(f"Publishing failed: {e}", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
