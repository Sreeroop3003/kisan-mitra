from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app import main, weather
from app.weather import HOURS, parse_accuweather

client = TestClient(main.app)
START = datetime(2026, 10, 26, 10, 0)


def aw_fixture():
    hourly = [{"Temperature": {"Value": 30.0 + i * 0.1}, "Rain": {"Value": 2.0 if i == 3 else 0.0},
               "PrecipitationProbability": 60 if i == 3 else 5} for i in range(12)]
    daily = {"DailyForecasts": [
        {"Date": (START + timedelta(days=i)).strftime("%Y-%m-%dT07:00:00+05:30"),
         "Temperature": {"Minimum": {"Value": 24.0}, "Maximum": {"Value": 34.0}},
         "Day": {"Rain": {"Value": 12.0 if i == 1 else 0.0}}, "Night": {"Rain": {"Value": 0.0}}}
        for i in range(5)]}
    return hourly, daily


def test_accuweather_parse_shape_and_real_hours():
    out = parse_accuweather(*aw_fixture(), "Tirupati", START)
    assert out["source"] == "AccuWeather" and out["approximate_after_hours"] == 12
    assert len(out["rain_mm_hourly"]) == len(out["temp_c_hourly"]) == len(out["precip_prob"]) == HOURS
    assert out["rain_mm_hourly"][3] == 2.0          # real hourly value kept
    assert len(out["daily"]) == 4 and out["tmax"][0] == 34.0


def test_accuweather_daily_rain_spread_over_daytime_hours():
    out = parse_accuweather(*aw_fixture(), "Tirupati", START)
    # day 1 (Oct 27) has 12 mm in daylight hours: 1 mm/h between 06:00 and 18:00, none at night
    hour_idx = lambda dt: int((dt - START).total_seconds() // 3600)
    assert out["rain_mm_hourly"][hour_idx(datetime(2026, 10, 27, 9))] == 1.0
    assert out["rain_mm_hourly"][hour_idx(datetime(2026, 10, 27, 22))] == 0.0
    assert abs(sum(out["rain_mm_hourly"][hour_idx(datetime(2026, 10, 27, 6)):hour_idx(datetime(2026, 10, 27, 18))]) - 12.0) < 0.1


def test_auto_provider_without_key_uses_open_meteo(monkeypatch):
    monkeypatch.delenv("ACCUWEATHER_API_KEY", raising=False)
    called = {}
    monkeypatch.setattr(weather, "fetch_open_meteo", lambda lat, lon, name: called.setdefault("om", {"source": "Open-Meteo"}))
    assert weather.fetch_forecast("tirupati", "auto")["source"] == "Open-Meteo"


def test_forced_accuweather_without_key_warns_and_falls_back(monkeypatch):
    monkeypatch.delenv("ACCUWEATHER_API_KEY", raising=False)
    monkeypatch.setattr(weather, "fetch_open_meteo", lambda lat, lon, name: {"source": "Open-Meteo"})
    out = weather.fetch_forecast("tirupati", "accuweather")
    assert "ACCUWEATHER_API_KEY" in out["warning"]


def test_accuweather_failure_falls_back_with_warning(monkeypatch):
    monkeypatch.setenv("ACCUWEATHER_API_KEY", "dummy")
    def boom(*a, **k): raise OSError("down")
    monkeypatch.setattr(weather, "fetch_accuweather", boom)
    monkeypatch.setattr(weather, "fetch_open_meteo", lambda lat, lon, name: {"source": "Open-Meteo"})
    out = weather.fetch_forecast("chittoor", "auto")
    assert out["source"] == "Open-Meteo" and "AccuWeather request failed" in out["warning"]


def test_live_endpoint_validates_and_reports_outage(monkeypatch):
    assert client.get("/api/weather/live?location=mars").status_code == 422
    assert client.get("/api/weather/live?provider=bing").status_code == 422
    def boom(*a, **k): raise OSError("no network")
    monkeypatch.setattr(main, "fetch_forecast", boom)
    assert client.get("/api/weather/live").status_code == 503


def test_locations_endpoint():
    body = client.get("/api/weather/locations").json()
    assert {l["id"] for l in body["locations"]} == {"tirupati", "chittoor", "madanapalle"}
