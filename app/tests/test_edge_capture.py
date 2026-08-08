"""Tests for stabilized edge capture metrics."""

import pytest

from app.utils.edge import EDGE_CAPTURE_CLIP, EDGE_CAPTURE_EPSILON, compute_edge_metrics


class TestEdgeCaptureStabilization:
    def test_full_capture_unchanged_when_closing_edge_large(self):
        m = compute_edge_metrics(0.60, 0.50, 0.70)
        assert m.model_edge == pytest.approx(0.10)
        assert m.closing_edge == pytest.approx(0.20)
        assert m.raw_edge_capture == pytest.approx(0.50)
        assert m.adjusted_edge_capture == pytest.approx(0.50)
        assert m.edge_capture == m.adjusted_edge_capture

    def test_epsilon_when_closing_edge_near_zero(self):
        m = compute_edge_metrics(0.55, 0.50, 0.501)
        assert abs(m.closing_edge) < EDGE_CAPTURE_EPSILON * 2
        assert m.adjusted_edge_capture is not None
        assert abs(m.adjusted_edge_capture) <= EDGE_CAPTURE_CLIP

    def test_clip_positive_extreme(self):
        m = compute_edge_metrics(0.80, 0.50, 0.505, epsilon=0.01, clip=3.0)
        assert m.adjusted_edge_capture == pytest.approx(3.0)

    def test_clip_negative_extreme(self):
        m = compute_edge_metrics(0.40, 0.50, 0.505, epsilon=0.01, clip=3.0)
        assert m.adjusted_edge_capture == pytest.approx(-3.0)

    def test_stores_raw_and_adjusted_separately(self):
        m = compute_edge_metrics(0.52, 0.50, 0.60)
        assert m.raw_edge_capture is not None
        assert m.adjusted_edge_capture is not None
        assert m.raw_edge_capture == pytest.approx(0.20)
        assert m.adjusted_edge_capture == pytest.approx(0.20)

    def test_no_closing_returns_none(self):
        m = compute_edge_metrics(0.60, 0.50, None)
        assert m.edge_capture is None
        assert m.raw_edge_capture is None
        assert m.adjusted_edge_capture is None
