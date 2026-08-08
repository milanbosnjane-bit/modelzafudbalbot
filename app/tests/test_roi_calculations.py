"""Tests for core ROI calculations and odds hygiene."""

import pytest

from app.utils.helpers import (
    closing_line_value,
    expected_value,
    fractional_kelly_stake,
    implied_probability,
    kelly_stake,
    model_agreement,
    pick_rank_score,
    roi_score,
)
from app.utils.odds import proportional_devig, shrink_probability


class TestEVCalculations:
    def test_implied_probability(self):
        assert implied_probability(2.0) == pytest.approx(0.5)
        assert implied_probability(4.0) == pytest.approx(0.25)

    def test_expected_value_positive(self):
        ev = expected_value(0.64, 2.05)
        assert ev == pytest.approx(0.312, abs=0.01)

    def test_expected_value_negative(self):
        ev = expected_value(0.45, 2.0)
        assert ev < 0

    def test_clv_raw_positive(self):
        clv = closing_line_value(2.10, 1.95)
        assert clv > 0

    def test_clv_fair_edge_separate_from_raw(self):
        from app.utils.clv_metrics import closing_fair_edge

        raw = closing_line_value(2.10, 1.95)
        fair_edge = closing_fair_edge(2.10, 0.48)
        assert raw > 0
        assert fair_edge != raw


class TestDevigging:
    def test_proportional_devig_sums_to_one(self):
        fair = proportional_devig([2.0, 3.5, 4.0])
        assert sum(fair) == pytest.approx(1.0, abs=0.001)

    def test_devig_removes_overround(self):
        raw_sum = sum(implied_probability(o) for o in [1.9, 3.4, 4.2])
        fair = proportional_devig([1.9, 3.4, 4.2])
        assert sum(fair) < raw_sum
        assert sum(fair) == pytest.approx(1.0)


class TestStaking:
    def test_kelly_positive_edge(self):
        stake = kelly_stake(0.55, 2.0)
        assert stake > 0

    def test_kelly_no_edge(self):
        stake = kelly_stake(0.40, 2.0)
        assert stake == 0

    def test_fractional_kelly(self):
        full = kelly_stake(0.55, 2.0)
        fractional = fractional_kelly_stake(0.55, 2.0, 0.25, bankroll=1.0, max_pct=1.0)
        assert fractional == pytest.approx(full * 0.25)

    def test_fractional_kelly_capped(self):
        capped = fractional_kelly_stake(0.55, 2.0, 0.25, bankroll=100.0)
        assert capped <= 2.0

    def test_shrink_probability(self):
        shrunk = shrink_probability(0.70, 0.50, weight=0.35)
        assert 0.50 < shrunk < 0.70


class TestEnsemble:
    def test_high_agreement(self):
        agreement = model_agreement([0.58, 0.61, 0.59])
        assert agreement > 0.7

    def test_low_agreement(self):
        agreement = model_agreement([0.55, 0.41, 0.67])
        assert agreement < 0.5

    def test_pick_rank_score_not_roi(self):
        high_ev = pick_rank_score(0.20, 0.75, 0.80)
        low_ev = pick_rank_score(0.05, 0.90, 0.90)
        assert high_ev > low_ev
        assert roi_score(0.20, 0.75, 0.80) == high_ev  # alias


class TestPoissonModel:
    def test_btts_probability(self):
        from app.models.poisson_model import PoissonModel

        model = PoissonModel()
        pred = model.simulate(1.5, 1.3)
        assert 0 < pred.probabilities["btts_yes"] < 1
        assert pred.probabilities["btts_yes"] + pred.probabilities["btts_no"] == pytest.approx(1.0, abs=0.01)

    def test_over_under_respects_line(self):
        from app.models.poisson_model import PoissonModel

        model = PoissonModel()
        p25 = model.predict(1.5, 1.3, "over_under", "over", line=2.5)
        p35 = model.predict(1.5, 1.3, "over_under", "over", line=3.5)
        assert p25 > p35


class TestTemporalSplit:
    def test_chronological_split_no_overlap(self):
        from datetime import datetime, timedelta
        from app.training.validation import chronological_train_test_split
        import numpy as np

        n = 20
        X = np.arange(n).reshape(-1, 1).astype(float)
        y = (X[:, 0] % 2).astype(int)
        ts = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(n)]
        split = chronological_train_test_split(X, y, ts, test_ratio=0.2, embargo_days=1)
        assert len(split.y_train) + len(split.y_test) <= n
        assert len(split.y_test) >= 1
