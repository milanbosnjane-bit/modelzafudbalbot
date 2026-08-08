"""Obriši ROI statistiku (daily_picks) osim jučerašnjeg i današnjeg datuma.

Mečevi, kvote, feature vektori i predikcije ostaju u bazi za trening.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "football_roi.db"


def cleanup_roi_stats(db_path: Path, *, dry_run: bool = False) -> dict:
    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)
    keep = {yesterday.isoformat(), today.isoformat()}

    conn = sqlite3.connect(db_path)
    try:
        before = conn.execute("SELECT COUNT(*) FROM daily_picks").fetchone()[0]
        to_delete = conn.execute(
            """
            SELECT COUNT(*) FROM daily_picks
            WHERE date(pick_date) NOT IN (?, ?)
            """,
            tuple(sorted(keep)),
        ).fetchone()[0]
        kept = before - to_delete

        if not dry_run and to_delete:
            conn.execute(
                """
                DELETE FROM daily_picks
                WHERE date(pick_date) NOT IN (?, ?)
                """,
                tuple(sorted(keep)),
            )
            conn.commit()

        after = conn.execute("SELECT COUNT(*) FROM daily_picks").fetchone()[0]
        by_date = conn.execute(
            """
            SELECT date(pick_date), COUNT(*)
            FROM daily_picks
            GROUP BY date(pick_date)
            ORDER BY 1
            """
        ).fetchall()
        fixtures = conn.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0]
    finally:
        conn.close()

    return {
        "db": str(db_path),
        "keep_dates": sorted(keep),
        "before": before,
        "deleted": to_delete if not dry_run else 0,
        "would_delete": to_delete,
        "after": after,
        "by_date": by_date,
        "fixtures_unchanged": fixtures,
        "dry_run": dry_run,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.db.is_file():
        raise SystemExit(f"DB not found: {args.db}")

    result = cleanup_roi_stats(args.db, dry_run=args.dry_run)
    mode = "DRY RUN" if result["dry_run"] else "DONE"
    print(f"[{mode}] {result['db']}")
    print(f"Keep dates (UTC): {', '.join(result['keep_dates'])}")
    print(f"daily_picks before: {result['before']}")
    if result["dry_run"]:
        print(f"would delete: {result['would_delete']}")
    else:
        print(f"deleted: {result['deleted']}")
    print(f"daily_picks after: {result['after']}")
    print("remaining by date:")
    for d, n in result["by_date"]:
        print(f"  {d}: {n}")
    print(f"fixtures (unchanged): {result['fixtures_unchanged']}")


if __name__ == "__main__":
    main()
