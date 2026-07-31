"""Populate price_snapshots for every modelable event.

Run with:  python -m tixtime.pricing.generate
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from tixtime import db
from tixtime.config import SYNTHETIC_SOURCE, WAREHOUSE
from tixtime.pricing.simulator import simulate_event

_SNAPSHOT_COLUMNS = [
    "event_id", "tier_key", "as_of_date", "days_until_event",
    "get_in_price", "median_price", "listing_count", "ticket_count",
    "is_burn_in", "source",
]


def generate(
    warehouse_path: Path = WAREHOUSE,
    limit: int | None = None,
    batch_size: int = 400,
    verbose: bool = True,
) -> int:
    with db.warehouse(warehouse_path) as con:
        db.create_schema(con)
        events = con.execute(
            """
            SELECT e.event_id, e.event_date, e.horizon_days, e.league, e.is_ga,
                   e.postseason_tier, e.demand_rank, e.home_demand_index,
                   e.away_demand_index, v.archetype
            FROM events e
            JOIN venues v USING (venue_id)
            WHERE e.is_modelable
            ORDER BY e.event_id
            """
            + (f" LIMIT {int(limit)}" if limit else "")
        ).df()

        if events.empty:
            raise RuntimeError(
                "No modelable events found. Run `python -m tixtime.catalog.ingest` first."
            )

        con.execute("DELETE FROM price_snapshots WHERE source = ?", [SYNTHETIC_SOURCE])

        started = time.time()
        total = 0
        buffer: list[pd.DataFrame] = []

        for position, (_, event) in enumerate(events.iterrows(), start=1):
            buffer.append(simulate_event(event))

            if len(buffer) >= batch_size or position == len(events):
                chunk = pd.concat(buffer, ignore_index=True)[_SNAPSHOT_COLUMNS]
                con.register("_chunk", chunk)
                con.execute("INSERT INTO price_snapshots SELECT * FROM _chunk")
                con.unregister("_chunk")
                total += len(chunk)
                buffer.clear()
                if verbose:
                    rate = position / max(time.time() - started, 1e-6)
                    print(
                        f"  {position:,}/{len(events):,} events  "
                        f"{total:,} snapshots  ({rate:.0f} events/s)",
                        flush=True,
                    )

        db.record_run(
            con, "price_generate", total,
            detail=f"{len(events)} events, source={SYNTHETIC_SOURCE}",
        )

    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic price history")
    parser.add_argument("--warehouse", type=Path, default=WAREHOUSE)
    parser.add_argument("--limit", type=int, default=None, help="only the first N events")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    started = time.time()
    total = generate(args.warehouse, args.limit, verbose=not args.quiet)
    print(f"\nWrote {total:,} snapshots in {time.time() - started:.1f}s")
    print(f"All rows tagged source='{SYNTHETIC_SOURCE}' -- these are not observed prices.")


if __name__ == "__main__":
    main()
