"""Demand forecast and order planner.

Looks at weekly units per product and projects the next few weeks with damped Holt
smoothing (a level plus a gently fading trend). It needs nothing but the sales table, runs
without any AI service, and every number can be traced back to the weekly history.

For each product it also works out a safety stock (enough extra to cover normal ups and
downs about nine times out of ten) and a suggested order once the owner types in what is
on the shelf. That last step happens in the browser, because stock levels are not stored.
"""
import math
from datetime import date, timedelta

from flask import jsonify, request, send_from_directory

HORIZON = 4              # weeks ahead
MIN_WEEKS = 6            # complete weeks of history needed before we forecast
HISTORY_WEEKS = 26       # weeks of history sent to the chart
MAX_PRODUCTS = 40
ALPHA, BETA, PHI = 0.5, 0.3, 0.9
Z90 = 1.28               # safety factor for roughly 90 % service level
TREND_BAND = 10.0        # percent change that counts as rising or falling


def holt(series, horizon=HORIZON):
    """Damped Holt forecast. Returns (forecast list, rmse of the one-step errors)."""
    n = len(series)
    if n < 2:
        base = series[0] if series else 0.0
        return [max(0.0, base)] * horizon, 0.0
    span = min(3, n - 1)
    level = float(series[0])
    trend = (series[span] - series[0]) / span
    errors = []
    for y in series[1:]:
        predicted = level + PHI * trend
        errors.append(y - predicted)
        new_level = ALPHA * y + (1 - ALPHA) * predicted
        trend = BETA * (new_level - level) + (1 - BETA) * PHI * trend
        level = new_level
    out, fade = [], 0.0
    for k in range(1, horizon + 1):
        fade += PHI ** k
        out.append(max(0.0, level + fade * trend))
    rmse = math.sqrt(sum(e * e for e in errors) / len(errors)) if errors else 0.0
    return out, rmse


def trend_label(change_pct):
    if change_pct is None:
        return "new"
    if change_pct >= TREND_BAND:
        return "rising"
    if change_pct <= -TREND_BAND:
        return "falling"
    return "steady"


def week_starts(first, last):
    out, d = [], first
    while d <= last:
        out.append(d)
        d += timedelta(days=7)
    return out


def build_forecast(rows, last_day):
    """rows: (week_start 'YYYY-MM-DD', product, category, units, revenue_cents).

    last_day is the newest date in the data. A week that is not finished by then is left out,
    so a half-empty final week does not look like a sales drop.
    """
    if not rows:
        return {"ok": False, "message": "There is no sales data yet."}
    last_monday = last_day - timedelta(days=last_day.weekday())
    last_complete = last_monday - timedelta(days=7) if last_day.weekday() != 6 else last_monday
    firsts = date.fromisoformat(min(r[0] for r in rows))
    if last_complete < firsts or (last_complete - firsts).days // 7 + 1 < MIN_WEEKS:
        return {"ok": False, "message": f"A forecast needs at least {MIN_WEEKS} full weeks of sales. "
                                        "Import more history and it will appear here."}
    first = max(firsts, last_complete - timedelta(days=7 * (HISTORY_WEEKS - 1)))
    weeks = week_starts(first, last_complete)
    index = {w.isoformat(): i for i, w in enumerate(weeks)}
    future = [last_complete + timedelta(days=7 * (k + 1)) for k in range(HORIZON)]

    units, revenue, category = {}, {}, {}
    for wk, product, cat, u, rev in rows:
        i = index.get(wk)
        if i is None:
            continue
        units.setdefault(product, [0.0] * len(weeks))[i] += u or 0
        revenue.setdefault(product, [0.0] * len(weeks))[i] += (rev or 0) / 100
        category[product] = cat

    products = []
    for name, series in units.items():
        total_units = sum(series)
        if total_units <= 0:
            continue
        fc, rmse = holt(series)
        recent = series[-HORIZON:]
        recent_units = sum(recent)
        next_units = sum(fc)
        change = None if recent_units == 0 else round((next_units - recent_units) / recent_units * 100, 1)
        last_rev = sum(revenue[name][-8:])
        last_units = sum(series[-8:])
        price = last_rev / last_units if last_units else 0.0
        products.append({
            "product": name, "category": category[name],
            "recent_units": round(recent_units), "forecast_units": round(next_units),
            "change_pct": change, "trend": trend_label(change),
            "safety_stock": math.ceil(Z90 * rmse * math.sqrt(HORIZON)),
            "avg_price": round(price, 2), "forecast_sales": round(next_units * price, 2),
            "history": [round(x) for x in series[-12:]], "forecast": [round(x, 1) for x in fc],
            "_sales": sum(revenue[name]),
        })
    products.sort(key=lambda p: p["_sales"], reverse=True)
    products = products[:MAX_PRODUCTS]
    for p in products:
        del p["_sales"]

    # whole-business weekly sales, forecast as the sum of the product forecasts' sales
    total_hist = [sum(revenue[p][i] for p in revenue) for i in range(len(weeks))]
    total_fc, rmse = holt(total_hist)
    band = Z90 * rmse
    chart = {
        "history": [{"week": w.isoformat(), "sales": round(v, 2)} for w, v in zip(weeks, total_hist)],
        "forecast": [{"week": w.isoformat(), "sales": round(v, 2), "low": round(max(0.0, v - band), 2),
                      "high": round(v + band, 2)} for w, v in zip(future, total_fc)],
    }
    recent_sales = sum(total_hist[-HORIZON:])
    next_sales = sum(total_fc)
    return {
        "ok": True, "as_of": last_complete.isoformat(), "horizon_weeks": HORIZON, "weeks_of_history": len(weeks),
        "forecast_from": future[0].isoformat(), "forecast_to": (future[-1] + timedelta(days=6)).isoformat(),
        "totals": {"recent_sales": round(recent_sales, 2), "forecast_sales": round(next_sales, 2),
                   "change_pct": None if not recent_sales else round((next_sales - recent_sales) / recent_sales * 100, 1),
                   "forecast_units": sum(p["forecast_units"] for p in products)},
        "counts": {t: sum(1 for p in products if p["trend"] == t) for t in ("rising", "falling", "steady", "new")},
        "chart": chart, "products": products,
    }


def register(app, db, filters, error_cls):
    @app.get("/forecast")
    def forecast_page():
        return send_from_directory(app.static_folder, "forecast.html")

    @app.get("/api/forecast")
    def api_forecast():
        where, params, _, _ = filters(with_dates=False)
        con = db()
        newest = con.execute(f"SELECT MAX(day) FROM sales WHERE {where}", params).fetchone()[0]
        if newest is None:
            return jsonify({"ok": False, "message": "There is no sales data for this selection."})
        try:
            last_day = date.fromisoformat(newest)
        except ValueError as e:
            raise error_cls("Sales dates are not in YYYY-MM-DD form") from e
        rows = con.execute(
            "SELECT date(day, 'weekday 0', '-6 days') wk, product, category, SUM(units), SUM(revenue_cents)"
            f" FROM sales WHERE {where} GROUP BY wk, product, category", params).fetchall()
        return jsonify(build_forecast([tuple(r) for r in rows], last_day))
