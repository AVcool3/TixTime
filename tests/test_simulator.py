"""Calibration and reproducibility tests for the price simulator.

These lock in properties that were arrived at empirically and are easy to
break with an innocent-looking parameter tweak. Each one failed at some point
during calibration, and the comment says what the failure looked like.
"""

from __future__ import annotations

import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from tixtime import db
from tixtime.catalog.reference import tiers_for_event
from tixtime.config import SIMULATION, WARMUP_DAYS
from tixtime.pricing.simulator import _demand_score, _event_rng, simulate_event


@pytest.fixture(scope="module")
def events() -> pd.DataFrame:
    with db.warehouse(read_only=True) as con:
        return con.execute(
            """
            SELECT e.event_id, e.event_date, e.horizon_days, e.league, e.is_ga,
                   e.postseason_tier, e.demand_rank, e.home_demand_index,
                   e.away_demand_index, v.archetype
            FROM events e JOIN venues v USING (venue_id)
            WHERE e.is_modelable
            ORDER BY e.event_id
            LIMIT 400
            """
        ).df()


@pytest.fixture(scope="module")
def paths(events: pd.DataFrame) -> pd.DataFrame:
    """Cheapest-tier realised path summary, one row per event."""
    rows = []
    for _, event in events.iterrows():
        frame = simulate_event(event)
        frame = frame[~frame["is_burn_in"]]
        tier = frame["tier_key"].iloc[-1]
        series = frame[frame["tier_key"] == tier].sort_values("days_until_event", ascending=False)
        prices = series["get_in_price"].to_numpy()
        days = series["days_until_event"].to_numpy()
        rng = _event_rng(int(event["event_id"]), SIMULATION.seed)
        rows.append(
            {
                "event_id": int(event["event_id"]),
                "demand": _demand_score(event, rng),
                "optimal_day": int(days[prices.argmin()]),
                "open_price": float(prices[0]),
                "min_price": float(prices.min()),
                "final_price": float(prices[-1]),
            }
        )
    return pd.DataFrame(rows)


class TestReproducibility:
    def test_same_event_gives_identical_path_across_calls(self, events):
        event = events.iloc[0]
        first, second = simulate_event(event), simulate_event(event)
        pd.testing.assert_frame_equal(first, second)

    def test_paths_are_stable_across_processes(self, events):
        """Guards against seeding with Python's hash(), which is salted per
        process by PYTHONHASHSEED and would silently regenerate a different
        market on every run."""
        code = (
            "import sys; sys.path.insert(0,'.');"
            "from tixtime import db;"
            "from tixtime.pricing.simulator import simulate_event;"
            "con=db.connect(read_only=True);"
            "e=con.execute('''SELECT e.event_id,e.event_date,e.horizon_days,e.league,e.is_ga,"
            "e.postseason_tier,e.demand_rank,e.home_demand_index,e.away_demand_index,v.archetype "
            "FROM events e JOIN venues v USING(venue_id) WHERE e.is_modelable "
            "ORDER BY e.event_id LIMIT 1''').df().iloc[0];"
            "print(round(float(simulate_event(e).get_in_price.sum()),4))"
        )
        runs = {
            subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True, check=True
            ).stdout.strip()
            for _ in range(2)
        }
        assert len(runs) == 1, f"simulator is not reproducible across processes: {runs}"


class TestInvariants:
    def test_get_in_never_exceeds_median(self, events):
        for _, event in events.head(40).iterrows():
            frame = simulate_event(event)
            assert (frame["get_in_price"] <= frame["median_price"]).all()

    def test_prices_are_positive(self, events):
        for _, event in events.head(40).iterrows():
            frame = simulate_event(event)
            assert (frame["get_in_price"] > 0).all()

    def test_burn_in_covers_the_longest_rolling_window(self, events):
        """Rolling 30-day features must be fully defined on the first exposed
        day, otherwise the model never learns the long-horizon regime the
        product exists to serve."""
        event = events.iloc[0]
        frame = simulate_event(event)
        burn = frame[frame["is_burn_in"]]
        exposed = frame[~frame["is_burn_in"]]
        assert burn["days_until_event"].min() > exposed["days_until_event"].max()
        per_tier = burn.groupby("tier_key").size()
        assert (per_tier >= WARMUP_DAYS).all()

    def test_ga_events_get_exactly_one_tier(self, events):
        ga = events[events["is_ga"].astype(bool)]
        if ga.empty:
            pytest.skip("no GA events in sample")
        frame = simulate_event(ga.iloc[0])
        assert frame["tier_key"].nunique() == 1
        assert frame["tier_key"].iloc[0] == "ga"

    def test_hockey_and_basketball_get_different_tier_names(self):
        """The same building is courtside for basketball and glass for hockey."""
        nba = {t.key for t in tiers_for_event("nba", "arena", False)}
        nhl = {t.key for t in tiers_for_event("nhl", "arena", False)}
        assert "courtside" in nba and "glass" in nhl
        assert nba != nhl


class TestCalibration:
    def test_decline_into_event_is_the_dominant_regime(self, paths):
        """Sweeting (2012) finds secondary prices fall into game day. An
        earlier calibration centred the terminal branch at demand 0.50, which
        made half the catalogue run up and inverted the documented default."""
        bottoms_at_end = (paths["optimal_day"] <= 3).mean()
        assert 0.55 <= bottoms_at_end <= 0.85, bottoms_at_end

    def test_late_run_up_is_a_real_but_minority_regime(self, paths):
        """If nothing ever ran up, waiting would be free and the whole
        prediction problem would be vacuous."""
        runs_up = (paths["optimal_day"] > 7).mean()
        assert 0.12 <= runs_up <= 0.40, runs_up

    def test_optimal_day_tracks_demand(self, paths):
        """The entire product premise: WHEN to buy depends on the event. At one
        point this correlation was -0.04 -- hot and soft events bottomed on the
        same day and the seat/date recommendation was noise."""
        corr = float(np.corrcoef(paths["demand"], paths["optimal_day"])[0, 1])
        assert corr > 0.45, corr

    def test_open_to_trough_fall_is_plausible(self, paths):
        """A calibration that stacked several large amplitude terms implied
        prices more than halving over a listing window."""
        fall = (1 - paths["min_price"] / paths["open_price"]).median()
        assert 0.15 <= fall <= 0.40, fall

    def test_optimal_day_is_not_pinned_to_one_value(self, paths):
        assert paths["optimal_day"].nunique() > 20

    def test_per_tier_optimal_days_genuinely_differ(self, events):
        """If a tier's price were the event path times a constant, every tier
        would share an argmin and the per-seat recommendation would be noise."""
        spreads = []
        for _, event in events.head(120).iterrows():
            frame = simulate_event(event)
            frame = frame[~frame["is_burn_in"]]
            if frame["tier_key"].nunique() < 2:
                continue
            best = frame.loc[frame.groupby("tier_key")["get_in_price"].idxmin()]
            spreads.append(best["days_until_event"].max() - best["days_until_event"].min())
        assert np.median(spreads) >= 3, np.median(spreads)


class TestRegimes:
    """The regime holdout is only meaningful if the alternative regimes are
    genuinely structurally different and the trained-on one is untouched."""

    def test_v1_regime_reproduces_the_default_market(self, events):
        from tixtime.pricing.regimes import V1

        event = events.iloc[0]
        pd.testing.assert_frame_equal(simulate_event(event), simulate_event(event, regime=V1))

    def test_alternative_regimes_produce_different_markets(self, events):
        from tixtime.pricing.regimes import V2_SHARP, V3_INVERTED

        event = events.iloc[0]
        base = simulate_event(event)["get_in_price"].to_numpy()
        for regime in (V2_SHARP, V3_INVERTED):
            other = simulate_event(event, regime=regime)["get_in_price"].to_numpy()
            assert not np.allclose(base, other), regime.key

    def test_regime_rows_carry_their_own_source_tag(self, events):
        from tixtime.pricing.regimes import V2_SHARP

        frame = simulate_event(events.iloc[0], regime=V2_SHARP)
        assert set(frame["source"].unique()) == {"synthetic_v2_sharp"}

    def test_inverted_regime_reverses_the_demand_to_timing_relationship(self, events):
        """The adversarial regime must actually be adversarial: if its
        correlation had the same sign as v1, the holdout would prove nothing."""
        from tixtime.pricing.regimes import V1, V3_INVERTED

        correlations = {}
        for regime in (V1, V3_INVERTED):
            demands, optimal = [], []
            for _, event in events.head(150).iterrows():
                frame = simulate_event(event, regime=regime)
                frame = frame[~frame["is_burn_in"]]
                tier = frame["tier_key"].iloc[-1]
                series = frame[frame["tier_key"] == tier]
                prices = series["get_in_price"].to_numpy()
                optimal.append(series["days_until_event"].to_numpy()[prices.argmin()])
                demands.append(_demand_score(event, _event_rng(int(event["event_id"]), SIMULATION.seed)))
            correlations[regime.key] = float(np.corrcoef(demands, optimal)[0, 1])

        assert correlations[V1.key] > 0.3, correlations
        assert correlations[V3_INVERTED.key] < correlations[V1.key] - 0.4, correlations
