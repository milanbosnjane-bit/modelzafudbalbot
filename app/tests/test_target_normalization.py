"""Tests for target normalization variants A/B/C and selection."""

from datetime import datetime, timedelta

import numpy as np
import pytest

from app.training.target_selector import select_best_target
from app.training.targets import (
    TargetTransform,
    apply_target_transform,
    denormalize_target,
    invert_target_transform,
    normalize_target,
    realized_return_from_outcome,
    rolling_return_std,
    score_target_variant,
    stability_score,
)
from app.training.validation import chronological_train_test_split


class TestTargetTransforms:
    def test_log_reduces_high_odds_magnitude(self):
        low = normalize_target(0.50, 1.50, 0.15, TargetTransform.LOG)
        high = normalize_target(4.50, 5.50, 0.15, TargetTransform.LOG)
        assert high < 4.50
        assert abs(high - low) < abs(4.50 - 0.50)

    def test_odds_norm_scales_by_implied_profit(self):
        ret = realized_return_from_outcome("win", 5.50, 4.50, 1.0)
        norm = normalize_target(ret, 5.50, 0.15, TargetTransform.ODDS_NORM)
        assert norm == pytest.approx(ret / 4.50)

    def test_risk_adj_uses_rolling_std(self):
        ret = -1.0
        norm = normalize_target(ret, 2.0, 0.20, TargetTransform.RISK_ADJ)
        assert norm == pytest.approx(-5.0)

    def test_invert_roundtrip_all_methods(self):
        odds = 2.10
        rolling = 0.18
        for method in TargetTransform:
            raw = realized_return_from_outcome("win", odds, 1.10, 1.0)
            norm = normalize_target(raw, odds, rolling, method)
            restored = denormalize_target(norm, odds, rolling, method)
            assert restored == pytest.approx(raw, abs=0.01)

    def test_apply_and_invert_vectorized(self):
        raw = np.array([0.5, -1.0, 0.0])
        odds = np.array([1.5, 2.0, 3.0])
        norm = apply_target_transform(raw, odds, TargetTransform.ODDS_NORM)
        restored = invert_target_transform(norm, odds, rolling_return_std(raw), TargetTransform.ODDS_NORM)
        np.testing.assert_allclose(restored, raw, atol=0.01)


class TestTargetScoring:
    def test_score_target_variant_keys(self):
        y_true = np.array([0.5, -1.0, 0.3, -1.0])
        y_pred = np.array([0.4, -0.8, 0.2, -0.9])
        metrics = score_target_variant(y_true, y_pred)
        assert "mae" in metrics
        assert "rmse" in metrics
        assert "oos_roi_pct" in metrics
        assert "stability" in metrics
        assert "composite_score" in metrics

    def test_stability_prefers_consistent_errors(self):
        y_true = np.array([0.5, 0.4, 0.3, 0.2, 0.1] * 4)
        stable_pred = y_true + 0.05
        volatile_pred = y_true.copy()
        volatile_pred[5:10] += 0.5
        assert stability_score(y_true, stable_pred) > stability_score(y_true, volatile_pred)


class TestTargetSelection:
    def test_select_best_target_chronological(self):
        n = 60
        timestamps = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(n)]
        X = np.column_stack([
            np.linspace(1.0, 2.0, n),
            np.linspace(0.5, 1.5, n),
        ])
        odds = np.full(n, 2.0)
        raw_returns = np.array([
            realized_return_from_outcome("win" if i % 3 == 0 else "lose", 2.0, 1.0, 1.0)
            for i in range(n)
        ])
        selected, payload = select_best_target(X, raw_returns, odds, timestamps)
        assert selected in TargetTransform
        assert payload["selected"] == selected.value
        assert len(payload["comparison"]) == 4
        split = chronological_train_test_split(X, raw_returns, timestamps, test_ratio=0.2)
        assert len(split.y_train) + len(split.y_test) <= n
