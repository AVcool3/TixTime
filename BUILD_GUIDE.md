# TixTime Build Guide

Building TixTime: a React app where users pick concert venues across the
country and get the **best time to buy tickets**, powered by:

- **Data**: [rebrowser/seatgeek-dataset](https://github.com/rebrowser/seatgeek-dataset)
  — bulk SeatGeek marketplace data (events, listings, performers, venues) as
  Parquet, updated daily.
- **Logic flow**: modeled on
  [ubparmar/Airline_Fare_Prediction](https://github.com/ubparmar/Airline_Fare_Prediction)
  — historical data → preprocessing → feature engineering → regression model
  trained in notebooks → serialized model → web app serves predictions.

The one twist vs. the airline project: it predicts *a fare given inputs*;
we need *the best day to buy*. Solution: train the same style of regressor
with **days-until-event as a feature**, then sweep that feature over every
remaining purchase date and take the minimum. The argmin **is** the
recommendation.

---

## 0. Ground rules (read first)

- **This is a recommendation tool, not a purchasing bot.** Automating ticket
  checkout violates the U.S. BOTS Act and platform ToS. TixTime tells a
  human when to buy and links out; it never buys.
- **Dataset licensing**: the rebrowser dataset is free for **research and
  non-commercial use with attribution**; commercial use is paid (~$2/1,000
  rows). Comply with their terms and SeatGeek's ToS.
- **The GitHub repo is a preview.** Critically, the preview **redacts price,
  fees, and deal-score fields** — the exact columns this project needs — and
  its events skew sports (MLB/NBA/NFL). Before building anything, get access
  to the full dataset via Rebrowser and confirm (a) price fields are
  populated and (b) concert/music events are covered. Everything below
  assumes you have listings **with prices**. If that falls through, the
  fallback is polling SeatGeek's free API yourself (see §7).

---

## 1. Architecture

Mirrors Airline_Fare_Prediction's shape (data → notebooks → trained model →
web app), with the Django monolith split into a React frontend + Python API
so the UI matches the original TixTime plan:

```
 rebrowser Parquet drops (daily)
        │
        ▼
┌───────────────────┐      ┌──────────────────────┐
│  Ingestion job    │─────▶│  PostgreSQL           │
│  (Python, cron)   │      │  venues/events/       │
└───────────────────┘      │  listings/daily_prices│
                           └─────────▲────────────┘
┌───────────────────┐               │
│  Notebooks/        │  train ──────┤
│  train.py          │  read        │
│  → model.joblib    │              │
└─────────┬─────────┘               │
          ▼                         │
┌───────────────────┐               │
│  FastAPI service   │◀─────────────┘
│  loads model.joblib│
└─────────▲─────────┘
          │ JSON
┌─────────┴─────────┐
│  React SPA (Vite)  │  venue picker → event page → buy-timing curve
└───────────────────┘
```

| Layer          | Choice                          | Airline-repo analog |
|----------------|--------------------------------|---------------------|
| Data           | rebrowser Parquet → Postgres    | `Data/` CSVs |
| EDA + training | Jupyter notebooks + `train.py`  | `Notebooks/` |
| Model          | sklearn regressor → `joblib`    | Decision Tree Regressor |
| Serving        | FastAPI (Python)                | Django views |
| Frontend       | React + Vite + TS + Recharts    | Django templates/HTML |
| Scheduler      | cron (ingest daily, retrain weekly) | manual retraining |

Repo layout:

```
tixtime/
  ingestion/     load_parquet.py, build_daily_prices.py
  notebooks/     01_eda.ipynb, 02_features.ipynb, 03_model.ipynb
  ml/            features.py, train.py, model.joblib
  api/           main.py (FastAPI), predict.py
  web/           React app (Vite)
```

---

## 2. Ingesting the dataset

The dataset has four entities; map them straight into Postgres:

| Parquet entity   | Table      | Keep |
|------------------|-----------|------|
| `venues`         | `venues`   | id, name, city, state, lat/lng, capacity, popularity score |
| `performers`     | `performers` | id, name, type, popularity score |
| `events`         | `events`   | id, title, datetime, status, venue_id, performer_id, taxonomy (filter to music/concert), cross-platform IDs |
| `event_listings` | `listings` | event_id, captured/observed date, section, row, quantity, marketplace, **price, fees, deal bucket** |

Loader — DuckDB reads Parquet natively and writes to Postgres in a few lines:

```python
# ingestion/load_parquet.py
import duckdb

con = duckdb.connect()
con.execute("INSTALL postgres; LOAD postgres;")
con.execute("ATTACH 'dbname=tixtime user=tixtime host=localhost' AS pg (TYPE postgres);")

for entity in ["venues", "performers", "events", "event_listings"]:
    con.execute(f"""
        INSERT INTO pg.{entity}
        SELECT * FROM read_parquet('data/drops/{entity}/*.parquet')
        ON CONFLICT DO NOTHING
    """)
```

Run daily via cron when new drops land. Then collapse raw listings into the
training-friendly table — one row per event per observation day:

```sql
-- ingestion/build_daily_prices.sql
INSERT INTO daily_prices (event_id, observed_date, days_until_event,
                          min_price, median_price, listing_count)
SELECT
  l.event_id,
  l.observed_date,
  (e.event_date::date - l.observed_date) AS days_until_event,
  MIN(l.price + COALESCE(l.fees, 0)),
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY l.price + COALESCE(l.fees, 0)),
  COUNT(*)
FROM listings l
JOIN events e ON e.id = l.event_id
WHERE e.taxonomy_root = 'concert'      -- adjust to actual taxonomy values
GROUP BY 1, 2, 3
ON CONFLICT (event_id, observed_date) DO UPDATE
  SET min_price = EXCLUDED.min_price,
      median_price = EXCLUDED.median_price,
      listing_count = EXCLUDED.listing_count;
```

`daily_prices` is your equivalent of the airline repo's cleaned training CSV.
Always model **all-in price (price + fees)** — that's what buyers actually pay.

---

## 3. Feature engineering — the airline features, translated

This is the "logic flows with" part. Every feature in
Airline_Fare_Prediction has a direct ticket-world analog:

| Airline feature            | TixTime feature                            |
|----------------------------|--------------------------------------------|
| Journey date → month/day   | Event date → month, day-of-week (Fri/Sat premium) |
| Days until departure       | **`days_until_event`** (the sweep variable) |
| Source / destination city  | Venue: city/state, capacity, popularity score |
| Airline carrier            | Performer: popularity score, type          |
| Total stops                | Listing supply: `listing_count` that day   |
| Flight duration            | (no analog — drop)                         |
| —                          | Price momentum: 7-day slope of median price |
| —                          | `price_ratio` = current median / event's first observed median |

```python
# ml/features.py
import pandas as pd

CATEGORICAL = ["venue_state", "event_dow", "performer_type"]
NUMERIC = ["days_until_event", "event_month", "venue_capacity",
           "venue_popularity", "performer_popularity",
           "listing_count", "price_slope_7d", "price_ratio"]
TARGET = "median_price"

def build_features(daily: pd.DataFrame) -> pd.DataFrame:
    df = daily.sort_values(["event_id", "observed_date"]).copy()
    df["event_dow"] = df["event_date"].dt.dayofweek
    df["event_month"] = df["event_date"].dt.month
    first = df.groupby("event_id")["median_price"].transform("first")
    df["price_ratio"] = df["median_price"] / first
    df["price_slope_7d"] = (
        df.groupby("event_id")["median_price"]
          .transform(lambda s: s.diff(6) / 6)   # ≈ $/day over last 7 obs
          .fillna(0)
    )
    return df

def encode(df: pd.DataFrame) -> pd.DataFrame:
    return pd.get_dummies(df[CATEGORICAL + NUMERIC + [TARGET]],
                          columns=CATEGORICAL)   # same one-hot approach as the airline repo
```

Preprocessing, mirroring the airline notebooks: drop rows with null prices,
clip listing prices at the 1st/99th percentile per event (speculative $9,999
listings would poison the target), and require ≥5 observation days per event.

---

## 4. Model training

Same starting model as the airline repo (Decision Tree Regressor), same
notebook-driven workflow — but hold out **entire events**, not random rows.
Random splits leak: rows from the same event on adjacent days are nearly
identical, which inflates test scores and is the airline-repo mistake worth
fixing rather than copying.

```python
# ml/train.py
import joblib
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit
from sklearn.tree import DecisionTreeRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error
from features import build_features, encode, TARGET

daily = pd.read_sql("SELECT * FROM daily_prices_joined", DB_URL)  # view joining venue/performer attrs
df = build_features(daily)
X = encode(df).drop(columns=[TARGET])
y = df[TARGET]

# split by event_id so the test set is genuinely unseen events
gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
train_idx, test_idx = next(gss.split(X, y, groups=df["event_id"]))

model = DecisionTreeRegressor(max_depth=12, min_samples_leaf=20)  # v1, per airline repo
# model = RandomForestRegressor(n_estimators=300, min_samples_leaf=10)  # v2: strictly better here
model.fit(X.iloc[train_idx], y.iloc[train_idx])

mae = mean_absolute_error(y.iloc[test_idx], model.predict(X.iloc[test_idx]))
print(f"MAE on unseen events: ${mae:.2f}")

joblib.dump({"model": model, "columns": list(X.columns)}, "ml/model.joblib")
```

Also **backtest the actual product claim**: for each held-out completed
event, compute the day your sweep (§5) would have said "buy" and compare
that day's real price to the event's true minimum. Report the average $ left
on the table — that number, not MAE, is whether TixTime works. Retrain
weekly via cron as new daily drops extend the dataset.

---

## 5. Serving: the days-until-event sweep

The airline app answers one (inputs → fare) query. TixTime asks the model
the same question once per remaining purchase date and returns the curve +
its minimum:

```python
# api/predict.py
import joblib, pandas as pd
from datetime import date, timedelta

bundle = joblib.load("ml/model.joblib")
MODEL, COLUMNS = bundle["model"], bundle["columns"]

def best_time_to_buy(event_row: dict) -> dict:
    days_out_today = (event_row["event_date"] - date.today()).days
    rows = []
    for d in range(days_out_today, 0, -1):          # every remaining purchase day
        f = dict(event_row["features"])              # venue/performer/current-market features
        f["days_until_event"] = d
        rows.append(f)

    X = pd.get_dummies(pd.DataFrame(rows)).reindex(columns=COLUMNS, fill_value=0)
    preds = MODEL.predict(X)

    curve = [
        {"buy_date": str(date.today() + timedelta(days=days_out_today - d)),
         "days_until_event": d,
         "predicted_price": round(float(p), 2)}
        for d, p in zip(range(days_out_today, 0, -1), preds)
    ]
    best = min(curve, key=lambda c: c["predicted_price"])
    today_price = curve[0]["predicted_price"]
    savings = today_price - best["predicted_price"]

    return {
        "curve": curve,
        "best_buy_date": best["buy_date"],
        "recommendation": "wait" if savings > max(5, 0.05 * today_price) else "buy_now",
        "expected_savings": round(savings, 2),
        "reasoning": (
            f"Model expects the low (${best['predicted_price']}) around "
            f"{best['buy_date']} ({best['days_until_event']} days out); "
            f"buying today costs about ${today_price}."
        ),
    }
```

FastAPI surface:

```
GET /api/venues?q=&state=        search venues (from the ingested venues table)
GET /api/venues/{id}/events      upcoming concerts at a venue
GET /api/events/{id}             event detail + latest observed prices
GET /api/events/{id}/history     daily_prices series (the observed past)
GET /api/events/{id}/forecast    best_time_to_buy() output (the predicted future)
```

Caveat to encode in the response: features like `listing_count` and
`price_slope_7d` are only known for *today* — for future purchase dates the
sweep holds them at current values. That's standard for this style of model
but means confidence decays with horizon; say so in the UI for far-out dates.

---

## 6. React frontend

```
web/src/
  pages/VenueSearch.tsx      debounced search + state filter (nationwide browsing)
  pages/VenueDetail.tsx      upcoming concerts, current low price, rec badge
  pages/EventDetail.tsx      the money page
  components/PriceChart.tsx  Recharts: solid line = observed history,
                             dashed line = forecast curve, dot on best_buy_date
  components/RecommendationCard.tsx  BUY NOW / WAIT + expected savings + reasoning
```

EventDetail renders one chart from two endpoints: `/history` (solid,
observed) flowing into `/forecast` (dashed, predicted), with the recommended
buy date marked and a shaded ±few-days window around it. The
RecommendationCard shows the verdict, expected savings, and the `reasoning`
string verbatim — users trust a recommendation they can read the why for.
Use TanStack Query for data fetching. When an event has fewer than ~5
observation days, show "still collecting data" instead of a confident
forecast.

---

## 7. Fallback / supplement: live polling

The dataset gives deep history (great for training) but you're dependent on
daily drops for freshness, and the preview's price redaction means access
could be a blocker. Keep the original plan's poller as a complement: SeatGeek's
free API (`/events/{id}` → `stats.lowest_price/median_price/listing_count`,
client ID from https://seatgeek.com/account/develop) polled every few hours
for events at user-selected venues, written into the same `daily_prices`
table. Train on the bulk dataset; keep "today's price" fresh via the API.

---

## 8. Build order

| Step | Milestone |
|------|-----------|
| 1 | **Verify the full dataset**: request access from Rebrowser, confirm prices are populated and concerts are covered. This is the go/no-go gate. |
| 2 | Ingestion: Parquet → Postgres → `daily_prices`. Notebook 01: EDA — plot median price vs. days-until-event for a few dozen concerts; confirm the price curves actually have exploitable shape. |
| 3 | Notebooks 02–03 + `train.py`: features, DecisionTree baseline, event-grouped split, backtest the buy-date claim. |
| 4 | FastAPI: model loading, sweep endpoint, venue/event endpoints. |
| 5 | React: VenueSearch → VenueDetail → EventDetail with history+forecast chart. |
| 6 | Cron: daily ingest, weekly retrain. Deploy (web: Vercel; api+jobs: Railway/Render — the airline repo's host works fine here too). |
| 7 | Upgrade DecisionTree → RandomForest/LightGBM; add the API poller (§7) for freshness. |
