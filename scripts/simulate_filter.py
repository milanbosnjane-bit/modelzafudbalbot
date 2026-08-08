"""Simulacija: kakav bi bio ROI/winrate da je novi filter bio aktivan od početka.

Primenjuje SELECTION_QUALITY_FILTERS retroaktivno na setlovane tipove u bazi
i poredi rezultat sa stvarnim (nefiltriranim) rezultatima.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "football_roi.db"

# Iste vrednosti kao u app/predictions/pick_selector.py
SELECTION_QUALITY_FILTERS = {
    ("match_winner", "home"): {"min_ev": 0.08, "min_confidence": 0.62, "max_odds": 5.0},
    ("match_winner", "away"): {"min_ev": 0.08, "min_confidence": 0.62, "max_odds": 6.0},
}


def passes_filter(market: str, selection: str, ev: float, conf: float, odds: float) -> tuple[bool, str]:
    key = (market.strip().lower(), selection.strip().lower())
    rule = SELECTION_QUALITY_FILTERS.get(key)
    if rule is None:
        return True, "prošao (bez filtera)"
    if odds > rule["max_odds"]:
        return False, f"kvota {odds:.2f} > max {rule['max_odds']}"
    if ev < rule["min_ev"]:
        return False, f"EV {ev*100:.1f}% < min {rule['min_ev']*100:.0f}%"
    if conf < rule["min_confidence"]:
        return False, f"conf {conf:.3f} < min {rule['min_confidence']}"
    return True, "prošao"


def stats(picks: list) -> dict:
    if not picks:
        return {"n": 0, "wins": 0, "losses": 0, "profit": 0.0, "staked": 0.0, "wr": 0.0, "roi": 0.0}
    wins = sum(1 for p in picks if p["outcome"] == "win")
    losses = sum(1 for p in picks if p["outcome"] == "lose")
    profit = sum(p["profit_units"] or 0.0 for p in picks)
    staked = sum(p["stake_units"] or 0.0 for p in picks)
    wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0.0
    roi = profit / staked * 100 if staked > 0 else 0.0
    return {"n": len(picks), "wins": wins, "losses": losses, "profit": profit, "staked": staked, "wr": wr, "roi": roi}


def _bar(value: float, max_val: float, width: int = 20, fill: str = "█") -> str:
    filled = int(round(value / max_val * width)) if max_val else 0
    return fill * filled + "░" * (width - filled)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not DB_PATH.exists():
        print(f"[GRESKA] Baza ne postoji: {DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT dp.id, dp.pick_date, dp.market, dp.selection, dp.odds,
               dp.expected_value, dp.confidence,
               dp.outcome, dp.stake_units, dp.profit_units,
               th.name AS home, ta.name AS away,
               f.home_goals, f.away_goals
        FROM daily_picks dp
        JOIN fixtures f  ON f.id = dp.fixture_id
        JOIN teams th    ON th.id = f.home_team_id
        JOIN teams ta    ON ta.id = f.away_team_id
        WHERE dp.outcome IN ('win', 'lose', 'push')
        ORDER BY dp.pick_date ASC
    """).fetchall()
    conn.close()

    all_picks = [dict(r) for r in rows]

    passed_picks = []
    filtered_picks = []

    for p in all_picks:
        ev = p["expected_value"] or 0.0
        conf = p["confidence"] or 0.0
        ok, reason = passes_filter(p["market"], p["selection"], ev, conf, p["odds"])
        p["_filter_reason"] = reason
        if ok:
            passed_picks.append(p)
        else:
            filtered_picks.append(p)

    s_all = stats(all_picks)
    s_new = stats(passed_picks)

    SEP = "=" * 80

    print(f"\n{SEP}")
    print("  SIMULACIJA: NOVI FILTER vs. STVARNI REZULTATI")
    print(SEP)

    print(f"\n{'':4} {'Metrika':<22} {'BEZ filtera':>15} {'SA filterom':>15}  {'Razlika':>10}")
    print(f"  {'-'*65}")

    def row(label, a, b, fmt="{:.1f}", unit=""):
        diff = b - a
        sign = "+" if diff > 0 else ""
        print(f"  {'':2} {label:<22} {fmt.format(a):>14}{unit} {fmt.format(b):>14}{unit}  {sign}{fmt.format(diff):>9}{unit}")

    row("Tipova ukupno",   s_all["n"],      s_new["n"],     fmt="{:.0f}")
    row("Pobede",           s_all["wins"],   s_new["wins"],  fmt="{:.0f}")
    row("Porazi",           s_all["losses"], s_new["losses"],fmt="{:.0f}")
    row("Uloženo",          s_all["staked"], s_new["staked"],fmt="{:.2f}", unit="u")
    profit_diff = s_new["profit"] - s_all["profit"]
    sign = "+" if profit_diff >= 0 else ""
    print(f"  {'':2} {'Profit':<22} {s_all['profit']:>+14.2f}u {s_new['profit']:>+14.2f}u  {sign}{profit_diff:>9.2f}u")
    row("Winrate",          s_all["wr"],     s_new["wr"],    fmt="{:.1f}", unit="%")
    row("ROI",              s_all["roi"],    s_new["roi"],   fmt="{:.1f}", unit="%")

    # Vizualni bar chart winrate/ROI
    print(f"\n  Winrate:  BEZ  {_bar(s_all['wr'], 100)} {s_all['wr']:.1f}%")
    print(f"  Winrate:  SA   {_bar(s_new['wr'], 100)} {s_new['wr']:.1f}%")
    print()
    roi_max = max(abs(s_all["roi"]), abs(s_new["roi"]), 1)
    print(f"  ROI:      BEZ  {_bar(max(0, s_all['roi']), roi_max)} {s_all['roi']:+.1f}%")
    print(f"  ROI:      SA   {_bar(max(0, s_new['roi']), roi_max)} {s_new['roi']:+.1f}%")

    # Odbačeni tipovi
    print(f"\n{SEP}")
    print(f"  ODBAČENO FILTEROM — {len(filtered_picks)} tipova")
    print(SEP)
    s_filt = stats(filtered_picks)
    print(f"  (Da su ostali: {s_filt['wins']}W / {s_filt['losses']}L, "
          f"WR {s_filt['wr']:.1f}%, profit {s_filt['profit']:+.2f}u, ROI {s_filt['roi']:+.1f}%)\n")

    for p in filtered_picks:
        score = f"({p['home_goals']}-{p['away_goals']})" if p["home_goals"] is not None else "(NS)"
        icon = "✅" if p["outcome"] == "win" else "❌"
        ev_pct = (p["expected_value"] or 0.0) * 100
        conf = p["confidence"] or 0.0
        print(
            f"  {icon} {p['home']} vs {p['away']} {score}"
            f"  |  {p['market']} {p['selection']} @{p['odds']:.2f}"
            f"  |  EV {ev_pct:+.1f}%  conf {conf:.2f}"
            f"  |  {p['_filter_reason']}"
        )

    # Prošli tipovi po tipu selekcije
    print(f"\n{SEP}")
    print("  PROŠLI TIPOVI — PERFORMANS PO VRSTI")
    print(SEP)
    by_sel: dict[str, list] = {}
    for p in passed_picks:
        key = f"{p['market']} / {p['selection']}"
        by_sel.setdefault(key, []).append(p)

    print(f"  {'Tip':<28} {'UK':>4} {'W':>4} {'L':>4} {'WR%':>7}  {'Profit':>9}  {'ROI':>8}")
    print(f"  {'-'*70}")
    for key, ps in sorted(by_sel.items(), key=lambda x: -stats(x[1])["profit"]):
        s = stats(ps)
        sign = "+" if s["profit"] >= 0 else ""
        roi_sign = "+" if s["roi"] >= 0 else ""
        print(
            f"  {key:<28} {s['n']:>4} {s['wins']:>4} {s['losses']:>4} {s['wr']:>6.1f}%"
            f"  {sign}{s['profit']:>8.2f}u  {roi_sign}{s['roi']:>6.1f}%"
        )

    print(f"\n{SEP}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
