"""Generates realistic sample sales data for the 5-store retail chain.

Jan-Jun 2026 is calibrated to the numbers used in the presentation (read from
data/targets.csv): sales $482K, profit $96K, 12,340 orders, stores A-E =
120/98/105/82/77 ($K). Earlier months (Jan-Dec 2025) are generated so that the
previous 6 months show +8.4% sales, +3.1% profit and -1.2% orders.
"""
import csv
import calendar
import os
import random
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))

STORES = ["Store A", "Store B", "Store C", "Store D", "Store E"]
# category -> [(product, relative price/popularity weight)]
CATALOG = {
    "Grocery": [("Basmati Rice 5kg", 3.0), ("Cooking Oil 1L", 2.4), ("Fresh Milk 2L", 4.0)],
    "Electronics": [("Wireless Earbuds", 1.0), ("Phone Charger", 1.6), ("Smart Speaker", 0.5)],
    "Apparel": [("Cotton T-Shirt", 2.0), ("Denim Jeans", 1.2), ("Running Shoes", 0.8)],
    "Home": [("LED Bulb Pack", 2.2), ("Bath Towel Set", 1.1), ("Cookware Set", 0.7)],
    "Beauty": [("Face Wash", 2.0), ("Shampoo 400ml", 2.5), ("Moisturiser", 1.4)],
}
CATEGORY_MARGIN = {"Grocery": 0.9, "Electronics": 1.1, "Apparel": 0.95, "Home": 1.0, "Beauty": 0.9}
PRODUCTS = [(c, p, w) for c, items in CATALOG.items() for p, w in items]
# higher = more revenue per order (used as a weight only; totals are calibrated)
TICKET = {"Grocery": 0.7, "Electronics": 2.6, "Apparel": 1.4, "Home": 1.1, "Beauty": 0.8}

H1_2026 = [(2026, m) for m in range(1, 7)]
H2_2025 = [(2025, m) for m in range(7, 13)]
H1_2025 = [(2025, m) for m in range(1, 7)]
H2_SHAPE = [0.90, 0.94, 1.00, 1.04, 1.12, 1.20]   # Jul..Dec seasonality
H1_2025_FACTOR = 0.86


def largest_remainder(total, weights):
    """Split an integer total across weights so the parts sum exactly to total."""
    s = sum(weights)
    raw = [total * w / s for w in weights]
    parts = [int(x) for x in raw]
    left = total - sum(parts)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - parts[i], reverse=True)
    for i in order[:left]:
        parts[i] += 1
    return parts


def load_targets():
    """(store, (year, month)) -> dict(rev, profit, orders) in cents / count."""
    t = {}
    with open(os.path.join(HERE, "data", "targets.csv"), newline="") as f:
        rows = list(csv.DictReader(f))
    month_idx = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6}
    for r in rows:
        key = (r["Store"], (2026, month_idx[r["Month"]]))
        t[key] = {"rev": round(float(r["Sales_K"]) * 100000),
                  "profit": round(float(r["Profit_K"]) * 100000),
                  "orders": int(r["Orders"])}
    # Jul-Dec 2025: same store split, seasonal shape, calibrated period totals
    sales_tot, profit_tot, orders_tot = 44465000, 9311000, 12490
    store_share = {s: sum(t[(s, ym)]["rev"] for ym in H1_2026) for s in STORES}
    grand = sum(store_share.values())
    cells = [(s, ym, store_share[s] / grand * H2_SHAPE[i]) for s in STORES for i, ym in enumerate(H2_2025)]
    wsum = sum(c[2] for c in cells)
    rev = largest_remainder(sales_tot, [c[2] for c in cells])
    prof = largest_remainder(profit_tot, [c[2] for c in cells])
    orde = largest_remainder(orders_tot, [c[2] for c in cells])
    for (s, ym, _), r_, p_, o_ in zip(cells, rev, prof, orde):
        t[(s, ym)] = {"rev": r_, "profit": p_, "orders": o_}
    # Jan-Jun 2025: scaled-down copy of 2026 H1
    for s in STORES:
        for i, ym in enumerate(H1_2025):
            base = t[(s, H1_2026[i])]
            t[(s, ym)] = {k: round(v * H1_2025_FACTOR) for k, v in base.items()}
    return t


def generate_rows(seed=7):
    rng = random.Random(seed)
    targets = load_targets()
    rows = []
    for (store, (y, m)), tgt in sorted(targets.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        days = calendar.monthrange(y, m)[1]
        cells = []
        for d in range(1, days + 1):
            dow = date(y, m, d).weekday()
            dow_f = 1.35 if dow >= 5 else 1.0
            for cat, prod, pop in PRODUCTS:
                cells.append((d, cat, prod, pop * dow_f * rng.lognormvariate(0, 0.45)))
        orders = largest_remainder(tgt["orders"], [c[3] for c in cells])
        kept = [(c, o) for c, o in zip(cells, orders) if o > 0]
        revw = [o * TICKET[c[1]] * rng.lognormvariate(0, 0.25) for c, o in kept]
        rev = largest_remainder(tgt["rev"], revw)
        cost_total = tgt["rev"] - tgt["profit"]
        costw = [r * CATEGORY_MARGIN[c[1]] for (c, _), r in zip(kept, rev)]
        cost = largest_remainder(cost_total, costw)
        for ((d, cat, prod, _), o), r, c in zip(kept, rev, cost):
            units = max(o, round(o * rng.uniform(1.2, 2.0)))
            rows.append((date(y, m, d).isoformat(), store, cat, prod, o, units, r, c))
    return rows


if __name__ == "__main__":
    rows = generate_rows()
    print(len(rows), "rows;", sum(r[6] for r in rows) / 100, "revenue")
