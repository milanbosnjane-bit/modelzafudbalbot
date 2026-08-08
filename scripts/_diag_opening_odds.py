#!/usr/bin/env python3
"""Confirm opening odds / line movement survived the cleanup. Read-only."""
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

print("=== opening_odds na predstojecim mecevima ===")
r = conn.execute('''
    SELECT COUNT(*) n,
           SUM(CASE WHEN o.opening_odds IS NULL THEN 1 ELSE 0 END) bez_opening,
           SUM(CASE WHEN ABS(o.opening_odds - o.current_odds) > 0.001 THEN 1 ELSE 0 END) sa_kretanjem
    FROM odds_snapshots o JOIN fixtures f ON f.id=o.fixture_id
    WHERE f.status='NS' AND f.fixture_date >= datetime('now')
''').fetchone()
print(f"  redova={r['n']} bez_opening={r['bez_opening']} sa_kretanjem_kvote={r['sa_kretanjem']}")

print("\n=== Primer: kretanje kvote po mecu (match_winner Home) ===")
for r in conn.execute('''
    SELECT o.fixture_id, o.bookmaker, o.opening_odds, o.current_odds, o.odds_change_pct
    FROM odds_snapshots o JOIN fixtures f ON f.id=o.fixture_id
    WHERE f.status='NS' AND f.fixture_date >= datetime('now')
      AND o.market='match_winner' AND o.selection='Home'
      AND ABS(o.opening_odds - o.current_odds) > 0.01
    ORDER BY o.captured_at DESC LIMIT 8
'''):
    print(f"  {r['fixture_id']} {r['bookmaker']:16s} {r['opening_odds']:.2f} -> {r['current_odds']:.2f} "
          f"({r['odds_change_pct']:+.2%})" if r["odds_change_pct"] is not None else
          f"  {r['fixture_id']} {r['bookmaker']:16s} {r['opening_odds']:.2f} -> {r['current_odds']:.2f}")

print("\n=== Marketi koji su ostali u bazi (top 10) ===")
for r in conn.execute('''
    SELECT market, COUNT(*) n FROM odds_snapshots GROUP BY market ORDER BY n DESC LIMIT 10
'''):
    print(f"  {r['market']:16s} {r['n']}")

print("\n=== Settled pickovi i CLV (mora ostati netaknuto) ===")
r = conn.execute('''
    SELECT COUNT(*) n,
           SUM(CASE WHEN clv IS NOT NULL THEN 1 ELSE 0 END) sa_clv,
           SUM(CASE WHEN outcome IN ('won','lost') THEN 1 ELSE 0 END) resenih
    FROM daily_picks
''').fetchone()
print(f"  pickova={r['n']} resenih={r['resenih']} sa_clv={r['sa_clv']}")

conn.close()
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_diag_opening.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && venv/bin/python /tmp/_diag_opening.py 2>&1", timeout=600
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
