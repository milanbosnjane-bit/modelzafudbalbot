#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
source venv/bin/activate

export LOCAL_MODE=true
export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///./data/football_roi.db}"
export DATABASE_URL_SYNC="${DATABASE_URL_SYNC:-sqlite:///./data/football_roi.db}"
export PYTHONUTF8=1

exec python -m app.services.scheduler
