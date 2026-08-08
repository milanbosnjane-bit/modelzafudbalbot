"""APScheduler for continuous data collection."""

import asyncio
import signal
import sys
from zoneinfo import ZoneInfo

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

UTC = ZoneInfo("UTC")

from app.config import get_settings
from app.database.session import AsyncSessionLocal, init_db
from app.services.ingestion import DataIngestionService
from app.predictions.pipeline import PipelineMode, PredictionPipeline
from app.telegram.bot import TelegramNotifier

logger = structlog.get_logger()
settings = get_settings()


async def job_ingest_fixtures():
    pipeline = PredictionPipeline()
    result = await pipeline.run_phase1_build()
    logger.info("job_ingest_fixtures_complete", **result)


async def job_update_odds():
    async with AsyncSessionLocal() as session:
        service = DataIngestionService(session)
        from datetime import datetime, timedelta
        from sqlalchemy import select
        from app.database.models import Fixture

        now = datetime.utcnow()
        result = await session.execute(
            select(Fixture).where(
                Fixture.fixture_date >= now,
                Fixture.fixture_date <= now + timedelta(hours=48),
                Fixture.status == "NS",
            )
        )
        count = lineup_count = injury_count = 0
        for fixture in result.scalars().all():
            count += await service.ingest_odds(fixture.id)
            try:
                injury_count += await service.ingest_injuries(fixture.id)
            except Exception as exc:
                logger.warning("ingest_injuries_failed", fixture_id=fixture.id, error=str(exc))
            hours_until = (fixture.fixture_date - now).total_seconds() / 3600.0
            if hours_until <= 6:
                try:
                    lineup_count += await service.ingest_lineups(fixture.id)
                except Exception as exc:
                    logger.warning("ingest_lineups_failed", fixture_id=fixture.id, error=str(exc))
        logger.info(
            "job_update_odds_complete",
            odds=count,
            injuries=injury_count,
            lineups=lineup_count,
        )


async def job_capture_closing_odds():
    async with AsyncSessionLocal() as session:
        service = DataIngestionService(session)
        count = await service.capture_closing_odds()
        logger.info("job_closing_odds_complete", count=count)


async def job_daily_predictions():
    logger.info("[MODE] live: using cached data only")
    pipeline = PredictionPipeline()
    picks = await pipeline.run_daily(mode=PipelineMode.LIVE)
    if picks:
        notifier = TelegramNotifier()
        await notifier.send_daily_picks(picks)
    logger.info("job_daily_predictions_complete", picks=len(picks))


async def job_paper_settle():
    from app.services.paper_trading import PaperTradingService
    service = PaperTradingService()
    count = await service.settle_finished_picks()
    logger.info("job_paper_settle_complete", settled=count)


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=UTC)

    # UTC cron (srpsko UTC+2): ingest 07:00, pickovi 08:00
    scheduler.add_job(
        job_ingest_fixtures,
        CronTrigger(hour=5, minute=0, timezone=UTC),
        id="ingest_fixtures",
        name="Daily fixture ingestion (07:00 srpsko)",
    )
    scheduler.add_job(
        job_update_odds,
        IntervalTrigger(minutes=30),
        id="update_odds",
        name="Odds refresh every 30 min",
    )
    scheduler.add_job(
        job_capture_closing_odds,
        IntervalTrigger(minutes=5),
        id="closing_odds",
        name="Capture closing odds",
    )
    scheduler.add_job(
        job_daily_predictions,
        CronTrigger(hour=6, minute=0, timezone=UTC),
        id="daily_predictions",
        name="Generate daily picks + Telegram (08:00 srpsko)",
    )
    scheduler.add_job(
        job_paper_settle,
        IntervalTrigger(hours=2),
        id="paper_settle",
        name="Settle paper picks + CLV + edge capture",
    )

    return scheduler


async def main():
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )

    await init_db()
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("scheduler_started")

    stop_event = asyncio.Event()

    def shutdown(sig, frame):
        logger.info("shutdown_signal", signal=sig)
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    await stop_event.wait()
    scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
