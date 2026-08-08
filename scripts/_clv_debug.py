import sqlite3

c = sqlite3.connect("data/football_roi.db")
print("=== Recent settled picks with CLV ===")
for r in c.execute(
    """
    SELECT id, selection, odds, closing_odds, clv, outcome, fair_implied_prob, expected_value
    FROM daily_picks
    WHERE outcome IN ('win','lose','push') AND clv IS NOT NULL
    ORDER BY pick_date DESC
    LIMIT 15
    """
):
    print(r)

print("\n=== Aggregates ===")
for label, q in [
    ("all settled w/ clv", "SELECT AVG(clv), COUNT(*) FROM daily_picks WHERE outcome IN ('win','lose','push') AND clv IS NOT NULL"),
    ("wins w/ clv", "SELECT AVG(clv), COUNT(*) FROM daily_picks WHERE outcome='win' AND clv IS NOT NULL"),
    ("losses w/ clv", "SELECT AVG(clv), COUNT(*) FROM daily_picks WHERE outcome='lose' AND clv IS NOT NULL"),
    ("settled no clv", "SELECT COUNT(*) FROM daily_picks WHERE outcome IN ('win','lose','push') AND clv IS NULL"),
]:
    print(label, c.execute(q).fetchone())

print("\n=== Today's last win ===")
for r in c.execute(
    """
    SELECT id, selection, odds, closing_odds, clv, fair_implied_prob, expected_value, outcome
    FROM daily_picks
    WHERE outcome='win'
    ORDER BY pick_date DESC
    LIMIT 3
    """
):
    bet, close, fair = r[2], r[3], r[6]
    implied = 1/bet if bet else 0
    calc = (bet * fair - 1) if fair else None
    print(r, f"implied@{bet:.2f}={implied:.2%}", f"calc_fair_clv={calc:.2%}" if calc else "")
