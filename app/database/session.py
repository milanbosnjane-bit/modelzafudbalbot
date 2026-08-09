"""Database session management."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.database.models import Base

settings = get_settings()

_engine_kwargs: dict = {"echo": settings.app_debug}
if settings.database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 60}
else:
    _engine_kwargs["pool_pre_ping"] = True
    _engine_kwargs["pool_size"] = 10
    _engine_kwargs["max_overflow"] = 20

async_engine = create_async_engine(settings.database_url, **_engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

_sync_kwargs: dict = {}
if settings.database_url_sync.startswith("sqlite"):
    _sync_kwargs["connect_args"] = {"check_same_thread": False}

sync_engine = create_engine(settings.database_url_sync, pool_pre_ping=True, **_sync_kwargs)
SyncSessionLocal = sessionmaker(bind=sync_engine, autocommit=False, autoflush=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if settings.database_url.startswith("sqlite"):
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.run_sync(_sqlite_migrate_daily_picks)
            await conn.run_sync(_sqlite_migrate_confidence)


def _sqlite_migrate_daily_picks(conn) -> None:
    """Add manual betting columns to existing SQLite DBs."""
    rows = conn.execute(text("PRAGMA table_info(daily_picks)")).fetchall()
    existing = {row[1] for row in rows}
    if "played_manually" not in existing:
        conn.execute(
            text("ALTER TABLE daily_picks ADD COLUMN played_manually BOOLEAN DEFAULT 0")
        )
    if "user_odds" not in existing:
        conn.execute(text("ALTER TABLE daily_picks ADD COLUMN user_odds FLOAT"))
    if "clv_raw" not in existing:
        conn.execute(text("ALTER TABLE daily_picks ADD COLUMN clv_raw FLOAT"))
    if "closing_fair_edge" not in existing:
        conn.execute(text("ALTER TABLE daily_picks ADD COLUMN closing_fair_edge FLOAT"))
    if "calibrated_confidence" not in existing:
        conn.execute(text("ALTER TABLE daily_picks ADD COLUMN calibrated_confidence FLOAT"))
    if "calibrated_ev" not in existing:
        conn.execute(text("ALTER TABLE daily_picks ADD COLUMN calibrated_ev FLOAT"))
    if "warning_sent" not in existing:
        conn.execute(
            text("ALTER TABLE daily_picks ADD COLUMN warning_sent BOOLEAN DEFAULT 0")
        )


def _sqlite_migrate_confidence(conn) -> None:
    """Ensure confidence calibrator tables/columns exist on legacy SQLite DBs."""
    tables = {
        row[0]
        for row in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    }
    if "confidence_prediction_logs" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE confidence_prediction_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    daily_pick_id INTEGER,
                    fixture_id INTEGER NOT NULL,
                    predicted_at DATETIME NOT NULL,
                    dixon_coles_probability FLOAT NOT NULL,
                    market_fair_probability FLOAT NOT NULL,
                    edge FLOAT NOT NULL,
                    raw_ev FLOAT NOT NULL,
                    odds FLOAT NOT NULL,
                    market VARCHAR(50) NOT NULL,
                    selection VARCHAR(100) NOT NULL,
                    league_id INTEGER,
                    home_ft_count INTEGER,
                    away_ft_count INTEGER,
                    used_default_lambda BOOLEAN DEFAULT 0,
                    home_lambda FLOAT,
                    away_lambda FLOAT,
                    feature_quality FLOAT,
                    hours_to_kickoff FLOAT,
                    old_confidence FLOAT NOT NULL,
                    calibrated_confidence FLOAT,
                    calibrated_ev FLOAT,
                    outcome VARCHAR(20) DEFAULT 'pending',
                    snapshot_json JSON,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(daily_pick_id) REFERENCES daily_picks(id),
                    FOREIGN KEY(fixture_id) REFERENCES fixtures(id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_confidence_logs_predicted "
                "ON confidence_prediction_logs (predicted_at)"
            )
        )
