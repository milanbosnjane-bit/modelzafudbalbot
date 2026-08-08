"""Daily prediction pipeline with point-in-time discipline."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.database.models import Fixture
from app.database.session import AsyncSessionLocal
from app.features.engineer import FeatureEngineer
from app.predictions.pick_selector import PickSelectionEngine, SelectedPick
from app.predictions.probability_layer import ev_variance, is_legacy_clamped_ev
from app.services.ingestion import DataIngestionService
from app.services.retrain_manager import RetrainManager
from app.utils.helpers import decision_time

logger = structlog.get_logger()
settings = get_settings()


class PipelineMode(str, Enum):
    LIVE = "live"
    FULL_BUILD = "full-build"


class PipelineDataCorruptionError(RuntimeError):
    """Raised when candidate EV distribution indicates corrupted/fallback data."""


EV_VARIANCE_MIN = 0.01


@dataclass
class PipelineResult:
    picks: list[SelectedPick]
    target_date: str
    fixture_count: int
    lookahead_used: bool = False
    all_already_picked: bool = False


class PredictionPipeline:
    LOOKAHEAD_DAYS = 7
    EV_VARIANCE_MIN = EV_VARIANCE_MIN

    @staticmethod
    def validate_candidate_ev_distribution(candidates: list) -> None:
        if len(candidates) <= 1:
            return
        evs = [c.ensemble.expected_value for c in candidates]
        variance = ev_variance(evs)
        if variance >= EV_VARIANCE_MIN:
            return

        rounded = {round(e, 4) for e in evs}
        if len(rounded) <= 1:
            raise PipelineDataCorruptionError(
                f"EV variance {variance:.6f} below threshold {EV_VARIANCE_MIN} "
                f"with {len(candidates)} candidates — all EV values identical "
                f"({evs[0]:.4f}), likely fallback/clamped EV corruption"
            )
        if all(is_legacy_clamped_ev(e) for e in evs):
            raise PipelineDataCorruptionError(
                f"EV variance {variance:.6f} below threshold {EV_VARIANCE_MIN} "
                f"with {len(candidates)} candidates — legacy clamped EV values detected"
            )

        logger.info(
            "ev_variance_below_threshold_allowed",
            variance=variance,
            unique_ev_levels=len(rounded),
            count=len(candidates),
        )

    async def run_daily(
        self,
        date: str | None = None,
        mode: PipelineMode = PipelineMode.LIVE,
    ) -> list[SelectedPick]:
        result = await self.run_daily_detailed(date, mode=mode)
        return result.picks

    async def run_daily_detailed(
        self,
        date: str | None = None,
        mode: PipelineMode = PipelineMode.LIVE,
    ) -> PipelineResult:
        base = date or datetime.utcnow().strftime("%Y-%m-%d")
        logger.info("pipeline_start", date=base, mode=mode.value)

        for offset in range(self.LOOKAHEAD_DAYS + 1):
            target = (
                datetime.strptime(base, "%Y-%m-%d") + timedelta(days=offset)
            ).strftime("%Y-%m-%d")
            result = await self._run_for_date(target, mode=mode)
            if result.fixture_count > 0:
                if offset > 0:
                    logger.info("pipeline_lookahead_hit", base_date=base, target_date=target)
                    result.lookahead_used = True
                return result

        logger.warning("no_fixtures_found", date=base, lookahead_days=self.LOOKAHEAD_DAYS)
        return PipelineResult(picks=[], target_date=base, fixture_count=0)

    async def run_phase1_build(self, date: str | None = None) -> dict:
        """Phase 1: ingestion + feature engineering (daily job)."""
        target = date or datetime.utcnow().strftime("%Y-%m-%d")
        logger.info("[MODE] full-build: rebuilding dataset", date=target)
        async with AsyncSessionLocal() as session:
            ingestion = DataIngestionService(session)
            ingest_result = await ingestion.full_daily_ingest(target)
            _, _, fixture_ids, as_of_map = await self._load_fixtures(session, target)
            feature_count = 0
            if fixture_ids:
                engineer = FeatureEngineer(session, historical_mode=False)
                features_map = await engineer.build_batch(
                    fixture_ids, as_of_map=as_of_map, persist=True
                )
                feature_count = len(features_map)
            logger.info(
                "phase1_complete",
                date=target,
                fixtures=len(fixture_ids),
                features=feature_count,
            )
            return {**ingest_result, "features_built": feature_count}

    async def _run_for_date(self, date: str, mode: PipelineMode) -> PipelineResult:
        async with AsyncSessionLocal() as session:
            if mode == PipelineMode.FULL_BUILD:
                logger.info("[MODE] full-build: rebuilding dataset", date=date)
                ingestion = DataIngestionService(session)
                await ingestion.full_daily_ingest(date)
            else:
                logger.info("[MODE] live: using cached data only", date=date)

            fixtures_loaded, fixtures, fixture_ids, as_of_map = await self._load_fixtures(
                session, date
            )

            selector = PickSelectionEngine(session)
            already_picked = await selector.get_fixture_ids_picked_today()
            if already_picked:
                before = len(fixture_ids)
                fixture_ids = [fid for fid in fixture_ids if fid not in already_picked]
                fixtures = [f for f in fixtures if f.id in fixture_ids]
                logger.info(
                    "fixtures_already_picked_today",
                    skipped=before - len(fixture_ids),
                    remaining=len(fixture_ids),
                )
            fixtures_with_odds = 0
            eligible = 0
            from app.predictions.market_selection import is_eligible_selection

            for f in fixtures:
                as_of = as_of_map[f.id]
                odds_by_market = await selector._get_decision_odds(f.id, as_of)
                if not odds_by_market:
                    continue
                fixtures_with_odds += 1
                for market, selections in odds_by_market.items():
                    if market not in selector.PICK_MARKETS:
                        continue
                    for selection, odds_info in selections.items():
                        if odds_info["bookmaker_count"] < selector.MIN_LIQUIDITY_BOOKMAKERS:
                            continue
                        if not is_eligible_selection(
                            market, selection, odds_info.get("line"), live=True
                        ):
                            continue
                        eligible += 1

            if not fixture_ids:
                if already_picked:
                    logger.info(
                        "all_fixtures_already_picked_today",
                        skipped=len(already_picked),
                    )
                    return PipelineResult(
                        picks=[],
                        target_date=date,
                        fixture_count=len(fixtures_loaded),
                        all_already_picked=True,
                    )
                logger.info(
                    "DEBUG_FUNNEL",
                    fixtures_loaded=len(fixtures_loaded),
                    fixtures_status_ns=0,
                    fixtures_with_odds=0,
                    after_eligibility=0,
                    final_candidates=0,
                )
                return PipelineResult(picks=[], target_date=date, fixture_count=0)

            await self._refresh_context_data(session, fixture_ids)

            engineer = FeatureEngineer(session, historical_mode=False)
            if mode == PipelineMode.FULL_BUILD:
                features_map = await engineer.build_batch(
                    fixture_ids, as_of_map=as_of_map, persist=True
                )
            else:
                features_map = await engineer.load_batch(fixture_ids, as_of_map=as_of_map)

            candidates = await selector.generate_candidates(
                fixture_ids, features_map, as_of_map=as_of_map
            )
            if candidates:
                self.validate_candidate_ev_distribution(candidates)
            logger.info(
                "DEBUG_FUNNEL",
                fixtures_loaded=len(fixtures_loaded),
                fixtures_status_ns=len(fixtures),
                fixtures_with_odds=fixtures_with_odds,
                after_eligibility=eligible,
                final_candidates=len(candidates),
            )
            picks = await selector.select_top_picks(candidates)

            if picks:
                picks = await selector.persist_picks(picks)
                from app.model.confidence_service import enrich_persisted_picks

                picks = await enrich_persisted_picks(
                    session,
                    picks,
                    features_map=features_map,
                )
                if picks:
                    await session.commit()

            if mode == PipelineMode.FULL_BUILD:
                feature_snapshots = [
                    features_map[fid] for fid in fixture_ids if fid in features_map
                ]
                try:
                    monitoring = await RetrainManager().post_prediction_cycle(
                        feature_snapshots, prediction_time=datetime.utcnow()
                    )
                except Exception as e:
                    logger.warning("post_prediction_monitoring_skipped", error=str(e))
                else:
                    logger.info(
                        "pipeline_complete",
                        date=date,
                        fixtures=len(fixture_ids),
                        picks=len(picks),
                        drift_status=monitoring.get("drift_run", {}).get("status"),
                    )
            else:
                logger.info(
                    "pipeline_complete",
                    date=date,
                    fixtures=len(fixture_ids),
                    picks=len(picks),
                    mode="live",
                )

            return PipelineResult(
                picks=picks,
                target_date=date,
                fixture_count=len(fixture_ids),
            )

    async def _load_fixtures(
        self, session, date: str
    ) -> tuple[list[Fixture], list[Fixture], list[int], dict[int, datetime]]:
        day_start = datetime.strptime(date, "%Y-%m-%d")
        day_end = day_start + timedelta(days=1)

        all_result = await session.execute(
            select(Fixture).where(
                Fixture.fixture_date >= day_start,
                Fixture.fixture_date < day_end,
            )
        )
        excluded = set(settings.exclude_league_ids)
        fixtures_loaded = [
            f for f in all_result.scalars().all() if f.league_id not in excluded
        ]

        result = await session.execute(
            select(Fixture).where(
                Fixture.fixture_date >= day_start,
                Fixture.fixture_date < day_end,
                Fixture.status == "NS",
            )
        )
        fixtures = [
            f for f in result.scalars().all() if f.league_id not in excluded
        ]
        fixture_ids = [f.id for f in fixtures]
        as_of_map = {
            f.id: decision_time(f.fixture_date, settings.decision_hours_before_kickoff)
            for f in fixtures
        }
        return fixtures_loaded, fixtures, fixture_ids, as_of_map

    async def _refresh_context_data(self, session, fixture_ids: list[int]) -> None:
        """Povredi i lineup pre feature build-a (context gates)."""
        if not settings.context_gates_enabled:
            return

        service = DataIngestionService(session)
        now = datetime.utcnow()
        for fixture_id in fixture_ids:
            fixture = await session.get(Fixture, fixture_id)
            if not fixture:
                continue
            try:
                await service.ingest_injuries(fixture_id)
            except Exception as exc:
                logger.warning(
                    "context_refresh_injuries_failed",
                    fixture_id=fixture_id,
                    error=str(exc),
                )
            hours_until = (fixture.fixture_date - now).total_seconds() / 3600.0
            if hours_until <= 8:
                try:
                    await service.ingest_lineups(fixture_id)
                except Exception as exc:
                    logger.warning(
                        "context_refresh_lineups_failed",
                        fixture_id=fixture_id,
                        error=str(exc),
                    )
