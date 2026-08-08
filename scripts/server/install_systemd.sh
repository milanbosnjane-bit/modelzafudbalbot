#!/usr/bin/env bash
# Instalira systemd user servise + autostart posle reboota (linger)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
USER_NAME="$(whoami)"
UNIT_DIR="$HOME/.config/systemd/user"

mkdir -p "$UNIT_DIR" "$ROOT/logs" "$ROOT/data/models" "$ROOT/data/features"
chmod +x "$ROOT/scripts/server/"*.sh

cat > "$UNIT_DIR/football-dc-startup.service" <<EOF
[Unit]
Description=Football DC Bot — startup ingest + picks
After=network-online.target
Wants=network-online.target
Before=football-dc-scheduler.service football-dc-telegram.service

[Service]
Type=oneshot
WorkingDirectory=$ROOT
ExecStart=$ROOT/scripts/server/startup_ingest.sh
RemainAfterExit=yes
Environment=PYTHONUTF8=1
Environment=LOCAL_MODE=true
Environment=DATABASE_URL=sqlite+aiosqlite:///./data/football_roi.db
Environment=DATABASE_URL_SYNC=sqlite:///./data/football_roi.db

[Install]
WantedBy=default.target
EOF

cat > "$UNIT_DIR/football-dc-scheduler.service" <<EOF
[Unit]
Description=Football DC Bot Scheduler
After=network-online.target football-dc-startup.service
Wants=network-online.target football-dc-startup.service

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$ROOT/scripts/server/scheduler.sh
Restart=always
RestartSec=15
Environment=PYTHONUTF8=1
Environment=LOCAL_MODE=true
Environment=DATABASE_URL=sqlite+aiosqlite:///./data/football_roi.db
Environment=DATABASE_URL_SYNC=sqlite:///./data/football_roi.db

[Install]
WantedBy=default.target
EOF

cat > "$UNIT_DIR/football-dc-telegram.service" <<EOF
[Unit]
Description=Football DC Bot Telegram Menu
After=network-online.target football-dc-startup.service football-dc-scheduler.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$ROOT/scripts/server/telegram.sh
Restart=always
RestartSec=15
Environment=PYTHONUTF8=1
Environment=LOCAL_MODE=true
Environment=DATABASE_URL=sqlite+aiosqlite:///./data/football_roi.db
Environment=DATABASE_URL_SYNC=sqlite:///./data/football_roi.db

[Install]
WantedBy=default.target
EOF

# FastAPI za iOS app — PORT 8001 (PrelaziBot koristi 8000, ne dirati)
cat > "$UNIT_DIR/football-dc-api.service" <<EOF
[Unit]
Description=Football DC Bot FastAPI (iOS REST, port 8001)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$ROOT/scripts/server/fastapi.sh
Restart=always
RestartSec=15
Environment=PYTHONUTF8=1
Environment=LOCAL_MODE=true
Environment=DATABASE_URL=sqlite+aiosqlite:///./data/football_roi.db
Environment=DATABASE_URL_SYNC=sqlite:///./data/football_roi.db

[Install]
WantedBy=default.target
EOF

# Omoguci user servise posle reboota (bez potrebe za login-om)
if command -v loginctl >/dev/null 2>&1; then
  sudo loginctl enable-linger "$USER_NAME" 2>/dev/null || loginctl enable-linger "$USER_NAME" 2>/dev/null || true
fi

systemctl --user daemon-reload
systemctl --user enable football-dc-startup.service football-dc-scheduler.service football-dc-telegram.service football-dc-api.service
"$ROOT/scripts/server/restart_bot.sh"

echo "Systemd servisi instalirani za korisnika $USER_NAME:"
echo "  Linger: $(loginctl show-user "$USER_NAME" -p Linger 2>/dev/null || echo 'n/a')"
systemctl --user is-enabled football-dc-scheduler.service football-dc-telegram.service football-dc-api.service
systemctl --user status football-dc-scheduler.service --no-pager -l | head -5
systemctl --user status football-dc-telegram.service --no-pager -l | head -5
systemctl --user status football-dc-api.service --no-pager -l | head -5
echo ""
echo "FastAPI (iOS): port 8001 — PrelaziBot na 8000 se NE dira."
echo "Komande:"
echo "  systemctl --user status football-dc-scheduler"
echo "  systemctl --user status football-dc-api"
echo "  curl http://127.0.0.1:8001/api/v1/health"
echo ""
echo "Bot ce se automatski pokrenuti posle restarta servera."
