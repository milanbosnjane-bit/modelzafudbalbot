"""Telegram display for calibrated confidence (display-only)."""

from datetime import datetime
from unittest.mock import patch

from app.predictions.pick_selector import SelectedPick
from app.telegram.bot import TelegramNotifier


def _pick(**kwargs) -> SelectedPick:
    base = dict(
        fixture_id=1,
        match_label="Team A vs Team B",
        market="match_winner",
        selection="home",
        odds=5.0,
        opening_odds=5.0,
        fair_implied_prob=0.24,
        line=None,
        expected_return=0.84,
        probability=0.44,
        expected_value=0.84,
        confidence=0.95,
        pick_rank_score=0.8,
        stake_units=1.0,
        stake_method="fractional_kelly",
        market_regime="moderate",
        reasoning=[],
        rank=1,
        fixture_date=datetime(2026, 8, 3, 18, 0),
        calibrated_confidence=0.27,
    )
    base.update(kwargs)
    return SelectedPick(**base)


class TestCalibratedTelegramFormat:
    def test_legacy_conf_line_when_flag_off(self):
        text = TelegramNotifier().format_pick(_pick())
        assert "🔒 CONF: 95%" in text
        assert "Kalibrisana pouzdanost" not in text

    @patch("app.telegram.bot.settings.use_calibrated_confidence", True)
    def test_calibrated_display_when_flag_on(self):
        text = TelegramNotifier().format_pick(_pick())
        assert "Model verovatnoća: 44%" in text
        assert "Kalibrisana pouzdanost: 27%" in text
        assert "EV po modelu: +84%" in text
        assert "🔒 CONF:" not in text

    @patch("app.telegram.bot.settings.use_calibrated_confidence", True)
    def test_not_calibrated_fallback(self):
        text = TelegramNotifier().format_pick(_pick(calibrated_confidence=None))
        assert "Kalibrisana pouzdanost: nije kalibrisan" in text
