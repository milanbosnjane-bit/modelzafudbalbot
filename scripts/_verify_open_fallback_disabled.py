#!/usr/bin/env python3
"""Verify MAX_OPEN_FIXTURES=0 is actually loaded by the running venv's settings."""

from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("DEPLOY_HOST", "192.168.1.106")
USER = os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "")
REMOTE = "/home/miki/football-dc-bot"


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 60) -> str:
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

    cmd = (
        f"cd {REMOTE} && venv/bin/python -c "
        "\"from app.config import get_settings; s = get_settings(); "
        "print('max_open_fixtures =', s.max_open_fixtures); "
        "print('league_ids =', s.league_ids); "
        "print('min_ev_threshold =', s.min_ev_threshold); "
        "print('min_confidence_threshold =', s.min_confidence_threshold)\""
    )
    safe_print("=== Effective settings (server venv) ===")
    safe_print(run(client, cmd))

    safe_print("=== football-dc-scheduler + telegram service status ===")
    safe_print(run(client, "systemctl --user is-active football-dc-scheduler football-dc-telegram"))

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
