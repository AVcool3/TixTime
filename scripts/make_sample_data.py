"""Generate SYNTHETIC sample Parquet drops in the exact rebrowser/seatgeek-dataset
schema (camelCase columns, same directory layout), so the full pipeline —
ingest -> train -> API -> UI — can be exercised before real dataset access
is wired up. Prices follow realistic resale dynamics: spike at on-sale,
decay toward a bottom 3–14 days out, with a minority of "hot" events whose
prices only climb.

Usage: python scripts/make_sample_data.py [--days 60] [--out data/sample-drops]
Then:  python -m ingestion.ingest auto data/sample-drops
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

RNG = np.random.default_rng(42)

VENUES = [
    (101, "Red Rocks Amphitheatre", "Morrison", "CO", 9525, 0.92),
    (102, "Madison Square Garden", "New York", "NY", 20789, 0.98),
    (103, "The Gorge Amphitheatre", "George", "WA", 27500, 0.85),
    (104, "Ryman Auditorium", "Nashville", "TN", 2362, 0.88),
    (105, "Hollywood Bowl", "Los Angeles", "CA", 17500, 0.94),
    (106, "The Fillmore", "San Francisco", "CA", 1315, 0.81),
    (107, "9:30 Club", "Washington", "DC", 1200, 0.78),
    (108, "House of Blues", "Chicago", "IL", 1800, 0.74),
]

PERFORMERS = [
    (201, "The Midnight Hollows", 0.91), (202, "Silver Casket", 0.66),
    (203, "Juniper & Sage", 0.83), (204, "DJ Meridian", 0.72),
    (205, "The Copper Owls", 0.58), (206, "Violet Reign", 0.95),
    (207, "Lakeshore Drive", 0.63), (208, "Neon Prairie", 0.79),
    (209, "The Standard Deviants", 0.51), (210, "Aurora Falls", 0.87),
]


def price_curve(days_out: np.ndarray, base: float, hot: bool) -> np.ndarray:
    """Median resale price as a function of days-until-event."""
    if hot:
        # High demand: steady climb as supply dries up.
        curve = base * (1.0 + 0.9 * np.exp(-days_out / 25.0))
    else:
        # Typical: on-sale hype premium decays; slight uptick in last 2 days.
        curve = base * (1.0 + 0.5 * (days_out / 90.0) ** 0.7 + 0.12 * np.exp(-days_out / 2.0))
    noise = RNG.normal(1.0, 0.03, size=len(days_out))
    return np.round(curve * noise, 2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60, help="days of daily drops")
    parser.add_argument("--events", type=int, default=48)
    parser.add_argument("--out", default="data/sample-drops")
    args = parser.parse_args()

    out = Path(args.out)
    today = date.today()

    (out / "venues").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "_primaryKey": f"v{vid}", "_firstSeenAt": datetime.now(), "_lastSeenAt": datetime.now(),
                "venueId": vid, "name": name, "slug": name.lower().replace(" ", "-"),
                "url": f"https://seatgeek.com/venues/{vid}", "addressStreet": "1 Main St",
                "addressCity": city, "addressState": state, "addressCountry": "US",
                "addressPostalCode": "00000", "timezone": "America/New_York",
                "latitude": 39.0 + RNG.normal(0, 5), "longitude": -98.0 + RNG.normal(0, 15),
                "capacity": cap, "score": pop, "popularity": pop, "metroCode": None,
            }
            for vid, name, city, state, cap, pop in VENUES
        ]
    ).to_parquet(out / "venues" / "data.parquet")

    (out / "performers").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "_primaryKey": f"p{pid}", "_firstSeenAt": datetime.now(), "_lastSeenAt": datetime.now(),
                "performerId": pid, "name": name, "shortName": name, "type": "band",
                "slug": name.lower().replace(" ", "-"), "url": f"https://seatgeek.com/performers/{pid}",
                "heroImageUrl": None, "bannerImageUrl": None, "score": pop, "popularity": pop,
                "homeVenueId": None, "primaryColor": None, "iconicColor": None, "isEvent": False,
                "divisionName": None, "divisionShortName": None,
                "taxonomyName": "concert", "taxonomySubName": "band",
            }
            for pid, name, pop in PERFORMERS
        ]
    ).to_parquet(out / "performers" / "data.parquet")

    # Events: shows 10–90 days in the future, each with a hidden price regime.
    events = []
    for i in range(args.events):
        vid, vname, *_rest, vpop = VENUES[i % len(VENUES)]
        pid, pname, ppop = PERFORMERS[i % len(PERFORMERS)]
        event_dt = datetime.combine(today + timedelta(days=int(RNG.integers(10, 90))),
                                    datetime.min.time()) + timedelta(hours=20)
        base = 40 + 220 * (0.5 * vpop + 0.5 * ppop) * RNG.uniform(0.6, 1.4)
        events.append({
            "eventId": 1000 + i,
            "name": f"{pname} at {vname}",
            "venueId": vid, "performerIds": [pid],
            "datetimeUtc": event_dt, "base": base,
            "hot": bool(RNG.random() < 0.2), "popularity": ppop,
        })

    events_dir = out / "events" / "data"
    events_dir.mkdir(parents=True, exist_ok=True)
    for d in range(args.days, 0, -1):
        obs = today - timedelta(days=d - 1)
        rows = []
        for ev in events:
            days_out = (ev["datetimeUtc"].date() - obs).days
            if days_out < 1:
                continue
            median = price_curve(np.array([days_out], dtype=float), ev["base"], ev["hot"])[0]
            listings = max(8, int(400 * (0.3 + 0.7 * (days_out / 90.0)) *
                                  (0.4 if ev["hot"] else 1.0) * RNG.uniform(0.8, 1.2)))
            rows.append({
                "_primaryKey": f"e{ev['eventId']}", "_firstSeenAt": datetime.now(),
                "_lastSeenAt": datetime.combine(obs, datetime.min.time()) + timedelta(hours=6),
                "eventId": ev["eventId"], "name": ev["name"], "shortName": ev["name"],
                "type": "concert", "datetimeUtc": ev["datetimeUtc"], "endDatetimeUtc": None,
                "dateTbd": False, "timeTbd": False, "datetimeTbd": False,
                "status": "normal", "scheduleStatus": "scheduled", "conditional": False,
                "contingent": False, "isOpen": False, "isVisible": True, "isHybrid": False,
                "eventScore": ev["popularity"], "popularityScore": ev["popularity"],
                "url": f"https://seatgeek.com/e/{ev['eventId']}",
                "createdAt": datetime.now(), "announceDate": None, "visibleAt": None,
                "visibleUntilUtc": None,
                "listingCount": listings, "ticketCount": listings * 2,
                "averagePrice": round(median * 1.15, 2), "lowestPrice": round(median * 0.62, 2),
                "highestPrice": round(median * 4.1, 2), "medianPrice": float(median),
                "lowestSgBasePrice": round(median * 0.55, 2),
                "venueId": ev["venueId"], "performerIds": ev["performerIds"],
                "taxonomyName": "concert", "taxonomySubName": "band",
                "ticketmasterId": None, "stubhubId": None, "integratedProvider": None,
                "integratedProviderId": None, "isMapped": True, "isGa": False,
                "seatSelectionEnabled": True,
            })
        pd.DataFrame(rows).to_parquet(events_dir / f"{obs.isoformat()}.parquet")

    print(f"Wrote synthetic drops to {out}/ "
          f"({len(VENUES)} venues, {len(PERFORMERS)} performers, "
          f"{len(events)} events, {args.days} daily event files)")


if __name__ == "__main__":
    main()
