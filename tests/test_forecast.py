from datetime import date

import pytest

import forecast
from app import create_app


def test_holt_follows_a_rising_series():
    fc, rmse = forecast.holt([10, 12, 14, 16, 18, 20, 22, 24])
    assert fc[0] > 24 and fc == sorted(fc)
    assert rmse < 1


def test_holt_flat_series_stays_flat_and_never_negative():
    fc, _ = forecast.holt([50] * 10)
    assert all(abs(x - 50) < 0.01 for x in fc)
    assert all(x >= 0 for x in forecast.holt([9, 6, 3, 1, 0, 0])[0])


def test_trend_label():
    assert forecast.trend_label(None) == "new"
    assert forecast.trend_label(15) == "rising"
    assert forecast.trend_label(-15) == "falling"
    assert forecast.trend_label(2) == "steady"


def test_too_little_history_gives_a_message():
    rows = [("2026-01-05", "Tea", "Drinks", 5, 500)]
    out = forecast.build_forecast(rows, date(2026, 1, 11))
    assert out["ok"] is False and "full weeks" in out["message"]
    assert forecast.build_forecast([], date(2026, 1, 11))["ok"] is False


def test_unfinished_last_week_is_ignored():
    mondays = [date.fromordinal(date(2026, 1, 5).toordinal() + 7 * i).isoformat() for i in range(9)]
    rows = [(m, "Tea", "Drinks", 10, 1000) for m in mondays]
    out = forecast.build_forecast(rows, date.fromisoformat(mondays[-1]))   # last week only has its Monday
    assert out["ok"] and out["weeks_of_history"] == 8
    assert out["products"][0]["forecast_units"] == 40
    assert out["products"][0]["avg_price"] == 1.0


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app = create_app(db_path=str(tmp_path_factory.mktemp("fc") / "t.db"))
    app.config["TESTING"] = True
    return app.test_client()


def test_forecast_api_on_sample_data(client):
    d = client.get("/api/forecast").get_json()
    assert d["ok"] and len(d["chart"]["forecast"]) == 4 and d["products"]
    p = d["products"][0]
    assert p["forecast_units"] >= 0 and p["safety_stock"] >= 0
    assert client.get("/api/forecast?store=Nowhere").get_json()["ok"] is False


def test_forecast_page_is_served(client):
    assert b"What to order" in client.get("/forecast").data
