"""Tests for return targets, Kelly cap, edge capture, regime."""

import pytest

from app.utils.edge import compute_edge_metrics
from app.training.targets import realized_return_from_outcome
from app.predictions.regime import RegimeDetector, MarketRegime
from app.utils.helpers import capped_stake, fractional_kelly_stake


class TestRealizedReturn:
    def test_win_at_140(self):
        ret = realized_return_from_outcome("win", 1.40, 0.40, 1.0)
        assert ret == pytest.approx(0.40)

    def test_win_at_450(self):
        ret = realized_return_from_outcome("win", 4.50, 3.50, 1.0)
        assert ret == pytest.approx(3.50)

    def test_different_returns_not_equal(self):
        a = realized_return_from_outcome("win", 1.40, 0.40, 1.0)
        b = realized_return_from_outcome("win", 4.50, 3.50, 1.0)
        assert a != b

    def test_lose(self):
        assert realized_return_from_outcome("lose", 2.0, -1.0, 1.0) == -1.0


class TestKellyCap:
    def test_hard_cap_2pct(self):
        bankroll = 100.0
        # Strong edge would suggest large Kelly
        stake = capped_stake(0.65, 2.5, "fractional_kelly", bankroll)
        assert stake <= bankroll * 0.02 + 0.001

    def test_cap_below_raw_kelly(self):
        bankroll = 100.0
        raw = fractional_kelly_stake(0.65, 2.5, 0.25, bankroll, max_pct=1.0)
        capped = capped_stake(0.65, 2.5, "fractional_kelly", bankroll)
        assert capped <= raw or capped <= 2.0


class TestEdgeCapture:
    def test_full_capture(self):
        m = compute_edge_metrics(0.60, 0.50, 0.70)
        assert m.model_edge == pytest.approx(0.10)
        assert m.closing_edge == pytest.approx(0.20)
        assert m.edge_capture == pytest.approx(0.50)

    def test_no_closing(self):
        m = compute_edge_metrics(0.60, 0.50, None)
        assert m.edge_capture is None

    def test_unhealthy_capture(self):
        m = compute_edge_metrics(0.52, 0.50, 0.60)
        assert m.edge_capture is not None
        assert m.edge_capture < 0.5


class TestRegime:
    def test_fallback_moderate_without_model(self):
        det = RegimeDetector()
        det.kmeans = None
        det.scaler = None
        det.cluster_to_regime = {}
        profile = det.detect(
            {"market_overround_1x2": 0.04, "odds_change_pct_home": 0.01,
             "home_weighted_xG_last5": 1.4, "away_weighted_xG_last5": 1.2},
            league_id=39,
        )
        assert profile.regime == MarketRegime.MODERATE
        assert profile.ev_threshold == 0.02
        assert profile.confidence_threshold == 0.55

    def test_confidence_threshold_relaxed(self):
        det = RegimeDetector()
        profile = det.detect(
            {"market_overround_1x2": 0.12, "odds_change_pct_home": 0.15,
             "home_weighted_xG_last5": 2.0, "away_weighted_xG_last5": 1.9},
            league_id=999,
        )
        assert profile.confidence_threshold == 0.55
