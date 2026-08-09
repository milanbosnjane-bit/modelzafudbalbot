#!/usr/bin/env python3
"""Verify warning_sent column and that the warning job imports cleanly. Read-only after deploy."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")

SCRIPT = r"""
import sqlite3
from app.config import get_settings
from app.services.scheduler import create_scheduler
from app.services.odds_warning import OddsWarningService, odds_jump_pct

conn = sqlite3.connect("file:./data/football_roi.db?mode=ro", uri=True)
cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_picks)")]
print("warning_sent in daily_picks:", "warning_sent" in cols)
if "warning_sent" in cols:
    n = conn.execute("SELECT COUNT(*) FROM daily_picks WHERE warning_sent = 1").fetchone()[0]
    print("warning_sent=1 count:", n)
conn.close()

s = get_settings()
print("pre_kickoff_adverse_jump_pct:", s.pre_kickoff_adverse_jump_pct)
print("jump 2.10->2.17:", round(odds_jump_pct(2.10, 2.17), 2))

jobs = {j.id for j in create_scheduler().get_jobs()}
print("job registered:", "pre_kickoff_odds_warnings" in jobs)
print("OddsWarningService ok:", OddsWarningService is not None)
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)
    sftp = client.open_sftp()
    with sftp.open("/tmp/_verify_warn.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()
    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && PYTHONPATH={REMOTE} PYTHONUTF8=1 LOCAL_MODE=true "
        f"venv/bin/python /tmp/_verify_warn.py 2>&1",
        timeout=120,
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    # also check recent job log
    _, stdout, _ = client.exec_command(
        "journalctl --user -u football-dc-scheduler --since '2 minutes ago' --no-pager "
        "| grep -iE 'pre_kickoff|scheduler_started|error' | tail -20",
        timeout=60,
    )
    print("\n=== recent scheduler log ===")
    print(stdout.read().decode("utf-8", errors="replace").strip() or "(nema jos warning logova)")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
