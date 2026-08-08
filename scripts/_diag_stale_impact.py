#!/usr/bin/env python3
"""Median decision odds with and without pre-fix rows, for over_under 2.5. Read-only."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")
CUTOFF = os.environ.get("FIX_CUTOFF", "2026-08-08 15:29:00")

SCRIPT = f"CUTOFF = {CUTOFF!r}\n" + r"""
import sqlite3
from statistics import median

conn = sqlite3.connect("file:./data/football_roi.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

rows = conn.execute(f'''
    SELECT o.fixture_id, o.bookmaker, o.selection, o.current_odds, o.captured_at
    FROM odds_snapshots o
    JOIN fixtures f ON f.id = o.fixture_id
    WHERE f.status='NS' AND f.fixture_date >= datetime('now')
      AND o.market='over_under' AND o.line=2.5
      AND o.selection IN ('Over 2.5','Under 2.5','over 2.5','under 2.5')
''').fetchall()

# Mirror _group_odds_snapshots: newest row wins per (bookmaker, selection).
latest = {}
for r in rows:
    key = (r["fixture_id"], r["bookmaker"], r["selection"].title())
    if key not in latest or r["captured_at"] > latest[key]["captured_at"]:
        latest[key] = r

groups = {}
for (fid, bm, sel), r in latest.items():
    groups.setdefault((fid, sel), []).append((r["current_odds"], r["captured_at"] >= CUTOFF))

print(f"{'mec':>9s} {'selekcija':12s} {'sada':>7s} {'samo_svezi':>11s} {'razlika':>8s}  stari_bukiji")
changed = 0
for (fid, sel), vals in sorted(groups.items()):
    all_odds = [o for o, _ in vals]
    fresh = [o for o, is_fresh in vals if is_fresh]
    if not fresh or len(fresh) == len(all_odds):
        continue
    now_med = median(all_odds)
    fresh_med = median(fresh)
    diff = fresh_med - now_med
    stale_n = len(all_odds) - len(fresh)
    flag = "" if abs(diff) < 0.005 else "  <-- MENJA"
    if abs(diff) >= 0.005:
        changed += 1
    print(f"{fid:>9d} {sel:12s} {now_med:7.2f} {fresh_med:11.2f} {diff:+8.2f}  {stale_n}{flag}")

print(f"\nkombinacija gde stari redovi menjaju medijanu: {changed}")
conn.close()
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_diag_stale_impact.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && venv/bin/python /tmp/_diag_stale_impact.py 2>&1", timeout=600
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
