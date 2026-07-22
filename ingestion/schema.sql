-- TixTime warehouse schema (DuckDB).
-- Mirrors the rebrowser/seatgeek-dataset entities, normalized to snake_case.

CREATE TABLE IF NOT EXISTS venues (
    venue_id     BIGINT PRIMARY KEY,
    name         TEXT,
    slug         TEXT,
    url          TEXT,
    city         TEXT,
    state        TEXT,
    country      TEXT,
    postal_code  TEXT,
    timezone     TEXT,
    latitude     DOUBLE,
    longitude    DOUBLE,
    capacity     BIGINT,
    score        DOUBLE,
    popularity   DOUBLE,
    metro_code   TEXT
);

CREATE TABLE IF NOT EXISTS performers (
    performer_id      BIGINT PRIMARY KEY,
    name              TEXT,
    short_name        TEXT,
    type              TEXT,
    slug              TEXT,
    score             DOUBLE,
    popularity        DOUBLE,
    taxonomy_name     TEXT,
    taxonomy_sub_name TEXT,
    home_venue_id     BIGINT
);

CREATE TABLE IF NOT EXISTS events (
    event_id          BIGINT PRIMARY KEY,
    name              TEXT,
    type              TEXT,
    datetime_utc      TIMESTAMP,
    status            TEXT,
    venue_id          BIGINT,
    performer_ids     BIGINT[],
    taxonomy_name     TEXT,
    taxonomy_sub_name TEXT,
    url               TEXT,
    announce_date     TIMESTAMP,
    created_at        TIMESTAMP
);

-- One row per event per observation day: the price time series everything
-- downstream (features, training, charts) is built from. Populated from the
-- daily events drops, which carry per-day aggregate price stats.
CREATE TABLE IF NOT EXISTS event_snapshots (
    event_id      BIGINT,
    observed_date DATE,
    lowest_price  DOUBLE,
    median_price  DOUBLE,
    average_price DOUBLE,
    highest_price DOUBLE,
    listing_count BIGINT,
    ticket_count  BIGINT,
    event_score   DOUBLE,
    PRIMARY KEY (event_id, observed_date)
);

-- Raw listings (optional; only populated when full-dataset listing drops are
-- ingested). Lets us upgrade the target to all-in price (priceWithFees).
CREATE TABLE IF NOT EXISTS listings (
    listing_id    TEXT,
    event_id      BIGINT,
    observed_date DATE,
    price         DOUBLE,
    price_with_fees DOUBLE,
    fee           DOUBLE,
    section       TEXT,
    "row"         TEXT,
    quantity      BIGINT,
    marketplace   TEXT,
    deal_bucket   TEXT,
    deal_score    DOUBLE,
    PRIMARY KEY (listing_id, observed_date)
);

-- Everything the feature builder needs, one row per (event, day).
CREATE OR REPLACE VIEW snapshot_features AS
SELECT
    s.event_id,
    s.observed_date,
    s.lowest_price,
    s.median_price,
    s.listing_count,
    e.name              AS event_name,
    e.datetime_utc      AS event_datetime,
    CAST(e.datetime_utc AS DATE) - s.observed_date AS days_until_event,
    e.taxonomy_name,
    e.taxonomy_sub_name,
    v.venue_id,
    v.name              AS venue_name,
    v.city              AS venue_city,
    v.state             AS venue_state,
    v.capacity          AS venue_capacity,
    v.popularity        AS venue_popularity,
    p.popularity        AS performer_popularity,
    p.score             AS performer_score,
    p.type              AS performer_type
FROM event_snapshots s
JOIN events e   ON e.event_id = s.event_id
JOIN venues v   ON v.venue_id = e.venue_id
LEFT JOIN performers p ON p.performer_id = e.performer_ids[1]
WHERE s.median_price IS NOT NULL;
