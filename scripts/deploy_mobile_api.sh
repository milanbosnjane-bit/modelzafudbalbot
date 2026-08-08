#!/usr/bin/env bash
# deploy_mobile_api.sh — Sinhronizacija mobile API ruta na server preko Tailscale-a
#
# VAŽNO: PrelaziBot radi na portu 8000 — ovaj skript ga NE DIRA.
# Football ROI Bot API koristi isključivo port 8001.
#
# Upotreba:
#   export SERVER_IP="100.64.0.1"
#   export SERVER_USER="miki"
#   bash scripts/deploy_mobile_api.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER_USER="${SERVER_USER:-miki}"
SERVER_IP="${SERVER_IP:-100.x.x.x}"
REMOTE_PATH="${REMOTE_PATH:-/home/miki/football-dc-bot}"
REMOTE_API="${REMOTE_PATH}/app/api"
API_PORT="${FOOTBALL_API_PORT:-8001}"

echo "🚀 Sinhronizujem mobile API rute (port ${API_PORT}, PrelaziBot/8000 netaknut)..."
echo "   Server: ${SERVER_USER}@${SERVER_IP}"
echo "   Target: ${REMOTE_API}/"

scp "${ROOT}/app/api/mobile_routes.py" "${SERVER_USER}@${SERVER_IP}:${REMOTE_API}/"
scp "${ROOT}/app/api/routes.py" "${SERVER_USER}@${SERVER_IP}:${REMOTE_API}/"
scp "${ROOT}/scripts/server/fastapi.sh" "${SERVER_USER}@${SERVER_IP}:${REMOTE_PATH}/scripts/server/"

echo "🔄 Ponovno pokretanje Football DC API na portu ${API_PORT}..."
ssh "${SERVER_USER}@${SERVER_IP}" bash -s <<EOF
set -e
cd "${REMOTE_PATH}"
chmod +x scripts/server/fastapi.sh

# Samo football-dc-api servis ili uvicorn na ${API_PORT} iz ovog foldera.
# NIKAD ne dirati port 8000 (PrelaziBot).
if systemctl --user is-active football-dc-api.service >/dev/null 2>&1; then
  systemctl --user restart football-dc-api.service
  echo "   Restarted: football-dc-api.service (:${API_PORT})"
else
  pkill -u "\$(whoami)" -f "${REMOTE_PATH}/venv/bin/uvicorn app.main:app.*--port ${API_PORT}" 2>/dev/null || true
  sleep 1
  mkdir -p logs
  nohup venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${API_PORT} >> logs/fastapi.log 2>&1 &
  echo "   Started: football-dc uvicorn on :${API_PORT}"
fi

sleep 2
curl -sf "http://127.0.0.1:${API_PORT}/api/v1/health" && echo ""
curl -sf "http://127.0.0.1:${API_PORT}/api/v1/status" | head -c 200 && echo ""
EOF

echo "✅ Mobile API lansiran na portu ${API_PORT} (PrelaziBot :8000 netaknut)."
echo ""
echo "Test sa iPhone-a (Tailscale):"
echo "  curl http://${SERVER_IP}:${API_PORT}/api/v1/health"
echo "  curl http://${SERVER_IP}:${API_PORT}/api/v1/odds/tracker"
echo ""
echo "iOS app Base URL:"
echo "  http://${SERVER_IP}:${API_PORT}/api/v1"
