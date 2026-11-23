"""
03_gold_warehouse.py
GOLD LAYER (Dimensional Star + Snowflake Schema & Data Marts for MySQL 8.0)

Builds:
1. Snowflake Geography Hierarchy: dim_region -> dim_state -> dim_city -> dim_airport
2. Core Star Dimensions: dim_date, dim_airline, dim_flight_status, dim_weather
3. Fact Tables: fact_flight, fact_weather_observation
4. Operations Mart Summary: mart_operations_route_summary
5. Exports all Gold tables to data/gold/ (CSV for Power BI Desktop) and loads them
   into MySQL 8.0 using native bulk loading (LOAD DATA LOCAL INFILE + fast batched fallback).

Tip: Pass --mysql-only to skip regenerating Gold CSVs and immediately load existing
     data/gold/*.csv files into MySQL 8.0.
"""

import os
import time
import numpy as np
import pandas as pd
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

STATE_TO_REGION = {
    "CT": "Northeast", "ME": "Northeast", "MA": "Northeast", "NH": "Northeast",
    "RI": "Northeast", "VT": "Northeast", "NJ": "Northeast", "NY": "Northeast", "PA": "Northeast",
    "IL": "Midwest", "IN": "Midwest", "MI": "Midwest", "OH": "Midwest", "WI": "Midwest",
    "IA": "Midwest", "KS": "Midwest", "MN": "Midwest", "MO": "Midwest", "NE": "Midwest",
    "ND": "Midwest", "SD": "Midwest",
    "DE": "South", "FL": "South", "GA": "South", "MD": "South", "NC": "South",
    "SC": "South", "VA": "South", "DC": "South", "WV": "South", "AL": "South",
    "KY": "South", "MS": "South", "TN": "South", "AR": "South", "LA": "South",
    "OK": "South", "TX": "South",
    "AZ": "West", "CO": "West", "ID": "West", "MT": "West", "NV": "West",
    "NM": "West", "UT": "West", "WY": "West", "AK": "West", "CA": "West",
    "HI": "West", "OR": "West", "WA": "West"
}

CANCELLATION_DESC = {
    "A": "Carrier / Airline",
    "B": "Weather",
    "C": "National Air System (NAS)",
    "D": "Security",
    "N": "Not Cancelled"
}


def build_gold_warehouse(mysql_only=False):
    os.makedirs(config.GOLD_DIR, exist_ok=True)
    print("\n=== STEP 3: GOLD DIMENSIONAL WAREHOUSE & MARTS ===")

    table_names = [
        "dim_region", "dim_state", "dim_city", "dim_airport",
        "dim_date", "dim_airline", "dim_flight_status", "dim_weather",
        "fact_flight", "mart_operations_route_summary", "fact_weather_observation"
    ]

    if mysql_only:
        print("(--mysql-only mode: reading pre-exported Gold CSVs from data/gold/)")
        tables = {}
        for name in table_names:
            csv_path = os.path.join(config.GOLD_DIR, f"{name}.csv")
            if os.path.exists(csv_path):
                tables[name] = pd.read_csv(csv_path, low_memory=False)
                print(f"  -> Loaded {name}.csv ({len(tables[name]):,} rows)")
        load_to_mysql(tables)
        return

    flights = config.load_lakehouse_table(config.SILVER_DIR, "silver_flights")
    flights["flight_date"] = pd.to_datetime(flights["flight_date"])
    airlines = config.load_lakehouse_table(config.SILVER_DIR, "silver_airlines")
    airports = config.load_lakehouse_table(config.SILVER_DIR, "silver_airports")

    # 1. Snowflake Geography Dimensions (Region -> State -> City -> Airport)
    airports["region_name"] = airports["state_code"].map(STATE_TO_REGION).fillna("Other/Territory")

    dim_region = pd.DataFrame({
        "region_name": sorted(airports["region_name"].unique())
    })
    dim_region["region_key"] = range(1, len(dim_region) + 1)
    dim_region = dim_region[["region_key", "region_name"]]

    dim_state = airports[["state_code", "region_name"]].drop_duplicates().sort_values("state_code").reset_index(drop=True)
    dim_state = dim_state.merge(dim_region, on="region_name", how="left")
    dim_state["state_key"] = range(1, len(dim_state) + 1)
    dim_state = dim_state[["state_key", "state_code", "region_key"]]

    dim_city = airports[["city_name", "state_code"]].drop_duplicates().sort_values(["state_code", "city_name"]).reset_index(drop=True)
    dim_city = dim_city.merge(dim_state[["state_key", "state_code"]], on="state_code", how="left")
    dim_city["city_key"] = range(1, len(dim_city) + 1)
    dim_city = dim_city[["city_key", "city_name", "state_key"]]

    all_flight_iatas = set(flights["origin_iata"]).union(set(flights["destination_iata"]))
    missing_iatas = sorted(all_flight_iatas - set(airports["iata_code"]))
    if missing_iatas:
        unknown_rows = pd.DataFrame([{
            "iata_code": code,
            "airport_name": f"Unknown Airport ({code})",
            "city_name": "Unknown",
            "state_code": "UNK",
            "country": "USA",
            "latitude": np.nan,
            "longitude": np.nan,
            "coord_source": "Unmatched",
            "dot_airport_id": np.nan,
            "timezone_name": "UTC"
        } for code in missing_iatas])
        airports = pd.concat([airports, unknown_rows], ignore_index=True)

    dim_airport = airports.merge(dim_state[["state_key", "state_code"]], on="state_code", how="left")
    dim_airport = dim_airport.merge(dim_city, on=["city_name", "state_key"], how="left")
    dim_airport = dim_airport.sort_values("iata_code").reset_index(drop=True)
    dim_airport["airport_key"] = range(1, len(dim_airport) + 1)
    dim_airport = dim_airport[[
        "airport_key", "iata_code", "dot_airport_id", "airport_name",
        "city_key", "country", "latitude", "longitude", "timezone_name", "coord_source"
    ]]

    # 2. Star Schema Dimensions (Date, Airline, FlightStatus, Weather)
    unique_dates = pd.Series(sorted(flights["flight_date"].unique()))
    dow_series = unique_dates.dt.weekday + 1
    dim_date = pd.DataFrame({
        "date_key": unique_dates.dt.strftime("%Y%m%d").astype(int),
        "full_date": unique_dates.dt.date,
        "year": unique_dates.dt.year,
        "quarter": unique_dates.dt.quarter,
        "month": unique_dates.dt.month,
        "month_name": unique_dates.dt.strftime("%B"),
        "day_of_month": unique_dates.dt.day,
        "day_of_week": dow_series,
        "day_name": unique_dates.dt.strftime("%A"),
        "is_weekend": dow_series.isin([6, 7]).astype(int)
    })

    all_carriers = set(flights["iata_carrier"]) - set(airlines["iata_carrier"])
    if all_carriers:
        extra_al = pd.DataFrame([{"iata_carrier": c, "airline_name": f"Unknown Carrier ({c})"} for c in all_carriers])
        airlines = pd.concat([airlines, extra_al], ignore_index=True)
    dim_airline = airlines.sort_values("iata_carrier").reset_index(drop=True)
    dim_airline["airline_key"] = range(1, len(dim_airline) + 1)
    dim_airline = dim_airline[["airline_key", "iata_carrier", "airline_name"]]

    flights["cancellation_code"] = flights["cancellation_code"].fillna("N")
    status_combos = flights[["status_label", "cancellation_code"]].drop_duplicates().sort_values(["status_label", "cancellation_code"]).reset_index(drop=True)
    status_combos["cancellation_desc"] = status_combos["cancellation_code"].map(CANCELLATION_DESC).fillna("Other")
    status_combos["status_key"] = range(1, len(status_combos) + 1)
    dim_flight_status = status_combos[["status_key", "status_label", "cancellation_code", "cancellation_desc"]]

    severity_map = {"Unknown": 0, "Clear": 1, "Rain": 2, "HighWind": 2, "Fog/LowVis": 3, "Snow/Ice": 3}
    weather_cats = sorted(set(flights["weather_category"].unique()).union({"Unknown", "Clear"}))
    dim_weather = pd.DataFrame({
        "weather_key": range(1, len(weather_cats) + 1),
        "weather_category": weather_cats,
        "severity_level": [severity_map.get(c, 2) for c in weather_cats]
    })

    # 3. FactFlight
    fact = flights.copy()
    fact["date_key"] = fact["flight_date"].dt.strftime("%Y%m%d").astype(int)

    airline_map = dict(zip(dim_airline["iata_carrier"], dim_airline["airline_key"]))
    airport_map = dict(zip(dim_airport["iata_code"], dim_airport["airport_key"]))
    weather_map = dict(zip(dim_weather["weather_category"], dim_weather["weather_key"]))

    fact["airline_key"] = fact["iata_carrier"].map(airline_map)
    fact["origin_airport_key"] = fact["origin_iata"].map(airport_map)
    fact["destination_airport_key"] = fact["destination_iata"].map(airport_map)
    fact["weather_key"] = fact["weather_category"].map(weather_map)

    fact = fact.merge(
        dim_flight_status[["status_key", "status_label", "cancellation_code"]],
        on=["status_label", "cancellation_code"],
        how="left"
    )

    fact = fact.reset_index(drop=True)
    fact["flight_key"] = range(1, len(fact) + 1)

    fact_flight = fact[[
        "flight_key", "date_key", "airline_key", "origin_airport_key", "destination_airport_key",
        "status_key", "weather_key", "flight_number", "tail_number",
        "origin_iata", "destination_iata", "scheduled_dep_hhmm", "scheduled_arr_hhmm", "dep_hour",
        "scheduled_elapsed_min", "actual_elapsed_min", "air_time_min",
        "departure_delay_min", "arrival_delay_min", "distance_miles",
        "carrier_delay_min", "weather_delay_min", "nas_delay_min",
        "security_delay_min", "late_aircraft_delay_min",
        "is_delayed_15", "cancelled_flag", "diverted_flag"
    ]]

    try:
        sw = config.load_lakehouse_table(config.SILVER_DIR, "silver_weather")
        sw["airport_key"] = sw["iata_code"].map(airport_map)
        sw["weather_key"] = sw["weather_category"].map(weather_map)
        sw["weather_obs_key"] = range(1, len(sw) + 1)
        fact_weather = sw[[
            "weather_obs_key", "airport_key", "weather_key", "station_id",
            "observation_ts_utc", "temp_celsius", "wind_speed_knots",
            "visibility_miles", "precipitation_mm", "weather_category"
        ]]
    except FileNotFoundError:
        fact_weather = pd.DataFrame()

    # 4. Operations Mart Aggregates
    ops_route_mart = (
        fact_flight.groupby(["origin_iata", "destination_iata"], as_index=False)
        .agg(
            total_flights=("flight_key", "count"),
            delayed_flights=("is_delayed_15", "sum"),
            cancelled_flights=("cancelled_flag", "sum"),
            diverted_flights=("diverted_flag", "sum"),
            avg_arrival_delay_min=("arrival_delay_min", "mean"),
            median_arrival_delay_min=("arrival_delay_min", "median"),
            avg_scheduled_elapsed_min=("scheduled_elapsed_min", "mean"),
            avg_distance_miles=("distance_miles", "mean"),
            total_carrier_delay_min=("carrier_delay_min", "sum"),
            total_weather_delay_min=("weather_delay_min", "sum"),
            total_nas_delay_min=("nas_delay_min", "sum"),
            total_late_aircraft_delay_min=("late_aircraft_delay_min", "sum"),
        )
    )
    ops_route_mart["route_pair"] = ops_route_mart["origin_iata"] + "-" + ops_route_mart["destination_iata"]
    ops_route_mart["delay_rate_pct"] = (
        100.0 * ops_route_mart["delayed_flights"]
        / (ops_route_mart["total_flights"] - ops_route_mart["cancelled_flights"]).replace(0, np.nan)
    ).round(2)
    ops_route_mart["cancellation_rate_pct"] = (
        100.0 * ops_route_mart["cancelled_flights"] / ops_route_mart["total_flights"]
    ).round(2)
    ops_route_mart["avg_arrival_delay_min"] = ops_route_mart["avg_arrival_delay_min"].round(2)

    tables = {
        "dim_region": dim_region,
        "dim_state": dim_state,
        "dim_city": dim_city,
        "dim_airport": dim_airport,
        "dim_date": dim_date,
        "dim_airline": dim_airline,
        "dim_flight_status": dim_flight_status,
        "dim_weather": dim_weather,
        "fact_flight": fact_flight,
        "mart_operations_route_summary": ops_route_mart,
    }
    if not fact_weather.empty:
        tables["fact_weather_observation"] = fact_weather

    for name, table_df in tables.items():
        config.save_lakehouse_table(table_df, config.GOLD_DIR, name, also_csv=True)
        print(f"  -> Exported Gold table {name}: {len(table_df):,} rows")

    if config.LOAD_TO_MYSQL:
        load_to_mysql(tables)


def _bulk_load_fact_flight_infile(raw_conn, csv_path):
    """Uses MySQL 8.0 native LOAD DATA LOCAL INFILE to load 5.8M rows in seconds."""
    sql_path = csv_path.replace("\\", "/")
    line_sep = "\\r\\n" if os.name == "nt" else "\\n"
    load_sql = f"""
        LOAD DATA LOCAL INFILE '{sql_path}'
        INTO TABLE fact_flight
        CHARACTER SET utf8mb4
        FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
        LINES TERMINATED BY '{line_sep}'
        IGNORE 1 LINES
        (
            flight_key, date_key, airline_key, origin_airport_key, destination_airport_key,
            status_key, weather_key, @flight_number, @tail_number,
            @origin_iata, @destination_iata, @scheduled_dep_hhmm, @scheduled_arr_hhmm, @dep_hour,
            @scheduled_elapsed_min, @actual_elapsed_min, @air_time_min,
            @departure_delay_min, @arrival_delay_min, @distance_miles,
            @carrier_delay_min, @weather_delay_min, @nas_delay_min,
            @security_delay_min, @late_aircraft_delay_min,
            is_delayed_15, cancelled_flag, diverted_flag
        )
        SET
            flight_number           = NULLIF(@flight_number, ''),
            tail_number             = NULLIF(@tail_number, ''),
            origin_iata             = NULLIF(@origin_iata, ''),
            destination_iata        = NULLIF(@destination_iata, ''),
            scheduled_dep_hhmm      = NULLIF(@scheduled_dep_hhmm, ''),
            scheduled_arr_hhmm      = NULLIF(@scheduled_arr_hhmm, ''),
            dep_hour                = NULLIF(@dep_hour, ''),
            scheduled_elapsed_min   = NULLIF(@scheduled_elapsed_min, ''),
            actual_elapsed_min      = NULLIF(@actual_elapsed_min, ''),
            air_time_min            = NULLIF(@air_time_min, ''),
            departure_delay_min     = NULLIF(@departure_delay_min, ''),
            arrival_delay_min       = NULLIF(@arrival_delay_min, ''),
            distance_miles          = NULLIF(@distance_miles, ''),
            carrier_delay_min       = COALESCE(NULLIF(@carrier_delay_min, ''), 0),
            weather_delay_min       = COALESCE(NULLIF(@weather_delay_min, ''), 0),
            nas_delay_min           = COALESCE(NULLIF(@nas_delay_min, ''), 0),
            security_delay_min      = COALESCE(NULLIF(@security_delay_min, ''), 0),
            late_aircraft_delay_min = COALESCE(NULLIF(@late_aircraft_delay_min, ''), 0);
    """
    with raw_conn.cursor() as cur:
        cur.execute(load_sql)
    raw_conn.commit()


def _fast_executemany_load(raw_conn, table_name, df, batch_size=50000):
    """Fast batched executemany loader with live progress output (no giant SQL strings)."""
    cols = list(df.columns)
    col_list = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    insert_sql = f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})"

    total = len(df)
    clean_df = df.astype(object).where(pd.notna(df), None)
    records = list(clean_df.itertuples(index=False, name=None))

    with raw_conn.cursor() as cur:
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            cur.executemany(insert_sql, records[start:end])
            raw_conn.commit()
            print(f"     ... {end:,} / {total:,} rows inserted ({100.0 * end / total:.1f}%)")


def load_to_mysql(tables):
    from sqlalchemy import create_engine, text
    print("\nLoading Gold tables into MySQL 8.0...")
    t0 = time.time()

    engine = create_engine(
        config.DB_URI,
        connect_args={"local_infile": 1}
    )

    load_order = [
        "dim_region", "dim_state", "dim_city", "dim_airport",
        "dim_date", "dim_airline", "dim_flight_status", "dim_weather",
        "fact_flight", "fact_weather_observation"
    ]

    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
        try:
            conn.execute(text("SET GLOBAL local_infile = 1;"))
        except Exception:
            pass

        for t in load_order:
            if t not in tables:
                continue
            conn.execute(text(f"TRUNCATE TABLE {t};"))

            if t == "dim_airport":
                # In MySQL 8.0, POINT(X, Y) with SRID 4326 expects X = longitude [-180, 180] and Y = latitude [-90, 90]
                for _, r in tables["dim_airport"].iterrows():
                    lat = float(r["latitude"]) if pd.notna(r["latitude"]) else 0.0
                    lon = float(r["longitude"]) if pd.notna(r["longitude"]) else 0.0
                    lat = max(-90.0, min(90.0, lat))
                    lon = max(-180.0, min(180.0, lon))
                    conn.execute(
                        text("""
                            INSERT INTO dim_airport (
                                airport_key, iata_code, dot_airport_id, airport_name,
                                city_key, country, latitude, longitude, timezone_name, coord_source, geom
                            ) VALUES (
                                :airport_key, :iata_code, :dot_airport_id, :airport_name,
                                :city_key, :country, :latitude, :longitude, :timezone_name, :coord_source,
                                ST_SRID(POINT(:lon_pt, :lat_pt), 4326)
                            )
                        """),
                        {
                            "airport_key": int(r["airport_key"]),
                            "iata_code": r["iata_code"],
                            "dot_airport_id": int(r["dot_airport_id"]) if pd.notna(r["dot_airport_id"]) else None,
                            "airport_name": r["airport_name"],
                            "city_key": int(r["city_key"]) if pd.notna(r["city_key"]) else None,
                            "country": r["country"],
                            "latitude": float(r["latitude"]) if pd.notna(r["latitude"]) else None,
                            "longitude": float(r["longitude"]) if pd.notna(r["longitude"]) else None,
                            "timezone_name": r["timezone_name"],
                            "coord_source": r["coord_source"],
                            "lon_pt": lon,
                            "lat_pt": lat,
                        },
                    )
                print(f"  -> Loaded dim_airport ({len(tables['dim_airport']):,} rows with SRID 4326 spatial points)")
            elif t in ("fact_flight", "fact_weather_observation"):
                continue  # Loaded right after dimensions using raw bulk loader
            else:
                tables[t].to_sql(t, conn, if_exists="append", index=False, chunksize=5000)
                print(f"  -> Loaded {t} ({len(tables[t]):,} rows)")

    # Bulk-load fact tables outside SQLAlchemy's slow multi-value INSERT compiler
    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0;")
            cur.execute("SET UNIQUE_CHECKS = 0;")
            cur.execute("SET autocommit = 0;")

        if "fact_flight" in tables:
            ff_csv = os.path.join(config.GOLD_DIR, "fact_flight.csv")
            print(f"  -> Bulk-loading fact_flight ({len(tables['fact_flight']):,} rows)...")
            t_ff = time.time()
            try:
                _bulk_load_fact_flight_infile(raw_conn, ff_csv)
                print(f"  -> Loaded fact_flight ({len(tables['fact_flight']):,} rows) via LOAD DATA LOCAL INFILE in {time.time() - t_ff:.1f}s!")
            except Exception as e:
                print(f"     [Note] LOAD DATA LOCAL INFILE unavailable ({e}); using fast batched executemany...")
                _fast_executemany_load(raw_conn, "fact_flight", tables["fact_flight"], batch_size=50000)
                print(f"  -> Loaded fact_flight ({len(tables['fact_flight']):,} rows) in {time.time() - t_ff:.1f}s!")

        if "fact_weather_observation" in tables and not tables["fact_weather_observation"].empty:
            fw_df = tables["fact_weather_observation"]
            print(f"  -> Bulk-loading fact_weather_observation ({len(fw_df):,} rows)...")
            _fast_executemany_load(raw_conn, "fact_weather_observation", fw_df, batch_size=12500)
            print(f"  -> Loaded fact_weather_observation ({len(fw_df):,} rows)")

        with raw_conn.cursor() as cur:
            cur.execute("SET UNIQUE_CHECKS = 1;")
            cur.execute("SET FOREIGN_KEY_CHECKS = 1;")
        raw_conn.commit()
    finally:
        raw_conn.close()

    print(f"MySQL 8.0 load complete in {time.time() - t0:.1f}s!")


if __name__ == "__main__":
    mysql_only_flag = "--mysql-only" in sys.argv
    build_gold_warehouse(mysql_only=mysql_only_flag)
