"""Synthetic till data for the 5-store chain: one CSV per day, one row per order line.

The default run writes two years (2024-07-01 to 2026-06-30) of files, about 1.0 million rows.
The data is seeded, so the same command always writes the same files. A few rows are dirty on
purpose, the way real till exports are: about 0.2% of lines are sent twice and 0.1% have no
payment method. The pipeline has to cope with both.

    python -m warehouse.generate                      # two years, about 1.0M rows
    python -m warehouse.generate --days 30            # a quick month
    python -m warehouse.generate --next 1             # one more day after the newest file
    python -m warehouse.generate --corrupt 2026-07-01 # a broken file, to see the pipeline stop
"""
import argparse
import bisect
import csv
import itertools
import math
import os
import random
from datetime import date, datetime, timedelta

from warehouse import config

DEFAULT_START = date(2024, 7, 1)
DEFAULT_DAYS = 730
# Orders per store on an average weekday, before season and growth (Store A is the busiest)
STORE_ORDERS = {"S01": 146, "S02": 119, "S03": 128, "S04": 99, "S05": 93}
MONTH_SEASON = {1: 0.92, 2: 0.88, 3: 0.95, 4: 0.97, 5: 1.0, 6: 1.02, 7: 0.98, 8: 1.0, 9: 1.01, 10: 1.08,
                11: 1.15, 12: 1.3}
WEEKDAY = [0.92, 0.9, 0.93, 0.97, 1.08, 1.25, 1.18]     # Mon..Sun
YEARLY_GROWTH = 0.08
PAYMENTS = (["card", "cash", "upi", "wallet"], [0.42, 0.18, 0.32, 0.08])
DISCOUNTS = ([0, 5, 10, 20], [0.8, 0.1, 0.07, 0.03])
QUANTITIES = ([1, 2, 3, 4, 6], [0.62, 0.24, 0.08, 0.04, 0.02])
DUPLICATE_RATE, BLANK_PAYMENT_RATE = 0.002, 0.001


def load_products(seeds_dir=config.SEEDS_DIR):
    with open(os.path.join(seeds_dir, "products.csv"), newline="", encoding="utf-8") as f:
        return [(r["sku"], float(r["list_price"]), float(r["popularity"])) for r in csv.DictReader(f)]


def poisson(rng, mean):
    """Knuth's method is slow for big means; a rounded normal is close enough above 30."""
    if mean > 30:
        return max(0, round(rng.gauss(mean, math.sqrt(mean))))
    limit, k, p = math.exp(-mean), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def day_rows(day, products, seed=7, scale=1.0):
    """All order lines for one day across the five stores, in till order."""
    rng = random.Random(f"{seed}-{day.isoformat()}")
    skus = [p[0] for p in products]
    prices = {p[0]: p[1] for p in products}
    cum = list(itertools.accumulate(p[2] for p in products))
    growth = (1 + YEARLY_GROWTH) ** ((day - DEFAULT_START).days / 365)
    factor = MONTH_SEASON[day.month] * WEEKDAY[day.weekday()] * growth * scale
    rows = []
    for store, base in STORE_ORDERS.items():
        orders = poisson(rng, base * factor)
        times = sorted(rng.randint(8 * 3600, 22 * 3600 - 1) for _ in range(orders))
        for seq, secs in enumerate(times, start=1):
            order_id = f"{store}-{day:%Y%m%d}-{seq:04d}"
            sold_at = (datetime.combine(day, datetime.min.time()) + timedelta(seconds=secs)).isoformat(sep=" ")
            payment = rng.choices(*PAYMENTS)[0]
            lines = 1 + min(11, int(rng.expovariate(1 / 1.6)))
            for line in range(1, lines + 1):
                sku = skus[bisect.bisect(cum, rng.random() * cum[-1])]
                rows.append([order_id, line, sold_at, store, sku, rng.choices(*QUANTITIES)[0],
                             f"{prices[sku]:.2f}", rng.choices(*DISCOUNTS)[0], payment])
    for row in rows:
        if rng.random() < BLANK_PAYMENT_RATE:
            row[8] = ""
    dupes = [list(r) for r in rows if rng.random() < DUPLICATE_RATE]
    for d in dupes:
        rows.insert(rng.randrange(len(rows) + 1), d)
    return rows


def corrupt_rows(rows, rng):
    """A broken export: a third of the products are missing and a quarter of the lines are repeated."""
    for row in rows:
        if rng.random() < 0.35:
            row[4] = ""
    return rows + [list(r) for r in rows[: len(rows) // 4]]


def write_day(day, out_dir, products, seed=7, scale=1.0, corrupt=False):
    rows = day_rows(day, products, seed, scale)
    if corrupt:
        rows = corrupt_rows(rows, random.Random(seed))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, config.file_name(day))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(config.COLUMNS)
        w.writerows(rows)
    return path, len(rows)


def landed_days(out_dir):
    days = []
    for name in os.listdir(out_dir) if os.path.isdir(out_dir) else []:
        if name.startswith(config.FILE_PREFIX) and name.endswith(".csv"):
            try:
                days.append(date.fromisoformat(name[len(config.FILE_PREFIX):-4]))
            except ValueError:
                pass
    return sorted(days)


def generate(start=DEFAULT_START, days=DEFAULT_DAYS, out_dir=config.LANDING_DIR, seed=7, scale=1.0):
    products = load_products()
    total = 0
    for i in range(days):
        _, n = write_day(start + timedelta(days=i), out_dir, products, seed, scale)
        total += n
    return total


def main(argv=None):
    p = argparse.ArgumentParser(description="Write synthetic daily till files for the 5 stores.")
    p.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START, help="first day (YYYY-MM-DD)")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS, help="how many daily files to write")
    p.add_argument("--next", type=int, metavar="N", help="write N more days after the newest file")
    p.add_argument("--corrupt", type=date.fromisoformat, metavar="DAY", help="write one broken file for DAY")
    p.add_argument("--out", default=config.LANDING_DIR, help="folder for the files")
    p.add_argument("--scale", type=float, default=1.0, help="multiply the number of orders")
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args(argv)
    if a.corrupt:
        path, n = write_day(a.corrupt, a.out, load_products(), a.seed, a.scale, corrupt=True)
        print(f"Wrote a broken file with {n:,} rows: {path}")
        return
    start, days = a.start, a.days
    if a.next:
        have = landed_days(a.out)
        start, days = (max(have) + timedelta(days=1) if have else DEFAULT_START), a.next
    total = generate(start, days, a.out, a.seed, a.scale)
    print(f"Wrote {days:,} daily files from {start} with {total:,} rows to {a.out}")


if __name__ == "__main__":
    main()
