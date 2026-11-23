"""
run_pipeline.py
One-command master runner for the Airline Flight Delay & Rebooking DW&BI Project (MySQL 8.0 Edition).
"""

import os
import sys
import importlib.util

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    scripts_dir = os.path.join(config.BASE_DIR, "scripts")

    if not os.path.exists(config.FLIGHTS_FILE):
        print("No flights.csv found in data/raw/ -> generating sample US DOT dataset first...")
        gen_mod = load_module(os.path.join(scripts_dir, "00_generate_sample_data.py"), "gen_data")
        gen_mod.generate_sample_dataset(num_flights=15000)

    bronze_mod = load_module(os.path.join(scripts_dir, "01_bronze_ingest.py"), "bronze")
    bronze_mod.run_bronze_ingestion()

    silver_mod = load_module(os.path.join(scripts_dir, "02_silver_transform.py"), "silver")
    silver_mod.run_silver_transformation()

    gold_mod = load_module(os.path.join(scripts_dir, "03_gold_warehouse.py"), "gold")
    gold_mod.build_gold_warehouse()

    astar_mod = load_module(os.path.join(scripts_dir, "04_astar_rebooking.py"), "astar")
    astar_mod.run_rebooking_engine(max_disrupted_flights=50)

    ml_mod = load_module(os.path.join(scripts_dir, "05_delay_risk_model.py"), "ml")
    ml_mod.train_delay_classifier()

    print("\n========================================================")
    print("PIPELINE COMPLETE! Gold Warehouse & Marts are ready in:")
    print(f"  {config.GOLD_DIR}")
    print("========================================================")


if __name__ == "__main__":
    main()
