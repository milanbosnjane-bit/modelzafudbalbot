"""Tests for KMeans dynamic regime detection."""

import pytest

from app.predictions.regime import (
    REGIME_FEATURE_KEYS,
    REGIME_THRESHOLDS,
    MarketRegime,
    RegimeDetector,
    extract_regime_features,
    _noise_score,
)


class TestRegimeFeatures:
    def test_extract_regime_features_keys(self):
        vec = extract_regime_features({
            "market_overround_1x2": 0.06,
            "odds_change_pct_home": 0.03,
            "home_weighted_xG_last5": 1.5,
            "away_weighted_xG_last5": 1.1,
        })
        for key in REGIME_FEATURE_KEYS:
            assert key in vec

    def test_stable_fallback_low_noise(self):
        vec = extract_regime_features({
            "market_overround_1x2": 0.04,
            "odds_change_pct_home": 0.01,
            "home_weighted_xG_last5": 1.4,
            "away_weighted_xG_last5": 1.2,
        })
        assert _noise_score(vec) < 0.25


class TestKMeansRegime:
    def _sample_rows(self, n: int, noise_level: float) -> list[dict]:
        rows = []
        for i in range(n):
            rows.append({
                "market_overround": 0.04 + noise_level * 0.08,
                "odds_volatility_7d": 0.1 + noise_level * 0.5,
                "average_line_movement": 0.01 + noise_level * 0.12,
                "liquidity_score": max(0.0, 0.9 - noise_level * 0.4),
                "goals_std_30d": 0.3 + noise_level * 0.5,
                "market_dispersion": 0.05 + noise_level * 0.3,
            })
        return rows

    def test_fit_maps_three_regimes(self):
        rows = (
            self._sample_rows(20, 0.1)
            + self._sample_rows(20, 0.5)
            + self._sample_rows(20, 0.9)
        )
        detector = RegimeDetector()
        detector.fit(rows)
        assert len(detector.cluster_to_regime) == 3
        assert set(detector.cluster_to_regime.values()) == set(MarketRegime)

    def test_thresholds_per_regime(self):
        profile = RegimeDetector().detect(
            {
                "market_overround_1x2": 0.12,
                "odds_change_pct_home": 0.15,
                "home_weighted_xG_last5": 2.0,
                "away_weighted_xG_last5": 1.9,
            },
            league_id=999,
        )
        assert profile.regime in MarketRegime
        assert profile.confidence_threshold == 0.55
        assert profile.ev_threshold == REGIME_THRESHOLDS[profile.regime]["ev"]

    def test_fallback_moderate_when_model_unavailable(self):
        detector = RegimeDetector()
        detector.kmeans = None
        detector.scaler = None
        detector.cluster_to_regime = {}
        profile = detector.detect(
            {
                "market_overround_1x2": 0.12,
                "odds_change_pct_home": 0.15,
                "home_weighted_xG_last5": 2.0,
                "away_weighted_xG_last5": 1.9,
            },
            league_id=999,
        )
        assert profile.regime == MarketRegime.MODERATE
        assert profile.ev_threshold == 0.02
        assert profile.confidence_threshold == 0.55

    def test_detect_returns_profile(self):
        detector = RegimeDetector()
        profile = detector.detect(
            {"market_overround_1x2": 0.04, "odds_change_pct_home": 0.01,
             "home_weighted_xG_last5": 1.4, "away_weighted_xG_last5": 1.2},
            league_id=39,
        )
        assert profile.regime in MarketRegime
        assert profile.ev_threshold == REGIME_THRESHOLDS[profile.regime]["ev"]
        assert profile.confidence_threshold == REGIME_THRESHOLDS[profile.regime]["confidence"]
