#!/usr/bin/env bash
# Football DC Bot v3 — Linux env (ne dira druge botove na serveru)
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

echo "[1/3] Kalibracija + ingest + pickovi..."
chmod +x scripts/server/*.sh
./scripts/server/startup_ingest.sh

echo "[2/3] Pokrecem scheduler + Telegram u pozadini..."
nohup python -m app.services.scheduler >> logs/scheduler.log 2>&1 &
echo $! > logs/scheduler.pid
nohup python -m app.telegram.run_bot >> logs/telegram.log 2>&1 &
echo $! > logs/telegram.pid

echo "Bot pokrenut u $ROOT"
echo "  Scheduler PID: $(cat logs/scheduler.pid)"
echo "  Telegram  PID: $(cat logs/telegram.pid)"
echo "  Logovi: logs/scheduler.log, logs/telegram.log"
