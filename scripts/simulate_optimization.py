"""Simulacija optimizacije: isključi BTTS No i Under 2.5 @ kvota > 2.0.

Upoređuje stvarni profit/ROI/winrate sa scenario gde se ti tipovi ne igraju
(profit tih redova tretira se kao 0 — ne ulaze u optimizovane statistike).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "football_roi.db"

SETTLED_OUTCOMES = ("win", "lose", "push")


def is_btts_no(market: str | None, selection: str | None) -> bool:
    """BTTS No — u bazi je market=btts, selection=No."""
    m = (market or "").strip().lower()
    s = (selection or "").strip().lower()
    if m == "btts" and s in ("no", "ng"):
        return True
    # fallback ako je selection pun naziv
    combined = f"{market or ''} {selection or ''}".lower()
    return "btts" in combined and (" no" in combined or combined.endswith("no"))


def is_under_25_high_odds(selection: str | None, odds: float | None) -> bool:
    """Under 2.5 sa kvotom strogo iznad 2.0."""
    s = (selection or "").strip().lower()
    if "under" not in s:
        return False
    if odds is None:
        return False
    # 2.5 linija — u bazi je selection 'Under 2.5'
    is_u25 = "2.5" in s or s in ("under", "under 2.5")
    return is_u25 and odds > 2.0


def should_exclude(row: sqlite3.Row) -> tuple[bool, str]:
    if is_btts_no(row["market"], row["selection"]):
        return True, "BTTS No"
    if is_under_25_high_odds(row["selection"], row["odds"]):
        return True, f"Under 2.5 @ {row['odds']:.2f} (>2.0)"
    return False, ""


def compute_stats(picks: list[sqlite3.Row]) -> dict:
    if not picks:
        return {
            "n": 0,
            "wins": 0,
            "losses": 0,
            "pushes": 0,
            "profit": 0.0,
            "staked": 0.0,
            "winrate": 0.0,
            "roi": 0.0,
        }
    wins = sum(1 for p in picks if p["outcome"] == "win")
    losses = sum(1 for p in picks if p["outcome"] == "lose")
    pushes = sum(1 for p in picks if p["outcome"] == "push")
    profit = sum(p["profit_units"] or 0.0 for p in picks)
    staked = sum(p["stake_units"] or 0.0 for p in picks)
    decisive = wins + losses
    winrate = (wins / decisive * 100) if decisive else 0.0
    roi = (profit / staked * 100) if staked > 0 else 0.0
    return {
        "n": len(picks),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "profit": profit,
        "staked": staked,
        "winrate": winrate,
        "roi": roi,
    }


def fmt_profit(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}u"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not DB_PATH.exists():
        print(f"[GRESKA] Baza ne postoji: {DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    all_rows = conn.execute(
        """
        SELECT id, pick_date, market, selection, odds, outcome,
               stake_units, profit_units, expected_value
        FROM daily_picks
        ORDER BY pick_date ASC, id ASC
        """
    ).fetchall()
    conn.close()

    settled = [r for r in all_rows if r["outcome"] in SETTLED_OUTCOMES]

    excluded: list[tuple[sqlite3.Row, str]] = []
    kept: list[sqlite3.Row] = []
    for row in settled:
        ex, reason = should_exclude(row)
        if ex:
            excluded.append((row, reason))
        else:
            kept.append(row)

    actual = compute_stats(settled)
    optimized = compute_stats(kept)
    excluded_stats = compute_stats([r for r, _ in excluded])

    saved_units = optimized["profit"] - actual["profit"]

    print("=" * 72)
    print("  SIMULACIJA OPTIMIZACIJE STRATEGIJE")
    print(f"  Baza: {DB_PATH}")
    print("=" * 72)
    print()
    print("Pravila isključenja:")
    print("  • BTTS No (market=btts, selection=No)")
    print("  • Under 2.5 sa kvotom > 2.0")
    print()

    print("-" * 72)
    print(f"  {'Metrika':<22} {'STVARNO':>14} {'OPTIMIZOVANO':>14} {'RAZLIKA':>12}")
    print("-" * 72)
    print(
        f"  {'Tipova (setlovano)':<22} {actual['n']:>14} {optimized['n']:>14} "
        f"{optimized['n'] - actual['n']:>+12}"
    )
    print(
        f"  {'Pobede / Gubici':<22} {actual['wins']}/{actual['losses']:>12} "
        f"{optimized['wins']}/{optimized['losses']:>12} "
        f"{'':>12}"
    )
    print(
        f"  {'Winrate':<22} {actual['winrate']:>13.1f}% {optimized['winrate']:>13.1f}% "
        f"{optimized['winrate'] - actual['winrate']:>+11.1f}%"
    )
    print(
        f"  {'Uloženo':<22} {actual['staked']:>13.2f}u {optimized['staked']:>13.2f}u "
        f"{optimized['staked'] - actual['staked']:>+11.2f}u"
    )
    print(
        f"  {'Profit':<22} {fmt_profit(actual['profit']):>14} "
        f"{fmt_profit(optimized['profit']):>14} "
        f"{fmt_profit(saved_units):>12}"
    )
    print(
        f"  {'ROI':<22} {actual['roi']:>+13.1f}% {optimized['roi']:>+13.1f}% "
        f"{optimized['roi'] - actual['roi']:>+11.1f}%"
    )
    print("-" * 72)
    print()
    print(f"  Sačuvano jedinica (optimizovano − stvarno): {fmt_profit(saved_units)}")
    print(f"  Isključeno tipova: {len(excluded)}  (profit tih tipova: {fmt_profit(excluded_stats['profit'])})")
    print()

    if excluded:
        print("=" * 72)
        print("  ISKLJUČENI TIPOVI (ne ulaze u optimizovane statistike)")
        print("=" * 72)
        for row, reason in excluded:
            outcome = row["outcome"] or "?"
            profit = row["profit_units"] or 0.0
            date = (row["pick_date"] or "")[:10]
            print(
                f"  [{date}] {row['market']}/{row['selection']} @ {row['odds']:.2f} "
                f"| {outcome.upper():4} | {fmt_profit(profit):>8} | {reason}"
            )
        print()

    print("=" * 72)
    print("  REZIME")
    print("=" * 72)
    print(f"  Stvarni profit:      {fmt_profit(actual['profit'])}  |  WR {actual['winrate']:.1f}%  |  ROI {actual['roi']:+.1f}%")
    print(
        f"  Optimizovani profit: {fmt_profit(optimized['profit'])}  |  "
        f"WR {optimized['winrate']:.1f}%  |  ROI {optimized['roi']:+.1f}%"
    )
    print(f"  Ušteda:                {fmt_profit(saved_units)}")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
