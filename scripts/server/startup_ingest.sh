#!/usr/bin/env bash
# Ingest + pickovi + Telegram odmah (posle restarta ili boot-a)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

export LOCAL_MODE=true
export USE_MEMORY_CACHE=true
export APP_DEBUG=false
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export POISSON_ONLY_MODE=true
export PAPER_TRADING_ENABLED=true
export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///./data/football_roi.db}"
export DATABASE_URL_SYNC="${DATABASE_URL_SYNC:-sqlite:///./data/football_roi.db}"

mkdir -p data/models data/features logs
source venv/bin/activate

echo "[startup] Kalibracija DC (ako treba)..."
python -m app.calibrate_models --if-missing || echo "[WARN] Kalibracija preskocena"

echo "[startup] Ingest podataka (bez pickova — ceka scheduler)..."
python -m app.run_local --ingest-only
