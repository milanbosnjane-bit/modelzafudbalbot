import sqlite3
from datetime import datetime, timedelta

c = sqlite3.connect("data/football_roi.db")

rows = c.execute("""
    select f.id, f.fixture_date, f.status,
           min(o.captured_at) as min_cap, max(o.captured_at) as max_cap,
           count(o.id) as n
    from fixtures f
    join odds_snapshots o on o.fixture_id = f.id
    where f.status='FT'
    group by f.id
""").fetchall()
print("FT with odds:", len(rows))
for r in rows[:5]:
    print(r)
