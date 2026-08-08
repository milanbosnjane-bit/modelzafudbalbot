"""CLV pregled + batch update iz baze."""
import asyncio
import os
import sqlite3
import sys

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/football_roi.db")
os.environ.setdefault("DATABASE_URL_SYNC", "sqlite:///./data/football_roi.db")
os.environ.setdefault("LOCAL_MODE", "true")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def sqlite_clv_before():
    conn = sqlite3.connect("data/football_roi.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM daily_picks")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM daily_picks WHERE clv IS NOT NULL")
    with_clv = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM odds_snapshots WHERE is_closing = 1")
    closing_snaps = c.fetchone()[0]
    c.execute("""
        SELECT id, fixture_id, selection, odds, clv, closing_odds, outcome
        FROM daily_picks WHERE clv IS NOT NULL
        ORDER BY id DESC LIMIT 10
    """)
    rows = c.fetchall()
    conn.close()
    return total, with_clv, closing_snaps, rows


async def run_clv_update():
    from app.services.clv_tracker import CLVTracker
    tracker = CLVTracker()
    batch = await tracker.batch_update_clv()
    summary = await tracker.get_clv_summary()
    return batch, summary


async def main():
    total, with_clv, closing_snaps, rows_before = sqlite_clv_before()

    print("=== CLV / BAZA (pre update) ===")
    print(f"  daily_picks ukupno:     {total}")
    print(f"  sa CLV vrednošću:       {with_clv}")
    print(f"  closing odds snapshot:  {closing_snaps}")
    print()

    print("=== CLV BATCH UPDATE ===")
    batch, summary = await run_clv_update()
    print(f"  Ažurirano:              {batch['updated']}")
    print(f"  Bez closing kvote:      {batch['failed']}")
    print(f"  Coverage (batch):       {batch['coverage']:.1%}")
    print()

    print("=== CLV SUMMARY ===")
    for k, v in summary.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}" if abs(v) < 10 else f"  {k}: {v:.2f}")
        else:
            print(f"  {k}: {v}")

    _, with_clv_after, _, rows = sqlite_clv_before()
    print()
    print(f"=== POSLE UPDATE: {with_clv_after}/{total} pickova ima CLV ===")
    if rows:
        print("\nPoslednji pickovi sa CLV:")
        for r in rows:
            print(f"  id={r[0]} | {r[2]} @ {r[3]} | CLV={r[4]:+.4f} | close={r[5]} | {r[6]}")
    else:
        print("\nNema pickova sa CLV — closing kvote nisu snimljene u odds_snapshots.")


if __name__ == "__main__":
    asyncio.run(main())
