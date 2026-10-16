"""Paths and settings shared by every pipeline step. Each can be overridden with an environment variable."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

DATA_DIR = os.environ.get("WAREHOUSE_DATA_DIR", os.path.join(HERE, "data"))
LANDING_DIR = os.environ.get("WAREHOUSE_LANDING_DIR", os.path.join(DATA_DIR, "landing"))
QUARANTINE_DIR = os.path.join(LANDING_DIR, "quarantine")
WAREHOUSE_PATH = os.environ.get("WAREHOUSE_PATH", os.path.join(DATA_DIR, "warehouse.duckdb"))
ALERT_LOG = os.path.join(DATA_DIR, "alerts.log")
DBT_DIR = os.path.join(HERE, "dbt")
SEEDS_DIR = os.path.join(DBT_DIR, "seeds")
SNAPSHOT_DIR = os.path.join(HERE, "snapshot")

# A daily file is one CSV per day for all five stores, one row per order line
FILE_PREFIX = "sales_"
COLUMNS = ["order_id", "line_number", "sold_at", "store_id", "sku", "quantity", "unit_price", "discount_pct",
           "payment_method"]
COLUMN_TYPES = {"order_id": "text", "line_number": "number", "sold_at": "date", "store_id": "text", "sku": "text",
                "quantity": "number", "unit_price": "number", "discount_pct": "number", "payment_method": "text"}

# Validation thresholds: a file below these stops the run and sends an alert
MIN_ROWS = int(os.environ.get("WAREHOUSE_MIN_ROWS", "50"))
MIN_QUALITY_SCORE = int(os.environ.get("WAREHOUSE_MIN_QUALITY_SCORE", "85"))
MAX_BAD_VALUE_PCT = 0.5


def file_name(day):
    return f"{FILE_PREFIX}{day.isoformat()}.csv"


def is_motherduck(path):
    """A MotherDuck cloud database is named like md:retail_warehouse instead of a file path."""
    return str(path or "").startswith("md:")


def share_motherduck_token():
    """DuckDB's MotherDuck extension reads motherduck_token; GitHub secrets are usually upper case."""
    token = os.environ.get("MOTHERDUCK_TOKEN", "").strip()
    if token and not os.environ.get("motherduck_token"):
        os.environ["motherduck_token"] = token
