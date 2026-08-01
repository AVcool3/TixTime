"""Train the buy-timing model heads.

Run with:  python -m tixtime.ml.train

Three heads, all HistGradientBoosting (histogram-based, multithreaded, native
categorical support -- a RandomForest at this panel size exhausts memory and
takes orders of magnitude longer for no accuracy gain):

  q10 / q50 / q90   log return over horizon h, giving the forecast curve and
                    its uncertainty band
  min_return        log(cheapest price still to come / price now). This, not
                    the price forecast, is the buy decision: it answers "how
                    much is waiting worth?" in a unit the UI can state in
                    dollars.

Evaluation is grouped by event_id throughout. A random row split would put
day 91 of a series in train and day 92 in test -- near-duplicates -- and
report an R^2 near 1.0 that measures nothing.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import GroupShuffleSplit

from tixtime import db
from tixtime.config import ARTIFACT_DIR, REPORT_DIR, WAREHOUSE, as_of_date
from tixtime.ml import dataset
from tixtime.ml.features import (
    ALL_FEATURES,
    CATEGORICAL_FEATURES,
    PAIRS,
    build_panel,
    build_pairs,
    design_matrix,
)

MODEL_VERSION = "tixtime-v1"


def _regressor(quantile: float | None = None, **kwargs) -> HistGradientBoostingRegressor:
    common = dict(
        max_iter=300,
        learning_rate=0.06,
        max_leaf_nodes=31,
        min_samples_leaf=40,
        l2_regularization=1.0,
        early_stopping=True,
        validation_fraction=0.1,
        random_state=17,
        categorical_features=[ALL_FEATURES.index(c) for c in CATEGORICAL_FEATURES],
    )
    common.update(kwargs)
    if quantile is not None:
        return HistGradientBoostingRegressor(loss="quantile", quantile=quantile, **common)
    return HistGradientBoostingRegressor(loss="squared_error", **common)


def _pinball(y_true: np.ndarray, y_pred: np.ndarray, quantile: float) -> float:
    delta = y_true - y_pred
    return float(np.mean(np.maximum(quantile * delta, (quantile - 1) * delta)))


def train(
    warehouse_path: Path = WAREHOUSE,
    cutoff: date | None = None,
    max_events: int | None = None,
    verbose: bool = True,
) -> dict:
    cutoff = cutoff or as_of_date()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    started = time.time()
    with db.warehouse(warehouse_path, read_only=True) as con:
        events = dataset.load_events(con)
        tiers = dataset.load_tiers(con)
        train_ids = dataset.resolved_event_ids(con, cutoff)
        if max_events:
            train_ids = train_ids[:max_events]
        if len(train_ids) < 50:
            raise RuntimeError(
                f"only {len(train_ids)} events are complete before {cutoff}; "
                "not enough to train on. Move TIXTIME_AS_OF later."
            )
        snapshots = dataset.load_snapshots(con, train_ids)

    if verbose:
        print(f"training on {len(train_ids):,} events complete before {cutoff}")
        print(f"  {len(snapshots):,} raw snapshot rows")

    panel = build_panel(snapshots)
    labelled = dataset.attach_future_labels(panel, events, cutoff)
    pairs = build_pairs(labelled, events, tiers, PAIRS)
    pairs = pairs.replace([np.inf, -np.inf], np.nan).dropna(subset=["y_log_return"])

    if verbose:
        print(f"  {len(panel):,} panel rows -> {len(pairs):,} (as-of, horizon) pairs")

    matrix = design_matrix(pairs)
    groups = pairs["event_id"].to_numpy()

    splitter = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=17)
    train_idx, test_idx = next(splitter.split(matrix, groups=groups))
    x_train, x_test = matrix.iloc[train_idx], matrix.iloc[test_idx]

    metrics: dict = {
        "model_version": MODEL_VERSION,
        "trained_through": cutoff.isoformat(),
        "n_train_events": len(train_ids),
        "n_pairs": int(len(pairs)),
        "n_features": len(ALL_FEATURES),
        "data_source": "synthetic_v1",
        "heads": {},
    }
    models: dict = {}

    # --- forecast curve: quantile heads on the log return -----------------
    y_return = pairs["y_log_return"].to_numpy()
    for quantile, name in ((0.1, "q10"), (0.5, "q50"), (0.9, "q90")):
        model = _regressor(quantile=quantile)
        model.fit(x_train, y_return[train_idx])
        predicted = model.predict(x_test)
        models[name] = model
        head = {
            "pinball_loss": _pinball(y_return[test_idx], predicted, quantile),
            "coverage": float(np.mean(y_return[test_idx] <= predicted)),
        }
        if name == "q50":
            head["mae_log_return"] = float(np.mean(np.abs(y_return[test_idx] - predicted)))
            # Back out a dollar error: exp(pred) scales today's price.
            actual_price = pairs["target_price"].to_numpy()[test_idx]
            implied = pairs["get_in_price"].to_numpy()[test_idx] * np.exp(predicted)
            head["price_mape"] = float(np.mean(np.abs(implied - actual_price) / actual_price))
            head["price_mae"] = float(np.mean(np.abs(implied - actual_price)))
        metrics["heads"][name] = head
        if verbose:
            print(f"  {name}: " + "  ".join(f"{k}={v:.4f}" for k, v in head.items()))

    # --- the buy decision: how much is waiting worth? ---------------------
    # One row per (event, tier, as-of) -- horizon is irrelevant to this head,
    # so collapse the pair expansion back down first.
    decision = pairs.drop_duplicates(subset=["event_id", "tier_key", "as_of_date"]).copy()
    decision_matrix = design_matrix(decision)
    decision_groups = decision["event_id"].to_numpy()
    d_train_idx, d_test_idx = next(
        GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=17).split(
            decision_matrix, groups=decision_groups
        )
    )
    y_min = decision["y_log_min_return"].to_numpy()
    min_model = _regressor()
    min_model.fit(decision_matrix.iloc[d_train_idx], y_min[d_train_idx])
    models["min_return"] = min_model

    predicted_min = min_model.predict(decision_matrix.iloc[d_test_idx])
    truth_min = y_min[d_test_idx]
    price_now = decision["get_in_price"].to_numpy()[d_test_idx]
    metrics["heads"]["min_return"] = {
        "mae_log_return": float(np.mean(np.abs(truth_min - predicted_min))),
        "savings_mae_dollars": float(
            np.mean(np.abs(price_now * (1 - np.exp(predicted_min)) - price_now * (1 - np.exp(truth_min))))
        ),
        # Does the head get the DIRECTION right -- is it worth waiting at all?
        "wait_decision_accuracy": float(
            np.mean((predicted_min < np.log(0.97)) == (truth_min < np.log(0.97)))
        ),
        "n_decision_rows": int(len(decision)),
    }
    if verbose:
        print("  min_return: " + "  ".join(
            f"{k}={v:.4f}" for k, v in metrics["heads"]["min_return"].items()
        ))

    metrics["train_seconds"] = round(time.time() - started, 1)

    artifact = {
        "models": models,
        "features": ALL_FEATURES,
        "categorical": CATEGORICAL_FEATURES,
        "version": MODEL_VERSION,
        "trained_through": cutoff.isoformat(),
        "categories": {
            column: list(pairs[column].cat.categories) for column in CATEGORICAL_FEATURES
        },
    }
    joblib.dump(artifact, ARTIFACT_DIR / "models.joblib")
    (REPORT_DIR / "training_metrics.json").write_text(json.dumps(metrics, indent=2))

    with db.warehouse(warehouse_path) as con:
        db.record_run(con, "train", len(pairs), cutoff, MODEL_VERSION)

    if verbose:
        print(f"\nsaved {ARTIFACT_DIR / 'models.joblib'} in {metrics['train_seconds']}s")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Train TixTime buy-timing models")
    parser.add_argument("--warehouse", type=Path, default=WAREHOUSE)
    parser.add_argument("--cutoff", type=date.fromisoformat, default=None)
    parser.add_argument("--max-events", type=int, default=None)
    args = parser.parse_args()
    train(args.warehouse, args.cutoff, args.max_events)


if __name__ == "__main__":
    main()
