"""Feature engineering for the buy-timing model.

Mirrors the Airline_Fare_Prediction pipeline: date parts + categorical
one-hots + demand signals, with days_until_event as the feature the serving
layer sweeps to find the cheapest purchase date.
"""
from __future__ import annotations

import pandas as pd

CATEGORICAL = ["venue_state", "performer_type"]
NUMERIC = [
    "days_until_event",
    "event_month",
    "event_dow",
    "venue_capacity",
    "venue_popularity",
    "performer_popularity",
    "listing_count",
    "price_slope_7d",
    "price_ratio",
]
TARGET = "median_price"

# Concert-ish taxonomies in the SeatGeek data. Everything else (sports etc.)
# is excluded from training/serving.
CONCERT_TAXONOMIES = ("concert", "music_festival", "classical", "band", "theater")


def filter_concerts(df: pd.DataFrame) -> pd.DataFrame:
    tax = df["taxonomy_name"].fillna("").str.lower()
    sub = df["taxonomy_sub_name"].fillna("").str.lower()
    mask = tax.str.startswith(CONCERT_TAXONOMIES) | sub.str.startswith(CONCERT_TAXONOMIES)
    return df[mask]


def build_features(daily: pd.DataFrame) -> pd.DataFrame:
    """Input: snapshot_features rows (one per event per observed day)."""
    df = daily.sort_values(["event_id", "observed_date"]).copy()
    df = df[df[TARGET].notna() & (df[TARGET] > 0)]

    # Clip runaway speculative prices per event so they don't poison the target.
    lo = df.groupby("event_id")[TARGET].transform(lambda s: s.quantile(0.01))
    hi = df.groupby("event_id")[TARGET].transform(lambda s: s.quantile(0.99))
    df[TARGET] = df[TARGET].clip(lo, hi)

    dt = pd.to_datetime(df["event_datetime"])
    df["event_month"] = dt.dt.month
    df["event_dow"] = dt.dt.dayofweek

    first = df.groupby("event_id")[TARGET].transform("first")
    df["price_ratio"] = df[TARGET] / first
    df["price_slope_7d"] = (
        df.groupby("event_id")[TARGET]
        .transform(lambda s: s.diff(6) / 6)
        .fillna(0.0)
    )

    # Require enough history per event to learn a curve from.
    obs = df.groupby("event_id")["observed_date"].transform("count")
    df = df[obs >= 5]

    for col in ["venue_capacity", "venue_popularity", "performer_popularity", "listing_count"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    for col in CATEGORICAL:
        df[col] = df[col].fillna("unknown")

    return df


def encode(df: pd.DataFrame, columns: list[str] | None = None) -> pd.DataFrame:
    """One-hot encode; when `columns` is given (serving), align to it."""
    X = pd.get_dummies(df[NUMERIC + CATEGORICAL], columns=CATEGORICAL)
    if columns is not None:
        X = X.reindex(columns=columns, fill_value=0)
    return X
