# TixTime

Ticket Purchase Timer — pick a concert venue anywhere in the country and get
the **best time to buy** tickets, predicted from SeatGeek marketplace price
history.

TixTime **recommends timing only**. It never automates ticket purchases
(that would violate the U.S. BOTS Act and platform ToS) — it tells a human
when to buy and links out.

## How it works

```
rebrowser/seatgeek-dataset (Parquet, daily)
        │  ingestion/ingest.py  (DuckDB)
        ▼
   DuckDB warehouse  ──►  snapshot_features view  (one row / event / day)
        │  ml/train.py
        ▼
   model.joblib (RandomForest)  ──►  api/  (FastAPI)  ──►  web/ (React + Recharts)
```

The model predicts an event's median resale price as a function of
`days_until_event` plus venue/performer/demand features (the logic flow is
modeled on [ubparmar/Airline_Fare_Prediction](https://github.com/ubparmar/Airline_Fare_Prediction)).
To turn "predict a price" into "recommend a date", the serving layer
**sweeps** `days_until_event` over every remaining purchase day and returns
the cheapest — that argmin is the recommendation, and the swept curve is the
forecast line in the UI.

## Quick start (with synthetic data — no dataset access needed)

```bash
python -m venv .venv && ./.venv/bin/pip install -r requirements.txt

# 1. generate sample Parquet drops in the real dataset schema
./.venv/bin/python scripts/make_sample_data.py

# 2. ingest -> DuckDB warehouse
./.venv/bin/python -m ingestion.ingest auto data/sample-drops

# 3. train the model (prints MAE + the buy-date backtest)
./.venv/bin/python -m ml.train

# 4. run the API
./.venv/bin/uvicorn api.main:app --port 8000

# 5. in another terminal, run the UI
cd web && npm install && npm run dev   # http://localhost:5173
```

## Using the real dataset

`ingestion/ingest.py` expects the [rebrowser/seatgeek-dataset](https://github.com/rebrowser/seatgeek-dataset)
layout (`venues/`, `performers/`, `events/data/`, `event-listings/data/`)
with the documented camelCase columns:

```bash
./.venv/bin/python -m ingestion.ingest auto /path/to/seatgeek-dataset
```

The **events** drops carry per-day price aggregates
(`lowestPrice`/`medianPrice`/`averagePrice`/`highestPrice`/`listingCount`),
which become one `event_snapshots` row per event per day — that's the price
time series the model learns from. The `event-listings` drops are optional
and only needed if you want to compute the target from all-in
`priceWithFees` instead. See `BUILD_GUIDE.md` for the full rationale.

Schedule daily refresh + retrain with `scripts/retrain.sh` via cron.

## Layout

| Path            | What |
|-----------------|------|
| `ingestion/`    | DuckDB schema + Parquet loader for the four dataset entities |
| `ml/`           | Feature engineering, training, buy-date backtest |
| `api/`          | FastAPI: venue search, events, price history, forecast (the sweep) |
| `web/`          | React + Vite + Recharts frontend |
| `scripts/`      | Synthetic data generator + retrain cron script |
| `BUILD_GUIDE.md`| Full design doc and rationale |

## Data & licensing

The rebrowser dataset is free for research/non-commercial use with
attribution; commercial use is paid. Comply with their terms and SeatGeek's
ToS. The GitHub **preview** redacts listing price fields and skews toward
sports events — confirm the full dataset has populated concert prices before
relying on trained output.
