"""Feature engineering for the buy-timing models.

DESIGN CONSTRAINTS (each one exists because the obvious alternative is wrong)
---------------------------------------------------------------------------

1. DIRECT MULTI-HORIZON, NOT A FEATURE SWEEP.
   The naive way to draw a forecast curve is to train p ~ f(days_until_event,
   rolling stats, inventory, ...) and then, at serving time, hold everything
   fixed and sweep `days_until_event` from 90 down to 1. That is invalid:
   rolling stats, momentum and inventory are all themselves functions of the
   quantity being swept, so the sweep evaluates the model on feature
   combinations that never occur in training. Gradient-boosted trees do not
   extrapolate; they just fall into whatever leaf the frozen features imply.

   Instead every training row is a PAIR (t, h): features observed strictly at
   as-of date t, plus the horizon h as an ordinary input feature, predicting
   the price h days later. Serving sweeps h, which the model was trained on.

2. THE TARGET IS A LOG RETURN, NOT A LOG PRICE.
   Predicting log(p_{t+h} / p_t) removes the price level entirely. Tier and
   league multipliers span more than an order of magnitude (upper deck $15,
   courtside $600); a level model spends its capacity rediscovering those
   multipliers and underfits the timing signal, which is the only thing the
   product actually needs.

3. NO SIMULATOR PARAMETERS AS FEATURES.
   `home_demand_index` / `away_demand_index` are direct multiplicative inputs
   to the simulator's base price, and the latent demand score is what decides
   the terminal regime. Feeding either back as a feature would be training a
   model to invert its own data generator. They are excluded here, and
   `assert_no_generative_leakage` fails the build if they reappear.

4. EVERY HISTORY FEATURE IS BACKWARD-LOOKING AND TRUNCATED AT t.
   Rolling windows are computed on a time-sorted frame per (event, tier) with
   closed='left'-style semantics, and the "price versus its own window median"
   feature uses an EXPANDING median over [listing_open, t] rather than the
   full listing window -- a full-window statistic would encode where p_t sits
   in a distribution that includes the future, which is very close to a
   sufficient statistic for the label.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from tixtime.config import ROLLING_WINDOWS

# Columns that must never enter the design matrix: they are inputs to the
# generative process, or identifiers the model could memorise.
FORBIDDEN_FEATURES = frozenset(
    {
        "home_demand_index",
        "away_demand_index",
        "demand_score",
        "demand_rank",
        "event_id",
        "venue_id",
        "home_slug",
        "home_team",
        "away_team",
    }
)

CATEGORICAL_FEATURES = [
    "league",
    "postseason_tier",
    "tier_key",
    "archetype",
]

NUMERIC_FEATURES = [
    # horizon and timing -- the core of the buy-timing signal
    "horizon_days",
    "days_until_event",
    "days_until_event_target",
    "log_days_until_event",
    "log_horizon",
    # calendar
    "event_dow",
    "event_month",
    "event_is_weekend",
    "as_of_dow",
    "target_dow",
    "target_is_weekend",
    "days_since_announce",
    "history_len_days",
    # current market state (levels are relative, never absolute dollars)
    "log_price_vs_tier_base",
    "get_in_to_median_ratio",
    "log_listing_count",
    "inventory_ratio_vs_open",
    # backward-looking price dynamics
    "ret_7d",
    "ret_14d",
    "ret_30d",
    "price_vs_min_7d",
    "price_vs_min_30d",
    "price_vs_expanding_median",
    "vol_7d",
    "vol_30d",
    # venue / event shape
    "capacity",
    "is_ga",
    "tier_rank",
    "tier_price_multiplier",
    "tier_inventory_share",
]

ALL_FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES


def assert_no_generative_leakage(columns) -> None:
    """Fail loudly if a simulator parameter reached the design matrix."""
    offending = FORBIDDEN_FEATURES.intersection(columns)
    if offending:
        raise ValueError(
            "these columns are inputs to the price simulator (or identifiers) and "
            f"must not be model features: {sorted(offending)}"
        )


@dataclass(frozen=True)
class PairSpec:
    """How (as-of, horizon) training pairs are sampled.

    The full cross product of ~180 as-of days x ~150 horizons per series is
    both enormous and highly redundant -- adjacent days are near-duplicates.
    Sampling keeps the panel tractable without losing regimes.
    """

    # As-of days are kept at full daily resolution close to the event, where
    # the price action is, and thinned further out.
    near_days: int = 30          # d <= 30: every day
    mid_days: int = 90           # 30 < d <= 90: every mid_stride days
    mid_stride: int = 3
    far_stride: int = 7          # d > 90
    # Horizons sampled per as-of row.
    horizons: tuple[int, ...] = (1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60, 90)
    horizons_per_row: int = 4
    seed: int = 7


PAIRS = PairSpec()


def build_panel(snapshots: pd.DataFrame) -> pd.DataFrame:
    """Add backward-looking history features to the raw snapshot panel.

    `snapshots` must contain every day of each (event, tier) series INCLUDING
    burn-in rows, so the rolling windows are complete on the first exposed day.
    Burn-in rows are dropped at the end, after they have done their job.
    """
    panel = snapshots.sort_values(["event_id", "tier_key", "as_of_date"]).copy()
    grouped = panel.groupby(["event_id", "tier_key"], sort=False)["get_in_price"]

    for window in ROLLING_WINDOWS:
        rolled = grouped.rolling(window, min_periods=2)
        panel[f"_min_{window}"] = rolled.min().to_numpy()
        panel[f"_mean_{window}"] = rolled.mean().to_numpy()
        panel[f"_std_{window}"] = rolled.std().to_numpy()
        panel[f"_lag_{window}"] = grouped.shift(window).to_numpy()

    # Expanding, not full-window: only prices up to and including t.
    panel["_expanding_median"] = grouped.expanding(min_periods=2).median().to_numpy()
    panel["_first_listing_count"] = panel.groupby(
        ["event_id", "tier_key"], sort=False
    )["listing_count"].transform("first")
    panel["history_len_days"] = grouped.cumcount().to_numpy()

    price = panel["get_in_price"]
    panel["ret_7d"] = np.log(price / panel["_lag_7"])
    panel["ret_14d"] = np.log(price / panel["_lag_14"])
    panel["ret_30d"] = np.log(price / panel["_lag_30"])
    panel["price_vs_min_7d"] = np.log(price / panel["_min_7"])
    panel["price_vs_min_30d"] = np.log(price / panel["_min_30"])
    panel["price_vs_expanding_median"] = np.log(price / panel["_expanding_median"])
    panel["vol_7d"] = panel["_std_7"] / panel["_mean_7"]
    panel["vol_30d"] = panel["_std_30"] / panel["_mean_30"]
    panel["get_in_to_median_ratio"] = price / panel["median_price"]
    panel["log_listing_count"] = np.log1p(panel["listing_count"])
    panel["inventory_ratio_vs_open"] = panel["listing_count"] / panel[
        "_first_listing_count"
    ].clip(lower=1)

    panel = panel[~panel["is_burn_in"]].copy()
    return panel.drop(columns=[c for c in panel.columns if c.startswith("_")])


def _sample_as_of(panel: pd.DataFrame, spec: PairSpec = PAIRS) -> pd.DataFrame:
    days = panel["days_until_event"]
    keep = (
        (days <= spec.near_days)
        | ((days <= spec.mid_days) & (days % spec.mid_stride == 0))
        | ((days > spec.mid_days) & (days % spec.far_stride == 0))
    )
    return panel[keep]


def build_pairs(
    panel: pd.DataFrame,
    events: pd.DataFrame,
    tiers: pd.DataFrame,
    spec: PairSpec = PAIRS,
    include_targets: bool = True,
) -> pd.DataFrame:
    """Expand the panel into (as-of, horizon) rows with targets attached.

    The target for a row is the price h days after the as-of date, looked up
    from the same series. `include_targets=False` is the serving path, where
    the future does not exist yet.
    """
    sampled = _sample_as_of(panel, spec)
    rng = np.random.default_rng(spec.seed)

    horizons = np.array(spec.horizons)
    frames = []
    for horizon in horizons:
        # A horizon is only usable if the target day is still before the event.
        usable = sampled[sampled["days_until_event"] >= horizon]
        if usable.empty:
            continue
        if include_targets and spec.horizons_per_row < len(horizons):
            mask = rng.random(len(usable)) < (spec.horizons_per_row / len(horizons))
            usable = usable[mask]
        block = usable.copy()
        block["horizon_days"] = int(horizon)
        frames.append(block)

    if not frames:
        return pd.DataFrame()

    pairs = pd.concat(frames, ignore_index=True)
    pairs["days_until_event_target"] = pairs["days_until_event"] - pairs["horizon_days"]
    pairs["target_date"] = pd.to_datetime(pairs["as_of_date"]) + pd.to_timedelta(
        pairs["horizon_days"], unit="D"
    )

    if include_targets:
        lookup = panel[["event_id", "tier_key", "days_until_event", "get_in_price"]].rename(
            columns={
                "days_until_event": "days_until_event_target",
                "get_in_price": "target_price",
            }
        )
        pairs = pairs.merge(
            lookup, on=["event_id", "tier_key", "days_until_event_target"], how="inner"
        )
        pairs["y_log_return"] = np.log(pairs["target_price"] / pairs["get_in_price"])

    return _attach_context(pairs, events, tiers)


def _attach_context(
    pairs: pd.DataFrame, events: pd.DataFrame, tiers: pd.DataFrame
) -> pd.DataFrame:
    frame = pairs.merge(
        events[
            [
                "event_id", "league", "postseason_tier", "event_date", "announce_date",
                "is_ga", "archetype", "capacity", "url", "name",
            ]
        ],
        on="event_id",
        how="left",
    )
    frame = frame.merge(
        tiers[["archetype", "tier_key", "tier_rank", "price_multiplier", "inventory_share"]].rename(
            columns={
                "price_multiplier": "tier_price_multiplier",
                "inventory_share": "tier_inventory_share",
            }
        ),
        on=["archetype", "tier_key"],
        how="left",
    )

    as_of = pd.to_datetime(frame["as_of_date"])
    event_date = pd.to_datetime(frame["event_date"])
    target_date = pd.to_datetime(frame["target_date"])

    frame["event_dow"] = event_date.dt.dayofweek
    frame["event_month"] = event_date.dt.month
    frame["event_is_weekend"] = event_date.dt.dayofweek.isin((4, 5, 6)).astype(int)
    frame["as_of_dow"] = as_of.dt.dayofweek
    frame["target_dow"] = target_date.dt.dayofweek
    frame["target_is_weekend"] = target_date.dt.dayofweek.isin((4, 5, 6)).astype(int)
    frame["days_since_announce"] = (as_of - pd.to_datetime(frame["announce_date"])).dt.days
    frame["log_days_until_event"] = np.log1p(frame["days_until_event"])
    frame["log_horizon"] = np.log1p(frame["horizon_days"])
    frame["is_ga"] = frame["is_ga"].astype(int)

    # Price relative to the tier's own multiplier, so the model sees "expensive
    # for this kind of seat" rather than the absolute dollar level.
    frame["log_price_vs_tier_base"] = np.log(
        frame["get_in_price"] / frame["tier_price_multiplier"].clip(lower=0.01)
    )

    for column in CATEGORICAL_FEATURES:
        frame[column] = frame[column].astype("category")

    return frame


def design_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    """Select the model inputs, after checking nothing forbidden slipped in.

    The guard is on the SELECTED feature list, not on the frame's columns: the
    frame legitimately carries event_id for grouping, prices for reporting and
    urls for the UI. What must stay clean is what reaches the estimator.
    """
    assert_no_generative_leakage(ALL_FEATURES)
    missing = [c for c in ALL_FEATURES if c not in frame.columns]
    if missing:
        raise ValueError(f"feature frame is missing columns: {missing}")
    matrix = frame[ALL_FEATURES].copy()
    for column in CATEGORICAL_FEATURES:
        matrix[column] = matrix[column].astype("category")
    return matrix
