import sqlite3
c = sqlite3.connect("data/football_roi.db")

print("Settled:", c.execute("SELECT COUNT(*) FROM daily_picks WHERE outcome != 'pending'").fetchone()[0])
print("Pending:", c.execute("SELECT COUNT(*) FROM daily_picks WHERE outcome = 'pending'").fetchone()[0])

ft_ids = [r[0] for r in c.execute(
    "SELECT id FROM fixtures WHERE date(fixture_date)=date('now') AND status IN ('FT','AET','PEN')"
)]
print("FT fixture ids today:", ft_ids)

overlap = c.execute(
    f"SELECT fixture_id, market, selection, outcome FROM daily_picks WHERE fixture_id IN ({','.join('?'*len(ft_ids))})",
    ft_ids,
).fetchall() if ft_ids else []
print("Picks on FT fixtures today:", len(overlap))
for r in overlap[:10]:
    print(" ", r)

latest = c.execute(
    "SELECT pick_date, rank, market, selection, outcome FROM daily_picks ORDER BY pick_date DESC LIMIT 3"
).fetchall()
print("Latest picks:")
for r in latest:
    print(" ", r)

print("\nLatest batch (15:29) with fixture status:")
rows = c.execute("""
    SELECT dp.rank, dp.market, dp.selection, dp.outcome,
           f.fixture_date, f.status, f.home_goals, f.away_goals
    FROM daily_picks dp
    JOIN fixtures f ON f.id = dp.fixture_id
    WHERE dp.pick_date LIKE '2026-06-27 15:29%'
    ORDER BY dp.rank
""").fetchall()
for r in rows:
    print(" ", r)
