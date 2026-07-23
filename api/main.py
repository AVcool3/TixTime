"""TixTime API.

Run: uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from api.predict import best_time_to_buy
from ingestion.db import connect
from ml.features import build_features, filter_concerts

app = FastAPI(title="TixTime API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def q(sql: str, params: list | None = None) -> list[dict]:
    con = connect(read_only=True)
    try:
        return con.execute(sql, params or []).df().to_dict("records")
    finally:
        con.close()


@app.get("/api/venues")
def search_venues(q_: str = Query("", alias="q"), state: str = ""):
    return q(
        """
        SELECT venue_id, name, city, state, capacity, popularity
        FROM venues
        WHERE (? = '' OR name ILIKE '%' || ? || '%' OR city ILIKE '%' || ? || '%')
          AND (? = '' OR state = upper(?))
        ORDER BY popularity DESC NULLS LAST
        LIMIT 50
        """,
        [q_, q_, q_, state, state],
    )


@app.get("/api/venues/{venue_id}/events")
def venue_events(venue_id: int):
    return q(
        """
        SELECT e.event_id, e.name, e.datetime_utc, e.taxonomy_name,
               s.lowest_price, s.median_price, s.listing_count,
               s.observed_date AS last_observed
        FROM events e
        LEFT JOIN event_snapshots s ON s.event_id = e.event_id
          AND s.observed_date = (
            SELECT max(observed_date) FROM event_snapshots WHERE event_id = e.event_id
          )
        WHERE e.venue_id = ? AND e.datetime_utc > now()
        ORDER BY e.datetime_utc
        """,
        [venue_id],
    )


@app.get("/api/events/{event_id}")
def event_detail(event_id: int):
    rows = q(
        """
        SELECT e.*, v.name AS venue_name, v.city AS venue_city, v.state AS venue_state
        FROM events e JOIN venues v ON v.venue_id = e.venue_id
        WHERE e.event_id = ?
        """,
        [event_id],
    )
    if not rows:
        raise HTTPException(404, "event not found")
    row = rows[0]
    row["performer_ids"] = list(row.get("performer_ids") or [])
    return row


@app.get("/api/events/{event_id}/history")
def event_history(event_id: int):
    return q(
        """
        SELECT observed_date, lowest_price, median_price, average_price,
               highest_price, listing_count
        FROM event_snapshots
        WHERE event_id = ? AND median_price IS NOT NULL
        ORDER BY observed_date
        """,
        [event_id],
    )


@app.get("/api/events/{event_id}/forecast")
def event_forecast(event_id: int):
    con = connect(read_only=True)
    try:
        df = con.execute(
            "SELECT * FROM snapshot_features WHERE event_id = ? ORDER BY observed_date",
            [event_id],
        ).df()
    finally:
        con.close()
    if df.empty:
        raise HTTPException(404, "no price history for this event")

    feats = build_features(filter_concerts(df))
    if feats.empty:
        return {
            "status": "collecting",
            "message": "Still collecting data for this event — need at least "
            "5 days of price history before forecasting.",
            "observed_days": int(df["observed_date"].nunique()),
        }

    try:
        result = best_time_to_buy(feats.iloc[-1].to_dict())
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))
    if "error" in result:
        raise HTTPException(400, result["error"])
    return {"status": "ok", **result}


@app.get("/api/health")
def health():
    counts = q(
        """
        SELECT (SELECT count(*) FROM venues) AS venues,
               (SELECT count(*) FROM events) AS events,
               (SELECT count(*) FROM event_snapshots) AS snapshots
        """
    )[0]
    return {"status": "ok", **{k: int(v) for k, v in counts.items()}}
