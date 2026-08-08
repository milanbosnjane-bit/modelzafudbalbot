import sqlite3
c = sqlite3.connect("data/football_roi.db")
print("VOID:")
for r in c.execute("""
    SELECT th.name, ta.name, dp.market, dp.selection, f.status, f.home_goals, f.away_goals
    FROM daily_picks dp
    JOIN fixtures f ON f.id=dp.fixture_id
    JOIN teams th ON th.id=f.home_team_id
    JOIN teams ta ON ta.id=f.away_team_id
    WHERE dp.outcome='void'
"""):
    print(r)

print("\nUNIQUE W/L by match:")
for r in c.execute("""
    SELECT th.name, ta.name, f.home_goals, f.away_goals, dp.market, dp.selection, dp.outcome, COUNT(*)
    FROM daily_picks dp
    JOIN fixtures f ON f.id=dp.fixture_id
    JOIN teams th ON th.id=f.home_team_id
    JOIN teams ta ON ta.id=f.away_team_id
    WHERE dp.outcome IN ('win','lose')
    GROUP BY dp.fixture_id, dp.market, dp.selection, dp.outcome
"""):
    print(r)
