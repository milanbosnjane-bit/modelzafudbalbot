"""CLV tracking — RAW line shopping + optional closing fair edge."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

import structlog
from sqlalchemy import func, select

from app.database.models import DailyPick, Fixture, ModelMetrics, OddsSnapshot
from app.database.session import AsyncSessionLocal
from app.utils.clv_metrics import (
    SnapshotOutcome,
    clv_raw,
    closing_fair_edge,
    validated_closing_fair_prob,
)
from app.utils.helpers import utc_now

logger = structlog.get_logger()


class ClosingMissReason(str, Enum):
    NO_CLOSING_SNAPSHOT = "no_closing_snapshot"
    MARKET_SELECTION_NOT_FOUND = "market_selection_not_found"
    FIXTURE_NOT_FOUND = "fixture_not_found"
    KICKOFF_WINDOW_MISSED = "kickoff_window_missed"
    NO_CLOSING_ODDS_VALUE = "no_closing_odds_value"
    INCOMPLETE_MARKET_GROUP = "incomplete_market_group"
    FAIR_PROB_INVALID = "fair_prob_invalid"


class CLVTracker:
    async def _load_closing_row(self, session, pick: DailyPick) -> OddsSnapshot | None:
        result = await session.execute(
            select(OddsSnapshot)
            .where(
                OddsSnapshot.fixture_id == pick.fixture_id,
                OddsSnapshot.market == pick.market,
                OddsSnapshot.selection == pick.selection,
                OddsSnapshot.is_closing == True,  # noqa: E712
            )
            .order_by(OddsSnapshot.captured_at.desc())
        )
        return result.scalars().first()

    async def _load_snapshot_group(
        self, session, closing: OddsSnapshot
    ) -> list[OddsSnapshot]:
        query = select(OddsSnapshot).where(
            OddsSnapshot.fixture_id == closing.fixture_id,
            OddsSnapshot.bookmaker == closing.bookmaker,
            OddsSnapshot.market == closing.market,
            OddsSnapshot.captured_at == closing.captured_at,
        )
        if closing.line is not None:
            query = query.where(OddsSnapshot.line == closing.line)
        result = await session.execute(query)
        return list(result.scalars().all())

    def _diagnose_missing_closing(
        self,
        pick: DailyPick,
        fixture: Fixture | None,
        *,
        has_any_snapshot: bool,
        has_market_selection: bool,
    ) -> ClosingMissReason:
        if fixture is None:
            return ClosingMissReason.FIXTURE_NOT_FOUND
        if not has_any_snapshot:
            if fixture.fixture_date and fixture.fixture_date < utc_now() - timedelta(hours=2):
                return ClosingMissReason.KICKOFF_WINDOW_MISSED
            return ClosingMissReason.NO_CLOSING_SNAPSHOT
        if not has_market_selection:
            return ClosingMissReason.MARKET_SELECTION_NOT_FOUND
        return ClosingMissReason.NO_CLOSING_SNAPSHOT

    async def compute_pick_clv(self, session, pick: DailyPick) -> dict | None:
        entry_odds = pick.user_odds or pick.odds
        closing = await self._load_closing_row(session, pick)
        fixture = await session.get(Fixture, pick.fixture_id)

        any_snap = (
            await session.execute(
                select(func.count(OddsSnapshot.id)).where(
                    OddsSnapshot.fixture_id == pick.fixture_id
                )
            )
        ).scalar() or 0
        ms_snap = (
            await session.execute(
                select(func.count(OddsSnapshot.id)).where(
                    OddsSnapshot.fixture_id == pick.fixture_id,
                    OddsSnapshot.market == pick.market,
                    OddsSnapshot.selection == pick.selection,
                )
            )
        ).scalar() or 0

        if not closing:
            reason = self._diagnose_missing_closing(
                pick,
                fixture,
                has_any_snapshot=any_snap > 0,
                has_market_selection=ms_snap > 0,
            )
            logger.info(
                "closing_odds_missing",
                pick_id=pick.id,
                fixture_id=pick.fixture_id,
                reason=reason.value,
            )
            return None

        closing_odds = closing.closing_odds or closing.current_odds
        if not closing_odds or closing_odds <= 1.0:
            logger.info(
                "closing_odds_missing",
                pick_id=pick.id,
                fixture_id=pick.fixture_id,
                reason=ClosingMissReason.NO_CLOSING_ODDS_VALUE.value,
            )
            return None

        if fixture and closing.captured_at and fixture.fixture_date:
            if closing.captured_at > fixture.fixture_date:
                logger.warning(
                    "closing_after_kickoff",
                    pick_id=pick.id,
                    fixture_id=pick.fixture_id,
                    captured_at=str(closing.captured_at),
                    kickoff=str(fixture.fixture_date),
                )

        group = await self._load_snapshot_group(session, closing)
        outcomes = [
            SnapshotOutcome(
                selection=s.selection,
                odds=s.closing_odds or s.current_odds,
                line=s.line,
            )
            for s in group
            if (s.closing_odds or s.current_odds) and (s.closing_odds or s.current_odds) > 1.0
        ]

        fair_prob = validated_closing_fair_prob(
            pick.market,
            pick.selection,
            closing_odds,
            outcomes,
            pick_line=pick.line,
            pick_id=pick.id,
            fixture_id=pick.fixture_id,
        )

        raw = clv_raw(entry_odds, closing_odds)
        fair_edge = closing_fair_edge(entry_odds, fair_prob)

        return {
            "clv_raw": raw,
            "clv": raw,
            "closing_fair_edge": fair_edge,
            "closing_odds": closing_odds,
            "closing_fair_prob": fair_prob,
            "closing_bookmaker": closing.bookmaker,
            "closing_captured_at": closing.captured_at,
        }

    async def update_pick_clv(self, pick_id: int) -> float | None:
        async with AsyncSessionLocal() as session:
            pick = await session.get(DailyPick, pick_id)
            if not pick:
                return None
            metrics = await self.compute_pick_clv(session, pick)
            if not metrics:
                return None
            pick.clv_raw = metrics["clv_raw"]
            pick.clv = metrics["clv_raw"]
            pick.closing_fair_edge = metrics["closing_fair_edge"]
            pick.closing_odds = metrics["closing_odds"]
            pick.closing_fair_prob = metrics["closing_fair_prob"]
            await session.commit()
            return metrics["clv_raw"]

    async def batch_update_clv(self) -> dict:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(DailyPick).where(
                    DailyPick.outcome.in_(("win", "lose", "push", "pending")),
                )
            )
            picks = result.scalars().all()
            updated = failed = 0
            miss_reasons: dict[str, int] = {}

            for pick in picks:
                if pick.clv_raw is not None and pick.closing_odds:
                    continue
                metrics = await self.compute_pick_clv(session, pick)
                if metrics:
                    pick.clv_raw = metrics["clv_raw"]
                    pick.clv = metrics["clv_raw"]
                    pick.closing_fair_edge = metrics["closing_fair_edge"]
                    pick.closing_odds = metrics["closing_odds"]
                    pick.closing_fair_prob = metrics["closing_fair_prob"]
                    updated += 1
                else:
                    failed += 1

            await session.commit()
            return {
                "updated": updated,
                "failed": failed,
                "coverage": updated / max(len(picks), 1),
            }

    async def compute_model_metrics(
        self,
        model_name: str = "ensemble",
        days: int = 30,
    ) -> dict:
        async with AsyncSessionLocal() as session:
            cutoff = utc_now() - timedelta(days=days)
            all_picks = (
                await session.execute(
                    select(DailyPick).where(DailyPick.created_at >= cutoff)
                )
            ).scalars().all()
            with_clv = [p for p in all_picks if p.clv_raw is not None or p.clv is not None]

            if not with_clv:
                return {
                    "avg_clv": 0,
                    "sample_size": 0,
                    "clv_coverage_pct": 0,
                    "total_picks": len(all_picks),
                }

            clvs = [p.clv_raw if p.clv_raw is not None else p.clv for p in with_clv]
            evs = [p.expected_value for p in with_clv]
            profits = [
                p.profit_units or 0
                for p in with_clv
                if p.outcome in ("win", "lose", "push")
            ]
            staked = sum(
                p.stake_units
                for p in with_clv
                if p.outcome in ("win", "lose", "push")
            )

            metrics = ModelMetrics(
                model_name=model_name,
                market="all",
                period_start=cutoff,
                period_end=utc_now(),
                avg_clv=sum(clvs) / len(clvs),
                avg_ev=sum(evs) / len(evs),
                roi_pct=(sum(profits) / staked * 100) if staked else 0,
                sample_size=len(with_clv),
                notes=f"coverage={len(with_clv)}/{len(all_picks)}",
            )
            session.add(metrics)
            await session.commit()

            return {
                "avg_clv": metrics.avg_clv,
                "avg_ev": metrics.avg_ev,
                "roi_pct": metrics.roi_pct,
                "sample_size": metrics.sample_size,
                "clv_coverage_pct": len(with_clv) / max(len(all_picks), 1),
                "total_picks": len(all_picks),
            }

    async def get_clv_summary(self) -> dict:
        async with AsyncSessionLocal() as session:
            total = (
                await session.execute(select(func.count(DailyPick.id)))
            ).scalar() or 0
            with_clv = (
                await session.execute(
                    select(func.count(DailyPick.id)).where(
                        (DailyPick.clv_raw.isnot(None)) | (DailyPick.clv.isnot(None))
                    )
                )
            ).scalar() or 0
            result = await session.execute(
                select(
                    func.avg(
                        func.coalesce(DailyPick.clv_raw, DailyPick.clv)
                    ),
                    func.avg(DailyPick.expected_value),
                ).where(
                    (DailyPick.clv_raw.isnot(None)) | (DailyPick.clv.isnot(None))
                )
            )
            row = result.one()
            return {
                "avg_clv": float(row[0] or 0),
                "avg_ev": float(row[1] or 0),
                "sample_size": with_clv,
                "total_picks": total,
                "clv_coverage_pct": with_clv / max(total, 1),
            }
