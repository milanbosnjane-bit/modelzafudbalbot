import sqlite3
from datetime import datetime, timedelta

c = sqlite3.connect("data/football_roi.db")
cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
n = c.execute(
    "select count(*) from fixtures where status='FT' and fixture_date >= ?",
    (cutoff,),
).fetchone()[0]
with_odds = c.execute("""
    select count(distinct f.id) from fixtures f
    join odds_snapshots o on o.fixture_id = f.id
    where f.status='FT' and f.fixture_date >= ?
""", (cutoff,)).fetchone()[0]
print("FT last 30d:", n, "with odds:", with_odds)
