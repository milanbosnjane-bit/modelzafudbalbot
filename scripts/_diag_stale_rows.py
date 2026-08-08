#!/usr/bin/env python3
"""
How much pre-fix odds data can still win the "latest row per key" race.

_group_odds_snapshots keeps the newest row per (bookmaker, market, selection, line),
so a stale pre-fix row only matters when no fresh row replaced that key. Read-only.
"""
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

# Pick-relevant keys only: what _odds_query_filters would actually load.
PICK_FILTER = (
    "((o.market='match_winner' AND lower(o.selection) IN ('home','draw','away','1','2','x'))"
    " OR (o.market='over_under' AND o.line=2.5)"
    " OR (o.market='btts' AND lower(o.selection) IN ('yes','no')))"
)

print(f"granica popravke: {CUTOFF}")

print("\n=== Pick-relevantni ključevi za buduce meceve ===")
r = conn.execute(f'''
    SELECT COUNT(*) kljuceva,
           SUM(CASE WHEN last_seen < ? THEN 1 ELSE 0 END) stari,
           SUM(CASE WHEN last_seen >= ? THEN 1 ELSE 0 END) osvezeni
    FROM (
      SELECT o.fixture_id, o.bookmaker, o.market, o.selection, o.line,
             MAX(o.captured_at) last_seen
      FROM odds_snapshots o
      JOIN fixtures f ON f.id = o.fixture_id
      WHERE f.status = 'NS' AND f.fixture_date >= datetime('now')
        AND {PICK_FILTER}
      GROUP BY o.fixture_id, o.bookmaker, o.market, o.selection, o.line
    )
''', (CUTOFF, CUTOFF)).fetchone()
print(f"  ukupno kljuceva: {r['kljuceva']}")
print(f"  osvezeno posle popravke: {r['osvezeni']}")
print(f"  jos na starim podacima: {r['stari']}")

print("\n=== Od tih starih, koliko ih je bilo u koliziji (dakle sumnjivi) ===")
r = conn.execute(f'''
    SELECT COUNT(*) sumnjivih FROM (
      SELECT o.fixture_id, o.bookmaker, o.market, o.selection, o.line,
             MAX(o.captured_at) last_seen, COUNT(*) n
      FROM odds_snapshots o
      JOIN fixtures f ON f.id = o.fixture_id
      WHERE f.status = 'NS' AND f.fixture_date >= datetime('now')
        AND {PICK_FILTER}
      GROUP BY o.fixture_id, o.bookmaker, o.market, o.selection, o.line, o.captured_at
      HAVING n > 1 AND last_seen < ?
    )
''', (CUTOFF,)).fetchone()
print(f"  sumnjivih kljuceva (stari + imali duplikat): {r['sumnjivih']}")

print("\n=== Koliko buducih meceva je vec potpuno osvezeno ===")
for r in conn.execute(f'''
    SELECT f.id, f.fixture_date,
           SUM(CASE WHEN o.captured_at >= ? THEN 1 ELSE 0 END) novi,
           COUNT(*) ukupno
    FROM odds_snapshots o
    JOIN fixtures f ON f.id = o.fixture_id
    WHERE f.status='NS' AND f.fixture_date >= datetime('now')
      AND {PICK_FILTER}
    GROUP BY f.id ORDER BY f.fixture_date LIMIT 12
''', (CUTOFF,)):
    mark = "osvezen" if r["novi"] else "STARI PODACI"
    print(f"  {r['id']}  {r['fixture_date']}  novih={r['novi']:4d}/{r['ukupno']:4d}  {mark}")

conn.close()
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_diag_stale.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && venv/bin/python /tmp/_diag_stale.py 2>&1", timeout=600
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
