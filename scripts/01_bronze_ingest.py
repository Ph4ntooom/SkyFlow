"""
01_bronze_ingest.py
BRONZE LAYER (Raw Landing Zone)

Reads raw downloaded source CSV files without destructive modifications,
computes SHA-256 checksums and ingestion timestamps, and writes raw snapshots
plus bronze_ingestion_manifest.json into data/bronze/.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
import pandas as pd
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def compute_sha256(filepath):
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def ingest_file(source_name, csv_path, table_name):
    if not os.path.exists(csv_path):
        print(f"[SKIP] Optional source not found: {csv_path}")
        return None

    print(f"Ingesting {source_name} from {os.path.basename(csv_path)}...")
    checksum = compute_sha256(csv_path)
    load_ts = datetime.now(timezone.utc).isoformat()

    # Read all columns as string in Bronze so raw formats (like '0030' HHMM) are preserved verbatim
    df = pd.read_csv(csv_path, dtype=str, low_memory=False)
    df["_bronze_source_file"] = os.path.basename(csv_path)
    df["_bronze_load_ts"] = load_ts

    saved = config.save_lakehouse_table(df, config.BRONZE_DIR, table_name, also_csv=True)

    meta = {
        "source_name": source_name,
        "source_file": os.path.basename(csv_path),
        "bronze_artifacts": saved,
        "row_count": int(len(df)),
        "column_count": int(len(df.columns) - 2),
        "sha256_checksum": checksum,
        "ingested_at_utc": load_ts,
    }
    print(f"  -> Saved {meta['row_count']:,} rows (SHA-256: {checksum[:12]}...)")
    return meta


def run_bronze_ingestion():
    os.makedirs(config.BRONZE_DIR, exist_ok=True)
    print("\n=== STEP 1: BRONZE LAYER INGESTION ===")

    sources = [
        ("US_DOT_Flights", config.FLIGHTS_FILE, "bronze_flights"),
        ("US_DOT_Airlines", config.AIRLINES_FILE, "bronze_airlines"),
        ("US_DOT_Airports", config.AIRPORTS_FILE, "bronze_airports"),
        ("OurAirports_Reference", config.OURAIRPORTS_FILE, "bronze_ourairports"),
        ("NOAA_Weather", config.WEATHER_FILE, "bronze_weather"),
    ]

    manifest = []
    for name, src, out in sources:
        record = ingest_file(name, src, out)
        if record:
            manifest.append(record)

    manifest_path = os.path.join(config.BRONZE_DIR, "bronze_ingestion_manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Bronze manifest written to {manifest_path}")


if __name__ == "__main__":
    run_bronze_ingestion()
