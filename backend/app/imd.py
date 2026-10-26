"""India Meteorological Department (IMD) forecast support.

IMD's programmatic APIs (https://api.imd.gov.in) need organisational onboarding via IMD's nodal officer,
JWT credentials and IP whitelisting, so they are NOT wired in here. What this module supports today:

  * IMD's official 24-hour rainfall categories (verify on https://mausam.imd.gov.in) used to label rain.
  * Building the engine's hourly forecast from the values a user reads off an IMD district forecast
    or a KVK / District Agromet Unit advisory (entered manually).

IMD gives rain as a category per day, not mm per hour. We therefore carry a LOWER and an UPPER bound:
irrigation uses the lower bound (never skip watering on rain that may not come), the fertiliser-wash-off
hold uses the upper bound (never fertilise ahead of rain that may be heavy). Rain is spread evenly over
each day because the timing is unknown.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import List, Optional

from .weather import HOURS, LOCATIONS, normalise

# 24-hour rainfall classes, mm. Open-ended top class is capped at 250 mm for the upper bound.
RAIN_CLASSES = [
    {"id": "none", "label": "No rain", "te": "వర్షం లేదు", "lo": 0.0, "hi": 0.0},
    {"id": "very_light", "label": "Very light rain", "te": "చాలా తేలికపాటి వర్షం", "lo": 0.1, "hi": 2.4},
    {"id": "light", "label": "Light rain", "te": "తేలికపాటి వర్షం", "lo": 2.5, "hi": 15.5},
    {"id": "moderate", "label": "Moderate rain", "te": "మోస్తరు వర్షం", "lo": 15.6, "hi": 64.4},
    {"id": "heavy", "label": "Heavy rain", "te": "భారీ వర్షం", "lo": 64.5, "hi": 115.5},
    {"id": "very_heavy", "label": "Very heavy rain", "te": "అతి భారీ వర్షం", "lo": 115.6, "hi": 204.4},
    {"id": "extremely_heavy", "label": "Extremely heavy rain", "te": "అత్యంత భారీ వర్షం", "lo": 204.5, "hi": 250.0},
]
_BY_ID = {c["id"]: c for c in RAIN_CLASSES}
SOURCE_NAME = "IMD district forecast (entered manually)"


def classify_rain(mm_24h: float) -> dict:
    """IMD category for a 24-hour rainfall total."""
    if mm_24h < 0.1:
        return RAIN_CLASSES[0]
    for c in RAIN_CLASSES[1:]:
        if mm_24h <= c["hi"] + 0.05:      # categories are contiguous at 0.1 mm resolution
            return c
    return RAIN_CLASSES[-1]


def category(cat_id: str) -> dict:
    if cat_id not in _BY_ID:
        raise ValueError(f"unknown IMD rain category {cat_id!r}; use one of {list(_BY_ID)}")
    return _BY_ID[cat_id]


def _diurnal(tmax: float, tmin: float, hour: int) -> float:
    """Minimum at ~03:00, maximum at ~15:00."""
    return (tmax + tmin) / 2 + (tmax - tmin) / 2 * math.sin(math.pi * (hour - 9) / 12)


def forecast_from_imd(location: str, days: List[dict], start: datetime, issued_on: Optional[str] = None) -> dict:
    """days: [{tmax, tmin, rain_category}], day 0 = the calendar day of `start`."""
    if not 1 <= len(days) <= 5:
        raise ValueError("give 1 to 5 forecast days")
    cats = []
    for i, d in enumerate(days):
        if not (10 <= d["tmin"] < d["tmax"] <= 50):
            raise ValueError(f"day {i + 1}: need 10 <= tmin < tmax <= 50 (got {d['tmin']}, {d['tmax']})")
        cats.append(category(d["rain_category"]))

    temp, rain, upper = [], [], []
    for k in range(HOURS):
        t = start + timedelta(hours=k)
        di = (t.date() - start.date()).days
        if di < len(days):
            d, c = days[di], cats[di]
            temp.append(_diurnal(d["tmax"], d["tmin"], t.hour))
            rain.append(c["lo"] / 24.0)
            upper.append(c["hi"] / 24.0)
        else:                              # beyond the entered days: hold last temperatures, assume no rain
            d = days[-1]
            temp.append(_diurnal(d["tmax"], d["tmin"], t.hour))
            rain.append(0.0)
            upper.append(0.0)

    daily = [{"date": (start + timedelta(days=i)).strftime("%Y-%m-%d"), "tmax": d["tmax"], "tmin": d["tmin"],
              "rain_mm": cats[i]["lo"], "rain_mm_upper": cats[i]["hi"], "imd_category": cats[i]["label"]}
             for i, d in enumerate(days)]
    name = LOCATIONS.get(location, {}).get("name", location)
    out = normalise(SOURCE_NAME, name, start, temp, rain, None, daily)
    out["rain_upper_mm_hourly"] = [round(x, 2) for x in upper]
    out["issued_on"] = issued_on
    out["entered_manually"] = True
    out["approximate_after_hours"] = None
    return out
