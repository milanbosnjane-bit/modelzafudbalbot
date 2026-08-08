import sqlite3
from pathlib import Path

c = sqlite3.connect("data/football_roi.db")

ft = c.execute(
    "select count(*) from fixtures where status in ('FT','AET','PEN') and home_goals is not null"
).fetchone()[0]
fv = c.execute("select count(*) from feature_vectors").fetchone()[0]
odds_fixtures = c.execute("select count(distinct fixture_id) from odds_snapshots").fetchone()[0]

ft_with_odds = c.execute("""
    select count(distinct f.id) from fixtures f
    join odds_snapshots o on o.fixture_id = f.id
    where f.status in ('FT','AET','PEN') and f.home_goals is not null
""").fetchone()[0]

ft_with_fv = c.execute("""
    select count(distinct f.id) from fixtures f
    join feature_vectors fv on fv.fixture_id = f.id
    where f.status in ('FT','AET','PEN') and f.home_goals is not null
""").fetchone()[0]

all_three = c.execute("""
    select count(distinct f.id) from fixtures f
    join feature_vectors fv on fv.fixture_id = f.id
    join odds_snapshots o on o.fixture_id = f.id
    where f.status in ('FT','AET','PEN') and f.home_goals is not null
""").fetchone()[0]

print("FT fixtures:", ft)
print("feature_vectors:", fv)
print("fixtures with any odds:", odds_fixtures)
print("FT with odds:", ft_with_odds)
print("FT with features:", ft_with_fv)
print("FT with both:", all_three)

sample = c.execute("""
    select f.id, f.status, f.home_goals, count(o.id) as odds_n
    from fixtures f
    left join odds_snapshots o on o.fixture_id = f.id
    where f.status='FT'
    group by f.id
    limit 5
""").fetchall()
print("sample FT:", sample)
