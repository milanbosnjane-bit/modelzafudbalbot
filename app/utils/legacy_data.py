"""Identify Football-Data legacy import rows vs API-Football ingest."""

from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.sql import ColumnElement

from app.database.models import Fixture, OddsSnapshot

# Same bookmakers as app/services/legacy_history_importer.py
LEGACY_BOOKMAKERS: frozenset[str] = frozenset({"football-data", "football-data-ref"})


def has_api_odds_exists() -> ColumnElement[bool]:
    """True when fixture has at least one non-legacy odds snapshot."""
    return exists(
        select(1)
        .where(
            OddsSnapshot.fixture_id == Fixture.id,
            OddsSnapshot.bookmaker.not_in(tuple(LEGACY_BOOKMAKERS)),
        )
        .correlate(Fixture)
    )


def is_legacy_bookmaker(name: str | None) -> bool:
    return (name or "").strip().lower() in LEGACY_BOOKMAKERS


def fixture_has_api_odds(session, fixture_id: int) -> bool:
    """Sync/async-safe: fixture has at least one non-legacy bookmaker snapshot."""
    row = session.execute(
        select(OddsSnapshot.id)
        .where(
            OddsSnapshot.fixture_id == fixture_id,
            OddsSnapshot.bookmaker.not_in(tuple(LEGACY_BOOKMAKERS)),
        )
        .limit(1)
    ).first()
    return row is not None
