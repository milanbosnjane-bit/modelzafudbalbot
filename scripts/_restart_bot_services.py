#!/usr/bin/env python3
"""
Restart Football DC bot services (telegram, scheduler, api) and report health.

PrelaziBot on port 8000 is never touched — only football-dc-* user services.
Does not regenerate picks: no run_daily, no ingest.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
API_PORT = int(os.environ.get("FOOTBALL_API_PORT", "8001"))

SERVICES = (
    "football-dc-telegram.service",
    "football-dc-scheduler.service",
    "football-dc-api.service",
)


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 120) -> str:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return out + (f"\n[stderr] {err}" if err.strip() else "")


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    safe_print("=== PRE restarta ===")
    safe_print(run(client, f"systemctl --user is-active {' '.join(SERVICES)} 2>&1"))

    safe_print("=== Restart football-dc servisa (PrelaziBot :8000 netaknut) ===")
    safe_print(run(client, f"systemctl --user restart {' '.join(SERVICES)} 2>&1; echo restart_done"))

    safe_print("=== POSLE restarta ===")
    safe_print(run(client, f"sleep 6; systemctl --user is-active {' '.join(SERVICES)} 2>&1"))

    safe_print("=== Portovi (8000 = PrelaziBot, 8001 = Football ROI) ===")
    safe_print(run(client, "ss -ltnp 2>/dev/null | grep -E ':8000|:8001' || echo 'nema 8000/8001'"))

    safe_print("=== Telegram servis — poslednjih 15 linija ===")
    safe_print(run(client, "journalctl --user -u football-dc-telegram --no-pager -n 15 2>&1"))

    safe_print("=== Scheduler servis — poslednjih 15 linija ===")
    safe_print(run(client, "journalctl --user -u football-dc-scheduler --no-pager -n 15 2>&1"))

    safe_print("=== API health/status ===")
    safe_print(run(client, f"curl -sf http://127.0.0.1:{API_PORT}/api/v1/health; echo; curl -sf http://127.0.0.1:{API_PORT}/api/v1/status; echo"))

    client.close()

    safe_print("=== App /picks/today (preko Tailscale) ===")
    try:
        url = f"http://{HOST}:{API_PORT}/api/v1/picks/today"
        with urllib.request.urlopen(url, timeout=20) as resp:
            picks = json.loads(resp.read())
        safe_print(f"pickova: {len(picks)}")
        for p in picks:
            safe_print(f"  #{p['rank']} {p['match']} | {p['market']}/{p['selection']} @ {p['odds']:.2f}")
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        safe_print(f"[GRESKA] API nedostupan: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
