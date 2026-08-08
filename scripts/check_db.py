import sqlite3
from pathlib import Path

db = Path("data/football_roi.db")
if not db.exists():
    print("no db")
    raise SystemExit(1)

c = sqlite3.connect(db)
tables = [r[0] for r in c.execute("select name from sqlite_master where type='table'").fetchall()]
print("tables:", tables)
for tbl in tables:
    n = c.execute(f"select count(*) from [{tbl}]").fetchone()[0]
    print(f"  {tbl}: {n}")

if "daily_picks" in tables:
    settled = c.execute(
        "select count(*) from daily_picks where outcome in ('win','lose','push')"
    ).fetchone()[0]
    print("settled picks:", settled)

if "fixtures" in tables:
    ft = c.execute(
        "select count(*) from fixtures where status in ('FT','AET','PEN')"
    ).fetchone()[0]
    ns = c.execute("select count(*) from fixtures where status='NS'").fetchone()[0]
    scored = c.execute(
        "select count(*) from fixtures where home_goals is not null"
    ).fetchone()[0]
    print(f"fixtures FT={ft} NS={ns} with_scores={scored}")
