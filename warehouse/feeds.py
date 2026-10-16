"""The two daily feeds the warehouse loads, and the rules each file must pass.

* pos: the 5-store chain's till files (synthetic, warehouse/generate.py), one file per day.
* online_retail: a real public dataset, UCI "Online Retail II" (a UK online gift shop, Dec 2009 to
  Dec 2011, about 1.07 million invoice lines, CC BY 4.0), split into one file per day by
  warehouse/online_retail.py so it arrives and is checked exactly like the till files.

A file's feed is decided by its name prefix, so both land in the same folder (or bucket).
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

from warehouse import config


def as_number(value):
    try:
        return float(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class Feed:  # pylint: disable=too-many-instance-attributes
    name: str
    title: str
    prefix: str
    table: str                       # raw table, in the raw schema
    columns: list
    column_types: dict
    date_column: str                 # every row must be dated on the file's day
    min_rows: int
    min_score: int
    quality_skip: tuple = ()         # optional columns left out of the quality score
    fail_on_high: tuple = ()         # high-severity quality problems that reject the file; () means all
    rules: Optional[Callable] = field(default=None, compare=False)   # business rules: (rows, idx) -> [failure]

    def file_name(self, day):
        return f"{self.prefix}{day.isoformat()}.csv"

    def owns(self, name):
        return name.startswith(self.prefix) and name.endswith(".csv")

    def day_of(self, name):
        return date.fromisoformat(name[len(self.prefix):-4])


KNOWN_STORES = {"S01", "S02", "S03", "S04", "S05"}


def pos_rules(rows, idx):
    unknown_store = sum(1 for r in rows if r[idx["store_id"]].strip().upper() not in KNOWN_STORES)
    negative = sum(1 for r in rows if (as_number(r[idx["quantity"]]) or 0) < 0
                   or (as_number(r[idx["unit_price"]]) or 0) < 0)
    return [f"{n:,} {what}" for n, what in ((unknown_store, "rows have an unknown store"),
                                           (negative, "rows have a negative quantity or price")) if n]


# Real data is messier than the tills, and some of it is meant to be: cancelled invoices start with "C"
# and carry negative quantities, and "B" lines are bad-debt adjustments with a negative price. Anything
# else that is negative, or a missing country, counts against the file.
MAX_ODD_ROWS_PCT = 1.0


def online_retail_rules(rows, idx):
    failures = []
    odd = 0
    for r in rows:
        qty, price = as_number(r[idx["quantity"]]) or 0, as_number(r[idx["unit_price"]]) or 0
        cancelled = r[idx["invoice"]].strip().upper().startswith("C")
        if (qty < 0 < price and not cancelled) or (price < 0 and r[idx["stock_code"]].strip().upper() != "B"):
            odd += 1
    if rows and odd * 100 / len(rows) > MAX_ODD_ROWS_PCT:
        failures.append(f"{odd:,} rows have a negative quantity or price that is not a cancellation or adjustment")
    no_country = sum(1 for r in rows if not r[idx["country"]].strip())
    if no_country:
        failures.append(f"{no_country:,} rows have no country")
    return failures


POS = Feed(
    name="pos", title="Store tills (synthetic)", prefix=config.FILE_PREFIX, table="sales_lines",
    columns=config.COLUMNS, column_types=config.COLUMN_TYPES, date_column="sold_at",
    min_rows=config.MIN_ROWS, min_score=config.MIN_QUALITY_SCORE, rules=pos_rules)

ONLINE_RETAIL = Feed(
    name="online_retail", title="UCI Online Retail II (real)", prefix="online_retail_", table="online_retail_lines",
    columns=["invoice", "stock_code", "description", "quantity", "invoice_date", "unit_price", "customer_id", "country"],
    column_types={"invoice": "text", "stock_code": "text", "description": "text", "quantity": "number",
                  "invoice_date": "date", "unit_price": "number", "customer_id": "text", "country": "text"},
    date_column="invoice_date", min_rows=5, min_score=70,
    # guest checkouts have no customer id and some lines have no description; both are expected
    quality_skip=("customer_id", "description"),
    # a shop that sells the same item twice on one invoice repeats the line, so repeats are cleaned in staging
    fail_on_high=("missing",),
    rules=online_retail_rules)

FEEDS = [POS, ONLINE_RETAIL]


def feed_for(name):
    for feed in FEEDS:
        if feed.owns(name):
            return feed
    return None
