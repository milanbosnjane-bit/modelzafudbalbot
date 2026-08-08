#!/usr/bin/env python3
"""Inspect pick-relevant keys that no fresh ingest refreshed — likely phantom rows. Read-only."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")
CUTOFF = os.environ.get("FIX_CUTOFF", "2026-08-08 15:29:00")

PICK_FILTER = (
    "((o.market='match_winner' AND lower(o.selection) IN ('home','draw','away','1','2','x'))"
    " OR (o.market='over_under' AND o.line=2.5)"
    " OR (o.market='btts' AND lower(o.selection) IN ('yes','no')))"
)

SCRIPT = f"CUTOFF = {CUTOFF!r}\nPICK_FILTER = {PICK_FILTER!r}\n" + r"""
import sqlite3

conn = sqlite3.connect("file:./data/football_roi.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

STALE = f'''
    SELECT o.fixture_id, o.bookmaker, o.market, o.selection, o.line,
           MAX(o.captured_at) last_seen
    FROM odds_snapshots o
    JOIN fixtures f ON f.id = o.fixture_id
    WHERE f.status='NS' AND f.fixture_date >= datetime('now') AND {PICK_FILTER}
    GROUP BY o.fixture_id, o.bookmaker, o.market, o.selection, o.line
    HAVING last_seen < '{CUTOFF}'
'''

print("=== Zaostali kljucevi po marketu i selekciji ===")
for r in conn.execute(f"SELECT market, selection, line, COUNT(*) n FROM ({STALE}) "
                      f"GROUP BY market, selection, line ORDER BY n DESC"):
    print(f"  {r['market']:14s} {r['selection']:12s} line={str(r['line']):5s} kljuceva={r['n']}")

print("\n=== Zaostali kljucevi po bookmakeru (top 15) ===")
for r in conn.execute(f"SELECT bookmaker, COUNT(*) n FROM ({STALE}) "
                      f"GROUP BY bookmaker ORDER BY n DESC LIMIT 15"):
    print(f"  {r['bookmaker']:22s} kljuceva={r['n']}")

print("\n=== Da li ti bookmakeri imaju SVEZE redove za isti mec/market? ===")
rows = conn.execute(f'''
    SELECT s.fixture_id, s.bookmaker, s.market, s.selection,
           (SELECT COUNT(*) FROM odds_snapshots x
             WHERE x.fixture_id=s.fixture_id AND x.bookmaker=s.bookmaker
               AND x.market=s.market AND x.captured_at >= '{CUTOFF}') svezih
    FROM ({STALE}) s LIMIT 400
''').fetchall()
imaju = sum(1 for r in rows if r["svezih"] > 0)
print(f"  uzorak: {len(rows)} kljuceva")
print(f"  bookmaker JESTE osvezen za taj mec/market: {imaju}")
print(f"  bookmaker NIJE osvezen (fantom - nudio samo strani market): {len(rows) - imaju}")

print("\n=== Primeri fantom kljuceva ===")
for r in rows:
    if r["svezih"] == 0:
        print(f"  {r['fixture_id']} {r['bookmaker']:20s} {r['market']:14s} {r['selection']}")
        if r is rows[-1]:
            break
conn.close()
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_diag_phantom.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && venv/bin/python /tmp/_diag_phantom.py 2>&1", timeout=600
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
