"""Tests for numeric feature helpers — 0.0 is valid, only None is missing."""

import pytest

from app.utils.feature_values import first_present, has_usable_match_xg, numeric_feature


class TestFirstPresent:
    def test_zero_is_valid(self):
        features = {"home_weighted_xG_last5": 0.0, "home_venue_adjusted_xg": 1.2}
        assert first_present(features, "home_weighted_xG_last5") == 0.0

    def test_skips_none_falls_through_to_next_key(self):
        features = {
            "home_venue_adjusted_xg": None,
            "home_weighted_xG_last5": 0.0,
        }
        assert first_present(features, "home_venue_adjusted_xg", "home_weighted_xG_last5") == 0.0

    def test_all_missing_returns_none(self):
        features = {"home_venue_adjusted_xg": None}
        assert first_present(features, "home_venue_adjusted_xg", "home_weighted_xG_last5") is None


class TestNumericFeature:
    def test_zero_is_valid(self):
        assert numeric_feature({"score": 0.0}, "score") == 0.0

    def test_missing_returns_default(self):
        assert numeric_feature({}, "score", default=0.0) == 0.0

    def test_none_returns_default(self):
        assert numeric_feature({"score": None}, "score", default=0.0) == 0.0

    def test_has_usable_match_xg(self):
        assert has_usable_match_xg(
            {"home_weighted_xG_last5": 1.1, "away_weighted_xG_last5": 0.9},
            0.15,
        )
        assert not has_usable_match_xg(
            {"home_weighted_xG_last5": 0.0, "away_weighted_xG_last5": 0.0},
            0.15,
        )
        assert not has_usable_match_xg({}, 0.15)


class TestEnsembleWarmup:
    def test_warmup_loads_dixon_coles_only(self):
        from app.predictions.ensemble import EnsemblePredictor

        engine = EnsemblePredictor()
        loaded = engine.warmup()
        assert loaded == ["dixon_coles"]
        assert engine.loaded_models == ["dixon_coles"]

    def test_confidence_edge_based(self):
        from app.predictions.ensemble import ProbabilityEngine

        engine = ProbabilityEngine()
        conf = engine._confidence(model_prob=0.35, fair_implied=0.25, odds=4.0)
        assert 0.35 <= conf <= 0.95

        low_edge = engine._confidence(model_prob=0.26, fair_implied=0.25, odds=4.0)
        assert low_edge < conf


class TestPreflightXg:
    def test_zero_xg_fails_preflight(self):
        from app.predictions.pick_selector import PickSelectionEngine

        engine = object.__new__(PickSelectionEngine)
        features = {
            "home_weighted_xG_last5": 0.0,
            "away_weighted_xG_last5": 0.0,
        }
        odds_info = {"fair_prob": 0.48}
        assert engine._preflight_ok("match_winner", features, odds_info) is False

    def test_usable_xg_passes_preflight(self):
        from app.predictions.pick_selector import PickSelectionEngine

        engine = object.__new__(PickSelectionEngine)
        features = {
            "home_weighted_xG_last5": 1.2,
            "away_weighted_xG_last5": 0.9,
        }
        odds_info = {"fair_prob": 0.48}
        assert engine._preflight_ok("match_winner", features, odds_info) is True
