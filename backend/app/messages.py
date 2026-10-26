"""Farmer-facing advice text (Telugu + English) built from an engine decision.

Telugu is simple, spoken-style. ALL TELUGU STRINGS NEED REVIEW BY A NATIVE SPEAKER
(ideally a farmer or KVK extension officer) before any field use.

build_advice() returns:
  code      primary action code (one clear action)
  en / te   {title, text, why} for the primary action
  messages  one message per domain: water, nutrient, device (what the phone shows)
  sms       combined SMS for basic phones, with character and segment counts
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Dict, List, Optional

PRODUCT_NAMES = {
    "urea": {"en": "Urea", "te": "యూరియా"},
    "dap": {"en": "DAP", "te": "డి.ఏ.పి."},
    "mop": {"en": "MOP (potash)", "te": "ఎం.ఓ.పి. (పొటాష్)"},
}
NUTRIENT_NAMES = {
    "N": {"en": "nitrogen", "te": "నత్రజని"},
    "P": {"en": "phosphorus", "te": "భాస్వరం"},
    "K": {"en": "potash", "te": "పొటాష్"},
}
BUTTONS = {"en": {"done": "Done", "skip": "Skip"}, "te": {"done": "అయింది", "skip": "వద్దు"}}
DAY_TE = {0: "ఈ సాయంత్రం", 1: "రేపు సాయంత్రం"}
DAY_EN = {0: "this evening", 1: "tomorrow evening"}
# Messages the farmer can act on (get Done / Skip buttons)
ACTIONABLE = {"irrigate_now", "irrigate_evening", "wait_rain", "fert_apply", "fert_hold"}


def _clock(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%-I:%M %p")


def _sms_stats(lang: str, body: str) -> dict:
    # GSM-7 for English (160 / 153), UCS-2 for Telugu (70 / 67).
    single, multi = (160, 153) if lang == "en" else (70, 67)
    n = len(body)
    return {"text": body, "chars": n, "segments": 1 if n <= single else math.ceil(n / multi)}


def _moisture_why(irr: dict) -> Dict[str, str]:
    m, r = irr.get("moisture_now"), irr.get("refill_point")
    if m is None:
        return {"en": "", "te": ""}
    return {"en": f" Moisture {m:.1f}% (refill point {r:.0f}%).",
            "te": f" తేమ {m:.1f}% (కనీసం {r:.0f}% ఉండాలి)."}


def _text(code: str, decision: dict) -> Dict[str, Dict[str, str]]:
    """{en:{title,text,why}, te:{...}} for one action code."""
    irr, nut, health = decision["irrigation"], decision["nutrients"], decision["sensor_health"]
    mw = _moisture_why(irr)

    if code in ("irrigate_now", "irrigate_evening"):
        m, l = irr["pump_minutes"], f'{irr["litres"]:,}'
        if code == "irrigate_now":
            h = irr["hours_to_refill"]
            if irr["moisture_now"] <= irr["critical_point"]:
                why_en, why_te = ("Soil moisture is very low and no useful rain is due.",
                                  "నేలలో తేమ బాగా తగ్గింది, వర్షం రాదు.")
            elif not h:
                why_en, why_te = ("Soil moisture is already below the safe level and no useful rain is due.",
                                  "నేలలో తేమ సురక్షిత స్థాయి కంటే తగ్గింది, వర్షం రాదు.")
            else:   # crossing soon, and no 18:00 slot fits before it
                why_en, why_te = (f"Soil moisture will drop below the safe level in about {h} hours, before the evening, and no useful rain is due.",
                                  f"సుమారు {h} గంటల్లో నేలలో తేమ తగ్గిపోతుంది, సాయంత్రం వరకు ఆగలేము, వర్షం రాదు.")
            en = {"title": "Irrigate now",
                  "text": f"Irrigate now. Run the pump for {m} minutes ({l} litres).",
                  "why": why_en + mw["en"]}
            te = {"title": "ఇప్పుడే నీరు పెట్టండి",
                  "text": f"ఇప్పుడే నీరు పెట్టండి. పంపు {m} నిమిషాలు నడపండి ({l} లీటర్లు).",
                  "why": why_te + mw["te"]}
        else:
            off = irr["start_day_offset"]
            t = _clock(irr["start_time"])
            h = irr["hours_to_refill"]
            en = {"title": f"Irrigate {DAY_EN[off]}",
                  "text": f"Irrigate {DAY_EN[off]} at {t}. Run the pump for {m} minutes ({l} litres).",
                  "why": f"Soil moisture will drop below the safe level in about {h} hours and no useful rain is due." + mw["en"]}
            te = {"title": f"{DAY_TE[off]} నీరు పెట్టండి",
                  "text": f"{DAY_TE[off]} {t} కి నీరు పెట్టండి. పంపు {m} నిమిషాలు నడపండి ({l} లీటర్లు).",
                  "why": f"సుమారు {h} గంటల్లో నేలలో తేమ తగ్గిపోతుంది, వర్షం రాదు." + mw["te"]}
    elif code == "wait_rain":
        mm = irr["rain_next_24h_mm"]
        en = {"title": "Do not irrigate - rain coming",
              "text": "Do not irrigate today. Rain is coming.",
              "why": f"About {mm} mm of rain is due in the next 24 hours and will refill the soil." + mw["en"]}
        te = {"title": "నీరు పెట్టకండి - వర్షం వస్తుంది",
              "text": "ఈ రోజు నీరు పెట్టకండి. వర్షం వస్తుంది.",
              "why": f"24 గంటల్లో సుమారు {mm} మి.మీ. వర్షం వస్తుంది, నేలకు నీరు అందుతుంది." + mw["te"]}
    elif code == "water_ok":
        en = {"title": "No water needed",
              "text": "No water needed today. Soil moisture is enough.",
              "why": "Moisture stays above the refill point for the next 24 hours." + mw["en"]}
        te = {"title": "నీరు అవసరం లేదు",
              "text": "ఈ రోజు నీరు అవసరం లేదు. నేలలో తేమ సరిపోతుంది.",
              "why": "రేపటి వరకు తేమ సరిపోతుంది." + mw["te"]}
    elif code == "check_sensor":
        ids = ", ".join(f["id"] for f in health["faulty"])
        en = {"title": "Check sensor",
              "text": f"Sensor {ids} is not working properly. Please check it.",
              "why": "Its readings do not match the other sensors, so advice uses the healthy ones only."}
        te = {"title": "సెన్సార్ చూడండి",
              "text": f"సెన్సార్ {ids} సరిగా పనిచేయడం లేదు. దయచేసి సెన్సార్ చూడండి.",
              "why": "ఈ సెన్సార్ రీడింగ్ మిగతా వాటితో సరిపోలడం లేదు, అందుకే సలహా మిగతా సెన్సార్ల ఆధారంగా ఇచ్చాము."}
    elif code == "no_data":
        en = {"title": "Sensors not working",
              "text": "We cannot give advice now. Please check your sensors.",
              "why": "No sensor is giving a reliable reading."}
        te = {"title": "సెన్సార్లు పనిచేయడం లేదు",
              "text": "ఇప్పుడు సలహా ఇవ్వలేము. దయచేసి సెన్సార్లు చూడండి.",
              "why": "ఏ సెన్సార్ నుంచీ సరైన రీడింగ్ రావడం లేదు."}
    elif code == "fert_apply":
        items_en = ", ".join(f'{PRODUCT_NAMES[d["product"]]["en"]} {d["kg_per_acre"]} kg' for d in nut["doses"])
        items_te = ", ".join(f'{PRODUCT_NAMES[d["product"]]["te"]} {d["kg_per_acre"]} కిలోలు' for d in nut["doses"])
        low_en = " and ".join(NUTRIENT_NAMES[d["nutrient"]]["en"] for d in nut["doses"])
        low_te = ", ".join(NUTRIENT_NAMES[d["nutrient"]]["te"] for d in nut["doses"])
        capped = any(d["capped"] for d in nut["doses"])
        en = {"title": "Apply fertiliser",
              "text": f"Apply {items_en} per acre.",
              "why": f"Soil {low_en} is low." + (" Dose is limited to a safe amount per application; repeat in 2-3 weeks if advised." if capped else "")}
        te = {"title": "ఎరువు వేయండి",
              "text": f"ఎకరాకు {items_te} వేయండి.",
              "why": f"నేలలో {low_te} తక్కువగా ఉంది." + (" ఒకేసారి ఎక్కువ వేయకూడదు, 2-3 వారాల తర్వాత మళ్ళీ చెబుతాము." if capped else "")}
    elif code == "fert_hold":
        mm = nut["rain_next_48h_mm"]
        en = {"title": "Hold fertiliser - heavy rain coming",
              "text": "Apply fertiliser after the rain. If applied now it will wash away.",
              "why": f"About {mm} mm of rain is due within 48 hours. Holding saves money and keeps nitrogen out of the water."}
        te = {"title": "ఎరువు వేయకండి - భారీ వర్షం వస్తుంది",
              "text": "ఎరువు వర్షం తర్వాత వేయండి. ఇప్పుడు వేస్తే కొట్టుకుపోతుంది.",
              "why": f"48 గంటల్లో సుమారు {mm} మి.మీ. వర్షం వస్తుంది."}
    elif code == "fert_ok":
        en = {"title": "No fertiliser needed",
              "text": "No fertiliser needed now. Nutrients are in range.",
              "why": "Nitrogen, phosphorus and potash are all inside the target range."}
        te = {"title": "ఎరువు అవసరం లేదు",
              "text": "ఇప్పుడు ఎరువు అవసరం లేదు. పోషకాలు సరిపడా ఉన్నాయి.",
              "why": "నత్రజని, భాస్వరం, పొటాష్ అన్నీ సరిపడా ఉన్నాయి."}
    else:  # "wait": everything fine
        en = {"title": "All good - no action needed",
              "text": "No irrigation or fertiliser needed today.",
              "why": "Soil moisture is enough for the next 24 hours and nutrients are in range." + mw["en"]}
        te = {"title": "అంతా బాగుంది - ఏమీ చేయక్కర్లేదు",
              "text": "ఈ రోజు నీరు, ఎరువు ఏమీ అవసరం లేదు.",
              "why": "రేపటి వరకు నేలలో తేమ సరిపోతుంది, పోషకాలు సరిగ్గా ఉన్నాయి." + mw["te"]}
    return {"en": en, "te": te}


def _domain_codes(decision: dict) -> List[tuple]:
    """[(domain, code)] for the messages the phone shows, in display order."""
    irr, nut, health = decision["irrigation"], decision["nutrients"], decision["sensor_health"]
    out = []
    if health["status"] != "insufficient":
        out.append(("water", {"irrigate_now": "irrigate_now", "irrigate_evening": "irrigate_evening",
                              "wait_rain": "wait_rain"}.get(irr["action"], "water_ok")))
    if nut["action"] != "no_data":
        out.append(("nutrient", {"apply": "fert_apply", "hold_rain": "fert_hold"}.get(nut["action"], "fert_ok")))
    if health["status"] == "insufficient":
        out.append(("device", "no_data"))
    elif health["status"] == "fault_found":
        out.append(("device", "check_sensor"))
    return out


def _reference(domain: str, ref: dict) -> Optional[Dict[str, str]]:
    """Cite the live forecast behind a water / fertiliser message (None in simulated mode)."""
    src = ref.get("source")
    if not ref.get("live") or not src or domain not in ("water", "nutrient"):
        return None
    when = datetime.fromisoformat(src["fetched_at"]).strftime("%-I:%M %p") if src.get("fetched_at") else ""
    label = src["name"] if "forecast" in src["name"].lower() else f'{src["name"]} forecast'
    tag_en = f'{label}, {src["location"]}' + (f", synced {when}" if when else "")
    tag_te = f'{src["location"]} వాతావరణ అంచనా ({src["name"]})' + (f", {when}" if when else "")
    if domain == "water":
        en = (f'{tag_en}: {ref["rain_24h_mm"]} mm rain in the next 24 h, high {ref["tmax_today"]}\u00b0C, '
              f'crop water use driven by ET0 {ref["et0_mm_day"]} mm/day. IMD rain category (24 h): {ref["rain_24h_imd_class"]}.')
        te = (f'{tag_te}: 24 గంటల్లో {ref["rain_24h_mm"]} మి.మీ. వర్షం, గరిష్ఠ ఉష్ణోగ్రత {ref["tmax_today"]}\u00b0C.')
    else:
        en = f'{tag_en}: {ref["rain_48h_mm"]} mm rain in the next 48 h (fertiliser is held at 15 mm or more).'
        te = f'{tag_te}: 48 గంటల్లో {ref["rain_48h_mm"]} మి.మీ. వర్షం.'
    return {"en": en, "te": te, "source": src["name"]}


def build_advice(decision: dict, field: dict) -> dict:
    code = decision["primary"]
    primary = _text(code, decision)

    messages = []
    for domain, c in _domain_codes(decision):
        t = _text(c, decision)
        messages.append({"domain": domain, "code": c, "actionable": c in ACTIONABLE, "en": t["en"], "te": t["te"],
                         "reference": _reference(domain, decision.get("weather_ref") or {})})

    sms = {}
    for lang, head, tail in (("en", "Kisan Mitra:", "Reply 1=Done 2=Skip"), ("te", "కిసాన్ మిత్ర:", "1=అయింది 2=వద్దు")):
        body = " ".join([head] + [m[lang]["text"] for m in messages] + [tail])
        sms[lang] = _sms_stats(lang, body)

    return {
        "code": code,
        "en": primary["en"],
        "te": primary["te"],
        "messages": messages,
        "sms": sms,
        "buttons": BUTTONS,
    }
