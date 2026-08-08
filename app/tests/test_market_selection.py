"""Tests for canonical market selection filters."""

from app.predictions.market_selection import (
    format_prediction_selection,
    is_eligible_selection,
    passes_prediction_type_filter,
)


class TestMarketSelection:
    def test_match_winner_core(self):
        assert is_eligible_selection("match_winner", "Home") is True
        assert is_eligible_selection("match_winner", "Away") is True

    def test_btts_rejects_composite(self):
        assert is_eligible_selection("btts", "Yes") is True
        assert is_eligible_selection("btts", "Home/Yes") is False

    def test_btts_no_permanently_blocked(self):
        assert format_prediction_selection("btts", "No") == "BTTS No"
        assert passes_prediction_type_filter("btts", "No") is False
        assert is_eligible_selection("btts", "No") is False
        assert is_eligible_selection("btts", "NG") is False

    def test_over_under_allowed_lines_only(self):
        assert is_eligible_selection("over_under", "Over 2.5", 2.5) is True
        assert is_eligible_selection("over_under", "Over 10.5", 10.5) is False
        assert is_eligible_selection("over_under", "Home/Over 2.5", 2.5) is False
