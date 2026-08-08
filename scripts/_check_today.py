import sqlite3, datetime, sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
c = sqlite3.connect("data/football_roi.db")
c.row_factory = sqlite3.Row
today = datetime.date.today().isoformat()
rows = c.execute("""
    SELECT dp.id, dp.selection, dp.market, dp.odds, dp.expected_value, dp.confidence,
           dp.outcome, dp.stake_units, th.name as home, ta.name as away,
           f.fixture_date, f.status, f.league_id
    FROM daily_picks dp
    JOIN fixtures f ON f.id=dp.fixture_id
    JOIN teams th ON th.id=f.home_team_id
    JOIN teams ta ON ta.id=f.away_team_id
    WHERE dp.pick_date >= ?
    ORDER BY dp.rank
""", (today,)).fetchall()
print(f"Danas ({today}) — {len(rows)} pikova:")
for r in rows:
    ev = (r["expected_value"] or 0) * 100
    print(f"  {r['home']} vs {r['away']}")
    print(f"    Liga: {r['league_id']} | Status meča: {r['status']} | Vreme: {str(r['fixture_date'])[:16]}")
    print(f"    Tip: {r['market']} {r['selection']} @{r['odds']} | EV {ev:+.1f}% | conf {r['confidence']:.2f} | ishod: {r['outcome']}")
    print()
