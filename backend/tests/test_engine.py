from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import scenarios
from app.engine import (advise_irrigation, advise_nutrients, check_sensor_health,
                        hargreaves_et0)
from app.main import app

START = datetime(2026, 10, 26, 7, 0)
TMAX, TMIN = [34.0] * 3, [25.0] * 3
DRY = [0.0] * 72


def irr(moisture, rain=DRY):
    return advise_irrigation(moisture, "paddy", "development", 1.0, 600.0, START, TMAX, TMIN, rain)


def test_et0_in_plausible_range_for_chittoor():
    assert 3.5 <= hargreaves_et0(34, 25, 13.6, 299) <= 6.5


def test_wet_soil_waits():
    assert irr(38.0)["action"] == "wait"


def test_crossing_within_24h_without_rain_irrigates_in_evening():
    r = irr(34.2)
    assert r["action"] == "irrigate_evening"
    assert r["hours_to_refill"] <= 24
    assert datetime.fromisoformat(r["start_time"]).hour == 18
    assert r["litres"] > 0 and r["pump_minutes"] > 0


def test_useful_rain_blocks_irrigation():
    rain = [0.0] * 72
    rain[8] = 8.0
    assert irr(34.2, rain)["action"] == "wait_rain"


def test_light_rain_does_not_block_irrigation():
    rain = [0.0] * 72
    rain[8] = 3.0
    assert irr(33.0, rain)["action"].startswith("irrigate")


def test_critical_dry_irrigates_now():
    r = irr(26.5)
    assert r["action"] == "irrigate_now"
    assert r["start_in_hours"] == 0


def test_irrigation_depth_is_capped():
    r = irr(26.5)
    assert r["net_mm"] <= 50.0


def test_no_moisture_means_no_advice():
    assert irr(None)["action"] == "no_data"


# --- nutrients
def test_low_n_dose_is_capped_to_split_limit():
    r = advise_nutrients({"N": 100, "P": 22, "K": 180}, "paddy", "development", DRY)
    d = r["doses"][0]
    assert r["action"] == "apply" and d["capped"]
    # 40 kg N/ha / 0.46 * 0.4047 ha/acre ~= 35 kg urea per acre
    assert d["kg_per_acre"] == 35


def test_fertiliser_held_when_heavy_rain_due_in_48h():
    rain = [0.0] * 72
    rain[30] = 15.0
    r = advise_nutrients({"N": 250, "P": 22, "K": 180}, "paddy", "development", rain)
    assert r["action"] == "hold_rain"


def test_rain_after_48h_does_not_hold_fertiliser():
    rain = [0.0] * 72
    rain[60] = 40.0
    r = advise_nutrients({"N": 250, "P": 22, "K": 180}, "paddy", "development", rain)
    assert r["action"] == "apply"


def test_nutrients_in_band_need_nothing():
    assert advise_nutrients({"N": 320, "P": 22, "K": 180}, "paddy", "mid", DRY)["action"] == "ok"


# --- sensor health
def probes(a, b, c):
    return [{"id": i, "readings": r} for i, r in zip("ABC", (a, b, c))]


GOOD_A = [36.4, 36.3, 36.2, 36.1, 36.0, 35.9]
GOOD_B = [36.8, 36.7, 36.6, 36.5, 36.4, 36.3]


def test_flat_outlier_probe_is_excluded():
    h = check_sensor_health(probes(GOOD_A, GOOD_B, [3.2] * 6))
    assert [f["id"] for f in h["faulty"]] == ["C"]
    assert h["healthy_ids"] == ["A", "B"]
    assert 35.9 <= h["moisture_now"] <= 36.4


def test_out_of_range_probe_is_flagged():
    h = check_sensor_health(probes(GOOD_A, GOOD_B, [36.0, 36.0, 99.0, 99.0, 99.0, 99.0]))
    assert h["faulty"][0]["reason"] == "out_of_range"


def test_all_probes_bad_gives_insufficient():
    h = check_sensor_health(probes([0.0] * 6, [0.0] * 6, [0.0] * 6))
    assert h["status"] == "insufficient" and h["moisture_now"] is None


# --- scenarios + API
EXPECTED = {
    "normal": "wait",
    "dry_spell": "irrigate_evening",
    "rain_coming": "wait_rain",
    "low_n": "fert_apply",
    "low_n_heavy_rain": "fert_hold",
    "faulty_sensor": "check_sensor",
    "critical_dry": "irrigate_now",
}
client = TestClient(app)


@pytest.mark.parametrize("sid,code", EXPECTED.items())
def test_scenario_via_api(sid, code):
    r = client.get(f"/api/scenarios/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["advice"]["code"] == code
    assert body["advice"]["te"]["text"] and body["advice"]["en"]["why"]


def test_all_scenarios_covered():
    assert set(EXPECTED) == set(scenarios.ORDER)


def test_post_advice_roundtrip():
    sc = client.get("/api/scenarios/dry_spell").json()["scenario"]
    body = {"field": {"crop": "paddy", "stage": "development", "area_acres": 1, "pump_lpm": 600},
            "probes": sc["probes"], "npk": sc["npk"], "weather": sc["weather"]}
    r = client.post("/api/advice", json=body)
    assert r.status_code == 200 and r.json()["advice"]["code"] == "irrigate_evening"


def test_bad_crop_rejected():
    sc = client.get("/api/scenarios/normal").json()["scenario"]
    body = {"field": {"crop": "banana", "stage": "mid", "area_acres": 1, "pump_lpm": 600},
            "probes": sc["probes"], "npk": sc["npk"], "weather": sc["weather"]}
    assert client.post("/api/advice", json=body).status_code == 422


# --- phone messages + crops
def test_phone_messages_cover_water_fertiliser_and_device():
    body = client.get("/api/scenarios/faulty_sensor").json()
    domains = [m["domain"] for m in body["advice"]["messages"]]
    assert domains == ["water", "nutrient", "device"]
    assert body["advice"]["messages"][2]["code"] == "check_sensor"


def test_only_actionable_messages_get_buttons():
    body = client.get("/api/scenarios/normal").json()
    assert not any(m["actionable"] for m in body["advice"]["messages"])
    body = client.get("/api/scenarios/low_n_heavy_rain").json()
    assert [m["code"] for m in body["advice"]["messages"] if m["actionable"]] == ["fert_hold"]


def test_sms_reports_segments():
    sms = client.get("/api/scenarios/dry_spell").json()["advice"]["sms"]
    assert sms["en"]["segments"] >= 1 and sms["te"]["chars"] > 0


def test_crops_endpoint_lists_four_crops():
    r = client.get("/api/crops").json()
    assert set(r) == {"paddy", "groundnut", "tomato", "maize"}
    assert r["paddy"]["refill_point"] < r["paddy"]["field_capacity"]


# --- forecast reference (cited in advice when the weather is live)
def _post(rain, source=None, scenario="dry_spell"):
    sc = client.get(f"/api/scenarios/{scenario}").json()["scenario"]
    w = {**sc["weather"], "rain_mm_hourly": rain}
    if source:
        w["source"] = source
    body = {"field": {"crop": "paddy", "stage": "development", "area_acres": 1, "pump_lpm": 600},
            "probes": sc["probes"], "npk": sc["npk"], "weather": w}
    return client.post("/api/advice", json=body).json()


SRC = {"name": "Open-Meteo", "location": "Tirupati", "fetched_at": "2026-10-26T11:53:00"}


def test_live_forecast_is_cited_in_water_and_fertiliser_messages():
    rain = [0.0] * 72
    rain[5] = 12.0
    out = _post(rain, SRC)
    refs = {m["domain"]: m["reference"] for m in out["advice"]["messages"] if m["reference"]}
    assert set(refs) == {"water", "nutrient"}
    assert "Open-Meteo forecast, Tirupati" in refs["water"]["en"] and "12.0 mm rain in the next 24 h" in refs["water"]["en"]
    assert "48 h" in refs["nutrient"]["en"] and refs["water"]["te"]
    assert out["decision"]["weather_ref"]["live"] is True


def test_simulated_weather_has_no_forecast_citation():
    out = _post([0.0] * 72)
    assert all(m["reference"] is None for m in out["advice"]["messages"])
    assert out["decision"]["weather_ref"]["live"] is False


def test_engine_follows_the_live_forecast():
    dry = _post([0.0] * 72, SRC)["advice"]["code"]
    rain = [0.0] * 72
    rain[6] = 20.0
    wet = _post(rain, SRC)
    assert dry == "irrigate_evening" and wet["advice"]["code"] == "wait_rain"
    ref = wet["decision"]["weather_ref"]
    assert ref["rain_24h_mm"] == 20.0 and ref["peak_rain_in_h"] == 6


def test_irrigate_now_reason_matches_how_dry_the_soil_is():
    crit = client.get("/api/scenarios/critical_dry").json()["advice"]["messages"][0]
    assert crit["code"] == "irrigate_now" and "very low" in crit["en"]["why"]
    # a mildly dry soil that cannot wait for the 18:00 slot must not be described as "very low"
    sc = client.get("/api/scenarios/dry_spell").json()["scenario"]
    w = {**sc["weather"], "start": sc["weather"]["start"][:11] + "19:00:00"}
    body = {"field": {"crop": "paddy", "stage": "development", "area_acres": 1, "pump_lpm": 600},
            "probes": sc["probes"], "npk": sc["npk"], "weather": w}
    msg = client.post("/api/advice", json=body).json()["advice"]["messages"][0]
    assert msg["code"] == "irrigate_now" and "very low" not in msg["en"]["why"]
