#!/usr/bin/env python3
"""Start Football ROI FastAPI on port 8001 (does NOT touch PrelaziBot :8000)."""

from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("DEPLOY_HOST", "192.168.1.106")
USER = os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "")
REMOTE = "/home/miki/football-dc-bot"
API_PORT = 8001
TAILSCALE_IP = os.environ.get("TAILSCALE_IP", "100.122.226.3")


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 120) -> str:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return out + (f"\n[stderr] {err}" if err.strip() else "")


def main() -> int:
    if not PASS:
        safe_print("[GRESKA] Postavi DEPLOY_PASS.")
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)
    sftp = client.open_sftp()

    # Ensure launcher script exists
    local_fastapi = os.path.join(os.path.dirname(__file__), "server", "fastapi.sh")
    sftp.put(local_fastapi, f"{REMOTE}/scripts/server/fastapi.sh")
    sftp.close()

    unit = f"""[Unit]
Description=Football DC Bot FastAPI (iOS REST, port {API_PORT})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={REMOTE}
ExecStart={REMOTE}/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port {API_PORT}
Restart=always
RestartSec=15
Environment=PYTHONUTF8=1
Environment=LOCAL_MODE=true
Environment=DATABASE_URL=sqlite+aiosqlite:///./data/football_roi.db
Environment=DATABASE_URL_SYNC=sqlite:///./data/football_roi.db

[Install]
WantedBy=default.target
"""

    setup_cmd = f"""
set -e
cd {REMOTE}
chmod +x scripts/server/fastapi.sh
mkdir -p logs

# Install/update systemd user unit (port {API_PORT} only — never 8000)
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/football-dc-api.service <<'UNIT_EOF'
{unit}
UNIT_EOF

systemctl --user daemon-reload
systemctl --user enable football-dc-api.service
systemctl --user restart football-dc-api.service
sleep 3
systemctl --user is-active football-dc-api.service || (journalctl --user -u football-dc-api -n 30 --no-pager; exit 1)

echo "--- Port listen ---"
ss -tlnp 2>/dev/null | grep ':{API_PORT}' || netstat -tlnp 2>/dev/null | grep ':{API_PORT}' || echo "NOT LISTENING"

echo "--- Local API checks ---"
curl -sf http://127.0.0.1:{API_PORT}/api/v1/health
echo
curl -sf http://127.0.0.1:{API_PORT}/api/v1/status
echo
curl -sf "http://127.0.0.1:{API_PORT}/api/v1/odds/tracker?limit=1" | head -c 200
echo

echo "--- PrelaziBot :8000 still up (unchanged) ---"
curl -sf http://127.0.0.1:8000/api/v1/health | head -c 120
echo
"""
    safe_print("=== Starting football-dc-api on port 8001 ===")
    safe_print(run(client, setup_cmd))
    client.close()

    safe_print(f"\n=== Test from this machine (Tailscale) ===")
    safe_print(f"URL: http://{TAILSCALE_IP}:{API_PORT}/api/v1/health")
    safe_print("Ako iPhone ima Tailscale ukljucen, app URL mora biti:")
    safe_print(f"  http://{TAILSCALE_IP}:{API_PORT}/api/v1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
