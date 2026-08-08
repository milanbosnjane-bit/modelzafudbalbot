#!/usr/bin/env python3
"""Detect odds snapshots that collide on (bookmaker, market, selection, line). Read-only."""
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

conn = sqlite3.connect("file:./data/football_roi.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

print("=== Duplikati u istom captured_at batch-u (isti bookmaker/market/selection/line) ===")
rows = conn.execute('''
    SELECT market, selection, line, COUNT(*) n,
           MIN(current_odds) min_odds, MAX(current_odds) max_odds,
           COUNT(DISTINCT current_odds) distinct_odds
    FROM odds_snapshots
    WHERE captured_at = (SELECT MAX(captured_at) FROM odds_snapshots)
    GROUP BY fixture_id, bookmaker, market, selection, line
    HAVING n > 1
    ORDER BY n DESC
    LIMIT 15
''').fetchall()
if not rows:
    print("  (nema duplikata)")
for r in rows:
    print(f"  {r['market']:14s} {str(r['selection']):12s} line={str(r['line']):5s} "
          f"puta={r['n']}  kvote {r['min_odds']:.2f}..{r['max_odds']:.2f} "
          f"(razlicitih={r['distinct_odds']})")

print()
print("=== Koliko grupa ima koliziju (poslednji batch) ===")
r = conn.execute('''
    SELECT COUNT(*) groups, SUM(n) rows FROM (
      SELECT COUNT(*) n FROM odds_snapshots
      WHERE captured_at = (SELECT MAX(captured_at) FROM odds_snapshots)
      GROUP BY fixture_id, bookmaker, market, selection, line
      HAVING n > 1
    )
''').fetchone()
print(f"  grupa sa kolizijom: {r['groups']}, redova: {r['rows']}")

print()
print("=== Raspon kvota za over_under 2.5 (jedan fixture, poslednji batch) ===")
fx = conn.execute('''
    SELECT fixture_id FROM odds_snapshots
    WHERE captured_at = (SELECT MAX(captured_at) FROM odds_snapshots)
      AND market='over_under' AND line=2.5
    LIMIT 1
''').fetchone()
if fx:
    for r in conn.execute('''
        SELECT bookmaker, selection, current_odds FROM odds_snapshots
        WHERE captured_at = (SELECT MAX(captured_at) FROM odds_snapshots)
          AND fixture_id=? AND market='over_under' AND line=2.5
        ORDER BY selection, current_odds
        LIMIT 20
    ''', (fx["fixture_id"],)):
        print(f"  {r['bookmaker']:18s} {r['selection']:12s} {r['current_odds']:.2f}")

print()
print("=== Marketi u bazi (poslednji batch) ===")
for r in conn.execute('''
    SELECT market, COUNT(*) n FROM odds_snapshots
    WHERE captured_at = (SELECT MAX(captured_at) FROM odds_snapshots)
    GROUP BY market ORDER BY n DESC
'''):
    print(f"  {r['market']:16s} {r['n']}")

conn.close()
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_diag_collision.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && venv/bin/python /tmp/_diag_collision.py 2>&1", timeout=300
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
