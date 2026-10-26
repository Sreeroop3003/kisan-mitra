"""Crop parameter tables.

ILLUSTRATIVE VALUES. Kc follows the FAO-56 pattern (initial / development / mid / late),
but every number here must be validated with ANGRAU / KVK agronomists before field use.
Soil-moisture values are volumetric % as read by a typical capacitive probe.
"""
from __future__ import annotations

STAGES = ("initial", "development", "mid", "late")

CROPS = {
    "paddy": {
        "name_en": "Paddy",
        "name_te": "వరి",
        "season_days": 120,
        "usual_irrigation_mm": 45.0,   # baseline "usual practice" for impact tracking
        "usual_urea_kg_acre": 45.0,
        "kc": {"initial": 1.05, "development": 1.10, "mid": 1.20, "late": 0.90},
        "root_depth_mm": 300,
        "field_capacity": 40.0,
        "refill_point": 32.0,   # irrigate when moisture is forecast to cross this
        "wilting_point": 25.0,
        "loss_mm_day": 3.0,     # seepage + percolation
        "max_irrigation_mm": 50.0,
        # Soil Health Card style units: kg/ha available nutrient. (low, high) target band.
        "npk_band": {
            "initial": {"N": (240, 300), "P": (15, 30), "K": (120, 240)},
            "development": {"N": (280, 360), "P": (15, 30), "K": (120, 240)},
            "mid": {"N": (280, 360), "P": (15, 30), "K": (120, 240)},
            "late": {"N": (240, 300), "P": (15, 30), "K": (120, 240)},
        },
    },
    "groundnut": {
        "name_en": "Groundnut",
        "name_te": "వేరుశెనగ",
        "season_days": 110,
        "usual_irrigation_mm": 50.0,
        "usual_urea_kg_acre": 20.0,
        "kc": {"initial": 0.45, "development": 0.75, "mid": 1.05, "late": 0.70},
        "root_depth_mm": 500,
        "field_capacity": 28.0,
        "refill_point": 20.0,
        "wilting_point": 12.0,
        "loss_mm_day": 0.5,
        "max_irrigation_mm": 40.0,
        "npk_band": {
            s: {"N": (180, 280), "P": (15, 30), "K": (120, 240)} for s in STAGES
        },
    },
    "tomato": {
        "name_en": "Tomato",
        "name_te": "టమాటా",
        "season_days": 120,
        "usual_irrigation_mm": 40.0,
        "usual_urea_kg_acre": 35.0,
        "kc": {"initial": 0.60, "development": 0.90, "mid": 1.15, "late": 0.80},
        "root_depth_mm": 500,
        "field_capacity": 30.0,
        "refill_point": 22.0,
        "wilting_point": 14.0,
        "loss_mm_day": 0.5,
        "max_irrigation_mm": 40.0,
        "npk_band": {
            s: {"N": (240, 340), "P": (20, 40), "K": (150, 280)} for s in STAGES
        },
    },
    "maize": {
        "name_en": "Maize",
        "name_te": "మొక్కజొన్న",
        "season_days": 110,
        "usual_irrigation_mm": 60.0,
        "usual_urea_kg_acre": 55.0,
        "kc": {"initial": 0.40, "development": 0.80, "mid": 1.20, "late": 0.60},
        "root_depth_mm": 600,
        "field_capacity": 30.0,
        "refill_point": 21.0,
        "wilting_point": 13.0,
        "loss_mm_day": 0.5,
        "max_irrigation_mm": 50.0,
        "npk_band": {
            s: {"N": (220, 320), "P": (20, 40), "K": (130, 230)} for s in STAGES
        },
    },
}

# Fertiliser products: nutrient -> (product code, nutrient fraction)
PRODUCTS = {
    "N": ("urea", 0.46),
    "P": ("dap", 0.46),   # P2O5 fraction; N supplied by DAP is ignored (conservative)
    "K": ("mop", 0.60),   # K2O fraction
}

# Max nutrient per single application (kg/ha). ILLUSTRATIVE caps standing in for ICAR split limits.
SPLIT_CAP_KG_HA = {"N": 40.0, "P": 30.0, "K": 20.0}

ACRE_M2 = 4046.86
HA_PER_ACRE = 0.404686
