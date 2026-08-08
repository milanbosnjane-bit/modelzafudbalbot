"""Tests for automatic retrain trigger logic."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.retrain_manager import RetrainManager


class TestRetrainTriggers:
    def test_evaluate_triggers_psi_drift(self):
        manager = RetrainManager()
        with patch.object(manager, "_clv_and_edge_metrics", return_value={
            "avg_clv": 0.05, "avg_edge_capture": 0.7, "sample_size": 10
        }), patch.object(manager, "_roi_deterioration", return_value={
            "deteriorated": False, "drop_pct": 0.0, "recent_roi_pct": 5.0, "prior_roi_pct": 5.0
        }), patch("app.services.retrain_manager.DriftMonitor") as mock_drift:
            mock_drift.return_value.get_latest_status.return_value = {
                "retrain_required": True, "max_psi": 0.30, "status": "drift_detected"
            }
            result = manager.evaluate_triggers()
        assert result["retrain_required"] is True
        assert any("PSI" in r for r in result["reasons"])

    def test_evaluate_triggers_negative_clv(self):
        manager = RetrainManager()
        with patch.object(manager, "_clv_and_edge_metrics", return_value={
            "avg_clv": -0.02, "avg_edge_capture": 0.7, "sample_size": 10
        }), patch.object(manager, "_roi_deterioration", return_value={
            "deteriorated": False, "drop_pct": 0.0, "recent_roi_pct": 5.0, "prior_roi_pct": 5.0
        }), patch("app.services.retrain_manager.DriftMonitor") as mock_drift:
            mock_drift.return_value.get_latest_status.return_value = {
                "retrain_required": False, "max_psi": 0.05
            }
            result = manager.evaluate_triggers()
        assert result["retrain_required"] is True
        assert any("CLV" in r for r in result["reasons"])

    def test_evaluate_triggers_low_edge_capture(self):
        manager = RetrainManager()
        with patch.object(manager, "_clv_and_edge_metrics", return_value={
            "avg_clv": 0.05, "avg_edge_capture": 0.3, "sample_size": 10
        }), patch.object(manager, "_roi_deterioration", return_value={
            "deteriorated": False, "drop_pct": 0.0, "recent_roi_pct": 5.0, "prior_roi_pct": 5.0
        }), patch("app.services.retrain_manager.DriftMonitor") as mock_drift:
            mock_drift.return_value.get_latest_status.return_value = {
                "retrain_required": False, "max_psi": 0.05
            }
            result = manager.evaluate_triggers()
        assert result["retrain_required"] is True
        assert any("edge_capture" in r for r in result["reasons"])

    def test_evaluate_triggers_roi_deterioration(self):
        manager = RetrainManager()
        with patch.object(manager, "_clv_and_edge_metrics", return_value={
            "avg_clv": 0.05, "avg_edge_capture": 0.7, "sample_size": 10
        }), patch.object(manager, "_roi_deterioration", return_value={
            "deteriorated": True, "drop_pct": 45.0, "recent_roi_pct": 2.0, "prior_roi_pct": 10.0
        }), patch("app.services.retrain_manager.DriftMonitor") as mock_drift:
            mock_drift.return_value.get_latest_status.return_value = {
                "retrain_required": False, "max_psi": 0.05
            }
            result = manager.evaluate_triggers()
        assert result["retrain_required"] is True
        assert any("ROI" in r for r in result["reasons"])

    def test_no_triggers_when_healthy(self):
        manager = RetrainManager()
        with patch.object(manager, "_clv_and_edge_metrics", return_value={
            "avg_clv": 0.05, "avg_edge_capture": 0.7, "sample_size": 10
        }), patch.object(manager, "_roi_deterioration", return_value={
            "deteriorated": False, "drop_pct": 5.0, "recent_roi_pct": 8.0, "prior_roi_pct": 8.5
        }), patch("app.services.retrain_manager.DriftMonitor") as mock_drift:
            mock_drift.return_value.get_latest_status.return_value = {
                "retrain_required": False, "max_psi": 0.05
            }
            result = manager.evaluate_triggers()
        assert result["retrain_required"] is False
        assert result["reasons"] == []
