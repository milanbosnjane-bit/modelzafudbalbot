"""Jedan izolovan backtest profil — pokreće se iz run_automated_tests (subprocess)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backtest worker (single A/B profile)")
    p.add_argument("--profile", required=True, help="Profile ID iz ab_test_profiles.py")
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--output", required=True, help="Putanja za JSON izlaz")
    return p.parse_args()


def apply_runtime_patches(patches: dict) -> list[str]:
    """Runtime monkeypatch — ne menja source fajlove u app/predictions/."""
    applied: list[str] = []
    import app.predictions.pick_selector as pick_selector

    sel_filters = patches.get("selection_quality_filters")
    if sel_filters:
        pick_selector.SELECTION_QUALITY_FILTERS = sel_filters
        applied.append("selection_quality_filters")

    global_min_odds = patches.get("global_min_odds")
    global_min_ev = patches.get("global_min_ev")
    if global_min_odds is not None or global_min_ev is not None:
        original_fn = pick_selector.passes_selection_filter

        def patched_selection_filter(candidate):
            ok, reason = original_fn(candidate)
            if not ok:
                return ok, reason
            if global_min_odds is not None and candidate.odds < global_min_odds:
                return False, f"global_min_odds ({candidate.odds:.2f} < {global_min_odds})"
            if global_min_ev is not None and candidate.ensemble.expected_value < global_min_ev:
                return False, (
                    f"global_min_ev ({candidate.ensemble.expected_value:.3f} < {global_min_ev})"
                )
            return True, None

        pick_selector.passes_selection_filter = patched_selection_filter
        applied.append(
            f"global_min_odds={global_min_odds},global_min_ev={global_min_ev}"
        )

    return applied


def compute_max_drawdown(picks: list[dict]) -> float:
    """Peak-to-trough pad kumulativnog profita (hronološki)."""
    ordered = sorted(picks, key=lambda p: (p.get("date", ""), p.get("fixture_id", 0)))
    peak = 0.0
    cumulative = 0.0
    max_dd = 0.0
    for pick in ordered:
        cumulative += float(pick.get("profit") or 0.0)
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return round(max_dd, 4)


def compute_avg_odds(picks: list[dict]) -> float:
    odds = [float(p["odds"]) for p in picks if p.get("odds")]
    return round(sum(odds) / len(odds), 4) if odds else 0.0


async def run_backtest(
    *,
    start: datetime,
    end: datetime,
    decision_hours: float,
    exclude_legacy: bool,
    profile_id: str,
    profile_meta: dict,
) -> dict:
    from app.config import get_settings
    from app.database.session import init_db
    from app.training.backtest import BacktestEngine

    get_settings.cache_clear()
    settings = get_settings()

    await init_db()

    engine = BacktestEngine(
        decision_hours=decision_hours,
        exclude_legacy=exclude_legacy,
    )
    result = await engine.run(start, end, name=f"ab_{profile_id}")

    wins = sum(1 for p in result.picks if p["outcome"] == "win")
    losses = sum(1 for p in result.picks if p["outcome"] == "lose")
    pushes = sum(1 for p in result.picks if p["outcome"] == "push")

    max_drawdown = compute_max_drawdown(result.picks)
    avg_odds = compute_avg_odds(result.picks)

    return {
        "profile_id": profile_id,
        "label": profile_meta.get("label"),
        "description": profile_meta.get("description"),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "config": {
            "decision_hours": decision_hours,
            "exclude_legacy": exclude_legacy,
            "env": profile_meta.get("env", {}),
            "patches_applied": profile_meta.get("_patches_applied", []),
            "poisson_only_mode": settings.poisson_only_mode,
            "context_gates_enabled": settings.context_gates_enabled,
            "market_confirmation_gate_enabled": settings.market_confirmation_gate_enabled,
            "min_team_xg_threshold": settings.min_team_xg_threshold,
        },
        "metrics": {
            "total_bets": result.total_bets,
            "total_staked": round(result.total_staked, 4),
            "total_profit": round(result.total_profit, 4),
            "roi_pct": round(result.roi_pct, 4),
            "win_rate": round(result.win_rate, 6),
            "avg_ev": round(result.avg_ev, 6),
            "avg_clv": round(result.avg_clv, 6),
            "avg_odds": avg_odds,
            "max_drawdown_units": max_drawdown,
            "sharpe_ratio": round(result.sharpe_ratio, 4),
            "clv_coverage_pct": round(result.clv_coverage_pct, 6),
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
        },
        "picks": result.picks,
        "run_at": datetime.utcnow().isoformat(),
    }


def main() -> int:
    args = parse_args()

    from scripts.ab_test_profiles import AB_PROFILES, BASE_ENV

    if args.profile not in AB_PROFILES:
        print(f"Nepoznat profil: {args.profile}", file=sys.stderr)
        return 1

    profile = AB_PROFILES[args.profile]

    # Env mora biti postavljen PRE importa app modula u ovom subprocess-u
    for k, v in BASE_ENV.items():
        os.environ[k] = str(v)
    for k, v in profile.get("env", {}).items():
        os.environ[k] = str(v)

    patches = profile.get("patches", {})
    profile["_patches_applied"] = apply_runtime_patches(patches)

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(hour=23, minute=59)
    bt_cfg = profile.get("backtest", {})

    payload = asyncio.run(
        run_backtest(
            start=start,
            end=end,
            decision_hours=float(bt_cfg.get("decision_hours", 1.0)),
            exclude_legacy=bool(bt_cfg.get("exclude_legacy", True)),
            profile_id=args.profile,
            profile_meta=profile,
        )
    )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    m = payload["metrics"]
    print(
        f"[{payload['label']}] bets={m['total_bets']} "
        f"ROI={m['roi_pct']:+.2f}% WR={m['win_rate']:.1%} "
        f"CLV={m['avg_clv']:+.4f} -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
