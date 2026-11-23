"""
04_astar_rebooking.py
AI COMPONENT: A* SEARCH FOR ALTERNATE-ROUTE REBOOKING RECOMMENDATIONS

- Nodes: Airports with valid IATA codes and coordinates (excludes unmapped numeric DOT codes)
- Edges: Valid historical flight corridors from Operations Mart
- Edge Cost g(n): scheduled_flight_time + connection_buffer (45 min) + historical_delay_penalty
- Heuristic h(n): Great-circle Haversine distance(n, goal) / MAX_JET_SPEED_MPH * 60 (admissible lower bound)
- Outputs: fact_route_recommendation.csv & mart_rebooking_recommendations.csv (plus MySQL load if enabled)
"""

import heapq
import math
import os
from datetime import datetime, timezone
import pandas as pd
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance between two (lat, lon) coordinates in miles."""
    if any(pd.isna(v) for v in [lat1, lon1, lat2, lon2]):
        return 0.0
    r_miles = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * r_miles * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def build_flight_graph(dim_airport, route_mart):
    """Builds an adjacency list of valid flight corridors and airport coordinates."""
    coords = {}
    for _, row in dim_airport.iterrows():
        code = str(row["iata_code"]).strip()
        # Keep only valid 3-letter IATA airports with known coordinates for routing
        if len(code) == 3 and code.isalpha() and pd.notna(row["latitude"]) and pd.notna(row["longitude"]):
            coords[code] = (float(row["latitude"]), float(row["longitude"]))

    graph = {code: [] for code in coords}
    for _, row in route_mart.iterrows():
        u = str(row["origin_iata"]).strip()
        v = str(row["destination_iata"]).strip()
        if u == v or u not in coords or v not in coords:
            continue

        sched_min = float(row["avg_scheduled_elapsed_min"]) if pd.notna(row["avg_scheduled_elapsed_min"]) else 120.0
        avg_delay = max(0.0, float(row["avg_arrival_delay_min"])) if pd.notna(row["avg_arrival_delay_min"]) else 0.0
        cancel_pct = float(row["cancellation_rate_pct"]) if pd.notna(row["cancellation_rate_pct"]) else 0.0

        delay_penalty = round(config.DELAY_PENALTY_WEIGHT * avg_delay + 1.5 * cancel_pct, 2)

        graph[u].append({
            "to": v,
            "flight_min": round(sched_min, 2),
            "delay_penalty": delay_penalty,
            "flight_count": int(row["total_flights"])
        })

    return graph, coords


def astar_find_routes(graph, coords, origin, destination, top_k=3, exclude_direct=True):
    """Finds up to top_k lowest-cost alternate paths from origin to destination using A* search."""
    if origin not in graph or destination not in graph:
        return []

    goal_lat, goal_lon = coords[destination]

    def heuristic(airport_code):
        lat, lon = coords[airport_code]
        dist_miles = haversine_miles(lat, lon, goal_lat, goal_lon)
        return (dist_miles / config.MAX_JET_SPEED_MPH) * 60.0

    start_h = heuristic(origin)
    pq = [(start_h, 0.0, origin, [origin], 0.0, 0.0, 0.0)]
    results = []

    while pq and len(results) < top_k:
        f_score, g_score, curr, path, flight_min, conn_min, penalty_min = heapq.heappop(pq)

        if curr == destination and len(path) > 1:
            if exclude_direct and len(path) == 2:
                continue
            results.append({
                "airport_sequence": " -> ".join(path),
                "connection_count": len(path) - 2,
                "estimated_flight_min": round(flight_min, 2),
                "connection_buffer_min": round(conn_min, 2),
                "delay_risk_penalty_min": round(penalty_min, 2),
                "total_path_score": round(g_score, 2),
            })
            continue

        if len(path) - 1 >= config.MAX_CONNECTION_LEGS:
            continue

        for edge in graph.get(curr, []):
            nxt = edge["to"]
            if nxt in path:
                continue

            added_conn = config.MIN_CONNECTION_MINUTES if len(path) > 1 else 0.0
            step_cost = edge["flight_min"] + added_conn + edge["delay_penalty"]

            new_g = g_score + step_cost
            new_f = new_g + heuristic(nxt)

            heapq.heappush(pq, (
                new_f,
                new_g,
                nxt,
                path + [nxt],
                flight_min + edge["flight_min"],
                conn_min + added_conn,
                penalty_min + edge["delay_penalty"]
            ))

    return results


def run_rebooking_engine(max_disrupted_flights=50):
    print("\n=== STEP 4: A* ALTERNATE-ROUTE REBOOKING ENGINE ===")

    dim_airport = config.load_lakehouse_table(config.GOLD_DIR, "dim_airport")
    route_mart = config.load_lakehouse_table(config.GOLD_DIR, "mart_operations_route_summary")
    fact_flight = config.load_lakehouse_table(config.GOLD_DIR, "fact_flight")

    graph, coords = build_flight_graph(dim_airport, route_mart)
    airport_key_map = dict(zip(dim_airport["iata_code"], dim_airport["airport_key"]))
    airport_name_map = dict(zip(dim_airport["iata_code"], dim_airport["airport_name"]))

    disrupted = fact_flight[
        ((fact_flight["cancelled_flag"] == 1) | (fact_flight["arrival_delay_min"] >= 60))
        & (fact_flight["origin_iata"].isin(coords))
        & (fact_flight["destination_iata"].isin(coords))
    ].head(max_disrupted_flights)

    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    recommendations = []
    rec_key = 1

    for _, flight in disrupted.iterrows():
        orig = flight["origin_iata"]
        dest = flight["destination_iata"]
        candidates = astar_find_routes(graph, coords, orig, dest, top_k=3, exclude_direct=True)

        for rank, cand in enumerate(candidates, start=1):
            recommendations.append({
                "recommendation_key": rec_key,
                "disrupted_flight_key": int(flight["flight_key"]),
                "origin_airport_key": airport_key_map.get(orig),
                "destination_airport_key": airport_key_map.get(dest),
                "origin_iata": orig,
                "origin_airport_name": airport_name_map.get(orig, orig),
                "destination_iata": dest,
                "destination_airport_name": airport_name_map.get(dest, dest),
                "candidate_rank": rank,
                "airport_sequence": cand["airport_sequence"],
                "connection_count": cand["connection_count"],
                "estimated_flight_min": cand["estimated_flight_min"],
                "connection_buffer_min": cand["connection_buffer_min"],
                "delay_risk_penalty_min": cand["delay_risk_penalty_min"],
                "total_path_score": cand["total_path_score"],
                "algorithm_run_ts": run_ts,
                "recommendation_status": "Candidate Path (Live Seats & Fares Unverified)"
            })
            rec_key += 1

    rec_df = pd.DataFrame(recommendations)
    config.save_lakehouse_table(rec_df, config.GOLD_DIR, "fact_route_recommendation", also_csv=True)
    rec_df.to_csv(os.path.join(config.GOLD_DIR, "mart_rebooking_recommendations.csv"), index=False)

    if config.LOAD_TO_MYSQL and not rec_df.empty:
        from sqlalchemy import create_engine, text
        engine = create_engine(config.DB_URI)
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE TABLE fact_route_recommendation;"))
            rec_df.to_sql("fact_route_recommendation", conn, if_exists="append", index=False, chunksize=2000)
        print("  -> Loaded fact_route_recommendation into MySQL 8.0")

    print(f"Evaluated {len(disrupted)} disrupted flights -> generated {len(rec_df)} A* alternate route candidates.")
    if not rec_df.empty:
        print("\nSample A* Rebooking Recommendations:")
        print(rec_df[["disrupted_flight_key", "origin_iata", "destination_iata", "candidate_rank", "airport_sequence", "total_path_score"]].head(6).to_string(index=False))


if __name__ == "__main__":
    run_rebooking_engine()
