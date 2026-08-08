import os
import sqlite3
import tempfile
import paramiko
import io
from pathlib import Path

HOST = "192.168.1.106"
USER = "miki"
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = "/home/miki/football-dc-bot/data/football_roi.db"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()
buf = io.BytesIO()
sftp.getfo(REMOTE, buf)
sftp.close()
c.close()

tmp = Path(tempfile.gettempdir()) / "football_roi_remote.db"
buf.seek(0)
tmp.write_bytes(buf.read())
conn = sqlite3.connect(str(tmp))

print("=== Server CLV aggregates ===")
for label, q in [
    ("settled total", "SELECT COUNT(*) FROM daily_picks WHERE outcome IN ('win','lose','push')"),
    ("with clv", "SELECT COUNT(*) FROM daily_picks WHERE outcome IN ('win','lose','push') AND clv IS NOT NULL"),
    ("avg clv", "SELECT AVG(clv) FROM daily_picks WHERE outcome IN ('win','lose','push') AND clv IS NOT NULL"),
    ("wins avg clv", "SELECT AVG(clv), COUNT(*) FROM daily_picks WHERE outcome='win' AND clv IS NOT NULL"),
]:
    print(label, conn.execute(q).fetchone())

print("\n=== All picks with CLV ===")
for r in conn.execute(
    "SELECT id, selection, odds, closing_odds, clv, outcome, fair_implied_prob, expected_value FROM daily_picks WHERE clv IS NOT NULL ORDER BY id"
):
    print(r)

print("\n=== Recent wins ===")
for r in conn.execute(
    "SELECT id, selection, odds, closing_odds, clv, outcome, fair_implied_prob FROM daily_picks WHERE outcome='win' ORDER BY pick_date DESC LIMIT 5"
):
    print(r)

print("\n=== Pick 121 detail ===")
p = conn.execute(
    "SELECT fixture_id, market, selection, odds, closing_odds, clv, fair_implied_prob FROM daily_picks WHERE id=121"
).fetchone()
print("pick:", p)
if p:
    fid = p[0]
    snaps = conn.execute(
        "SELECT bookmaker, selection, current_odds, closing_odds, fair_prob, captured_at "
        "FROM odds_snapshots WHERE fixture_id=? AND market='over_under' AND is_closing=1 LIMIT 20",
        (fid,),
    ).fetchall()
    for s in snaps:
        print("  snap:", s)
    under = conn.execute(
        "SELECT fair_prob, closing_odds, bookmaker FROM odds_snapshots "
        "WHERE fixture_id=? AND market='over_under' AND selection='Under 2.5' AND is_closing=1 "
        "ORDER BY captured_at DESC LIMIT 1",
        (fid,),
    ).fetchone()
    print("under closing used:", under)
    if under and p[3]:
        print("CLV calc:", p[3] * under[0] - 1)
