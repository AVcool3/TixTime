# TixTime
#
#   make quick   fastest path to a browsable app  (~4 min)
#   make demo    the above plus the accuracy analysis  (~8 min)
#
# Then, in two terminals:
#   make api     http://localhost:8000
#   make web     http://localhost:5173
#
# Requires Python 3.11+ and Node 18+. Every artefact is derived from
# data/raw/seatgeek_events.csv, which is committed, so a clean clone rebuilds
# byte-identical output -- the simulator is seeded per event.
#
# Stages are ordered by dependency and each is runnable on its own.

PY := ./.venv/bin/python
PIP := ./.venv/bin/pip

.PHONY: demo quick setup ingest simulate train precompute backtest regime-test \
        analysis api web test clean

## quick: everything needed to browse and get recommendations.
## The Accuracy page will say it has no results yet -- run `make analysis` to
## fill it in. Nothing else depends on those two stages.
quick: setup ingest simulate train precompute
	@echo ""
	@echo "Ready. Start the two servers in separate terminals:"
	@echo "    make api      ->  http://localhost:8000"
	@echo "    make web      ->  http://localhost:5173"
	@echo ""
	@echo "The Accuracy page is empty until you run:  make analysis"

demo: quick analysis
	@echo ""
	@echo "Build complete, including the accuracy analysis."
	@echo "    make api      ->  http://localhost:8000"
	@echo "    make web      ->  http://localhost:5173"

## analysis: the two slow stages that only produce reports.
analysis: backtest regime-test

setup:             ## create the venv and install Python + npm dependencies
	test -d .venv || python3 -m venv .venv
	$(PIP) install -q -r requirements.txt
	cd web && npm install --silent

ingest:            ## raw CSV -> DuckDB catalogue          (~10s)
	$(PY) -m tixtime.catalog.ingest

simulate:          ## generate the price history the models learn from  (~40s)
	$(PY) -m tixtime.pricing.generate

train:             ## fit the quantile + decision heads    (~3 min)
	$(PY) -m tixtime.ml.train

precompute:        ## build the deal board and sparkline cache  (~45s)
	$(PY) -m tixtime.ml.precompute

backtest:          ## score the policy against baselines on unseen events  (~2 min)
	$(PY) -m tixtime.ml.backtest

regime-test:       ## does the model generalise, or did it memorise the simulator?  (~2 min)
	$(PY) -m tixtime.ml.regime_test

api:               ## serve the API on :8000
	./.venv/bin/uvicorn tixtime.api.main:app --reload --port 8000

web:               ## serve the UI on :5173 (proxies /api to :8000)
	cd web && npm run dev

test:
	$(PY) -m pytest tests/ -q

clean:             ## drop every generated artefact; the raw CSV is untouched
	rm -f data/tixtime.duckdb data/tixtime.duckdb.wal
	rm -rf data/artifacts data/reports web/dist
