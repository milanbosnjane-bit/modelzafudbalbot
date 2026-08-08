#!/usr/bin/env python3
"""
Set MAX_OPEN_FIXTURES=0 on server .env (disable open-fallback ingestion),
restart scheduler service, verify.

Root cause (data-confirmed 2026-08-07): open-fallback fixtures (leagues
outside LEAGUE_IDS) produced -43% ROI (-14.53u) since 2026-07-28, while
tracked leagues stayed profitable (+17.6% ROI, +5.21u) in the same window.
Tracked LEAGUE_IDS and all other config/pick logic are left untouched.
"""

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

    safe_print("=== BEFORE: MAX_OPEN_FIXTURES in server .env ===")
    safe_print(run(client, f"grep -i MAX_OPEN_FIXTURES {REMOTE}/.env || echo '(not set — default 80 from config.py)'"))

    safe_print("=== Updating .env ===")
    update_cmd = (
        f"cd {REMOTE} && "
        "if grep -qi '^MAX_OPEN_FIXTURES=' .env; then "
        "  sed -i 's/^MAX_OPEN_FIXTURES=.*/MAX_OPEN_FIXTURES=0/i' .env; "
        "else "
        "  printf '\\n# Open fallback iskljucen (2026-08-07) - non-tracked leagues losing -43%% ROI since 7/28\\nMAX_OPEN_FIXTURES=0\\n' >> .env; "
        "fi"
    )
    safe_print(run(client, update_cmd))

    safe_print("=== AFTER: MAX_OPEN_FIXTURES in server .env ===")
    safe_print(run(client, f"grep -i MAX_OPEN_FIXTURES {REMOTE}/.env"))

    safe_print("=== Restarting scheduler service ===")
    safe_print(run(client, "systemctl --user restart football-dc-scheduler"))

    safe_print("=== Service status after restart ===")
    safe_print(run(client, "sleep 3 && systemctl --user is-active football-dc-scheduler football-dc-telegram"))
    safe_print(run(client, "journalctl --user -u football-dc-scheduler --no-pager -n 15"))

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
