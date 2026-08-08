"""Tests for context gates (fatigue, market, lineup)."""

from datetime import datetime, timedelta

import pytest

from app.predictions.context_gates import (
    ContextGateInput,
    passes_context_gates,
    passes_fatigue_gate,
    passes_lineup_gate,
    passes_market_confirmation_gate,
)


def _features(
    *,
    home_fatigue=0.3,
    away_fatigue=0.3,
    home_motivation=0.5,
    away_motivation=0.5,
    home_injury=0.0,
    away_injury=0.0,
    home_rotation=0.0,
    away_rotation=0.0,
) -> dict:
    return {
        "home_fatigue_score": home_fatigue,
        "away_fatigue_score": away_fatigue,
        "home_motivation_score": home_motivation,
        "away_motivation_score": away_motivation,
        "home_injury_impact_score": home_injury,
        "away_injury_impact_score": away_injury,
        "home_rotation_score": home_rotation,
        "away_rotation_score": away_rotation,
    }


class TestFatigueGate:
    def test_blocks_fresh_teams_high_motivation_under(self):
        gate = passes_fatigue_gate(
            ContextGateInput(market="over_under", selection="under"),
            _features(
                home_fatigue=0.1,
                away_fatigue=0.15,
                home_motivation=0.7,
                away_motivation=0.68,
            ),
        )
        assert not gate.passed
        assert gate.drop_reason == "fatigue_fresh_high_motivation"

    def test_blocks_exhausted_home_pick(self):
        gate = passes_fatigue_gate(
            ContextGateInput(market="match_winner", selection="home"),
            _features(home_fatigue=0.75, away_fatigue=0.2),
        )
        assert not gate.passed
        assert gate.drop_reason == "fatigue_picked_side_exhausted"

    def test_allows_tired_teams_under(self):
        gate = passes_fatigue_gate(
            ContextGateInput(market="over_under", selection="under"),
            _features(home_fatigue=0.4, away_fatigue=0.45),
        )
        assert gate.passed
        assert gate.notes


class TestMarketGate:
    def test_blocks_adverse_move(self):
        gate = passes_market_confirmation_gate(opening_odds=2.0, current_odds=2.06)
        assert not gate.passed
        assert gate.drop_reason == "market_adverse_move"

    def test_allows_shortening(self):
        gate = passes_market_confirmation_gate(opening_odds=2.0, current_odds=1.95)
        assert gate.passed
        assert any("potvrđuje" in n for n in (gate.notes or []))

    def test_neutral_without_opening(self):
        gate = passes_market_confirmation_gate(opening_odds=None, current_odds=1.9)
        assert gate.passed


class TestLineupGate:
    def test_blocks_injured_home_pick(self):
        gate = passes_lineup_gate(
            ContextGateInput(market="match_winner", selection="home"),
            _features(home_injury=0.6),
        )
        assert not gate.passed
        assert gate.drop_reason == "lineup_injury_picked_side"

    def test_blocks_heavy_rotation_away(self):
        gate = passes_lineup_gate(
            ContextGateInput(market="match_winner", selection="away"),
            _features(away_rotation=0.8),
        )
        assert not gate.passed
        assert gate.drop_reason == "lineup_heavy_rotation"

    def test_blocks_missing_lineup_near_kickoff(self, monkeypatch):
        kickoff = datetime.utcnow() + timedelta(hours=1)
        gate = passes_lineup_gate(
            ContextGateInput(
                market="match_winner",
                selection="home",
                fixture_date=kickoff,
            ),
            _features(),
        )
        assert not gate.passed
        assert gate.drop_reason == "lineup_missing_near_kickoff"


class TestCombinedGates:
    def test_all_pass_with_notes(self):
        gate = passes_context_gates(
            ContextGateInput(market="over_under", selection="under"),
            _features(home_fatigue=0.4, away_fatigue=0.42, home_injury=0.4),
            opening_odds=1.95,
            current_odds=1.88,
        )
        assert gate.passed
        assert gate.notes

    def test_disabled_via_settings(self, monkeypatch):
        monkeypatch.setattr("app.predictions.context_gates.settings.context_gates_enabled", False)
        gate = passes_context_gates(
            ContextGateInput(market="match_winner", selection="home"),
            _features(home_fatigue=0.99),
            opening_odds=2.0,
            current_odds=2.1,
        )
        assert gate.passed
