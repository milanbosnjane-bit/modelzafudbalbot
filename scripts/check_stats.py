import sqlite3
c = sqlite3.connect("data/football_roi.db")

print("=== OUTCOMES ===")
for r in c.execute("SELECT outcome, COUNT(*) FROM daily_picks GROUP BY outcome ORDER BY outcome"):
    print(f"  {r[0]}: {r[1]}")

w = c.execute("SELECT COUNT(*) FROM daily_picks WHERE outcome='win'").fetchone()[0]
l = c.execute("SELECT COUNT(*) FROM daily_picks WHERE outcome='lose'").fetchone()[0]
p = c.execute("SELECT COUNT(*) FROM daily_picks WHERE outcome='push'").fetchone()[0]
pend = c.execute("SELECT COUNT(*) FROM daily_picks WHERE outcome='pending'").fetchone()[0]
settled = w + l + p

print(f"\nSettled: {settled}  Pending: {pend}")

if settled:
    stake = c.execute("SELECT COALESCE(SUM(stake_units),0) FROM daily_picks WHERE outcome IN ('win','lose','push')").fetchone()[0]
    profit = c.execute("SELECT COALESCE(SUM(profit_units),0) FROM daily_picks WHERE outcome IN ('win','lose','push')").fetchone()[0]
    wr = w / (w + l) * 100 if (w + l) else 0
    roi = profit / stake * 100 if stake else 0
    print(f"Winrate: {wr:.1f}% ({w}W / {l}L / {p}P)")
    print(f"Profit: {profit:+.2f}u  Stake: {stake:.2f}u  ROI: {roi:+.2f}%")

print("\n=== ALL SETTLED PICKS ===")
for r in c.execute("""
    SELECT th.name, ta.name, f.home_goals, f.away_goals, f.status,
           dp.market, dp.selection, dp.odds, dp.outcome, dp.profit_units
    FROM daily_picks dp
    JOIN fixtures f ON f.id = dp.fixture_id
    JOIN teams th ON th.id = f.home_team_id
    JOIN teams ta ON ta.id = f.away_team_id
    WHERE dp.outcome IN ('win','lose','push')
    ORDER BY dp.id
"""):
    home, away, hg, ag, st, market, sel, odds, outcome, profit = r
    print(f"  {home} vs {away} ({hg}-{ag} {st}) | {market} {sel} @{odds:g} -> {outcome} ({profit:+.2f}u)")

print("\n=== PENDING ===")
for r in c.execute("""
    SELECT th.name, ta.name, f.home_goals, f.away_goals, f.status,
           dp.market, dp.selection, dp.odds
    FROM daily_picks dp
    JOIN fixtures f ON f.id = dp.fixture_id
    JOIN teams th ON th.id = f.home_team_id
    JOIN teams ta ON ta.id = f.away_team_id
    WHERE dp.outcome = 'pending'
    ORDER BY f.fixture_date
"""):
    home, away, hg, ag, st, market, sel, odds = r
    score = f"{hg}-{ag}" if hg is not None else "?"
    print(f"  {home} vs {away} ({score} {st}) | {market} {sel} @{odds:g}")
