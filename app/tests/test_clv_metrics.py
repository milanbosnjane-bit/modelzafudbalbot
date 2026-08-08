"""Tests for CLV RAW vs closing fair edge."""

import pytest

from app.utils.clv_metrics import (
    SnapshotOutcome,
    clv_raw,
    closing_fair_edge,
    compute_no_vig_fair_prob,
    fair_prob_matches_closing_odds,
    market_outcomes_complete,
    validated_closing_fair_prob,
)
from app.utils.helpers import closing_line_value


class TestClvRaw:
    def test_entry_7_close_580(self):
        assert clv_raw(7.00, 5.80) == pytest.approx(0.2069, abs=0.001)

    def test_entry_580_close_700(self):
        assert clv_raw(5.80, 7.00) == pytest.approx(-0.1714, abs=0.001)

    def test_entry_580_close_570(self):
        assert clv_raw(5.80, 5.70) == pytest.approx(0.0175, abs=0.001)

    def test_helpers_closing_line_value_is_raw_only(self):
        raw = closing_line_value(7.00, 5.80)
        with_fair = closing_line_value(7.00, 5.80, closing_fair_prob=0.05)
        assert raw == pytest.approx(0.2069, abs=0.001)
        assert with_fair == raw

    def test_closing_fair_edge_separate(self):
        assert closing_fair_edge(7.0, 0.15) == pytest.approx(0.05, abs=0.001)


class TestNoVigFair:
    def test_match_winner_three_outcomes(self):
        outcomes = [
            SnapshotOutcome("Home", 3.10),
            SnapshotOutcome("Draw", 3.50),
            SnapshotOutcome("Away", 2.20),
        ]
        assert market_outcomes_complete("match_winner", outcomes)
        fair_home = compute_no_vig_fair_prob("match_winner", "Home", outcomes)
        assert fair_home is not None
        assert fair_home == pytest.approx(0.322 / (0.322 + 0.286 + 0.455), abs=0.01)
        assert fair_prob_matches_closing_odds(fair_home, 3.10)

    def test_over_under_two_outcomes(self):
        outcomes = [
            SnapshotOutcome("Over 2.5", 2.10, line=2.5),
            SnapshotOutcome("Under 2.5", 1.75, line=2.5),
        ]
        assert market_outcomes_complete("over_under", outcomes)
        fair_over = compute_no_vig_fair_prob(
            "over_under", "Over 2.5", outcomes, pick_line=2.5
        )
        assert fair_over is not None
        assert 0.40 < fair_over < 0.55

    def test_btts_two_outcomes(self):
        outcomes = [
            SnapshotOutcome("Yes", 1.85),
            SnapshotOutcome("No", 2.00),
        ]
        assert market_outcomes_complete("btts", outcomes)
        fair_yes = compute_no_vig_fair_prob("btts", "Yes", outcomes)
        assert fair_yes is not None

    def test_incomplete_market_returns_none(self):
        outcomes = [SnapshotOutcome("Home", 3.10)]
        assert not market_outcomes_complete("match_winner", outcomes)
        assert compute_no_vig_fair_prob("match_winner", "Home", outcomes) is None

    def test_invalid_fair_vs_odds_rejected(self):
        outcomes = [
            SnapshotOutcome("Home", 3.10),
            SnapshotOutcome("Draw", 3.50),
            SnapshotOutcome("Away", 2.20),
        ]
        fair = validated_closing_fair_prob(
            "match_winner",
            "Home",
            3.10,
            outcomes,
        )
        assert fair is not None
        bad = validated_closing_fair_prob(
            "match_winner",
            "Home",
            3.10,
            outcomes,
            pick_id=1,
        )
        assert bad is not None
        assert fair_prob_matches_closing_odds(0.052, 3.10) is False
