"""
05_delay_risk_model.py
OPTIONAL AI EXTENSION: BASELINE DELAY-RISK CLASSIFIER (ARRIVAL DELAY >= 15 MIN)

Uses strictly pre-departure features and a chronological train/test split
(Months 1-9 train, Months 10-12 test) to prevent data leakage.
Optimized for large datasets (5.8M+ rows) using multi-core execution.
"""

import json
import os
import time
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_score, recall_score, f1_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def train_delay_classifier(max_train_rows=500000):
    print("\n=== STEP 5: BASELINE ARRIVAL DELAY CLASSIFIER (NO LEAKAGE) ===")
    t0 = time.time()

    fact = config.load_lakehouse_table(config.GOLD_DIR, "fact_flight")
    dim_date = config.load_lakehouse_table(config.GOLD_DIR, "dim_date")
    dim_airline = config.load_lakehouse_table(config.GOLD_DIR, "dim_airline")
    dim_weather = config.load_lakehouse_table(config.GOLD_DIR, "dim_weather")

    df = fact[fact["cancelled_flag"] == 0].copy()
    df = df.merge(dim_date[["date_key", "month", "day_of_week"]], on="date_key", how="left")
    df = df.merge(dim_airline[["airline_key", "iata_carrier"]], on="airline_key", how="left")
    df = df.merge(dim_weather[["weather_key", "weather_category"]], on="weather_key", how="left")

    num_features = ["month", "day_of_week", "dep_hour", "distance_miles"]
    cat_features = ["iata_carrier", "origin_iata", "destination_iata", "weather_category"]
    target = "is_delayed_15"

    df = df.dropna(subset=num_features + cat_features + [target])

    train_df = df[df["month"] <= 9]
    test_df = df[df["month"] > 9]

    if len(train_df) < 50 or len(test_df) < 20:
        split_idx = int(len(df) * 0.8)
        df = df.sort_values("date_key")
        train_df, test_df = df.iloc[:split_idx], df.iloc[split_idx:]

    # Stratified/random cap on training rows so Random Forest finishes in ~10 seconds on 5.8M datasets
    if len(train_df) > max_train_rows:
        train_df = train_df.sample(n=max_train_rows, random_state=42)

    X_train = train_df[num_features + cat_features]
    y_train = train_df[target]
    X_test = test_df[num_features + cat_features]
    y_test = test_df[target]

    preprocessor = ColumnTransformer(
        transformers=[
            ("num", StandardScaler(), num_features),
            ("cat", OneHotEncoder(handle_unknown="ignore"), cat_features),
        ]
    )

    models = {
        "Logistic_Regression": LogisticRegression(max_iter=300, class_weight="balanced"),
        "Random_Forest": RandomForestClassifier(
            n_estimators=60,
            max_depth=10,
            min_samples_leaf=50,
            n_jobs=-1,
            random_state=42,
            class_weight="balanced",
        ),
    }

    metrics_summary = {}
    for name, clf in models.items():
        t_m = time.time()
        pipe = Pipeline([("prep", preprocessor), ("model", clf)])
        pipe.fit(X_train, y_train)
        preds = pipe.predict(X_test)
        probs = pipe.predict_proba(X_test)[:, 1]

        metrics = {
            "train_rows": int(len(train_df)),
            "test_rows": int(len(test_df)),
            "precision": round(float(precision_score(y_test, preds, zero_division=0)), 4),
            "recall": round(float(recall_score(y_test, preds, zero_division=0)), 4),
            "f1_score": round(float(f1_score(y_test, preds, zero_division=0)), 4),
            "roc_auc": round(float(roc_auc_score(y_test, probs)), 4) if len(y_test.unique()) > 1 else None,
        }
        metrics_summary[name] = metrics
        print(
            f"  -> {name} ({time.time() - t_m:.1f}s): "
            f"Precision={metrics['precision']}, Recall={metrics['recall']}, "
            f"F1={metrics['f1_score']}, ROC-AUC={metrics['roc_auc']}"
        )

    out_path = os.path.join(config.OUTPUT_DIR, "delay_classifier_metrics.json")
    with open(out_path, "w") as f:
        json.dump(metrics_summary, f, indent=2)
    print(f"Saved classifier metrics to {out_path} in {time.time() - t0:.1f}s.")


if __name__ == "__main__":
    train_delay_classifier()
