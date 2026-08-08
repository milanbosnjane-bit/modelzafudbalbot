import sqlite3
c = sqlite3.connect("data/football_roi.db")

print("Status breakdown today:")
for r in c.execute(
    "SELECT status, COUNT(*) FROM fixtures WHERE date(fixture_date)=date('now') GROUP BY status"
):
    print(" ", r)

print("\nPick fixtures (should have started):")
ids = (1499958, 1499964, 1553749, 1554058, 1554051, 1554049)
for r in c.execute(
    f"SELECT id, status, home_goals, away_goals, fixture_date FROM fixtures WHERE id IN ({','.join('?'*len(ids))}) ORDER BY fixture_date",
    ids,
):
    print(" ", r)

print("\nAny LIVE/HT/FT among pick fixtures:")
for r in c.execute("""
    SELECT f.id, f.status, f.home_goals, f.away_goals, f.fixture_date
    FROM daily_picks dp JOIN fixtures f ON f.id=dp.fixture_id
    WHERE f.status NOT IN ('NS','TBD','PST')
    GROUP BY f.id
"""):
    print(" ", r)
