"""Tests for pick selector validation and pipeline corruption guard."""

import pytest

from app.predictions.ensemble import EnsembleResult, ModelOutputs
from app.predictions.pipeline import PipelineDataCorruptionError, PredictionPipeline
from app.predictions.pick_selector import PickCandidate, candidate_passes_validation
from app.predictions.probability_layer import ev_variance


def _result(**kwargs) -> EnsembleResult:
    defaults = dict(
        expected_return=0.1,
        probability=0.55,
        calibrated_probability=0.55,
        confidence=0.72,
        agreement=0.8,
        model_outputs=ModelOutputs(0.54, 0.56, 0.55, None),
        expected_value=0.1,
        fair_implied_prob=0.48,
        bookmaker_odds=2.0,
        pick_rank_score=0.5,
        rejected=False,
        rejection_reason=None,
    )
    defaults.update(kwargs)
    return EnsembleResult(**defaults)


class TestCandidateValidation:
    def test_rejects_invalid_market(self):
        ok, reason = candidate_passes_validation(_result(), "exact_score", 2.0)
        assert ok is False
        assert reason == "invalid_market"

    def test_rejects_legacy_clamped_ev(self):
        ok, reason = candidate_passes_validation(
            _result(expected_value=0.5), "match_winner", 2.0
        )
        assert ok is False
        assert reason == "fallback_ev_used"

    def test_accepts_ev_below_regime_threshold_rejection(self):
        ok, reason = candidate_passes_validation(
            _result(
                rejected=True,
                rejection_reason="EV 3.0% below threshold 12.0%",
            ),
            "match_winner",
            2.0,
        )
        assert ok is True
        assert reason is None


class TestPipelineCorruptionGuard:
    def test_fails_on_constant_ev(self):
        candidates = [
            PickCandidate(
                fixture_id=1,
                home_team="A",
                away_team="B",
                fixture_date=__import__("datetime").datetime.utcnow(),
                market="match_winner",
                selection="home",
                odds=2.0,
                opening_odds=2.0,
                fair_implied_prob=0.48,
                line=None,
                market_regime="moderate",
                ensemble=_result(expected_value=0.5),
            ),
            PickCandidate(
                fixture_id=2,
                home_team="C",
                away_team="D",
                fixture_date=__import__("datetime").datetime.utcnow(),
                market="match_winner",
                selection="away",
                odds=2.5,
                opening_odds=2.5,
                fair_implied_prob=0.40,
                line=None,
                market_regime="moderate",
                ensemble=_result(expected_value=0.5, calibrated_probability=0.60),
            ),
        ]
        with pytest.raises(PipelineDataCorruptionError):
            PredictionPipeline.validate_candidate_ev_distribution(candidates)

    def test_passes_with_ev_spread(self):
        candidates = [
            PickCandidate(
                fixture_id=1,
                home_team="A",
                away_team="B",
                fixture_date=__import__("datetime").datetime.utcnow(),
                market="match_winner",
                selection="home",
                odds=2.0,
                opening_odds=2.0,
                fair_implied_prob=0.48,
                line=None,
                market_regime="moderate",
                ensemble=_result(expected_value=0.02, calibrated_probability=0.51),
            ),
            PickCandidate(
                fixture_id=2,
                home_team="C",
                away_team="D",
                fixture_date=__import__("datetime").datetime.utcnow(),
                market="match_winner",
                selection="away",
                odds=2.5,
                opening_odds=2.5,
                fair_implied_prob=0.40,
                line=None,
                market_regime="moderate",
                ensemble=_result(expected_value=0.25, calibrated_probability=0.50),
            ),
        ]
        PredictionPipeline.validate_candidate_ev_distribution(candidates)
        assert ev_variance([c.ensemble.expected_value for c in candidates]) >= 0.01

    def test_allows_low_variance_with_distinct_evs(self):
        candidates = [
            PickCandidate(
                fixture_id=i,
                home_team="A",
                away_team="B",
                fixture_date=__import__("datetime").datetime.utcnow(),
                market="match_winner",
                selection="home",
                odds=2.0,
                opening_odds=2.0,
                fair_implied_prob=0.48,
                line=None,
                market_regime="moderate",
                ensemble=_result(expected_value=0.02 + i * 0.001),
            )
            for i in range(6)
        ]
        PredictionPipeline.validate_candidate_ev_distribution(candidates)
