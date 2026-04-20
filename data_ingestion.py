"""
╔══════════════════════════════════════════════════════════════════════════╗
║      HVAC COMFORT — DATA INGESTION & FEATURE ENGINEERING PIPELINE v2    ║
║                                                                          ║
║  Sources                                                                 ║
║    1. NeonDB  →  aqi_readings   (temp, humidity, CO2, AQI, PM2.5)       ║
║    2. NeonDB  →  sensor_readings (energy_kwh, voltage, current)         ║
║    3. Google Form CSV export    (survey responses)                       ║
║                                                                          ║
║  Output : merged_comfort_dataset.csv  — PMV-ready feature matrix        ║
║                                                                          ║
║  v2 changes                                                              ║
║    • Exact Google Form column names matched to live form                 ║
║    • Air velocity (v_a) read FROM the form (fan level 0-4 scale)        ║
║    • Footwear CLO column added                                           ║
║    • Age computed from Date of Birth field                               ║
║    • Only prediction-relevant sensor columns retained                    ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import os, math, warnings
import numpy as np
import pandas as pd
from datetime import timezone, timedelta
from sqlalchemy import create_engine

warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════════════════════════
# 0.  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

NEON_DB_URL         = os.getenv("NEON_DB_URL", "postgresql://neondb_owner:npg_JBplV4btz2Fa@ep-summer-tree-a1qoan7c-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require")
GOOGLE_FORM_CSV     = os.getenv("GOOGLE_FORM_CSV", "form_responses.csv")
MERGE_TOLERANCE_MIN = 5       # match survey to nearest sensor bucket within ±N min
LAMBDA_WINDOW       = 30      # rolling window for aPMV calibration
DEFAULT_LAMBDA      = 0.293   # warm-humid climate default (Yao et al. 2009)


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  LOOKUP TABLES  —  CLO, MET, FAN VELOCITY
# ═══════════════════════════════════════════════════════════════════════════════

# Keys = EXACT option text from your Google Form (lowercased for matching)
CLO_MAP = {
    # Inner layers
    "men's briefs / panties":            0.04,
    "baniyan / undershirt / camisole":   0.06,
    "thermal underwear (upper)":         0.20,
    "thermal underwear (lower)":         0.15,
    "none":                              0.00,
    # Upper body
    "short-sleeved t-shirt / polo":      0.08,
    "short-sleeved dress shirt / kurta": 0.19,
    "long-sleeved dress shirt":          0.25,
    "long-sleeved sweatshirt / sweater": 0.36,
    "jacket":                            0.35,
    "shawl/blanket":                     0.40,
    # Lower body
    "short shorts":                      0.06,
    "walking shorts":                    0.08,
    "straight trousers / pants":         0.24,
    "sweatpants":                        0.28,
    "dhoti / pajama / salwar":           0.13,
    "saree":                             0.30,
    # Footwear
    "chappals / sandals / slippers":     0.02,
    "ankle-length socks":                0.02,
    "calf-length socks":                 0.03,
    "shoes":                             0.02,
    "barefoot":                          0.00,
}

MET_MAP = {
    "sleeping":           0.9,
    "reclining":          0.8,
    "seating":            1.0,
    "watching tv":        1.0,
    "listening to music": 1.0,
    "reading":            1.0,
    "writing":            1.8,
    "typing":             1.8,
    "standing":           1.2,
    "walking":            2.3,
}

# Fan level from form linear scale (0-4) → air velocity m/s
FAN_VELOCITY = {0: 0.10, 1: 0.15, 2: 0.25, 3: 0.40, 4: 0.60}

SENSATION_LABEL = {
    -3:"Cold", -2:"Cool", -1:"Slightly Cool",
     0:"Neutral", 1:"Slightly Warm", 2:"Warm", 3:"Hot"
}


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  GOOGLE FORM — COLUMN NAME SUBSTRINGS
#     These act as partial match strings to locate the correct CSV column.
# ═══════════════════════════════════════════════════════════════════════════════

F_TIMESTAMP     = "Timestamp"
F_NAME          = "Name"
F_DOB           = "Date of birth"
F_GENDER        = "Gender:"

F_DELTA_TEMP    = "room temperature to be adjusted"
F_DELTA_FAN     = "adjust the fan speed"
F_COMFORT_LEVEL = "current level of thermal comfort"
F_AIR_VELOCITY  = "current level of Air velociy"
F_SENSATION     = "current thermal sensation"
F_ACTIVITY      = "currently up to"
F_INNER_LAYERS  = "inner layers"
F_UPPER_BODY    = "upper body garments"
F_LOWER_BODY    = "lower body garments"
F_FOOTWEAR      = "footwear"


# Answer → numeric maps (handle both raw numbers and full option text)
DELTA_TEMP_MAP = {
    # numeric (pandas reads "+1" → int 1)
    -1: -1, 0: 0, 1: 1,
    # string variants
    "-1": -1, "0": 0, "+1": 1,
    "(-1) cooler": -1, "(0) no change": 0, "(+1) hotter": 1,
}
DELTA_FAN_MAP = {
    -1: -1, 0: 0, 1: 1,
    "-1": -1, "0": 0, "+1": 1,
    "(-1) less than current speed": -1,
    "(0) no change": 0,
    "(+1) more than current speed": 1,
}
COMFORT_MAP = {
    # numeric
    0: 0, 1: 1, 2: 2, 3: 3,
    # string
    "0": 0, "+1": 1, "+2": 2, "+3": 3,
    "(0) comfortable": 0, "(+1) slightly uncomfortable": 1,
    "(+2) uncomfortable": 2, "(+3) very uncomfortable": 3,
}
SENSATION_MAP = {
    # numeric (most common from CSV)
    -3:-3, -2:-2, -1:-1, 0:0, 1:1, 2:2, 3:3,
    # string with sign prefix
    "-3":-3, "-2":-2, "-1":-1, "0":0, "+1":1, "+2":2, "+3":3,
    # full option text
    "(-3) cold":-3, "(-2) cool":-2, "(-1) slightly cool":-1,
    "(0) neutral":0, "(+1) slightly warm":1, "(+2) warm":2, "(+3) hot":3,
}
GENDER_MAP = {"male": 0, "female": 1, "other": 2}


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  FEATURE ENGINEERING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _checkboxes(cell: str) -> list:
    """Google Forms separates multi-select with ', '  →  list of lowercase tokens."""
    if pd.isna(cell) or str(cell).strip() == "":
        return []
    return [x.strip().lower() for x in str(cell).split(",")]


def compute_clo(inner: str, upper: str, lower: str, footwear: str) -> float:
    """Sum CLO values for all worn garments across all four clothing columns."""
    total = 0.0
    for cell in (inner, upper, lower, footwear):
        for item in _checkboxes(cell):
            val = CLO_MAP.get(item)
            if val is None:                          # fuzzy fallback
                for k, v in CLO_MAP.items():
                    if k in item or item in k:
                        val = v; break
            total += val or 0.0
    return round(max(total, 0.10), 3)               # ASHRAE minimum


def compute_met(cell: str) -> float:
    """Return the highest MET among all checked activities."""
    vals = [MET_MAP.get(a) for a in _checkboxes(cell)]
    vals = [v for v in vals if v]
    return max(vals) if vals else 1.0


def compute_age(dob_str) -> float:
    try:
        dob = pd.to_datetime(dob_str, dayfirst=False)
        return float((pd.Timestamp.today() - dob).days // 365)
    except Exception:
        return float("nan")


def _lookup(cell, mapping: dict, default=float("nan")):
    """
    Handles three lookup cases automatically:
      1. Numeric key (pandas reads '+1' from CSV as int 1)
      2. String with sign prefix ('+1', '-1')
      3. Full option text ('(+1) slightly warm')
    """
    if pd.isna(cell):
        return default
    # Try numeric key first (most common: CSV stores 1, 2, -1 etc.)
    try:
        num = int(float(cell))
        if num in mapping:
            return mapping[num]
    except (ValueError, TypeError):
        pass
    # Fall back to string lookup
    return mapping.get(str(cell).strip().lower(), default)


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  GOOGLE FORM PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def parse_google_form(csv_path: str) -> pd.DataFrame:
    raw = pd.read_csv(csv_path)
    raw.columns = raw.columns.str.strip()

    def _get_col(partial_name):
        for col in raw.columns:
            if partial_name.lower().strip() in col.lower():
                return raw[col]
        return pd.Series([""] * len(raw))

    n = len(raw)
    empty = pd.Series([""] * n)

    df = pd.DataFrame()

    # Timestamp
    df["timestamp_ist"] = pd.to_datetime(_get_col(F_TIMESTAMP), dayfirst=False)
    if df["timestamp_ist"].dt.tz is None:
        df["timestamp_ist"] = df["timestamp_ist"].dt.tz_localize("Asia/Kolkata")
    else:
        df["timestamp_ist"] = df["timestamp_ist"].dt.tz_convert("Asia/Kolkata")

    # Demographics
    df["name"]           = _get_col(F_NAME).fillna("")
    df["dob"]            = _get_col(F_DOB)
    df["age"]            = df["dob"].apply(compute_age)
    df["gender"]         = _get_col(F_GENDER).str.strip()
    df["gender_encoded"] = df["gender"].str.lower().map(GENDER_MAP)

    # Preference / sensation questions
    df["delta_temp_pref"] = _get_col(F_DELTA_TEMP).apply(
                                lambda x: _lookup(x, DELTA_TEMP_MAP))
    df["delta_fan_pref"]  = _get_col(F_DELTA_FAN).apply(
                                lambda x: _lookup(x, DELTA_FAN_MAP))
    df["comfort_level"]   = _get_col(F_COMFORT_LEVEL).apply(
                                lambda x: _lookup(x, COMFORT_MAP))
    df["amv"]             = _get_col(F_SENSATION).apply(
                                lambda x: _lookup(x, SENSATION_MAP))
    df["amv_label"]       = df["amv"].map(SENSATION_LABEL)

    # ── Air velocity — directly from form ──────────────────────────────────────
    # Respondents report the current fan level they perceive (0=Off, 4=AC Blowing)
    # This replaces any assumed default and gives us actual v_a per response.
    df["fan_level_reported"] = pd.to_numeric(
        _get_col(F_AIR_VELOCITY), errors="coerce")
    df["v_a"] = df["fan_level_reported"].map(FAN_VELOCITY).fillna(0.25)

    # ── CLO: sum all four garment columns ──────────────────────────────────────
    inner    = _get_col(F_INNER_LAYERS)
    upper    = _get_col(F_UPPER_BODY)
    lower    = _get_col(F_LOWER_BODY)
    footwear = _get_col(F_FOOTWEAR)

    df["I_cl"] = [
        compute_clo(inner.iloc[i], upper.iloc[i],
                    lower.iloc[i], footwear.iloc[i])
        for i in range(n)
    ]

    # ── MET: from activity checkboxes ─────────────────────────────────────────
    df["M_met"] = _get_col(F_ACTIVITY).apply(compute_met)

    df = df.drop(columns=["dob"]).sort_values("timestamp_ist").reset_index(drop=True)
    print(f"[Form]  {len(df)} responses parsed successfully")
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  DATABASE — only prediction-relevant columns
# ═══════════════════════════════════════════════════════════════════════════════

def get_engine():
    return create_engine(NEON_DB_URL, pool_pre_ping=True)


def _since_clause(since, col="timestamp"):
    if since is None:
        return ""
    ts = pd.Timestamp(since).tz_convert("UTC").strftime("%Y-%m-%d %H:%M:%S+00")
    return f"WHERE {col} > '{ts}'"


def fetch_aqi(engine, since=None) -> pd.DataFrame:
    """
    Kept   : timestamp_ist, temperature, humidity, co2, pm2_5, final_aqi, window_status
    Dropped: pm1_0, pm4_0, pm10, tvoc, voc_index, nox_index,
             aqi_pm25, aqi_pm10, aqi_co2, aqi_tvoc, id, device_id, location
    Why    : Only T, RH, CO2, PM2.5, and summary AQI are PMV / recommendation inputs.
    """
    q = f"""
        SELECT
            timestamp AT TIME ZONE 'Asia/Kolkata' AS timestamp_ist,
            temperature, humidity, co2, pm2_5, final_aqi, window_status
        FROM aqi_readings
        {_since_clause(since)}
        ORDER BY timestamp_ist
    """
    df = pd.read_sql(q, engine)
    df["timestamp_ist"] = pd.to_datetime(df["timestamp_ist"])
    return df


def fetch_energy(engine, since=None) -> pd.DataFrame:
    """
    Kept   : timestamp_ist, energy_kwh, voltage, current, power_w
    Dropped: id, device_id  (irrelevant for analysis)
    """
    q = f"""
        SELECT
            timestamp AT TIME ZONE 'Asia/Kolkata' AS timestamp_ist,
            energy_kwh, voltage, current
        FROM sensor_readings
        {_since_clause(since)}
        ORDER BY timestamp_ist
    """
    df = pd.read_sql(q, engine)
    df["timestamp_ist"] = pd.to_datetime(df["timestamp_ist"])
    df["voltage"]  = pd.to_numeric(df["voltage"],  errors="coerce")
    df["current"]  = pd.to_numeric(df["current"],  errors="coerce")
    df["power_w"]  = (df["voltage"] * df["current"]).fillna(0)
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  SENSOR AGGREGATION — 1-minute buckets
# ═══════════════════════════════════════════════════════════════════════════════

def aggregate_sensors(aqi_df: pd.DataFrame,
                      energy_df: pd.DataFrame,
                      freq: str = "1min") -> pd.DataFrame:
    # AQI numeric columns
    aqi = aqi_df.set_index("timestamp_ist").sort_index()
    num_cols = ["temperature", "humidity", "co2", "pm2_5", "final_aqi"]
    aqi_r = aqi[num_cols].apply(pd.to_numeric, errors="coerce").resample(freq).mean()
    aqi_r["window_status"] = aqi.resample(freq)["window_status"].agg(
        lambda x: x.mode().iloc[0] if len(x) > 0 else np.nan)

    # Energy numeric columns
    en = energy_df.set_index("timestamp_ist").sort_index()
    en_r = en[["energy_kwh", "voltage", "current", "power_w"]] \
             .apply(pd.to_numeric, errors="coerce").resample(freq).mean()
    en_r["energy_kwh_delta"] = en_r["energy_kwh"].diff().clip(lower=0)

    merged = aqi_r.join(en_r, how="outer").reset_index()
    return merged.dropna(subset=["temperature"])   # drop sensor-offline buckets


# ═══════════════════════════════════════════════════════════════════════════════
# 7.  PMV  (Fanger / ISO 7730)
# ═══════════════════════════════════════════════════════════════════════════════

def pmv_ppd(T_a, T_r, RH, v_a, I_cl, M_met):
    M   = M_met * 58.15
    W   = 0
    Rcl = 0.155 * I_cl
    fcl = 1.05 + 0.645 * I_cl if I_cl > 0.5 else 1.0 + 1.29 * I_cl
    pa  = (RH / 100) * math.exp(16.6536 - 4030.18 / (T_a + 235))

    Tcl = T_a
    for _ in range(150):
        hc    = max(2.38 * abs(Tcl - T_a) ** 0.25, 12.1 * math.sqrt(v_a))
        Tcl_n = (35.7 - 0.028 * (M - W)
                 - Rcl * (3.96e-8 * fcl * ((Tcl+273)**4 - (T_r+273)**4)
                          + fcl * hc * (Tcl - T_a)))
        if abs(Tcl_n - Tcl) < 0.001:
            break
        Tcl = Tcl_n

    hc = max(2.38 * abs(Tcl - T_a) ** 0.25, 12.1 * math.sqrt(v_a))
    L  = ((M - W)
          - 3.05e-3 * (5733 - 6.99*(M-W) - pa)
          - 0.42    * ((M-W) - 58.15)
          - 1.7e-5  * M * (5867 - pa)
          - 0.0014  * M * (34 - T_a)
          - 3.96e-8 * fcl * ((Tcl+273)**4 - (T_r+273)**4)
          - fcl * hc * (Tcl - T_a))

    PMV = (0.303 * math.exp(-0.036 * M) + 0.028) * L
    PPD = 100 - 95 * math.exp(-0.03353 * PMV**4 - 0.2179 * PMV**2)
    return round(PMV, 4), round(PPD, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# 8.  ADAPTIVE PMV  (Yao et al. 2009)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_lambda(df: pd.DataFrame, window: int = LAMBDA_WINDOW) -> float:
    sub   = df.dropna(subset=["amv", "pmv_predicted"]).tail(window)
    valid = sub[(sub["pmv_predicted"] != 0) & (sub["amv"] != 0)]
    if len(valid) < 5:
        return DEFAULT_LAMBDA
    return float(
        ((valid["amv"] - valid["pmv_predicted"])
         / (valid["pmv_predicted"] * valid["amv"])).mean()
    )


def adaptive_pmv(pmv: float, lam: float) -> float:
    d = 1 + lam * pmv
    return pmv / d if d != 0 else pmv


# ═══════════════════════════════════════════════════════════════════════════════
# 9.  MERGE
# ═══════════════════════════════════════════════════════════════════════════════

def merge_form_sensors(sensor_agg: pd.DataFrame,
                       form_df:    pd.DataFrame,
                       tol_min:    int = MERGE_TOLERANCE_MIN) -> pd.DataFrame:
    def _tz(df):
        col = "timestamp_ist"
        if df[col].dt.tz is None:
            df[col] = df[col].dt.tz_localize("Asia/Kolkata")
        else:
            df[col] = df[col].dt.tz_convert("Asia/Kolkata")
        return df.sort_values(col)

    s = _tz(sensor_agg.copy())
    f = _tz(form_df.copy())

    out = pd.merge_asof(f, s, on="timestamp_ist",
                        tolerance=pd.Timedelta(minutes=tol_min),
                        direction="nearest")
    matched = out["temperature"].notna().sum()
    print(f"[Merge] {matched}/{len(f)} responses matched (±{tol_min} min)")
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# 10. FEATURE ENGINEERING
# ═══════════════════════════════════════════════════════════════════════════════

def engineer_features(merged: pd.DataFrame) -> pd.DataFrame:
    df = merged.copy()
    df["T_r"] = df["temperature"]          # mean radiant ≈ air temp (indoor)

    def _pmv_row(row):
        try:
            p, d = pmv_ppd(float(row["temperature"]), float(row["T_r"]),
                           float(row["humidity"]),    float(row["v_a"]),
                           float(row["I_cl"]),        float(row["M_met"]))
            return pd.Series({"pmv_predicted": p, "ppd": d})
        except Exception:
            return pd.Series({"pmv_predicted": np.nan, "ppd": np.nan})

    res = df.apply(_pmv_row, axis=1)
    df["pmv_predicted"] = res["pmv_predicted"]
    df["ppd"]           = res["ppd"]

    lam          = compute_lambda(df)
    df["lambda"] = lam
    df["apmv"]   = df["pmv_predicted"].apply(
        lambda p: adaptive_pmv(p, lam) if pd.notna(p) else np.nan)

    df["pmv_error"]       = df["amv"] - df["pmv_predicted"]
    df["in_comfort_zone"] = (df["apmv"].abs() <= 0.5).astype(int)
    df["power_kw"]        = df.get("power_w", pd.Series(0)) / 1000

    if "co2" in df.columns:
        df["co2_flag"] = (df["co2"] > 1000).astype(int)
    if "final_aqi" in df.columns:
        df["aqi_category"] = pd.cut(
            df["final_aqi"],
            bins  =[0, 50, 100, 150, 200, 300, float("inf")],
            labels=["Good","Moderate","Sensitive",
                    "Unhealthy","Very Unhealthy","Hazardous"])

    df["hour_of_day"] = df["timestamp_ist"].dt.hour
    df["day_of_week"] = df["timestamp_ist"].dt.dayofweek
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 11. OUTPUT SCHEMA
# ═══════════════════════════════════════════════════════════════════════════════

OUTPUT_COLUMNS = [
    # Time
    "timestamp_ist", "hour_of_day", "day_of_week",
    # Person
    "name", "age", "gender", "gender_encoded",
    # PMV 6 inputs
    "temperature",        # T_a  — sensor
    "humidity",           # RH   — sensor
    "v_a",                # air velocity m/s — FROM FORM
    "fan_level_reported", # raw 0-4 answer
    "I_cl",               # clo  — feature engineered
    "M_met",              # met  — feature engineered
    "T_r",                # mean radiant temp
    # Environment
    "co2", "pm2_5", "final_aqi", "window_status",
    # Energy
    "energy_kwh", "energy_kwh_delta", "power_w", "power_kw", "voltage", "current",
    # PMV outputs
    "pmv_predicted", "ppd", "lambda", "apmv",
    # Survey ground truth
    "amv", "amv_label", "comfort_level", "delta_temp_pref", "delta_fan_pref",
    # Derived
    "pmv_error", "in_comfort_zone", "co2_flag", "aqi_category",
]


def export_dataset(df: pd.DataFrame, path: str = None) -> pd.DataFrame:
    cols = [c for c in OUTPUT_COLUMNS if c in df.columns]
    out  = df[cols].copy()
    out["timestamp_ist"] = out["timestamp_ist"].dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    if path:
        out.to_csv(path, index=False)
        print(f"\n✅  Saved → {path}  ({len(out)} rows × {len(cols)} cols)")
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# 12. FULL PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def run_full_pipeline(form_csv: str = GOOGLE_FORM_CSV,
                      output:   str = "merged_comfort_dataset.csv"):
    print("=" * 62)
    print("  HVAC COMFORT  —  FULL DATA INGESTION PIPELINE  v2")
    print("=" * 62)
    engine = get_engine()

    print("\n[1/5] Fetching aqi_readings …")
    aqi_df = fetch_aqi(engine)
    print(f"      {len(aqi_df):,} rows | {aqi_df['timestamp_ist'].min()} → "
          f"{aqi_df['timestamp_ist'].max()}")

    print("\n[2/5] Fetching sensor_readings …")
    en_df = fetch_energy(engine)
    print(f"      {len(en_df):,} rows | {en_df['timestamp_ist'].min()} → "
          f"{en_df['timestamp_ist'].max()}")

    print("\n[3/5] Aggregating to 1-minute buckets …")
    sensor_agg = aggregate_sensors(aqi_df, en_df)
    print(f"      {len(sensor_agg):,} buckets")

    print(f"\n[4/5] Parsing Google Form: {form_csv} …")
    form_df = parse_google_form(form_csv)

    print("\n[5/5] Merging + feature engineering …")
    merged   = merge_form_sensors(sensor_agg, form_df)
    featured = engineer_features(merged)

    valid = featured.dropna(subset=["pmv_predicted", "amv"])
    print(f"\n  PMV range      : {featured['pmv_predicted'].min():.2f} → "
                               f"{featured['pmv_predicted'].max():.2f}")
    print(f"  aPMV lambda    : {featured['lambda'].iloc[0]:.4f}")
    print(f"  Comfort zone   : {featured['in_comfort_zone'].mean()*100:.1f}% of responses")
    if len(valid):
        rmse = ((valid["amv"] - valid["pmv_predicted"])**2).mean()**0.5
        print(f"  PMV RMSE vs AMV: {rmse:.3f} sensation units")

    return export_dataset(featured, output)


# ═══════════════════════════════════════════════════════════════════════════════
# 13. INCREMENTAL UPDATE
# ═══════════════════════════════════════════════════════════════════════════════

def run_incremental(form_csv: str = GOOGLE_FORM_CSV,
                    existing: str = "merged_comfort_dataset.csv",
                    output:   str = "merged_comfort_dataset.csv"):
    engine = get_engine()
    since  = None

    if os.path.exists(existing):
        prev   = pd.read_csv(existing)
        since  = pd.Timestamp(prev["timestamp_ist"].max(), tz="Asia/Kolkata")
        print(f"[Incremental] Last record: {since}")

    aqi_df     = fetch_aqi(engine, since=since)
    en_df      = fetch_energy(engine, since=since)
    sensor_agg = aggregate_sensors(aqi_df, en_df)

    form_df = parse_google_form(form_csv)
    if since:
        form_df = form_df[form_df["timestamp_ist"] > since]
    print(f"[Incremental] {len(form_df)} new responses to process")

    if len(form_df) == 0:
        print("[Incremental] Nothing new."); return

    merged   = merge_form_sensors(sensor_agg, form_df)
    featured = engineer_features(merged)
    new_rows = export_dataset(featured)

    if os.path.exists(existing):
        combined = pd.concat([pd.read_csv(existing), new_rows], ignore_index=True)
        combined.to_csv(output, index=False)
        print(f"✅  Appended {len(new_rows)} rows → {output} (total {len(combined)})")
    else:
        new_rows.to_csv(output, index=False)
        print(f"✅  Created {output} ({len(new_rows)} rows)")


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="HVAC Comfort Pipeline v2")
    ap.add_argument("--mode",   choices=["full", "incremental"], default="full")
    ap.add_argument("--form",   default=GOOGLE_FORM_CSV)
    ap.add_argument("--output", default="merged_comfort_dataset.csv")
    args = ap.parse_args()

    if args.mode == "full":
        run_full_pipeline(form_csv=args.form, output=args.output)
    else:
        run_incremental(form_csv=args.form, existing=args.output, output=args.output)