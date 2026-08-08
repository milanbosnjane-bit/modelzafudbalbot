"""Tests for pick status resolution and live picks filter."""

from app.telegram.pick_status import resolve_pick_status


class TestResolvePickStatus:
    def test_explicit_status(self):
        assert resolve_pick_status("pending", "NS", "LIVE") == "LIVE"
        assert resolve_pick_status(None, None, "PENDING") == "PENDING"

    def test_settled_from_outcome(self):
        assert resolve_pick_status("win", "FT", None) == "SETTLED"
        assert resolve_pick_status("lose", "1H", None) == "SETTLED"

    def test_live_from_fixture(self):
        assert resolve_pick_status("pending", "1H", None) == "LIVE"
        assert resolve_pick_status("pending", "2H", None) == "LIVE"
        assert resolve_pick_status("pending", "HT", None) == "LIVE"

    def test_void_is_settled(self):
        assert resolve_pick_status("void", "NS", None) == "SETTLED"

    def test_pending_fallback(self):
        assert resolve_pick_status(None, None, None) == "PENDING"
        assert resolve_pick_status("pending", "NS", None) == "PENDING"

    def test_lowercase_outcome(self):
        assert resolve_pick_status("pending", "NS", None) == "PENDING"
