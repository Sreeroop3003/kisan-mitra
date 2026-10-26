"""Seven simulated demo scenarios. All sensor and weather data here is SYNTHETIC."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List

# Simulated clock: 07:00 on the day the server starts, so evening-slot logic is deterministic.
def _start() -> datetime:
    return datetime.now().replace(hour=7, minute=0, second=0, microsecond=0)


def _rain(events: Dict[int, float]) -> List[float]:
    r = [0.0] * 72
    for h, mm in events.items():
        r[h] = mm
    return r


def _history(now: float, drift_per_h: float = 0.12, n: int = 6) -> List[float]:
    """Last n hourly readings ending at `now`, drying at drift_per_h."""
    return [round(now + drift_per_h * (n - 1 - i) + (0.05 if i % 2 else -0.05), 2) for i in range(n)]


def _probes(now: float, spread=(0.0, 0.4, -0.3)) -> List[dict]:
    return [{"id": pid, "readings": _history(now + s)} for pid, s in zip("ABC", spread)]


FIELD = {"crop": "paddy", "stage": "development", "area_acres": 1.0, "pump_lpm": 600.0,
         "latitude": 13.63, "irrigation_efficiency": 0.75}
NPK_OK = {"N": 320, "P": 22, "K": 180}

META = {
    "normal": ("Normal", "సాధారణం", "Moisture is comfortable and nutrients are in range."),
    "dry_spell": ("Dry spell", "ఎండ కాలం", "Moisture will cross the refill point today, no rain."),
    "rain_coming": ("Rain coming", "వర్షం వస్తోంది", "Same dry field, but 20 mm of rain is due."),
    "low_n": ("Low N", "నత్రజని తక్కువ", "Nitrogen below the target band, dry weather."),
    "low_n_heavy_rain": ("Low N + heavy rain", "నత్రజని తక్కువ + భారీ వర్షం", "Nitrogen is low but 30 mm rain is due in 48 h."),
    "faulty_sensor": ("Faulty sensor", "సెన్సార్ లోపం", "Probe C reads 3.2% flat while A and B agree."),
    "critical_dry": ("Critical dry", "తీవ్రమైన ఎండ", "Soil is near wilting point."),
}


def build(name: str) -> dict:
    start = _start()
    tmax, tmin = [34.0, 35.0, 34.0], [25.0, 25.0, 24.5]
    field, npk, rain = dict(FIELD), dict(NPK_OK), [0.0] * 72
    probes = _probes(36.0)

    if name == "normal":
        pass
    elif name == "dry_spell":
        probes = _probes(34.2)
    elif name == "rain_coming":
        probes = _probes(34.2)
        rain = _rain({8: 6.0, 9: 8.0, 10: 6.0})
    elif name == "low_n":
        npk = {"N": 250, "P": 22, "K": 180}
    elif name == "low_n_heavy_rain":
        npk = {"N": 250, "P": 22, "K": 180}
        rain = _rain({26: 6.0, 27: 10.0, 28: 8.0, 29: 6.0})
    elif name == "faulty_sensor":
        probes = _probes(36.0)
        probes[2] = {"id": "C", "readings": [3.2] * 6}
    elif name == "critical_dry":
        probes = _probes(26.5)
        tmax = [36.0, 37.0, 36.0]
    else:
        raise KeyError(name)

    return {
        "id": name,
        "label": {"en": META[name][0], "te": META[name][1]},
        "description": META[name][2],
        "field": field,
        "probes": probes,
        "npk": npk,
        "weather": {"start": start.isoformat(), "tmax": tmax, "tmin": tmin, "rain_mm_hourly": rain},
    }


ORDER = list(META)
