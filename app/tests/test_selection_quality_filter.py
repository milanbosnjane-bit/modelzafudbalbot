"""Testovi za strict odds floor (>=2.00), shrink-balanced thresholds."""

from datetime import datetime
from types import SimpleNamespace

from app.predictions.pick_selector import (
    DEFAULT_MAX_ODDS,
    DRAW_MAX_ODDS,
    DRAW_RULES,
    GLOBAL_MIN_ODDS,
    MAX_EV,
    SELECTION_QUALITY_FILTERS,
    PickCandidate,
    dynamic_quality_rule,
    passes_selection_filter,
)


def _candidate(
    market: str,
    selection: str,
    ev: float,
    odds: float,
    *,
    calibrated_prob: float | None = None,
    conf: float = 0.70,
):
    fair = 1.0 / odds
    cal = calibrated_prob if calibrated_prob is not None else fair + 0.05
    ensemble = SimpleNamespace(
        expected_value=ev,
        confidence=conf,
        pick_rank_score=ev,
        expected_return=ev,
        fair_implied_prob=fair,
        calibrated_probability=cal,
        rejection_reason=None,
        reasoning=[],
    )
    return PickCandidate(
        fixture_id=1,
        home_team="A",
        away_team="B",
        fixture_date=datetime.utcnow(),
        market=market,
        selection=selection,
        odds=odds,
        opening_odds=None,
        fair_implied_prob=fair,
        line=2.5 if market == "over_under" else None,
        market_regime="moderate",
        ensemble=ensemble,
    )


class TestDynamicQualityRule:

    def test_global_min_odds_blocks_low_odds(self):
        c = _candidate("match_winner", "Draw", ev=0.10, odds=1.85, calibrated_prob=0.60)
        ok, reason = dynamic_quality_rule(c)
        assert not ok
        assert "odds_below_floor" in reason
        assert GLOBAL_MIN_ODDS == 2.0

    def test_btts_below_floor_rejected(self):
        c = _candidate("btts", "Yes", ev=0.10, odds=1.95, calibrated_prob=0.55)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "odds_below_floor" in reason

    def test_max_ev_rejects_longshot_mirage(self):
        assert MAX_EV == 0.25
        c = _candidate("match_winner", "Draw", ev=0.40, odds=3.40, calibrated_prob=0.40)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "ev_too_high" in reason

    def test_draw_passes_with_strict_ev_and_edge(self):
        # fair@3.0≈0.333; need edge≥3.0pp → cal≥0.363; EV≥3.0%
        c = _candidate("match_winner", "Draw", ev=0.04, odds=3.0, calibrated_prob=0.37)
        ok, reason = passes_selection_filter(c)
        assert ok, reason

    def test_draw_rejected_low_ev(self):
        c = _candidate("match_winner", "Draw", ev=0.02, odds=3.0, calibrated_prob=0.38)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "ev_too_low" in reason
        assert DRAW_RULES["min_ev"] == 0.030

    def test_draw_rejected_low_edge(self):
        c = _candidate("match_winner", "Draw", ev=0.05, odds=3.0, calibrated_prob=0.35)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "edge_too_low" in reason

    def test_draw_rejected_odds_above_3_60(self):
        c = _candidate("match_winner", "Draw", ev=0.10, odds=3.70, calibrated_prob=0.40)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "odds_too_high" in reason
        assert DRAW_MAX_ODDS == 3.60

    def test_draw_at_cap_passes(self):
        c = _candidate("match_winner", "Draw", ev=0.04, odds=3.60, calibrated_prob=0.31)
        ok, reason = passes_selection_filter(c)
        assert ok, reason

    def test_under25_passes_at_odds_above_floor(self):
        c = _candidate("over_under", "Under 2.5", ev=0.03, odds=2.10, calibrated_prob=0.50)
        ok, reason = passes_selection_filter(c)
        assert ok, reason

    def test_under25_blocked_below_floor(self):
        c = _candidate("over_under", "Under 2.5", ev=0.06, odds=1.70, calibrated_prob=0.62)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "odds_below_floor" in reason

    def test_btts_no_permanently_blocked(self):
        c = _candidate("btts", "No", ev=0.05, odds=2.20, calibrated_prob=0.50)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert reason == "btts_no_blocked"

    def test_btts_yes_passes_mid_band(self):
        c = _candidate("btts", "Yes", ev=0.02, odds=2.40, calibrated_prob=0.44)
        ok, reason = passes_selection_filter(c)
        assert ok, reason

    def test_btts_yes_rejected_low_ev(self):
        c = _candidate("btts", "Yes", ev=0.01, odds=2.40, calibrated_prob=0.45)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "ev_too_low" in reason

    def test_btts_yes_rejected_above_max_odds(self):
        c = _candidate("btts", "Yes", ev=0.05, odds=4.80, calibrated_prob=0.30)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "odds_too_high" in reason

    def test_away_rejected_low_ev(self):
        c = _candidate("match_winner", "Away", ev=0.01, odds=4.0, calibrated_prob=0.30)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "ev_too_low" in reason

    def test_away_rejected_low_edge(self):
        c = _candidate("match_winner", "Away", ev=0.08, odds=4.0, calibrated_prob=0.255)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "edge_too_low" in reason

    def test_away_rejected_odds_too_high(self):
        c = _candidate("match_winner", "Away", ev=0.15, odds=5.0, calibrated_prob=0.28)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "odds_too_high" in reason

    def test_away_passes_when_ev_and_edge_met(self):
        c = _candidate("match_winner", "Away", ev=0.02, odds=4.50, calibrated_prob=0.24)
        ok, reason = passes_selection_filter(c)
        assert ok, reason

    def test_home_passes_1_5pct_ev(self):
        c = _candidate("match_winner", "Home", ev=0.018, odds=2.80, calibrated_prob=0.38)
        ok, reason = passes_selection_filter(c)
        assert ok, reason

    def test_home_rejected_below_1_5pct_ev(self):
        c = _candidate("match_winner", "Home", ev=0.01, odds=2.80, calibrated_prob=0.40)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "ev_too_low" in reason

    def test_home_rejected_odds_above_cap(self):
        c = _candidate("match_winner", "Home", ev=0.10, odds=5.40, calibrated_prob=0.24)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "odds_too_high" in reason

    def test_caps_and_filters_configured(self):
        assert DEFAULT_MAX_ODDS == 4.50
        assert DRAW_MAX_ODDS == 3.60
        assert SELECTION_QUALITY_FILTERS[("match_winner", "home")]["max_odds"] == 4.50
        assert SELECTION_QUALITY_FILTERS[("match_winner", "away")]["max_odds"] == 4.50
        assert SELECTION_QUALITY_FILTERS[("match_winner", "home")]["min_ev"] == 0.015
        assert SELECTION_QUALITY_FILTERS[("match_winner", "home")]["min_edge_pp"] == 1.5
        assert DRAW_RULES["min_edge_pp"] == 3.0
