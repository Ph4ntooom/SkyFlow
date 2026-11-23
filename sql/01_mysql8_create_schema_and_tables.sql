-- ============================================================================
-- 01_mysql8_create_schema_and_tables.sql
-- Airline Flight Delay Prediction & Rebooking Data Warehouse
-- Target DBMS: MySQL 8.0 (Run directly in MySQL Workbench)
-- Section: BDS-5A | Group: Ahad (24K-2510), Amail (24K-2575), Aayan Hassan (24K-2561)
-- ============================================================================

CREATE DATABASE IF NOT EXISTS airline_dw
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE airline_dw;

-- Temporarily disable FK checks while dropping existing tables for clean re-runs
SET FOREIGN_KEY_CHECKS = 0;

DROP VIEW IF EXISTS mart_rebooking_recommendations;
DROP VIEW IF EXISTS mart_operations_summary;

DROP TABLE IF EXISTS fact_route_recommendation;
DROP TABLE IF EXISTS fact_weather_observation;
DROP TABLE IF EXISTS fact_flight;

DROP TABLE IF EXISTS dim_weather;
DROP TABLE IF EXISTS dim_flight_status;
DROP TABLE IF EXISTS dim_airline;
DROP TABLE IF EXISTS dim_date;

DROP TABLE IF EXISTS dim_airport;
DROP TABLE IF EXISTS dim_city;
DROP TABLE IF EXISTS dim_state;
DROP TABLE IF EXISTS dim_region;
DROP TABLE IF EXISTS etl_load_audit;

SET FOREIGN_KEY_CHECKS = 1;

-- ----------------------------------------------------------------------------
-- 1. SNOWFLAKE GEOGRAPHY HIERARCHY (dim_region -> dim_state -> dim_city -> dim_airport)
-- Normalizes geography so city, state, and region names are not repeated per airport.
-- ----------------------------------------------------------------------------

CREATE TABLE dim_region (
    region_key      INT AUTO_INCREMENT PRIMARY KEY,
    region_name     VARCHAR(50) NOT NULL UNIQUE
) ENGINE=InnoDB;

CREATE TABLE dim_state (
    state_key       INT AUTO_INCREMENT PRIMARY KEY,
    state_code      VARCHAR(10) NOT NULL UNIQUE,
    region_key      INT,
    CONSTRAINT fk_state_region FOREIGN KEY (region_key) REFERENCES dim_region(region_key)
) ENGINE=InnoDB;

CREATE TABLE dim_city (
    city_key        INT AUTO_INCREMENT PRIMARY KEY,
    city_name       VARCHAR(100) NOT NULL,
    state_key       INT,
    UNIQUE KEY uq_city_state (city_name, state_key),
    CONSTRAINT fk_city_state FOREIGN KEY (state_key) REFERENCES dim_state(state_key)
) ENGINE=InnoDB;

CREATE TABLE dim_airport (
    airport_key     INT AUTO_INCREMENT PRIMARY KEY,
    iata_code       VARCHAR(10) NOT NULL UNIQUE,   -- Business key (e.g., ATL, ORD, JFK)
    dot_airport_id  INT NULL,                      -- Numeric DOT code (October 2015 Kaggle anomaly)
    airport_name    VARCHAR(200) NOT NULL,
    city_key        INT,
    country         VARCHAR(50) DEFAULT 'USA',
    latitude        DOUBLE NULL,
    longitude       DOUBLE NULL,
    timezone_name   VARCHAR(64),
    coord_source    VARCHAR(50),                   -- 'Kaggle_Airports' or 'OurAirports_Fallback'
    geom            POINT SRID 4326 NOT NULL,      -- MySQL 8.0 Spatial Point for R-Tree index
    CONSTRAINT fk_airport_city FOREIGN KEY (city_key) REFERENCES dim_city(city_key)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- 2. STAR SCHEMA DIMENSIONS
-- ----------------------------------------------------------------------------

CREATE TABLE dim_date (
    date_key        INT PRIMARY KEY,               -- Smart surrogate key: YYYYMMDD
    full_date       DATE NOT NULL UNIQUE,
    year            INT NOT NULL,
    quarter         INT NOT NULL,
    month           INT NOT NULL,
    month_name      VARCHAR(20) NOT NULL,
    day_of_month    INT NOT NULL,
    day_of_week     INT NOT NULL,                  -- 1=Monday ... 7=Sunday
    day_name        VARCHAR(20) NOT NULL,
    is_weekend      TINYINT(1) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE dim_airline (
    airline_key     INT AUTO_INCREMENT PRIMARY KEY,
    iata_carrier    VARCHAR(10) NOT NULL UNIQUE,   -- Business key (e.g., AA, DL, UA)
    airline_name    VARCHAR(150) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE dim_flight_status (
    status_key          INT AUTO_INCREMENT PRIMARY KEY,
    status_label        VARCHAR(50) NOT NULL,      -- On-Time, Delayed (>=15m), Cancelled, Diverted
    cancellation_code   VARCHAR(5) NOT NULL,       -- A=Carrier, B=Weather, C=NAS, D=Security, N=None
    cancellation_desc   VARCHAR(100),
    UNIQUE KEY uq_status_cancel (status_label, cancellation_code)
) ENGINE=InnoDB;

CREATE TABLE dim_weather (
    weather_key         INT AUTO_INCREMENT PRIMARY KEY,
    weather_category    VARCHAR(50) NOT NULL UNIQUE, -- Clear, Rain, Snow/Ice, Fog/LowVis, HighWind, Unknown
    severity_level      INT NOT NULL                 -- 0=Unknown, 1=Clear, 2=Moderate, 3=Severe
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- 3. FACT TABLES
-- ----------------------------------------------------------------------------

-- Grain: One row per reported flight leg (cancelled flights kept with NULL delay minutes)
CREATE TABLE fact_flight (
    flight_key              BIGINT AUTO_INCREMENT PRIMARY KEY,
    date_key                INT NOT NULL,
    airline_key             INT NOT NULL,
    origin_airport_key      INT NOT NULL,
    destination_airport_key INT NOT NULL,
    status_key              INT NOT NULL,
    weather_key             INT NOT NULL,

    -- Business keys retained for operational traceability
    flight_number           VARCHAR(20),
    tail_number             VARCHAR(20),
    origin_iata             VARCHAR(10),
    destination_iata        VARCHAR(10),
    scheduled_dep_hhmm      VARCHAR(4),
    scheduled_arr_hhmm      VARCHAR(4),
    dep_hour                INT,

    -- Measures
    scheduled_elapsed_min   DECIMAL(8,2) NULL,
    actual_elapsed_min      DECIMAL(8,2) NULL,
    air_time_min            DECIMAL(8,2) NULL,
    departure_delay_min     DECIMAL(8,2) NULL,     -- NULL if cancelled before departure
    arrival_delay_min       DECIMAL(8,2) NULL,     -- NULL if cancelled or diverted without arrival
    distance_miles          DECIMAL(8,2) NULL,

    -- Delay Cause Breakdown (minutes)
    carrier_delay_min       DECIMAL(8,2) DEFAULT 0,
    weather_delay_min       DECIMAL(8,2) DEFAULT 0,
    nas_delay_min           DECIMAL(8,2) DEFAULT 0,
    security_delay_min      DECIMAL(8,2) DEFAULT 0,
    late_aircraft_delay_min DECIMAL(8,2) DEFAULT 0,

    -- Indicator Flags
    is_delayed_15           TINYINT(1) NOT NULL DEFAULT 0,
    cancelled_flag          TINYINT(1) NOT NULL DEFAULT 0,
    diverted_flag           TINYINT(1) NOT NULL DEFAULT 0,

    CONSTRAINT fk_ff_date    FOREIGN KEY (date_key) REFERENCES dim_date(date_key),
    CONSTRAINT fk_ff_airline FOREIGN KEY (airline_key) REFERENCES dim_airline(airline_key),
    CONSTRAINT fk_ff_origin  FOREIGN KEY (origin_airport_key) REFERENCES dim_airport(airport_key),
    CONSTRAINT fk_ff_dest    FOREIGN KEY (destination_airport_key) REFERENCES dim_airport(airport_key),
    CONSTRAINT fk_ff_status  FOREIGN KEY (status_key) REFERENCES dim_flight_status(status_key),
    CONSTRAINT fk_ff_weather FOREIGN KEY (weather_key) REFERENCES dim_weather(weather_key)
) ENGINE=InnoDB;

-- Grain: One row per weather station observation timestamp
CREATE TABLE fact_weather_observation (
    weather_obs_key     BIGINT AUTO_INCREMENT PRIMARY KEY,
    airport_key         INT NULL,
    weather_key         INT NULL,
    station_id          VARCHAR(30) NOT NULL,
    observation_ts_utc  DATETIME NOT NULL,
    temp_celsius        DECIMAL(6,2) NULL,
    wind_speed_knots    DECIMAL(6,2) NULL,
    visibility_miles    DECIMAL(6,2) NULL,
    precipitation_mm    DECIMAL(6,2) NULL,
    weather_category    VARCHAR(50),

    CONSTRAINT fk_fwo_airport FOREIGN KEY (airport_key) REFERENCES dim_airport(airport_key),
    CONSTRAINT fk_fwo_weather FOREIGN KEY (weather_key) REFERENCES dim_weather(weather_key)
) ENGINE=InnoDB;

-- Grain: One candidate alternate itinerary per disrupted flight and A* algorithm run
CREATE TABLE fact_route_recommendation (
    recommendation_key      BIGINT AUTO_INCREMENT PRIMARY KEY,
    disrupted_flight_key    BIGINT NOT NULL,
    origin_airport_key      INT NULL,
    destination_airport_key INT NULL,
    origin_iata             VARCHAR(10) NOT NULL,
    origin_airport_name     VARCHAR(200),
    destination_iata        VARCHAR(10) NOT NULL,
    destination_airport_name VARCHAR(200),
    candidate_rank          INT NOT NULL,
    airport_sequence        VARCHAR(100) NOT NULL, -- e.g. 'ORD -> ATL -> JFK'
    connection_count        INT NOT NULL,
    estimated_flight_min    DECIMAL(8,2) NOT NULL,
    connection_buffer_min   DECIMAL(8,2) NOT NULL,
    delay_risk_penalty_min  DECIMAL(8,2) NOT NULL,
    total_path_score        DECIMAL(10,2) NOT NULL,
    algorithm_run_ts        DATETIME NOT NULL,
    recommendation_status   VARCHAR(80) DEFAULT 'Candidate Path (Live Seats & Fares Unverified)',

    CONSTRAINT fk_frr_origin FOREIGN KEY (origin_airport_key) REFERENCES dim_airport(airport_key),
    CONSTRAINT fk_frr_dest   FOREIGN KEY (destination_airport_key) REFERENCES dim_airport(airport_key)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- 4. SUPPORTING DATA MARTS (SQL Views over Fact & Dimensions)
-- ----------------------------------------------------------------------------

-- Operations Mart: Aggregated KPIs by Date, Airline, Origin, Destination, and Weather
CREATE VIEW mart_operations_summary AS
SELECT
    d.full_date,
    d.year,
    d.month,
    d.month_name,
    d.day_name,
    al.iata_carrier,
    al.airline_name,
    orig.iata_code AS origin_iata,
    orig.airport_name AS origin_airport,
    dest.iata_code AS destination_iata,
    dest.airport_name AS destination_airport,
    CONCAT(orig.iata_code, '-', dest.iata_code) AS route_pair,
    w.weather_category,
    COUNT(*) AS total_flights,
    SUM(CASE WHEN f.cancelled_flag = 0 AND f.is_delayed_15 = 0 THEN 1 ELSE 0 END) AS on_time_flights,
    SUM(f.is_delayed_15) AS delayed_flights,
    SUM(f.cancelled_flag) AS cancelled_flights,
    SUM(f.diverted_flag) AS diverted_flights,
    ROUND(AVG(f.arrival_delay_min), 2) AS avg_arrival_delay_min,
    ROUND(AVG(f.departure_delay_min), 2) AS avg_departure_delay_min,
    SUM(f.carrier_delay_min) AS total_carrier_delay_min,
    SUM(f.weather_delay_min) AS total_weather_delay_min,
    SUM(f.nas_delay_min) AS total_nas_delay_min,
    SUM(f.security_delay_min) AS total_security_delay_min,
    SUM(f.late_aircraft_delay_min) AS total_late_aircraft_delay_min
FROM fact_flight f
JOIN dim_date d ON f.date_key = d.date_key
JOIN dim_airline al ON f.airline_key = al.airline_key
JOIN dim_airport orig ON f.origin_airport_key = orig.airport_key
JOIN dim_airport dest ON f.destination_airport_key = dest.airport_key
JOIN dim_weather w ON f.weather_key = w.weather_key
GROUP BY
    d.full_date, d.year, d.month, d.month_name, d.day_name,
    al.iata_carrier, al.airline_name,
    orig.iata_code, orig.airport_name,
    dest.iata_code, dest.airport_name,
    w.weather_category;

-- Rebooking Mart: Candidate alternate paths for disrupted flights
CREATE VIEW mart_rebooking_recommendations AS
SELECT
    r.recommendation_key,
    r.disrupted_flight_key,
    r.origin_iata,
    COALESCE(orig.airport_name, r.origin_airport_name) AS origin_airport_name,
    r.destination_iata,
    COALESCE(dest.airport_name, r.destination_airport_name) AS destination_airport_name,
    r.candidate_rank,
    r.airport_sequence,
    r.connection_count,
    r.estimated_flight_min,
    r.connection_buffer_min,
    r.delay_risk_penalty_min,
    r.total_path_score,
    r.algorithm_run_ts,
    r.recommendation_status
FROM fact_route_recommendation r
LEFT JOIN dim_airport orig ON r.origin_airport_key = orig.airport_key
LEFT JOIN dim_airport dest ON r.destination_airport_key = dest.airport_key;

-- ----------------------------------------------------------------------------
-- 5. ETL AUDIT & RECONCILIATION LOG TABLE
-- ----------------------------------------------------------------------------

CREATE TABLE etl_load_audit (
    audit_id            INT AUTO_INCREMENT PRIMARY KEY,
    pipeline_stage      VARCHAR(50) NOT NULL,
    table_name          VARCHAR(100) NOT NULL,
    source_rows         BIGINT,
    loaded_rows         BIGINT,
    rejected_rows       BIGINT DEFAULT 0,
    file_checksum       VARCHAR(128),
    load_timestamp      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes               TEXT
) ENGINE=InnoDB;
