"""Kisan Mitra decision engine: irrigation forecaster, nutrient advisor, sensor health monitor.

Pure functions, no I/O. Rules (from the project spec):
  * Irrigate only if moisture will cross the crop's refill point within 24 h
    AND no useful rain (>= 8 mm) is due in the next 24 h.
  * Hold fertiliser if >= 15 mm rain is due within 48 h.
  * Doses are capped per application; a faulty probe is excluded from decisions.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .imd import classify_rain
from .crops import ACRE_M2, CROPS, HA_PER_ACRE, PRODUCTS, SPLIT_CAP_KG_HA

USEFUL_RAIN_MM = 8.0        # in the next 24 h -> skip irrigation
FERT_HOLD_RAIN_MM = 15.0    # in the next 48 h -> hold fertiliser
RAIN_EFFICIENCY = 0.8       # share of rain that reaches the root zone
IRRIGATE_LOOKAHEAD_H = 24
EVENING_HOUR = 18
HORIZON_H = 72

# Sensor health thresholds
VALID_RANGE = (0.0, 60.0)
OUTLIER_PP = 10.0           # percentage points from the median of all probes
STUCK_EPS = 0.02


# ----------------------------------------------------------------------------- ET0

def extraterrestrial_radiation(lat_deg: float, doy: int) -> float:
    """Ra in mm/day equivalent (FAO-56 eq. 21, divided by latent heat 2.45)."""
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi / 365 * doy)
    delta = 0.409 * math.sin(2 * math.pi / 365 * doy - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    ra = (24 * 60 / math.pi) * 0.0820 * dr * (
        ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws)
    )
    return ra / 2.45


def hargreaves_et0(tmax: float, tmin: float, lat_deg: float, doy: int) -> float:
    """Reference evapotranspiration, mm/day (Hargreaves-Samani)."""
    tmean = (tmax + tmin) / 2
    ra = extraterrestrial_radiation(lat_deg, doy)
    return max(0.0, 0.0023 * ra * (tmean + 17.8) * math.sqrt(max(tmax - tmin, 0.0)))


def _hourly_et_weights() -> List[float]:
    """Share of daily ET falling in each clock hour (half-sine over 06:00-18:00)."""
    raw = [math.sin(math.pi * (h - 6) / 12) if 6 <= h <= 18 else 0.0 for h in range(24)]
    total = sum(raw)
    return [r / total for r in raw]


_ET_W = _hourly_et_weights()


# ----------------------------------------------------------------------------- water balance

def simulate_moisture(
    theta0: float,
    crop: str,
    stage: str,
    start: datetime,
    tmax: List[float],
    tmin: List[float],
    rain_mm_hourly: List[float],
    lat_deg: float,
    irrigation_mm: Optional[Dict[int, float]] = None,
    hours: int = HORIZON_H,
) -> List[dict]:
    """FAO-56 style bucket model, hourly. Returns hours+1 points (hour 0 = now)."""
    c = CROPS[crop]
    zr, fc = c["root_depth_mm"], c["field_capacity"]
    kc = c["kc"][stage]
    irrigation_mm = irrigation_mm or {}
    theta = theta0
    out = [{"hour": 0, "time": start.isoformat(), "moisture": round(theta, 2),
            "rain_mm": 0.0, "et_mm": 0.0}]
    for h in range(hours):
        t = start + timedelta(hours=h)
        day = min(h // 24, len(tmax) - 1)
        et0 = hargreaves_et0(tmax[day], tmin[day], lat_deg, t.timetuple().tm_yday)
        etc_h = et0 * kc * _ET_W[t.hour]
        loss_h = c["loss_mm_day"] / 24
        rain = rain_mm_hourly[h] if h < len(rain_mm_hourly) else 0.0
        net_mm = RAIN_EFFICIENCY * rain + irrigation_mm.get(h, 0.0) - etc_h - loss_h
        theta = max(0.0, min(fc, theta + net_mm / zr * 100))
        out.append({"hour": h + 1, "time": (t + timedelta(hours=1)).isoformat(),
                    "moisture": round(theta, 2), "rain_mm": round(rain, 2), "et_mm": round(etc_h, 3)})
    return out


# ----------------------------------------------------------------------------- sensor health

def check_sensor_health(probes: List[dict]) -> dict:
    """probes: [{id, readings:[...]}], readings hourly oldest -> newest.

    Returns {status, healthy_ids, faulty:[{id, reason}], moisture_now}.
    status: ok | fault_found | insufficient
    """
    faulty: Dict[str, str] = {}
    lasts = {}
    for p in probes:
        r = p["readings"]
        if not r or any(v is None or (isinstance(v, float) and math.isnan(v)) for v in r):
            faulty[p["id"]] = "missing_data"
            continue
        lasts[p["id"]] = r[-1]
        if not (VALID_RANGE[0] < r[-1] <= VALID_RANGE[1]):
            faulty[p["id"]] = "out_of_range"

    def spread(r):
        return max(r) - min(r)

    movers = [p for p in probes if p["id"] in lasts and spread(p["readings"]) > 0.2]
    for p in probes:
        pid = p["id"]
        if pid in faulty or pid not in lasts:
            continue
        if spread(p["readings"]) < STUCK_EPS and any(m["id"] != pid for m in movers):
            faulty[pid] = "stuck"

    live = {k: v for k, v in lasts.items() if k not in faulty}
    if len(live) >= 3:
        med = statistics.median(live.values())
        for pid, v in live.items():
            if abs(v - med) > OUTLIER_PP:
                faulty[pid] = "disagrees_with_other_probes"

    healthy = [k for k in lasts if k not in faulty]
    if not healthy:
        status, now = "insufficient", None
    else:
        now = round(statistics.median(lasts[k] for k in healthy), 2)
        status = "fault_found" if faulty else "ok"
    return {
        "status": status,
        "healthy_ids": healthy,
        "faulty": [{"id": k, "reason": v} for k, v in faulty.items()],
        "moisture_now": now,
    }


# ----------------------------------------------------------------------------- irrigation

def advise_irrigation(
    moisture_now: Optional[float],
    crop: str,
    stage: str,
    area_acres: float,
    pump_lpm: float,
    start: datetime,
    tmax: List[float],
    tmin: List[float],
    rain_mm_hourly: List[float],
    lat_deg: float = 13.63,
    irrigation_efficiency: float = 0.75,
) -> dict:
    c = CROPS[crop]
    if moisture_now is None:
        return {"action": "no_data", "trajectory": [], "trajectory_with_irrigation": None}

    traj = simulate_moisture(moisture_now, crop, stage, start, tmax, tmin, rain_mm_hourly, lat_deg)
    refill = c["refill_point"]
    critical = c["wilting_point"] + 0.3 * (refill - c["wilting_point"])
    rain_24 = round(sum(rain_mm_hourly[:IRRIGATE_LOOKAHEAD_H]), 1)
    first_cross = next((p["hour"] for p in traj if p["moisture"] < refill), None)
    et0_today = hargreaves_et0(tmax[0], tmin[0], lat_deg, start.timetuple().tm_yday)
    base = {
        "moisture_now": moisture_now,
        "field_capacity": c["field_capacity"],
        "water_use_mm_day": round(et0_today * c["kc"][stage] + c["loss_mm_day"], 1),
        "refill_point": refill,
        "critical_point": round(critical, 1),
        "rain_next_24h_mm": rain_24,
        "hours_to_refill": first_cross,
        "trajectory": traj,
        "trajectory_with_irrigation": None,
    }

    if first_cross is None or first_cross > IRRIGATE_LOOKAHEAD_H:
        # Rain may be the reason no irrigation is needed: say so if the dry forecast would have crossed.
        if rain_24 >= USEFUL_RAIN_MM:
            dry = simulate_moisture(moisture_now, crop, stage, start, tmax, tmin, [0.0] * 72, lat_deg)
            dry_cross = next((p["hour"] for p in dry if p["moisture"] < refill), None)
            if dry_cross is not None and dry_cross <= IRRIGATE_LOOKAHEAD_H:
                return {**base, "action": "wait_rain"}
        return {**base, "action": "wait"}
    if rain_24 >= USEFUL_RAIN_MM:
        return {**base, "action": "wait_rain"}

    # Choose start: latest 18:00 slot not after the crossing; otherwise start now.
    start_h = 0
    if moisture_now > critical:
        slots = [h for h in range(0, first_cross + 1)
                 if (start + timedelta(hours=h)).hour == EVENING_HOUR]
        if slots:
            start_h = slots[-1]
    action = "irrigate_now" if start_h == 0 else "irrigate_evening"

    theta_at_start = traj[start_h]["moisture"]
    target = c["field_capacity"] - 2.0
    net_mm = min(max((target - theta_at_start) / 100 * c["root_depth_mm"], 0.0), c["max_irrigation_mm"])
    gross_mm = net_mm / irrigation_efficiency
    litres = gross_mm * area_acres * ACRE_M2          # 1 mm over 1 m2 = 1 L
    minutes = litres / pump_lpm if pump_lpm > 0 else 0.0
    start_time = start + timedelta(hours=start_h)
    with_irr = simulate_moisture(moisture_now, crop, stage, start, tmax, tmin, rain_mm_hourly,
                                 lat_deg, irrigation_mm={start_h: net_mm})
    return {
        **base,
        "action": action,
        "start_time": start_time.isoformat(),
        "start_in_hours": start_h,
        "start_day_offset": (start_time.date() - start.date()).days,
        "net_mm": round(net_mm, 1),
        "litres": int(round(litres, -2)),
        "pump_minutes": int(round(minutes / 5.0) * 5) or 5,
        "trajectory_with_irrigation": with_irr,
    }


# ----------------------------------------------------------------------------- nutrients

def advise_nutrients(
    npk: Optional[dict],
    crop: str,
    stage: str,
    rain_mm_hourly: List[float],
) -> dict:
    """npk: {N, P, K} in kg/ha available (Soil Health Card style units)."""
    if not npk:
        return {"action": "no_data", "doses": [], "rain_next_48h_mm": 0.0}
    band = CROPS[crop]["npk_band"][stage]
    rain_48 = round(sum(rain_mm_hourly[:48]), 1)
    status, doses = {}, []
    for nut in ("N", "P", "K"):
        v = npk[nut]
        lo, hi = band[nut]
        status[nut] = {"value": v, "low": lo, "high": hi,
                       "state": "low" if v < lo else ("high" if v > hi else "ok")}
        if v < lo:
            mid = (lo + hi) / 2
            needed = mid - v
            cap = SPLIT_CAP_KG_HA[nut]
            nutrient_kg_ha = min(needed, cap)
            product, frac = PRODUCTS[nut]
            doses.append({
                "nutrient": nut,
                "product": product,
                "kg_per_acre": int(round(nutrient_kg_ha / frac * HA_PER_ACRE)),
                "capped": needed > cap,
                "deficit_kg_ha": round(needed, 1),
            })
    if not doses:
        action = "ok"
    elif rain_48 >= FERT_HOLD_RAIN_MM:
        action = "hold_rain"
    else:
        action = "apply"
    return {"action": action, "status": status, "doses": doses, "rain_next_48h_mm": rain_48}


# ----------------------------------------------------------------------------- orchestration

def weather_reference(weather: dict, lat_deg: float = 13.63) -> dict:
    """Summary of the forecast the decision was based on, for citing in advice and the console."""
    rain = weather["rain_mm_hourly"]
    peak_h = max(range(len(rain)), key=lambda i: rain[i]) if rain else 0
    has_peak = bool(rain) and rain[peak_h] > 0
    start = weather["start"]
    return {
        "live": bool(weather.get("source")),
        "source": weather.get("source"),
        "rain_24h_mm": round(sum(rain[:24]), 1),
        "rain_48h_mm": round(sum(rain[:48]), 1),
        "rain_72h_mm": round(sum(rain[:72]), 1),
        "rain_24h_imd_class": classify_rain(sum(rain[:24]))["label"],   # IMD 24-h rainfall category
        "peak_rain_mm_h": round(rain[peak_h], 1) if has_peak else 0.0,
        "peak_rain_in_h": peak_h if has_peak else None,
        "tmax_today": round(max(weather["tmax"][:1] or [0]), 1),
        "et0_mm_day": round(hargreaves_et0(weather["tmax"][0], weather["tmin"][0], lat_deg,
                                           start.timetuple().tm_yday), 1),
    }


def decide(field: dict, probes: List[dict], npk: Optional[dict], weather: dict) -> dict:
    health = check_sensor_health(probes)
    start = weather["start"]
    irr = advise_irrigation(
        health["moisture_now"], field["crop"], field["stage"], field["area_acres"],
        field["pump_lpm"], start, weather["tmax"], weather["tmin"], weather["rain_mm_hourly"],
        field.get("latitude", 13.63), field.get("irrigation_efficiency", 0.75),
    )
    # Fertiliser wash-off is judged on the cautious (upper-bound) rain when the source only gives a range (IMD).
    nut = advise_nutrients(npk, field["crop"], field["stage"],
                           weather.get("rain_upper_mm_hourly") or weather["rain_mm_hourly"])

    # One clear primary action, in priority order.
    if health["status"] == "insufficient":
        primary = "no_data"
    elif irr["action"] in ("irrigate_now", "irrigate_evening"):
        primary = irr["action"]
    elif health["status"] == "fault_found":
        primary = "check_sensor"
    elif nut["action"] in ("apply", "hold_rain"):
        primary = "fert_apply" if nut["action"] == "apply" else "fert_hold"
    elif irr["action"] == "wait_rain":
        primary = "wait_rain"
    else:
        primary = "wait"
    return {"primary": primary, "irrigation": irr, "nutrients": nut, "sensor_health": health,
            "weather_ref": weather_reference(weather, field.get("latitude", 13.63))}
