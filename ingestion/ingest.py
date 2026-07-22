"""Ingest rebrowser/seatgeek-dataset Parquet drops into the DuckDB warehouse.

Usage:
    python -m ingestion.ingest venues path/to/venues/*.parquet
    python -m ingestion.ingest performers path/to/performers/*.parquet
    python -m ingestion.ingest events path/to/events/data/*.parquet [--date 2026-07-18]
    python -m ingestion.ingest listings path/to/event-listings/data/*.parquet [--date 2026-07-18]
    python -m ingestion.ingest auto path/to/dataset-root

The events entity does double duty: each daily drop upserts event metadata
AND appends one price snapshot per event (the drops carry lowestPrice /
medianPrice / averagePrice / highestPrice / listingCount per day).

The observation date for a file is resolved in order of preference:
  1. --date argument
  2. a YYYY-MM-DD substring in the filename
  3. per-row date(_lastSeenAt)
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

from ingestion.db import connect, init_schema

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _file_date(path: Path, override: str | None) -> str | None:
    if override:
        return override
    m = DATE_RE.search(path.name)
    return m.group(1) if m else None


def ingest_venues(con, path: Path) -> int:
    con.execute(
        """
        INSERT OR REPLACE INTO venues
        SELECT venueId, name, slug, url,
               addressCity, addressState, addressCountry, addressPostalCode,
               timezone, latitude, longitude, capacity, score, popularity, metroCode
        FROM read_parquet(?)
        QUALIFY row_number() OVER (PARTITION BY venueId ORDER BY _lastSeenAt DESC) = 1
        """,
        [str(path)],
    )
    return con.execute("SELECT count(*) FROM venues").fetchone()[0]


def ingest_performers(con, path: Path) -> int:
    con.execute(
        """
        INSERT OR REPLACE INTO performers
        SELECT performerId, name, shortName, type, slug,
               score, popularity, taxonomyName, taxonomySubName, homeVenueId
        FROM read_parquet(?)
        QUALIFY row_number() OVER (PARTITION BY performerId ORDER BY _lastSeenAt DESC) = 1
        """,
        [str(path)],
    )
    return con.execute("SELECT count(*) FROM performers").fetchone()[0]


def ingest_events(con, path: Path, observed: str | None) -> tuple[int, int]:
    con.execute(
        """
        INSERT OR REPLACE INTO events
        SELECT eventId, name, type, datetimeUtc, status, venueId,
               performerIds, taxonomyName, taxonomySubName, url, announceDate, createdAt
        FROM read_parquet(?)
        QUALIFY row_number() OVER (PARTITION BY eventId ORDER BY _lastSeenAt DESC) = 1
        """,
        [str(path)],
    )
    con.execute(
        """
        INSERT OR REPLACE INTO event_snapshots
        SELECT eventId,
               coalesce(CAST(? AS DATE), CAST(_lastSeenAt AS DATE)),
               lowestPrice, medianPrice, averagePrice, highestPrice,
               listingCount, ticketCount, eventScore
        FROM read_parquet(?)
        QUALIFY row_number() OVER (PARTITION BY eventId ORDER BY _lastSeenAt DESC) = 1
        """,
        [observed, str(path)],
    )
    n_events = con.execute("SELECT count(*) FROM events").fetchone()[0]
    n_snaps = con.execute("SELECT count(*) FROM event_snapshots").fetchone()[0]
    return n_events, n_snaps


def ingest_listings(con, path: Path, observed: str | None) -> int:
    con.execute(
        """
        INSERT OR REPLACE INTO listings
        SELECT listingId, eventId,
               coalesce(CAST(? AS DATE), CAST(_lastSeenAt AS DATE)),
               price, priceWithFees, fee, section, "row", quantity,
               marketplace, dealBucket, dealScore
        FROM read_parquet(?)
        QUALIFY row_number() OVER (PARTITION BY listingId ORDER BY _lastSeenAt DESC) = 1
        """,
        [observed, str(path)],
    )
    return con.execute("SELECT count(*) FROM listings").fetchone()[0]


def ingest_auto(con, root: Path, override: str | None) -> None:
    """Walk a dataset checkout laid out like the rebrowser repo."""
    for path in sorted(root.glob("venues/**/*.parquet")):
        print(f"venues     <- {path}: {ingest_venues(con, path)} total")
    for path in sorted(root.glob("performers/**/*.parquet")):
        print(f"performers <- {path}: {ingest_performers(con, path)} total")
    for path in sorted(root.glob("events/**/*.parquet")):
        n_e, n_s = ingest_events(con, path, _file_date(path, override))
        print(f"events     <- {path}: {n_e} events, {n_s} snapshots")
    for path in sorted(root.glob("event-listings/**/*.parquet")):
        print(f"listings   <- {path}: {ingest_listings(con, path, _file_date(path, override))} total")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entity", choices=["venues", "performers", "events", "listings", "auto"])
    parser.add_argument("paths", nargs="+", help="Parquet file(s), or dataset root for 'auto'")
    parser.add_argument("--date", help="Observation date override (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    if args.date:
        date.fromisoformat(args.date)  # validate early

    con = connect()
    init_schema(con)

    if args.entity == "auto":
        ingest_auto(con, Path(args.paths[0]), args.date)
        return

    for raw in args.paths:
        path = Path(raw)
        observed = _file_date(path, args.date)
        if args.entity == "venues":
            print(f"venues     <- {path}: {ingest_venues(con, path)} total")
        elif args.entity == "performers":
            print(f"performers <- {path}: {ingest_performers(con, path)} total")
        elif args.entity == "events":
            n_e, n_s = ingest_events(con, path, observed)
            print(f"events     <- {path}: {n_e} events, {n_s} snapshots")
        elif args.entity == "listings":
            print(f"listings   <- {path}: {ingest_listings(con, path, observed)} total")


if __name__ == "__main__":
    main(sys.argv[1:])
