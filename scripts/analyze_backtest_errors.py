"""
Error analysis za backtest — lige, marketi, opsezi kvota.

Ulaz:
  --input data/ab_tests/<run>/baseline.json   (preporučeno)
  --input-dir data/ab_tests/<run>             (svi JSON u folderu)
  --from-db --name oos_2025                   (iz backtest_runs; max 100 pickova u DB!)

Izlaz: konzola + opciono --csv-dir za CSV fajlove
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "football_roi.db"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backtest error analysis")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", type=str, help="Jedan JSON iz A/B testa")
    src.add_argument("--input-dir", type=str, help="Folder sa *.json profilima")
    src.add_argument("--from-db", action="store_true", help="Učitaj iz backtest_runs")
    p.add_argument("--name", default="oos_2025", help="Ime run-a u bazi (--from-db)")
    p.add_argument("--csv-dir", type=str, default=None, help="Folder za CSV izvoz")
    return p.parse_args()


def odds_bucket(odds: float) -> str:
    if odds < 1.50:
        return "<1.50"
    if odds < 2.00:
        return "1.50-2.00"
    if odds < 3.00:
        return "2.00-3.00"
    return "3.00+"


def market_label(market: str, selection: str) -> str:
    m = (market or "").lower()
    s = (selection or "").lower()
    if m == "over_under":
        return f"O/U {selection}"
    if m == "match_winner":
        if s in ("draw", "x"):
            return "Match Winner — Draw"
        if s in ("home", "1"):
            return "Match Winner — Home"
        if s in ("away", "2"):
            return "Match Winner — Away"
    if m == "btts":
        return f"BTTS {selection}"
    return f"{market}/{selection}"


def aggregate_bucket(picks: list[dict], key_fn) -> dict:
    buckets: dict[str, dict] = {}
    for p in picks:
        if p.get("outcome") not in ("win", "lose", "push"):
            continue
        key = key_fn(p)
        b = buckets.setdefault(
            key,
            {"n": 0, "wins": 0, "losses": 0, "pushes": 0, "profit": 0.0, "staked": 0.0},
        )
        b["n"] += 1
        b["staked"] += float(p.get("stake") or 1.0)
        b["profit"] += float(p.get("profit") or 0.0)
        oc = p.get("outcome")
        if oc == "win":
            b["wins"] += 1
        elif oc == "lose":
            b["losses"] += 1
        else:
            b["pushes"] += 1
    return buckets


def enrich_picks_with_league(picks: list[dict]) -> list[dict]:
    if not DB_PATH.exists():
        return picks
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    fixture_ids = {p["fixture_id"] for p in picks if p.get("fixture_id")}
    if not fixture_ids:
        conn.close()
        return picks

    placeholders = ",".join("?" * len(fixture_ids))
    rows = conn.execute(
        f"""
        SELECT f.id AS fixture_id, f.league_id, l.name AS league_name, l.country
        FROM fixtures f
        LEFT JOIN leagues l ON l.id = f.league_id
        WHERE f.id IN ({placeholders})
        """,
        list(fixture_ids),
    ).fetchall()
    conn.close()

    by_fid = {r["fixture_id"]: dict(r) for r in rows}
    enriched = []
    for p in picks:
        cp = dict(p)
        meta = by_fid.get(p.get("fixture_id"), {})
        cp["league_id"] = meta.get("league_id")
        cp["league_name"] = meta.get("league_name") or f"league_{meta.get('league_id', '?')}"
        cp["country"] = meta.get("country") or ""
        enriched.append(cp)
    return enriched


def load_picks_from_json(path: Path) -> tuple[dict, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data, data.get("picks", [])


def load_picks_from_db(name: str) -> tuple[dict, list[dict]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM backtest_runs WHERE name = ? ORDER BY id DESC LIMIT 1",
        (name,),
    ).fetchone()
    conn.close()
    if not row:
        raise SystemExit(f"Nema backtest_runs za name={name!r}")
    results = json.loads(row["results"]) if row["results"] else {}
    picks = results.get("picks", [])
    meta = {
        "profile_id": name,
        "label": name,
        "metrics": {
            "total_bets": row["total_bets"],
            "roi_pct": row["roi_pct"],
            "win_rate": row["win_rate"],
            "avg_clv": row["avg_clv"],
        },
        "warning": "DB čuva max 100 pickova u results JSON — koristi A/B JSON za punu analizu",
    }
    return meta, picks


def print_table(title: str, buckets: dict, *, min_n: int = 1, sort_key=None) -> None:
    print(f"\n{'='*88}")
    print(f"  {title}")
    print(f"{'='*88}")
    print(f"  {'Grupa':<36} {'N':>5} {'W':>5} {'L':>5} {'WR%':>7} {'ROI%':>8} {'Profit':>9}")
    print(f"  {'-'*76}")

    items = [
        (k, v)
        for k, v in buckets.items()
        if v["n"] >= min_n
    ]
    if sort_key:
        items.sort(key=sort_key)
    else:
        items.sort(key=lambda x: x[1]["n"], reverse=True)

    for key, b in items:
        decisive = b["wins"] + b["losses"]
        wr = (b["wins"] / decisive * 100) if decisive else 0.0
        roi = (b["profit"] / b["staked"] * 100) if b["staked"] else 0.0
        sign = "+" if b["profit"] >= 0 else ""
        print(
            f"  {str(key)[:35]:<36} {b['n']:5d} {b['wins']:5d} {b['losses']:5d} "
            f"{wr:6.1f}% {roi:+7.2f}% {sign}{b['profit']:8.2f}u"
        )


def export_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def buckets_to_rows(buckets: dict, group_col: str) -> list[dict]:
    rows = []
    for key, b in buckets.items():
        decisive = b["wins"] + b["losses"]
        wr = (b["wins"] / decisive) if decisive else 0.0
        roi = (b["profit"] / b["staked"] * 100) if b["staked"] else 0.0
        rows.append({
            group_col: key,
            "n": b["n"],
            "wins": b["wins"],
            "losses": b["losses"],
            "win_rate": round(wr, 4),
            "roi_pct": round(roi, 4),
            "profit": round(b["profit"], 4),
            "staked": round(b["staked"], 4),
        })
    return rows


def analyze(meta: dict, picks: list[dict], csv_dir: Path | None) -> None:
    picks = enrich_picks_with_league(picks)
    settled = [p for p in picks if p.get("outcome") in ("win", "lose", "push")]

    m = meta.get("metrics", {})
    print(f"\n{'#'*88}")
    print(f"  ERROR ANALYSIS — {meta.get('label', meta.get('profile_id', '?'))}")
    if meta.get("warning"):
        print(f"  ⚠ {meta['warning']}")
    print(f"{'#'*88}")
    print(
        f"  Ukupno: {m.get('total_bets', len(settled))} tipova | "
        f"ROI {m.get('roi_pct', 0):+.2f}% | WR {m.get('win_rate', 0):.1%} | "
        f"CLV {m.get('avg_clv', 0):+.4f}"
    )

    by_league = aggregate_bucket(
        settled,
        lambda p: f"{p.get('league_name', '?')} ({p.get('country', '')})".strip(),
    )
    by_market = aggregate_bucket(settled, lambda p: market_label(p.get("market", ""), p.get("selection", "")))
    by_odds = aggregate_bucket(settled, lambda p: odds_bucket(float(p.get("odds") or 0)))
    by_selection = aggregate_bucket(
        settled,
        lambda p: (p.get("selection") or "?").lower(),
    )

    print_table("ROI / WIN RATE PO LIGAMA (crna lista = negativan ROI, N>=5)", by_league, min_n=1,
                sort_key=lambda x: (x[1]["profit"] / x[1]["staked"] if x[1]["staked"] else 0))
    print_table("ROI / WIN RATE PO MARKETIMA", by_market)
    print_table("ROI / WIN RATE PO OPSEGU KVOTA", by_odds)
    print_table("ROI / WIN RATE PO SELEKCIJI (raw)", by_selection)

    # Crna lista liga
    blacklist = [
        (k, v) for k, v in by_league.items()
        if v["n"] >= 5 and v["staked"] and (v["profit"] / v["staked"] * 100) < -10
    ]
    if blacklist:
        print(f"\n{'='*88}")
        print("  🚫 KANDIDATI ZA CRNU LISTU (ROI < -10%, N>=5)")
        print(f"{'='*88}")
        for k, v in sorted(blacklist, key=lambda x: x[1]["profit"] / x[1]["staked"]):
            roi = v["profit"] / v["staked"] * 100
            print(f"  • {k}: ROI {roi:+.1f}% ({v['n']} tipova)")

    if csv_dir:
        export_csv(csv_dir / "by_league.csv", buckets_to_rows(by_league, "league"), 
                   ["league", "n", "wins", "losses", "win_rate", "roi_pct", "profit", "staked"])
        export_csv(csv_dir / "by_market.csv", buckets_to_rows(by_market, "market"),
                   ["market", "n", "wins", "losses", "win_rate", "roi_pct", "profit", "staked"])
        export_csv(csv_dir / "by_odds_bucket.csv", buckets_to_rows(by_odds, "odds_bucket"),
                   ["odds_bucket", "n", "wins", "losses", "win_rate", "roi_pct", "profit", "staked"])
        print(f"\nCSV izvezen u: {csv_dir}")


def main() -> int:
    args = parse_args()
    csv_dir = Path(args.csv_dir) if args.csv_dir else None

    if args.input:
        meta, picks = load_picks_from_json(Path(args.input))
        analyze(meta, picks, csv_dir)
        return 0

    if args.input_dir:
        folder = Path(args.input_dir)
        for json_file in sorted(folder.glob("*.json")):
            if json_file.name in ("manifest.json",):
                continue
            meta, picks = load_picks_from_json(json_file)
            analyze(meta, picks, csv_dir / json_file.stem if csv_dir else None)
        return 0

    meta, picks = load_picks_from_db(args.name)
    analyze(meta, picks, csv_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
