"""FastAPI application entry point."""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import router
from app.config import get_settings
from app.database.session import init_db
from app.utils.cache import cache

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    await init_db()
    settings.model_dir.mkdir(parents=True, exist_ok=True)
    settings.feature_dir.mkdir(parents=True, exist_ok=True)
    yield
    await cache.disconnect()


app = FastAPI(
    title="Football ROI Prediction System",
    description=(
        "Production-grade football betting system optimized for long-term ROI and CLV, "
        "NOT win rate. Generates exactly 6 daily picks with highest expected profitability."
    ),
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1", tags=["football-roi"])


@app.get("/")
async def root():
    return {
        "name": "Football ROI Prediction System",
        "version": __version__,
        "objective": "Long-term ROI via positive EV and CLV",
        "docs": "/docs",
    }
