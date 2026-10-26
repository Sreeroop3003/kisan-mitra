"""Live forecast providers: AccuWeather (official API, needs a key) and Open-Meteo (free, no key).

Both return the same shape (see `normalise`): 96 hourly values starting at the current local hour,
plus 4 daily summaries. Verify terms at https://developer.accuweather.com and https://open-meteo.com.

AccuWeather: set the ACCUWEATHER_API_KEY environment variable. We use only the official API
(the accuweather.com web pages must not be scraped). The free API tier gives 12 hourly + 5 daily
forecasts, so hours beyond the first 12 are *approximated* from daily totals and flagged as such.
The AccuWeather code path follows the published API docs but has NOT been tested against a live key.
"""
from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Dict, List, Optional

HOURS = 96
LOCATIONS = {
    "tirupati": {"name": "Tirupati", "lat": 13.6288, "lon": 79.4192},
    "chittoor": {"name": "Chittoor", "lat": 13.2172, "lon": 79.1003},
    "madanapalle": {"name": "Madanapalle", "lat": 13.5503, "lon": 78.5029},
}
DEFAULT_LOCATION = "tirupati"


def _get_json(url: str, timeout: float = 8.0) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _now_hour() -> datetime:
    return datetime.now().replace(minute=0, second=0, microsecond=0)


def accuweather_configured() -> bool:
    return bool(os.environ.get("ACCUWEATHER_API_KEY"))


def normalise(source: str, location: str, start: datetime, temp: List[float], rain: List[float],
              prob: Optional[List[float]], daily: List[dict], approximate_after_h: Optional[int] = None) -> dict:
    pad = lambda xs, v=0.0: (list(xs) + [v] * HOURS)[:HOURS]
    return {
        "source": source,
        "location": location,
        "start": start.isoformat(),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "temp_c_hourly": [round(x, 1) for x in pad(temp, temp[-1] if temp else 25.0)],
        "rain_mm_hourly": [round(x, 2) for x in pad(rain)],
        "precip_prob": pad(prob, 0) if prob is not None else None,
        "daily": daily[:4],
        "tmax": [d["tmax"] for d in daily[:4]],
        "tmin": [d["tmin"] for d in daily[:4]],
        "approximate_after_hours": approximate_after_h,
    }


# ----------------------------------------------------------------------------- Open-Meteo

def fetch_open_meteo(lat: float, lon: float, location: str) -> dict:
    qs = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,precipitation,precipitation_probability",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "forecast_days": 5, "timezone": "Asia/Kolkata",
    })
    data = _get_json(f"https://api.open-meteo.com/v1/forecast?{qs}")
    start = _now_hour()
    idx = start.hour                                 # hourly series starts at 00:00 today
    h, d = data["hourly"], data["daily"]
    sl = lambda key: [float(x or 0.0) for x in h[key][idx: idx + HOURS]]
    daily = [{"date": d["time"][i], "tmax": d["temperature_2m_max"][i], "tmin": d["temperature_2m_min"][i],
              "rain_mm": float(d["precipitation_sum"][i] or 0.0)} for i in range(len(d["time"]))]
    return normalise("Open-Meteo", location, start, sl("temperature_2m"), sl("precipitation"),
                     sl("precipitation_probability"), daily)


# ----------------------------------------------------------------------------- AccuWeather

AW = "https://dataservice.accuweather.com"


def parse_accuweather(hourly12: list, daily5: dict, location: str, start: datetime) -> dict:
    """Build the normalised forecast from AccuWeather 12-hour and 5-day responses (pure; unit-tested)."""
    daily = []
    for f in daily5["DailyForecasts"]:
        day_rain = float(((f.get("Day") or {}).get("Rain") or {}).get("Value") or 0.0)
        night_rain = float(((f.get("Night") or {}).get("Rain") or {}).get("Value") or 0.0)
        daily.append({"date": f["Date"][:10], "tmax": f["Temperature"]["Maximum"]["Value"],
                      "tmin": f["Temperature"]["Minimum"]["Value"], "rain_mm": day_rain + night_rain,
                      "_day_rain": day_rain, "_night_rain": night_rain})

    temp, rain, prob = [], [], []
    for p in hourly12:                                # real hourly values, first 12 h
        temp.append(float(p["Temperature"]["Value"]))
        rain.append(float(((p.get("Rain") or {}).get("Value")) or 0.0))
        prob.append(float(p.get("PrecipitationProbability") or 0))
    real_hours = len(hourly12)

    # Beyond the 12 h: spread each daily Day (06-18) / Night (18-06) rain total evenly over its hours.
    by_date = {d["date"]: d for d in daily}
    for k in range(real_hours, HOURS):
        t = start + timedelta(hours=k)
        d = by_date.get(t.strftime("%Y-%m-%d"))
        if d is None:
            temp.append(temp[-1]); rain.append(0.0); prob.append(0.0); continue
        is_day = 6 <= t.hour < 18
        rain.append((d["_day_rain"] if is_day else d["_night_rain"]) / 12.0)
        prob.append(0.0)
        mean, amp = (d["tmax"] + d["tmin"]) / 2, (d["tmax"] - d["tmin"]) / 2
        temp.append(mean + amp * math.sin(math.pi * (t.hour - 9) / 12))   # min ~03:00, max ~15:00
    daily = [{k: v for k, v in d.items() if not k.startswith("_")} for d in daily]
    return normalise("AccuWeather", location, start, temp, rain, prob, daily, approximate_after_h=real_hours)


def fetch_accuweather(lat: float, lon: float, location: str, key: str) -> dict:
    q = urllib.parse.urlencode({"apikey": key, "q": f"{lat},{lon}"})
    loc = _get_json(f"{AW}/locations/v1/cities/geoposition/search?{q}")
    lk, name = loc["Key"], loc.get("LocalizedName") or location
    opts = urllib.parse.urlencode({"apikey": key, "metric": "true", "details": "true"})
    hourly = _get_json(f"{AW}/forecasts/v1/hourly/12hour/{lk}?{opts}")
    daily = _get_json(f"{AW}/forecasts/v1/daily/5day/{lk}?{opts}")
    return parse_accuweather(hourly, daily, name, _now_hour())


# ----------------------------------------------------------------------------- entry point

def fetch_forecast(location: str = DEFAULT_LOCATION, provider: str = "auto") -> dict:
    """provider: auto (AccuWeather if a key is set, else Open-Meteo) | accuweather | open-meteo.
    Falls back to Open-Meteo, with a `warning`, if AccuWeather fails."""
    loc = LOCATIONS.get(location, LOCATIONS[DEFAULT_LOCATION])
    lat, lon, name = loc["lat"], loc["lon"], loc["name"]
    want_aw = provider == "accuweather" or (provider == "auto" and accuweather_configured())
    warning = None
    if want_aw:
        if not accuweather_configured():
            warning = "ACCUWEATHER_API_KEY is not set on the server; showing Open-Meteo instead."
        else:
            try:
                return fetch_accuweather(lat, lon, name, os.environ["ACCUWEATHER_API_KEY"])
            except Exception as e:
                warning = f"AccuWeather request failed ({type(e).__name__}); showing Open-Meteo instead."
    out = fetch_open_meteo(lat, lon, name)
    if warning:
        out["warning"] = warning
    return out
