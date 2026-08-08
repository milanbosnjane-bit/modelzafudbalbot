"""Build calibrator inputs from DB context at prediction time."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import FeatureVector, Fixture
from app.features.engineer import TEAM_FEATURE_KEYS
from app.model.confidence_calibrator import (
    CalibratorInput,
    detect_default_lambda,
    parse_lambdas_from_reasoning,
)
from app.predictions.pick_selector import SelectedPick
from app.utils.helpers import utc_now


def compute_feature_quality(features: dict | None) -> float:
    """Share of expected home/away team features present (0–1)."""
    if not features:
        return 0.0
    keys = [f"home_{k}" for k in TEAM_FEATURE_KEYS] + [f"away_{k}" for k in TEAM_FEATURE_KEYS]
    present = sum(1 for k in keys if features.get(k) is not None)
    return present / len(keys) if keys else 0.0


async def count_team_ft_matches(
    session: AsyncSession,
    team_id: int,
    before: datetime,
) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(Fixture)
        .where(
            Fixture.status == "FT",
            Fixture.fixture_date < before,
            or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
        )
    )
    return int(result.scalar_one() or 0)


async def load_features_for_fixture(
    session: AsyncSession,
    fixture_id: int,
    as_of: datetime | None = None,
) -> dict | None:
    query = (
        select(FeatureVector)
        .where(FeatureVector.fixture_id == fixture_id)
        .order_by(FeatureVector.as_of_datetime.desc())
    )
    if as_of is not None:
        query = query.where(FeatureVector.as_of_datetime <= as_of)
    row = (await session.execute(query.limit(1))).scalar_one_or_none()
    return row.features if row else None


async def build_calibrator_input(
    session: AsyncSession,
    pick: SelectedPick,
    *,
    predicted_at: datetime | None = None,
    fixture: Fixture | None = None,
    features: dict | None = None,
) -> CalibratorInput:
    predicted_at = predicted_at or utc_now()
    if fixture is None:
        fixture = await session.get(Fixture, pick.fixture_id)

    as_of = predicted_at
    if fixture and fixture.fixture_date:
        as_of = min(predicted_at, fixture.fixture_date)

    if features is None:
        features = await load_features_for_fixture(session, pick.fixture_id, as_of=as_of)

    home_ft = away_ft = None
    league_id = None
    hours_to_kickoff = None
    if fixture:
        league_id = fixture.league_id
        home_ft = await count_team_ft_matches(session, fixture.home_team_id, fixture.fixture_date)
        away_ft = await count_team_ft_matches(session, fixture.away_team_id, fixture.fixture_date)
        hours_to_kickoff = max(
            0.0, (fixture.fixture_date - predicted_at).total_seconds() / 3600.0
        )

    home_lambda, away_lambda = parse_lambdas_from_reasoning(pick.reasoning)
    edge = pick.probability - pick.fair_implied_prob

    return CalibratorInput(
        dixon_coles_probability=pick.probability,
        market_fair_probability=pick.fair_implied_prob,
        edge=edge,
        raw_ev=pick.expected_value,
        odds=pick.odds,
        market=pick.market,
        selection=pick.selection,
        league_id=league_id,
        home_ft_count=home_ft,
        away_ft_count=away_ft,
        used_default_lambda=detect_default_lambda(home_lambda, away_lambda),
        home_lambda=home_lambda,
        away_lambda=away_lambda,
        feature_quality=compute_feature_quality(features),
        hours_to_kickoff=hours_to_kickoff,
        old_confidence=pick.confidence,
        predicted_at=predicted_at,
    )
