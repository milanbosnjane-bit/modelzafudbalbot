import sqlite3

c = sqlite3.connect("data/football_roi.db")
c.row_factory = sqlite3.Row
rows = c.execute(
    "SELECT id, fixture_id, market, selection, odds, closing_odds, clv, outcome "
    "FROM daily_picks WHERE clv IS NOT NULL LIMIT 20"
).fetchall()
for r in rows:
    print(dict(r))
