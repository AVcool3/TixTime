"""Train the buy-timing price model and backtest the product claim.

Usage:
    python -m ml.train [--model tree|forest] [--min-days 5]

Outputs ml/model.joblib: {"model", "columns", "trained_at", "metrics"}.

Two evaluations are reported:
  1. MAE on held-out events (split by event_id — random row splits leak,
     since adjacent days of one event are near-duplicates).
  2. The backtest that actually matters: for each held-out event, the day
     the model would have recommended buying vs. that event's true minimum —
     reported as average $ left on the table.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import GroupShuffleSplit
from sklearn.tree import DecisionTreeRegressor

from ingestion.db import connect
from ml.features import TARGET, build_features, encode, filter_concerts

MODEL_PATH = Path(__file__).with_name("model.joblib")


def load_training_frame() -> pd.DataFrame:
    con = connect(read_only=True)
    df = con.execute("SELECT * FROM snapshot_features").df()
    con.close()
    return build_features(filter_concerts(df))


def backtest_buy_date(model, columns, test_df: pd.DataFrame) -> dict:
    """For each test event, simulate: on its first observed day, sweep
    days_until_event and pick the predicted-cheapest remaining day. Compare
    that day's REAL price to the event's true minimum."""
    left_on_table, naive_left = [], []
    for _, ev in test_df.groupby("event_id"):
        ev = ev.sort_values("observed_date")
        if len(ev) < 5:
            continue
        first = ev.iloc[0]
        sweep = pd.DataFrame([first.to_dict()] * len(ev))
        sweep["days_until_event"] = ev["days_until_event"].values
        preds = model.predict(encode(sweep, columns))
        rec_day = ev["days_until_event"].values[int(np.argmin(preds))]

        actual_at_rec = ev.loc[ev["days_until_event"] == rec_day, TARGET].iloc[0]
        true_min = ev[TARGET].min()
        left_on_table.append(actual_at_rec - true_min)
        naive_left.append(first[TARGET] - true_min)  # baseline: buy immediately

    return {
        "events_backtested": len(left_on_table),
        "avg_left_on_table": float(np.mean(left_on_table)) if left_on_table else None,
        "avg_left_on_table_buy_today_baseline": float(np.mean(naive_left)) if naive_left else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["tree", "forest"], default="forest")
    args = parser.parse_args()

    df = load_training_frame()
    if df["event_id"].nunique() < 10:
        raise SystemExit(
            f"Only {df['event_id'].nunique()} events with enough history — "
            "ingest more daily drops before training."
        )
    print(f"Training frame: {len(df)} rows, {df['event_id'].nunique()} events")

    X = encode(df)
    y = df[TARGET]
    gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
    train_idx, test_idx = next(gss.split(X, y, groups=df["event_id"]))

    if args.model == "tree":
        model = DecisionTreeRegressor(max_depth=12, min_samples_leaf=20, random_state=42)
    else:
        model = RandomForestRegressor(
            n_estimators=300, min_samples_leaf=10, n_jobs=-1, random_state=42
        )
    model.fit(X.iloc[train_idx], y.iloc[train_idx])

    mae = mean_absolute_error(y.iloc[test_idx], model.predict(X.iloc[test_idx]))
    bt = backtest_buy_date(model, list(X.columns), df.iloc[test_idx])

    metrics = {"mae_unseen_events": float(mae), **bt}
    print(f"MAE on unseen events: ${mae:.2f}")
    print(
        f"Backtest ({bt['events_backtested']} events): model leaves "
        f"${bt['avg_left_on_table']:.2f} on the table vs true minimum "
        f"(buy-today baseline: ${bt['avg_left_on_table_buy_today_baseline']:.2f})"
    )

    joblib.dump(
        {
            "model": model,
            "columns": list(X.columns),
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
        },
        MODEL_PATH,
    )
    print(f"Saved {MODEL_PATH}")


if __name__ == "__main__":
    main()
