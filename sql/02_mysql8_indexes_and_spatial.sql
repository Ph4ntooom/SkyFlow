-- ============================================================================
-- 02_mysql8_indexes_and_spatial.sql
-- Composite B-Tree Indexes & MySQL 8.0 InnoDB R-Tree SPATIAL INDEX
-- Run in MySQL Workbench after loading tables
-- ============================================================================

USE airline_dw;

-- 1. Composite B-Tree Indexes on fact_flight
-- Aligned with common Power BI and analytical query slices (Date + Airline, Route + Date)
CREATE INDEX idx_fact_flight_date_airline
    ON fact_flight (date_key, airline_key);

CREATE INDEX idx_fact_flight_route_date
    ON fact_flight (origin_airport_key, destination_airport_key, date_key);

CREATE INDEX idx_fact_flight_weather
    ON fact_flight (weather_key);

CREATE INDEX idx_fact_flight_status
    ON fact_flight (status_key);

-- 2. Rebooking Mart Lookup Index
CREATE INDEX idx_rebooking_flight_run
    ON fact_route_recommendation (disrupted_flight_key, algorithm_run_ts);

-- 3. MySQL 8.0 InnoDB R-Tree Spatial Index on dim_airport(geom)
-- InnoDB implements SPATIAL INDEX as an R-tree structure using minimum bounding rectangles (MBR).
CREATE SPATIAL INDEX idx_dim_airport_geom_rtree
    ON dim_airport (geom);

-- ============================================================================
-- SAMPLE SPATIAL QUERIES FOR EVALUATION DEMO IN MYSQL WORKBENCH
-- ============================================================================

-- Example A: Find alternate airports near Chicago O'Hare (ORD) using the R-Tree SPATIAL INDEX
-- (Discovers Chicago Midway MDW ~23 km away as a co-terminal alternative!)
SELECT
    b.iata_code AS nearby_airport,
    b.airport_name,
    ROUND(ST_Distance_Sphere(a.geom, b.geom) / 1000.0, 2) AS distance_km
FROM dim_airport a
JOIN dim_airport b ON a.iata_code <> b.iata_code
WHERE a.iata_code = 'ORD'
  AND MBRContains(
        ST_SRID(
            ST_MakeEnvelope(
                POINT(a.longitude - 1.5, a.latitude - 1.5),
                POINT(a.longitude + 1.5, a.latitude + 1.5)
            ),
            4326
        ),
        b.geom
      )
  AND ST_Distance_Sphere(a.geom, b.geom) <= 150000
ORDER BY distance_km ASC;

-- Example B: Run EXPLAIN to verify R-Tree index usage ('type = range', 'key = idx_dim_airport_geom_rtree')
EXPLAIN
SELECT b.iata_code, b.airport_name
FROM dim_airport a
JOIN dim_airport b ON a.iata_code <> b.iata_code
WHERE a.iata_code = 'ORD'
  AND MBRContains(
        ST_SRID(
            ST_MakeEnvelope(
                POINT(a.longitude - 1.5, a.latitude - 1.5),
                POINT(a.longitude + 1.5, a.latitude + 1.5)
            ),
            4326
        ),
        b.geom
      );
