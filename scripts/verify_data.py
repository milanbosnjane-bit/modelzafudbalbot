"""Verifikacija daily_picks iz lokalne SQLite baze."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "football_roi.db"

QUERY = """
    SELECT pick_date, selection, odds, outcome, stake_units, profit_units
    FROM daily_picks
    ORDER BY pick_date ASC, id ASC
"""


def _fmt_date(value: str) -> str:
    if not value:
        return "?"
    return value.replace("T", " ")[:16]


def _fmt_profit(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not DB_PATH.exists():
        print(f"[GRESKA] Baza ne postoji: {DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(QUERY).fetchall()
    conn.close()

    if not rows:
        print("Nema zapisa u daily_picks.")
        return 0

    print(f"Baza: {DB_PATH}")
    print(f"Ukupno redova: {len(rows)}\n")
    print("-" * 90)

    wins = losses = pushes = voids = pending = 0
    total_profit = 0.0
    profit_count = 0

    for row in rows:
        outcome = row["outcome"] or "pending"
        profit = row["profit_units"]

        print(
            f"[{_fmt_date(row['pick_date'])}] | "
            f"Tip: {row['selection']} | "
            f"Kvota: {row['odds']:.2f} | "
            f"Ishod: {outcome} | "
            f"Ulog: {row['stake_units']:.2f} | "
            f"Profit: {_fmt_profit(profit)}"
        )

        if outcome == "win":
            wins += 1
        elif outcome == "lose":
            losses += 1
        elif outcome == "push":
            pushes += 1
        elif outcome == "void":
            voids += 1
        else:
            pending += 1

        if profit is not None:
            total_profit += profit
            profit_count += 1

    print("-" * 90)
    print("\n=== REZIME ===")
    print(f"Ukupno mečeva:     {len(rows)}")
    print(f"Pobede:            {wins}")
    print(f"Porazi:            {losses}")
    if pushes:
        print(f"Push:              {pushes}")
    if voids:
        print(f"Void:              {voids}")
    if pending:
        print(f"Na čekanju:        {pending}")
    print(f"Ukupan profit:     {_fmt_profit(total_profit)}u")
    if profit_count < len(rows):
        print(
            f"(profit sabran za {profit_count} redova sa profit_units; "
            f"{len(rows) - profit_count} bez vrednosti)"
        )

    settled = wins + losses + pushes
    if settled and (wins + losses) > 0:
        winrate = wins / (wins + losses) * 100
        print(f"Winrate (W/L):     {winrate:.1f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
