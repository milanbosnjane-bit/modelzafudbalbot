"""Detaljan izveštaj za A/B test minimalne kvote — čita JSON iz run_automated_tests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyze_backtest_errors import (
    aggregate_bucket,
    enrich_picks_with_league,
    market_label,
    odds_bucket,
)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Odds floor A/B report")
    p.add_argument("--input-dir", required=True, help="Folder sa JSON profilima")
    p.add_argument(
        "--profiles",
        nargs="*",
        default=None,
        help="Redosled profila (default: ODDS_FLOOR_PROFILE_ORDER)",
    )
    return p.parse_args()


def roi_pct(profit: float, staked: float) -> float:
    return (profit / staked * 100) if staked else 0.0


def print_breakdown(title: str, buckets: dict, *, min_n: int = 3) -> None:
    print(f"\n  --- {title} (N>={min_n}) ---")
    print(f"  {'Grupa':<34} {'N':>5} {'WR%':>7} {'ROI%':>8} {'Profit':>9}")
    items = [(k, v) for k, v in buckets.items() if v["n"] >= min_n]
    items.sort(key=lambda x: roi_pct(x[1]["profit"], x[1]["staked"]))
    for key, b in items:
        decisive = b["wins"] + b["losses"]
        wr = (b["wins"] / decisive * 100) if decisive else 0.0
        roi = roi_pct(b["profit"], b["staked"])
        sign = "+" if b["profit"] >= 0 else ""
        print(
            f"  {str(key)[:33]:<34} {b['n']:5d} {wr:6.1f}% {roi:+7.2f}% {sign}{b['profit']:8.2f}u"
        )


def worst_segments(picks: list[dict], *, min_n: int = 5) -> list[tuple[str, str, float, int]]:
    settled = [p for p in picks if p.get("outcome") in ("win", "lose", "push")]
    segments: list[tuple[str, str, float, int]] = []

    by_league = aggregate_bucket(
        settled,
        lambda p: f"{p.get('league_name', '?')}",
    )
    by_market = aggregate_bucket(
        settled,
        lambda p: market_label(p.get("market", ""), p.get("selection", "")),
    )
    by_odds = aggregate_bucket(settled, lambda p: odds_bucket(float(p.get("odds") or 0)))

    for label, buckets in (
        ("Liga", by_league),
        ("Market", by_market),
        ("Kvote", by_odds),
    ):
        for key, b in buckets.items():
            if b["n"] >= min_n and b["staked"]:
                segments.append((label, key, roi_pct(b["profit"], b["staked"]), b["n"]))

    segments.sort(key=lambda x: x[2])
    return segments


def sample_verdict(n: int) -> str:
    if n < 50:
        return "PREMALO — verovatno šum (N<50)"
    if n < 100:
        return "GRANIČNO — moguć šum (50–99 tipova)"
    if n < 300:
        return "UMEREN — statistički koristan, ali ne definitivan"
    return "DOVOLJAN — N>=300, pouzdaviji signal"


def load_profile(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    args = parse_args()
    folder = Path(args.input_dir)

    from scripts.ab_test_profiles import ODDS_FLOOR_PROFILE_ORDER

    profile_ids = args.profiles or ODDS_FLOOR_PROFILE_ORDER

    rows: list[dict] = []
    profiles_data: dict[str, dict] = {}

    print("=" * 96)
    print("  A/B TEST — MINIMALNA KVOTA (API-only, hronološki backtest, bez lookahead-a)")
    print("=" * 96)

    for pid in profile_ids:
        path = folder / f"{pid}.json"
        if not path.exists():
            print(f"  [SKIP] Nedostaje {path.name}")
            continue
        data = load_profile(path)
        profiles_data[pid] = data
        m = data["metrics"]
        period = data.get("period", {})
        rows.append({
            "profile_id": pid,
            "label": data.get("label", pid),
            "n": m["total_bets"],
            "profit": m["total_profit"],
            "roi": m["roi_pct"],
            "wr": m["win_rate"] * 100,
            "avg_odds": m.get("avg_odds", 0),
            "clv": m["avg_clv"],
            "max_dd": m.get("max_drawdown_units", 0),
            "period": period,
        })

    if not rows:
        print("Nema učitanih profila.")
        return 1

    period = rows[0].get("period", {})
    print(
        f"\nPeriod: {period.get('start', '?')} → {period.get('end', '?')}"
    )
    print("Metod: walk-forward backtest, decision T-1h, slippage 1.5%, legacy isključen, UCL excluded")

    print(f"\n{'Profil':<22} {'N':>6} {'Profit':>9} {'ROI%':>8} {'WR%':>7} "
          f"{'AvgOdds':>8} {'CLV':>8} {'MaxDD':>8}")
    print("-" * 96)
    for r in rows:
        sign = "+" if r["profit"] >= 0 else ""
        print(
            f"{r['label']:<22} {r['n']:6d} {sign}{r['profit']:8.2f}u {r['roi']:+7.2f}% "
            f"{r['wr']:6.1f}% {r['avg_odds']:8.2f} {r['clv']:+7.4f} {r['max_dd']:7.2f}u"
        )

    baseline = next((r for r in rows if r["profile_id"] == "baseline"), rows[0])
    best = max(rows, key=lambda x: x["roi"])
    print(f"\nΔ ROI vs baseline:")
    for r in rows:
        delta = r["roi"] - baseline["roi"]
        print(f"  {r['label']:<22} {delta:+7.2f} pp  |  uzorak: {sample_verdict(r['n'])}")

    for pid in profile_ids:
        data = profiles_data.get(pid)
        if not data:
            continue
        picks = enrich_picks_with_league(data.get("picks", []))
        settled = [p for p in picks if p.get("outcome") in ("win", "lose", "push")]
        m = data["metrics"]

        print(f"\n{'#' * 96}")
        print(f"  PROFIL: {data.get('label')} ({pid}) — {m['total_bets']} tipova")
        print(f"{'#' * 96}")

        by_market = aggregate_bucket(
            settled,
            lambda p: market_label(p.get("market", ""), p.get("selection", "")),
        )
        by_league = aggregate_bucket(
            settled,
            lambda p: f"{p.get('league_name', '?')}",
        )
        by_odds = aggregate_bucket(settled, lambda p: odds_bucket(float(p.get("odds") or 0)))

        print_breakdown("ROI po marketu", by_market, min_n=3)
        print_breakdown("ROI po ligi", by_league, min_n=5)
        print_breakdown("ROI po opsegu kvota", by_odds, min_n=3)

    # Global worst segments from baseline
    base_data = profiles_data.get("baseline")
    if base_data:
        worst = worst_segments(base_data.get("picks", []), min_n=5)[:8]
        print(f"\n{'=' * 96}")
        print("  GDE BOT NAJVIŠE GUBI (baseline, segmenti N>=5, sortirano po ROI)")
        print(f"{'=' * 96}")
        for kind, name, roi, n in worst:
            print(f"  [{kind}] {name}: ROI {roi:+.1f}% ({n} tipova)")

    print(f"\n{'=' * 96}")
    print("  ZAKLJUČAK")
    print(f"{'=' * 96}")
    print(f"  Najbolji ROI profil: {best['label']} ({best['roi']:+.2f}%, N={best['n']})")
    print(f"  Baseline:            {baseline['label']} ({baseline['roi']:+.2f}%, N={baseline['n']})")
    print(f"  Uzorak baseline:     {sample_verdict(baseline['n'])}")
    print(f"  Uzorak najboljeg:    {sample_verdict(best['n'])}")

    if best["n"] < 100:
        print(
            "\n  ⚠ Pažnja: profili sa visokim min kvotom imaju mali uzorak — "
            "pozitivan ROI može biti varijansa, ne stabilna prednost."
        )
    elif best["profile_id"] == "baseline":
        print(
            "\n  → Dodatni floor kvote ne poboljšavaju rezultat na ovom periodu; "
            "baseline je dovoljno dobar ili manje loš."
        )
    else:
        print(
            f"\n  → {best['label']} pobedjuje na ovom periodu, ali proveri N i max drawdown "
            f"pre promene live config-a."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
