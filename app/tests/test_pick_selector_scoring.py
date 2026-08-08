"""Tests for pick selector soft scoring and dynamic EV selection."""

import pytest

from app.predictions.pick_selector import (
    apply_diversity_rules,
    apply_ev_selection,
    apply_market_diversity_cap,
    compute_dynamic_ev_threshold,
    compute_final_score,
    confidence_weight,
    ev_distribution_stats,
    is_draw_pick,
    regime_weight,
    select_candidates,
    top_k_candidates,
)
from app.predictions.regime import MarketRegime
from app.tests.test_pick_selector_validation import _result
from app.predictions.pick_selector import PickCandidate
from datetime import datetime


class TestSoftScoring:
    def test_confidence_weight_range(self):
        assert confidence_weight(0.0) == pytest.approx(0.8)
        assert confidence_weight(1.0) == pytest.approx(1.2)

    def test_regime_weight_stable_highest(self):
        assert regime_weight(MarketRegime.STABLE) > regime_weight(MarketRegime.MODERATE)
        assert regime_weight(MarketRegime.MODERATE) > regime_weight(MarketRegime.HIGH_NOISE)

    def test_final_score_uses_soft_multipliers(self):
        ev = 0.10
        conf = 0.72
        score = compute_final_score(ev, conf, MarketRegime.STABLE)
        expected = ev * confidence_weight(conf) * regime_weight(MarketRegime.STABLE)
        assert score == pytest.approx(expected)


class TestDynamicEvSelection:
    def test_dynamic_threshold_is_max_of_floor_and_median(self):
        assert compute_dynamic_ev_threshold([0.01, 0.03, 0.05]) == pytest.approx(0.03)
        assert compute_dynamic_ev_threshold([-0.05, -0.02, -0.01]) == pytest.approx(0.0)

    def test_ev_distribution_stats(self):
        median, p25, p75 = ev_distribution_stats([0.0, 0.04, 0.08, 0.12])
        assert median == pytest.approx(0.06)
        assert p25 <= median <= p75

    def _candidate(self, fixture_id: int, ev: float) -> PickCandidate:
        return PickCandidate(
            fixture_id=fixture_id,
            home_team="A",
            away_team="B",
            fixture_date=datetime.utcnow(),
            market="match_winner",
            selection="home",
            odds=2.0,
            opening_odds=2.0,
            fair_implied_prob=0.48,
            line=None,
            market_regime="moderate",
            ensemble=_result(expected_value=ev),
        )

    def test_fallback_when_none_pass_dynamic_threshold(self):
        candidates = [
            self._candidate(1, -0.05),
            self._candidate(2, -0.02),
            self._candidate(3, -0.08),
        ]
        selected, meta = apply_ev_selection(candidates, max_picks=2)
        assert meta["used_fallback"] is True
        assert len(selected) == 2
        assert selected[0].ensemble.expected_value == pytest.approx(-0.02)
        assert selected[1].ensemble.expected_value == pytest.approx(-0.05)

    def test_threshold_pool_keeps_all_passers_for_downstream_scoring(self):
        candidates = [
            self._candidate(1, 0.09),
            self._candidate(2, 0.09),
            self._candidate(3, 0.09),
        ]
        selected, meta = apply_ev_selection(candidates, max_picks=2)
        assert meta["used_fallback"] is False
        assert len(selected) == 3


class TestMarketDiversityCap:
    def _candidate(self, fixture_id: int, market: str, ev: float) -> PickCandidate:
        return PickCandidate(
            fixture_id=fixture_id,
            home_team="A",
            away_team="B",
            fixture_date=datetime.utcnow(),
            market=market,
            selection="home",
            odds=2.0,
            opening_odds=2.0,
            fair_implied_prob=0.48,
            line=None,
            market_regime="moderate",
            ensemble=_result(expected_value=ev),
        )

    def test_caps_at_two_per_market(self):
        candidates = [
            self._candidate(1, "match_winner", 0.20),
            self._candidate(2, "match_winner", 0.18),
            self._candidate(3, "match_winner", 0.16),
            self._candidate(4, "over_under", 0.14),
            self._candidate(5, "btts", 0.12),
        ]
        picked = apply_market_diversity_cap(candidates, max_picks=6)
        assert len(picked) == 4
        assert sum(1 for p in picked if p.market == "match_winner") == 2
        assert picked[0].fixture_id == 1
        assert picked[1].fixture_id == 2

    def test_caps_at_one_per_fixture(self):
        candidates = [
            self._candidate(1, "match_winner", 0.20),
            self._candidate(1, "over_under", 0.18),
            self._candidate(2, "match_winner", 0.16),
        ]
        picked = apply_market_diversity_cap(candidates, max_picks=6)
        assert len(picked) == 2
        assert sum(1 for p in picked if p.fixture_id == 1) == 1
        assert picked[0].fixture_id == 1
        assert picked[1].fixture_id == 2

    def test_respects_max_picks(self):
        candidates = [
            self._candidate(i, "match_winner" if i % 2 else "over_under", 0.20 - i * 0.01)
            for i in range(10)
        ]
        picked = apply_market_diversity_cap(candidates, max_picks=3)
        assert len(picked) == 3


class TestSelectCandidatesLadder:
    def _candidate(self, fixture_id: int, market: str, ev: float) -> PickCandidate:
        c = PickCandidate(
            fixture_id=fixture_id,
            home_team="A",
            away_team="B",
            fixture_date=datetime.utcnow(),
            market=market,
            selection="home",
            odds=2.0,
            opening_odds=2.0,
            fair_implied_prob=0.48,
            line=None,
            market_regime="moderate",
            ensemble=_result(expected_value=ev),
        )
        c.final_score = ev
        return c

    def test_step1_normal_ev_when_enough_pass(self):
        pool = [
            self._candidate(1, "match_winner", 0.10),
            self._candidate(2, "match_winner", 0.09),
            self._candidate(3, "over_under", 0.08),
            self._candidate(4, "over_under", 0.07),
            self._candidate(5, "btts", 0.06),
            self._candidate(6, "btts", 0.05),
        ]
        picked, meta = select_candidates(pool, max_picks=6)
        assert meta["step"] == "normal_ev"
        assert meta["used_fallback"] is False
        assert len(picked) == 6

    def test_step3_no_picks_when_ev_below_floor(self):
        pool = [
            self._candidate(1, "match_winner", 0.01),
            self._candidate(2, "over_under", 0.005),
            self._candidate(3, "btts", 0.001),
            self._candidate(4, "match_winner", -0.01),
        ]
        picked, meta = select_candidates(pool, max_picks=6, ev_floor=0.02)
        assert meta["step"] == "no_positive_ev"
        assert picked == []

    def test_step4_no_negative_ev_picks(self):
        pool = [
            self._candidate(i, "match_winner" if i % 2 else "over_under", -0.05 - i * 0.01)
            for i in range(1, 5)
        ]
        picked, meta = select_candidates(pool, max_picks=6)
        assert meta["step"] == "no_positive_ev"
        assert picked == []

    def test_empty_pool_returns_empty(self):
        picked, meta = select_candidates([], max_picks=6)
        assert picked == []
        assert meta["step"] == "empty"


class TestApplyDiversityRules:
    def _candidate(
        self,
        fixture_id: int,
        market: str,
        selection: str,
        ev: float,
    ) -> PickCandidate:
        c = PickCandidate(
            fixture_id=fixture_id,
            home_team="A",
            away_team="B",
            fixture_date=datetime.utcnow(),
            market=market,
            selection=selection,
            odds=2.0,
            opening_odds=2.0,
            fair_implied_prob=0.48,
            line=None,
            market_regime="moderate",
            ensemble=_result(expected_value=ev),
        )
        c.final_score = ev
        return c

    def test_max_two_draw_picks(self):
        pool = [
            self._candidate(i, "match_winner", "Draw", 0.10 - i * 0.01)
            for i in range(1, 6)
        ]
        picked = apply_diversity_rules(pool, max_picks=6)
        assert len(picked) == 2
        assert all(is_draw_pick(p) for p in picked)
        assert picked[0].fixture_id == 1
        assert picked[1].fixture_id == 2

    def test_one_pick_per_match(self):
        pool = [
            self._candidate(1, "match_winner", "Draw", 0.10),
            self._candidate(1, "over_under", "Under 2.5", 0.09),
            self._candidate(2, "match_winner", "Home", 0.08),
        ]
        picked = apply_diversity_rules(pool, max_picks=6)
        assert len(picked) == 2
        assert sum(1 for p in picked if p.fixture_id == 1) == 1

    def test_non_draw_not_capped_at_two(self):
        pool = [
            self._candidate(i, "over_under", "Under 2.5", 0.10 - i * 0.01)
            for i in range(1, 5)
        ]
        picked = apply_diversity_rules(pool, max_picks=6)
        assert len(picked) == 4


class TestTopKCandidates:
    def _candidate(self, fixture_id: int, ev: float) -> PickCandidate:
        c = PickCandidate(
            fixture_id=fixture_id,
            home_team="A",
            away_team="B",
            fixture_date=datetime.utcnow(),
            market="match_winner",
            selection="home",
            odds=2.0,
            opening_odds=2.0,
            fair_implied_prob=0.48,
            line=None,
            market_regime="moderate",
            ensemble=_result(expected_value=ev),
        )
        return c

    def test_top_k_returns_up_to_max(self):
        pool = [self._candidate(i, 0.10 - i * 0.01) for i in range(1, 9)]
        picked = top_k_candidates(pool, max_picks=6)
        assert len(picked) == 6
        assert picked[0].fixture_id == 1

    def test_top_k_empty_pool(self):
        assert top_k_candidates([], max_picks=6) == []
