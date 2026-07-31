"""Loading and label construction for the buy-timing models.

The forward-looking label lives here, in one place, behind an explicit cutoff
argument -- so there is exactly one function in the codebase permitted to look
past the as-of date, and it refuses to do so for events that have not finished.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from tixtime import db


def load_events(con, modelable_only: bool = True) -> pd.DataFrame:
    where = "WHERE e.is_modelable" if modelable_only else ""
    return con.execute(
        f"""
        SELECT e.event_id, e.name, e.league, e.event_type, e.postseason_tier,
               e.event_date, e.event_datetime_utc, e.announce_date, e.is_ga,
               e.seat_selection, e.horizon_days, e.url, e.venue_id,
               e.home_team, e.away_team, e.is_tbd, e.is_modelable,
               e.exclusion_reason,
               v.venue_name, v.city, v.region, v.archetype, v.capacity
        FROM events e JOIN venues v USING (venue_id)
        {where}
        ORDER BY e.event_id
        """
    ).df()


def load_tiers(con) -> pd.DataFrame:
    return con.execute("SELECT * FROM seat_tiers ORDER BY archetype, tier_rank").df()


def load_snapshots(con, event_ids=None, include_burn_in: bool = True) -> pd.DataFrame:
    """Raw price panel. Burn-in rows are included by default because rolling
    features need them; build_panel drops them once they have been used."""
    clauses = []
    params: list = []
    if event_ids is not None:
        ids = list(event_ids)
        if not ids:
            return pd.DataFrame()
        clauses.append(f"event_id IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    if not include_burn_in:
        clauses.append("NOT is_burn_in")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return con.execute(
        f"""
        SELECT event_id, tier_key, as_of_date, days_until_event, get_in_price,
               median_price, listing_count, ticket_count, is_burn_in, source
        FROM price_snapshots {where}
        ORDER BY event_id, tier_key, as_of_date
        """,
        params,
    ).df()


def resolved_event_ids(con, cutoff: date) -> list[int]:
    """Events whose entire price path is complete before the cutoff.

    This is the ONLY admissible training population. An event still in progress
    at the cutoff yields a censored minimum -- the min over a truncated window
    is larger than the true remaining minimum, so "today is near the minimum"
    fires far too often and the model learns the censoring boundary instead of
    the market.
    """
    rows = con.execute(
        "SELECT event_id FROM events WHERE is_modelable AND event_date < ? ORDER BY event_id",
        [cutoff],
    ).fetchall()
    return [int(r[0]) for r in rows]


def attach_future_labels(panel: pd.DataFrame, events: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    """Attach the forward-looking targets used for the buy decision.

    Adds, per (event, tier, as-of):
      min_remaining_price  cheapest price still available on any later day
      y_log_min_return     log(min_remaining / price_now); <= 0 always
      days_to_min          how far ahead that minimum sits
      is_near_min          whether buying now lands within tolerance of it

    Raises if any event in the frame is not complete by `cutoff`, which is the
    guard against censored labels.
    """
    incomplete = events.loc[
        events["event_id"].isin(panel["event_id"].unique())
        & (pd.to_datetime(events["event_date"]).dt.date >= cutoff),
        "event_id",
    ]
    if len(incomplete):
        raise ValueError(
            f"{len(incomplete)} event(s) have not finished by {cutoff}; their labels "
            "would be censored. Filter with resolved_event_ids() first."
        )

    frame = panel.sort_values(
        ["event_id", "tier_key", "days_until_event"], ascending=[True, True, False]
    ).copy()
    grouped = frame.groupby(["event_id", "tier_key"], sort=False)["get_in_price"]

    # Rows are ordered from listing open toward the event, so "still available
    # later" means rows further down the group. Reverse-cumulative minimum of
    # the *strictly* subsequent rows.
    reversed_min = (
        grouped.apply(lambda s: s[::-1].cummin()[::-1].shift(-1)).reset_index(level=[0, 1], drop=True)
    )
    frame["min_remaining_price"] = reversed_min

    # On event day there is no "later", so no buy decision to make.
    frame = frame[frame["min_remaining_price"].notna()].copy()
    frame["y_log_min_return"] = np.log(
        frame["min_remaining_price"] / frame["get_in_price"]
    )
    return frame
