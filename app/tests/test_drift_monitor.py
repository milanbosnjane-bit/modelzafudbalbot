"""Tests for PSI feature drift monitoring."""

import numpy as np
import pytest

from app.services.drift_monitor import (
    DriftMonitor,
    compute_psi,
    jensen_shannon_divergence,
    psi_status,
)


class TestPSI:
    def test_identical_distributions_low_psi(self):
        baseline = np.array([1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9] * 5)
        current = baseline.copy()
        psi = compute_psi(baseline, current)
        assert psi == pytest.approx(0.0, abs=0.01)

    def test_shifted_distribution_raises_psi(self):
        baseline = np.linspace(0.0, 1.0, 100)
        current = baseline + 0.5
        psi = compute_psi(baseline, current)
        assert psi > 0.1

    def test_psi_status_thresholds(self):
        assert psi_status(0.05) == "stable"
        assert psi_status(0.15) == "warning"
        assert psi_status(0.30) == "drift_detected"


class TestJSD:
    def test_identical_distributions_zero_js(self):
        p = np.array([0.25, 0.25, 0.25, 0.25])
        assert jensen_shannon_divergence(p, p) == pytest.approx(0.0, abs=0.01)


class TestDriftMonitor:
    def test_run_psi_check_no_baseline(self):
        monitor = DriftMonitor()
        monitor.baseline = {}
        result = monitor.run_psi_check([{"x": 1.0}])
        assert result["status"] == "no_baseline"
        assert result["retrain_required"] is False

    def test_run_psi_check_drift_detected(self):
        monitor = DriftMonitor()
        monitor.baseline = {"feature_a": list(np.linspace(0, 1, 50))}
        current = [{"feature_a": float(v + 2.0)} for v in np.linspace(0, 1, 20)]
        result = monitor.run_psi_check(current)
        assert result["max_psi"] > 0.25
        assert result["retrain_required"] is True
        assert result["status"] == "drift_detected"

    def test_summarize_snapshot(self):
        monitor = DriftMonitor()
        monitor.baseline = {}
        summary = monitor._summarize_snapshot([
            {"home_xg": 1.5, "away_xg": 1.2},
            {"home_xg": 1.6, "away_xg": 1.1},
        ])
        assert summary["fixture_count"] == 2
        assert "home_xg" in summary["features"]
        assert summary["features"]["home_xg"]["mean"] == pytest.approx(1.55)
