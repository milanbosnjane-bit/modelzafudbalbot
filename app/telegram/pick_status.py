"""Pick status: PENDING / LIVE / SETTLED."""

from __future__ import annotations

from datetime import datetime

FIXTURE_LIVE_STATUSES = frozenset({
    "1H",
    "2H",
    "HT",
    "ET",
    "BT",
    "P",
    "LIVE",
    "INT",
    "SUSP",
    "BREAK",
})

FIXTURE_FINISHED_STATUSES = frozenset({
    "FT",
    "AET",
    "PEN",
    "AWD",
    "WO",
})

# Alias for stats_service
FINISHED_FIXTURE_STATUSES = FIXTURE_FINISHED_STATUSES


def is_fixture_pre_kickoff(
    fixture_date: datetime | None,
    fixture_status: str | None,
    *,
    now: datetime | None = None,
) -> bool:
    """True ako meč još nije počeo — pogodan za LIVE/PENDING prikaz."""
    from app.utils.helpers import utc_now

    now = now or utc_now()
    if fixture_date is not None and fixture_date <= now:
        return False
    fs = (fixture_status or "NS").strip().upper()
    if fs in FIXTURE_FINISHED_STATUSES or fs in FIXTURE_LIVE_STATUSES:
        return False
    return True


def resolve_pick_status(
    outcome: str | None,
    fixture_status: str | None,
    row_status: str | None = None,
) -> str:
    """
    Derive pick status for Telegram LIVE PICKS.

    Fallback: explicit row status, else outcome + fixture status.
    """
    explicit = (row_status or "").strip().upper()
    if explicit in ("PENDING", "LIVE", "SETTLED"):
        return explicit

    oc = (outcome or "pending").strip().lower()
    if oc in ("win", "lose", "push", "void"):
        return "SETTLED"

    fs = (fixture_status or "NS").strip().upper()
    if fs in FIXTURE_LIVE_STATUSES:
        return "LIVE"

    return "PENDING"
