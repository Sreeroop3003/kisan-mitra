"""Kisan Mitra API + demo UI.   Run:  uvicorn app.main:app --reload   (from backend/)"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import scenarios
from .crops import CROPS, STAGES
from .engine import decide
from .messages import build_advice
from .imd import RAIN_CLASSES, SOURCE_NAME, forecast_from_imd
from .weather import LOCATIONS, accuweather_configured, fetch_forecast

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
FEEDBACK_FILE = ROOT / "data" / "feedback.jsonl"

app = FastAPI(title="Kisan Mitra", version="0.1.0-prototype",
              description="AI soil & water advisor for small farmers. PROTOTYPE: simulated data, rule-based engine.")


class FieldIn(BaseModel):
    crop: str
    stage: str
    area_acres: float = Field(gt=0)
    pump_lpm: float = Field(gt=0)
    latitude: float = 13.63
    irrigation_efficiency: float = Field(0.75, gt=0, le=1)


class ProbeIn(BaseModel):
    id: str
    readings: List[float] = Field(description="Hourly soil moisture %, oldest first, last = now")


class NPKIn(BaseModel):
    N: float
    P: float
    K: float


class WeatherSource(BaseModel):
    """Where a live forecast came from, so advice can cite it."""
    name: str
    location: str
    fetched_at: Optional[str] = None


class WeatherIn(BaseModel):
    source: Optional[WeatherSource] = None
    start: datetime
    rain_upper_mm_hourly: Optional[List[float]] = Field(None, description="Upper-bound rain where the source gives a range (IMD categories)")
    tmax: List[float] = Field(min_length=1)
    tmin: List[float] = Field(min_length=1)
    rain_mm_hourly: List[float] = Field(description="72 hourly rain values, mm")


class AdviceRequest(BaseModel):
    field: FieldIn
    probes: List[ProbeIn]
    npk: Optional[NPKIn] = None
    weather: WeatherIn


class Feedback(BaseModel):
    scenario_id: Optional[str] = None
    advice_code: str
    response: str = Field(pattern="^(done|skip)$")


def _run(field: dict, probes: list, npk: Optional[dict], weather: dict) -> dict:
    if field["crop"] not in CROPS:
        raise HTTPException(422, f"crop must be one of {list(CROPS)}")
    if field["stage"] not in STAGES:
        raise HTTPException(422, f"stage must be one of {list(STAGES)}")
    decision = decide(field, probes, npk, weather)
    return {"decision": decision, "advice": build_advice(decision, field), "field": field}


@app.get("/api/health")
def health():
    return {"ok": True, "version": app.version}


@app.get("/api/crops")
def crops():
    """Crop parameter table (illustrative values) for UIs and sanity checks."""
    return {k: {**v, "npk_band": {st: {n: list(b) for n, b in bands.items()} for st, bands in v["npk_band"].items()}}
            for k, v in CROPS.items()}


@app.get("/api/scenarios")
def list_scenarios():
    return [{"id": s, "label": scenarios.build(s)["label"], "description": scenarios.build(s)["description"]}
            for s in scenarios.ORDER]


@app.get("/api/scenarios/{scenario_id}")
def run_scenario(scenario_id: str):
    try:
        sc = scenarios.build(scenario_id)
    except KeyError:
        raise HTTPException(404, "unknown scenario")
    w = {**sc["weather"], "start": datetime.fromisoformat(sc["weather"]["start"])}
    out = _run(sc["field"], sc["probes"], sc["npk"], w)
    return {"scenario": {k: sc[k] for k in ("id", "label", "description", "probes", "npk", "weather")}, **out}


@app.post("/api/advice")
def advice(req: AdviceRequest):
    d = req.model_dump()
    npk = d["npk"]
    return _run(d["field"], d["probes"], npk, d["weather"])


@app.get("/api/weather/live")
def live_weather(location: str = "tirupati", provider: str = "auto"):
    """Real-time 72-96 h forecast. provider: auto | accuweather | open-meteo (see app/weather.py)."""
    if location not in LOCATIONS:
        raise HTTPException(422, f"location must be one of {list(LOCATIONS)}")
    if provider not in ("auto", "accuweather", "open-meteo"):
        raise HTTPException(422, "provider must be auto, accuweather or open-meteo")
    try:
        return fetch_forecast(location, provider)
    except Exception as e:  # network down at the demo venue is likely; fail clearly
        raise HTTPException(503, f"weather service unreachable: {e}")


class ImdDay(BaseModel):
    tmax: float
    tmin: float
    rain_category: str


class ImdForecastIn(BaseModel):
    location: str = "tirupati"
    days: List[ImdDay] = Field(min_length=1, max_length=5)
    issued_on: Optional[str] = Field(None, description="Bulletin date, e.g. 2026-10-26")


@app.get("/api/imd/rain-classes")
def imd_rain_classes():
    """IMD 24-hour rainfall categories (verify on mausam.imd.gov.in)."""
    return {"classes": RAIN_CLASSES, "source": "India Meteorological Department"}


@app.post("/api/weather/imd")
def imd_forecast(req: ImdForecastIn):
    """Turn values read from an IMD district forecast / agromet advisory into the engine's forecast format."""
    if req.location not in LOCATIONS:
        raise HTTPException(422, f"location must be one of {list(LOCATIONS)}")
    start = datetime.now().replace(minute=0, second=0, microsecond=0)
    try:
        return forecast_from_imd(req.location, [d.model_dump() for d in req.days], start, req.issued_on)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/weather/locations")
def weather_locations():
    return {"locations": [{"id": k, **v} for k, v in LOCATIONS.items()],
            "accuweather_configured": accuweather_configured()}


@app.post("/api/feedback")
def feedback(fb: Feedback):
    """Done/Skip responses. Stored locally as JSON lines; used later for model retraining."""
    FEEDBACK_FILE.parent.mkdir(exist_ok=True)
    row = {"ts": datetime.now().isoformat(timespec="seconds"), **fb.model_dump()}
    with FEEDBACK_FILE.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return {"saved": True}


# Per acre, per season, paddy. ESTIMATES from the project brief - to be validated in the pilot.
IMPACT_PER_ACRE = {"water_litres": 1_460_000, "urea_kg": 25, "energy_kwh": 300, "co2e_t": 0.3}


@app.get("/api/impact")
def impact(acres: float = 1.0, seasons: float = 1.0):
    k = acres * seasons
    return {"estimate": True, "basis": "paddy, per acre per season, unvalidated",
            **{key: round(v * k, 2) for key, v in IMPACT_PER_ACRE.items()}}


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
