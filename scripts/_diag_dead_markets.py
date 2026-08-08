#!/usr/bin/env python3
"""Pre-flight for deleting non-allowlisted markets: what goes, and what depends on it. Read-only."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")

SCRIPT = r"""
import os
import sqlite3

KEEP = ("match_winner", "over_under", "btts")
conn = sqlite3.connect("file:./data/football_roi.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

size_mb = os.path.getsize("data/football_roi.db") / 1024 / 1024
total = conn.execute("SELECT COUNT(*) n FROM odds_snapshots").fetchone()["n"]
print(f"fajl: {size_mb:.0f} MB     odds_snapshots: {total}")

print("\n=== ZA BRISANJE (market nije na allowlist-u) ===")
doomed = 0
for r in conn.execute('''
    SELECT market, COUNT(*) n FROM odds_snapshots
    WHERE market NOT IN ('match_winner','over_under','btts')
    GROUP BY market ORDER BY n DESC
'''):
    doomed += r["n"]
    print(f"  {r['market']:32s} {r['n']:8d}")
print(f"  UKUPNO ZA BRISANJE: {doomed}  ({doomed*100.0/total:.1f}%)")

print("\n=== OSTAJE ===")
for r in conn.execute('''
    SELECT market, COUNT(*) n FROM odds_snapshots
    WHERE market IN ('match_winner','over_under','btts')
    GROUP BY market ORDER BY n DESC
'''):
    print(f"  {r['market']:32s} {r['n']:8d}")

print("\n=== RIZIK 1: mecevi koji bi ostali BEZ IJEDNOG snapshota ===")
r = conn.execute('''
    SELECT COUNT(*) n FROM (
      SELECT fixture_id FROM odds_snapshots GROUP BY fixture_id
      HAVING SUM(CASE WHEN market IN ('match_winner','over_under','btts') THEN 1 ELSE 0 END) = 0
    )
''').fetchone()
print(f"  meceva koji ostaju bez kvota: {r['n']}")

print("\n=== RIZIK 2: mecevi koji bi izgubili non-legacy kvote (fixture_has_api_odds) ===")
LEGACY = "('Pinnacle_legacy','Bet365_legacy','WilliamHill_legacy','VCBet_legacy','Interwetten_legacy')"
r = conn.execute(f'''
    SELECT COUNT(*) n FROM (
      SELECT fixture_id FROM odds_snapshots
      WHERE bookmaker NOT IN {LEGACY}
      GROUP BY fixture_id
      HAVING SUM(CASE WHEN market IN ('match_winner','over_under','btts') THEN 1 ELSE 0 END) = 0
    )
''').fetchone()
print(f"  meceva: {r['n']}")

print("\n=== RIZIK 3: is_closing redovi koje CLV koristi ===")
r = conn.execute('''
    SELECT
      SUM(CASE WHEN market NOT IN ('match_winner','over_under','btts') THEN 1 ELSE 0 END) brisemo,
      SUM(CASE WHEN market IN ('match_winner','over_under','btts') THEN 1 ELSE 0 END) ostaje
    FROM odds_snapshots WHERE is_closing = 1
''').fetchone()
print(f"  is_closing za brisanje={r['brisemo']} ostaje={r['ostaje']}")

r = conn.execute('''
    SELECT COUNT(*) n FROM daily_picks p
    WHERE EXISTS (SELECT 1 FROM odds_snapshots o
                  WHERE o.fixture_id=p.fixture_id AND o.market=p.market
                    AND o.selection=p.selection AND o.is_closing=1)
''').fetchone()
print(f"  pickova koji imaju svoj closing red (ostaje netaknuto): {r['n']}")

print("\n=== RIZIK 4: market_overround_1x2 feature ===")
print("  _market_features racuna overround preko SVIH marketa (engineer.py:271).")
for r in conn.execute('''
    SELECT
      ROUND(AVG(CASE WHEN market IN ('match_winner','over_under','btts')
                THEN market_overround END), 4) samo_allowlist,
      ROUND(AVG(market_overround), 4) svi_marketi
    FROM odds_snapshots WHERE market_overround IS NOT NULL
'''):
    print(f"  prosecan overround: svi_marketi={r['svi_marketi']}  samo_allowlist={r['samo_allowlist']}")

print("\n=== feature_vectors (vec izracunati, ne menjaju se) ===")
try:
    r = conn.execute("SELECT COUNT(*) n FROM feature_vectors").fetchone()
    print(f"  redova: {r['n']}")
except Exception as exc:
    print(f"  (nedostupno: {exc})")

conn.close()
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_diag_dead_markets.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && venv/bin/python /tmp/_diag_dead_markets.py 2>&1", timeout=900
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
