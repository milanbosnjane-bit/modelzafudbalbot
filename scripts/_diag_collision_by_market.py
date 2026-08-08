#!/usr/bin/env python3
"""Per-market breakdown of odds snapshot collisions, incl. pick-eligible selections. Read-only."""
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
LAST = "(SELECT MAX(captured_at) FROM odds_snapshots)"

print("=== Kolizije po marketu (poslednji batch) ===")
for r in conn.execute(f'''
    SELECT market,
           COUNT(*) grupa,
           SUM(n) redova,
           MAX(n) max_po_grupi,
           MAX(spread) max_spread
    FROM (
      SELECT market, COUNT(*) n,
             MAX(current_odds) - MIN(current_odds) spread
      FROM odds_snapshots
      WHERE captured_at = {LAST}
      GROUP BY fixture_id, bookmaker, market, selection, line
      HAVING n > 1
    )
    GROUP BY market ORDER BY redova DESC
'''):
    print(f"  {r['market']:16s} grupa={r['grupa']:4d} redova={r['redova']:5d} "
          f"max_u_grupi={r['max_po_grupi']:2d} max_raspon_kvota={r['max_spread']:.2f}")

print()
print("=== Kolizije SAMO na selekcijama koje ulaze u pickove ===")
print("    (match_winner: Home/Draw/Away | over_under: Over/Under 2.5 | btts: Yes)")
for r in conn.execute(f'''
    SELECT market, selection, line, n, min_odds, max_odds FROM (
      SELECT market, selection, line, COUNT(*) n,
             MIN(current_odds) min_odds, MAX(current_odds) max_odds
      FROM odds_snapshots
      WHERE captured_at = {LAST}
        AND (
          (market='match_winner' AND lower(selection) IN ('home','draw','away'))
          OR (market='over_under' AND line=2.5 AND selection IN ('Over 2.5','Under 2.5'))
          OR (market='btts' AND lower(selection)='yes')
        )
      GROUP BY fixture_id, bookmaker, market, selection, line
      HAVING n > 1
    ) ORDER BY n DESC LIMIT 15
'''):
    print(f"  {r['market']:14s} {r['selection']:10s} line={str(r['line']):5s} "
          f"puta={r['n']}  kvote {r['min_odds']:.2f}..{r['max_odds']:.2f}")

r = conn.execute(f'''
    SELECT COUNT(*) grupa, SUM(n) redova FROM (
      SELECT COUNT(*) n FROM odds_snapshots
      WHERE captured_at = {LAST}
        AND (
          (market='match_winner' AND lower(selection) IN ('home','draw','away'))
          OR (market='over_under' AND line=2.5 AND selection IN ('Over 2.5','Under 2.5'))
          OR (market='btts' AND lower(selection)='yes')
        )
      GROUP BY fixture_id, bookmaker, market, selection, line
      HAVING n > 1
    )
''').fetchone()
print(f"\n  UKUPNO pick-relevantnih kolizija: grupa={r['grupa']}, redova={r['redova']}")

print()
print("=== Da li su danasnji pickovi pogodjeni? ===")
for r in conn.execute(f'''
    SELECT p.id, p.market, p.selection, p.line, p.odds,
           (SELECT COUNT(*) FROM odds_snapshots o
             WHERE o.fixture_id=p.fixture_id AND o.market=p.market
               AND o.selection=p.selection AND o.captured_at = {LAST}) snaps,
           (SELECT COUNT(DISTINCT o.current_odds) FROM odds_snapshots o
             WHERE o.fixture_id=p.fixture_id AND o.market=p.market
               AND o.selection=p.selection AND o.captured_at = {LAST}) distinct_odds,
           (SELECT MIN(o.current_odds) FROM odds_snapshots o
             WHERE o.fixture_id=p.fixture_id AND o.market=p.market
               AND o.selection=p.selection AND o.captured_at = {LAST}) lo,
           (SELECT MAX(o.current_odds) FROM odds_snapshots o
             WHERE o.fixture_id=p.fixture_id AND o.market=p.market
               AND o.selection=p.selection AND o.captured_at = {LAST}) hi
    FROM daily_picks p
    WHERE date(p.pick_date) = date('now')
      AND (p.outcome IS NULL OR p.outcome='' OR lower(p.outcome)='pending')
    ORDER BY p.pick_date, p.rank
'''):
    flag = ""
    if r["snaps"] and r["hi"] and r["lo"] and r["hi"] > r["lo"] * 1.5:
        flag = "  <-- SIROK RASPON"
    print(f"  id={r['id']:4d} {r['market']:14s} {str(r['selection']):12s} "
          f"pick_kvota={r['odds']:.2f} snapshotova={r['snaps']:3d} "
          f"kvote={r['lo'] if r['lo'] else 0:.2f}..{r['hi'] if r['hi'] else 0:.2f}{flag}")

conn.close()
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_diag_collision_market.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && venv/bin/python /tmp/_diag_collision_market.py 2>&1", timeout=300
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
