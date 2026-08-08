"""Live paper trading — no real money, full lifecycle tracking."""

from datetime import datetime, timedelta

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.database.models import DailyPick, Fixture
from app.database.session import AsyncSessionLocal, SyncSessionLocal
from app.services.clv_tracker import CLVTracker
from app.services.edge_benchmark import EdgeBenchmark
from app.training.targets import realized_return_from_outcome

logger = structlog.get_logger()
settings = get_settings()


class PaperTradingService:
    """
    30-60 day paper trading evaluation before real money.

    Flow: predict → store (is_paper=True) → settle → CLV → edge_capture → evaluate

    Go-live criteria (defaults):
    - min 100 bets (ideal 300+)
    - ROI > 3%
    - avg CLV > 1%
    - avg edge_capture >= 0.5
    """

    MIN_BETS = 100
    IDEAL_BETS = 300
    MIN_ROI_PCT = 3.0
    MIN_CLV = 0.01
    MIN_EDGE_CAPTURE = 0.5
    MIN_DAYS = 30

    async def refresh_pending_fixtures_from_api(self) -> int:
        """Osveži statuse mečeva sa pending tipovima (API-Football)."""
        from app.services.api_football import APIFootballClient
        from app.services.ingestion import DataIngestionService

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(DailyPick.fixture_id)
                .where(DailyPick.outcome == "pending")
                .distinct()
            )
            fixture_ids = [row[0] for row in result.all()]

        if not fixture_ids:
            return 0

        api = APIFootballClient()
        updated = 0
        for fid in fixture_ids:
            try:
                data = await api._request("fixtures", {"id": fid})
                items = data.get("response", [])
                if not items:
                    continue
                item = items[0]
                league_id = item.get("league", {}).get("id")
                async with AsyncSessionLocal() as session:
                    svc = DataIngestionService(session)
                    if await svc._upsert_fixture_item(item, league_id):
                        updated += 1
                    await session.commit()
            except Exception as e:
                logger.warning("fixture_refresh_failed", fixture_id=fid, error=str(e))

        logger.info("pending_fixtures_refreshed", count=updated, pending=len(fixture_ids))
        return updated

    async def settle_finished_picks(self) -> int:
        """Resolve outcomes for paper picks on finished fixtures."""
        await self.refresh_pending_fixtures_from_api()

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(DailyPick).where(DailyPick.outcome == "pending")
            )
            picks = result.scalars().all()
            count = 0

            for pick in picks:
                fixture = await session.get(Fixture, pick.fixture_id)
                if not fixture or fixture.status not in ("FT", "AET", "PEN"):
                    continue
                if fixture.home_goals is None:
                    continue

                outcome = self._resolve_outcome(fixture, pick)
                profit = self._profit(pick, outcome)
                ret = realized_return_from_outcome(
                    outcome, pick.user_odds or pick.odds, profit, pick.stake_units
                )

                pick.outcome = outcome
                pick.profit_units = profit
                pick.realized_return = ret
                count += 1

            await session.commit()

        if count:
            clv = CLVTracker()
            await clv.batch_update_clv()
            edge = EdgeBenchmark()
            await edge.batch_update()

        logger.info("paper_picks_settled", count=count)
        return count

    def evaluate(
        self,
        min_days: int | None = None,
        *,
        all_time: bool = False,
    ) -> dict:
        """Paper evaluacija — tačne vrednosti profit_units i stake_units iz baze."""
        session = SyncSessionLocal()
        try:
            query = select(DailyPick).where(
                DailyPick.outcome.in_(["win", "lose", "push"]),
            )
            if not all_time:
                days = min_days if min_days is not None else self.MIN_DAYS
                cutoff = datetime.utcnow() - timedelta(days=days)
                query = query.where(
                    DailyPick.is_paper == True,
                    DailyPick.pick_date >= cutoff,
                )

            picks = session.execute(query).scalars().all()

            if not picks:
                return self._empty_report(min_days or self.MIN_DAYS)

            profits = [
                p.profit_units if p.profit_units is not None else 0.0
                for p in picks
            ]
            staked = sum(p.stake_units or 0.0 for p in picks)
            wins = sum(1 for p in picks if p.outcome == "win")
            losses = sum(1 for p in picks if p.outcome == "lose")
            pushes = sum(1 for p in picks if p.outcome == "push")
            winrate = (wins / (wins + losses) * 100) if (wins + losses) else 0.0
            profit_total = sum(profits)
            roi_pct = (profit_total / staked * 100) if staked else 0.0

            clvs = [
                p.clv_raw if p.clv_raw is not None else p.clv
                for p in picks
                if (p.clv_raw is not None or p.clv is not None)
            ]
            captures = [p.edge_capture for p in picks if p.edge_capture is not None]
            avg_clv = sum(clvs) / len(clvs) if clvs else 0.0
            avg_capture = sum(captures) / len(captures) if captures else 0.0

            checks = {
                "min_bets": len(picks) >= self.MIN_BETS,
                "ideal_bets": len(picks) >= self.IDEAL_BETS,
                "roi": roi_pct >= self.MIN_ROI_PCT,
                "clv": avg_clv >= self.MIN_CLV,
                "edge_capture": avg_capture >= self.MIN_EDGE_CAPTURE,
                "min_days": (
                    (datetime.utcnow() - min(p.pick_date for p in picks)).days >= self.MIN_DAYS
                    if picks
                    else False
                ),
            }

            go_live = all(
                [checks["min_bets"], checks["roi"], checks["clv"], checks["edge_capture"]]
            )

            return {
                "period_days": min_days if min_days is not None else self.MIN_DAYS,
                "all_time": all_time,
                "total_bets": len(picks),
                "wins": wins,
                "losses": losses,
                "pushes": pushes,
                "winrate": winrate,
                "profit_units": profit_total,
                "staked_units": staked,
                "roi_pct": roi_pct,
                "avg_clv": avg_clv,
                "avg_edge_capture": avg_capture,
                "clv_coverage": len(clvs) / len(picks),
                "checks": checks,
                "go_live_ready": go_live,
                "verdict": self._verdict(len(picks), roi_pct, avg_clv),
            }
        finally:
            session.close()

    def _verdict(self, n: int, roi: float, clv: float) -> str:
        if n < 50:
            return f"IGNORE — only {n} bets (need {self.MIN_BETS}+)"
        if n < self.MIN_BETS:
            return f"INSUFFICIENT — {n} bets, need {self.MIN_BETS}+"
        if roi > 15 and n < 100:
            return f"SUSPICIOUS — ROI {roi:.1f}% on {n} bets, likely noise"
        if roi >= self.MIN_ROI_PCT and clv >= self.MIN_CLV and n >= self.IDEAL_BETS:
            return f"PROMISING — ROI {roi:.1f}%, CLV {clv:.1%}, n={n}"
        if roi >= self.MIN_ROI_PCT and n >= self.MIN_BETS:
            return f"WATCH — ROI {roi:.1f}% on {n} bets, continue paper trading"
        return f"NOT READY — ROI {roi:.1f}%, CLV {clv:.1%}"

    def _empty_report(self, min_days: int) -> dict:
        return {
            "period_days": min_days,
            "all_time": False,
            "total_bets": 0,
            "wins": 0,
            "losses": 0,
            "pushes": 0,
            "winrate": 0.0,
            "profit_units": 0.0,
            "staked_units": 0.0,
            "roi_pct": 0.0,
            "go_live_ready": False,
            "verdict": "NO DATA — start paper trading",
        }

    def _resolve_outcome(self, fixture: Fixture, pick: DailyPick) -> str:
        hg, ag = fixture.home_goals, fixture.away_goals
        if pick.market == "btts":
            if "yes" in pick.selection.lower():
                return "win" if hg > 0 and ag > 0 else "lose"
            return "win" if hg == 0 or ag == 0 else "lose"
        if pick.market == "over_under":
            total = hg + ag
            line = pick.line or 2.5
            if "over" in pick.selection.lower():
                return "push" if total == line else ("win" if total > line else "lose")
            return "push" if total == line else ("win" if total < line else "lose")
        if pick.market == "match_winner":
            sel = pick.selection.lower()
            if sel == "home" and hg > ag:
                return "win"
            if sel == "away" and ag > hg:
                return "win"
            if sel == "draw" and hg == ag:
                return "win"
            return "lose"
        return "void"

    def _profit(self, pick: DailyPick, outcome: str) -> float:
        odds = pick.user_odds or pick.odds
        if outcome == "win":
            return pick.stake_units * (odds - 1)
        if outcome == "lose":
            return -pick.stake_units
        return 0.0
