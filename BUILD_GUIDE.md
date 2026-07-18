# TixTime Build Guide

A step-by-step guide to building TixTime: a web app where users pick concert
venues across the country in a React UI, and the system tells them the best
time to buy tickets for events at those venues.

---

## 0. What you are actually building (and what you are not)

You are building a **price-timing recommendation system**, made of four parts:

1. **A React frontend** — users search/select venues, browse upcoming events,
   and see a "buy now / wait" recommendation with a price-history chart.
2. **A backend API** — serves venues, events, price history, and predictions.
3. **A price-polling worker** — a scheduled job that snapshots ticket prices
   for tracked events every few hours. This is the heart of the system:
   **no public API gives you historical ticket prices, so you must build your
   own price time-series by polling.**
4. **A prediction engine** — starts as a simple heuristic, graduates to a
   trained model once you've collected weeks of your own price data.

**What you must NOT build:** anything that automates the purchase itself,
bypasses queues/captchas, or scrapes sites that prohibit it. The U.S. BOTS Act
(2016) makes circumventing ticket-purchase controls illegal, and
Ticketmaster/StubHub ToS prohibit automated checkout. A tool that *informs* a
human when to buy is fine; a tool that *buys* is not. TixTime informs.

---

## 1. Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────┐
│  React SPA  │────▶│  API (Node or    │────▶│  PostgreSQL   │
│  (Vite)     │     │  FastAPI)        │     │               │
└─────────────┘     └──────────────────┘     └──────▲───────┘
                                                    │
                    ┌──────────────────┐            │
                    │  Polling worker  │────────────┘
                    │  (cron, every    │
                    │  4–6 hours)      │──▶ Ticketmaster / SeatGeek APIs
                    └──────────────────┘
```

Recommended stack (all free tiers to start):

| Layer      | Choice                              | Why |
|------------|-------------------------------------|-----|
| Frontend   | React + Vite + TypeScript           | Fast dev, typed API contracts |
| Charts     | Recharts                            | Simple time-series charts |
| Backend    | Node.js + Express + TypeScript      | One language across the stack |
| Database   | PostgreSQL (Supabase/Neon free tier)| Time-series-friendly, free hosting |
| Scheduler  | node-cron in the worker process, or GitHub Actions cron | Zero-infra polling |
| Hosting    | Frontend: Vercel/Netlify. API+worker: Railway/Render/Fly.io | Free tiers |

---

## 2. Data sources — where venues, events, and prices come from

### 2.1 Ticketmaster Discovery API (venues + events, free)

Register at https://developer.ticketmaster.com — free key, 5,000 calls/day,
rate limit 5 req/sec. This is your source for **every major venue and concert
in the country**.

- Venue search: `GET /discovery/v2/venues?keyword=red+rocks&apikey=KEY`
- Events at a venue:
  `GET /discovery/v2/events?venueId={id}&classificationName=music&apikey=KEY`
- Events include `priceRanges` (min/max face value) — useful, but primary
  prices are mostly static. The interesting price movement is on resale.

### 2.2 SeatGeek API (resale price stats — your main price signal)

Register at https://seatgeek.com/account/develop — free client ID. Every
event response includes a `stats` object:

```json
"stats": {
  "listing_count": 428,
  "lowest_price": 89,
  "average_price": 173,
  "median_price": 141,
  "highest_price": 950
}
```

This is exactly what you need to snapshot over time. Endpoints:

- Venues: `GET https://api.seatgeek.com/2/venues?q=red+rocks&client_id=ID`
- Events: `GET https://api.seatgeek.com/2/events?venue.id={id}&type=concert&client_id=ID`

### 2.3 Matching the two sources

Match Ticketmaster and SeatGeek venues on `(normalized name, city, state)`,
falling back to lat/long proximity (< 500 m). Store both external IDs on your
venue row. You can also run on SeatGeek alone to start — it has venues,
events, AND price stats, so **v1 can be SeatGeek-only**. Add Ticketmaster
later for better event coverage and face-value ranges.

> **Do not scrape** Ticketmaster or StubHub HTML. Both prohibit it, both
> aggressively bot-detect, and the official APIs give you what you need.

---

## 3. Database schema

```sql
CREATE TABLE venues (
  id            SERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  city          TEXT NOT NULL,
  state         TEXT NOT NULL,        -- 2-letter code
  lat           DOUBLE PRECISION,
  lng           DOUBLE PRECISION,
  seatgeek_id   INTEGER UNIQUE,
  ticketmaster_id TEXT UNIQUE,
  capacity      INTEGER
);

CREATE TABLE events (
  id            SERIAL PRIMARY KEY,
  venue_id      INTEGER NOT NULL REFERENCES venues(id),
  title         TEXT NOT NULL,
  performer     TEXT,
  event_date    TIMESTAMPTZ NOT NULL,
  on_sale_date  TIMESTAMPTZ,
  seatgeek_id   INTEGER UNIQUE,
  ticketmaster_id TEXT UNIQUE,
  face_min      NUMERIC,
  face_max      NUMERIC,
  is_tracked    BOOLEAN DEFAULT TRUE  -- stop polling past events
);

-- The core table: one row per event per poll. This is your gold.
CREATE TABLE price_snapshots (
  id            BIGSERIAL PRIMARY KEY,
  event_id      INTEGER NOT NULL REFERENCES events(id),
  captured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  lowest_price  NUMERIC,
  median_price  NUMERIC,
  average_price NUMERIC,
  highest_price NUMERIC,
  listing_count INTEGER,
  source        TEXT NOT NULL DEFAULT 'seatgeek'
);
CREATE INDEX idx_snapshots_event_time ON price_snapshots(event_id, captured_at);

CREATE TABLE predictions (
  id            SERIAL PRIMARY KEY,
  event_id      INTEGER NOT NULL REFERENCES events(id) UNIQUE,
  recommendation TEXT NOT NULL,       -- 'buy_now' | 'wait' | 'monitor'
  best_window_start DATE,
  best_window_end   DATE,
  confidence    NUMERIC,              -- 0..1
  reasoning     TEXT,                 -- human-readable explanation
  computed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 4. The polling worker (build this FIRST)

Start collecting data on day one — every day you wait is a day of training
data lost. Poll every 4–6 hours; that's plenty of resolution for multi-week
price curves and stays far under rate limits.

```ts
// worker/poll.ts
import cron from "node-cron";
import { db } from "./db";

const SG = "https://api.seatgeek.com/2";
const CLIENT_ID = process.env.SEATGEEK_CLIENT_ID!;

async function pollTrackedEvents() {
  const events = await db.query(
    `SELECT id, seatgeek_id FROM events
     WHERE is_tracked AND event_date > now() AND seatgeek_id IS NOT NULL`
  );

  for (const ev of events.rows) {
    const res = await fetch(`${SG}/events/${ev.seatgeek_id}?client_id=${CLIENT_ID}`);
    if (!res.ok) continue;
    const { stats } = await res.json();
    await db.query(
      `INSERT INTO price_snapshots
         (event_id, lowest_price, median_price, average_price, highest_price, listing_count)
       VALUES ($1,$2,$3,$4,$5,$6)`,
      [ev.id, stats.lowest_price, stats.median_price,
       stats.average_price, stats.highest_price, stats.listing_count]
    );
    await new Promise(r => setTimeout(r, 300)); // stay polite on rate limits
  }
}

async function refreshEventsForTrackedVenues() {
  // For each venue any user has selected, upsert upcoming concerts
  const venues = await db.query(`SELECT id, seatgeek_id FROM venues WHERE seatgeek_id IS NOT NULL`);
  for (const v of venues.rows) {
    const res = await fetch(
      `${SG}/events?venue.id=${v.seatgeek_id}&type=concert&per_page=50&client_id=${CLIENT_ID}`
    );
    const { events } = await res.json();
    for (const e of events) {
      await db.query(
        `INSERT INTO events (venue_id, title, performer, event_date, seatgeek_id)
         VALUES ($1,$2,$3,$4,$5)
         ON CONFLICT (seatgeek_id) DO UPDATE SET event_date = EXCLUDED.event_date`,
        [v.id, e.title, e.performers?.[0]?.name ?? null, e.datetime_utc, e.id]
      );
    }
  }
}

cron.schedule("0 */4 * * *", async () => {   // every 4 hours
  await refreshEventsForTrackedVenues();
  await pollTrackedEvents();
});
```

**Which events to track:** every upcoming concert at every venue any user has
selected. That keeps the poll set demand-driven and small. If you want a
nationwide dataset for model training, additionally track the top ~200
venues by capacity.

---

## 5. Prediction engine — "best time to buy"

### Phase 1: Heuristic (ship this in week 1)

Resale concert prices follow a well-documented pattern:

- Prices **spike at on-sale** (hype), then **drift down** as more listings appear.
- They typically **bottom out 3–14 days before the event** for
  average-demand shows, as resellers get nervous.
- **High-demand shows are the exception**: prices only climb. Signal: rising
  median with *falling* listing count.
- The **final 24–72 hours** can dip further (fire-sale) but is risky —
  inventory can also vanish.

Encode that directly:

```ts
// server/predict.ts
type Snapshot = { captured_at: Date; median_price: number; listing_count: number };

export function predict(snapshots: Snapshot[], eventDate: Date) {
  const daysOut = (eventDate.getTime() - Date.now()) / 86_400_000;
  const recent = snapshots.slice(-6);                    // last ~24h of polls
  const week = snapshots.filter(s => s.captured_at > new Date(Date.now() - 7 * 86_400_000));

  const trend = linearSlope(week.map(s => s.median_price));        // $/day
  const supplyTrend = linearSlope(week.map(s => s.listing_count)); // listings/day
  const current = recent.at(-1)?.median_price;
  const historicalMin = Math.min(...snapshots.map(s => s.median_price));

  // High-demand: price rising AND supply shrinking -> it only gets worse
  if (trend > 0 && supplyTrend < 0) {
    return rec("buy_now", 0.8,
      "Prices are rising while listings disappear — this show is selling out. Waiting will cost you.");
  }
  // Near the historical low with a falling trend and time left -> wait a bit
  if (trend < 0 && daysOut > 14) {
    return rec("wait", 0.7,
      `Prices are falling (~$${Math.abs(trend).toFixed(0)}/day). Typical bottom is 3–14 days out.`);
  }
  // In the sweet spot and at/near the observed low -> buy
  if (daysOut <= 14 && daysOut > 2 && current !== undefined && current <= historicalMin * 1.05) {
    return rec("buy_now", 0.75,
      "You're in the 3–14 day window and at the lowest price we've seen for this event.");
  }
  if (daysOut <= 2) {
    return rec("buy_now", 0.6,
      "Event is imminent. Prices may dip further, but inventory can vanish — buy if you're committed.");
  }
  return rec("monitor", 0.5, "No strong signal yet — we'll keep watching.");
}
```

Run this nightly for every tracked event and upsert into `predictions`.
Always store the `reasoning` string — users trust a recommendation they can
read the *why* for, and it makes debugging the model trivial.

### Phase 2: Learned model (after 4–8 weeks of snapshots)

Once you have completed events (you know each one's actual price minimum and
when it occurred), you have labeled training data. Frame it as:

> Given (days_until_event, current_median / historical_median, 7-day price
> slope, 7-day supply slope, venue capacity, day-of-week of event, performer
> popularity), predict **P(price will drop ≥5% before the event)**.

Train gradient-boosted trees (XGBoost/LightGBM) in a small Python service or
offline notebook; export predictions back into the `predictions` table. If
P(drop) > 0.6 → "wait", < 0.4 → "buy now", else "monitor". Keep the
heuristic as fallback for events with sparse data.

---

## 6. Backend API

Small surface, four resources:

```
GET  /api/venues?q=&state=          venue search (proxy SeatGeek + cache in DB)
GET  /api/venues/:id/events         upcoming concerts at a venue
GET  /api/events/:id                event detail + latest prediction
GET  /api/events/:id/prices         price_snapshots time series for the chart
POST /api/track                     { venueId } — user selects a venue; worker
                                    begins polling its events
```

Notes:
- Venue search should hit your DB first and fall through to SeatGeek's
  `/venues?q=` on miss, upserting results — the catalog builds itself.
- Cache API responses (60s is fine). Never call SeatGeek/Ticketmaster
  directly from the browser: you'd leak your keys. All third-party calls go
  through your backend.
- Auth is optional for v1; add it when you add per-user watchlists/alerts.

---

## 7. React frontend

```
src/
  api/client.ts          typed fetch wrappers for the API above
  pages/
    VenueSearch.tsx      search box + state filter + results
    VenueDetail.tsx      upcoming events at the venue
    EventDetail.tsx      the money page: chart + recommendation
  components/
    RecommendationCard.tsx   BUY NOW / WAIT / MONITOR + confidence + reasoning
    PriceChart.tsx           Recharts line chart of median/lowest price
    VenueCard.tsx
```

Build order:

1. **VenueSearch** — debounced text input (300 ms) hitting `/api/venues?q=`,
   plus a state dropdown for "across the country" browsing. Selecting a venue
   calls `POST /api/track` and navigates to VenueDetail. (A map view with
   react-leaflet is a nice v2; a list is fine for v1.)
2. **VenueDetail** — list of upcoming concerts with date, performer, current
   lowest price, and a small recommendation badge.
3. **EventDetail** — the core screen:
   - `RecommendationCard`: big verdict ("WAIT — prices falling ~$4/day,
     typical bottom is 6–10 days out"), confidence, and the reasoning text
     straight from the `predictions` row.
   - `PriceChart`: `median_price` and `lowest_price` from
     `/api/events/:id/prices`, with a shaded band for the predicted best
     window and a dashed line at face value if known.
   - Honest empty state: "Tracking started today — check back in a few days"
     when snapshots are sparse. Do not fake a confident prediction without data.
4. Use React Query (TanStack Query) for fetching/caching; it eliminates most
   loading-state boilerplate.

---

## 8. Build order & milestones

| Week | Milestone |
|------|-----------|
| 1    | Repo scaffolding (Vite + Express + Postgres). Get SeatGeek key. **Ship the polling worker** tracking ~50 big venues. Data starts accumulating. |
| 2    | API endpoints + VenueSearch/VenueDetail pages. Heuristic predictor + RecommendationCard. |
| 3    | EventDetail with PriceChart. Deploy (Vercel + Railway). Nightly prediction job. |
| 4+   | Add Ticketmaster for coverage/face values. Email/push price alerts. |
| 8+   | Train the Phase-2 model on your accumulated snapshots. Backtest against completed events before trusting it. |

---

## 9. Rules of the road

- **No automated purchasing, ever.** The BOTS Act prohibits circumventing
  purchase controls; platforms ban automated checkout. TixTime recommends,
  humans buy. Link users out to the ticketing page instead.
- **Respect API terms**: keep keys server-side, honor rate limits, and check
  each API's attribution requirements (SeatGeek requires attribution/linkback).
- **Don't scrape** sites that prohibit it. The official APIs are sufficient.
- **Be honest in the UI** about confidence and data sparsity. A wrong "BUY
  NOW" that users trusted is worse than "we're still collecting data."
