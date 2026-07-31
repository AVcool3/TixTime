# TixTime — one-command demo path.
#
#   make demo    build everything from the raw CSV, then start API + UI
#
# Stages are ordered by dependency and each is runnable on its own.

PY := ./.venv/bin/python
PIP := ./.venv/bin/pip

.PHONY: demo setup ingest simulate train precompute backtest api web test clean

demo: setup ingest simulate train precompute backtest
	@echo ""
	@echo "Build complete. Start the two servers in separate terminals:"
	@echo "    make api"
	@echo "    make web"

setup:
	test -d .venv || python3 -m venv .venv
	$(PIP) install -q -r requirements.txt
	cd web && npm install --silent

ingest:            ## raw CSV -> DuckDB catalogue
	$(PY) -m tixtime.catalog.ingest

simulate:          ## generate the price history the models learn from
	$(PY) -m tixtime.pricing.generate

train:             ## fit the quantile + decision heads
	$(PY) -m tixtime.ml.train

precompute:        ## build the deal board and sparkline cache
	$(PY) -m tixtime.ml.precompute

backtest:          ## score the policy against baselines on unseen events
	$(PY) -m tixtime.ml.backtest

api:
	./.venv/bin/uvicorn tixtime.api.main:app --reload --port 8000

web:
	cd web && npm run dev

test:
	$(PY) -m pytest tests/ -q

clean:
	rm -f data/tixtime.duckdb data/tixtime.duckdb.wal
	rm -rf data/artifacts data/reports web/dist
