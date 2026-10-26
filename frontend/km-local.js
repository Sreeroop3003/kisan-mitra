/* Kisan Mitra - in-browser engine for the standalone HTML build.
 *
 * A line-by-line port of backend/app/{engine,messages,imd,weather}.py plus a fetch() shim for the /api/* routes
 * the console calls, so the single HTML file works without the Python server. The only network use is the optional
 * live forecast (Open-Meteo, direct from the browser). Parity with the Python engine is checked by
 * tools/parity_test.mjs. Keep both in sync: this file is NOT the source of truth, the Python engine is.
 * CROPS and RAIN_CLASSES are injected by tools/build_standalone.py.
 */
(function () {
"use strict";
const CROPS = /*__CROPS__*/ null;
const RAIN_CLASSES = /*__RAIN_CLASSES__*/ null;
const STAGES = ["initial", "development", "mid", "late"];
const PRODUCTS = {N: ["urea", 0.46], P: ["dap", 0.46], K: ["mop", 0.60]};
const SPLIT_CAP = {N: 40.0, P: 30.0, K: 20.0};
const ACRE_M2 = 4046.86, HA_PER_ACRE = 0.404686;
const USEFUL_RAIN = 8.0, FERT_HOLD_RAIN = 15.0, RAIN_EFF = 0.8, LOOKAHEAD = 24, EVENING = 18, HORIZON = 72, HOURS = 96;
const VALID_RANGE = [0.0, 60.0], OUTLIER_PP = 10.0, STUCK_EPS = 0.02;
const IMD_SOURCE = "IMD district forecast (entered manually)";
const LOCATIONS = {
  tirupati: {name: "Tirupati", lat: 13.6288, lon: 79.4192},
  chittoor: {name: "Chittoor", lat: 13.2172, lon: 79.1003},
  madanapalle: {name: "Madanapalle", lat: 13.5503, lon: 78.5029},
};

/* ---------- Python-compatible helpers ---------- */
function pyRound(x, nd = 0) {              // Python round(): correctly rounded, exact ties to even
  if (nd < 0) { const m = Math.pow(10, -nd); return pyRound(x / m, 0) * m; }
  if (!Number.isFinite(x)) return x;
  const a = Math.abs(x), s = a.toFixed(80), dot = s.indexOf("."), tail = s.slice(dot + 1 + nd);
  let r;
  if (/^50*$/.test(tail)) {                // the binary value is exactly half-way: round to the even digit
    const n = Number(s.slice(0, dot) + s.slice(dot + 1, dot + 1 + nd));
    r = (n % 2 === 0 ? n : n + 1) / Math.pow(10, nd);
  } else r = Number(a.toFixed(nd));
  return x < 0 ? -r : r;
}
const pyFixed = (x, nd) => pyRound(x, nd).toFixed(nd);                   // f"{x:.{nd}f}"
const pf = x => (Number.isInteger(x) ? x.toFixed(1) : String(x));         // str(float)
const median = xs => { const a = [...xs].sort((p, q) => p - q), n = a.length; return n % 2 ? a[(n - 1) / 2] : (a[n / 2 - 1] + a[n / 2]) / 2; };
const sum = xs => xs.reduce((a, b) => a + b, 0);
const pad2 = n => String(n).padStart(2, "0");
const parseLocal = iso => { const m = iso.match(/^(\d+)-(\d+)-(\d+)[T ](\d+):(\d+)(?::(\d+))?/); return new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)); };
const isoLocal = d => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
const addH = (d, k) => new Date(d.getFullYear(), d.getMonth(), d.getDate(), d.getHours() + k, d.getMinutes(), d.getSeconds());
const yday = d => Math.round((Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) - Date.UTC(d.getFullYear(), 0, 0)) / 864e5);
const dateKey = d => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const dayDiff = (a, b) => Math.round((Date.UTC(a.getFullYear(), a.getMonth(), a.getDate()) - Date.UTC(b.getFullYear(), b.getMonth(), b.getDate())) / 864e5);

/* ---------- ET0 + water balance (engine.py) ---------- */
function extraterrestrialRadiation(lat, doy) {
  const phi = lat * Math.PI / 180;
  const dr = 1 + 0.033 * Math.cos(2 * Math.PI / 365 * doy);
  const delta = 0.409 * Math.sin(2 * Math.PI / 365 * doy - 1.39);
  const ws = Math.acos(Math.max(-1, Math.min(1, -Math.tan(phi) * Math.tan(delta))));
  const ra = (24 * 60 / Math.PI) * 0.0820 * dr * (ws * Math.sin(phi) * Math.sin(delta) + Math.cos(phi) * Math.cos(delta) * Math.sin(ws));
  return ra / 2.45;
}
function hargreavesEt0(tmax, tmin, lat, doy) {
  const tmean = (tmax + tmin) / 2, ra = extraterrestrialRadiation(lat, doy);
  return Math.max(0, 0.0023 * ra * (tmean + 17.8) * Math.sqrt(Math.max(tmax - tmin, 0)));
}
const ET_W = (() => { const raw = Array.from({length: 24}, (_, h) => (h >= 6 && h <= 18) ? Math.sin(Math.PI * (h - 6) / 12) : 0), t = sum(raw); return raw.map(r => r / t); })();

function simulateMoisture(theta0, crop, stage, start, tmax, tmin, rain, lat, irrigation = {}, hours = HORIZON) {
  const c = CROPS[crop], zr = c.root_depth_mm, fc = c.field_capacity, kc = c.kc[stage];
  let theta = theta0;
  const out = [{hour: 0, time: isoLocal(start), moisture: pyRound(theta, 2), rain_mm: 0.0, et_mm: 0.0}];
  for (let h = 0; h < hours; h++) {
    const t = addH(start, h), day = Math.min(Math.floor(h / 24), tmax.length - 1);
    const et0 = hargreavesEt0(tmax[day], tmin[day], lat, yday(t));
    const etc = et0 * kc * ET_W[t.getHours()], loss = c.loss_mm_day / 24;
    const r = h < rain.length ? rain[h] : 0.0;
    const net = RAIN_EFF * r + (irrigation[h] || 0.0) - etc - loss;
    theta = Math.max(0, Math.min(fc, theta + net / zr * 100));
    out.push({hour: h + 1, time: isoLocal(addH(t, 1)), moisture: pyRound(theta, 2), rain_mm: pyRound(r, 2), et_mm: pyRound(etc, 3)});
  }
  return out;
}

/* ---------- sensor health ---------- */
function checkSensorHealth(probes) {
  const faulty = new Map(), lasts = new Map();
  for (const p of probes) {
    const r = p.readings;
    if (!r || !r.length || r.some(v => v === null || v === undefined || Number.isNaN(v))) { faulty.set(p.id, "missing_data"); continue; }
    lasts.set(p.id, r[r.length - 1]);
    if (!(VALID_RANGE[0] < r[r.length - 1] && r[r.length - 1] <= VALID_RANGE[1])) faulty.set(p.id, "out_of_range");
  }
  const spread = r => Math.max(...r) - Math.min(...r);
  const movers = probes.filter(p => lasts.has(p.id) && spread(p.readings) > 0.2);
  for (const p of probes) {
    if (faulty.has(p.id) || !lasts.has(p.id)) continue;
    if (spread(p.readings) < STUCK_EPS && movers.some(m => m.id !== p.id)) faulty.set(p.id, "stuck");
  }
  const live = [...lasts].filter(([k]) => !faulty.has(k));
  if (live.length >= 3) {
    const med = median(live.map(([, v]) => v));
    for (const [pid, v] of live) if (Math.abs(v - med) > OUTLIER_PP) faulty.set(pid, "disagrees_with_other_probes");
  }
  const healthy = [...lasts.keys()].filter(k => !faulty.has(k));
  let status, now;
  if (!healthy.length) { status = "insufficient"; now = null; }
  else { now = pyRound(median(healthy.map(k => lasts.get(k))), 2); status = faulty.size ? "fault_found" : "ok"; }
  return {status, healthy_ids: healthy, faulty: [...faulty].map(([id, reason]) => ({id, reason})), moisture_now: now};
}

/* ---------- irrigation ---------- */
function adviseIrrigation(m, crop, stage, area, pumpLpm, start, tmax, tmin, rain, lat = 13.63, eff = 0.75) {
  const c = CROPS[crop];
  if (m === null) return {action: "no_data", trajectory: [], trajectory_with_irrigation: null};
  const traj = simulateMoisture(m, crop, stage, start, tmax, tmin, rain, lat);
  const refill = c.refill_point, critical = c.wilting_point + 0.3 * (refill - c.wilting_point);
  const rain24 = pyRound(sum(rain.slice(0, LOOKAHEAD)), 1);
  const cross = arr => { const p = arr.find(q => q.moisture < refill); return p ? p.hour : null; };
  const firstCross = cross(traj);
  const et0Today = hargreavesEt0(tmax[0], tmin[0], lat, yday(start));
  const base = {
    moisture_now: m, field_capacity: c.field_capacity, water_use_mm_day: pyRound(et0Today * c.kc[stage] + c.loss_mm_day, 1),
    refill_point: refill, critical_point: pyRound(critical, 1), rain_next_24h_mm: rain24, hours_to_refill: firstCross,
    trajectory: traj, trajectory_with_irrigation: null,
  };
  if (firstCross === null || firstCross > LOOKAHEAD) {
    if (rain24 >= USEFUL_RAIN) {
      const dryCross = cross(simulateMoisture(m, crop, stage, start, tmax, tmin, new Array(72).fill(0), lat));
      if (dryCross !== null && dryCross <= LOOKAHEAD) return {...base, action: "wait_rain"};
    }
    return {...base, action: "wait"};
  }
  if (rain24 >= USEFUL_RAIN) return {...base, action: "wait_rain"};

  let startH = 0;
  if (m > critical) {
    const slots = [];
    for (let h = 0; h <= firstCross; h++) if (addH(start, h).getHours() === EVENING) slots.push(h);
    if (slots.length) startH = slots[slots.length - 1];
  }
  const action = startH === 0 ? "irrigate_now" : "irrigate_evening";
  const target = c.field_capacity - 2.0;
  const netMm = Math.min(Math.max((target - traj[startH].moisture) / 100 * c.root_depth_mm, 0), c.max_irrigation_mm);
  const litres = (netMm / eff) * area * ACRE_M2;
  const minutes = pumpLpm > 0 ? litres / pumpLpm : 0;
  const startTime = addH(start, startH);
  return {
    ...base, action, start_time: isoLocal(startTime), start_in_hours: startH,
    start_day_offset: dayDiff(startTime, start), net_mm: pyRound(netMm, 1),
    litres: Math.trunc(pyRound(litres, -2)), pump_minutes: (Math.trunc(pyRound(minutes / 5.0) * 5)) || 5,
    trajectory_with_irrigation: simulateMoisture(m, crop, stage, start, tmax, tmin, rain, lat, {[startH]: netMm}),
  };
}

/* ---------- nutrients ---------- */
function adviseNutrients(npk, crop, stage, rain) {
  if (!npk) return {action: "no_data", doses: [], rain_next_48h_mm: 0.0};
  const band = CROPS[crop].npk_band[stage], rain48 = pyRound(sum(rain.slice(0, 48)), 1), status = {}, doses = [];
  for (const nut of ["N", "P", "K"]) {
    const v = npk[nut], [lo, hi] = band[nut];
    status[nut] = {value: v, low: lo, high: hi, state: v < lo ? "low" : (v > hi ? "high" : "ok")};
    if (v < lo) {
      const needed = (lo + hi) / 2 - v, cap = SPLIT_CAP[nut], kg = Math.min(needed, cap), [product, frac] = PRODUCTS[nut];
      doses.push({nutrient: nut, product, kg_per_acre: Math.trunc(pyRound(kg / frac * HA_PER_ACRE)), capped: needed > cap, deficit_kg_ha: pyRound(needed, 1)});
    }
  }
  const action = !doses.length ? "ok" : (rain48 >= FERT_HOLD_RAIN ? "hold_rain" : "apply");
  return {action, status, doses, rain_next_48h_mm: rain48};
}

/* ---------- IMD ---------- */
function classifyRain(mm) {
  if (mm < 0.1) return RAIN_CLASSES[0];
  for (const c of RAIN_CLASSES.slice(1)) if (mm <= c.hi + 0.05) return c;
  return RAIN_CLASSES[RAIN_CLASSES.length - 1];
}
function weatherReference(w, lat = 13.63) {
  const rain = w.rain_mm_hourly;
  let peak = 0; rain.forEach((v, i) => { if (v > rain[peak]) peak = i; });
  const hasPeak = rain.length > 0 && rain[peak] > 0;
  return {
    live: !!w.source, source: w.source || null,
    rain_24h_mm: pyRound(sum(rain.slice(0, 24)), 1), rain_48h_mm: pyRound(sum(rain.slice(0, 48)), 1), rain_72h_mm: pyRound(sum(rain.slice(0, 72)), 1),
    rain_24h_imd_class: classifyRain(sum(rain.slice(0, 24))).label,
    peak_rain_mm_h: hasPeak ? pyRound(rain[peak], 1) : 0.0, peak_rain_in_h: hasPeak ? peak : null,
    tmax_today: pyRound(w.tmax.length ? Math.max(...w.tmax.slice(0, 1)) : 0, 1),
    et0_mm_day: pyRound(hargreavesEt0(w.tmax[0], w.tmin[0], lat, yday(w.start)), 1),
  };
}
function decide(field, probes, npk, w) {
  const health = checkSensorHealth(probes), lat = field.latitude ?? 13.63;
  const irr = adviseIrrigation(health.moisture_now, field.crop, field.stage, field.area_acres, field.pump_lpm, w.start, w.tmax, w.tmin, w.rain_mm_hourly, lat, field.irrigation_efficiency ?? 0.75);
  const fertRain = (w.rain_upper_mm_hourly && w.rain_upper_mm_hourly.length) ? w.rain_upper_mm_hourly : w.rain_mm_hourly;
  const nut = adviseNutrients(npk, field.crop, field.stage, fertRain);
  let primary;
  if (health.status === "insufficient") primary = "no_data";
  else if (irr.action === "irrigate_now" || irr.action === "irrigate_evening") primary = irr.action;
  else if (health.status === "fault_found") primary = "check_sensor";
  else if (nut.action === "apply" || nut.action === "hold_rain") primary = nut.action === "apply" ? "fert_apply" : "fert_hold";
  else if (irr.action === "wait_rain") primary = "wait_rain";
  else primary = "wait";
  return {primary, irrigation: irr, nutrients: nut, sensor_health: health, weather_ref: weatherReference(w, lat)};
}

/* ---------- messages.py ---------- */
const PRODUCT_NAMES = {urea: {en: "Urea", te: "యూరియా"}, dap: {en: "DAP", te: "డి.ఏ.పి."}, mop: {en: "MOP (potash)", te: "ఎం.ఓ.పి. (పొటాష్)"}};
const NUTRIENT_NAMES = {N: {en: "nitrogen", te: "నత్రజని"}, P: {en: "phosphorus", te: "భాస్వరం"}, K: {en: "potash", te: "పొటాష్"}};
const BUTTONS = {en: {done: "Done", skip: "Skip"}, te: {done: "అయింది", skip: "వద్దు"}};
const DAY_TE = {0: "ఈ సాయంత్రం", 1: "రేపు సాయంత్రం"}, DAY_EN = {0: "this evening", 1: "tomorrow evening"};
const ACTIONABLE = new Set(["irrigate_now", "irrigate_evening", "wait_rain", "fert_apply", "fert_hold"]);
const clock12 = d => { const h = d.getHours(); return `${h % 12 || 12}:${pad2(d.getMinutes())} ${h < 12 ? "AM" : "PM"}`; };
const clockIso = iso => clock12(parseLocal(iso));
const smsStats = (lang, body) => { const [single, multi] = lang === "en" ? [160, 153] : [70, 67], n = [...body].length; return {text: body, chars: n, segments: n <= single ? 1 : Math.ceil(n / multi)}; };
const moistureWhy = irr => irr.moisture_now == null ? {en: "", te: ""} :
  {en: ` Moisture ${pyFixed(irr.moisture_now, 1)}% (refill point ${pyFixed(irr.refill_point, 0)}%).`, te: ` తేమ ${pyFixed(irr.moisture_now, 1)}% (కనీసం ${pyFixed(irr.refill_point, 0)}% ఉండాలి).`};

function textFor(code, d) {
  const irr = d.irrigation, nut = d.nutrients, health = d.sensor_health, mw = moistureWhy(irr);
  let en, te;
  if (code === "irrigate_now" || code === "irrigate_evening") {
    const m = irr.pump_minutes, l = irr.litres.toLocaleString("en-US");
    if (code === "irrigate_now") {
      const hh = irr.hours_to_refill;
      let whyEn, whyTe;
      if (irr.moisture_now <= irr.critical_point) { whyEn = "Soil moisture is very low and no useful rain is due."; whyTe = "నేలలో తేమ బాగా తగ్గింది, వర్షం రాదు."; }
      else if (!hh) { whyEn = "Soil moisture is already below the safe level and no useful rain is due."; whyTe = "నేలలో తేమ సురక్షిత స్థాయి కంటే తగ్గింది, వర్షం రాదు."; }
      else { whyEn = `Soil moisture will drop below the safe level in about ${hh} hours, before the evening, and no useful rain is due.`; whyTe = `సుమారు ${hh} గంటల్లో నేలలో తేమ తగ్గిపోతుంది, సాయంత్రం వరకు ఆగలేము, వర్షం రాదు.`; }
      en = {title: "Irrigate now", text: `Irrigate now. Run the pump for ${m} minutes (${l} litres).`, why: whyEn + mw.en};
      te = {title: "ఇప్పుడే నీరు పెట్టండి", text: `ఇప్పుడే నీరు పెట్టండి. పంపు ${m} నిమిషాలు నడపండి (${l} లీటర్లు).`, why: whyTe + mw.te};
    } else {
      const off = irr.start_day_offset, t = clockIso(irr.start_time), h = irr.hours_to_refill;
      en = {title: `Irrigate ${DAY_EN[off]}`, text: `Irrigate ${DAY_EN[off]} at ${t}. Run the pump for ${m} minutes (${l} litres).`, why: `Soil moisture will drop below the safe level in about ${h} hours and no useful rain is due.` + mw.en};
      te = {title: `${DAY_TE[off]} నీరు పెట్టండి`, text: `${DAY_TE[off]} ${t} కి నీరు పెట్టండి. పంపు ${m} నిమిషాలు నడపండి (${l} లీటర్లు).`, why: `సుమారు ${h} గంటల్లో నేలలో తేమ తగ్గిపోతుంది, వర్షం రాదు.` + mw.te};
    }
  } else if (code === "wait_rain") {
    const mm = pf(irr.rain_next_24h_mm);
    en = {title: "Do not irrigate - rain coming", text: "Do not irrigate today. Rain is coming.", why: `About ${mm} mm of rain is due in the next 24 hours and will refill the soil.` + mw.en};
    te = {title: "నీరు పెట్టకండి - వర్షం వస్తుంది", text: "ఈ రోజు నీరు పెట్టకండి. వర్షం వస్తుంది.", why: `24 గంటల్లో సుమారు ${mm} మి.మీ. వర్షం వస్తుంది, నేలకు నీరు అందుతుంది.` + mw.te};
  } else if (code === "water_ok") {
    en = {title: "No water needed", text: "No water needed today. Soil moisture is enough.", why: "Moisture stays above the refill point for the next 24 hours." + mw.en};
    te = {title: "నీరు అవసరం లేదు", text: "ఈ రోజు నీరు అవసరం లేదు. నేలలో తేమ సరిపోతుంది.", why: "రేపటి వరకు తేమ సరిపోతుంది." + mw.te};
  } else if (code === "check_sensor") {
    const ids = health.faulty.map(f => f.id).join(", ");
    en = {title: "Check sensor", text: `Sensor ${ids} is not working properly. Please check it.`, why: "Its readings do not match the other sensors, so advice uses the healthy ones only."};
    te = {title: "సెన్సార్ చూడండి", text: `సెన్సార్ ${ids} సరిగా పనిచేయడం లేదు. దయచేసి సెన్సార్ చూడండి.`, why: "ఈ సెన్సార్ రీడింగ్ మిగతా వాటితో సరిపోలడం లేదు, అందుకే సలహా మిగతా సెన్సార్ల ఆధారంగా ఇచ్చాము."};
  } else if (code === "no_data") {
    en = {title: "Sensors not working", text: "We cannot give advice now. Please check your sensors.", why: "No sensor is giving a reliable reading."};
    te = {title: "సెన్సార్లు పనిచేయడం లేదు", text: "ఇప్పుడు సలహా ఇవ్వలేము. దయచేసి సెన్సార్లు చూడండి.", why: "ఏ సెన్సార్ నుంచీ సరైన రీడింగ్ రావడం లేదు."};
  } else if (code === "fert_apply") {
    const itemsEn = nut.doses.map(x => `${PRODUCT_NAMES[x.product].en} ${x.kg_per_acre} kg`).join(", ");
    const itemsTe = nut.doses.map(x => `${PRODUCT_NAMES[x.product].te} ${x.kg_per_acre} కిలోలు`).join(", ");
    const lowEn = nut.doses.map(x => NUTRIENT_NAMES[x.nutrient].en).join(" and "), lowTe = nut.doses.map(x => NUTRIENT_NAMES[x.nutrient].te).join(", ");
    const capped = nut.doses.some(x => x.capped);
    en = {title: "Apply fertiliser", text: `Apply ${itemsEn} per acre.`, why: `Soil ${lowEn} is low.` + (capped ? " Dose is limited to a safe amount per application; repeat in 2-3 weeks if advised." : "")};
    te = {title: "ఎరువు వేయండి", text: `ఎకరాకు ${itemsTe} వేయండి.`, why: `నేలలో ${lowTe} తక్కువగా ఉంది.` + (capped ? " ఒకేసారి ఎక్కువ వేయకూడదు, 2-3 వారాల తర్వాత మళ్ళీ చెబుతాము." : "")};
  } else if (code === "fert_hold") {
    const mm = pf(nut.rain_next_48h_mm);
    en = {title: "Hold fertiliser - heavy rain coming", text: "Apply fertiliser after the rain. If applied now it will wash away.", why: `About ${mm} mm of rain is due within 48 hours. Holding saves money and keeps nitrogen out of the water.`};
    te = {title: "ఎరువు వేయకండి - భారీ వర్షం వస్తుంది", text: "ఎరువు వర్షం తర్వాత వేయండి. ఇప్పుడు వేస్తే కొట్టుకుపోతుంది.", why: `48 గంటల్లో సుమారు ${mm} మి.మీ. వర్షం వస్తుంది.`};
  } else if (code === "fert_ok") {
    en = {title: "No fertiliser needed", text: "No fertiliser needed now. Nutrients are in range.", why: "Nitrogen, phosphorus and potash are all inside the target range."};
    te = {title: "ఎరువు అవసరం లేదు", text: "ఇప్పుడు ఎరువు అవసరం లేదు. పోషకాలు సరిపడా ఉన్నాయి.", why: "నత్రజని, భాస్వరం, పొటాష్ అన్నీ సరిపడా ఉన్నాయి."};
  } else {
    en = {title: "All good - no action needed", text: "No irrigation or fertiliser needed today.", why: "Soil moisture is enough for the next 24 hours and nutrients are in range." + mw.en};
    te = {title: "అంతా బాగుంది - ఏమీ చేయక్కర్లేదు", text: "ఈ రోజు నీరు, ఎరువు ఏమీ అవసరం లేదు.", why: "రేపటి వరకు నేలలో తేమ సరిపోతుంది, పోషకాలు సరిగ్గా ఉన్నాయి." + mw.te};
  }
  return {en, te};
}
function domainCodes(d) {
  const irr = d.irrigation, nut = d.nutrients, health = d.sensor_health, out = [];
  if (health.status !== "insufficient") out.push(["water", {irrigate_now: "irrigate_now", irrigate_evening: "irrigate_evening", wait_rain: "wait_rain"}[irr.action] || "water_ok"]);
  if (nut.action !== "no_data") out.push(["nutrient", {apply: "fert_apply", hold_rain: "fert_hold"}[nut.action] || "fert_ok"]);
  if (health.status === "insufficient") out.push(["device", "no_data"]);
  else if (health.status === "fault_found") out.push(["device", "check_sensor"]);
  return out;
}
function referenceFor(domain, ref) {
  const src = ref.source;
  if (!ref.live || !src || (domain !== "water" && domain !== "nutrient")) return null;
  const when = src.fetched_at ? clockIso(src.fetched_at) : "";
  const label = src.name.toLowerCase().includes("forecast") ? src.name : `${src.name} forecast`;
  const tagEn = `${label}, ${src.location}` + (when ? `, synced ${when}` : ""), tagTe = `${src.location} వాతావరణ అంచనా (${src.name})` + (when ? `, ${when}` : "");
  let en, te;
  if (domain === "water") {
    en = `${tagEn}: ${pf(ref.rain_24h_mm)} mm rain in the next 24 h, high ${pf(ref.tmax_today)}°C, crop water use driven by ET0 ${pf(ref.et0_mm_day)} mm/day. IMD rain category (24 h): ${ref.rain_24h_imd_class}.`;
    te = `${tagTe}: 24 గంటల్లో ${pf(ref.rain_24h_mm)} మి.మీ. వర్షం, గరిష్ఠ ఉష్ణోగ్రత ${pf(ref.tmax_today)}°C.`;
  } else {
    en = `${tagEn}: ${pf(ref.rain_48h_mm)} mm rain in the next 48 h (fertiliser is held at 15 mm or more).`;
    te = `${tagTe}: 48 గంటల్లో ${pf(ref.rain_48h_mm)} మి.మీ. వర్షం.`;
  }
  return {en, te, source: src.name};
}
function buildAdvice(d) {
  const primary = textFor(d.primary, d), messages = domainCodes(d).map(([domain, c]) => {
    const t = textFor(c, d);
    return {domain, code: c, actionable: ACTIONABLE.has(c), en: t.en, te: t.te, reference: referenceFor(domain, d.weather_ref || {})};
  });
  const sms = {};
  for (const [lang, head, tail] of [["en", "Kisan Mitra:", "Reply 1=Done 2=Skip"], ["te", "కిసాన్ మిత్ర:", "1=అయింది 2=వద్దు"]])
    sms[lang] = smsStats(lang, [head, ...messages.map(m => m[lang].text), tail].join(" "));
  return {code: d.primary, en: primary.en, te: primary.te, messages, sms, buttons: BUTTONS};
}

/* ---------- forecast builders (imd.py, weather.py) ---------- */
function normalise(source, location, start, temp, rain, prob, daily, approxAfter = null) {
  const padTo = (xs, v) => { const a = [...xs]; while (a.length < HOURS) a.push(v); return a.slice(0, HOURS); };
  return {
    source, location, start: isoLocal(start), fetched_at: isoLocal(new Date()),
    temp_c_hourly: padTo(temp, temp.length ? temp[temp.length - 1] : 25.0).map(x => pyRound(x, 1)),
    rain_mm_hourly: padTo(rain, 0.0).map(x => pyRound(x, 2)),
    precip_prob: prob ? padTo(prob, 0) : null,
    daily: daily.slice(0, 4), tmax: daily.slice(0, 4).map(d => d.tmax), tmin: daily.slice(0, 4).map(d => d.tmin),
    approximate_after_hours: approxAfter,
  };
}
const diurnal = (tmax, tmin, hour) => (tmax + tmin) / 2 + (tmax - tmin) / 2 * Math.sin(Math.PI * (hour - 9) / 12);
function forecastFromImd(location, days, start, issuedOn) {
  if (!(days.length >= 1 && days.length <= 5)) throw new Error("give 1 to 5 forecast days");
  const cats = days.map((d, i) => {
    if (!(10 <= d.tmin && d.tmin < d.tmax && d.tmax <= 50)) throw new Error(`day ${i + 1}: need 10 <= tmin < tmax <= 50 (got ${pf(d.tmin)}, ${pf(d.tmax)})`);
    const c = RAIN_CLASSES.find(x => x.id === d.rain_category);
    if (!c) throw new Error(`unknown IMD rain category '${d.rain_category}'; use one of [${RAIN_CLASSES.map(x => "'" + x.id + "'").join(", ")}]`);
    return c;
  });
  const temp = [], rain = [], upper = [];
  for (let k = 0; k < HOURS; k++) {
    const t = addH(start, k), di = dayDiff(t, start);
    const d = di < days.length ? days[di] : days[days.length - 1];
    temp.push(diurnal(d.tmax, d.tmin, t.getHours()));
    rain.push(di < days.length ? cats[di].lo / 24 : 0); upper.push(di < days.length ? cats[di].hi / 24 : 0);
  }
  const daily = days.map((d, i) => ({date: dateKey(addH(start, 24 * i)), tmax: d.tmax, tmin: d.tmin, rain_mm: cats[i].lo, rain_mm_upper: cats[i].hi, imd_category: cats[i].label}));
  const out = normalise(IMD_SOURCE, (LOCATIONS[location] || {name: location}).name, start, temp, rain, null, daily);
  out.rain_upper_mm_hourly = upper.map(x => pyRound(x, 2)); out.issued_on = issuedOn || null; out.entered_manually = true; out.approximate_after_hours = null;
  return out;
}
async function openMeteo(location) {
  const loc = LOCATIONS[location], qs = new URLSearchParams({latitude: loc.lat, longitude: loc.lon, hourly: "temperature_2m,precipitation,precipitation_probability",
    daily: "temperature_2m_max,temperature_2m_min,precipitation_sum", forecast_days: 5, timezone: "Asia/Kolkata"});
  const r = await window.__kmRealFetch(`https://api.open-meteo.com/v1/forecast?${qs}`);
  if (!r.ok) throw new Error("Open-Meteo HTTP " + r.status);
  const data = await r.json(), ist = new Date(Date.now() + 5.5 * 3600e3);       // India wall-clock time, whatever the laptop's zone
  const nowKey = `${ist.getUTCFullYear()}-${pad2(ist.getUTCMonth() + 1)}-${pad2(ist.getUTCDate())}T${pad2(ist.getUTCHours())}:00`;
  const idx = Math.max(0, data.hourly.time.indexOf(nowKey)), start = parseLocal(nowKey);
  const sl = key => data.hourly[key].slice(idx, idx + HOURS).map(x => +x || 0);
  const d = data.daily, daily = d.time.map((t, i) => ({date: t, tmax: d.temperature_2m_max[i], tmin: d.temperature_2m_min[i], rain_mm: +d.precipitation_sum[i] || 0}));
  return normalise("Open-Meteo", loc.name, start, sl("temperature_2m"), sl("precipitation"), sl("precipitation_probability"), daily);
}

/* ---------- /api/* shim ---------- */
const json = (body, status = 200) => new Response(JSON.stringify(body), {status, headers: {"Content-Type": "application/json"}});
const err = (status, detail) => json({detail}, status);
function runAdvice(req) {
  const f = {latitude: 13.63, irrigation_efficiency: 0.75, ...req.field}, w = req.weather;
  if (!CROPS[f.crop]) return err(422, `crop must be one of ${JSON.stringify(Object.keys(CROPS))}`);
  if (!STAGES.includes(f.stage)) return err(422, `stage must be one of ${JSON.stringify(STAGES)}`);
  if (!(f.area_acres > 0) || !(f.pump_lpm > 0)) return err(422, "area_acres and pump_lpm must be positive");
  if (!w || !w.tmax || !w.tmax.length || !w.tmin || !w.rain_mm_hourly) return err(422, "weather needs tmax, tmin and rain_mm_hourly");
  const weather = {source: w.source || null, start: parseLocal(w.start), tmax: w.tmax, tmin: w.tmin, rain_mm_hourly: w.rain_mm_hourly, rain_upper_mm_hourly: w.rain_upper_mm_hourly || null};
  const decision = decide(f, req.probes, req.npk || null, weather);
  return json({decision, advice: buildAdvice(decision), field: f});
}
async function route(url, init) {
  const u = new URL(url, "http://local"), path = u.pathname, method = ((init && init.method) || "GET").toUpperCase();
  const body = init && init.body ? JSON.parse(init.body) : null;
  try {
    if (path === "/api/crops") return json(CROPS);
    if (path === "/api/imd/rain-classes") return json({classes: RAIN_CLASSES, source: "India Meteorological Department"});
    if (path === "/api/advice" && method === "POST") return runAdvice(body);
    if (path === "/api/feedback" && method === "POST") {
      try { const k = "km-feedback", a = JSON.parse(localStorage.getItem(k) || "[]"); a.push({ts: isoLocal(new Date()), ...body}); localStorage.setItem(k, JSON.stringify(a.slice(-500))); } catch (e) { /* storage unavailable */ }
      return json({saved: true});
    }
    if (path === "/api/weather/imd" && method === "POST") {
      const loc = body.location || "tirupati";
      if (!LOCATIONS[loc]) return err(422, `location must be one of ${JSON.stringify(Object.keys(LOCATIONS))}`);
      const start = new Date(); start.setMinutes(0, 0, 0);
      try { return json(forecastFromImd(loc, body.days || [], start, body.issued_on)); } catch (e) { return err(422, e.message); }
    }
    if (path === "/api/weather/live") {
      const loc = u.searchParams.get("location") || "tirupati", prov = u.searchParams.get("provider") || "auto";
      if (!LOCATIONS[loc]) return err(422, `location must be one of ${JSON.stringify(Object.keys(LOCATIONS))}`);
      let out;
      try { out = await openMeteo(loc); } catch (e) { return err(503, "weather service unreachable: " + e.message); }
      if (prov === "accuweather") out.warning = "AccuWeather needs the Python server and an API key; showing Open-Meteo instead.";
      return json(out);
    }
    return err(404, "not available in the standalone build: " + path);
  } catch (e) { return err(500, String(e.message || e)); }
}
window.__kmRealFetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  const url = typeof input === "string" ? input : (input && input.url) || "";
  return url.startsWith("/api/") ? route(url, init) : window.__kmRealFetch(input, init);
};
window.KMLocal = {decide, buildAdvice, forecastFromImd, classifyRain, pyRound, CROPS, RAIN_CLASSES, _route: route};
})();
