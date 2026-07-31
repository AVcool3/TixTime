"""Precompute the serving cache: deal board and sparklines.

Run with:  python -m tixtime.ml.precompute

Two things the API must never compute per request:

  deal_board   the cross-event ranked view. "Which Warriors game in March, in
               which section, and when do I buy?" is a cross-event
               optimisation, not a per-event lookup. Answering it live would
               mean fanning out a model call per event per tier.
  sparklines   the search grid shows a price trace on every card. Sixty cards
               would otherwise be sixty history queries against a 4M-row table.

Both are rebuilt whenever the model or the clock changes.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from tixtime import db
from tixtime.config import NEAR_MIN_TOLERANCE, SYNTHETIC_SOURCE, WAREHOUSE, as_of_date
from tixtime.ml import dataset
from tixtime.ml.features import _attach_context, build_panel, design_matrix
from tixtime.ml.serve import _CURVE_HORIZONS, _align_categories, load_artifact

SPARKLINE_POINTS = 28

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS deal_board (
    event_id             BIGINT NOT NULL,
    tier_key             VARCHAR NOT NULL,
    as_of_date           DATE NOT NULL,
    days_until_event     INTEGER NOT NULL,
    price_now            DOUBLE NOT NULL,
    forecast_min         DOUBLE NOT NULL,
    forecast_min_date    DATE,
    days_to_forecast_min INTEGER,
    expected_saving      DOUBLE NOT NULL,
    expected_saving_pct  DOUBLE NOT NULL,
    action               VARCHAR NOT NULL,
    confidence           VARCHAR NOT NULL,
    source               VARCHAR NOT NULL,
    model_version        VARCHAR NOT NULL,
    PRIMARY KEY (event_id, tier_key)
);

CREATE TABLE IF NOT EXISTS event_sparkline (
    event_id   BIGINT NOT NULL,
    tier_key   VARCHAR NOT NULL,
    points     VARCHAR NOT NULL,   -- JSON array of prices, oldest first
    low        DOUBLE NOT NULL,
    high       DOUBLE NOT NULL,
    PRIMARY KEY (event_id, tier_key)
);
"""


def _downsample(prices: np.ndarray, points: int = SPARKLINE_POINTS) -> list[float]:
    if len(prices) <= points:
        return [round(float(p), 2) for p in prices]
    idx = np.linspace(0, len(prices) - 1, points).round().astype(int)
    return [round(float(p), 2) for p in prices[idx]]


def build(warehouse_path: Path = WAREHOUSE, as_of: date | None = None, verbose: bool = True) -> dict:
    as_of = as_of or as_of_date()
    artifact = load_artifact()
    models = artifact["models"]

    with db.warehouse(warehouse_path, read_only=True) as con:
        events = dataset.load_events(con)
        tiers = dataset.load_tiers(con)
        upcoming = events[pd.to_datetime(events["event_date"]).dt.date > as_of]
        if upcoming.empty:
            raise RuntimeError(f"no upcoming events after {as_of}")
        snapshots = dataset.load_snapshots(con, upcoming["event_id"].tolist())

    # Everything the site serves is strictly as-of: future rows exist in the
    # warehouse (the simulator generated whole paths) and must never be read
    # on the serving path.
    snapshots = snapshots[pd.to_datetime(snapshots["as_of_date"]).dt.date <= as_of]
    if snapshots.empty:
        raise RuntimeError(f"no snapshots at or before {as_of}")

    panel = build_panel(snapshots)
    if verbose:
        print(f"as-of {as_of}: {panel['event_id'].nunique():,} events, {len(panel):,} panel rows")

    # --- sparklines -------------------------------------------------------
    spark_rows = []
    for (event_id, tier_key), series in panel.groupby(["event_id", "tier_key"], sort=False):
        series = series.sort_values("as_of_date")
        prices = series["get_in_price"].to_numpy()
        if len(prices) < 2:
            continue
        spark_rows.append(
            {
                "event_id": int(event_id),
                "tier_key": tier_key,
                "points": json.dumps(_downsample(prices)),
                "low": round(float(prices.min()), 2),
                "high": round(float(prices.max()), 2),
            }
        )
    sparklines = pd.DataFrame(spark_rows)

    # --- deal board -------------------------------------------------------
    # Latest observed row per (event, tier) is the decision point.
    latest = panel.sort_values("as_of_date").groupby(["event_id", "tier_key"], as_index=False).tail(1)
    latest = latest[latest["days_until_event"] > 0].copy()

    decision = latest.copy()
    decision["horizon_days"] = 1
    decision["days_until_event_target"] = decision["days_until_event"] - 1
    decision["target_date"] = pd.to_datetime(decision["as_of_date"]) + pd.Timedelta(days=1)
    decision = _attach_context(decision, events, tiers)
    decision_matrix = _align_categories(design_matrix(decision), artifact)
    decision["predicted_min_return"] = models["min_return"].predict(decision_matrix)

    # Where the trough lands: sweep the horizon feature (valid -- it is a
    # trained input) and take the argmin of the median curve.
    best_horizon = np.zeros(len(decision), dtype=int)
    best_price = np.full(len(decision), np.inf)
    days_left = decision["days_until_event"].to_numpy()

    # `None` means "each row's own event day". Serving evaluates that horizon
    # too, and without it here the two disagree: the event page headline said
    # the trough was on event day while the seat table, read from this board,
    # said five days earlier.
    for horizon in (*_CURVE_HORIZONS, None):
        if horizon is None:
            horizons = days_left
            applicable = days_left > 0
        else:
            horizons = np.full(len(decision), horizon)
            applicable = days_left >= horizon
        if not applicable.any():
            continue
        block = decision.copy()
        block["horizon_days"] = horizons
        block["days_until_event_target"] = block["days_until_event"] - horizons
        block["target_date"] = pd.to_datetime(block["as_of_date"]) + pd.to_timedelta(
            horizons, unit="D"
        )
        matrix = _align_categories(design_matrix(block), artifact)
        predicted = block["get_in_price"].to_numpy() * np.exp(models["q50"].predict(matrix))
        improved = applicable & (predicted < best_price)
        best_price = np.where(improved, predicted, best_price)
        best_horizon = np.where(improved, horizons, best_horizon)

    price_now = decision["get_in_price"].to_numpy()
    predicted_min = price_now * np.exp(decision["predicted_min_return"].to_numpy())
    saving = np.maximum(price_now - predicted_min, 0.0)
    saving_pct = np.divide(saving, price_now, out=np.zeros_like(saving), where=price_now > 0)

    threshold = np.log(1.0 / NEAR_MIN_TOLERANCE)
    wait = decision["predicted_min_return"].to_numpy() < threshold
    margin = np.abs(decision["predicted_min_return"].to_numpy() - threshold)

    as_of_dates = pd.to_datetime(decision["as_of_date"])
    board = pd.DataFrame(
        {
            "event_id": decision["event_id"].astype("int64"),
            "tier_key": decision["tier_key"].astype(str),
            "as_of_date": as_of_dates.dt.date,
            "days_until_event": decision["days_until_event"].astype(int),
            "price_now": np.round(price_now, 2),
            "forecast_min": np.round(predicted_min, 2),
            "forecast_min_date": (as_of_dates + pd.to_timedelta(best_horizon, unit="D")).dt.date,
            "days_to_forecast_min": best_horizon.astype(int),
            "expected_saving": np.round(saving, 2),
            "expected_saving_pct": np.round(saving_pct, 4),
            "action": np.where(wait, "WAIT", "BUY_NOW"),
            "confidence": np.where(margin > 0.08, "high", np.where(margin > 0.03, "medium", "low")),
            "source": SYNTHETIC_SOURCE,
            "model_version": artifact["version"],
        }
    )

    with db.warehouse(warehouse_path) as con:
        con.execute(CACHE_SCHEMA)
        con.execute("DELETE FROM deal_board")
        con.execute("DELETE FROM event_sparkline")
        con.register("_board", board)
        con.execute("INSERT INTO deal_board SELECT * FROM _board")
        con.unregister("_board")
        if not sparklines.empty:
            con.register("_spark", sparklines)
            con.execute("INSERT INTO event_sparkline SELECT * FROM _spark")
            con.unregister("_spark")
        db.record_run(con, "precompute", len(board), as_of, artifact["version"])

    stats = {
        "as_of": as_of.isoformat(),
        "deal_board_rows": int(len(board)),
        "sparkline_rows": int(len(sparklines)),
        "events": int(board["event_id"].nunique()),
        "wait_share": round(float((board["action"] == "WAIT").mean()), 4),
    }
    if verbose:
        print(f"  deal_board {stats['deal_board_rows']:,} rows over {stats['events']:,} events")
        print(f"  sparklines {stats['sparkline_rows']:,} rows")
        print(f"  WAIT share {stats['wait_share']:.1%}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the TixTime serving cache")
    parser.add_argument("--warehouse", type=Path, default=WAREHOUSE)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    args = parser.parse_args()
    build(args.warehouse, args.as_of)


if __name__ == "__main__":
    main()
