"""Pokreni walk-forward backtest na podacima iz baze."""
import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/football_roi.db")
os.environ.setdefault("DATABASE_URL_SYNC", "sqlite:///./data/football_roi.db")
os.environ.setdefault("LOCAL_MODE", "true")
os.environ.setdefault("USE_MEMORY_CACHE", "true")
os.environ.setdefault("APP_DEBUG", "false")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Walk-forward backtest na football_roi.db")
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Početni datum YYYY-MM-DD (default: poslednjih 730 dana)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="Krajnji datum YYYY-MM-DD (default: danas)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=730,
        help="Koliko dana unazad ako --start nije zadat (default: 730 = ~2 godine OOS)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="manual_cli_backtest",
        help="Ime backtest run-a u bazi",
    )
    parser.add_argument(
        "--include-legacy",
        action="store_true",
        help="Uključi football-data istoriju (podrazumevano ISKLJUČENA)",
    )
    return parser.parse_args()


def db_stats(exclude_legacy: bool = True):
    conn = sqlite3.connect("data/football_roi.db")
    c = conn.cursor()
    c.execute(
        "SELECT MIN(date(fixture_date)), MAX(date(fixture_date)), COUNT(*) "
        "FROM fixtures WHERE status IN ('FT','AET','PEN')"
    )
    ft = c.fetchone()
    if exclude_legacy:
        c.execute(
            """
            SELECT COUNT(DISTINCT f.id)
            FROM fixtures f
            JOIN odds_snapshots o ON o.fixture_id = f.id
            WHERE f.status IN ('FT','AET','PEN')
              AND o.bookmaker NOT IN ('football-data', 'football-data-ref')
            """
        )
        api_ft = c.fetchone()[0]
    else:
        api_ft = ft[2]
    c.execute("SELECT COUNT(*) FROM odds_snapshots")
    odds = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM feature_vectors")
    feats = c.fetchone()[0]
    conn.close()
    return ft, odds, feats, api_ft


async def main():
    args = parse_args()
    exclude_legacy = not args.include_legacy
    ft, odds, feats, api_ft = db_stats(exclude_legacy=exclude_legacy)
    print("=== PODACI U BAZI ===")
    print(f"  FT mečevi (sve):     {ft[0]} → {ft[1]}  (ukupno {ft[2]})")
    if exclude_legacy:
        print(f"  FT sa API kvotama:    {api_ft}  (legacy football-data ISKLJUČEN)")
    else:
        print("  Režim:               legacy + API (football-data uključen)")
    print(f"  odds_snapshots:      {odds}")
    print(f"  feature_vectors:     {feats}")
    print()

    if exclude_legacy and api_ft < 10:
        print("Premalo API mečeva za backtest (bez legacy istorije).")
        print("Povuci podatke preko API-ja ili koristi --include-legacy.")
        return

    if not ft[0] or ft[2] < 10:
        print("Premalo FT mečeva za backtest.")
        return

    data_end = datetime.strptime(ft[1], "%Y-%m-%d")
    if args.end:
        end = datetime.strptime(args.end, "%Y-%m-%d").replace(hour=23, minute=59)
    else:
        end = data_end.replace(hour=23, minute=59)

    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d")
    else:
        start = (end - timedelta(days=args.days)).replace(hour=0, minute=0, second=0)

    from app.database.session import init_db
    from app.training.backtest import BacktestEngine

    await init_db()

    print(f"=== BACKTEST {start.date()} → {end.date()} ===")
    if exclude_legacy:
        print("Izvor: samo API kvote (Bet365, itd.) — football-data istorija isključena")
    print("Pokrećem (može trajati nekoliko minuta)...")
    print()

    engine = BacktestEngine(exclude_legacy=exclude_legacy)
    result = await engine.run(start, end, name=args.name)

    print("=== REZULTAT ===")
    print(f"  Ukupno opklada:  {result.total_bets}")
    print(f"  Uloženo:         {result.total_staked:.2f}u")
    print(f"  Profit:          {result.total_profit:+.2f}u")
    print(f"  ROI:             {result.roi_pct:+.2f}%")
    print(f"  Winrate:         {result.win_rate:.1%}")
    print(f"  Prosečan EV:     {result.avg_ev:.1%}")
    print(f"  Prosečan CLV:    {result.avg_clv:.4f}")
    print(f"  Sharpe:          {result.sharpe_ratio:.2f}")
    print(f"  CLV coverage:    {result.clv_coverage_pct:.0%}")

    if result.picks:
        wins = sum(1 for p in result.picks if p["outcome"] == "win")
        losses = sum(1 for p in result.picks if p["outcome"] == "lose")
        pushes = sum(1 for p in result.picks if p["outcome"] == "push")
        print(f"  W/L/P:           {wins}/{losses}/{pushes}")

        by_market: dict[str, dict] = {}
        for p in result.picks:
            key = f"{p['market']}/{p['selection']}"
            bucket = by_market.setdefault(key, {"n": 0, "profit": 0.0, "staked": 0.0, "wins": 0})
            bucket["n"] += 1
            bucket["profit"] += p["profit"]
            bucket["staked"] += p["stake"]
            if p["outcome"] == "win":
                bucket["wins"] += 1

        print()
        print("=== PO TRŽIŠTU (top 8 po broju tipova) ===")
        ranked = sorted(by_market.items(), key=lambda x: x[1]["n"], reverse=True)[:8]
        for key, s in ranked:
            roi_m = (s["profit"] / s["staked"] * 100) if s["staked"] else 0
            wr = s["wins"] / s["n"] if s["n"] else 0
            print(f"  {key:22} n={s['n']:4}  ROI={roi_m:+6.1f}%  WR={wr:.0%}")

        print()
        print("=== POSLEDNJIH 10 TIPOVA ===")
        for p in result.picks[-10:]:
            print(
                f"  {p['date']} | {p['market']}/{p['selection']} @ {p['odds']:.2f} "
                f"| EV:{p['ev']:.1%} | {p['outcome']} | {p['profit']:+.2f}u"
            )

    print()
    print("Sačuvano u backtest_runs tabeli.")


if __name__ == "__main__":
    asyncio.run(main())
