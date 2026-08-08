"""Import Football-Data history.db into football_roi.db for ML training."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import FeatureVector, Fixture, League, OddsSnapshot, Team
from app.utils.legacy_data import LEGACY_BOOKMAKERS
from app.database.session import AsyncSessionLocal, init_db
from app.features.engineer import FeatureEngineer
from app.utils.helpers import decision_time
from app.utils.odds import market_overround, proportional_devig

logger = structlog.get_logger()
settings = get_settings()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEGACY_HISTORY_DB = PROJECT_ROOT / "data" / "history.db"

# Football-Data league code -> API-Football league id (aligned with app/config.py)
LEAGUE_CODE_TO_API_ID: dict[str, int] = {
    "E0": 39,    # England Premier League
    "E1": 40,    # England Championship
    "E2": 41,    # England League One
    "SP1": 140,  # Spain La Liga
    "SP2": 141,  # Spain Segunda
    "D1": 78,    # Germany Bundesliga
    "D2": 79,    # Germany 2. Bundesliga
    "I1": 135,  # Italy Serie A
    "I2": 136,  # Italy Serie B
    "F1": 61,    # France Ligue 1
    "F2": 62,    # France Ligue 2
    "N1": 88,    # Netherlands Eredivisie
    "P1": 94,    # Portugal Liga
    "B1": 144,   # Belgium Pro League
    "T1": 203,   # Turkey Super Lig
    "G1": 197,   # Greece Super League
    "SC0": 179,  # Scotland Premiership
}

BATCH_COMMIT = 500
FEATURE_BATCH = 250


def parse_season(raw: str) -> int:
    """Parse Football-Data season label to starting year."""
    text = str(raw).strip()
    if "/" in text:
        return int(text.split("/")[0])
    if len(text) == 4 and text.isdigit():
        century = int(text[:2])
        return 2000 + century if century < 70 else 1900 + century
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 4:
        return int(digits[:4])
    return 2020


def _valid_odds(value: object) -> float | None:
    if value is None:
        return None
    try:
        odds = float(value)
    except (TypeError, ValueError):
        return None
    if odds < 1.01 or odds > 50.0:
        return None
    return odds


def load_legacy_rows(db_path: Path) -> list[dict[str, object]]:
    """Load finished fixtures with odds from legacy history.db."""
    if not db_path.exists():
        raise FileNotFoundError(f"Legacy history not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        query = """
            SELECT
                f.fixture_id AS legacy_fixture_id,
                f.date,
                f.league_code,
                f.league_name,
                f.season,
                f.home_team,
                f.away_team,
                f.home_score,
                f.away_score,
                o.home_odds,
                o.draw_odds,
                o.away_odds,
                o.over25_odds,
                o.under25_odds
            FROM history_fixtures f
            INNER JOIN history_odds o ON o.fixture_id = f.fixture_id
            WHERE f.home_score IS NOT NULL
              AND f.away_score IS NOT NULL
              AND f.league_code IS NOT NULL
            ORDER BY f.date ASC, f.fixture_id ASC
        """
        rows = [dict(row) for row in conn.execute(query)]
    finally:
        conn.close()

    filtered: list[dict[str, object]] = []
    for row in rows:
        code = str(row.get("league_code") or "").strip()
        if code not in LEAGUE_CODE_TO_API_ID:
            continue
        home_odds = _valid_odds(row.get("home_odds"))
        draw_odds = _valid_odds(row.get("draw_odds"))
        away_odds = _valid_odds(row.get("away_odds"))
        over_odds = _valid_odds(row.get("over25_odds"))
        under_odds = _valid_odds(row.get("under25_odds"))
        if None in (home_odds, draw_odds, away_odds, over_odds, under_odds):
            continue
        row["home_odds"] = home_odds
        row["draw_odds"] = draw_odds
        row["away_odds"] = away_odds
        row["over25_odds"] = over_odds
        row["under25_odds"] = under_odds
        filtered.append(row)
    return filtered


async def _get_or_create_league(session: AsyncSession, api_league_id: int, name: str) -> League:
    league = await session.get(League, api_league_id)
    if league:
        return league
    league = League(id=api_league_id, name=name, strength_rating=1.0)
    session.add(league)
    await session.flush()
    return league


async def _get_or_create_team(
    session: AsyncSession,
    *,
    league_id: int,
    name: str,
    cache: dict[tuple[int, str], int],
) -> int:
    key = (league_id, name.strip().lower())
    if key in cache:
        return cache[key]

    result = await session.execute(
        select(Team).where(
            Team.league_id == league_id,
            func.lower(Team.name) == name.strip().lower(),
        )
    )
    team = result.scalar_one_or_none()
    if team is None:
        team = Team(name=name.strip(), league_id=league_id)
        session.add(team)
        await session.flush()
    cache[key] = team.id
    return team.id


async def _fixture_exists(
    session: AsyncSession,
    *,
    league_id: int,
    fixture_date: datetime,
    home_team_id: int,
    away_team_id: int,
) -> Fixture | None:
    result = await session.execute(
        select(Fixture).where(
            Fixture.league_id == league_id,
            Fixture.fixture_date == fixture_date,
            Fixture.home_team_id == home_team_id,
            Fixture.away_team_id == away_team_id,
        )
    )
    return result.scalar_one_or_none()


def _odds_snapshots_for_fixture(
    fixture_id: int,
    *,
    captured_at: datetime,
    home_odds: float,
    draw_odds: float,
    away_odds: float,
    over_odds: float,
    under_odds: float,
) -> list[OddsSnapshot]:
    one_x_two = [home_odds, draw_odds, away_odds]
    fair_1x2 = proportional_devig(one_x_two)
    overround_1x2 = market_overround(one_x_two)
    ou_pair = [over_odds, under_odds]
    fair_ou = proportional_devig(ou_pair)
    overround_ou = market_overround(ou_pair)

    snapshots: list[OddsSnapshot] = []
    for bookmaker in LEGACY_BOOKMAKERS:
        snapshots.extend(
            [
                OddsSnapshot(
                    fixture_id=fixture_id,
                    bookmaker=bookmaker,
                    market="match_winner",
                    selection="home",
                    line=None,
                    opening_odds=home_odds,
                    current_odds=home_odds,
                    closing_odds=home_odds,
                    fair_prob=fair_1x2[0] if fair_1x2 else None,
                    market_overround=overround_1x2,
                    captured_at=captured_at,
                    is_closing=True,
                ),
                OddsSnapshot(
                    fixture_id=fixture_id,
                    bookmaker=bookmaker,
                    market="match_winner",
                    selection="draw",
                    line=None,
                    opening_odds=draw_odds,
                    current_odds=draw_odds,
                    closing_odds=draw_odds,
                    fair_prob=fair_1x2[1] if len(fair_1x2) > 1 else None,
                    market_overround=overround_1x2,
                    captured_at=captured_at,
                    is_closing=True,
                ),
                OddsSnapshot(
                    fixture_id=fixture_id,
                    bookmaker=bookmaker,
                    market="match_winner",
                    selection="away",
                    line=None,
                    opening_odds=away_odds,
                    current_odds=away_odds,
                    closing_odds=away_odds,
                    fair_prob=fair_1x2[2] if len(fair_1x2) > 2 else None,
                    market_overround=overround_1x2,
                    captured_at=captured_at,
                    is_closing=True,
                ),
                OddsSnapshot(
                    fixture_id=fixture_id,
                    bookmaker=bookmaker,
                    market="over_under",
                    selection="over 2.5",
                    line=2.5,
                    opening_odds=over_odds,
                    current_odds=over_odds,
                    closing_odds=over_odds,
                    fair_prob=fair_ou[0] if fair_ou else None,
                    market_overround=overround_ou,
                    captured_at=captured_at,
                    is_closing=True,
                ),
                OddsSnapshot(
                    fixture_id=fixture_id,
                    bookmaker=bookmaker,
                    market="over_under",
                    selection="under 2.5",
                    line=2.5,
                    opening_odds=under_odds,
                    current_odds=under_odds,
                    closing_odds=under_odds,
                    fair_prob=fair_ou[1] if len(fair_ou) > 1 else None,
                    market_overround=overround_ou,
                    captured_at=captured_at,
                    is_closing=True,
                ),
            ]
        )
    return snapshots


async def import_legacy_history(
    *,
    legacy_db_path: Path | None = None,
    build_features: bool = True,
    force: bool = False,
    if_empty: bool = False,
    feature_limit: int | None = None,
) -> dict[str, int]:
    """
    Import legacy history.db rows into football_roi.db.

    Uses mapped European leagues only (LEAGUE_CODE_TO_API_ID).
    Creates two bookmaker snapshots per market so training liquidity checks pass.
    """
    path = legacy_db_path or DEFAULT_LEGACY_HISTORY_DB
    rows = load_legacy_rows(path)
    if not rows:
        raise ValueError(f"No importable rows found in {path}")

    await init_db()

    counters = {
        "legacy_rows": len(rows),
        "fixtures_imported": 0,
        "fixtures_skipped": 0,
        "odds_snapshots": 0,
        "features_built": 0,
    }

    async with AsyncSessionLocal() as session:
        if if_empty and not force:
            existing = await session.execute(select(func.count(Fixture.id)))
            if int(existing.scalar_one() or 0) > 0:
                logger.info("legacy_import_skipped_db_not_empty")
                return {**counters, "skipped": 1}

        team_cache: dict[tuple[int, str], int] = {}
        imported_fixture_ids: list[int] = []

        for index, row in enumerate(rows, start=1):
            api_league_id = LEAGUE_CODE_TO_API_ID[str(row["league_code"])]
            league_name = str(row["league_name"])
            await _get_or_create_league(session, api_league_id, league_name)

            home_id = await _get_or_create_team(
                session,
                league_id=api_league_id,
                name=str(row["home_team"]),
                cache=team_cache,
            )
            away_id = await _get_or_create_team(
                session,
                league_id=api_league_id,
                name=str(row["away_team"]),
                cache=team_cache,
            )

            fixture_date = datetime.fromisoformat(f"{row['date']} 15:00:00")
            existing = await _fixture_exists(
                session,
                league_id=api_league_id,
                fixture_date=fixture_date,
                home_team_id=home_id,
                away_team_id=away_id,
            )
            if existing:
                counters["fixtures_skipped"] += 1
                imported_fixture_ids.append(existing.id)
                continue

            fixture = Fixture(
                league_id=api_league_id,
                season=parse_season(str(row["season"])),
                home_team_id=home_id,
                away_team_id=away_id,
                fixture_date=fixture_date,
                status="FT",
                home_goals=int(row["home_score"]),
                away_goals=int(row["away_score"]),
            )
            session.add(fixture)
            await session.flush()

            captured_at = decision_time(fixture_date, settings.decision_hours_before_kickoff)
            snapshots = _odds_snapshots_for_fixture(
                fixture.id,
                captured_at=captured_at,
                home_odds=float(row["home_odds"]),
                draw_odds=float(row["draw_odds"]),
                away_odds=float(row["away_odds"]),
                over_odds=float(row["over25_odds"]),
                under_odds=float(row["under25_odds"]),
            )
            session.add_all(snapshots)
            counters["fixtures_imported"] += 1
            counters["odds_snapshots"] += len(snapshots)
            imported_fixture_ids.append(fixture.id)

            if index % BATCH_COMMIT == 0:
                await session.commit()
                logger.info("legacy_import_progress", processed=index, imported=counters["fixtures_imported"])

        await session.commit()

        if build_features and imported_fixture_ids:
            result = await session.execute(
                select(Fixture)
                .where(Fixture.id.in_(imported_fixture_ids))
                .order_by(Fixture.fixture_date.asc())
            )
            ordered_fixtures = list(result.scalars().all())
            engineer = FeatureEngineer(session, historical_mode=True)
            as_of_map = {
                fixture.id: decision_time(fixture.fixture_date, settings.decision_hours_before_kickoff)
                for fixture in ordered_fixtures
            }

            fixture_ids = [fixture.id for fixture in ordered_fixtures]
            if feature_limit is not None and feature_limit > 0:
                fixture_ids = fixture_ids[-feature_limit:]

            for start in range(0, len(fixture_ids), FEATURE_BATCH):
                chunk = fixture_ids[start : start + FEATURE_BATCH]
                chunk_as_of = {fid: as_of_map[fid] for fid in chunk}
                built = await engineer.build_batch(chunk, as_of_map=chunk_as_of, persist=True)
                counters["features_built"] += len(built)
                logger.info(
                    "legacy_features_progress",
                    built=counters["features_built"],
                    total=len(fixture_ids),
                )

    logger.info("legacy_import_complete", **counters)
    return counters


async def build_missing_features(
    *,
    feature_limit: int | None = 12000,
) -> dict[str, int]:
    """Build feature vectors for finished fixtures that have odds but no features yet."""
    await init_db()

    counters = {"fixtures_needing": 0, "features_built": 0}

    async with AsyncSessionLocal() as session:
        existing_fv = select(FeatureVector.fixture_id).distinct().scalar_subquery()
        result = await session.execute(
            select(Fixture)
            .where(
                Fixture.status.in_(["FT", "AET", "PEN"]),
                Fixture.home_goals.isnot(None),
                Fixture.id.not_in(existing_fv),
            )
            .order_by(Fixture.fixture_date.asc())
        )
        fixtures = list(result.scalars().all())
        counters["fixtures_needing"] = len(fixtures)
        if not fixtures:
            logger.info("legacy_features_none_missing")
            return counters

        fixture_ids = [fixture.id for fixture in fixtures]
        if feature_limit is not None and feature_limit > 0:
            fixture_ids = fixture_ids[-feature_limit:]

        engineer = FeatureEngineer(session, historical_mode=True)
        as_of_map = {
            fixture.id: decision_time(fixture.fixture_date, settings.decision_hours_before_kickoff)
            for fixture in fixtures
            if fixture.id in fixture_ids
        }

        for start in range(0, len(fixture_ids), FEATURE_BATCH):
            chunk = fixture_ids[start : start + FEATURE_BATCH]
            chunk_as_of = {fid: as_of_map[fid] for fid in chunk}
            built = await engineer.build_batch(chunk, as_of_map=chunk_as_of, persist=True)
            counters["features_built"] += len(built)
            logger.info(
                "legacy_features_progress",
                built=counters["features_built"],
                total=len(fixture_ids),
            )

    logger.info("legacy_features_complete", **counters)
    return counters
