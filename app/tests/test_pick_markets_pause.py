"""Regression: over_under is paused from live tip selection."""

from app.config import get_settings
from app.predictions.pick_selector import PickSelectionEngine


def test_pick_markets_exclude_over_under():
    assert "over_under" not in PickSelectionEngine.PICK_MARKETS
    assert PickSelectionEngine.PICK_MARKETS == frozenset({"match_winner", "btts"})


def test_supported_markets_still_ingest_over_under():
    # Odds for OU may still be stored for features; tips must not select them.
    assert "over_under" in get_settings().supported_markets


def test_probability_shrink_weight_balanced():
    from app.config import Settings

    get_settings.cache_clear()
    assert Settings.model_fields["probability_shrink_weight"].default == 0.45
