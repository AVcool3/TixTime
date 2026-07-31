"""Load the SeatGeek CSV export into the DuckDB warehouse.

Run with:  python -m tixtime.catalog.ingest
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from tixtime import db
from tixtime.config import RAW_EVENTS_CSV, SIMULATION, WAREHOUSE, WARMUP_DAYS
from tixtime.catalog.parse import (
    parse_name,
    parse_url,
    parse_url_geo_fallback,
    titleize_slug,
)
from tixtime.catalog.reference import (
    ARCHETYPE_CAPACITY,
    DEFAULT_DEMAND_INDEX,
    FRANCHISES,
    GENERAL_ADMISSION,
    LEAGUE_ARCHETYPE,
    SEAT_TIERS,
    VENUE_NAME_OVERRIDES,
    franchise_for_name,
    franchise_for_slug,
)

# Events dated beyond this are data errors in the export (a handful sit in
# 2056) and are dropped outright.
MAX_SANE_EVENT_DATE = pd.Timestamp("2027-01-01")


def _read_raw(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, low_memory=False)
    required = {"eventId", "name", "datetimeUtc", "venueId", "url"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{csv_path} is missing required columns: {sorted(missing)}")
    return df


def build_frames(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Turn the raw export into (events, venues, franchises, seat_tiers)."""
    parsed_urls = [parse_url(u) for u in df["url"]]

    # Two-pass geography: learn the city vocabulary from the urls that parse
    # cleanly, then retry the stragglers against it.
    known_cities = frozenset(p.city for p in parsed_urls if p.city)
    for i, (url, parsed) in enumerate(zip(df["url"], parsed_urls)):
        if not parsed.has_geo and parsed.league:
            parsed_urls[i] = parse_url_geo_fallback(url, known_cities)

    parsed_names = [parse_name(n) for n in df["name"]]

    event_dt = pd.to_datetime(df["datetimeUtc"], errors="coerce")
    announce = pd.to_datetime(df.get("announceDate"), errors="coerce")
    visible = pd.to_datetime(df.get("visibleAt"), errors="coerce")

    league = pd.Series([p.league for p in parsed_urls], index=df.index)
    archetype = league.map(LEAGUE_ARCHETYPE).fillna(GENERAL_ADMISSION)
    is_ga = df["isGa"].fillna(0).astype(int).astype(bool)

    home_slug = pd.Series([p.home_team_slug for p in parsed_urls], index=df.index)
    home_name = pd.Series([p.home_team for p in parsed_names], index=df.index)
    away_name = pd.Series([p.away_team for p in parsed_names], index=df.index)

    def demand(slug, name):
        f = franchise_for_slug(slug) or franchise_for_name(name)
        return f.demand_index if f else DEFAULT_DEMAND_INDEX

    events = pd.DataFrame(
        {
            "event_id": df["eventId"].astype("int64"),
            "name": df["name"].astype(str),
            "short_name": df.get("shortName"),
            "league": league,
            "event_type": df.get("type"),
            "taxonomy_name": df.get("taxonomyName"),
            "taxonomy_sub_name": df.get("taxonomySubName"),
            "event_datetime_utc": event_dt,
            "event_date": event_dt.dt.date,
            "venue_id": df["venueId"].astype("int64"),
            "home_slug": home_slug,
            "home_team": home_name.fillna(
                pd.Series([titleize_slug(s) for s in home_slug], index=df.index)
            ),
            "away_team": away_name,
            "home_demand_index": [demand(s, n) for s, n in zip(home_slug, home_name)],
            "away_demand_index": [
                demand(None, n) if n else DEFAULT_DEMAND_INDEX for n in away_name
            ],
            "postseason_tier": [p.postseason_tier for p in parsed_names],
            "demand_rank": [p.demand_rank for p in parsed_names],
            "game_number": [p.game_number for p in parsed_names],
            "is_game": [p.is_game for p in parsed_names],
            "is_tbd": [
                p.is_tbd or bool(t) for p, t in zip(parsed_names, df["dateTbd"].fillna(0))
            ],
            "is_ga": is_ga,
            "seat_selection": df["seatSelectionEnabled"].fillna(0).astype(int).astype(bool),
            "announce_date": announce.dt.date,
            "visible_at": visible.dt.date,
            "url": df["url"].astype(str),
            "ticketmaster_id": df.get("ticketmasterId").astype("string"),
            "stubhub_id": df.get("stubhubId").astype("string"),
            "_archetype": archetype,
        }
    )

    # --- modelling window -------------------------------------------------
    # History runs from the announce date (when resale listings realistically
    # appear) to the event, capped at max_lead_days.
    lead = (event_dt - announce).dt.days
    events["lead_days"] = lead
    horizon = lead.clip(upper=SIMULATION.max_lead_days)
    events["horizon_days"] = horizon
    events["listing_open_date"] = (event_dt - pd.to_timedelta(horizon, unit="D")).dt.date

    reason = pd.Series("", index=df.index, dtype="object")
    reason = reason.mask(event_dt.isna(), "missing_event_date")
    reason = reason.mask(reason.eq("") & (event_dt >= MAX_SANE_EVENT_DATE), "implausible_date")
    reason = reason.mask(reason.eq("") & events["is_tbd"], "tbd_placeholder")
    reason = reason.mask(reason.eq("") & ~events["is_game"], "non_game_inventory")
    reason = reason.mask(reason.eq("") & lead.isna(), "missing_announce_date")
    reason = reason.mask(
        reason.eq("") & (lead < SIMULATION.min_lead_days), "insufficient_lead_time"
    )
    events["exclusion_reason"] = reason
    events["is_modelable"] = reason.eq("")

    # --- venues -----------------------------------------------------------
    # Built from the *unfiltered* frame and joined on the shared index, so a
    # dropped event row can never shift geography onto the wrong venue.
    geo = pd.DataFrame(
        {
            "venue_id": events["venue_id"],
            "venue_slug": pd.Series([p.venue_slug for p in parsed_urls], index=df.index),
            "city": pd.Series([p.city for p in parsed_urls], index=df.index),
            "region": pd.Series([p.region for p in parsed_urls], index=df.index),
            "archetype": events["_archetype"],
        }
    )

    events = events[events["event_datetime_utc"].notna()].copy()
    counts = geo.groupby("venue_id").size().rename("event_count")
    # Prefer a row that actually has geography; fall back to any row.
    with_geo = geo[geo["venue_slug"].notna()].drop_duplicates("venue_id").set_index("venue_id")
    any_row = geo.drop_duplicates("venue_id").set_index("venue_id")
    venues = any_row.copy()
    venues.update(with_geo)
    venues = venues.join(counts)

    # A venue's archetype is whatever it hosts most often (AT&T Stadium hosts
    # 1,316 tour slots and 9 NFL games -- it is still a football stadium).
    dominant = (
        geo[geo["archetype"] != GENERAL_ADMISSION]
        .groupby("venue_id")["archetype"]
        .agg(lambda s: s.value_counts().index[0])
    )
    venues["archetype"] = dominant.reindex(venues.index).fillna(venues["archetype"])

    venues = venues.reset_index()
    venues["venue_name"] = [
        VENUE_NAME_OVERRIDES.get(s, titleize_slug(s)) if isinstance(s, str) else None
        for s in venues["venue_slug"]
    ]
    venues["city"] = [titleize_slug(c) for c in venues["city"]]
    venues["region"] = [titleize_slug(r) for r in venues["region"]]
    venues["geo_confidence"] = [
        "url" if slug and city else ("inferred" if slug else "none")
        for slug, city in zip(venues["venue_slug"], venues["city"])
    ]
    venues["venue_name"] = venues["venue_name"].fillna(
        "Venue " + venues["venue_id"].astype(str)
    )
    venues["capacity"] = venues["archetype"].map(ARCHETYPE_CAPACITY).fillna(2000).astype(int)
    venues["event_count"] = venues["event_count"].fillna(0).astype(int)
    venues = venues[
        [
            "venue_id", "venue_slug", "venue_name", "city", "region",
            "archetype", "capacity", "geo_confidence", "event_count",
        ]
    ]

    # Backfill each event's archetype from its venue, so stadium tours at a
    # football stadium inherit the stadium's seating model rather than GA.
    venue_archetype = venues.set_index("venue_id")["archetype"]
    events["_archetype"] = events["venue_id"].map(venue_archetype)

    # --- franchises -------------------------------------------------------
    franchise_counts = (
        events.groupby("home_slug").size().rename("event_count")
        if "home_slug" in events
        else pd.Series(dtype=int)
    )
    home_venue = events.groupby("home_slug")["venue_id"].agg(
        lambda s: s.value_counts().index[0]
    )
    franchises = pd.DataFrame(
        [
            {
                "slug": f.slug,
                "name": f.name,
                "league": f.league,
                "demand_index": f.demand_index,
                "home_venue_id": home_venue.get(f.slug),
                "event_count": int(franchise_counts.get(f.slug, 0)),
            }
            for f in FRANCHISES.values()
        ]
    )
    franchises["home_venue_id"] = franchises["home_venue_id"].astype("Int64")

    # --- seat tiers -------------------------------------------------------
    tier_rows = [
        {
            "archetype": archetype_key,
            "tier_key": tier.key,
            "label": tier.label,
            "tier_rank": rank,
            "price_multiplier": tier.price_multiplier,
            "inventory_share": tier.inventory_share,
            "late_decay": tier.late_decay,
            "scarcity": tier.scarcity,
        }
        for archetype_key, tiers in SEAT_TIERS.items()
        for rank, tier in enumerate(tiers)
    ]
    seat_tiers = pd.DataFrame(tier_rows)

    events = events.drop(columns=["_archetype"])
    return events, venues, franchises, seat_tiers


def ingest(csv_path: Path = RAW_EVENTS_CSV, warehouse_path: Path = WAREHOUSE) -> dict[str, int]:
    raw = _read_raw(csv_path)
    events, venues, franchises, seat_tiers = build_frames(raw)

    with db.warehouse(warehouse_path) as con:
        db.create_schema(con)
        for table, frame in (
            ("events", events),
            ("venues", venues),
            ("franchises", franchises),
            ("seat_tiers", seat_tiers),
        ):
            con.execute(f"DELETE FROM {table}")
            con.register("_frame", frame)
            cols = ", ".join(f'"{c}"' for c in frame.columns)
            con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _frame")
            con.unregister("_frame")
        db.create_indexes(con)
        db.record_run(
            con, "catalog_ingest", len(events),
            detail=f"{csv_path.name}: {len(venues)} venues, {int(events.is_modelable.sum())} modelable",
        )

    return {
        "events": len(events),
        "modelable_events": int(events["is_modelable"].sum()),
        "venues": len(venues),
        "franchises": len(franchises),
        "seat_tiers": len(seat_tiers),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest the SeatGeek catalogue into DuckDB")
    parser.add_argument("--csv", type=Path, default=RAW_EVENTS_CSV)
    parser.add_argument("--warehouse", type=Path, default=WAREHOUSE)
    args = parser.parse_args()

    stats = ingest(args.csv, args.warehouse)
    print(f"Ingested {stats['events']:,} events into {args.warehouse}")
    for key, value in stats.items():
        print(f"  {key:20s} {value:,}")


if __name__ == "__main__":
    main()
