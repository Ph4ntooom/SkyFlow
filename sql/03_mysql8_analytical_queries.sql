-- ============================================================================
-- 03_mysql8_analytical_queries.sql
-- Ready-to-run BI & Evaluation Queries for MySQL 8.0 Workbench Demo
-- ============================================================================

USE airline_dw;

-- 1. Executive KPIs Overview
SELECT
    COUNT(*) AS total_flights,
    ROUND(100.0 * SUM(CASE WHEN cancelled_flag = 0 AND is_delayed_15 = 0 THEN 1 ELSE 0 END) / COUNT(*), 2) AS on_time_pct,
    ROUND(AVG(arrival_delay_min), 2) AS avg_arrival_delay_min,
    ROUND(100.0 * SUM(cancelled_flag) / COUNT(*), 2) AS cancellation_rate_pct,
    SUM(diverted_flag) AS diverted_flights
FROM fact_flight;

-- 2. Airline Delay & Cancellation Leaderboard
SELECT
    al.iata_carrier,
    al.airline_name,
    COUNT(*) AS total_flights,
    ROUND(100.0 * SUM(f.is_delayed_15) / NULLIF(SUM(1 - f.cancelled_flag), 0), 2) AS delay_rate_pct,
    ROUND(AVG(f.arrival_delay_min), 2) AS avg_arrival_delay_min,
    ROUND(100.0 * SUM(f.cancelled_flag) / COUNT(*), 2) AS cancellation_rate_pct
FROM fact_flight f
JOIN dim_airline al ON f.airline_key = al.airline_key
GROUP BY al.iata_carrier, al.airline_name
ORDER BY delay_rate_pct DESC;

-- 3. Worst-Performing Origin-Destination Routes
SELECT
    CONCAT(orig.iata_code, ' -> ', dest.iata_code) AS route,
    COUNT(*) AS flight_volume,
    ROUND(AVG(f.arrival_delay_min), 2) AS avg_delay_min,
    ROUND(100.0 * SUM(f.cancelled_flag) / COUNT(*), 2) AS cancel_rate_pct
FROM fact_flight f
JOIN dim_airport orig ON f.origin_airport_key = orig.airport_key
JOIN dim_airport dest ON f.destination_airport_key = dest.airport_key
GROUP BY orig.iata_code, dest.iata_code
HAVING COUNT(*) >= 10
ORDER BY avg_delay_min DESC
LIMIT 10;

-- 4. Snowflake Geography Rollup (Region -> State -> Airport Delay Summary)
SELECT
    r.region_name,
    s.state_code,
    COUNT(*) AS total_departures,
    ROUND(100.0 * SUM(f.is_delayed_15) / NULLIF(SUM(1 - f.cancelled_flag), 0), 2) AS delay_rate_pct,
    ROUND(AVG(f.arrival_delay_min), 2) AS avg_arrival_delay_min
FROM fact_flight f
JOIN dim_airport orig ON f.origin_airport_key = orig.airport_key
JOIN dim_city c ON orig.city_key = c.city_key
JOIN dim_state s ON c.state_key = s.state_key
JOIN dim_region r ON s.region_key = r.region_key
GROUP BY r.region_name, s.state_code
ORDER BY delay_rate_pct DESC;

-- 5. Delay Association Across Weather Categories
SELECT
    w.weather_category,
    COUNT(*) AS flight_count,
    ROUND(100.0 * SUM(f.is_delayed_15) / NULLIF(SUM(1 - f.cancelled_flag), 0), 2) AS delay_rate_pct,
    ROUND(AVG(f.arrival_delay_min), 2) AS avg_arrival_delay_min,
    ROUND(100.0 * SUM(f.cancelled_flag) / COUNT(*), 2) AS cancel_rate_pct
FROM fact_flight f
JOIN dim_weather w ON f.weather_key = w.weather_key
GROUP BY w.weather_category
ORDER BY delay_rate_pct DESC;

-- 6. Inspect A* Rebooking Candidate Routes (Rebooking Mart)
SELECT *
FROM mart_rebooking_recommendations
ORDER BY disrupted_flight_key, candidate_rank
LIMIT 15;
