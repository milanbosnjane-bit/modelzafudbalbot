"""Apply confidence calibration (display-only layer) and log prediction snapshots."""

from __future__ import annotations

from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import ConfidencePredictionLog, DailyPick, Fixture
from app.model.confidence_calibrator import (
    CalibratorInput,
    get_confidence_calibrator,
    input_to_dict,
)
from app.model.confidence_context import build_calibrator_input
from app.predictions.pick_selector import SelectedPick
from app.utils.helpers import utc_now

logger = structlog.get_logger()
settings = get_settings()


def _format_calibrated_label(value: float | None) -> str:
    if value is None:
        return "nije kalibrisan"
    return f"{value * 100:.0f}%"


async def apply_calibration_to_pick(
    session: AsyncSession,
    pick: SelectedPick,
    *,
    daily_pick: DailyPick | None = None,
    predicted_at: datetime | None = None,
    features: dict | None = None,
    persist_log: bool = True,
) -> SelectedPick:
    """
    Enrich pick with calibrated_confidence without changing DC probability/EV.
    Safe no-op when flag off, model missing, or prediction fails.
    """
    cal_input = await build_calibrator_input(
        session,
        pick,
        predicted_at=predicted_at,
        features=features,
    )

    calibrated_confidence: float | None = None
    calibrated_ev: float | None = None

    if settings.use_calibrated_confidence:
        calibrator = get_confidence_calibrator()
        if calibrator.is_ready:
            try:
                calibrated_confidence = calibrator.predict_proba(cal_input)
                if calibrated_confidence is not None:
                    calibrated_ev = cal_input.calibrated_ev(calibrated_confidence)
            except Exception as exc:
                logger.warning("calibrated_confidence_failed", error=str(exc))

    if daily_pick is not None:
        daily_pick.calibrated_confidence = calibrated_confidence
        daily_pick.calibrated_ev = calibrated_ev

    if persist_log:
        await log_prediction_snapshot(
            session,
            pick,
            cal_input,
            daily_pick=daily_pick,
            calibrated_confidence=calibrated_confidence,
            calibrated_ev=calibrated_ev,
            predicted_at=predicted_at or utc_now(),
        )

    return SelectedPick(
        fixture_id=pick.fixture_id,
        match_label=pick.match_label,
        market=pick.market,
        selection=pick.selection,
        odds=pick.odds,
        opening_odds=pick.opening_odds,
        fair_implied_prob=pick.fair_implied_prob,
        line=pick.line,
        expected_return=pick.expected_return,
        probability=pick.probability,
        expected_value=pick.expected_value,
        confidence=pick.confidence,
        pick_rank_score=pick.pick_rank_score,
        stake_units=pick.stake_units,
        stake_method=pick.stake_method,
        market_regime=pick.market_regime,
        reasoning=pick.reasoning,
        rank=pick.rank,
        fixture_date=pick.fixture_date,
        status=pick.status,
        pick_id=pick.pick_id or (daily_pick.id if daily_pick else None),
        calibrated_confidence=calibrated_confidence,
        calibrated_ev=calibrated_ev,
    )


async def log_prediction_snapshot(
    session: AsyncSession,
    pick: SelectedPick,
    cal_input: CalibratorInput,
    *,
    daily_pick: DailyPick | None = None,
    calibrated_confidence: float | None = None,
    calibrated_ev: float | None = None,
    predicted_at: datetime | None = None,
) -> ConfidencePredictionLog:
    predicted_at = predicted_at or cal_input.predicted_at or utc_now()
    fixture = await session.get(Fixture, pick.fixture_id)
    record = ConfidencePredictionLog(
        daily_pick_id=daily_pick.id if daily_pick else pick.pick_id,
        fixture_id=pick.fixture_id,
        predicted_at=predicted_at,
        dixon_coles_probability=pick.probability,
        market_fair_probability=pick.fair_implied_prob,
        edge=cal_input.edge,
        raw_ev=pick.expected_value,
        odds=pick.odds,
        market=pick.market,
        selection=pick.selection,
        league_id=cal_input.league_id,
        home_ft_count=cal_input.home_ft_count,
        away_ft_count=cal_input.away_ft_count,
        used_default_lambda=cal_input.used_default_lambda,
        home_lambda=cal_input.home_lambda,
        away_lambda=cal_input.away_lambda,
        feature_quality=cal_input.feature_quality,
        hours_to_kickoff=cal_input.hours_to_kickoff,
        old_confidence=pick.confidence,
        calibrated_confidence=calibrated_confidence,
        calibrated_ev=calibrated_ev,
        outcome=daily_pick.outcome if daily_pick else "pending",
        snapshot_json=input_to_dict(cal_input),
    )
    session.add(record)
    return record


async def enrich_persisted_picks(
    session: AsyncSession,
    picks: list[SelectedPick],
    *,
    pick_date: datetime | None = None,
    features_map: dict[int, dict] | None = None,
) -> list[SelectedPick]:
    """Called after persist_picks — updates DailyPick + logs snapshots."""
    if not picks:
        return picks

    enriched: list[SelectedPick] = []
    for pick in picks:
        daily_pick = None
        if pick.pick_id:
            daily_pick = await session.get(DailyPick, pick.pick_id)
        features = features_map.get(pick.fixture_id) if features_map else None
        enriched.append(
            await apply_calibration_to_pick(
                session,
                pick,
                daily_pick=daily_pick,
                predicted_at=pick_date,
                features=features,
            )
        )
    return enriched
