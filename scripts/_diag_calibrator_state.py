#!/usr/bin/env python3
"""Report the real server schema and calibrator readiness. Read-only."""
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

print("=== Tabele u bazi ===")
names = [r["name"] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
for n in names:
    cnt = conn.execute(f"SELECT COUNT(*) c FROM {n}").fetchone()["c"]
    print(f"  {n:34s} {cnt}")

print()
print("=== daily_picks kolone ===")
cols = [r["name"] for r in conn.execute("PRAGMA table_info(daily_picks)")]
print("  " + ", ".join(cols))
for need in ("calibrated_confidence", "calibrated_ev"):
    print(f"  {need}: {'IMA' if need in cols else 'NEMA'}")

print()
print("=== confidence_prediction_logs ===")
if "confidence_prediction_logs" in names:
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(confidence_prediction_logs)")]
    print("  kolone: " + ", ".join(cols))
    r = conn.execute("SELECT COUNT(*) n FROM confidence_prediction_logs").fetchone()
    print(f"  redova: {r['n']}")
    if r["n"]:
        for row in conn.execute(
            "SELECT predicted_at, market, selection, dixon_coles_probability, "
            "market_fair_probability, odds, old_confidence, calibrated_confidence, outcome "
            "FROM confidence_prediction_logs ORDER BY predicted_at DESC LIMIT 5"
        ):
            print("   ", dict(row))
else:
    print("  TABELA NE POSTOJI")

print()
print("=== daily_picks: uzorak za trening kalibratora ===")
for r in conn.execute(
    "SELECT outcome, COUNT(*) n FROM daily_picks GROUP BY outcome ORDER BY n DESC"
):
    print(f"  {str(r['outcome']):10s} {r['n']}")
r = conn.execute(
    "SELECT COUNT(*) n FROM daily_picks WHERE outcome IN ('win','lose')"
).fetchone()
print(f"  settled (win/lose) = upotrebljivo za trening: {r['n']}")
r = conn.execute(
    "SELECT MIN(pick_date) a, MAX(pick_date) b FROM daily_picks"
).fetchone()
print(f"  opseg pickova: {r['a']} .. {r['b']}")

print()
print("=== reasoning: da li se lambde mogu isparsirati za backfill ===")
for r in conn.execute(
    "SELECT reasoning FROM daily_picks WHERE outcome IN ('win','lose') LIMIT 3"
):
    print("  ", str(r["reasoning"])[:160])

conn.close()
"""

CAL = r"""
from app.model.confidence_calibrator import ConfidenceCalibrator, get_confidence_calibrator
from app.config import get_settings

s = get_settings()
print(f"use_calibrated_confidence (server): {s.use_calibrated_confidence}")
print(f"model_dir: {s.model_dir}")

cal = get_confidence_calibrator()
print(f"calibrator.is_ready: {cal.is_ready}")
print(f"MIN_SAMPLES/pragovi: ", {
    k: getattr(ConfidenceCalibrator, k)
    for k in dir(ConfidenceCalibrator)
    if k.isupper()
})

import pathlib
md = pathlib.Path(s.model_dir)
print(f"fajlovi u model_dir:")
for p in sorted(md.glob("*")):
    print(f"  {p.name}  {p.stat().st_size} B")
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
    run(client, SCRIPT, "_diag_cal_schema.py")
    print()
    print("=== KALIBRATOR (runtime) ===")
    run(client, CAL, "_diag_cal_runtime.py")
    print()
    print("=== Logovi predikcija: greske kalibracije ===")
    _, stdout, _ = client.exec_command(
        "journalctl --user -u football-dc-scheduler.service --since '8 hours ago' --no-pager "
        "| grep -iE 'calibrat|confidence_log|no such column|OperationalError|persist_picks' "
        "| tail -30",
        timeout=120,
    )
    out = stdout.read().decode("utf-8", errors="replace").strip()
    print(out or "  (nema pomena kalibracije u logovima)")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
