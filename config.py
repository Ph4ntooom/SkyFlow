import os
import pandas as pd

# Base directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
BRONZE_DIR = os.path.join(DATA_DIR, "bronze")
SILVER_DIR = os.path.join(DATA_DIR, "silver")
GOLD_DIR = os.path.join(DATA_DIR, "gold")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")

# Source file names (matches Kaggle usdot/flight-delays + supplementary sources)
FLIGHTS_FILE = os.path.join(RAW_DIR, "flights.csv")
AIRLINES_FILE = os.path.join(RAW_DIR, "airlines.csv")
AIRPORTS_FILE = os.path.join(RAW_DIR, "airports.csv")
OURAIRPORTS_FILE = os.path.join(RAW_DIR, "ourairports_reference.csv")
WEATHER_FILE = os.path.join(RAW_DIR, "noaa_weather_observations.csv")

# MySQL 8.0 Connection Settings (for MySQL Workbench)
# 1. Run sql/01_mysql8_create_schema_and_tables.sql in MySQL Workbench first.
# 2. Set LOAD_TO_MYSQL = True and update your MySQL username/password below.
LOAD_TO_MYSQL = True
DB_URI = os.getenv(
    "DW_DB_URI",
    "mysql+pymysql://root:1234@localhost:3306/airline_dw?charset=utf8mb4"
)

# ETL & Business Rule Parameters
ON_TIME_THRESHOLD_MIN = 15          # US DOT standard: >=15 min late = delayed
WEATHER_WINDOW_HOURS = 2            # +/- 2 hours tolerance when matching weather to departure
MIN_CONNECTION_MINUTES = 45         # Minimum layover buffer for A* alternate routes
MAX_CONNECTION_LEGS = 3             # Max flight legs in an alternate itinerary
MAX_JET_SPEED_MPH = 550.0           # Conservative upper speed bound for admissible A* heuristic
DELAY_PENALTY_WEIGHT = 0.5          # Weight applied to historical average route delay in A* edge cost


def save_lakehouse_table(df, folder, table_name, also_csv=True):
    """
    Saves a DataFrame as CSV (universally readable by Python, MySQL Workbench,
    and Power BI Desktop) and also saves Parquet if pyarrow is available.
    """
    os.makedirs(folder, exist_ok=True)
    saved_files = []

    if also_csv:
        csv_path = os.path.join(folder, f"{table_name}.csv")
        df.to_csv(csv_path, index=False)
        saved_files.append(f"{table_name}.csv")

    try:
        parquet_path = os.path.join(folder, f"{table_name}.parquet")
        df.to_parquet(parquet_path, index=False)
        saved_files.append(f"{table_name}.parquet")
    except ImportError:
        pass

    return saved_files


def load_lakehouse_table(folder, table_name, dtype=None):
    """Loads a lakehouse table from Parquet if present, otherwise from CSV."""
    parquet_path = os.path.join(folder, f"{table_name}.parquet")
    csv_path = os.path.join(folder, f"{table_name}.csv")
    if os.path.exists(parquet_path):
        return pd.read_parquet(parquet_path)
    if os.path.exists(csv_path):
        return pd.read_csv(csv_path, dtype=dtype, low_memory=False)
    raise FileNotFoundError(f"Table '{table_name}' not found in {folder}")
