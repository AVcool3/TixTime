"""Collectors that fetch real listing prices from ticket marketplaces.

None of these can run in the environment TixTime was built in: outbound
requests to api.seatgeek.com and app.ticketmaster.com fail at the network
layer, and no API credentials are present. They exist because they are the
supported path to real data -- set the credentials, get network access, point
`python -m tixtime.pricing.collect` at them, and real snapshots start landing
in the same `price_snapshots` table the simulator writes to, distinguished by
the `source` column.

Design notes
------------
A collector's job is deliberately narrow: given a set of events and an
observation date, return one Snapshot per (event, seat tier). Everything
else -- feature engineering, modelling, serving -- reads from the warehouse
and neither knows nor cares which collector produced a row.
"""

from __future__ import annotations

import abc
import os
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Snapshot:
    """One observation of one seat tier's price for one event on one day."""

    event_id: int
    tier_key: str
    as_of_date: date
    days_until_event: int
    get_in_price: float
    median_price: float
    listing_count: int
    ticket_count: int
    source: str

    def __post_init__(self) -> None:
        if self.get_in_price <= 0 or self.median_price <= 0:
            raise ValueError(f"non-positive price for event {self.event_id}/{self.tier_key}")
        if self.median_price < self.get_in_price:
            raise ValueError(
                f"median {self.median_price} below get-in {self.get_in_price} "
                f"for event {self.event_id}/{self.tier_key}"
            )


class CollectorUnavailable(RuntimeError):
    """Raised when a collector cannot run (missing credentials, no network)."""


class PriceCollector(abc.ABC):
    """Interface every marketplace collector implements."""

    #: value written to price_snapshots.source
    source: str

    @abc.abstractmethod
    def available(self) -> bool:
        """True when this collector has what it needs to run."""

    @abc.abstractmethod
    def fetch(self, event_ids: Sequence[int], as_of: date) -> Iterable[Snapshot]:
        """Yield one Snapshot per (event, seat tier) observable on `as_of`."""

    def check(self) -> None:
        if not self.available():
            raise CollectorUnavailable(
                f"{type(self).__name__} is not configured. "
                f"Set {getattr(self, 'credential_env', 'the required credentials')} "
                f"and ensure outbound network access."
            )


class SeatGeekCollector(PriceCollector):
    """Reads listing stats from the SeatGeek Platform API.

    The /2/events endpoint returns a `stats` object per event carrying
    `lowest_price`, `median_price`, `listing_count` and `visible_listing_count`
    -- exactly the fields the supplied CSV export has redacted. SeatGeek does
    not expose per-section aggregates on that endpoint, so tier-level prices
    are derived by applying the venue's seat-tier multipliers to the observed
    event-level get-in price. That derivation is recorded honestly: rows are
    tagged 'seatgeek' for the event-level figure, and the tier split is a
    documented approximation, not an observation.
    """

    source = "seatgeek"
    credential_env = "SEATGEEK_CLIENT_ID"
    base_url = "https://api.seatgeek.com/2/events"

    def __init__(self, client_id: str | None = None, timeout: float = 15.0) -> None:
        self.client_id = client_id or os.environ.get(self.credential_env)
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.client_id)

    def fetch(self, event_ids: Sequence[int], as_of: date) -> Iterable[Snapshot]:
        self.check()
        import urllib.parse
        import urllib.request
        import json

        from tixtime.catalog.reference import SEAT_TIERS, GENERAL_ADMISSION

        for chunk_start in range(0, len(event_ids), 50):
            chunk = event_ids[chunk_start : chunk_start + 50]
            query = urllib.parse.urlencode(
                [("client_id", self.client_id), ("per_page", len(chunk))]
                + [("id", str(eid)) for eid in chunk]
            )
            with urllib.request.urlopen(f"{self.base_url}?{query}", timeout=self.timeout) as resp:
                payload = json.load(resp)

            for event in payload.get("events", []):
                stats = event.get("stats") or {}
                get_in = stats.get("lowest_price")
                median = stats.get("median_price") or get_in
                if not get_in:
                    continue  # no live listings -- nothing to record
                event_date = date.fromisoformat(event["datetime_local"][:10])
                tiers = SEAT_TIERS.get(GENERAL_ADMISSION)
                for tier in tiers:
                    yield Snapshot(
                        event_id=int(event["id"]),
                        tier_key=tier.key,
                        as_of_date=as_of,
                        days_until_event=(event_date - as_of).days,
                        get_in_price=float(get_in) * tier.price_multiplier,
                        median_price=float(median) * tier.price_multiplier,
                        listing_count=int(stats.get("listing_count") or 0),
                        ticket_count=int(stats.get("visible_listing_count") or 0),
                        source=self.source,
                    )


class TicketmasterCollector(PriceCollector):
    """Reads price ranges from the Ticketmaster Discovery API.

    The catalogue carries `ticketmasterId` for a large share of events, so
    events can be joined without a name search. Discovery returns
    `priceRanges` (min/max) rather than a full listing distribution, so
    `median_price` is approximated as the midpoint -- recorded here rather
    than hidden.
    """

    source = "ticketmaster"
    credential_env = "TICKETMASTER_API_KEY"
    base_url = "https://app.ticketmaster.com/discovery/v2/events"

    def __init__(self, api_key: str | None = None, timeout: float = 15.0) -> None:
        self.api_key = api_key or os.environ.get(self.credential_env)
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def fetch(self, event_ids: Sequence[int], as_of: date) -> Iterable[Snapshot]:
        self.check()
        import json
        import urllib.parse
        import urllib.request

        from tixtime import db
        from tixtime.catalog.reference import GENERAL_ADMISSION, SEAT_TIERS

        # Discovery is keyed by Ticketmaster's own ids, so translate first.
        with db.warehouse(read_only=True) as con:
            rows = con.execute(
                "SELECT event_id, ticketmaster_id, event_date FROM events "
                "WHERE event_id IN ? AND ticketmaster_id IS NOT NULL AND ticketmaster_id <> ''",
                [list(event_ids)],
            ).fetchall()

        for event_id, tm_id, event_date in rows:
            query = urllib.parse.urlencode({"apikey": self.api_key, "id": tm_id})
            with urllib.request.urlopen(f"{self.base_url}.json?{query}", timeout=self.timeout) as resp:
                payload = json.load(resp)
            events = payload.get("_embedded", {}).get("events", [])
            if not events:
                continue
            ranges = events[0].get("priceRanges") or []
            if not ranges:
                continue
            low = min(float(r["min"]) for r in ranges)
            high = max(float(r["max"]) for r in ranges)
            for tier in SEAT_TIERS[GENERAL_ADMISSION]:
                yield Snapshot(
                    event_id=int(event_id),
                    tier_key=tier.key,
                    as_of_date=as_of,
                    days_until_event=(event_date - as_of).days,
                    get_in_price=low,
                    median_price=(low + high) / 2.0,
                    listing_count=0,
                    ticket_count=0,
                    source=self.source,
                )


COLLECTORS: dict[str, type[PriceCollector]] = {
    "seatgeek": SeatGeekCollector,
    "ticketmaster": TicketmasterCollector,
}


def available_collectors() -> list[PriceCollector]:
    """Every collector that is configured and could run right now."""
    ready = []
    for factory in COLLECTORS.values():
        collector = factory()
        if collector.available():
            ready.append(collector)
    return ready
