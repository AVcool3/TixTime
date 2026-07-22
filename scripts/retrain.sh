#!/usr/bin/env bash
# Daily ingest + weekly-style retrain. Point cron at this.
#   0 6 * * *  /home/user/TixTime/scripts/retrain.sh /path/to/dataset-root
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATASET_ROOT="${1:-$ROOT/data/drops}"
PY="$ROOT/.venv/bin/python"

echo "[$(date -u +%FT%TZ)] ingesting from $DATASET_ROOT"
"$PY" -m ingestion.ingest auto "$DATASET_ROOT"

echo "[$(date -u +%FT%TZ)] retraining model"
"$PY" -m ml.train --model forest

echo "[$(date -u +%FT%TZ)] done"
