"""Backfill clv_raw + closing_fair_edge for settled picks (read/write)."""
from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.database.models import DailyPick
from app.database.session import AsyncSessionLocal, init_db
from app.services.clv_tracker import CLVTracker

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "football_roi.db"


async def backfill(*, backup: bool = True) -> dict:
    if backup and DB.is_file():
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        dest = DB.with_name(f"football_roi.db.bak_clv_{ts}")
        shutil.copy2(DB, dest)
        print(f"Backup: {dest}")

    await init_db()
    tracker = CLVTracker()
    updated = skipped = no_closing = 0
    clv_raws: list[float] = []

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DailyPick).where(
                DailyPick.outcome.in_(("win", "lose", "push"))
            )
        )
        picks = list(result.scalars().all())

        for pick in picks:
            metrics = await tracker.compute_pick_clv(session, pick)
            if not metrics:
                no_closing += 1
                continue
            pick.clv_raw = metrics["clv_raw"]
            pick.clv = metrics["clv_raw"]
            pick.closing_fair_edge = metrics["closing_fair_edge"]
            pick.closing_odds = metrics["closing_odds"]
            pick.closing_fair_prob = metrics["closing_fair_prob"]
            clv_raws.append(metrics["clv_raw"])
            updated += 1

        await session.commit()

    pos = sum(1 for c in clv_raws if c > 0.001)
    neg = sum(1 for c in clv_raws if c < -0.001)
    neu = len(clv_raws) - pos - neg
    avg = sum(clv_raws) / len(clv_raws) if clv_raws else 0.0

    settled = len(picks)
    coverage = updated / settled * 100 if settled else 0

    summary = {
        "settled": settled,
        "updated": updated,
        "skipped": skipped,
        "no_closing": no_closing,
        "coverage_pct": coverage,
        "avg_clv_raw_pct": avg * 100,
        "positive": pos,
        "negative": neg,
        "neutral": neu,
    }
    print("Backfill summary:", summary)
    return summary


if __name__ == "__main__":
    asyncio.run(backfill())
