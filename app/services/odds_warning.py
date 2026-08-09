"""Pre-kickoff adverse odds warning — isolated from pick selection / EV math."""

from __future__ import annotations

from datetime import datetime, timedelta

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import DailyPick, Fixture, OddsSnapshot, Team
from app.database.session import AsyncSessionLocal
from app.services.ingestion import DataIngestionService
from app.telegram.bot import TelegramNotifier
from app.telegram.formatting import format_tip
from app.utils.helpers import normalize_selection, utc_now
from app.utils.odds import median_odds

logger = structlog.get_logger()
settings = get_settings()

WINDOW_MIN_MINUTES = 25
WINDOW_MAX_MINUTES = 35


def odds_jump_pct(initial_odds: float, current_odds: float) -> float | None:
    """Percent jump relative to initial odds. None if inputs are unusable."""
    if initial_odds is None or current_odds is None:
        return None
    if initial_odds <= 1.0 or current_odds <= 1.0:
        return None
    return ((current_odds - initial_odds) / initial_odds) * 100.0


def minutes_to_kickoff(fixture_date: datetime, *, now: datetime | None = None) -> float:
    now = now or utc_now()
    return (fixture_date - now).total_seconds() / 60.0


def in_pre_kickoff_window(
    fixture_date: datetime | None,
    *,
    now: datetime | None = None,
    min_minutes: float = WINDOW_MIN_MINUTES,
    max_minutes: float = WINDOW_MAX_MINUTES,
) -> bool:
    if fixture_date is None:
        return False
    mins = minutes_to_kickoff(fixture_date, now=now)
    return min_minutes <= mins <= max_minutes


def format_odds_warning_message(
    *,
    home: str,
    away: str,
    market: str,
    selection: str,
    line: float | None,
    initial_odds: float,
    current_odds: float,
    jump_pct: float,
) -> str:
    tip = format_tip(market, selection, line)
    return (
        "⚠️ UPOZORENJE / NE UPLAĆIVATI!\n"
        "\n"
        f"⚽ Meč: {home} vs {away}\n"
        f"{tip}\n"
        f"📉 Prvobitna kvota: {initial_odds:.2f}\n"
        f"📈 Trenutna kvota: {current_odds:.2f} (+{jump_pct:.1f}% skok)\n"
        "\n"
        "🚫 Savet: Kvota je znatno skočila u poslednjih pola sata. "
        "Preporuka je da se ovaj tip preskoči."
    )


def _pending_outcome_filter():
    return or_(
        DailyPick.outcome.is_(None),
        DailyPick.outcome == "",
        func.lower(DailyPick.outcome) == "pending",
    )


def _selection_matches(snap_selection: str, pick_selection: str) -> bool:
    return normalize_selection(snap_selection) == normalize_selection(pick_selection)


def _line_matches(snap_line: float | None, pick_line: float | None) -> bool:
    if pick_line is None and snap_line is None:
        return True
    if pick_line is None or snap_line is None:
        # over_under picks usually store line; tolerate missing snap line when
        # selection text already embeds it (e.g. "Under 2.5").
        return True
    return abs(float(snap_line) - float(pick_line)) < 1e-6


async def median_current_odds_for_pick(
    session: AsyncSession,
    pick: DailyPick,
) -> float | None:
    """Latest current_odds per bookmaker, then median — same idea as pick selector."""
    result = await session.execute(
        select(OddsSnapshot)
        .where(
            OddsSnapshot.fixture_id == pick.fixture_id,
            OddsSnapshot.market == pick.market,
        )
        .order_by(OddsSnapshot.captured_at.desc())
    )
    latest_by_book: dict[str, float] = {}
    for snap in result.scalars().all():
        if not _selection_matches(snap.selection, pick.selection):
            continue
        if not _line_matches(snap.line, pick.line):
            continue
        if snap.current_odds is None or snap.current_odds <= 1.0:
            continue
        if snap.bookmaker in latest_by_book:
            continue
        latest_by_book[snap.bookmaker] = float(snap.current_odds)

    if not latest_by_book:
        return None
    return median_odds(list(latest_by_book.values()))


class OddsWarningService:
    """Check pending picks ~30 min before kickoff for adverse odds jumps."""

    async def run_once(self) -> dict:
        now = utc_now()
        window_start = now + timedelta(minutes=WINDOW_MIN_MINUTES)
        window_end = now + timedelta(minutes=WINDOW_MAX_MINUTES)
        threshold_pct = float(settings.pre_kickoff_adverse_jump_pct) * 100.0

        stats = {
            "candidates": 0,
            "checked": 0,
            "warned": 0,
            "skipped_no_odds": 0,
            "skipped_below_threshold": 0,
            "send_failed": 0,
        }

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(DailyPick, Fixture)
                .join(Fixture, Fixture.id == DailyPick.fixture_id)
                .where(
                    _pending_outcome_filter(),
                    or_(
                        DailyPick.warning_sent.is_(False),
                        DailyPick.warning_sent.is_(None),
                    ),
                    Fixture.status == "NS",
                    Fixture.fixture_date >= window_start,
                    Fixture.fixture_date <= window_end,
                )
                .order_by(Fixture.fixture_date.asc())
            )
            rows = list(result.all())
            stats["candidates"] = len(rows)
            if not rows:
                logger.info("pre_kickoff_odds_warnings_idle", **stats)
                return stats

            ingest = DataIngestionService(session)
            fixture_ids = {fixture.id for _, fixture in rows}
            for fixture_id in fixture_ids:
                try:
                    await ingest.ingest_odds(fixture_id)
                except Exception as exc:
                    logger.warning(
                        "pre_kickoff_ingest_failed",
                        fixture_id=fixture_id,
                        error=str(exc),
                    )
            await session.commit()

            team_ids = {
                tid
                for _, fixture in rows
                for tid in (fixture.home_team_id, fixture.away_team_id)
            }
            team_result = await session.execute(select(Team).where(Team.id.in_(team_ids)))
            teams = {t.id: t for t in team_result.scalars().all()}

            notifier = TelegramNotifier()
            for pick, fixture in rows:
                if not in_pre_kickoff_window(fixture.fixture_date, now=now):
                    continue
                stats["checked"] += 1

                current = await median_current_odds_for_pick(session, pick)
                if current is None:
                    stats["skipped_no_odds"] += 1
                    logger.info(
                        "pre_kickoff_odds_missing",
                        pick_id=pick.id,
                        fixture_id=pick.fixture_id,
                    )
                    continue

                jump = odds_jump_pct(pick.odds, current)
                if jump is None or jump < threshold_pct:
                    stats["skipped_below_threshold"] += 1
                    continue

                home = teams.get(fixture.home_team_id)
                away = teams.get(fixture.away_team_id)
                home_name = home.name if home else "Home"
                away_name = away.name if away else "Away"

                text = format_odds_warning_message(
                    home=home_name,
                    away=away_name,
                    market=pick.market,
                    selection=pick.selection,
                    line=pick.line,
                    initial_odds=pick.odds,
                    current_odds=current,
                    jump_pct=jump,
                )
                sent = await notifier.send_odds_warning(text)
                if not sent:
                    stats["send_failed"] += 1
                    logger.warning(
                        "pre_kickoff_warning_send_failed",
                        pick_id=pick.id,
                        fixture_id=pick.fixture_id,
                    )
                    continue

                pick.warning_sent = True
                stats["warned"] += 1
                logger.info(
                    "pre_kickoff_odds_warning_sent",
                    pick_id=pick.id,
                    fixture_id=pick.fixture_id,
                    initial_odds=pick.odds,
                    current_odds=current,
                    jump_pct=round(jump, 2),
                )

            await session.commit()

        logger.info("pre_kickoff_odds_warnings_complete", **stats)
        return stats
