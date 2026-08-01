"""Regime holdout: does the model generalise, or did it memorise its generator?

Run with:  python -m tixtime.ml.regime_test

THE QUESTION
------------
Every accuracy figure in this project is measured on data produced by rules the
same author wrote. The sharpest objection is that a good score may only mean the
model successfully inverted its own generator, which would say nothing about
whether it could time a real market.

This is the test that addresses it. The model is NOT retrained. The same
`synthetic_v1`-trained artefact is pointed at markets built from structurally
different rules -- different curve shape, different timing of the terminal move,
several times the noise, and in one case a REVERSED relationship between demand
and when prices bottom.

HOW TO READ THE OUTPUT
----------------------
The interesting quantity is not the absolute score under a new regime -- the
achievable savings differ, so the levels are not comparable across regimes. It
is whether the model still BEATS ITS BASELINES on that regime's own terms.

  survives     still cheaper than both buying immediately and never waiting.
               The model is reading trajectory and inventory, not reciting
               memorised parameters.
  degrades     still beats buying immediately, but loses to never-waiting.
               Partial transfer.
  fails        loses to buying immediately. On this regime the model is worse
               than useless, and any claim of general skill is unsupported.

`v3_inverted` is designed to be hostile: it reverses the demand-to-timing
mapping the model was taught, on both mechanisms that control it. Doing badly
there would be informative rather than embarrassing.

As measured, the outcome is more specific than "it memorised the simulator":
the model degrades but still beats buying immediately on the inverted regime,
and its actual failure is on `v2_sharp` -- a steeper, noisier market. The
dependency is on curve SHAPE and noise level, not on the demand relationship.
That distinction is only visible because this test exists.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from tixtime import db
from tixtime.config import REPORT_DIR, WAREHOUSE
from tixtime.ml import dataset
from tixtime.ml.backtest import START_WINDOWS, _simulate, summarise_strategies
from tixtime.ml.features import _attach_context, build_panel, design_matrix
from tixtime.ml.serve import _align_categories, load_artifact
from tixtime.pricing.regimes import REGIMES
from tixtime.pricing.simulator import simulate_event


def _score_regime(events, tiers, chosen, regime, artifact) -> pd.DataFrame:
    """Simulate one regime in memory and score the v1-trained policy on it."""
    frames = [
        simulate_event(event, regime=regime)
        for _, event in events[events["event_id"].isin(chosen)].iterrows()
    ]
    snapshots = pd.concat(frames, ignore_index=True)

    panel = build_panel(snapshots)
    rows = panel.copy()
    rows["horizon_days"] = 1
    rows["days_until_event_target"] = rows["days_until_event"] - 1
    rows["target_date"] = pd.to_datetime(rows["as_of_date"]) + pd.Timedelta(days=1)
    contextual = _attach_context(rows, events, tiers)
    matrix = _align_categories(design_matrix(contextual), artifact)
    contextual["predicted_min_return"] = artifact["models"]["min_return"].predict(matrix)

    records = []
    for (event_id, tier_key), series in contextual.groupby(["event_id", "tier_key"], sort=False):
        series = series.sort_values("days_until_event", ascending=False)
        signals = series["predicted_min_return"].to_numpy()
        for start in START_WINDOWS:
            outcome = _simulate(series, signals, start)
            if outcome:
                outcome.update({"event_id": event_id, "tier_key": tier_key})
                records.append(outcome)
    return pd.DataFrame(records)


def _verdict(stats: dict) -> str:
    model = stats.get("MODEL", {}).get("mean_paid")
    buy_now = stats.get("BUY_NOW", {}).get("mean_paid")
    always_wait = stats.get("ALWAYS_WAIT", {}).get("mean_paid")
    if model is None or buy_now is None:
        return "unknown"
    if model > buy_now:
        return "fails"
    if always_wait is not None and model > always_wait:
        return "degrades"
    return "survives"


def run(
    warehouse_path: Path = WAREHOUSE,
    max_events: int = 500,
    verbose: bool = True,
) -> dict:
    artifact = load_artifact()
    trained_through = date.fromisoformat(artifact["trained_through"])
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    with db.warehouse(warehouse_path, read_only=True) as con:
        events = dataset.load_events(con)
        tiers = dataset.load_tiers(con)
        events = events.merge(
            con.execute(
                "SELECT event_id, has_seat_tiers, demand_rank, home_demand_index, "
                "away_demand_index, postseason_tier AS _pt FROM events"
            ).df().drop(columns=["_pt"]),
            on="event_id",
            how="left",
        )

    held_out = events[pd.to_datetime(events["event_date"]).dt.date > trained_through]
    rng = np.random.default_rng(23)
    chosen = sorted(
        rng.choice(held_out["event_id"].to_numpy(), size=min(max_events, len(held_out)),
                   replace=False).tolist()
    )

    if verbose:
        print(f"regime holdout: {len(chosen):,} held-out events, model {artifact['version']} "
              f"(trained on synthetic_v1 only, NOT retrained)\n")

    report = {
        "model_version": artifact["version"],
        "trained_on": "synthetic_v1",
        "n_events": len(chosen),
        "question": (
            "Does the model still beat its baselines when the market's structural rules "
            "change? If it only inverted its own generator, it should not."
        ),
        "regimes": {},
    }

    for key, regime in REGIMES.items():
        frame = _score_regime(events, tiers, chosen, regime, artifact)
        if frame.empty:
            continue
        stats = summarise_strategies(frame)
        verdict = _verdict(stats)
        report["regimes"][key] = {
            "description": regime.description,
            "verdict": verdict,
            "n_decisions": int(len(frame)),
            "model_fall_through_rate": float(frame["MODEL_fell_through"].mean()),
            "strategies": stats,
            "model_vs_buy_now": round(
                stats["MODEL"]["mean_paid"] - stats["BUY_NOW"]["mean_paid"], 2),
            "model_vs_always_wait": round(
                stats["MODEL"]["mean_paid"] - stats["ALWAYS_WAIT"]["mean_paid"], 2),
        }

        if verbose:
            print(f"── {key}")
            print(f"   {regime.description}")
            header = f"   {'strategy':<12} {'mean paid':>10} {'captured':>9} {'hit@5%':>7}"
            print(header)
            for name in ("BUY_NOW", "ALWAYS_WAIT", "MODEL", "FIXED_30"):
                if name in stats:
                    row = stats[name]
                    print(f"   {name:<12} {row['mean_paid']:>10.2f} "
                          f"{row['savings_captured_pct']:>9.1%} {row['hit_rate_within_5pct']:>7.1%}")
            print(f"   verdict: {verdict.upper()}  "
                  f"(vs buy-now {report['regimes'][key]['model_vs_buy_now']:+.2f}, "
                  f"vs always-wait {report['regimes'][key]['model_vs_always_wait']:+.2f})\n")

    by_verdict = {v: [k for k, r in report["regimes"].items() if r["verdict"] == v]
                  for v in ("survives", "degrades", "fails")}
    report["verdicts"] = by_verdict
    parts = [
        f"Trained on synthetic_v1 and never retrained, the policy beats both baselines on "
        f"{len(by_verdict['survives'])} of {len(report['regimes'])} regimes "
        f"({', '.join(by_verdict['survives']) or 'none'})."
    ]
    if by_verdict["fails"]:
        parts.append(
            f"It is worse than simply buying immediately on {', '.join(by_verdict['fails'])}, "
            "so it does not transfer: read the headline backtest as a within-regime result."
        )
    if by_verdict["degrades"]:
        parts.append(
            f"On {', '.join(by_verdict['degrades'])} it still beats buying immediately but loses "
            "to never waiting -- partial transfer."
        )
    parts.append(
        "Which regimes break it locates the dependency rather than merely recording a loss."
    )
    report["summary"] = " ".join(parts)
    (REPORT_DIR / "regime_holdout.json").write_text(json.dumps(report, indent=2))
    if verbose:
        print(report["summary"])
        print(f"\nwrote {REPORT_DIR / 'regime_holdout.json'}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Regime-holdout generalisation test")
    parser.add_argument("--warehouse", type=Path, default=WAREHOUSE)
    parser.add_argument("--max-events", type=int, default=500)
    args = parser.parse_args()
    run(args.warehouse, args.max_events)


if __name__ == "__main__":
    main()
