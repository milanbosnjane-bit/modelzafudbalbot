import sqlite3

c = sqlite3.connect("data/football_roi.db")
for label, q in [
    ("feat+odds", """
        SELECT COUNT(DISTINCT f.id) FROM fixtures f
        JOIN feature_vectors fv ON fv.fixture_id=f.id
        JOIN odds_snapshots o ON o.fixture_id=f.id
        WHERE f.status IN ('FT','AET','PEN')
    """),
    ("2024-2025 odds", """
        SELECT COUNT(DISTINCT f.id) FROM fixtures f
        JOIN odds_snapshots o ON o.fixture_id=f.id
        WHERE f.status IN ('FT','AET','PEN')
        AND date(f.fixture_date) BETWEEN '2024-01-01' AND '2025-12-31'
    """),
    ("feat range odds", """
        SELECT COUNT(DISTINCT f.id) FROM fixtures f
        JOIN feature_vectors fv ON fv.fixture_id=f.id
        JOIN odds_snapshots o ON o.fixture_id=f.id
        WHERE f.status IN ('FT','AET','PEN')
        AND date(f.fixture_date) >= '2026-03-14'
    """),
]:
    print(label, c.execute(q).fetchone()[0])
