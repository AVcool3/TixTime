"""Turn trained models into forecasts and buy recommendations.

The sweep here is over HORIZON, not over days_until_event. That distinction is
the whole reason the models are trained as (as-of, horizon) pairs: horizon is
an ordinary input feature with full training support, so asking the model
"what will this cost in 21 days?" is an in-distribution question. Sweeping
days_until_event while freezing the rolling/inventory features -- the obvious
alternative -- asks the model about states that never occur.
"""

from __future__ import annotations

import functools
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from tixtime import db
from tixtime.config import ARTIFACT_DIR, NEAR_MIN_TOLERANCE, SYNTHETIC_SOURCE
from tixtime.ml import dataset
from tixtime.ml.features import build_pairs, build_panel, design_matrix

# Horizons the forecast curve is evaluated on. Dense near the as-of date where
# the decision is live, sparser further out -- the curve is interpolated for
# display, and predicting every single day would be slower for no visible gain.
_CURVE_HORIZONS = (1, 2, 3, 4, 5, 7, 9, 12, 14, 17, 21, 25, 30, 37, 45, 52, 60, 75, 90, 110, 130, 150)

BUY_NOW = "BUY_NOW"
WAIT = "WAIT"
NO_FORECAST = "NO_FORECAST"


@dataclass(frozen=True)
class ForecastPoint:
    as_of_date: str
    days_until_event: int
    horizon_days: int
    q10: float
    q50: float
    q90: float


@dataclass(frozen=True)
class Recommendation:
    action: str
    confidence: str
    price_now: float | None
    forecast_min: float | None
    forecast_min_date: str | None
    days_to_forecast_min: int | None
    expected_saving: float
    expected_saving_pct: float
    rationale: str
    purchase_url: str
    source: str
    model_version: str


@functools.lru_cache(maxsize=1)
def load_artifact(path: Path = ARTIFACT_DIR / "models.joblib") -> dict:
    if not Path(path).exists():
        raise FileNotFoundError(
            f"no trained model at {path}. Run `python -m tixtime.ml.train` first."
        )
    return joblib.load(path)


def _align_categories(matrix: pd.DataFrame, artifact: dict) -> pd.DataFrame:
    """Force the serving frame onto the training category sets.

    HistGradientBoosting bakes the category ORDER into the fitted model. A
    serving frame built from one event sees only that event's league, so its
    category codes would silently mean something different.
    """
    aligned = matrix.copy()
    for column, categories in artifact["categories"].items():
        values = aligned[column].astype(object)
        # Map anything the model never saw to NaN explicitly. Passing unseen
        # values straight to pd.Categorical silently coerces them and is
        # deprecated (it raises in a future pandas); HistGradientBoosting
        # handles NaN natively, so an unseen league or postseason tier degrades
        # to "no information" instead of quietly meaning another category.
        known = set(categories)
        values = values.where(values.isin(known), other=None)
        aligned[column] = pd.Categorical(values, categories=categories)
    return aligned


def _series_context(con, event_id: int, as_of: date):
    """Panel rows for one event, truncated at the as-of date."""
    events = dataset.load_events(con, modelable_only=False)
    events = events[events["event_id"] == event_id]
    if events.empty:
        return None, None, None
    tiers = dataset.load_tiers(con)
    snapshots = dataset.load_snapshots(con, [event_id])
    if snapshots.empty:
        return events, tiers, None
    snapshots = snapshots[pd.to_datetime(snapshots["as_of_date"]).dt.date <= as_of]
    return events, tiers, snapshots


def forecast_curve(
    con, event_id: int, tier_key: str, as_of: date, artifact: dict | None = None
) -> list[ForecastPoint]:
    """Predicted price path from `as_of` to event day, with an 80% band."""
    artifact = artifact or load_artifact()
    events, tiers, snapshots = _series_context(con, event_id, as_of)
    if snapshots is None or snapshots.empty:
        return []

    panel = build_panel(snapshots)
    panel = panel[panel["tier_key"] == tier_key]
    if panel.empty:
        return []

    current = panel.sort_values("as_of_date").iloc[[-1]]
    days_left = int(current["days_until_event"].iloc[0])
    if days_left <= 0:
        return []

    horizons = [h for h in _CURVE_HORIZONS if h <= days_left]
    # Always terminate on event day. Without this the curve stops at the last
    # tabulated horizon below days_left -- a 57-day-out event drew a forecast
    # ending 5 days early, and the recommended buy window sat clipped against
    # the right edge of the chart.
    if days_left not in horizons:
        horizons.append(days_left)
    horizons = sorted(set(horizons))

    rows = pd.concat([current.assign(horizon_days=h) for h in horizons], ignore_index=True)
    rows["days_until_event_target"] = rows["days_until_event"] - rows["horizon_days"]
    rows["target_date"] = pd.to_datetime(rows["as_of_date"]) + pd.to_timedelta(
        rows["horizon_days"], unit="D"
    )
    from tixtime.ml.features import _attach_context

    rows = _attach_context(rows, events, tiers)
    matrix = _align_categories(design_matrix(rows), artifact)

    price_now = float(current["get_in_price"].iloc[0])
    models = artifact["models"]
    predictions = {
        name: price_now * np.exp(models[name].predict(matrix)) for name in ("q10", "q50", "q90")
    }
    # Quantile heads are fitted independently and can cross on individual rows;
    # sorting restores the ordering the band's meaning depends on.
    stacked = np.sort(
        np.vstack([predictions["q10"], predictions["q50"], predictions["q90"]]), axis=0
    )

    base_date = pd.Timestamp(current["as_of_date"].iloc[0])
    return [
        ForecastPoint(
            as_of_date=(base_date + timedelta(days=int(h))).date().isoformat(),
            days_until_event=days_left - int(h),
            horizon_days=int(h),
            q10=round(float(stacked[0, i]), 2),
            q50=round(float(stacked[1, i]), 2),
            q90=round(float(stacked[2, i]), 2),
        )
        for i, h in enumerate(horizons)
    ]


def recommend(
    con, event_id: int, tier_key: str, as_of: date, artifact: dict | None = None
) -> Recommendation:
    """BUY_NOW or WAIT, with the dollar value of waiting."""
    artifact = artifact or load_artifact()
    events, tiers, snapshots = _series_context(con, event_id, as_of)

    url = "" if events is None or events.empty else str(events["url"].iloc[0])
    empty = Recommendation(
        action=NO_FORECAST, confidence="none", price_now=None, forecast_min=None,
        forecast_min_date=None, days_to_forecast_min=None, expected_saving=0.0,
        expected_saving_pct=0.0, rationale="No price history available for this event.",
        purchase_url=url, source=SYNTHETIC_SOURCE, model_version=artifact["version"],
    )
    if snapshots is None or snapshots.empty:
        return empty

    panel = build_panel(snapshots)
    panel = panel[panel["tier_key"] == tier_key]
    if panel.empty:
        return empty

    current = panel.sort_values("as_of_date").iloc[[-1]]
    days_left = int(current["days_until_event"].iloc[0])
    price_now = float(current["get_in_price"].iloc[0])
    if days_left <= 0:
        return Recommendation(
            action=BUY_NOW, confidence="certain", price_now=round(price_now, 2),
            forecast_min=round(price_now, 2), forecast_min_date=as_of.isoformat(),
            days_to_forecast_min=0, expected_saving=0.0, expected_saving_pct=0.0,
            rationale="The event is today -- there is no later price to wait for.",
            purchase_url=url, source=SYNTHETIC_SOURCE, model_version=artifact["version"],
        )

    # The decision head: how much is the cheapest remaining price below today's?
    decision_row = current.assign(horizon_days=1)
    decision_row["days_until_event_target"] = decision_row["days_until_event"] - 1
    decision_row["target_date"] = pd.to_datetime(decision_row["as_of_date"]) + timedelta(days=1)
    from tixtime.ml.features import _attach_context

    decision_row = _attach_context(decision_row, events, tiers)
    decision_matrix = _align_categories(design_matrix(decision_row), artifact)
    predicted_min_return = float(artifact["models"]["min_return"].predict(decision_matrix)[0])
    predicted_min_price = price_now * float(np.exp(predicted_min_return))

    # The curve says WHEN that minimum is expected to land.
    curve = forecast_curve(con, event_id, tier_key, as_of, artifact)
    if curve:
        best = min(curve, key=lambda point: point.q50)
        best_date, best_days = best.as_of_date, best.horizon_days
    else:
        best_date, best_days = None, None

    saving = max(price_now - predicted_min_price, 0.0)
    saving_pct = saving / price_now if price_now else 0.0

    # A saving smaller than the tolerance is not worth acting on, and telling
    # someone to wait for it would be noise dressed as advice.
    wait = predicted_min_return < np.log(1.0 / NEAR_MIN_TOLERANCE)
    if wait:
        action = WAIT
        rationale = (
            f"Waiting is projected to save about ${saving:,.0f} "
            f"({saving_pct:.0%}); prices look likely to bottom around "
            f"{best_date or 'later in the window'}."
        )
    else:
        action = BUY_NOW
        rationale = (
            "Today's price is projected to be within "
            f"{(NEAR_MIN_TOLERANCE - 1):.0%} of the cheapest price still to come -- "
            "waiting is not expected to pay."
        )

    # Confidence comes from how decisive the projected move is relative to the
    # tolerance band, not from a model-reported probability we do not have.
    margin = abs(predicted_min_return - np.log(1.0 / NEAR_MIN_TOLERANCE))
    confidence = "high" if margin > 0.08 else "medium" if margin > 0.03 else "low"

    return Recommendation(
        action=action,
        confidence=confidence,
        price_now=round(price_now, 2),
        forecast_min=round(predicted_min_price, 2),
        forecast_min_date=best_date,
        days_to_forecast_min=best_days,
        expected_saving=round(saving, 2),
        expected_saving_pct=round(saving_pct, 4),
        rationale=rationale,
        purchase_url=url,
        source=SYNTHETIC_SOURCE,
        model_version=artifact["version"],
    )


def recommend_all_tiers(con, event_id: int, as_of: date) -> list[dict]:
    """Recommendation per seat tier, best expected saving first."""
    artifact = load_artifact()
    tiers = con.execute(
        "SELECT DISTINCT tier_key FROM price_snapshots WHERE event_id = ?", [event_id]
    ).fetchall()
    results = []
    for (tier_key,) in tiers:
        rec = recommend(con, event_id, tier_key, as_of, artifact)
        results.append({"tier_key": tier_key, **asdict(rec)})
    return sorted(results, key=lambda r: -r["expected_saving_pct"])
