#!/usr/bin/env python3
"""Compare app /picks/today ordering and ranks against the persisted batches. Read-only."""
from __future__ import annotations

import json
import os
import sys
import urllib.request

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")
API = f"http://{HOST}:8001/api/v1"

SCRIPT = r"""
import sqlite3

conn = sqlite3.connect("file:./data/football_roi.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

print("=== Danasnji batchevi u daily_picks ===")
for r in conn.execute('''
    SELECT pick_date, COUNT(*) n, MIN(rank) min_r, MAX(rank) max_r
    FROM daily_picks WHERE date(pick_date) = date('now')
    GROUP BY pick_date ORDER BY pick_date
'''):
    print(f"  {r['pick_date']}  pikova={r['n']}  rank {r['min_r']}..{r['max_r']}")

print("\n=== Svi danasnji pickovi (rank kako stoji u bazi) ===")
for r in conn.execute('''
    SELECT id, pick_date, rank, market, selection, odds, expected_value, roi_score, outcome
    FROM daily_picks WHERE date(pick_date) = date('now')
    ORDER BY pick_date, rank
'''):
    out = r["outcome"] or "pending"
    print(f"  id={r['id']:4d} rank={r['rank']:2d} {str(r['pick_date'])[:19]} "
          f"{r['market']:14s} {r['selection']:10s} kvota={r['odds']:.2f} "
          f"EV={r['expected_value']:+.4f} roi={r['roi_score']:.4f} {out}")

conn.close()
"""


def main() -> int:
    print("=== APP /picks/today (kako ga app vidi) ===")
    try:
        data = json.loads(urllib.request.urlopen(f"{API}/picks/today", timeout=30).read())
        print(f"  zapisa: {len(data)}")
        for p in data:
            print(f"  rank={p['rank']:2d} id={p['id']:4d} {p['market']:14s} {p['selection']:10s} "
                  f"EV={p['expected_value']:+.4f} roi={p['roi_score']:.4f}  {p['match'][:38]}")
        ranks = [p["rank"] for p in data]
        print(f"\n  rankovi: {ranks}")
        print(f"  ima duplih rankova: {len(ranks) != len(set(ranks))}")
    except Exception as exc:
        print(f"  [GRESKA] {exc}")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)
    sftp = client.open_sftp()
    with sftp.open("/tmp/_diag_ranks.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()
    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && venv/bin/python /tmp/_diag_ranks.py 2>&1", timeout=300
    )
    print()
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
