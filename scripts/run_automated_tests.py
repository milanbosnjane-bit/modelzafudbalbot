"""
Sistematski A/B backtest na 2025 (API-only).

Svaki profil se pokreće u izolovanom subprocess-u da nema curenja config-a.
Rezultati: data/ab_tests/<timestamp>/<profile_id>.json + summary.csv

Ne menja app/predictions/ — TEST A koristi runtime monkeypatch u worker-u.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

WORKER = PROJECT_ROOT / "scripts" / "_backtest_worker.py"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="A/B backtest runner (2025 API-only)")
    p.add_argument("--start", default="2025-01-01", help="YYYY-MM-DD")
    p.add_argument("--end", default="2025-12-31", help="YYYY-MM-DD")
    p.add_argument(
        "--profiles",
        nargs="*",
        default=None,
        help="Lista profila (default: svi iz DEFAULT_PROFILE_ORDER)",
    )
    p.add_argument(
        "--only",
        default=None,
        help="Pokreni samo jedan profil (npr. baseline)",
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help="Izlazni folder (default: data/ab_tests/<timestamp>)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Ispiši plan bez pokretanja backtesta",
    )
    return p.parse_args()


def load_profile_order(only: str | None, profiles: list[str] | None) -> list[str]:
    from scripts.ab_test_profiles import AB_PROFILES, DEFAULT_PROFILE_ORDER

    if only:
        if only not in AB_PROFILES:
            raise SystemExit(f"Nepoznat profil: {only}. Dostupno: {', '.join(AB_PROFILES)}")
        return [only]
    if profiles:
        unknown = [p for p in profiles if p not in AB_PROFILES]
        if unknown:
            raise SystemExit(f"Nepoznati profili: {unknown}")
        return profiles
    return list(DEFAULT_PROFILE_ORDER)


def run_profile(
    profile_id: str,
    *,
    start: str,
    end: str,
    output_path: Path,
) -> int:
    cmd = [
        sys.executable,
        str(WORKER),
        "--profile",
        profile_id,
        "--start",
        start,
        "--end",
        end,
        "--output",
        str(output_path),
    ]
    print(f"\n>>> Pokrećem: {profile_id}")
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return proc.returncode


def write_summary_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    fieldnames = [
        "profile_id",
        "label",
        "description",
        "total_bets",
        "roi_pct",
        "win_rate",
        "total_profit",
        "total_staked",
        "avg_clv",
        "avg_ev",
        "sharpe_ratio",
        "clv_coverage_pct",
        "delta_roi_vs_baseline",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def compare_to_baseline(rows: list[dict]) -> None:
    baseline = next((r for r in rows if r["profile_id"] == "baseline"), None)
    if not baseline:
        return
    base_roi = baseline["roi_pct"]
    print("\n=== POREĐENJE SA BASELINE ===")
    print(f"BASELINE ROI: {base_roi:+.2f}% ({baseline['total_bets']} tipova)")
    print(f"{'Profil':<28} {'ROI':>8} {'Δ ROI':>8} {'WR':>7} {'Bets':>6} {'CLV':>8}")
    print("-" * 72)
    for r in sorted(rows, key=lambda x: x["roi_pct"], reverse=True):
        delta = r["roi_pct"] - base_roi
        print(
            f"{r['profile_id']:<28} {r['roi_pct']:+7.2f}% {delta:+7.2f}pp "
            f"{r['win_rate']*100:6.1f}% {r['total_bets']:6d} {r['avg_clv']:+7.4f}"
        )


def main() -> int:
    args = parse_args()

    from scripts.ab_test_profiles import AB_PROFILES

    profile_ids = load_profile_order(args.only, args.profiles)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir) if args.output_dir else PROJECT_ROOT / "data" / "ab_tests" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=== A/B BACKTEST PLAN ===")
    print(f"Period:     {args.start} → {args.end}")
    print(f"Izvor:      API-only (legacy isključen)")
    print(f"Output:     {out_dir}")
    print(f"Profili:    {', '.join(profile_ids)}")
    print()
    for pid in profile_ids:
        meta = AB_PROFILES[pid]
        print(f"  • {meta['label']}: {meta['description']}")

    if args.dry_run:
        print("\n(dry-run — nije pokrenut backtest)")
        return 0

    manifest = {
        "created_at": datetime.utcnow().isoformat(),
        "start": args.start,
        "end": args.end,
        "profiles": profile_ids,
        "output_dir": str(out_dir),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    failures = 0
    for pid in profile_ids:
        rc = run_profile(
            pid,
            start=args.start,
            end=args.end,
            output_path=out_dir / f"{pid}.json",
        )
        if rc != 0:
            failures += 1
            print(f"[GRESKA] Profil {pid} nije uspeo (exit {rc})")

    summary_rows: list[dict] = []
    baseline_roi: float | None = None

    for pid in profile_ids:
        json_path = out_dir / f"{pid}.json"
        if not json_path.exists():
            continue
        data = json.loads(json_path.read_text(encoding="utf-8"))
        m = data["metrics"]
        row = {
            "profile_id": pid,
            "label": data.get("label"),
            "description": data.get("description"),
            "total_bets": m["total_bets"],
            "roi_pct": m["roi_pct"],
            "win_rate": m["win_rate"],
            "total_profit": m["total_profit"],
            "total_staked": m["total_staked"],
            "avg_clv": m["avg_clv"],
            "avg_ev": m["avg_ev"],
            "sharpe_ratio": m["sharpe_ratio"],
            "clv_coverage_pct": m["clv_coverage_pct"],
        }
        if pid == "baseline":
            baseline_roi = m["roi_pct"]
        summary_rows.append(row)

    if baseline_roi is not None:
        for row in summary_rows:
            row["delta_roi_vs_baseline"] = round(row["roi_pct"] - baseline_roi, 4)

    summary_path = out_dir / "summary.csv"
    write_summary_csv(summary_rows, summary_path)
    compare_to_baseline(summary_rows)

    print(f"\nSačuvano: {summary_path}")
    print(f"JSON po profilu: {out_dir}/*.json")
    print("\nError analysis (baseline):")
    print(f"  python scripts/analyze_backtest_errors.py --input {out_dir / 'baseline.json'}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
