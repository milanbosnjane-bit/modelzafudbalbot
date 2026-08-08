#!/usr/bin/env bash
# Football DC Bot — FastAPI (mobile/iOS REST API)
# PORT 8001 — PrelaziBot koristi 8000 i NE SME se dirati.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
mkdir -p logs
exec ./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001
