# TixTime

**When should you buy that ticket?** A buy-timing engine for concert and sports
tickets, built on the structure of
[ubparmar/Airline_Fare_Prediction](https://github.com/ubparmar/Airline_Fare_Prediction)
(predict a fare, decide when to book) and extended into a full product: a real
event catalogue, a calibrated price model, a walk-forward backtest, alerting,
and a UI whose centrepiece is a past-and-future price chart.

```
make demo      # build everything from the raw CSV
make api       # http://localhost:8000
make web       # http://localhost:5173
```

---

## Read this first: what is real and what is not

**The event catalogue is real.** 7,118 events, 170 venues, 124 franchises — real
names, dates, venues and SeatGeek purchase links, taken from the export in
`data/raw/seatgeek_events.csv`.

**Every price is simulated.** That export has *zero* price data: all 7,118 rows
have empty `lowestPrice`, `medianPrice`, `averagePrice` and `listingCount`, and
each event appears exactly once (`_firstSeenAt == _lastSeenAt`), so there is no
price *history* in it at all. It is the redacted GitHub preview of the rebrowser
dataset. This deployment also has no network route to any ticket API —
`api.seatgeek.com`, `app.ticketmaster.com` and `seatgeek.com` all fail to
connect, and no API credentials are present.

Without a price series there is nothing to learn buy-timing from, so TixTime
generates one. Every generated row is tagged `source='synthetic_v1'` in the
database, that tag travels in every API payload as `source` + `is_simulated`,
and the UI renders a non-dismissible ribbon plus a per-figure marker. The
`<Price>` React component *refuses to render* a figure without a provenance
token, so shipping an unlabelled fabricated dollar amount is a type error rather
than an oversight.

### What the accuracy numbers mean

Backtest over all 3,000 held-out events (53,696 scored decisions):

| Strategy | Mean paid | Overpay vs perfect | Savings captured | Within 5% of best |
|---|---|---|---|---|
| Buy immediately | $146.60 | $13.22 | 0.0% | 28.7% |
| **Follow TixTime** | **$140.12** | **$6.75** | **49.0%** | **69.9%** |
| Wait until 30 days out | $141.40 | $9.45 | 39.1% | 38.6% |
| Wait until 14 days out | $144.01 | $10.64 | 19.6% | 33.3% |
| Wait until 7 days out | $147.55 | $14.18 | −7.2% | 28.1% |

On smaller 250-event samples the savings-captured figure moves between 44% and
50% while the best fixed rule sits at 41–43%, so on *that* metric the margin is
sometimes narrow. The hit-rate advantage is the robust one: the model lands
within 5% of the best available price roughly 70% of the time against 39% for
the best heuristic, across every sample tested.

**Legitimate reading:** the pipeline recovers timing structure it was not shown.
The model trains only on events that finished before the clock date and is
scored on later ones; it is scored as a *policy* (ask daily, buy on the first
BUY signal), not as a single lucky prediction; and it beats every fixed
heuristic including the best one. Note that "wait until 7 days out" is *worse*
than buying immediately.

**Not legitimate:** treating these as a claim about real ticket markets. A model
trained on a simulator learns the simulator. The only way to know whether this
predicts real prices is to connect a live collector and measure it.

---

## Connecting real prices

`tixtime/pricing/collectors.py` defines the collector interface with working
SeatGeek and Ticketmaster implementations:

```bash
export SEATGEEK_CLIENT_ID=...        # or TICKETMASTER_API_KEY
python -m tixtime.pricing.collect    # snapshots land tagged 'seatgeek'
```

Real rows land in the same `price_snapshots` table, tagged with the provider
name instead of `synthetic_v1`. Every surface reads that tag, so real data stops
being labelled as simulated the moment it arrives.

---

## The as-of clock

The catalogue runs to July 2026, which the real calendar has passed — only
**59 of 7,118** events are still in the future. A buy-timing product needs both
sides of "now", so TixTime runs on an explicit as-of date, shown and adjustable
in the top bar.

Default is **2026-02-15**, which splits the catalogue into 1,987 modelable
events with complete price paths (the only legitimate training set) and 3,113
still upcoming (what the site recommends on). Override with `TIXTIME_AS_OF`.

This is not a trick to hide the data's age — it is the same mechanism the
backtest uses to replay any past date. Move the clock backward in the UI and
history, forecast, recommendation and alert evaluation all recompute from only
what was knowable then.

---

## How it works

```
data/raw/seatgeek_events.csv
   │  catalog/parse.py     recover venue geography + team identity from url/name
   │  catalog/ingest.py    → DuckDB: events, venues, franchises, seat_tiers
   ▼
pricing/simulator.py       calibrated price paths, tagged synthetic_v1
   │                       (or pricing/collectors.py for real data)
   ▼
ml/features.py             (as-of, horizon) pairs; strictly backward-looking
ml/train.py                q10/q50/q90 + a "how much is waiting worth" head
   │
   ├─ ml/backtest.py       policy simulation vs BUY_NOW / FIXED_T / ORACLE
   ├─ ml/precompute.py     deal board + sparkline cache
   ▼
api/main.py                FastAPI; as-of enforced, provenance in every payload
web/                       React + Recharts
```

### Recovering structure the export threw away

`performerIds`, `eventScore` and `popularityScore` are 100% redacted, so venue
geography and team identity come from parsing `url` and `name`. Measured
coverage: **170/170 venues** resolve (two URL shapes exist; a second pass
matches stragglers against the city vocabulary learned from the first), and
5,756 events resolve to a known franchise — the rest are all-star games, drafts
and minor-league events that genuinely are not franchises.

### The price model

Three design choices that exist because the obvious alternative is wrong:

1. **Direct multi-horizon, not a feature sweep.** The natural approach — train
   `price ~ f(days_until_event, rolling stats, inventory)` then sweep
   `days_until_event` at serving time — is invalid, because the rolling and
   inventory features are *themselves* functions of the swept quantity. Freezing
   them asks the model about states that never occur in training, and
   gradient-boosted trees do not extrapolate. Instead each row is an
   `(as-of, horizon)` pair with horizon as an ordinary input; serving sweeps
   horizon.

2. **The target is a log return, not a log price.** Tier multipliers span an
   order of magnitude (upper deck $15, courtside $600); a level model spends its
   capacity rediscovering them instead of learning timing.

3. **The buy decision is a dollar regression**, on `log(cheapest remaining /
   price now)` — not a "is today near the minimum" classifier. That label is
   true by construction near the event, and its tolerance means $2 on an
   upper-deck seat and $27 on a floor seat.

### Guards against fooling ourselves

Each is enforced by a test in `tests/test_pipeline.py`:

- Training uses only events whose paths **finished** before the cutoff; the
  label builder raises on a censored event.
- The franchise demand priors that seed the simulator are **excluded** from the
  feature set; `design_matrix()` raises if they reappear.
- History features are backward-looking only. A test builds features on the full
  series and on the series truncated at *t* and asserts the row at *t* is
  bit-identical.
- A 30-day burn-in precedes the served window so rolling features are defined on
  the first served day, rather than being dropped or zero-filled.
- Every split is grouped by `event_id`; a random row split would put day 91 in
  train and day 92 in test.
- Serving never reads a snapshot dated after the as-of date, even though the
  warehouse physically contains the whole future path.

### The price process

Following Sweeting (2012, *JPE*), which finds secondary-market prices **decline**
into game day — a ticket is worth nothing once the game starts — decline is the
majority regime (~70% of events bottom in the final 72 hours) with a late
run-up as a real but minority outcome for genuinely hot events. The terminal
regime resolves *per seat tier*: premium seats can firm up while the upper deck
is being dumped. `tests/test_simulator.py` pins this calibration, including that
`corr(demand, optimal_buy_day)` stays above 0.45 — without it, every event would
bottom on the same day and the recommendation would be noise.

---

## Layout

| Path | What |
|---|---|
| `tixtime/catalog/` | CSV → DuckDB, URL/name parsing, franchise + venue reference data |
| `tixtime/pricing/` | Collector interface (real feeds) and the market simulator |
| `tixtime/ml/` | Features, training, serving, precompute, backtest |
| `tixtime/alerts/` | Rule evaluation and delivery channels |
| `tixtime/api/` | FastAPI service |
| `web/` | React + TypeScript + Recharts frontend |
| `tests/` | 68 tests: parsing, simulator calibration, leakage, serving, API |

## Honest limits

- Seat tiers are a **modelled construct**, not observed sections — the export has
  no section or listing data. Where SeatGeek reports general admission or
  disabled seat selection, TixTime shows a single tier rather than inventing
  sections (2,296 of 7,118 events).
- SeatGeek's URL addresses an **event, not a seat**, so no honest "buy this exact
  seat" deep link exists. The event page links to the event and tells you which
  section to filter to and what a good price looks like there.
- TixTime never buys tickets. It recommends timing and links out — automating
  purchases would violate the BOTS Act and platform terms.
- Alerts: the in-app inbox is the system of record. Webhook delivery genuinely
  fires when configured; email needs `TIXTIME_SMTP_HOST`. Unconfigured channels
  are recorded as `unconfigured` rather than pretending a message was sent.
