"""FastAPI route handlers."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import DailyPick, Fixture, Prediction
from app.database.session import get_db
from app.predictions.pipeline import PredictionPipeline
from app.predictions.staking import StakeMethod, StakingCalculator
from app.services.clv_tracker import CLVTracker
from app.services.drift_monitor import DriftMonitor
from app.services.edge_benchmark import EdgeBenchmark
from app.services.ingestion import DataIngestionService
from app.services.paper_trading import PaperTradingService
from app.services.retrain_manager import RetrainManager
from app.training.backtest import BacktestEngine
from app.api.mobile_routes import mobile_router
from app.calibrate_models import calibrate_async

router = APIRouter()
router.include_router(mobile_router)
settings = get_settings()


class HealthResponse(BaseModel):
    status: str
    version: str


class PickResponse(BaseModel):
    id: int
    rank: int
    match: str
    market: str
    selection: str
    odds: float
    probability: float
    expected_value: float
    confidence: float
    roi_score: float
    stake_units: float
    reasoning: list[str]
    kickoff: datetime | None = None


class BacktestRequest(BaseModel):
    start_date: datetime
    end_date: datetime
    name: str = "manual_backtest"
    slippage_pct: float | None = None
    decision_hours_before_kickoff: float | None = None


@router.get("/health", response_model=HealthResponse)
async def health():
    from app import __version__
    return HealthResponse(status="ok", version=__version__)


@router.post("/ingest")
async def trigger_ingest(
    date: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    service = DataIngestionService(db)
    result = await service.full_daily_ingest(date)
    return {"status": "complete", "results": result}


@router.post("/predict")
async def trigger_predictions(date: str | None = None):
    pipeline = PredictionPipeline()
    picks = await pipeline.run_daily(date)
    return {
        "picks_count": len(picks),
        "picks": [
            PickResponse(
                id=p.pick_id or p.fixture_id,
                rank=p.rank,
                match=p.match_label,
                market=p.market,
                selection=p.selection,
                odds=p.odds,
                probability=p.probability,
                expected_value=p.expected_value,
                confidence=p.confidence,
                roi_score=p.roi_score,
                stake_units=p.stake_units,
                reasoning=p.reasoning,
                kickoff=p.fixture_date,
            )
            for p in picks
        ],
    }


@router.get("/picks/today", response_model=list[PickResponse])
async def get_today_picks():
    """Aktivni tipovi — deduplikovano kao Telegram LIVE PICKS (top 6 po EV)."""
    from app.telegram.pick_output import prepare_live_picks
    from app.telegram.stats_service import get_picks_from_db

    raw = await get_picks_from_db()
    active, _ = prepare_live_picks(raw, max_display=6)

    return [
        PickResponse(
            id=row.pick.pick_id or row.pick.fixture_id,
            rank=row.pick.rank,
            match=row.pick.match_label,
            market=row.pick.market,
            selection=row.pick.selection,
            odds=row.pick.odds,
            probability=row.pick.probability,
            expected_value=row.pick.expected_value,
            confidence=row.pick.confidence,
            roi_score=row.pick.roi_score,
            stake_units=row.pick.stake_units,
            reasoning=row.pick.reasoning or [],
            kickoff=row.pick.fixture_date,
        )
        for row in active
    ]


@router.post("/calibrate")
async def trigger_calibration(
    lookback_days: int | None = Query(None, ge=30, le=1095),
    include_legacy: bool = Query(False),
):
    result = await calibrate_async(
        lookback_days=lookback_days,
        exclude_legacy=not include_legacy,
    )
    return {"status": "complete", "results": result}


@router.post("/train")
async def trigger_training_deprecated(
    lookback_days: int | None = Query(None, ge=30, le=1095),
):
    """Deprecated alias — koristi /calibrate (Dixon-Coles MLE)."""
    result = await calibrate_async(lookback_days=lookback_days, exclude_legacy=True)
    return {
        "status": "complete",
        "deprecated": True,
        "message": "Use POST /api/v1/calibrate — ML training removed in v3",
        "results": result,
    }


@router.post("/backtest")
async def run_backtest(request: BacktestRequest):
    engine = BacktestEngine(
        slippage_pct=request.slippage_pct,
        decision_hours=request.decision_hours_before_kickoff,
    )
    result = await engine.run(request.start_date, request.end_date, request.name)
    return {
        "total_bets": result.total_bets,
        "roi_pct": result.roi_pct,
        "avg_clv": result.avg_clv,
        "avg_ev": result.avg_ev,
        "win_rate": result.win_rate,
        "sharpe_ratio": result.sharpe_ratio,
        "clv_coverage_pct": result.clv_coverage_pct,
    }


@router.get("/clv/summary")
async def clv_summary():
    tracker = CLVTracker()
    return await tracker.get_clv_summary()


@router.post("/clv/update")
async def update_clv():
    tracker = CLVTracker()
    result = await tracker.batch_update_clv()
    return result


@router.get("/edge/summary")
async def edge_summary():
    benchmark = EdgeBenchmark()
    return benchmark.aggregate_report(paper_only=True)


@router.post("/edge/update")
async def update_edge():
    benchmark = EdgeBenchmark()
    count = await benchmark.batch_update()
    return {"updated": count}


@router.get("/paper/evaluate")
async def paper_evaluate(days: int = Query(30, ge=7, le=90)):
    service = PaperTradingService()
    return service.evaluate(min_days=days)


@router.post("/paper/settle")
async def paper_settle():
    service = PaperTradingService()
    count = await service.settle_finished_picks()
    return {"settled": count}


@router.get("/metrics")
async def model_metrics(days: int = Query(30, ge=1, le=365)):
    tracker = CLVTracker()
    return await tracker.compute_model_metrics(days=days)


@router.post("/telegram/send")
async def send_telegram_picks(db: AsyncSession = Depends(get_db)):
    from app.telegram.bot import TelegramNotifier

    today = datetime.utcnow().date()
    result = await db.execute(
        select(DailyPick).where(
            DailyPick.pick_date >= datetime.combine(today, datetime.min.time()),
        ).order_by(DailyPick.rank)
    )
    db_picks = result.scalars().all()

    if not db_picks:
        raise HTTPException(status_code=404, detail="No picks found for today")

    from app.predictions.pick_selector import SelectedPick

    picks = []
    for p in db_picks:
        fixture = await db.get(Fixture, p.fixture_id)
        from app.database.models import Team
        home = await db.get(Team, fixture.home_team_id) if fixture else None
        away = await db.get(Team, fixture.away_team_id) if fixture else None

        picks.append(
            SelectedPick(
                fixture_id=p.fixture_id,
                match_label=f"{home.name if home else 'Home'} vs {away.name if away else 'Away'}",
                market=p.market,
                selection=p.selection,
                odds=p.odds,
                opening_odds=p.opening_odds,
                fair_implied_prob=p.fair_implied_prob or 0.5,
                line=p.line,
                expected_return=p.expected_value,
                probability=p.probability,
                expected_value=p.expected_value,
                confidence=p.confidence,
                pick_rank_score=p.roi_score,
                stake_units=p.stake_units,
                stake_method=p.stake_method,
                market_regime=p.market_regime or "moderate",
                reasoning=p.reasoning or [],
                rank=p.rank,
                fixture_date=fixture.fixture_date if fixture else None,
            )
        )

    notifier = TelegramNotifier()
    success = await notifier.send_daily_picks(picks)
    return {"sent": success, "picks_count": len(picks)}


@router.get("/config")
async def get_config():
    return {
        "min_ev_threshold": settings.min_ev_threshold,
        "min_confidence_threshold": settings.min_confidence_threshold,
        "max_daily_picks": settings.max_daily_picks,
        "kelly_fraction": settings.kelly_fraction,
        "supported_markets": settings.supported_markets,
        "league_ids": settings.league_ids,
    }


@router.get("/drift/status")
async def drift_status():
    monitor = DriftMonitor()
    return monitor.get_latest_status()


@router.post("/drift/run")
async def drift_run():
    monitor = DriftMonitor()
    snapshots = await monitor.collect_current_cycle_features()
    result = await monitor.run_and_persist(snapshots)
    return result


@router.post("/retrain/evaluate")
async def retrain_evaluate(execute: bool = Query(False)):
    manager = RetrainManager()
    return await manager.check_and_retrain(execute=execute)
