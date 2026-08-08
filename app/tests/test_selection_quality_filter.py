"""Testovi za edge-based quality filter (kvote >= 2.0)."""

from datetime import datetime
from types import SimpleNamespace

from app.predictions.pick_selector import (
    GLOBAL_MIN_ODDS,
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

    def test_draw_passes_with_ev_and_edge(self):
        c = _candidate("match_winner", "Draw", ev=0.10, odds=3.0, calibrated_prob=0.40)
        ok, reason = passes_selection_filter(c)
        assert ok, reason

    def test_draw_rejected_low_ev(self):
        c = _candidate("match_winner", "Draw", ev=0.02, odds=3.0, calibrated_prob=0.38)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "ev_too_low" in reason

    def test_draw_rejected_low_edge(self):
        c = _candidate("match_winner", "Draw", ev=0.05, odds=3.0, calibrated_prob=0.34)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "edge_too_low" in reason

    def test_under25_passes_at_odds_above_floor(self):
        c = _candidate("over_under", "Under 2.5", ev=0.06, odds=2.10, calibrated_prob=0.52)
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

    def test_away_rejected_low_ev(self):
        c = _candidate("match_winner", "Away", ev=0.02, odds=4.0, calibrated_prob=0.30)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "ev_too_low" in reason

    def test_away_rejected_low_edge(self):
        c = _candidate("match_winner", "Away", ev=0.08, odds=4.0, calibrated_prob=0.26)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "edge_too_low" in reason

    def test_away_rejected_odds_too_high(self):
        c = _candidate("match_winner", "Away", ev=0.15, odds=9.0, calibrated_prob=0.20)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "odds_too_high" in reason

    def test_away_junk_odds_rejected(self):
        c = _candidate("match_winner", "Away", ev=0.15, odds=14.50, calibrated_prob=0.12)
        ok, _ = passes_selection_filter(c)
        assert not ok

    def test_away_passes_when_ev_and_edge_met(self):
        c = _candidate("match_winner", "Away", ev=0.10, odds=4.50, calibrated_prob=0.32)
        ok, reason = passes_selection_filter(c)
        assert ok, reason

    def test_home_rejected_low_ev_mid_bucket(self):
        c = _candidate("match_winner", "Home", ev=0.025, odds=2.80, calibrated_prob=0.40)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "ev_too_low" in reason

    def test_away_borderline_now_passes_relaxed_high_bucket(self):
        # Edge 5.0pp @4.50 — ispod starog 6pp, iznad novog 5pp
        c = _candidate("match_winner", "Away", ev=0.22, odds=4.50, calibrated_prob=0.273)
        ok, reason = passes_selection_filter(c)
        assert ok, reason

    def test_home_rejected_odds_above_cap(self):
        c = _candidate("match_winner", "Home", ev=0.10, odds=7.50, calibrated_prob=0.20)
        ok, reason = passes_selection_filter(c)
        assert not ok
        assert "odds_too_high" in reason

    def test_home_passes_mid_bucket(self):
        c = _candidate("match_winner", "Home", ev=0.06, odds=2.80, calibrated_prob=0.42)
        ok, reason = passes_selection_filter(c)
        assert ok, reason

    def test_only_home_away_have_bucket_filters(self):
        assert ("match_winner", "home") in SELECTION_QUALITY_FILTERS
        assert ("match_winner", "away") in SELECTION_QUALITY_FILTERS
        assert ("match_winner", "draw") not in SELECTION_QUALITY_FILTERS
