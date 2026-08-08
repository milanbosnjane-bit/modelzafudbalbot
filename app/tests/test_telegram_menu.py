"""Tests for Telegram interactive menu."""

from app.telegram.keyboard import (
    BTN_LIVE,
    BTN_RESTART,
    BTN_RESULTS,
    BTN_ROI,
    BTN_SETTLE,
    BTN_STATUS,
    MAIN_MENU,
    STARTUP_MESSAGE,
)


class TestTelegramKeyboard:
    def test_startup_message(self):
        assert "FOOTBALL ROI BOT JE POKRENUT" in STARTUP_MESSAGE
        assert "Izaberi opciju" in STARTUP_MESSAGE

    def test_main_menu_buttons(self):
        flat = [btn.text for row in MAIN_MENU.keyboard for btn in row]
        assert BTN_ROI in flat
        assert BTN_LIVE in flat
        assert BTN_RESTART in flat
        assert BTN_STATUS in flat
        assert BTN_RESULTS in flat
        assert BTN_SETTLE in flat
        assert "AŽURIRANA STATISTIKA" not in flat

    def test_main_menu_resize(self):
        assert MAIN_MENU.resize_keyboard is True
