"""DuckDB warehouse: connection handling and schema."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from tixtime.config import WAREHOUSE

SCHEMA_SQL = """
-- Venues -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS venues (
    venue_id        BIGINT PRIMARY KEY,
    venue_slug      VARCHAR,
    venue_name      VARCHAR,
    city            VARCHAR,
    region          VARCHAR,
    archetype       VARCHAR NOT NULL,
    capacity        INTEGER NOT NULL,
    geo_confidence  VARCHAR NOT NULL,   -- 'url' | 'inferred' | 'none'
    event_count     INTEGER NOT NULL
);

-- Franchises (the "artists" of the sports catalogue) ------------------------
CREATE TABLE IF NOT EXISTS franchises (
    slug          VARCHAR PRIMARY KEY,
    name          VARCHAR NOT NULL,
    league        VARCHAR NOT NULL,
    demand_index  DOUBLE NOT NULL,
    home_venue_id BIGINT,
    event_count   INTEGER NOT NULL
);

-- Events -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    event_id             BIGINT PRIMARY KEY,
    name                 VARCHAR NOT NULL,
    short_name           VARCHAR,
    league               VARCHAR,          -- nba/nfl/mlb/nhl/other
    event_type           VARCHAR,          -- raw SeatGeek `type`
    taxonomy_name        VARCHAR,
    taxonomy_sub_name    VARCHAR,
    event_datetime_utc   TIMESTAMP NOT NULL,
    event_date           DATE NOT NULL,
    venue_id             BIGINT NOT NULL,
    home_slug            VARCHAR,
    home_team            VARCHAR,
    away_team            VARCHAR,
    home_demand_index    DOUBLE NOT NULL,
    away_demand_index    DOUBLE NOT NULL,
    postseason_tier      VARCHAR NOT NULL,
    demand_rank          INTEGER NOT NULL,
    game_number          INTEGER,
    is_game              BOOLEAN NOT NULL,
    is_tbd               BOOLEAN NOT NULL,
    is_ga                BOOLEAN NOT NULL,
    seat_selection       BOOLEAN NOT NULL,
    has_seat_tiers       BOOLEAN NOT NULL,  -- seat_selection AND NOT is_ga
    announce_date        DATE,
    visible_at           DATE,
    lead_days            INTEGER,          -- announce -> event
    listing_open_date    DATE,             -- first day we model a price for
    horizon_days         INTEGER,          -- listing_open -> event, inclusive
    url                  VARCHAR NOT NULL,
    ticketmaster_id      VARCHAR,
    stubhub_id           VARCHAR,
    is_modelable         BOOLEAN NOT NULL, -- has enough history to model
    exclusion_reason     VARCHAR
);

-- Seat tiers ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS seat_tiers (
    archetype        VARCHAR NOT NULL,
    tier_key         VARCHAR NOT NULL,
    label            VARCHAR NOT NULL,
    tier_rank        INTEGER NOT NULL,
    price_multiplier DOUBLE NOT NULL,
    inventory_share  DOUBLE NOT NULL,
    late_decay       DOUBLE NOT NULL,
    scarcity         DOUBLE NOT NULL,
    PRIMARY KEY (archetype, tier_key)
);

-- Price snapshots ----------------------------------------------------------
-- One row per (event, seat tier, observation date). `source` records
-- provenance: 'synthetic_v1' for simulator output, a provider name for real
-- collector output. Nothing downstream may drop this column.
CREATE TABLE IF NOT EXISTS price_snapshots (
    event_id          BIGINT NOT NULL,
    tier_key          VARCHAR NOT NULL,
    as_of_date        DATE NOT NULL,
    days_until_event  INTEGER NOT NULL,
    get_in_price      DOUBLE NOT NULL,   -- cheapest listing in the tier
    median_price      DOUBLE NOT NULL,
    listing_count     INTEGER NOT NULL,
    ticket_count      INTEGER NOT NULL,
    is_burn_in        BOOLEAN NOT NULL DEFAULT FALSE,
    source            VARCHAR NOT NULL
);

-- Watchlist + alert rules --------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_rules (
    rule_id      VARCHAR PRIMARY KEY,
    event_id     BIGINT NOT NULL,
    tier_key     VARCHAR,               -- NULL = any tier
    rule_type    VARCHAR NOT NULL,      -- 'model_buy' | 'target_price' | 'drop_pct'
    threshold    DOUBLE,
    channel      VARCHAR NOT NULL,      -- 'inapp' | 'webhook' | 'email'
    destination  VARCHAR,
    label        VARCHAR,
    created_at   TIMESTAMP NOT NULL,
    active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS alert_events (
    alert_id     VARCHAR PRIMARY KEY,
    rule_id      VARCHAR NOT NULL,
    event_id     BIGINT NOT NULL,
    tier_key     VARCHAR,
    fired_at     TIMESTAMP NOT NULL,
    as_of_date   DATE NOT NULL,
    headline     VARCHAR NOT NULL,
    body         VARCHAR NOT NULL,
    price        DOUBLE,
    purchase_url VARCHAR,
    delivered    BOOLEAN NOT NULL DEFAULT FALSE,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE
);

-- Pipeline provenance ------------------------------------------------------
CREATE TABLE IF NOT EXISTS pipeline_runs (
    stage       VARCHAR NOT NULL,
    ran_at      TIMESTAMP NOT NULL,
    as_of_date  DATE,
    row_count   BIGINT,
    detail      VARCHAR
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_snap_event      ON price_snapshots (event_id);
CREATE INDEX IF NOT EXISTS idx_snap_event_tier ON price_snapshots (event_id, tier_key);
CREATE INDEX IF NOT EXISTS idx_snap_asof       ON price_snapshots (as_of_date);
CREATE INDEX IF NOT EXISTS idx_events_date     ON events (event_date);
CREATE INDEX IF NOT EXISTS idx_events_venue    ON events (venue_id);
CREATE INDEX IF NOT EXISTS idx_alert_rule      ON alert_events (rule_id);
"""


def connect(path: Path | str = WAREHOUSE, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


@contextmanager
def warehouse(path: Path | str = WAREHOUSE, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    con = connect(path, read_only=read_only)
    try:
        yield con
    finally:
        con.close()


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA_SQL)


def create_indexes(con: duckdb.DuckDBPyConnection) -> None:
    """Built after bulk loads -- indexing an empty table then inserting is slower."""
    con.execute(INDEX_SQL)


def record_run(
    con: duckdb.DuckDBPyConnection,
    stage: str,
    row_count: int | None = None,
    as_of_date=None,
    detail: str = "",
) -> None:
    con.execute(
        "INSERT INTO pipeline_runs (stage, ran_at, as_of_date, row_count, detail) "
        "VALUES (?, now(), ?, ?, ?)",
        [stage, as_of_date, row_count, detail],
    )
