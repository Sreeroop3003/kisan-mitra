from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.imd import classify_rain, forecast_from_imd
from app.main import app

client = TestClient(app)
START = datetime(2026, 10, 26, 10, 0)


@pytest.mark.parametrize("mm,label", [
    (0.0, "No rain"), (0.1, "Very light rain"), (2.4, "Very light rain"), (2.5, "Light rain"),
    (15.5, "Light rain"), (15.6, "Moderate rain"), (64.4, "Moderate rain"), (64.5, "Heavy rain"),
    (115.6, "Very heavy rain"), (204.5, "Extremely heavy rain"), (300, "Extremely heavy rain"),
])
def test_imd_rain_categories(mm, label):
    assert classify_rain(mm)["label"] == label


def days(*cats):
    return [{"tmax": 33.0, "tmin": 24.0, "rain_category": c} for c in cats]


def test_forecast_uses_category_bounds_per_day():
    f = forecast_from_imd("tirupati", days("none", "light", "none"), START, "2026-10-26")
    day1 = slice(14, 38)                      # 00:00-24:00 of day 1 starts 14 h after 10:00
    assert abs(sum(f["rain_mm_hourly"][day1]) - 2.5) < 0.2          # lower bound of "light"
    assert abs(sum(f["rain_upper_mm_hourly"][day1]) - 15.5) < 0.2   # upper bound of "light"
    assert f["source"].startswith("IMD") and f["entered_manually"] and f["issued_on"] == "2026-10-26"
    assert len(f["temp_c_hourly"]) == len(f["rain_mm_hourly"]) == 96
    assert max(f["temp_c_hourly"][:24]) <= 33.01 and min(f["temp_c_hourly"][:24]) >= 23.99


def test_bad_inputs_rejected():
    with pytest.raises(ValueError):
        forecast_from_imd("tirupati", days("drizzle"), START)
    with pytest.raises(ValueError):
        forecast_from_imd("tirupati", [{"tmax": 20, "tmin": 25, "rain_category": "none"}], START)


def test_endpoint_roundtrip_and_validation():
    ok = client.post("/api/weather/imd", json={"location": "chittoor", "days": days("none", "none", "none")})
    assert ok.status_code == 200 and ok.json()["location"] == "Chittoor"
    assert client.post("/api/weather/imd", json={"days": days("lots")}).status_code == 422
    assert client.post("/api/weather/imd", json={"location": "mars", "days": days("none")}).status_code == 422
    assert len(client.get("/api/imd/rain-classes").json()["classes"]) == 7


def test_fertiliser_hold_uses_imd_upper_bound_but_irrigation_uses_lower():
    sc = client.get("/api/scenarios/low_n").json()["scenario"]     # low N, paddy, dry soil margin
    f = forecast_from_imd("tirupati", days("light", "light", "light"), datetime.now().replace(minute=0, second=0, microsecond=0))
    w = {"start": f["start"], "tmax": f["tmax"], "tmin": f["tmin"], "rain_mm_hourly": f["rain_mm_hourly"][:72],
         "rain_upper_mm_hourly": f["rain_upper_mm_hourly"][:72],
         "source": {"name": f["source"], "location": f["location"]}}
    body = {"field": {"crop": "paddy", "stage": "development", "area_acres": 1, "pump_lpm": 600},
            "probes": sc["probes"], "npk": sc["npk"], "weather": w}
    out = client.post("/api/advice", json=body).json()
    assert out["decision"]["nutrients"]["action"] == "hold_rain"         # upper bound >= 15 mm in 48 h
    assert out["decision"]["irrigation"]["rain_next_24h_mm"] < 8          # lower bound does not skip watering
    assert out["decision"]["weather_ref"]["rain_24h_imd_class"] in ("Very light rain", "Light rain")
