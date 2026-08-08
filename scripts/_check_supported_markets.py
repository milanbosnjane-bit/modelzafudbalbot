#!/usr/bin/env python3
"""Check effective supported_markets on the server and whether .env overrides it."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")

SCRIPT = r"""
from app.config import get_settings
from app.predictions.probability_layer import is_disabled_market
from app.predictions.market_selection import is_eligible_selection

s = get_settings()
print("supported_markets:", s.supported_markets)
print("double_chance u supported:", "double_chance" in s.supported_markets)
print("is_disabled_market('double_chance'):", is_disabled_market("double_chance"))
print("is_eligible_selection('double_chance','Home/Draw'):", is_eligible_selection("double_chance", "Home/Draw"))
"""


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    safe_print("=== .env override provera ===")
    _, stdout, _ = client.exec_command(
        f"grep -inE 'supported_markets|market' {REMOTE}/.env 2>/dev/null || echo 'nema market kljuceva u .env'",
        timeout=60,
    )
    safe_print(stdout.read().decode("utf-8", errors="replace").strip())

    sftp = client.open_sftp()
    with sftp.open("/tmp/_check_markets.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    safe_print("\n=== Efektivna konfiguracija (na serveru) ===")
    cmd = (
        f"cd {REMOTE} && PYTHONPATH={REMOTE} PYTHONUTF8=1 LOCAL_MODE=true "
        f"venv/bin/python /tmp/_check_markets.py 2>&1"
    )
    _, stdout, _ = client.exec_command(cmd, timeout=120)
    safe_print(stdout.read().decode("utf-8", errors="replace").strip())

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
