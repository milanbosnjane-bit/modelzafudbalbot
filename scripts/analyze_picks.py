"""Analiza završenih tipova — najbolji i najlošiji performans."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "football_roi.db"

QUERY_SETTLED = """
    SELECT
        dp.pick_date,
        dp.selection,
        dp.market,
        dp.odds,
        dp.outcome,
        dp.stake_units,
        dp.profit_units,
        dp.expected_value,
        dp.confidence,
        th.name AS home,
        ta.name AS away,
        f.home_goals,
        f.away_goals,
        f.status
    FROM daily_picks dp
    JOIN fixtures f  ON f.id = dp.fixture_id
    JOIN teams th    ON th.id = f.home_team_id
    JOIN teams ta    ON ta.id = f.away_team_id
    WHERE dp.outcome IN ('win', 'lose', 'push')
    ORDER BY dp.profit_units DESC, dp.pick_date ASC
"""

QUERY_BY_SELECTION = """
    SELECT
        dp.selection,
        dp.market,
        COUNT(*) AS total,
        SUM(CASE WHEN dp.outcome='win' THEN 1 ELSE 0 END) AS wins,
        SUM(CASE WHEN dp.outcome='lose' THEN 1 ELSE 0 END) AS losses,
        ROUND(COALESCE(SUM(dp.profit_units), 0), 2) AS total_profit,
        ROUND(COALESCE(AVG(dp.odds), 0), 2) AS avg_odds
    FROM daily_picks dp
    WHERE dp.outcome IN ('win', 'lose', 'push')
    GROUP BY dp.selection, dp.market
    ORDER BY total_profit DESC
"""

SEP = "-" * 95


def _fmt_date(v: str) -> str:
    return (v or "?").replace("T", " ")[:16]


def _outcome_icon(outcome: str) -> str:
    return {"win": "✅ WIN ", "lose": "❌ LOSE", "push": "➖ PUSH"}.get(outcome, "?")


def _profit_str(v) -> str:
    if v is None:
        return "  —   "
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}u"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not DB_PATH.exists():
        print(f"[GRESKA] Baza ne postoji: {DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    picks = conn.execute(QUERY_SETTLED).fetchall()
    by_type = conn.execute(QUERY_BY_SELECTION).fetchall()
    conn.close()

    if not picks:
        print("Nema setlovanih tipova.")
        return 0

    # ------------------------------------------------------------------ #
    # 1. Sve zavrsene utakmice sortirane po profitu (od najboljeg)         #
    # ------------------------------------------------------------------ #
    print(f"\n{'='*95}")
    print(f"  ANALIZA ZAVRŠENIH TIPOVA — {len(picks)} setlovanih")
    print(f"{'='*95}\n")

    print(f"{'RB':<4} {'Meč':<32} {'Tip':<10} {'Kv':>6} {'Ulog':>6} {'Profit':>8}  Ishod")
    print(SEP)

    for i, r in enumerate(picks, 1):
        score = f"({r['home_goals']}-{r['away_goals']})" if r["home_goals"] is not None else "(NS)"
        match_label = f"{r['home']} vs {r['away']} {score}"
        if len(match_label) > 31:
            match_label = match_label[:29] + ".."
        tip = r["selection"] or "?"
        if len(tip) > 9:
            tip = tip[:8] + "."
        print(
            f"{i:<4} {match_label:<32} {tip:<10} {r['odds']:>6.2f} "
            f"{(r['stake_units'] or 0):>6.2f} {_profit_str(r['profit_units']):>8}  "
            f"{_outcome_icon(r['outcome'])}"
        )

    # ------------------------------------------------------------------ #
    # 2. Top 5 najboljih tipova                                            #
    # ------------------------------------------------------------------ #
    print(f"\n{'='*95}")
    print("  🏆 TOP 5 NAJBOLJIH TIPOVA")
    print(f"{'='*95}")
    for r in picks[:5]:
        score = f"({r['home_goals']}-{r['away_goals']})" if r["home_goals"] is not None else "(NS)"
        ev = r["expected_value"]
        ev_str = f"EV {ev*100:+.1f}%" if ev is not None else ""
        print(
            f"  ✅  {r['home']} vs {r['away']} {score}"
            f"  |  {r['market']} {r['selection']} @{r['odds']:.2f}"
            f"  |  Profit: {_profit_str(r['profit_units'])}"
            f"  {ev_str}"
        )

    # ------------------------------------------------------------------ #
    # 3. Top 5 najlošijih tipova                                           #
    # ------------------------------------------------------------------ #
    print(f"\n{'='*95}")
    print("  💀 TOP 5 NAJLOŠIJIH TIPOVA")
    print(f"{'='*95}")
    for r in reversed(picks[-5:]):
        score = f"({r['home_goals']}-{r['away_goals']})" if r["home_goals"] is not None else "(NS)"
        ev = r["expected_value"]
        ev_str = f"EV {ev*100:+.1f}%" if ev is not None else ""
        print(
            f"  ❌  {r['home']} vs {r['away']} {score}"
            f"  |  {r['market']} {r['selection']} @{r['odds']:.2f}"
            f"  |  Profit: {_profit_str(r['profit_units'])}"
            f"  {ev_str}"
        )

    # ------------------------------------------------------------------ #
    # 4. Performans po tipu tipa (Draw / Home / Away / Under...)           #
    # ------------------------------------------------------------------ #
    print(f"\n{'='*95}")
    print("  📊 PERFORMANS PO VRSTI TIPA")
    print(f"{'='*95}")
    print(f"  {'Tip':<12} {'Market':<16} {'UK':>4} {'W':>4} {'L':>4} {'WR%':>6}  {'Profit':>8}  {'AvgKv':>6}")
    print(f"  {'-'*75}")
    for r in by_type:
        wr = r["wins"] / (r["wins"] + r["losses"]) * 100 if (r["wins"] + r["losses"]) > 0 else 0
        sign = "+" if r["total_profit"] >= 0 else ""
        print(
            f"  {r['selection']:<12} {r['market']:<16} {r['total']:>4} {r['wins']:>4} {r['losses']:>4} "
            f"{wr:>6.1f}%  {sign}{r['total_profit']:>7.2f}u  {r['avg_odds']:>6.2f}"
        )

    # ------------------------------------------------------------------ #
    # 5. Ukupan rezime                                                     #
    # ------------------------------------------------------------------ #
    wins_total = sum(1 for r in picks if r["outcome"] == "win")
    losses_total = sum(1 for r in picks if r["outcome"] == "lose")
    pushes_total = sum(1 for r in picks if r["outcome"] == "push")
    profit_total = sum(r["profit_units"] or 0.0 for r in picks)
    staked_total = sum(r["stake_units"] or 0.0 for r in picks)
    wr_total = wins_total / (wins_total + losses_total) * 100 if (wins_total + losses_total) > 0 else 0
    roi = profit_total / staked_total * 100 if staked_total > 0 else 0.0

    print(f"\n{'='*95}")
    print("  📈 UKUPAN REZIME")
    print(f"{'='*95}")
    print(f"  Tipovi:    {len(picks)}  (W:{wins_total} / L:{losses_total} / P:{pushes_total})")
    print(f"  Winrate:   {wr_total:.1f}%")
    print(f"  Uloženo:   {staked_total:.2f}u")
    sign = "+" if profit_total >= 0 else ""
    print(f"  Profit:    {sign}{profit_total:.2f}u")
    roi_sign = "+" if roi >= 0 else ""
    print(f"  ROI:       {roi_sign}{roi:.1f}%")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
