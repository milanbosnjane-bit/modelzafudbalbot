"""Read-only REST endpoints for the iOS mobile app (isolated from core bot logic)."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import DailyPick, Fixture, Team
from app.database.session import get_db
from app.predictions.probability_layer import is_disabled_market
from app.utils.model_paths import resolve_dc_params_path

mobile_router = APIRouter(tags=["mobile"])
settings = get_settings()

FIXTURE_FINISHED_STATUSES = frozenset({"FT", "AET", "PEN", "AWD", "WO"})
FIXTURE_LIVE_STATUSES = frozenset({"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT", "SUSP", "BREAK"})

# Same window the Telegram LIVE PICKS list uses (app.telegram.stats_service.get_picks_from_db).
PICKS_WINDOW_DAYS = 7


class TodayPickResponse(BaseModel):
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
    status: str = "PENDING"


class SettledPickResponse(BaseModel):
    rank: int
    match: str
    home_abbr: str
    away_abbr: str
    market: str
    selection: str
    odds: float
    outcome: str
    profit_units: float | None
    clv: float | None
    pick_date: datetime
    score: str | None = None


class OddsTrackerRow(BaseModel):
    fixture_id: int
    match_title: str
    pick_selection: str
    initial_odds: float
    current_odds: float
    odds_change_pct: float


class BotStatusResponse(BaseModel):
    status: str
    version: str
    dc_engine: str
    api_configured: bool
    max_daily_picks: int
    league_count: int
    use_calibrated_confidence: bool
    max_open_fixtures: int


def _abbr(name: str, length: int = 4) -> str:
    cleaned = "".join(ch for ch in name.upper() if ch.isalnum())
    if len(cleaned) >= length:
        return cleaned[:length]
    return (cleaned + "XXXX")[:length]


def _pick_selection_label(market: str, selection: str, line: float | None) -> str:
    """Human label for the exact tip the bot proposed (not full 1X2 board)."""
    from app.predictions.market_selection import format_prediction_selection

    base = format_prediction_selection(market, selection, line)
    return {
        "Home": "Pobeda Domaćina",
        "Away": "Pobeda Gosta",
        "Draw": "Nerešeno",
    }.get(base, base)


def _odds_change_pct(initial: float, current: float) -> float:
    from app.utils.helpers import odds_change_pct

    if initial <= 0 or current <= 0:
        return 0.0
    return round(odds_change_pct(initial, current), 6)


def _pending_outcome_filter():
    return or_(
        DailyPick.outcome.is_(None),
        DailyPick.outcome == "",
        func.lower(DailyPick.outcome) == "pending",
    )


def _is_fixture_finished(fixture_status: str | None) -> bool:
    return (fixture_status or "NS").strip().upper() in FIXTURE_FINISHED_STATUSES


def _resolve_status(fixture_status: str | None) -> str:
    """PENDING or LIVE, same rule as app.telegram.pick_status.resolve_pick_status.

    Kept local because the API must not import bot modules.
    """
    if (fixture_status or "NS").strip().upper() in FIXTURE_LIVE_STATUSES:
        return "LIVE"
    return "PENDING"


@mobile_router.get("/picks/today", response_model=list[TodayPickResponse])
async def get_today_picks(db: AsyncSession = Depends(get_db)):
    """
    Otvoreni tipovi — isti sadržaj i isti redosled kao Telegram dugme LIVE PICKS.

    Čita ISKLJUČIVO persistovane redove iz daily_picks (bez Dixon-Coles računa).
    Pipeline: pending u zadnjih 7 dana → meč nije završen (LIVE ostaje) → dedupe
    → sort po EV → rank 1..N. Rank iz baze se NE koristi: on se broji unutar
    jednog generisanja, pa bi se uz dva dnevna prolaza ponavljao (1..6, 1..6).
    """
    cutoff = datetime.utcnow() - timedelta(days=PICKS_WINDOW_DAYS)

    result = await db.execute(
        select(DailyPick)
        .where(
            DailyPick.pick_date >= cutoff,
            _pending_outcome_filter(),
        )
        .order_by(DailyPick.pick_date.desc(), DailyPick.rank)
    )
    db_picks = [p for p in result.scalars().all() if not is_disabled_market(p.market)]
    if not db_picks:
        return []

    fixture_ids = {p.fixture_id for p in db_picks}
    fx_result = await db.execute(select(Fixture).where(Fixture.id.in_(fixture_ids)))
    fixtures = {f.id: f for f in fx_result.scalars().all()}
    team_ids = {
        tid for f in fixtures.values() for tid in (f.home_team_id, f.away_team_id)
    }
    team_result = await db.execute(select(Team).where(Team.id.in_(team_ids)))
    teams = {t.id: t for t in team_result.scalars().all()}

    responses: list[TodayPickResponse] = []
    seen: set[tuple[int, str, str]] = set()
    for pick in db_picks:
        key = (
            pick.fixture_id,
            (pick.market or "").strip().lower(),
            (pick.selection or "").strip().lower(),
        )
        if key in seen:
            continue

        fixture = fixtures.get(pick.fixture_id)
        if fixture and _is_fixture_finished(fixture.status):
            continue
        seen.add(key)

        home_name = away_name = "?"
        if fixture:
            home = teams.get(fixture.home_team_id)
            away = teams.get(fixture.away_team_id)
            home_name = home.name if home else "Home"
            away_name = away.name if away else "Away"

        responses.append(
            TodayPickResponse(
                id=pick.id,
                rank=0,
                match=f"{home_name} vs {away_name}",
                market=pick.market,
                selection=pick.selection,
                odds=pick.odds,
                probability=pick.probability,
                expected_value=pick.expected_value,
                confidence=pick.confidence,
                roi_score=pick.roi_score,
                stake_units=pick.stake_units or 0.0,
                reasoning=pick.reasoning or [],
                kickoff=fixture.fixture_date if fixture else None,
                status=_resolve_status(fixture.status if fixture else None),
            )
        )

    responses.sort(key=lambda r: (r.expected_value, r.roi_score), reverse=True)
    for index, row in enumerate(responses, start=1):
        row.rank = index
    return responses


@mobile_router.get("/status", response_model=BotStatusResponse)
async def mobile_bot_status():
    from app import __version__

    dc_path = resolve_dc_params_path()
    dc_engine = "online" if dc_path else "online_default_params"
    return BotStatusResponse(
        status="ok",
        version=__version__,
        dc_engine=dc_engine,
        api_configured=bool(settings.api_football_key),
        max_daily_picks=settings.max_daily_picks,
        league_count=len(settings.league_ids),
        use_calibrated_confidence=getattr(settings, "use_calibrated_confidence", False),
        max_open_fixtures=getattr(settings, "max_open_fixtures", 0),
    )


@mobile_router.get("/picks/recent", response_model=list[SettledPickResponse])
async def get_recent_picks(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DailyPick)
        .where(DailyPick.outcome.in_(("win", "lose", "push")))
        .order_by(DailyPick.pick_date.desc())
        .limit(limit * 3)
    )
    picks = [p for p in result.scalars().all() if not is_disabled_market(p.market)][:limit]

    responses: list[SettledPickResponse] = []
    for pick in picks:
        fixture = await db.get(Fixture, pick.fixture_id)
        home_name = away_name = "Unknown"
        score = None
        if fixture:
            home = await db.get(Team, fixture.home_team_id)
            away = await db.get(Team, fixture.away_team_id)
            home_name = home.name if home else "Home"
            away_name = away.name if away else "Away"
            if fixture.home_goals is not None and fixture.away_goals is not None:
                score = f"{fixture.home_goals}-{fixture.away_goals}"

        clv_val = pick.clv_raw if pick.clv_raw is not None else pick.clv
        responses.append(
            SettledPickResponse(
                rank=pick.rank,
                match=f"{home_name} vs {away_name}",
                home_abbr=_abbr(home_name),
                away_abbr=_abbr(away_name),
                market=pick.market,
                selection=pick.selection,
                odds=pick.odds,
                outcome=pick.outcome,
                profit_units=pick.profit_units,
                clv=clv_val,
                pick_date=pick.pick_date,
                score=score,
            )
        )
    return responses


@mobile_router.get("/odds/tracker", response_model=list[OddsTrackerRow])
async def get_odds_tracker(
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Realtime odds for pending daily picks — only the proposed selection, not full 1X2."""
    from app.services.odds_warning import median_current_odds_for_pick

    cutoff = datetime.utcnow() - timedelta(days=PICKS_WINDOW_DAYS)
    pick_result = await db.execute(
        select(DailyPick)
        .where(
            DailyPick.pick_date >= cutoff,
            _pending_outcome_filter(),
        )
        .order_by(DailyPick.pick_date.desc(), DailyPick.rank)
    )
    picks = [p for p in pick_result.scalars().all() if not is_disabled_market(p.market)]

    rows: list[OddsTrackerRow] = []
    seen_keys: set[tuple[int, str, str, float | None]] = set()
    for pick in picks:
        fixture = await db.get(Fixture, pick.fixture_id)
        if not fixture or _is_fixture_finished(fixture.status):
            continue
        dedupe_key = (pick.fixture_id, pick.market, pick.selection, pick.line)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        home = await db.get(Team, fixture.home_team_id)
        away = await db.get(Team, fixture.away_team_id)
        home_name = home.name if home else "Home"
        away_name = away.name if away else "Away"

        initial = float(pick.odds)
        if initial <= 1.0:
            continue
        current = await median_current_odds_for_pick(db, pick)
        if current is None or current <= 1.0:
            current = initial

        rows.append(
            OddsTrackerRow(
                fixture_id=pick.fixture_id,
                match_title=f"{home_name} vs {away_name}",
                pick_selection=_pick_selection_label(pick.market, pick.selection, pick.line),
                initial_odds=round(initial, 2),
                current_odds=round(float(current), 2),
                odds_change_pct=_odds_change_pct(initial, float(current)),
            )
        )
        if len(rows) >= limit:
            break

    return rows
