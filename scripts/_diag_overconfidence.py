#!/usr/bin/env python3
"""Quantify how overconfident the model is on settled picks. Read-only."""
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

rows = conn.execute('''
    SELECT probability, fair_implied_prob, odds, expected_value, confidence,
           outcome, profit_units, market, pick_date
    FROM daily_picks
    WHERE outcome IN ('win','lose')
    ORDER BY pick_date
''').fetchall()

n = len(rows)
wins = sum(1 for r in rows if r["outcome"] == "win")
staked = n * 1.0
profit = sum((r["odds"] - 1.0) if r["outcome"] == "win" else -1.0 for r in rows)

print(f"=== Realizovano na {n} settled pickova ===")
print(f"  pogodjeno: {wins} ({100.0 * wins / n:.1f}%)")
print(f"  prosecna kvota: {sum(r['odds'] for r in rows) / n:.2f}")
print(f"  ROI po 1u ravnog uloga: {100.0 * profit / staked:+.1f}%  (profit {profit:+.2f}u)")

avg_p = sum(r["probability"] for r in rows) / n
avg_ev = sum(r["expected_value"] for r in rows) / n
avg_conf = sum(r["confidence"] for r in rows) / n
print()
print("=== Kalibracija: sta je model obecao vs sta se desilo ===")
print(f"  prosecna model verovatnoca: {100.0 * avg_p:.1f}%")
print(f"  stvarna stopa pogodaka:     {100.0 * wins / n:.1f}%")
print(f"  razlika (overconfidence):   {100.0 * (avg_p - wins / n):+.1f} pp")
print(f"  prosecan obecani EV: {100.0 * avg_ev:+.1f}%   realizovan: {100.0 * profit / staked:+.1f}%")
print(f"  prosecna 'confidence': {100.0 * avg_conf:.1f}%")

brier = sum((r["probability"] - (1 if r["outcome"] == "win" else 0)) ** 2 for r in rows) / n
base = wins / n
brier_base = sum((base - (1 if r["outcome"] == "win" else 0)) ** 2 for r in rows) / n
print()
print("=== Brier score (nize je bolje) ===")
print(f"  model verovatnoca: {brier:.4f}")
print(f"  trivijalna konstanta ({100.0 * base:.0f}%): {brier_base:.4f}")
print(f"  -> model je {'BOLJI' if brier < brier_base else 'LOSIJI'} od konstante")

print()
print("=== Po obecanom EV-u ===")
buckets = [(-9, 0.1, "EV < 10%"), (0.1, 0.3, "EV 10-30%"), (0.3, 0.6, "EV 30-60%"), (0.6, 99, "EV > 60%")]
for lo, hi, label in buckets:
    sel = [r for r in rows if lo <= r["expected_value"] < hi]
    if not sel:
        continue
    w = sum(1 for r in sel if r["outcome"] == "win")
    pr = sum((r["odds"] - 1.0) if r["outcome"] == "win" else -1.0 for r in sel)
    ap = sum(r["probability"] for r in sel) / len(sel)
    print(f"  {label:11s} n={len(sel):3d}  pogodjeno={100.0 * w / len(sel):5.1f}%  "
          f"model_tvrdi={100.0 * ap:5.1f}%  ROI={100.0 * pr / len(sel):+7.1f}%")

print()
print("=== Po kvoti ===")
for lo, hi, label in [(1, 2, "1.0-2.0"), (2, 3, "2.0-3.0"), (3, 4.5, "3.0-4.5"), (4.5, 99, "4.5+")]:
    sel = [r for r in rows if lo <= r["odds"] < hi]
    if not sel:
        continue
    w = sum(1 for r in sel if r["outcome"] == "win")
    pr = sum((r["odds"] - 1.0) if r["outcome"] == "win" else -1.0 for r in sel)
    ap = sum(r["probability"] for r in sel) / len(sel)
    print(f"  kvota {label:8s} n={len(sel):3d}  pogodjeno={100.0 * w / len(sel):5.1f}%  "
          f"model_tvrdi={100.0 * ap:5.1f}%  ROI={100.0 * pr / len(sel):+7.1f}%")

print()
print("=== Po marketu ===")
for r in conn.execute('''
    SELECT market, COUNT(*) n,
           SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) w,
           ROUND(AVG(probability), 3) p,
           ROUND(AVG(odds), 2) o
    FROM daily_picks WHERE outcome IN ('win','lose') GROUP BY market
'''):
    print(f"  {r['market']:14s} n={r['n']:3d} pogodjeno={100.0 * r['w'] / r['n']:5.1f}% "
          f"model_tvrdi={100.0 * r['p']:5.1f}% avg_kvota={r['o']}")

conn.close()
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)
    sftp = client.open_sftp()
    with sftp.open("/tmp/_diag_overconf.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()
    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && PYTHONUTF8=1 venv/bin/python /tmp/_diag_overconf.py 2>&1", timeout=600
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
