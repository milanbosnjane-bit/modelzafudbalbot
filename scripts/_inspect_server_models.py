#!/usr/bin/env python3
"""Inspect server DailyPick columns and SelectedPick fields (read-only)."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")

SCRIPT = r"""
import dataclasses, sqlite3
from app.database.models import DailyPick
from app.predictions.pick_selector import SelectedPick

cols = sorted(c.name for c in DailyPick.__table__.columns)
print("DAILYPICK_MODEL_COLS:", ",".join(cols))

fields = sorted(f.name for f in dataclasses.fields(SelectedPick))
print("SELECTEDPICK_FIELDS:", ",".join(fields))

conn = sqlite3.connect("file:./data/football_roi.db?mode=ro", uri=True)
db_cols = sorted(r[1] for r in conn.execute("PRAGMA table_info(daily_picks)"))
conn.close()
print("DAILYPICK_DB_COLS:", ",".join(db_cols))

for name in ("calibrated_confidence", "calibrated_ev", "status"):
    print(f"HAS_MODEL_{name}:", name in cols)
    print(f"HAS_DB_{name}:", name in db_cols)
    print(f"HAS_SELECTEDPICK_{name}:", name in fields)
"""


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_inspect_models.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    cmd = f"cd {REMOTE} && PYTHONPATH={REMOTE} venv/bin/python /tmp/_inspect_models.py 2>&1"
    _, stdout, _ = client.exec_command(cmd, timeout=120)
    safe_print(stdout.read().decode("utf-8", errors="replace"))

    safe_print("=== git stanje na serveru ===")
    _, stdout, _ = client.exec_command(
        f"cd {REMOTE} && (git log --oneline -3 2>&1 || echo 'nije git repo')", timeout=60
    )
    safe_print(stdout.read().decode("utf-8", errors="replace"))

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
