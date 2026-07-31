"""Leakage, serving and API tests.

The leakage tests are the important ones. Everything else in this project can
be wrong in a way that shows up as a bad number; leakage is wrong in a way that
shows up as a *good* number, which is far more dangerous.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tixtime import db
from tixtime.api.main import app
from tixtime.config import WARMUP_DAYS, as_of_date
from tixtime.ml import dataset
from tixtime.ml.features import (
    ALL_FEATURES,
    FORBIDDEN_FEATURES,
    assert_no_generative_leakage,
    build_panel,
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def sample_panel() -> pd.DataFrame:
    with db.warehouse(read_only=True) as con:
        ids = [r[0] for r in con.execute(
            "SELECT event_id FROM events WHERE is_modelable ORDER BY event_id LIMIT 6"
        ).fetchall()]
        snapshots = dataset.load_snapshots(con, ids)
    return build_panel(snapshots)


class TestLeakage:
    def test_simulator_parameters_are_not_features(self):
        """home/away demand_index are direct multiplicative inputs to the
        simulator's base price. Using them as features would train the model to
        invert its own data generator."""
        overlap = FORBIDDEN_FEATURES.intersection(ALL_FEATURES)
        assert not overlap, overlap
        assert_no_generative_leakage(ALL_FEATURES)

    def test_guard_actually_raises(self):
        with pytest.raises(ValueError, match="simulator"):
            assert_no_generative_leakage(["days_until_event", "home_demand_index"])

    def test_history_features_are_truncated_at_as_of(self, sample_panel):
        """Building features on the full series and on the series truncated at
        t must produce a bit-identical row at t. If any history feature peeked
        forward, these would differ."""
        key = sample_panel[["event_id", "tier_key"]].iloc[0]
        with db.warehouse(read_only=True) as con:
            snapshots = dataset.load_snapshots(con, [int(key["event_id"])])
        snapshots = snapshots[snapshots["tier_key"] == key["tier_key"]]

        full = build_panel(snapshots).sort_values("as_of_date").reset_index(drop=True)
        cutoff_index = len(full) // 2
        cutoff_date = full["as_of_date"].iloc[cutoff_index]

        truncated_source = snapshots[snapshots["as_of_date"] <= cutoff_date]
        truncated = build_panel(truncated_source).sort_values("as_of_date").reset_index(drop=True)

        columns = [
            "ret_7d", "ret_30d", "price_vs_min_7d", "price_vs_min_30d",
            "price_vs_expanding_median", "vol_7d", "vol_30d",
            "inventory_ratio_vs_open", "history_len_days",
        ]
        left = full.loc[cutoff_index, columns].astype(float).to_numpy()
        right = truncated.iloc[-1][columns].astype(float).to_numpy()
        np.testing.assert_allclose(left, right, rtol=1e-9, atol=1e-12)

    def test_rolling_features_are_defined_on_the_first_served_day(self, sample_panel):
        """Burn-in exists so the earliest exposed rows are not NaN. If they
        were dropped instead, the model would never learn the long-horizon
        regime the product exists to serve."""
        first_rows = (
            sample_panel.sort_values("as_of_date")
            .groupby(["event_id", "tier_key"], as_index=False)
            .head(1)
        )
        for column in ("ret_30d", "price_vs_min_30d", "vol_30d"):
            assert first_rows[column].notna().all(), column

    def test_burn_in_rows_never_reach_the_panel(self, sample_panel):
        assert "is_burn_in" not in sample_panel.columns or not sample_panel["is_burn_in"].any()

    def test_label_builder_refuses_censored_events(self):
        """An event still in progress at the cutoff yields a truncated minimum,
        which biases the label toward BUY. The builder must refuse."""
        with db.warehouse(read_only=True) as con:
            events = dataset.load_events(con)
            future_id = int(
                events.loc[pd.to_datetime(events["event_date"]).dt.date > as_of_date(), "event_id"].iloc[0]
            )
            snapshots = dataset.load_snapshots(con, [future_id])
        panel = build_panel(snapshots)
        with pytest.raises(ValueError, match="censored"):
            dataset.attach_future_labels(panel, events, as_of_date())

    def test_training_population_is_complete_events_only(self):
        with db.warehouse(read_only=True) as con:
            cutoff = as_of_date()
            ids = dataset.resolved_event_ids(con, cutoff)
            events = dataset.load_events(con)
        chosen = events[events["event_id"].isin(ids)]
        assert len(chosen) > 0
        assert (pd.to_datetime(chosen["event_date"]).dt.date < cutoff).all()


class TestServingRespectsTheClock:
    def test_timeline_history_never_passes_the_as_of_date(self, client):
        with db.warehouse(read_only=True) as con:
            event_id = con.execute(
                "SELECT event_id FROM deal_board ORDER BY expected_saving_pct DESC LIMIT 1"
            ).fetchone()[0]
        as_of = as_of_date()
        payload = client.get(f"/api/events/{event_id}/timeline?as_of={as_of}").json()
        assert payload["history"], "expected some history"
        assert max(point["date"] for point in payload["history"]) <= as_of.isoformat()

    def test_moving_the_clock_back_shortens_history(self, client):
        with db.warehouse(read_only=True) as con:
            event_id = con.execute(
                "SELECT event_id FROM deal_board ORDER BY expected_saving_pct DESC LIMIT 1"
            ).fetchone()[0]
        late = as_of_date()
        early = late - timedelta(days=30)
        long_history = client.get(f"/api/events/{event_id}/timeline?as_of={late}").json()
        short_history = client.get(f"/api/events/{event_id}/timeline?as_of={early}").json()
        assert len(short_history["history"]) < len(long_history["history"])

    def test_forecast_reaches_event_day(self, client):
        with db.warehouse(read_only=True) as con:
            event_id, event_date = con.execute(
                "SELECT d.event_id, e.event_date FROM deal_board d JOIN events e USING (event_id) "
                "WHERE d.days_until_event BETWEEN 20 AND 120 LIMIT 1"
            ).fetchone()
        payload = client.get(f"/api/events/{event_id}/timeline").json()
        assert payload["forecast"], "expected a forecast"
        assert payload["forecast"][-1]["date"] == event_date.isoformat()

    def test_precomputed_board_is_never_served_for_a_different_date(self, client):
        """The deal board is precomputed for ONE as-of date. Serving it for any
        other date returned prices, savings and BUY/WAIT signals derived from
        snapshots dated AFTER the date the response claimed -- a leak straight
        to the product surface, and the clock control invites moving the clock.
        """
        moved = (as_of_date() - timedelta(days=45)).isoformat()

        search = client.get(f"/api/search?as_of={moved}&limit=5").json()
        assert search["board_covers_as_of"] is False
        assert search["board_note"]
        # The catalogue is still browsable, but with no predictions attached.
        for row in search["results"]:
            assert row["price_now"] is None
            assert row["expected_saving"] is None
            assert row["action"] is None
            assert row["sparkline"] is None

        assert client.get(f"/api/deals?as_of={moved}&limit=5").json()["deals"] == []

        current = client.get("/api/search?limit=5").json()
        assert current["board_covers_as_of"] is True
        assert any(row["price_now"] is not None for row in current["results"])

    def test_event_page_still_charts_when_the_board_is_not_built(self, client):
        """Scoping the board to its own date must not silently blank the chart.

        The first attempt at that fix labelled these events 'no_snapshots' --
        telling the user no observations existed while the chart below drew a
        hundred of them -- and then removed the chart entirely because the tier
        was picked from the board. History and forecast are computed live and
        stay correct at any clock date; only the ranking is unavailable.
        """
        moved = (as_of_date() - timedelta(days=45)).isoformat()
        event_id = client.get(f"/api/search?as_of={moved}&limit=1").json()["results"][0]["event_id"]

        detail = client.get(f"/api/events/{event_id}?as_of={moved}").json()["event"]
        assert detail["unforecastable_reason"] == "board_not_built"

        timeline = client.get(f"/api/events/{event_id}/timeline?as_of={moved}").json()
        assert timeline["tier_key"] is not None
        assert timeline["history"], "the chart must still render at a moved clock"
        assert max(p["date"] for p in timeline["history"]) <= moved
        assert timeline["recommendation"]["action"] in {"BUY_NOW", "WAIT"}

    def test_forecast_band_is_ordered(self, client):
        with db.warehouse(read_only=True) as con:
            event_id = con.execute("SELECT event_id FROM deal_board LIMIT 1").fetchone()[0]
        payload = client.get(f"/api/events/{event_id}/timeline").json()
        for point in payload["forecast"]:
            assert point["q10"] <= point["q50"] <= point["q90"], point


class TestProvenance:
    ENDPOINTS = ["/api/meta", "/api/filters", "/api/search?limit=2", "/api/deals?limit=2", "/api/accuracy"]

    @pytest.mark.parametrize("path", ENDPOINTS)
    def test_every_payload_declares_its_source(self, client, path):
        payload = client.get(path).json()
        assert payload["is_simulated"] is True
        assert payload["source"] == "synthetic_v1"
        assert "disclaimer" in payload

    def test_every_priced_row_carries_provenance(self, client):
        for row in client.get("/api/search?limit=20").json()["results"]:
            assert row["source"] == "synthetic_v1"
            assert row["is_simulated"] is True
        for deal in client.get("/api/deals?limit=20").json()["deals"]:
            assert deal["source"] == "synthetic_v1"

    def test_accuracy_states_the_caveat_next_to_the_numbers(self, client):
        payload = client.get("/api/accuracy").json()
        assert "simulated" in payload["interpretation"].lower()
        if payload["backtest"]:
            assert "simulated" in payload["backtest"]["caveat"].lower()


class TestKnownBadSlices:
    """The ~7% of the catalogue that cannot be forecast must produce a named
    state, not a 500 and not a blank chart."""

    @pytest.mark.parametrize(
        "query,expected_reason",
        [
            ("SELECT event_id FROM events WHERE is_tbd LIMIT 1", "date_tbd"),
            ("SELECT event_id FROM events WHERE exclusion_reason='non_game_inventory' LIMIT 1", None),
            ("SELECT event_id FROM events WHERE exclusion_reason='implausible_date' LIMIT 1", None),
        ],
    )
    def test_unforecastable_events_answer_cleanly(self, client, query, expected_reason):
        with db.warehouse(read_only=True) as con:
            row = con.execute(query).fetchone()
        if not row:
            pytest.skip("slice not present")
        response = client.get(f"/api/events/{row[0]}")
        assert response.status_code == 200
        event = response.json()["event"]
        assert event["is_forecastable"] is False
        assert event["unforecastable_reason"]
        if expected_reason:
            assert event["unforecastable_reason"] == expected_reason
        assert client.get(f"/api/events/{row[0]}/timeline").status_code == 200

    def test_missing_event_is_404(self, client):
        assert client.get("/api/events/999999999").status_code == 404

    def test_ga_and_no_seat_selection_events_get_one_tier(self, client):
        with db.warehouse(read_only=True) as con:
            row = con.execute(
                "SELECT event_id FROM events WHERE NOT has_seat_tiers AND is_modelable "
                "AND event_date > ? LIMIT 1", [as_of_date()],
            ).fetchone()
        if not row:
            pytest.skip("no such event")
        payload = client.get(f"/api/events/{row[0]}").json()
        assert payload["event"]["has_seat_tiers"] is False
        assert len(payload["tiers"]) <= 1, "seat tiers must not be invented for these events"

    def test_search_hides_unforecastable_events_by_default(self, client):
        default = client.get("/api/search?limit=60").json()
        assert default["results"], "default search should not be empty"
        assert all(row["is_forecastable"] for row in default["results"])
        widened = client.get("/api/search?limit=60&include_unforecastable=true").json()
        assert widened["total"] > default["total"]


class TestAlerts:
    def test_rule_lifecycle_and_delivery_status(self, client):
        with db.warehouse(read_only=True) as con:
            event_id = con.execute("SELECT event_id FROM deal_board LIMIT 1").fetchone()[0]
        created = client.post(
            "/api/alerts/rules",
            json={"event_id": int(event_id), "rule_type": "target_price", "threshold": 100000.0},
        )
        assert created.status_code == 201
        rule_id = created.json()["rule_id"]

        fired = client.post("/api/alerts/evaluate").json()["fired"]
        assert any(alert["rule_id"] == rule_id for alert in fired), "a huge target should always fire"
        assert all(alert["status"] in {"delivered", "unconfigured", "failed"} for alert in fired)

        # Idempotent for the same simulated day.
        assert not [a for a in client.post("/api/alerts/evaluate").json()["fired"] if a["rule_id"] == rule_id]

        client.delete(f"/api/alerts/rules/{rule_id}")
        assert rule_id not in {r["rule_id"] for r in client.get("/api/alerts/rules").json()["rules"] if r["active"]}

        # These tests run against the real warehouse, so they must not leave
        # rows behind -- otherwise every run adds another synthetic alert to
        # the user's inbox.
        with db.warehouse() as con:
            con.execute("DELETE FROM alert_events WHERE rule_id = ?", [rule_id])
            con.execute("DELETE FROM alert_rules WHERE rule_id = ?", [rule_id])

    def test_any_tier_rule_matches_every_tier(self, client):
        """Regression: DuckDB hands SQL NULL back as pandas NaN, and passing
        that NaN into `CAST(? AS VARCHAR) IS NULL` rendered the string 'nan',
        so a rule watching ANY tier matched no tiers and silently never fired.
        It failed with no error, which is why it needs its own test."""
        with db.warehouse(read_only=True) as con:
            event_id = con.execute(
                "SELECT event_id FROM deal_board GROUP BY event_id HAVING count(*) > 1 LIMIT 1"
            ).fetchone()[0]

        # A rule with tier_key omitted entirely -> NULL in the database.
        created = client.post(
            "/api/alerts/rules",
            json={"event_id": int(event_id), "rule_type": "target_price", "threshold": 100000.0},
        )
        rule_id = created.json()["rule_id"]
        try:
            fired = client.post("/api/alerts/evaluate").json()["fired"]
            assert any(a["rule_id"] == rule_id for a in fired), (
                "an any-tier rule with an unreachable target must still fire"
            )
        finally:
            with db.warehouse() as con:
                con.execute("DELETE FROM alert_events WHERE rule_id = ?", [rule_id])
                con.execute("DELETE FROM alert_rules WHERE rule_id = ?", [rule_id])

    def test_unknown_rule_type_is_rejected(self, client):
        response = client.post(
            "/api/alerts/rules", json={"event_id": 1, "rule_type": "not_a_rule"}
        )
        assert response.status_code == 400
