import sqlite3
import os
import sys
import tempfile
import paramiko
import io
from pathlib import Path

def load_conn():
    if os.environ.get("USE_SERVER") == "1":
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(
            os.environ.get("DEPLOY_HOST", "192.168.1.106"),
            username=os.environ.get("DEPLOY_USER", "miki"),
            password=os.environ.get("DEPLOY_PASS", "miki0510"),
            timeout=30,
            allow_agent=False,
            look_for_keys=False,
        )
        sftp = c.open_sftp()
        buf = io.BytesIO()
        sftp.getfo("/home/miki/football-dc-bot/data/football_roi.db", buf)
        sftp.close()
        c.close()
        tmp = Path(tempfile.gettempdir()) / "srv.db"
        tmp.write_bytes(buf.getvalue())
        return sqlite3.connect(str(tmp))
    return sqlite3.connect("data/football_roi.db")

conn = load_conn()
conn.row_factory = sqlite3.Row

print("=== Picks 2026-07-26 ===")
rows = conn.execute(
    """
    SELECT dp.id, dp.fixture_id, dp.market, dp.selection, dp.odds, dp.expected_value,
           dp.probability, dp.fair_implied_prob, dp.confidence, dp.outcome,
           dp.profit_units, dp.stake_units, dp.clv, dp.pick_date,
           f.home_team_id, f.away_team_id, f.home_goals, f.away_goals
    FROM daily_picks dp
    JOIN fixtures f ON f.id = dp.fixture_id
    WHERE date(dp.pick_date) = '2026-07-26'
    ORDER BY dp.id
    """
).fetchall()
team_ids = set()
for r in rows:
    team_ids.update([r["home_team_id"], r["away_team_id"]])
teams = {}
if team_ids:
    q = "SELECT id, name FROM teams WHERE id IN ({})".format(",".join("?" * len(team_ids)))
    teams = {row[0]: row[1] for row in conn.execute(q, list(team_ids))}
for r in rows:
    d = dict(r)
    d["match"] = f"{teams.get(r['home_team_id'],'?')} vs {teams.get(r['away_team_id'],'?')}"
    if r["home_goals"] is not None:
        d["score"] = f"{r['home_goals']}-{r['away_goals']}"
    print(d)

wins = sum(1 for r in rows if r["outcome"] == "win")
total = sum(1 for r in rows if r["outcome"] in ("win", "lose", "push"))
print(f"\n26.07: {wins}/{total} wins")

print("\n=== Recent daily win counts ===")
for day, w, t, profit in conn.execute(
    """
    SELECT date(pick_date), 
           SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END),
           SUM(CASE WHEN outcome IN ('win','lose','push') THEN 1 ELSE 0 END),
           ROUND(SUM(COALESCE(profit_units,0)), 2)
    FROM daily_picks
    WHERE outcome IN ('win','lose','push')
    GROUP BY date(pick_date)
    ORDER BY date(pick_date) DESC
    LIMIT 14
    """
):
    print(f"  {day}: {w}/{t} wins, profit {profit}u")

print("\n=== All-time settled stats ===")
row = conn.execute(
    """
    SELECT COUNT(*) total,
           SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) wins,
           ROUND(AVG(odds),2) avg_odds,
           ROUND(AVG(expected_value)*100,2) avg_ev_pct,
           ROUND(SUM(profit_units),2) profit
    FROM daily_picks WHERE outcome IN ('win','lose','push')
    """
).fetchone()
print(dict(row))
if row["total"]:
    wr = row["wins"] / row["total"] * 100
    print(f"Winrate: {wr:.1f}%")
    # binomial: P(4/4) at 41% base rate
    p = row["wins"] / row["total"]
    import math
    p4 = p**4
    print(f"P(4/4 by chance at {p:.1%} WR) = {p4:.1%}")

print("\n=== Odds profile 26.07 vs all time ===")
for label, q in [
    ("26.07", "SELECT AVG(odds), MIN(odds), MAX(odds), AVG(expected_value) FROM daily_picks WHERE date(pick_date)='2026-07-26'"),
    ("all settled", "SELECT AVG(odds), MIN(odds), MAX(odds), AVG(expected_value) FROM daily_picks WHERE outcome IN ('win','lose','push')"),
]:
    print(label, conn.execute(q).fetchone())
