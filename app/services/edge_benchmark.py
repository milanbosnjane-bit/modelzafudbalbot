"""Closing line edge capture benchmark."""

import structlog
from sqlalchemy import select

from app.database.models import DailyPick, OddsSnapshot
from app.database.session import AsyncSessionLocal, SyncSessionLocal
from app.utils.edge import EdgeMetrics, compute_edge_metrics
from app.utils.odds import implied_probability

logger = structlog.get_logger()

__all__ = ["EdgeBenchmark", "compute_edge_metrics", "EdgeMetrics"]


class EdgeBenchmark:
    async def update_pick_edge(self, pick_id: int) -> EdgeMetrics | None:
        async with AsyncSessionLocal() as session:
            pick = await session.get(DailyPick, pick_id)
            if not pick or not pick.fair_implied_prob:
                return None

            closing_fair = await self._get_closing_fair_prob(session, pick)
            metrics = compute_edge_metrics(
                pick.probability, pick.fair_implied_prob, closing_fair
            )

            pick.model_edge = metrics.model_edge
            pick.closing_edge = metrics.closing_edge
            pick.edge_capture = metrics.adjusted_edge_capture
            pick.raw_edge_capture = metrics.raw_edge_capture
            pick.adjusted_edge_capture = metrics.adjusted_edge_capture
            pick.closing_fair_prob = closing_fair
            await session.commit()
            return metrics

    async def _get_closing_fair_prob(self, session, pick: DailyPick) -> float | None:
        if pick.closing_fair_prob:
            return pick.closing_fair_prob
        result = await session.execute(
            select(OddsSnapshot).where(
                OddsSnapshot.fixture_id == pick.fixture_id,
                OddsSnapshot.market == pick.market,
                OddsSnapshot.selection == pick.selection,
                OddsSnapshot.is_closing == True,
            ).order_by(OddsSnapshot.captured_at.desc())
        )
        closing = result.scalars().first()
        if not closing:
            return None
        if closing.fair_prob:
            return closing.fair_prob
        if closing.closing_odds:
            return implied_probability(closing.closing_odds)
        return None

    async def batch_update(self) -> int:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(DailyPick).where(
                    DailyPick.outcome.in_(["win", "lose", "push"]),
                    DailyPick.adjusted_edge_capture.is_(None),
                )
            )
            count = 0
            for pick in result.scalars().all():
                if not pick.fair_implied_prob:
                    continue
                closing_fair = await self._get_closing_fair_prob(session, pick)
                metrics = compute_edge_metrics(
                    pick.probability, pick.fair_implied_prob, closing_fair
                )
                pick.model_edge = metrics.model_edge
                pick.closing_edge = metrics.closing_edge
                pick.edge_capture = metrics.adjusted_edge_capture
                pick.raw_edge_capture = metrics.raw_edge_capture
                pick.adjusted_edge_capture = metrics.adjusted_edge_capture
                pick.closing_fair_prob = closing_fair
                count += 1
            await session.commit()
            return count

    def aggregate_report(self, paper_only: bool = True) -> dict:
        session = SyncSessionLocal()
        try:
            query = select(DailyPick).where(
                DailyPick.outcome.in_(["win", "lose", "push"]),
                DailyPick.adjusted_edge_capture.isnot(None),
            )
            if paper_only:
                query = query.where(DailyPick.is_paper == True)

            picks = session.execute(query).scalars().all()
            if not picks:
                return {"sample_size": 0, "avg_edge_capture": 0, "pct_below_half": 0}

            captures = [p.adjusted_edge_capture for p in picks if p.adjusted_edge_capture is not None]
            below_half = sum(1 for c in captures if c < 0.5)

            return {
                "sample_size": len(captures),
                "avg_model_edge": sum(p.model_edge or 0 for p in picks) / len(picks),
                "avg_closing_edge": sum(p.closing_edge or 0 for p in picks) / len(picks),
                "avg_edge_capture": sum(captures) / len(captures),
                "avg_raw_edge_capture": sum(p.raw_edge_capture or 0 for p in picks) / len(picks),
                "pct_below_half": below_half / len(captures),
                "healthy": sum(captures) / len(captures) >= 0.5,
            }
        finally:
            session.close()
