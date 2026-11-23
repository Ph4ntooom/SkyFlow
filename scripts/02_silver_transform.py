"""
02_silver_transform.py
SILVER LAYER (Standardized, Validated & Enriched Data)

Performs:
1. Airport & airline code trimming/uppercasing.
2. Resolution of Kaggle's October 2015 numeric DOT airport IDs to 3-letter IATA codes.
3. OurAirports coordinate fallback enrichment.
4. Safe HHMM local-time parsing (handles '2400' midnight rollover & missing actual times).
5. Exact flight-leg deduplication + data quality audit output.
6. Historical weather alignment within +/- 2 hours (unmatched rows labeled 'Unknown').
"""

import json
import os
import numpy as np
import pandas as pd
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def clean_hhmm_to_hour(hhmm_series):
    """Safely extracts hour (0-23) from HHMM string without treating it as a plain integer."""
    cleaned = hhmm_series.fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    cleaned = cleaned.apply(lambda x: x.zfill(4) if x.isdigit() else "")
    cleaned = cleaned.replace({"2400": "0000"})
    hours = pd.to_numeric(cleaned.str[:2], errors="coerce")
    hours = hours.where((hours >= 0) & (hours <= 23), np.nan)
    return hours, cleaned


def run_silver_transformation():
    os.makedirs(config.SILVER_DIR, exist_ok=True)
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    print("\n=== STEP 2: SILVER LAYER CLEANING & VALIDATION ===")

    flights = config.load_lakehouse_table(config.BRONZE_DIR, "bronze_flights", dtype=str)
    airlines = config.load_lakehouse_table(config.BRONZE_DIR, "bronze_airlines", dtype=str)
    airports = config.load_lakehouse_table(config.BRONZE_DIR, "bronze_airports", dtype=str)

    try:
        ourairports = config.load_lakehouse_table(config.BRONZE_DIR, "bronze_ourairports", dtype=str)
        has_ourairports = True
    except FileNotFoundError:
        ourairports = pd.DataFrame()
        has_ourairports = False

    try:
        weather = config.load_lakehouse_table(config.BRONZE_DIR, "bronze_weather", dtype=str)
        has_weather = True
    except FileNotFoundError:
        weather = pd.DataFrame()
        has_weather = False

    raw_flight_count = len(flights)

    # 1. Clean Airlines
    airlines_clean = pd.DataFrame({
        "iata_carrier": airlines["IATA_CODE"].astype(str).str.strip().str.upper(),
        "airline_name": airlines["AIRLINE"].astype(str).str.strip()
    }).drop_duplicates(subset=["iata_carrier"])

    # 2. Clean Airports & apply OurAirports coordinate fallback + DOT ID mapping
    airports_clean = pd.DataFrame({
        "iata_code": airports["IATA_CODE"].astype(str).str.strip().str.upper(),
        "airport_name": airports["AIRPORT"].astype(str).str.strip(),
        "city_name": airports["CITY"].fillna("Unknown").astype(str).str.strip(),
        "state_code": airports["STATE"].fillna("UNK").astype(str).str.strip().str.upper(),
        "country": airports["COUNTRY"].fillna("USA").astype(str).str.strip(),
        "latitude": pd.to_numeric(airports["LATITUDE"], errors="coerce"),
        "longitude": pd.to_numeric(airports["LONGITUDE"], errors="coerce"),
        "coord_source": "Kaggle_Airports",
        "dot_airport_id": np.nan,
        "timezone_name": "America/New_York"
    }).drop_duplicates(subset=["iata_code"])

    dot_to_iata = {}
    if has_ourairports and not ourairports.empty:
        oa = ourairports.copy()
        oa["iata_code"] = oa["iata_code"].astype(str).str.strip().str.upper()
        oa["latitude_deg"] = pd.to_numeric(oa["latitude_deg"], errors="coerce")
        oa["longitude_deg"] = pd.to_numeric(oa["longitude_deg"], errors="coerce")

        if "dot_airport_id" in oa.columns:
            for _, row in oa.dropna(subset=["dot_airport_id", "iata_code"]).iterrows():
                dot_str = str(row["dot_airport_id"]).strip().replace(".0", "")
                dot_to_iata[dot_str] = row["iata_code"]

        oa_lookup = oa.set_index("iata_code")
        for idx, row in airports_clean.iterrows():
            code = row["iata_code"]
            if code in oa_lookup.index:
                if pd.isna(row["latitude"]) or pd.isna(row["longitude"]):
                    airports_clean.at[idx, "latitude"] = oa_lookup.loc[code, "latitude_deg"]
                    airports_clean.at[idx, "longitude"] = oa_lookup.loc[code, "longitude_deg"]
                    airports_clean.at[idx, "coord_source"] = "OurAirports_Fallback"
                if "timezone" in oa_lookup.columns:
                    airports_clean.at[idx, "timezone_name"] = oa_lookup.loc[code, "timezone"]
                if "dot_airport_id" in oa_lookup.columns:
                    airports_clean.at[idx, "dot_airport_id"] = pd.to_numeric(
                        oa_lookup.loc[code, "dot_airport_id"], errors="coerce"
                    )

    # 3. Clean Flights
    df = pd.DataFrame()
    df["year"] = pd.to_numeric(flights["YEAR"], errors="coerce")
    df["month"] = pd.to_numeric(flights["MONTH"], errors="coerce")
    df["day"] = pd.to_numeric(flights["DAY"], errors="coerce")
    df["flight_date"] = pd.to_datetime(df[["year", "month", "day"]], errors="coerce")
    df = df.dropna(subset=["flight_date"]).copy()

    df["iata_carrier"] = flights.loc[df.index, "AIRLINE"].fillna("UNK").astype(str).str.strip().str.upper()
    df["flight_number"] = flights.loc[df.index, "FLIGHT_NUMBER"].fillna("").astype(str).str.strip()
    df["tail_number"] = flights.loc[df.index, "TAIL_NUMBER"].fillna("UNKNOWN").astype(str).str.strip().str.upper()

    orig_raw = flights.loc[df.index, "ORIGIN_AIRPORT"].fillna("UNK").astype(str).str.strip().str.upper()
    dest_raw = flights.loc[df.index, "DESTINATION_AIRPORT"].fillna("UNK").astype(str).str.strip().str.upper()

    df["origin_iata"] = orig_raw.replace(dot_to_iata)
    df["destination_iata"] = dest_raw.replace(dot_to_iata)

    dep_hour, sched_dep_clean = clean_hhmm_to_hour(flights.loc[df.index, "SCHEDULED_DEPARTURE"])
    _, sched_arr_clean = clean_hhmm_to_hour(flights.loc[df.index, "SCHEDULED_ARRIVAL"])
    df["scheduled_dep_hhmm"] = sched_dep_clean
    df["scheduled_arr_hhmm"] = sched_arr_clean
    df["dep_hour"] = dep_hour.fillna(12).astype(int)

    df["scheduled_elapsed_min"] = pd.to_numeric(flights.loc[df.index, "SCHEDULED_TIME"], errors="coerce")
    df["actual_elapsed_min"] = pd.to_numeric(flights.loc[df.index, "ELAPSED_TIME"], errors="coerce")
    df["air_time_min"] = pd.to_numeric(flights.loc[df.index, "AIR_TIME"], errors="coerce")
    df["departure_delay_min"] = pd.to_numeric(flights.loc[df.index, "DEPARTURE_DELAY"], errors="coerce")
    df["arrival_delay_min"] = pd.to_numeric(flights.loc[df.index, "ARRIVAL_DELAY"], errors="coerce")
    df["distance_miles"] = pd.to_numeric(flights.loc[df.index, "DISTANCE"], errors="coerce")

    df["cancelled_flag"] = pd.to_numeric(flights.loc[df.index, "CANCELLED"], errors="coerce").fillna(0).astype(int)
    df["diverted_flag"] = pd.to_numeric(flights.loc[df.index, "DIVERTED"], errors="coerce").fillna(0).astype(int)
    df["cancellation_code"] = flights.loc[df.index, "CANCELLATION_REASON"].fillna("N").astype(str).str.strip().replace({"": "N", "nan": "N"})

    for src_col, dst_col in [
        ("AIRLINE_DELAY", "carrier_delay_min"),
        ("WEATHER_DELAY", "weather_delay_min"),
        ("AIR_SYSTEM_DELAY", "nas_delay_min"),
        ("SECURITY_DELAY", "security_delay_min"),
        ("LATE_AIRCRAFT_DELAY", "late_aircraft_delay_min"),
    ]:
        if src_col in flights.columns:
            df[dst_col] = pd.to_numeric(flights.loc[df.index, src_col], errors="coerce").fillna(0.0)
        else:
            df[dst_col] = 0.0

    df["is_delayed_15"] = np.where(
        (df["cancelled_flag"] == 0) & (df["arrival_delay_min"] >= config.ON_TIME_THRESHOLD_MIN),
        1,
        0
    )

    def classify_status(row):
        if row["cancelled_flag"] == 1:
            return "Cancelled"
        if row["diverted_flag"] == 1:
            return "Diverted"
        if row["is_delayed_15"] == 1:
            return "Delayed (>=15m)"
        return "On-Time"

    df["status_label"] = df.apply(classify_status, axis=1)

    # 4. Deduplicate exact flight legs
    before_dedup = len(df)
    dedup_cols = ["flight_date", "iata_carrier", "flight_number", "origin_iata", "destination_iata", "scheduled_dep_hhmm"]
    df = df.drop_duplicates(subset=dedup_cols, keep="first").copy()
    duplicates_removed = before_dedup - len(df)

    valid_airports = set(airports_clean["iata_code"])
    matched_orig = df["origin_iata"].isin(valid_airports)
    matched_dest = df["destination_iata"].isin(valid_airports)
    airport_match_rate = round(100.0 * (matched_orig & matched_dest).mean(), 2)

    # 5. Historical Weather Alignment (+/- WEATHER_WINDOW_HOURS tolerance)
    if has_weather and not weather.empty:
        w = weather.copy()
        w["origin_iata"] = w["IATA_CODE"].astype(str).str.strip().str.upper()
        w["obs_ts"] = pd.to_datetime(w["OBS_TIMESTAMP"], errors="coerce")
        w = w.dropna(subset=["obs_ts"]).sort_values("obs_ts")
        w["weather_category"] = w["CONDITION"].fillna("Unknown").astype(str).str.strip()

        df["sched_dep_ts"] = df["flight_date"] + pd.to_timedelta(df["dep_hour"], unit="h")
        df = df.sort_values("sched_dep_ts").reset_index(drop=True)

        merged = pd.merge_asof(
            df,
            w[["origin_iata", "obs_ts", "weather_category"]],
            left_on="sched_dep_ts",
            right_on="obs_ts",
            by="origin_iata",
            tolerance=pd.Timedelta(hours=config.WEATHER_WINDOW_HOURS),
            direction="nearest"
        )
        df["weather_category"] = merged["weather_category"].fillna("Unknown")
        df = df.drop(columns=["sched_dep_ts"])

        weather_clean = pd.DataFrame({
            "station_id": w["STATION_ID"].astype(str).str.strip(),
            "iata_code": w["origin_iata"],
            "observation_ts_utc": w["obs_ts"],
            "temp_celsius": pd.to_numeric(w["TEMP_C"], errors="coerce"),
            "wind_speed_knots": pd.to_numeric(w["WIND_KNOTS"], errors="coerce"),
            "visibility_miles": pd.to_numeric(w["VISIBILITY_MILES"], errors="coerce"),
            "precipitation_mm": pd.to_numeric(w["PRECIP_MM"], errors="coerce"),
            "weather_category": w["weather_category"]
        })
        config.save_lakehouse_table(weather_clean, config.SILVER_DIR, "silver_weather", also_csv=True)
    else:
        df["weather_category"] = "Unknown"

    weather_match_rate = round(100.0 * (df["weather_category"] != "Unknown").mean(), 2)

    config.save_lakehouse_table(df, config.SILVER_DIR, "silver_flights", also_csv=True)
    config.save_lakehouse_table(airlines_clean, config.SILVER_DIR, "silver_airlines", also_csv=True)
    config.save_lakehouse_table(airports_clean, config.SILVER_DIR, "silver_airports", also_csv=True)

    dq_report = {
        "bronze_flight_rows": int(raw_flight_count),
        "silver_clean_flight_rows": int(len(df)),
        "duplicates_removed": int(duplicates_removed),
        "airport_code_match_rate_pct": airport_match_rate,
        "weather_observation_match_rate_pct": weather_match_rate,
        "date_range_min": str(df["flight_date"].min().date()),
        "date_range_max": str(df["flight_date"].max().date()),
        "cancelled_flights_count": int(df["cancelled_flag"].sum()),
        "delayed_15min_flights_count": int(df["is_delayed_15"].sum()),
        "null_arrival_delay_pct": round(100.0 * df["arrival_delay_min"].isna().mean(), 2),
    }

    dq_path = os.path.join(config.OUTPUT_DIR, "silver_data_quality_report.json")
    with open(dq_path, "w") as f:
        json.dump(dq_report, f, indent=2)

    print("Silver Data Quality Summary:")
    for k, v in dq_report.items():
        print(f"  - {k}: {v}")


if __name__ == "__main__":
    run_silver_transformation()
