#!/usr/bin/env python3
"""
Deploy mobile API routes to server (Windows-friendly, uses paramiko).

VAŽNO: PrelaziBot koristi port 8000 — ovaj skript ga NE DIRA.
Football ROI Bot API koristi isključivo port 8001.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "192.168.1.106")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")
REMOTE_API = f"{REMOTE}/app/api"
API_PORT = int(os.environ.get("FOOTBALL_API_PORT", "8001"))


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 120) -> tuple[int, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    return code, out + (f"\n[stderr] {err}" if err.strip() else "")


def main() -> int:
    if not PASS:
        safe_print("[GRESKA] Postavi DEPLOY_PASS u okruzenju.")
        return 1

    files = [
        (ROOT / "app" / "api" / "mobile_routes.py", f"{REMOTE_API}/mobile_routes.py"),
        (ROOT / "app" / "api" / "routes.py", f"{REMOTE_API}/routes.py"),
        (ROOT / "scripts" / "server" / "fastapi.sh", f"{REMOTE}/scripts/server/fastapi.sh"),
    ]
    if os.environ.get("RESTORE_TELEGRAM_BOT") == "1":
        files.append(
            (ROOT / "app" / "telegram" / "stats_service.py", f"{REMOTE}/app/telegram/stats_service.py"),
        )
    for local, _ in files:
        if not local.is_file():
            safe_print(f"[GRESKA] Nedostaje fajl: {local}")
            return 1

    safe_print(f"Deploy mobile API -> {USER}@{HOST} (port {API_PORT}, PrelaziBot :8000 netaknut)")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)
    sftp = client.open_sftp()

    for local, remote in files:
        safe_print(f"  upload {local.name}")
        sftp.put(str(local), remote)

    sftp.close()

    safe_print(f"Restart Football DC API on port {API_PORT}...")
    restart_cmd = f"""
cd {REMOTE}
chmod +x scripts/server/fastapi.sh

# Samo football-dc-api (:8001). NIKAD port 8000 (PrelaziBot).
if systemctl --user is-active football-dc-api.service >/dev/null 2>&1; then
  systemctl --user restart football-dc-api.service
  echo "Restarted football-dc-api.service"
else
  pkill -u $(whoami) -f '{REMOTE}/venv/bin/uvicorn app.main:app.*--port {API_PORT}' 2>/dev/null || true
  sleep 1
  mkdir -p logs
  nohup venv/bin/uvicorn app.main:app --host 0.0.0.0 --port {API_PORT} >> logs/fastapi.log 2>&1 &
  echo "Started football-dc uvicorn on :{API_PORT}"
fi
sleep 2
curl -sf http://127.0.0.1:{API_PORT}/api/v1/health || echo "health check failed"
curl -sf http://127.0.0.1:{API_PORT}/api/v1/status | head -c 300 || echo "status check failed"
"""
    if os.environ.get("RESTORE_TELEGRAM_BOT") == "1":
        restart_cmd += """
if systemctl --user is-active football-dc-telegram.service >/dev/null 2>&1; then
  systemctl --user restart football-dc-telegram.service
  echo "Restored + restarted football-dc-telegram.service"
fi
"""
    code, out = run(client, restart_cmd)
    safe_print(out)
    client.close()

    if code != 0:
        safe_print(f"[UPOZORENJE] Remote restart exit code {code}")

    safe_print("Mobile API deploy zavrsen.")
    safe_print(f"iOS Base URL: http://{HOST}:{API_PORT}/api/v1")
    safe_print(f"Test: curl http://{HOST}:{API_PORT}/api/v1/health")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
