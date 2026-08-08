#!/usr/bin/env python3
"""Measure what the ingest fix actually changed for pick quality. Read-only."""
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

print("=== 1. OVERROUND koji ulazi u regime detektor ===")
print("   (feature market_overround_1x2 -> liquidity_score -> EV prag)")
for r in conn.execute('''
    SELECT market,
           COUNT(*) n,
           ROUND(AVG(market_overround), 4) avg_or,
           ROUND(MIN(market_overround), 4) min_or,
           ROUND(MAX(market_overround), 4) max_or
    FROM odds_snapshots
    WHERE market_overround IS NOT NULL
    GROUP BY market ORDER BY n DESC
'''):
    print(f"   {r['market']:14s} n={r['n']:8d}  avg={r['avg_or']:8.4f}  "
          f"min={r['min_or']:7.4f}  max={r['max_or']:8.4f}")

print("\n=== 2. fair_prob kompletnost (bez toga se pick tiho odbacuje) ===")
for r in conn.execute('''
    SELECT market,
           COUNT(*) n,
           SUM(CASE WHEN fair_prob IS NULL THEN 1 ELSE 0 END) null_fair
    FROM odds_snapshots GROUP BY market ORDER BY n DESC
'''):
    pct = 100.0 * r["null_fair"] / r["n"] if r["n"] else 0
    print(f"   {r['market']:14s} n={r['n']:8d}  fair_prob NULL={r['null_fair']:7d} ({pct:5.2f}%)")

print("\n=== 3. Da li devig grupe imaju tacan broj ishoda ===")
for r in conn.execute('''
    SELECT market, line, grp_size, COUNT(*) grupa FROM (
      SELECT market, line, COUNT(DISTINCT selection) grp_size
      FROM odds_snapshots
      GROUP BY fixture_id, bookmaker, market, line, captured_at
    ) GROUP BY market, line, grp_size ORDER BY market, line, grp_size
'''):
    line = "-" if r["line"] is None else f"{r['line']:g}"
    print(f"   {r['market']:14s} line={line:4s} ishoda={r['grp_size']}  grupa={r['grupa']}")

print("\n=== 4. Kolizije (isti kljuc, razlicite kvote) ===")
r = conn.execute('''
    SELECT COUNT(*) n FROM (
      SELECT 1 FROM odds_snapshots
      GROUP BY fixture_id, bookmaker, market, selection, line, captured_at
      HAVING COUNT(*) > 1
    )
''').fetchone()
print(f"   kolizionih kljuceva: {r['n']}")

print("\n=== 5. Neallowlist marketi koji su ostali ===")
r = conn.execute('''
    SELECT COUNT(*) n FROM odds_snapshots
    WHERE market NOT IN ('match_winner','over_under','btts')
''').fetchone()
print(f"   redova van allowlist-a: {r['n']}")

print("\n=== 6. Sto ostaje NEDIRNUTO: kalibracija pouzdanosti ===")
r = conn.execute('''
    SELECT COUNT(*) n,
           SUM(CASE WHEN calibrated_confidence IS NOT NULL THEN 1 ELSE 0 END) cal
    FROM daily_picks
''').fetchone()
print(f"   daily_picks={r['n']}  sa calibrated_confidence={r['cal']}")

r = conn.execute("SELECT COUNT(*) n FROM confidence_prediction_log").fetchone()
print(f"   confidence_prediction_log redova: {r['n']}")

print("\n=== 7. Uzorak za ocenu ROI-ja (settled pickovi) ===")
for r in conn.execute('''
    SELECT outcome, COUNT(*) n FROM daily_picks GROUP BY outcome ORDER BY n DESC
'''):
    print(f"   {str(r['outcome']):10s} {r['n']}")

conn.close()
"""

REGIME = r"""
from app.predictions.regime import REGIME_MODEL_PATH, RegimeDetector

det = RegimeDetector()
print(f"regime_kmeans.pkl postoji: {REGIME_MODEL_PATH.exists()}")
print(f"kmeans ucitan: {det.kmeans is not None}  scaler: {det.scaler is not None}")
print(f"mapiranje klastera: {det.cluster_to_regime}")

for label, orr in (("staro (zagadjeno)", 20.5), ("staro allowlist", 3.06), ("cisto sada", 0.03)):
    p = det.detect(
        {"market_overround_1x2": orr, "odds_change_pct_home": 0.01,
         "home_weighted_xG_last5": 1.4, "away_weighted_xG_last5": 1.2},
        league_id=39,
    )
    print(f"  overround={orr:6.2f} ({label:18s}) -> regime={p.regime.value:10s} "
          f"EV prag={p.ev_threshold}  conf prag={p.confidence_threshold}")
"""


def run(client, script: str, name: str) -> None:
    sftp = client.open_sftp()
    with sftp.open(f"/tmp/{name}", "w") as fh:
        fh.write(script)
    sftp.close()
    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && PYTHONPATH={REMOTE} PYTHONUTF8=1 venv/bin/python /tmp/{name} 2>&1",
        timeout=900,
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)
    run(client, SCRIPT, "_diag_quality.py")
    print("\n=== 8. REGIME DETEKTOR: osetljivost na overround ===")
    run(client, REGIME, "_diag_regime.py")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
