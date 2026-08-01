"""Walk-forward backtest of the buy-timing policy.

Run with:  python -m tixtime.ml.backtest

WHAT IS BEING MEASURED
----------------------
Not "is the price forecast accurate" -- a low MAPE on a price nobody acts on
is worthless. What matters is the decision: following the model's advice from
some starting point, what do you actually end up paying, versus what you could
have paid?

The model is trained only on events whose price paths completed before the
as-of clock. Every event scored here happens AFTER that clock, so the model
has never seen it. Those events do have complete realised paths in the
warehouse, which is what makes scoring possible.

THE POLICY, NOT A SINGLE PREDICTION
-----------------------------------
A recommendation is not a one-shot call, so scoring it as one would flatter it.
The MODEL strategy is simulated as a policy: stand at the start date, ask the
model each day, and buy the first time it says BUY_NOW (falling through to
event day if it never does). That is what a user following the site would
actually experience, including the cost of being told to wait too long.

Baselines it has to beat:
  BUY_NOW      buy on the first day considered -- what you do with no tool
  ALWAYS_WAIT  never buy until event day. This is the ZERO-SKILL baseline and
               it matters more than any of the others: the simulated market
               declines into game day for ~70% of events, so simply waiting
               scores ~70% "within 5% of the best price" all on its own. Any
               hit-rate figure that is not compared against this number is
               meaningless, and an earlier version of this report led with
               exactly that meaningless figure.
  FIXED_7/14/30  the folk heuristics ("buy two weeks out")
  ORACLE       perfect hindsight; the best achievable, so regret against it is
               the honest measure of how much of the opportunity was captured

WHERE THE MODEL'S SKILL ACTUALLY IS
-----------------------------------
Not in the hit rate -- ALWAYS_WAIT matches it. It is in the mean price paid:
waiting to event day is right most of the time but catastrophic on the events
that run up, and the model's job is to identify those. Judge it on mean_paid
and savings_captured, not on hit_rate_within_5pct.

HONEST READING
--------------
These numbers are computed on simulated prices. They measure whether the
pipeline -- features, leakage control, policy, serving -- works end to end.
They are NOT evidence about real ticket markets, and the reported figures
carry that caveat wherever they surface.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from tixtime import db
from tixtime.config import NEAR_MIN_TOLERANCE, REPORT_DIR, WAREHOUSE, as_of_date
from tixtime.ml import dataset
from tixtime.ml.features import _attach_context, build_panel, design_matrix
from tixtime.ml.serve import _align_categories, load_artifact

# Decision windows to evaluate from, in days before the event. A user standing
# 90 days out faces a different problem from one standing 14 days out, and a
# single headline number would hide that.
START_WINDOWS = (90, 60, 30, 14)


@dataclass
class StrategyResult:
    name: str
    mean_paid: float
    mean_regret: float
    mean_regret_pct: float
    savings_captured_pct: float
    hit_rate_within_5pct: float
    n: int


def _simulate(
    series: pd.DataFrame, predicted_min_return: np.ndarray, start_days: int
) -> dict | None:
    """Score every strategy for one (event, tier) series from one start point.

    `series` is ordered from the start date toward the event.
    """
    window = series[series["days_until_event"] <= start_days]
    if len(window) < 5:
        return None
    prices = window["get_in_price"].to_numpy()
    days = window["days_until_event"].to_numpy()
    signals = predicted_min_return[series["days_until_event"] <= start_days]

    oracle = float(prices.min())
    buy_now = float(prices[0])
    if buy_now <= 0:
        return None

    # MODEL: buy the first day the model stops saying "wait".
    threshold = np.log(1.0 / NEAR_MIN_TOLERANCE)
    buy_indices = np.flatnonzero(signals >= threshold)
    model_index = int(buy_indices[0]) if buy_indices.size else len(prices) - 1
    model_paid = float(prices[model_index])

    result = {
        "start_days": start_days,
        "oracle": oracle,
        "BUY_NOW": buy_now,
        # The zero-skill comparator: hold until the doors open.
        "ALWAYS_WAIT": float(prices[-1]),
        "MODEL": model_paid,
        "MODEL_days_waited": int(days[0] - days[model_index]),
        # True when the model never emitted a BUY and the harness fell through
        # to the last day -- i.e. the decision was ALWAYS_WAIT wearing the
        # model's name. Reported, because it is most of them.
        "MODEL_fell_through": bool(buy_indices.size == 0),
    }
    for fixed in (7, 14, 30):
        if fixed > start_days:
            continue
        candidates = np.flatnonzero(days <= fixed)
        result[f"FIXED_{fixed}"] = float(prices[candidates[0]]) if candidates.size else buy_now
    return result


STRATEGY_COLUMNS = ["BUY_NOW", "ALWAYS_WAIT", "MODEL", "FIXED_7", "FIXED_14", "FIXED_30"]


def summarise_strategies(block: pd.DataFrame, strategy_columns=None) -> dict:
    """Score every strategy over a block of decisions.

    Module-level so the regime holdout scores with exactly this function rather
    than a lookalike copy that could drift away from it.
    """
    strategy_columns = strategy_columns or STRATEGY_COLUMNS
    oracle = block["oracle"].to_numpy()
    buy_now = block["BUY_NOW"].to_numpy()
    # The most you could possibly have saved by timing it well.
    achievable = buy_now - oracle
    out = {}
    for column in strategy_columns:
        if column not in block:
            continue
        paid = block[column].to_numpy()
        valid = ~np.isnan(paid)
        if valid.sum() == 0:
            continue
        paid, oracle_v, achievable_v = paid[valid], oracle[valid], achievable[valid]
        regret = paid - oracle_v
        # Portfolio-level, NOT the mean of per-decision ratios. Averaging
        # (achievable - regret)/achievable per row lets decisions where
        # almost nothing was achievable (a couple of cents) divide by
        # near-zero and dominate: the best strategy scored -149% while a
        # worse one scored +19%.
        total_achievable = float(achievable_v.sum())
        captured = 1.0 - float(regret.sum()) / total_achievable if total_achievable > 0 else 0.0
        out[column] = asdict_result(
            StrategyResult(
                name=column,
                mean_paid=float(paid.mean()),
                mean_regret=float(regret.mean()),
                mean_regret_pct=float((regret / oracle_v).mean()),
                savings_captured_pct=captured,
                hit_rate_within_5pct=float((paid <= oracle_v * 1.05).mean()),
                n=int(valid.sum()),
            )
        )
    return out


def _bootstrap(frame: pd.DataFrame, summarise, reps: int = 400, seed: int = 5) -> dict:
    """Cluster bootstrap by event_id.

    The decision count flatters itself badly. 53,696 "decisions" are ~3,000
    events counted once per seat tier and then again per start window, and the
    start windows are nested spans of the SAME price path -- a 14-day decision
    is a suffix of the 90-day one. Treating those as independent would produce
    absurdly tight intervals.

    Resampling whole EVENTS with replacement keeps every correlated decision
    for an event together, which is the only unit that is close to independent.
    The interval on the MODEL-minus-baseline GAP is the number that matters:
    the levels move together across resamples, so a gap can be solid even when
    both levels look wobbly.
    """
    rng = np.random.default_rng(seed)
    events = frame["event_id"].unique()
    # Row positions per event, so a resample is an index concat rather than a
    # concat of thousands of DataFrames -- the latter dominates runtime at
    # 3,000 events x 400 reps.
    positions = [group.to_numpy() for _, group in
                 pd.Series(np.arange(len(frame)), index=frame["event_id"].to_numpy()).groupby(level=0)]
    event_count = len(positions)

    samples: dict[str, list[float]] = {}
    for _ in range(reps):
        drawn = rng.integers(0, event_count, size=event_count)
        block = frame.iloc[np.concatenate([positions[i] for i in drawn])]
        stats = summarise(block)
        for name, values in stats.items():
            samples.setdefault(f"{name}.savings_captured_pct", []).append(values["savings_captured_pct"])
            samples.setdefault(f"{name}.mean_paid", []).append(values["mean_paid"])
            samples.setdefault(f"{name}.hit_rate_within_5pct", []).append(values["hit_rate_within_5pct"])
        # Gaps against the two baselines that matter, per resample.
        if "MODEL" in stats:
            for rival in ("ALWAYS_WAIT", "BUY_NOW", "FIXED_30"):
                if rival in stats:
                    samples.setdefault(f"MODEL_minus_{rival}.mean_paid", []).append(
                        stats["MODEL"]["mean_paid"] - stats[rival]["mean_paid"]
                    )
                    samples.setdefault(f"MODEL_minus_{rival}.hit_rate_within_5pct", []).append(
                        stats["MODEL"]["hit_rate_within_5pct"] - stats[rival]["hit_rate_within_5pct"]
                    )

    out = {}
    for key, values in samples.items():
        array = np.asarray(values, dtype=float)
        low, high = np.percentile(array, [2.5, 97.5])
        out[key] = {
            "point": round(float(array.mean()), 4),
            "ci_low": round(float(low), 4),
            "ci_high": round(float(high), 4),
            # For a gap, "excludes zero" is the whole question.
            "excludes_zero": bool(low > 0 or high < 0),
        }
    out["_method"] = (
        f"{reps} cluster-bootstrap resamples over {len(events)} events; every decision for a "
        "resampled event travels with it, because tiers and nested start windows of one event "
        "are not independent observations."
    )
    return out


def run(
    warehouse_path: Path = WAREHOUSE,
    cutoff: date | None = None,
    max_events: int | None = 400,
    verbose: bool = True,
) -> dict:
    cutoff = cutoff or as_of_date()
    artifact = load_artifact()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    with db.warehouse(warehouse_path, read_only=True) as con:
        events = dataset.load_events(con)
        tiers = dataset.load_tiers(con)
        trained_through = date.fromisoformat(artifact["trained_through"])
        held_out = events[pd.to_datetime(events["event_date"]).dt.date > trained_through]
        if held_out.empty:
            raise RuntimeError("no held-out events after the training cutoff")
        chosen = held_out["event_id"].tolist()
        if max_events:
            rng = np.random.default_rng(11)
            chosen = sorted(rng.choice(chosen, size=min(max_events, len(chosen)), replace=False).tolist())
        snapshots = dataset.load_snapshots(con, chosen)

    if verbose:
        print(f"backtesting {len(chosen):,} events unseen by the model "
              f"(trained through {trained_through})")

    panel = build_panel(snapshots)

    # Batch-predict the decision head for every (event, tier, day) at once --
    # calling the serving path per day would be thousands of panel rebuilds.
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

    frame = pd.DataFrame(records)
    if frame.empty:
        raise RuntimeError("backtest produced no scored decisions")

    report = {
        "model_version": artifact["version"],
        "trained_through": artifact["trained_through"],
        "data_source": "synthetic_v1",
        "caveat": (
            "Measured on simulated prices. This shows the pipeline recovers timing "
            "structure it was not shown; it is not evidence about real ticket markets."
        ),
        "n_events": int(frame["event_id"].nunique()),
        "n_decisions": int(len(frame)),
        "by_start_window": {},
        "overall": {},
    }

    strategy_columns = STRATEGY_COLUMNS

    def summarise(block: pd.DataFrame) -> dict:
        return summarise_strategies(block, strategy_columns)

    report["overall"] = summarise(frame)
    report["uncertainty"] = _bootstrap(frame, summarise)
    for start in START_WINDOWS:
        block = frame[frame["start_days"] == start]
        if not block.empty:
            report["by_start_window"][str(start)] = summarise(block)

    report["model_days_waited_median"] = float(frame["MODEL_days_waited"].median())
    report["model_fall_through_rate"] = float(frame["MODEL_fell_through"].mean())
    report["honest_reading"] = (
        "Judge the model on mean_paid and savings_captured, NOT on "
        "hit_rate_within_5pct: the zero-skill ALWAYS_WAIT baseline scores about "
        "the same hit rate, because the simulated market declines into event day "
        "for most events. The model's edge is that it leaves early on the "
        "minority of events that run up, which is where waiting is expensive. "
        f"Note also that {frame['MODEL_fell_through'].mean():.0%} of MODEL decisions "
        "never produced a BUY signal and fell through to event day, and that the "
        "FIXED_T rows are scored on fewer decisions than MODEL (a FIXED_30 rule is "
        "undefined for a decision starting 14 days out), so their n differs."
    )

    (REPORT_DIR / "backtest.json").write_text(json.dumps(report, indent=2))

    if verbose:
        print(f"\n{len(frame):,} decisions over {report['n_events']:,} events\n")
        header = (f"{'strategy':<12} {'mean paid':>10} {'regret':>9} {'captured':>9} "
                  f"{'hit@5%':>7} {'n':>7}")
        print(header)
        print("-" * len(header))
        for name, stats in report["overall"].items():
            print(
                f"{name:<12} {stats['mean_paid']:>10.2f} {stats['mean_regret']:>9.2f} "
                f"{stats['savings_captured_pct']:>9.1%} "
                f"{stats['hit_rate_within_5pct']:>7.1%} {stats['n']:>7,}"
            )
        print(f"\n{report['model_fall_through_rate']:.0%} of MODEL decisions never produced a "
              f"BUY signal and fell through to event day.")
        print("hit@5% is NOT the metric to judge on -- compare MODEL to ALWAYS_WAIT there.\n")

        unc = report.get("uncertainty", {})
        print("cluster-bootstrap 95% intervals on the gaps (negative mean paid = model is cheaper):")
        for rival in ("ALWAYS_WAIT", "BUY_NOW", "FIXED_30"):
            paid = unc.get(f"MODEL_minus_{rival}.mean_paid")
            hit = unc.get(f"MODEL_minus_{rival}.hit_rate_within_5pct")
            if not paid:
                continue
            verdict = "significant" if paid["excludes_zero"] else "NOT significant"
            print(f"  vs {rival:<12} mean paid  {paid['point']:+7.2f}  "
                  f"[{paid['ci_low']:+.2f}, {paid['ci_high']:+.2f}]  {verdict}")
            if hit:
                hv = "significant" if hit["excludes_zero"] else "NOT significant"
                print(f"  {'':<15} hit@5%    {hit['point']:+7.3f}  "
                      f"[{hit['ci_low']:+.3f}, {hit['ci_high']:+.3f}]  {hv}")
        print(f"\nwrote {REPORT_DIR / 'backtest.json'}")
    return report


def asdict_result(result: StrategyResult) -> dict:
    return {
        "mean_paid": round(result.mean_paid, 2),
        "mean_regret": round(result.mean_regret, 2),
        "mean_regret_pct": round(result.mean_regret_pct, 4),
        "savings_captured_pct": round(result.savings_captured_pct, 4),
        "hit_rate_within_5pct": round(result.hit_rate_within_5pct, 4),
        "n": result.n,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the TixTime buy policy")
    parser.add_argument("--warehouse", type=Path, default=WAREHOUSE)
    parser.add_argument("--max-events", type=int, default=400)
    args = parser.parse_args()
    run(args.warehouse, max_events=args.max_events)


if __name__ == "__main__":
    main()
