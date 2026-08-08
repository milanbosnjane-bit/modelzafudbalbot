"""Read-only REST endpoints for the iOS mobile app (isolated from core bot logic)."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import DailyPick, Fixture, OddsSnapshot, Team
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


class OddsSelectionResponse(BaseModel):
    odds: float
    direction: str  # up | down | flat


class OddsTrackerRow(BaseModel):
    fixture_id: int
    match: str
    home_abbr: str
    away_abbr: str
    home_logo: str | None = None
    away_logo: str | None = None
    home: OddsSelectionResponse
    draw: OddsSelectionResponse
    away: OddsSelectionResponse
    kickoff: datetime | None = None


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


def _odds_direction(current: float, opening: float | None) -> str:
    if opening is None or opening <= 0:
        return "flat"
    delta = (current - opening) / opening
    if delta >= 0.005:
        return "up"
    if delta <= -0.005:
        return "down"
    return "flat"


def _normalize_selection(raw: str) -> str:
    value = raw.lower().strip()
    if value in {"home", "1", "h"}:
        return "home"
    if value in {"draw", "x", "d"}:
        return "draw"
    if value in {"away", "2", "a"}:
        return "away"
    return value


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
    limit: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """Live 1X2 odds rows for upcoming fixtures (today's picks first, then NS fixtures)."""
    now = datetime.utcnow()
    today_start = datetime.combine(now.date(), datetime.min.time())

    pick_result = await db.execute(
        select(DailyPick)
        .where(DailyPick.pick_date >= today_start)
        .order_by(DailyPick.rank)
    )
    fixture_ids: list[int] = []
    seen: set[int] = set()
    for pick in pick_result.scalars().all():
        if pick.fixture_id not in seen:
            seen.add(pick.fixture_id)
            fixture_ids.append(pick.fixture_id)

    if len(fixture_ids) < limit:
        fx_result = await db.execute(
            select(Fixture)
            .where(
                Fixture.fixture_date >= now,
                Fixture.fixture_date <= now + timedelta(hours=48),
                Fixture.status == "NS",
            )
            .order_by(Fixture.fixture_date)
            .limit(limit * 2)
        )
        for fixture in fx_result.scalars().all():
            if fixture.id not in seen:
                seen.add(fixture.id)
                fixture_ids.append(fixture.id)
            if len(fixture_ids) >= limit:
                break

    rows: list[OddsTrackerRow] = []
    for fixture_id in fixture_ids[:limit]:
        fixture = await db.get(Fixture, fixture_id)
        if not fixture:
            continue

        home = await db.get(Team, fixture.home_team_id)
        away = await db.get(Team, fixture.away_team_id)
        home_name = home.name if home else "Home"
        away_name = away.name if away else "Away"

        odds_result = await db.execute(
            select(OddsSnapshot).where(
                OddsSnapshot.fixture_id == fixture_id,
                OddsSnapshot.market == "match_winner",
            )
        )
        snapshots = odds_result.scalars().all()

        by_sel: dict[str, OddsSnapshot] = {}
        for snap in snapshots:
            key = _normalize_selection(snap.selection)
            existing = by_sel.get(key)
            if existing is None or snap.captured_at > existing.captured_at:
                by_sel[key] = snap

        def _build(sel: str, fallback: float = 0.0) -> OddsSelectionResponse:
            snap = by_sel.get(sel)
            if not snap:
                return OddsSelectionResponse(odds=fallback, direction="flat")
            opening = snap.opening_odds or snap.current_odds
            return OddsSelectionResponse(
                odds=round(snap.current_odds, 2),
                direction=_odds_direction(snap.current_odds, opening),
            )

        home_odds = _build("home", 0.0)
        draw_odds = _build("draw", 0.0)
        away_odds = _build("away", 0.0)
        if home_odds.odds == 0 and draw_odds.odds == 0 and away_odds.odds == 0:
            continue

        rows.append(
            OddsTrackerRow(
                fixture_id=fixture_id,
                match=f"{home_name} vs {away_name}",
                home_abbr=_abbr(home_name),
                away_abbr=_abbr(away_name),
                home_logo=getattr(home, "logo_url", None) if home else None,
                away_logo=getattr(away, "logo_url", None) if away else None,
                home=home_odds,
                draw=draw_odds,
                away=away_odds,
                kickoff=fixture.fixture_date,
            )
        )

    return rows
