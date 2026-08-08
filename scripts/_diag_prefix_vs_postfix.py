#!/usr/bin/env python3
"""Split odds_snapshots into rows written before and after the ingest fix. Read-only."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")

# The strict allowlist went live on the server in this window.
CUTOFF = os.environ.get("FIX_CUTOFF", "2026-08-08 16:00:00")

SCRIPT = f'CUTOFF = "{CUTOFF}"\n' + r"""
import sqlite3

conn = sqlite3.connect("file:./data/football_roi.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

print(f"granica ispravke: {CUTOFF}")
print()

for label, cond in (("PRE ispravke", "captured_at < ?"), ("POSLE ispravke", "captured_at >= ?")):
    print(f"=== {label} ===")
    r = conn.execute(
        "SELECT COUNT(*) n, SUM(CASE WHEN fair_prob IS NULL THEN 1 ELSE 0 END) nf "
        f"FROM odds_snapshots WHERE {cond}", (CUTOFF,)
    ).fetchone()
    n, nf = r["n"], r["nf"] or 0
    pct = 100.0 * nf / n if n else 0
    print(f"  redova: {n}")
    print(f"  fair_prob NULL: {nf} ({pct:.2f}%)")

    r = conn.execute(
        f"SELECT COUNT(*) n FROM (SELECT 1 FROM odds_snapshots WHERE {cond} "
        "GROUP BY fixture_id, bookmaker, market, selection, line, captured_at "
        "HAVING COUNT(*) > 1)", (CUTOFF,)
    ).fetchone()
    print(f"  kolizionih kljuceva: {r['n']}")

    r = conn.execute(
        "SELECT ROUND(AVG(market_overround), 4) a FROM odds_snapshots "
        f"WHERE {cond} AND market_overround IS NOT NULL AND market='match_winner'", (CUTOFF,)
    ).fetchone()
    print(f"  match_winner prosecan overround: {r['a']}")

    r = conn.execute(
        f"SELECT COUNT(*) n FROM odds_snapshots WHERE {cond} AND market='over_under' "
        "AND (line IS NULL OR line NOT IN (1.5, 2.5, 3.5))", (CUTOFF,)
    ).fetchone()
    print(f"  over_under redova van linija 1.5/2.5/3.5: {r['n']}")

    r = conn.execute(
        f"SELECT COUNT(*) n FROM odds_snapshots WHERE {cond} AND selection LIKE '%/%'", (CUTOFF,)
    ).fetchone()
    print(f"  kombinovanih selekcija (sadrze '/'): {r['n']}")
    print()

print("=== Koliko istorije bi retrain/backtest procitao ===")
pre = conn.execute("SELECT COUNT(*) n FROM odds_snapshots WHERE captured_at < ?", (CUTOFF,)).fetchone()["n"]
tot = conn.execute("SELECT COUNT(*) n FROM odds_snapshots").fetchone()["n"]
print(f"  ukupno redova: {tot}, pre ispravke: {pre} ({100.0 * pre / tot:.1f}%)")

q = ("SELECT COUNT(DISTINCT o.fixture_id) n FROM odds_snapshots o "
     "JOIN fixtures f ON f.id = o.fixture_id "
     "WHERE f.status IN ('FT','AET','PEN') AND o.captured_at ")
print()
print("=== Zavrseni mecevi = osnova za trening i backtest ===")
print(f"  sa pre-fix kvotama:  {conn.execute(q + '< ?', (CUTOFF,)).fetchone()['n']}")
print(f"  sa post-fix kvotama: {conn.execute(q + '>= ?', (CUTOFF,)).fetchone()['n']}")

print()
print("=== Tabele koje trening cita ===")
for t in ("feature_vectors", "dc_params", "confidence_prediction_log", "regime_history", "daily_picks"):
    try:
        print(f"  {t}: {conn.execute('SELECT COUNT(*) n FROM ' + t).fetchone()['n']} redova")
    except Exception as exc:
        print(f"  {t}: {exc}")

conn.close()
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)
    sftp = client.open_sftp()
    with sftp.open("/tmp/_diag_split.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()
    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && PYTHONUTF8=1 venv/bin/python /tmp/_diag_split.py 2>&1", timeout=900
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
