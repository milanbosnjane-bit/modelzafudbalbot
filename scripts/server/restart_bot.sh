#!/usr/bin/env bash
# Restart bota: prvo povuci lige + pickovi, pa podigni servise
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

chmod +x "$ROOT/scripts/server/"*.sh

echo "[restart] Ingest + pickovi..."
systemctl --user stop football-dc-startup.service 2>/dev/null || true
systemctl --user start football-dc-startup.service

echo "[restart] Restart systemd servisa..."
systemctl --user restart football-dc-scheduler.service football-dc-telegram.service

sleep 2
systemctl --user is-active football-dc-scheduler.service football-dc-telegram.service
echo "[restart] Bot aktivan."
