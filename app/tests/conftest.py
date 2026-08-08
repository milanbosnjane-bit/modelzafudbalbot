"""Pytest configuration."""

import pytest

@pytest.fixture
def sample_features():
    return {
        "home_weighted_xG_last5": 1.6,
        "away_weighted_xG_last5": 1.2,
        "home_weighted_xGA": 1.0,
        "away_weighted_xGA": 1.4,
        "home_momentum_score": 0.7,
        "away_momentum_score": 0.5,
        "home_injury_impact_score": 0.1,
        "away_injury_impact_score": 0.2,
        "home_home_away_strength": 1.08,
        "away_home_away_strength": 0.92,
        "opening_odds": 2.0,
        "current_odds": 1.95,
        "odds_change_pct": -0.025,
        "sharp_money_signal": 0.025,
    }

@pytest.fixture
def sample_odds():
    return {"odds": 2.05, "opening_odds": 2.10, "bookmaker_count": 3}
