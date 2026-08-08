#!/usr/bin/env python3
"""Show the scheduler's most recent shutdown/startup log window (read-only)."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    cmd = (
        "journalctl --user -u football-dc-scheduler --no-pager -n 80 2>&1 | "
        "grep -E 'shutdown_signal|scheduler_stopped|scheduler_started|Stopping|Stopped|"
        "Started|Traceback|raised an exception|ERROR|Task exception|CancelledError|timed out'"
    )
    _, stdout, _ = client.exec_command(cmd, timeout=120)
    sys.stdout.buffer.write(stdout.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
