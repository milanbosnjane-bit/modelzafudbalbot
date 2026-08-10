"""Out-of-sample walk-forward backtest + calibration evaluation suite.

Simulira izbor tipova nad istorijskim FT mečevima sa kvotama, koristeći
ista produkciona pravila kao live bot (PickSelectionEngine / BacktestEngine).

Primeri:
  python scripts/run_backtest.py
  python scripts/run_backtest.py --days 180
"""
from __future__ import annotations

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
# Production shrink (env overrides Settings default)
os.environ["PROBABILITY_SHRINK_WEIGHT"] = "0.55"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

INITIAL_BANKROLL = 100.0
KELLY_FRACTION = 0.25
MAX_STAKE_PCT = 0.02


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Walk-forward OOS backtest + calibration evaluation"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Poslednjih N dana (bez argumenata = kompletna dostupna istorija)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Početni datum YYYY-MM-DD (opciono)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="Krajnji datum YYYY-MM-DD (opciono)",
    )
    parser.add_argument(
        "--include-legacy",
        action="store_true",
        help="Uključi football-data istoriju (podrazumevano isključena)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="oos_eval_suite",
        help="Ime backtest run-a u bazi",
    )
    return parser.parse_args()


def db_stats(*, exclude_legacy: bool) -> dict:
    conn = sqlite3.connect("data/football_roi.db")
    c = conn.cursor()
    c.execute(
        "SELECT MIN(date(fixture_date)), MAX(date(fixture_date)), COUNT(*) "
        "FROM fixtures WHERE status IN ('FT','AET','PEN') AND home_goals IS NOT NULL"
    )
    ft_min, ft_max, ft_n = c.fetchone()
    if exclude_legacy:
        c.execute(
            """
            SELECT MIN(date(f.fixture_date)), MAX(date(f.fixture_date)), COUNT(DISTINCT f.id)
            FROM fixtures f
            JOIN odds_snapshots o ON o.fixture_id = f.id
            WHERE f.status IN ('FT','AET','PEN')
              AND f.home_goals IS NOT NULL
              AND o.bookmaker NOT IN ('football-data', 'football-data-ref')
            """
        )
        api_min, api_max, api_n = c.fetchone()
    else:
        api_min, api_max, api_n = ft_min, ft_max, ft_n
    conn.close()
    return {
        "ft_min": ft_min,
        "ft_max": ft_max,
        "ft_n": ft_n or 0,
        "api_min": api_min,
        "api_max": api_max,
        "api_n": api_n or 0,
    }


def fractional_kelly(prob: float, odds: float, bankroll: float) -> float:
    if odds <= 1.0 or bankroll <= 0:
        return 0.0
    b = odds - 1.0
    q = 1.0 - prob
    full = ((prob * b) - q) / b if b > 0 else 0.0
    raw = max(0.0, full) * KELLY_FRACTION * bankroll
    return min(raw, bankroll * MAX_STAKE_PCT)


def simulate_bankroll(picks: list[dict], initial: float = INITIAL_BANKROLL) -> dict:
    """Chronological bankroll path with 1/4-Kelly, max 2% stake."""
    ordered = sorted(
        picks,
        key=lambda p: (p.get("date", ""), p.get("fixture_id", 0)),
    )
    bankroll = initial
    peak = initial
    max_dd_units = 0.0
    max_dd_pct = 0.0
    streak = 0
    max_losing_streak = 0
    path: list[tuple[str, float]] = []

    for pick in ordered:
        odds = float(pick.get("effective_odds") or pick.get("odds") or 0.0)
        prob = float(pick.get("probability") or 0.0)
        stake = fractional_kelly(prob, odds, bankroll)
        if stake <= 0:
            continue

        outcome = pick.get("outcome")
        if outcome == "win":
            pnl = stake * (odds - 1.0)
            streak = 0
        elif outcome == "lose":
            pnl = -stake
            streak += 1
            max_losing_streak = max(max_losing_streak, streak)
        else:
            pnl = 0.0

        bankroll += pnl
        peak = max(peak, bankroll)
        dd_units = peak - bankroll
        dd_pct = (dd_units / peak * 100.0) if peak > 0 else 0.0
        max_dd_units = max(max_dd_units, dd_units)
        max_dd_pct = max(max_dd_pct, dd_pct)
        path.append((str(pick.get("date")), bankroll))

    return {
        "initial": initial,
        "final": bankroll,
        "profit": bankroll - initial,
        "max_drawdown_units": max_dd_units,
        "max_drawdown_pct": max_dd_pct,
        "max_losing_streak": max_losing_streak,
        "path": path,
    }


def market_segments(picks: list[dict]) -> dict[str, dict]:
    by: dict[str, dict] = {}
    for p in picks:
        m = p.get("market") or "unknown"
        bucket = by.setdefault(
            m, {"n": 0, "wins": 0, "staked": 0.0, "profit": 0.0, "probs": [], "hits": []}
        )
        bucket["n"] += 1
        bucket["staked"] += float(p.get("stake") or 0.0)
        bucket["profit"] += float(p.get("profit") or 0.0)
        if p.get("outcome") == "win":
            bucket["wins"] += 1
            bucket["hits"].append(1.0)
        elif p.get("outcome") == "lose":
            bucket["hits"].append(0.0)
        if p.get("probability") is not None and p.get("outcome") in ("win", "lose"):
            bucket["probs"].append(float(p["probability"]))
    return by


def selection_label(pick: dict) -> str:
    market = (pick.get("market") or "").lower()
    sel = (pick.get("selection") or "").lower().strip()
    if market == "btts":
        return "BTTS Yes" if "yes" in sel else f"BTTS {pick.get('selection')}"
    if market == "match_winner":
        if sel in {"home", "1", "h"}:
            return "Home"
        if sel in {"away", "2", "a"}:
            return "Away"
        if sel in {"draw", "x", "tie"}:
            return "Draw"
    return f"{pick.get('market')}/{pick.get('selection')}"


def selection_segments(picks: list[dict]) -> dict[str, dict]:
    by: dict[str, dict] = {}
    for p in picks:
        key = selection_label(p)
        bucket = by.setdefault(key, {"n": 0, "wins": 0, "staked": 0.0, "profit": 0.0})
        bucket["n"] += 1
        bucket["staked"] += float(p.get("stake") or 0.0)
        bucket["profit"] += float(p.get("profit") or 0.0)
        if p.get("outcome") == "win":
            bucket["wins"] += 1
    return by


def _print_segment_row(label: str, s: dict | None) -> None:
    if not s:
        print(f"  {label:16}  (no picks)")
        return
    roi = (s["profit"] / s["staked"] * 100.0) if s["staked"] else 0.0
    wr = (s["wins"] / s["n"] * 100.0) if s["n"] else 0.0
    print(
        f"  {label:16}  n={s['n']:4}  WR={wr:5.1f}%  "
        f"staked={s['staked']:.1f}u  P/L={s['profit']:+.2f}u  ROI={roi:+.1f}%"
    )


def calibration_metrics(picks: list[dict]) -> dict:
    settled = [p for p in picks if p.get("outcome") in ("win", "lose")]
    if not settled:
        return {
            "n": 0,
            "avg_model_prob": 0.0,
            "actual_hit_rate": 0.0,
            "overconfidence_gap_pp": 0.0,
            "brier_score": 0.0,
        }
    probs = [float(p["probability"]) for p in settled]
    hits = [1.0 if p["outcome"] == "win" else 0.0 for p in settled]
    avg_prob = sum(probs) / len(probs)
    hit_rate = sum(hits) / len(hits)
    brier = sum((p - h) ** 2 for p, h in zip(probs, hits)) / len(probs)
    return {
        "n": len(settled),
        "avg_model_prob": avg_prob,
        "actual_hit_rate": hit_rate,
        "overconfidence_gap_pp": (avg_prob - hit_rate) * 100.0,
        "brier_score": brier,
    }


def print_report(
    *,
    start: datetime,
    end: datetime,
    stats: dict,
    result,
    bankroll: dict,
    calib: dict,
    segments: dict[str, dict],
    sel_segments: dict[str, dict],
    settings,
) -> None:
    print()
    print("=" * 64)
    print("  OOS WALK-FORWARD BACKTEST & CALIBRATION EVAL")
    print("=" * 64)
    print(f"  Period:              {start.date()} → {end.date()}")
    print(f"  API FT fixtures:     {stats['api_n']}  ({stats['api_min']} → {stats['api_max']})")
    from app.predictions.pick_selector import (
        DEFAULT_MAX_ODDS,
        DRAW_MAX_ODDS,
        GLOBAL_MIN_ODDS,
        PickSelectionEngine,
    )

    print(f"  PICK_MARKETS:        {sorted(PickSelectionEngine.PICK_MARKETS)}")
    print(f"  shrink_weight:       {settings.probability_shrink_weight}")
    print(
        f"  odds floor/caps:     min={GLOBAL_MIN_ODDS:.2f}  "
        f"H/A/BTTS max={DEFAULT_MAX_ODDS:.2f}  Draw max={DRAW_MAX_ODDS:.2f}"
    )
    print(f"  max_day / kelly:     {settings.max_daily_picks} / {settings.kelly_fraction}")
    print()

    print("--- FINANCIALS ---")
    print(f"  Total picks:         {result.total_bets}")
    print(f"  Win rate:            {result.win_rate * 100:.1f}%")
    print(f"  Total staked:        {result.total_staked:.2f} u")
    print(f"  Total P/L:           {result.total_profit:+.2f} u")
    print(f"  ROI:                 {result.roi_pct:+.2f}%")
    print(f"  Avg EV (promised):   {result.avg_ev * 100:+.1f}%")
    print(f"  Sharpe (daily):      {result.sharpe_ratio:.2f}")
    print()

    print("--- BY SELECTION (Home / Away / Draw / BTTS) ---")
    for label in ("Home", "Away", "Draw", "BTTS Yes"):
        _print_segment_row(label, sel_segments.get(label))
    extra = [k for k in sel_segments if k not in ("Home", "Away", "Draw", "BTTS Yes")]
    for label in extra:
        _print_segment_row(label, sel_segments[label])
    print()

    print("--- BY MARKET ---")
    for market in ("match_winner", "btts"):
        _print_segment_row(market, segments.get(market))
    other = [m for m in segments if m not in ("match_winner", "btts")]
    for market in other:
        _print_segment_row(f"{market} (unexpected)", segments[market])
    print()

    print("--- MODEL CALIBRATION ---")
    print(f"  Settled sample:      {calib['n']}")
    print(f"  Avg model prob:      {calib['avg_model_prob'] * 100:.1f}%")
    print(f"  Actual hit rate:     {calib['actual_hit_rate'] * 100:.1f}%")
    print(f"  Overconfidence gap:  {calib['overconfidence_gap_pp']:+.1f} pp")
    brier_ok = "OK" if calib["brier_score"] < 0.20 else "HIGH"
    print(f"  Brier score:         {calib['brier_score']:.4f}  (target < 0.20, {brier_ok})")
    print()

    print("--- BANKROLL SIMULATION ---")
    print(f"  Start bankroll:      {bankroll['initial']:.2f} u")
    print(f"  Final bankroll:      {bankroll['final']:.2f} u")
    print(f"  Bankroll P/L:        {bankroll['profit']:+.2f} u")
    print(
        f"  Max drawdown:        {bankroll['max_drawdown_units']:.2f} u "
        f"({bankroll['max_drawdown_pct']:.1f}%)"
    )
    print(f"  Max losing streak:   {bankroll['max_losing_streak']}")
    print("=" * 64)


async def main() -> None:
    args = parse_args()
    exclude_legacy = not args.include_legacy
    stats = db_stats(exclude_legacy=exclude_legacy)

    print("=== DATA ===")
    print(f"  FT all:              {stats['ft_min']} → {stats['ft_max']}  (n={stats['ft_n']})")
    if exclude_legacy:
        print(
            f"  FT + API odds:       {stats['api_min']} → {stats['api_max']}  "
            f"(n={stats['api_n']}; legacy excluded)"
        )
    else:
        print("  Mode:                legacy + API")

    if stats["api_n"] < 10:
        print("Premalo mečeva sa API kvotama za backtest.")
        return

    data_end = datetime.strptime(stats["api_max"], "%Y-%m-%d")
    data_start = datetime.strptime(stats["api_min"], "%Y-%m-%d")

    if args.end:
        end = datetime.strptime(args.end, "%Y-%m-%d").replace(hour=23, minute=59)
    else:
        end = data_end.replace(hour=23, minute=59)

    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d")
    elif args.days is not None:
        start = (end - timedelta(days=args.days)).replace(hour=0, minute=0, second=0)
    else:
        start = data_start.replace(hour=0, minute=0, second=0)

    from app.config import get_settings
    from app.database.session import init_db
    from app.predictions.pick_selector import PickSelectionEngine
    from app.training.backtest import BacktestEngine

    get_settings.cache_clear()
    settings = get_settings()

    assert PickSelectionEngine.PICK_MARKETS == frozenset({"match_winner", "btts"})
    if abs(settings.probability_shrink_weight - 0.55) > 1e-9:
        print(
            f"[WARN] probability_shrink_weight={settings.probability_shrink_weight} "
            "(expected 0.55)"
        )

    await init_db()

    print()
    print(f"=== RUNNING BACKTEST {start.date()} → {end.date()} ===")
    print("  Rules: production PickSelectionEngine (OU paused, shrink 0.55)")
    print("  (može potrajati nekoliko minuta...)")
    print()

    engine = BacktestEngine(exclude_legacy=exclude_legacy)
    result = await engine.run(start, end, name=args.name)

    segments = market_segments(result.picks)
    sel_segments = selection_segments(result.picks)
    calib = calibration_metrics(result.picks)
    bankroll = simulate_bankroll(result.picks, INITIAL_BANKROLL)

    print_report(
        start=start,
        end=end,
        stats=stats,
        result=result,
        bankroll=bankroll,
        calib=calib,
        segments=segments,
        sel_segments=sel_segments,
        settings=settings,
    )

    if result.picks:
        print()
        print("--- LAST 10 PICKS ---")
        for p in result.picks[-10:]:
            print(
                f"  {p['date']} | {p['market']}/{p['selection']} @ {p['odds']:.2f} "
                f"| p={p['probability']:.1%} EV={p['ev']:.1%} | {p['outcome']} "
                f"| {p['profit']:+.2f}u"
            )


if __name__ == "__main__":
    asyncio.run(main())
