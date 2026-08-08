"""Tests for isolated confidence calibrator layer."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from app.model.confidence_calibrator import (
    CalibratorInput,
    ConfidenceCalibrator,
    brier_score,
    compute_metrics,
    detect_default_lambda,
    parse_lambdas_from_reasoning,
    vectorize_input,
)


def _sample(**kwargs) -> CalibratorInput:
    base = dict(
        dixon_coles_probability=0.44,
        market_fair_probability=0.24,
        edge=0.20,
        raw_ev=0.84,
        odds=5.0,
        market="match_winner",
        selection="home",
        league_id=103,
        home_ft_count=12,
        away_ft_count=8,
        used_default_lambda=True,
        home_lambda=1.0,
        away_lambda=1.0,
        feature_quality=0.6,
        hours_to_kickoff=6.0,
        old_confidence=0.95,
        predicted_at=datetime(2026, 8, 1, 8, 0),
    )
    base.update(kwargs)
    return CalibratorInput(**base)


class TestCalibratorHelpers:
    def test_parse_lambdas_from_reasoning(self):
        hl, al = parse_lambdas_from_reasoning(
            ["Dixon-Coles λ: domaćin 1.00 — gost 1.00", "Edge +10pp"]
        )
        assert hl == pytest.approx(1.0)
        assert al == pytest.approx(1.0)

    def test_detect_default_lambda(self):
        assert detect_default_lambda(1.0, 1.0) is True
        assert detect_default_lambda(1.5, 1.2) is False

    def test_vectorize_shape(self):
        vec = vectorize_input(_sample())
        assert vec.shape == (16,)

    def test_calibrated_ev(self):
        ev = _sample().calibrated_ev(0.27)
        assert ev == pytest.approx(0.27 * 5.0 - 1.0)


class TestCalibratorTraining:
    def test_insufficient_data_report(self):
        cal = ConfidenceCalibrator()
        samples = [_sample(predicted_at=datetime(2026, 1, 1) + timedelta(days=i)) for i in range(10)]
        outcomes = [i % 2 for i in range(10)]
        report = cal.fit(samples, outcomes)
        assert report.sufficient_data is False
        assert not cal.is_ready

    def test_chronological_split_and_metrics(self):
        cal = ConfidenceCalibrator()
        samples = []
        outcomes = []
        for i in range(60):
            samples.append(
                _sample(
                    predicted_at=datetime(2026, 1, 1) + timedelta(days=i),
                    raw_ev=0.4 if i % 3 == 0 else 0.1,
                    old_confidence=0.9 if i % 3 == 0 else 0.6,
                    dixon_coles_probability=0.35 + (i % 5) * 0.05,
                )
            )
            outcomes.append(1 if i % 4 == 0 else 0)

        report = cal.fit(samples, outcomes)
        assert report.sufficient_data is True
        assert cal.is_ready
        assert report.old_metrics is not None
        assert report.new_metrics is not None
        assert report.val_samples >= 10

    def test_predict_proba_bounded(self):
        cal = ConfidenceCalibrator()
        samples = [_sample(predicted_at=datetime(2026, 1, 1) + timedelta(days=i)) for i in range(55)]
        outcomes = [i % 3 for i in range(55)]
        cal.fit(samples, outcomes)
        p = cal.predict_proba(_sample(raw_ev=0.88, old_confidence=0.95))
        assert p is not None
        assert 0.01 <= p <= 0.99


class TestCalibrationMetrics:
    def test_brier_and_buckets(self):
        probs = np.array([0.1, 0.2, 0.8, 0.9])
        outcomes = np.array([0, 0, 1, 1])
        m = compute_metrics(probs, outcomes, raw_evs=np.array([0.1, 0.4, 0.2, 0.5]))
        assert m.n_samples == 4
        assert brier_score(probs, outcomes) < 0.2
        assert any(row["n"] > 0 for row in m.bucket_table)
