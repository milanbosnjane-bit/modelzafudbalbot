#!/usr/bin/env python3
"""Count exactly what a cleanup would delete, before deleting anything. Read-only."""
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

conn = sqlite3.connect("file:./data/football_roi.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

total = conn.execute("SELECT COUNT(*) n FROM odds_snapshots").fetchone()["n"]
print(f"odds_snapshots ukupno: {total}")

print("\n=== A) Kombinacione selekcije (sadrze '/') u celoj bazi ===")
for r in conn.execute('''
    SELECT market, COUNT(*) n, COUNT(DISTINCT selection) razlicitih
    FROM odds_snapshots WHERE selection LIKE '%/%'
    GROUP BY market ORDER BY n DESC
'''):
    print(f"  {r['market']:14s} redova={r['n']:7d} razlicitih_selekcija={r['razlicitih']}")
a = conn.execute("SELECT COUNT(*) n FROM odds_snapshots WHERE selection LIKE '%/%'").fetchone()["n"]
print(f"  UKUPNO A: {a}")

print("\n  primeri:")
for r in conn.execute('''
    SELECT market, selection, COUNT(*) n FROM odds_snapshots
    WHERE selection LIKE '%/%' GROUP BY market, selection ORDER BY n DESC LIMIT 12
'''):
    print(f"    {r['market']:14s} {r['selection']:22s} {r['n']}")

print("\n=== B) Pre-fix over_under redovi za predstojece NS meceve ===")
b = conn.execute(f'''
    SELECT COUNT(*) n FROM odds_snapshots o
    JOIN fixtures f ON f.id=o.fixture_id
    WHERE f.status='NS' AND f.fixture_date >= datetime('now')
      AND o.market='over_under' AND o.captured_at < '{CUTOFF}'
''').fetchone()["n"]
print(f"  UKUPNO B: {b}")

print("\n=== Presek A i B (da se ne racuna dvaput) ===")
both = conn.execute(f'''
    SELECT COUNT(*) n FROM odds_snapshots o
    JOIN fixtures f ON f.id=o.fixture_id
    WHERE f.status='NS' AND f.fixture_date >= datetime('now')
      AND o.market='over_under' AND o.captured_at < '{CUTOFF}'
      AND o.selection LIKE '%/%'
''').fetchone()["n"]
print(f"  presek: {both}")
print(f"  ZA BRISANJE UKUPNO: {a + b - both}  ({(a + b - both) * 100.0 / total:.1f}% baze)")

print("\n=== Da li ijedan pick koristi selekciju sa '/'? (mora 0) ===")
r = conn.execute("SELECT COUNT(*) n FROM daily_picks WHERE selection LIKE '%/%'").fetchone()
print(f"  daily_picks sa '/' u selekciji: {r['n']}")

print("\n=== Ostale ne-kanonske selekcije (informativno, NE brisem) ===")
for r in conn.execute('''
    SELECT market, selection, COUNT(*) n FROM odds_snapshots
    WHERE selection NOT LIKE '%/%' AND (
       (market='match_winner' AND lower(selection) NOT IN ('home','draw','away'))
    OR (market='btts' AND lower(selection) NOT IN ('yes','no'))
    OR (market='over_under' AND lower(selection) NOT LIKE 'over %' AND lower(selection) NOT LIKE 'under %')
    )
    GROUP BY market, selection ORDER BY n DESC LIMIT 15
'''):
    print(f"  {r['market']:14s} {r['selection']:22s} {r['n']}")

conn.close()
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_diag_cleanup_scope.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && venv/bin/python /tmp/_diag_cleanup_scope.py 2>&1", timeout=900
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
