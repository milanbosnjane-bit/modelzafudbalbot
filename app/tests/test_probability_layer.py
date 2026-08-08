"""Tests for probability correctness layer."""

import pytest

from app.predictions.probability_layer import (
    compute_ev,
    confidence_from_uncertainty,
    ev_variance,
    is_disabled_market,
    is_legacy_clamped_ev,
    is_valid_probability,
    probability_from_return,
)


class TestProbabilityLayer:
    def test_no_fallback_invalid_odds(self):
        assert probability_from_return(0.5, 1.0) is None

    def test_valid_probability_range(self):
        assert is_valid_probability(0.10) is True
        assert is_valid_probability(0.04) is False
        assert is_valid_probability(0.96) is False

    def test_ev_not_clamped(self):
        ev = compute_ev(0.90, 10.0)
        assert ev is not None
        assert ev == pytest.approx(8.0)
        assert not is_legacy_clamped_ev(ev)

    def test_ev_invalid_prob(self):
        assert compute_ev(0.02, 2.0) is None

    def test_confidence_none_with_one_model(self):
        assert confidence_from_uncertainty([0.55]) is None

    def test_confidence_higher_with_agreement(self):
        low = confidence_from_uncertainty([0.40, 0.70])
        high = confidence_from_uncertainty([0.55, 0.56])
        assert low is not None and high is not None
        assert high > low

    def test_exact_score_disabled(self):
        assert is_disabled_market("correct_score") is True
        assert is_disabled_market("exact_score") is True
        assert is_disabled_market("ht_ft") is True
        assert is_disabled_market("match_winner") is False

    def test_supported_market_filter(self):
        from app.predictions.probability_layer import is_supported_market

        supported = {"match_winner", "over_under", "btts"}
        assert is_supported_market("match_winner", supported) is True
        assert is_supported_market("Offsides Total", supported) is False
        assert is_supported_market("correct_score", supported) is False

    def test_legacy_clamped_ev_detection(self):
        assert is_legacy_clamped_ev(0.5) is True
        assert is_legacy_clamped_ev(-0.5) is True
        assert is_legacy_clamped_ev(0.12) is False

    def test_ev_variance(self):
        assert ev_variance([0.1, 0.2, 0.15]) > 0.001
        assert ev_variance([0.5, 0.5, 0.5]) == pytest.approx(0.0)
