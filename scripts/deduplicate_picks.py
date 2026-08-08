"""Ukloni duplikate u daily_picks — zadrzi po 1 tip po mecu po danu."""
import sqlite3
from collections import defaultdict

DB = "data/football_roi.db"

OUTCOME_PRIORITY = {
    "win": 0,
    "lose": 1,
    "push": 2,
    "pending": 3,
    "void": 4,
}

MARKET_PRIORITY = {
    "match_winner": 0,
    "over_under": 1,
    "btts": 2,
    "double_chance": 3,
    "asian_handicap": 9,
}


def pick_score(row: tuple) -> tuple:
    """Lower = better candidate to keep."""
    (
        _id,
        pick_date,
        fixture_id,
        market,
        selection,
        outcome,
        profit_units,
        expected_value,
        rank,
    ) = row
    day = pick_date[:10]
    return (
        day,
        fixture_id,
        OUTCOME_PRIORITY.get(outcome, 99),
        MARKET_PRIORITY.get(market, 50),
        -float(expected_value or 0),
        int(_id),
    )


def main() -> None:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row

    before = c.execute("SELECT COUNT(*) FROM daily_picks").fetchone()[0]
    print(f"Before: {before} rows")

    rows = c.execute("""
        SELECT id, pick_date, fixture_id, market, selection, outcome,
               profit_units, expected_value, rank
        FROM daily_picks
        ORDER BY id
    """).fetchall()

    groups: dict[tuple[str, int], list] = defaultdict(list)
    for r in rows:
        day = r["pick_date"][:10]
        groups[(day, r["fixture_id"])].append(
            (
                r["id"],
                r["pick_date"],
                r["fixture_id"],
                r["market"],
                r["selection"],
                r["outcome"],
                r["profit_units"],
                r["expected_value"],
                r["rank"],
            )
        )

    keep_ids: set[int] = set()
    delete_ids: list[int] = []

    for key, items in groups.items():
        if len(items) == 1:
            keep_ids.add(items[0][0])
            continue
        best = min(items, key=pick_score)
        keep_ids.add(best[0])
        for item in items:
            if item[0] != best[0]:
                delete_ids.append(item[0])

    print(f"Groups with duplicates: {sum(1 for g in groups.values() if len(g) > 1)}")
    print(f"Keeping: {len(keep_ids)}  Deleting: {len(delete_ids)}")

    if delete_ids:
        placeholders = ",".join("?" * len(delete_ids))
        c.execute(f"DELETE FROM daily_picks WHERE id IN ({placeholders})", delete_ids)
        c.commit()

    after = c.execute("SELECT COUNT(*) FROM daily_picks").fetchone()[0]
    print(f"After: {after} rows\n")

    w = c.execute("SELECT COUNT(*) FROM daily_picks WHERE outcome='win'").fetchone()[0]
    l = c.execute("SELECT COUNT(*) FROM daily_picks WHERE outcome='lose'").fetchone()[0]
    p = c.execute("SELECT COUNT(*) FROM daily_picks WHERE outcome='push'").fetchone()[0]
    pend = c.execute("SELECT COUNT(*) FROM daily_picks WHERE outcome='pending'").fetchone()[0]
    void = c.execute("SELECT COUNT(*) FROM daily_picks WHERE outcome='void'").fetchone()[0]
    settled = w + l + p
    print("=== OUTCOMES ===")
    print(f"  win: {w}  lose: {l}  push: {p}  pending: {pend}  void: {void}")

    if settled:
        stake = c.execute(
            "SELECT COALESCE(SUM(stake_units),0) FROM daily_picks WHERE outcome IN ('win','lose','push')"
        ).fetchone()[0]
        profit = c.execute(
            "SELECT COALESCE(SUM(profit_units),0) FROM daily_picks WHERE outcome IN ('win','lose','push')"
        ).fetchone()[0]
        wr = w / (w + l) * 100 if (w + l) else 0
        roi = profit / stake * 100 if stake else 0
        print(f"\nWinrate: {wr:.1f}%  Profit: {profit:+.2f}u  ROI: {roi:+.2f}%")

    print("\n=== REMAINING PICKS ===")
    for r in c.execute("""
        SELECT dp.id, th.name, ta.name, dp.market, dp.selection, dp.odds,
               dp.outcome, dp.profit_units, date(dp.pick_date)
        FROM daily_picks dp
        JOIN fixtures f ON f.id = dp.fixture_id
        JOIN teams th ON th.id = f.home_team_id
        JOIN teams ta ON ta.id = f.away_team_id
        ORDER BY dp.pick_date, dp.id
    """):
        print(r)


if __name__ == "__main__":
    main()
