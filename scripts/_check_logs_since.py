#!/usr/bin/env python3
"""Check for service errors after a given time (default: last 20 min). Read-only."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")

SERVICES = ("football-dc-scheduler", "football-dc-telegram", "football-dc-api")
ERROR_PATTERN = (
    "Traceback|AttributeError|TypeError|ValueError|KeyError|ImportError|OperationalError|"
    "raised an exception|Task exception|CancelledError|ERROR|CRITICAL|"
    "timed out|SIGKILL|Failed with result"
)


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def main() -> int:
    since = sys.argv[1] if len(sys.argv) > 1 else "20 min ago"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    safe_print(f"=== Greske od: {since} ===")
    clean = True
    for svc in SERVICES:
        cmd = (
            f"journalctl --user -u {svc} --since '{since}' --no-pager 2>&1 | "
            f"grep -E '{ERROR_PATTERN}' | tail -15"
        )
        _, stdout, _ = client.exec_command(cmd, timeout=120)
        out = stdout.read().decode("utf-8", errors="replace").strip()
        if out:
            clean = False
            safe_print(f"\n[{svc}]")
            safe_print(out)
        else:
            safe_print(f"  {svc:26s} CISTO")

    safe_print("\n=== Aktivnost (od tada) ===")
    for svc in SERVICES:
        cmd = f"journalctl --user -u {svc} --since '{since}' --no-pager 2>&1 | wc -l"
        _, stdout, _ = client.exec_command(cmd, timeout=120)
        safe_print(f"  {svc:26s} {stdout.read().decode().strip()} log linija")

    client.close()
    safe_print("\nZAKLJUCAK: " + ("nema gresaka" if clean else "ima gresaka, vidi gore"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
