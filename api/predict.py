"""Turn the price model into a buy-date recommendation via the
days-until-event sweep: ask the model for a price at every remaining
purchase date and recommend the argmin."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import joblib
import pandas as pd

MODEL_PATH = Path(__file__).resolve().parent.parent / "ml" / "model.joblib"

_bundle = None


def get_bundle():
    global _bundle
    if _bundle is None:
        if not MODEL_PATH.exists():
            raise FileNotFoundError(
                f"{MODEL_PATH} not found — run `python -m ml.train` first."
            )
        _bundle = joblib.load(MODEL_PATH)
    return _bundle


def best_time_to_buy(latest: dict) -> dict:
    """`latest` is the newest snapshot_features row for the event, with the
    engineered features (price_ratio, price_slope_7d) already computed."""
    from ml.features import encode  # local import keeps API boot cheap

    bundle = get_bundle()
    event_date = pd.to_datetime(latest["event_datetime"]).date()
    days_out_today = (event_date - date.today()).days
    if days_out_today < 1:
        return {"error": "event is in the past or today"}

    days = list(range(days_out_today, 0, -1))
    sweep = pd.DataFrame([latest] * len(days))
    sweep["days_until_event"] = days
    preds = bundle["model"].predict(encode(sweep, bundle["columns"]))

    curve = [
        {
            "buy_date": (date.today() + timedelta(days=days_out_today - d)).isoformat(),
            "days_until_event": d,
            "predicted_price": round(float(p), 2),
        }
        for d, p in zip(days, preds)
    ]
    best = min(curve, key=lambda c: c["predicted_price"])
    today_price = curve[0]["predicted_price"]
    savings = round(today_price - best["predicted_price"], 2)
    wait = savings > max(5.0, 0.05 * today_price)

    return {
        "curve": curve,
        "best_buy_date": best["buy_date"],
        "best_predicted_price": best["predicted_price"],
        "today_predicted_price": today_price,
        "expected_savings": savings,
        "recommendation": "wait" if wait else "buy_now",
        "reasoning": (
            f"Model expects the low (~${best['predicted_price']:.0f}) around "
            f"{best['buy_date']} ({best['days_until_event']} days before the show); "
            f"buying today runs ~${today_price:.0f}."
            + (
                f" Waiting should save about ${savings:.0f}."
                if wait
                else " That gap is too small to gamble on — buy when ready."
            )
        ),
        "model_trained_at": bundle.get("trained_at"),
        "caveat": (
            "Demand features (listing count, price trend) are held at today's "
            "values across the sweep, so confidence decays for far-out dates."
        ),
    }
