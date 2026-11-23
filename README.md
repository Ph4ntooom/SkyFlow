# Airline Flight Delay Prediction & Rebooking Data Warehouse (MySQL 8.0 Edition)

**Course:** Data Warehousing & Business Intelligence (Section BDS-5A)  
**Institution:** National University of Computer & Emerging Sciences (FAST-NUCES), Karachi Campus  
**Group Members:**  
- **24K-2510** — Ahad  
- **24K-2575** — Amail  
- **24K-2561** — Aayan Hassan  

---

## 1. Quick Start Guide

### Step 1: Install Python Libraries
```bash
pip install -r requirements.txt
```

### Step 2: Run the ETL + AI Pipeline
```bash
python run_pipeline.py
```
- **Demo Mode:** If `data/raw/flights.csv` is not yet downloaded, `run_pipeline.py` generates 15,025 realistic US DOT flight records automatically.
- **Full Kaggle Dataset Mode (`usdot/flight-delays`):** Download `flights.csv`, `airlines.csv`, and `airports.csv` from Kaggle, copy them into `data/raw/`, and run `python run_pipeline.py`.

### Step 3: Set Up the Warehouse in MySQL 8.0 Workbench
1. Open **MySQL Workbench** and connect to your local MySQL 8.0 Server.
2. Open and execute **`sql/01_mysql8_create_schema_and_tables.sql`** (creates `airline_dw`, Snowflake + Star tables, Data Mart views, and `etl_load_audit`).
3. Open `config.py`, set `LOAD_TO_MYSQL = True`, and enter your MySQL password in `DB_URI`:
   ```python
   DB_URI = "mysql+pymysql://root:YOUR_PASSWORD@localhost:3306/airline_dw?charset=utf8mb4"
   ```
4. Re-run `python scripts/03_gold_warehouse.py` and `python scripts/04_astar_rebooking.py` to load all Gold tables into MySQL 8.0.
5. Open and execute **`sql/02_mysql8_indexes_and_spatial.sql`** to build composite B-Tree indexes and the InnoDB **R-Tree SPATIAL INDEX** on `dim_airport(geom)`.
6. Press **`Ctrl + R`** (`Database -> Reverse Engineer...`) in MySQL Workbench to display the visual **EER Diagram** of your Star + Snowflake schema during your presentation!
