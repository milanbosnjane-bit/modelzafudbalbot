"""Tests for pick status / pre-kickoff filtering."""

from datetime import datetime, timedelta

from app.telegram.pick_status import is_fixture_pre_kickoff, resolve_pick_status


class TestIsFixturePreKickoff:
    def test_future_ns_is_pre_kickoff(self):
        kickoff = datetime.utcnow() + timedelta(hours=2)
        assert is_fixture_pre_kickoff(kickoff, "NS") is True

    def test_past_kickoff_is_not_pre_kickoff(self):
        kickoff = datetime.utcnow() - timedelta(minutes=5)
        assert is_fixture_pre_kickoff(kickoff, "NS") is False

    def test_live_status_is_not_pre_kickoff(self):
        kickoff = datetime.utcnow() + timedelta(hours=1)
        assert is_fixture_pre_kickoff(kickoff, "1H") is False

    def test_finished_is_not_pre_kickoff(self):
        kickoff = datetime.utcnow() - timedelta(hours=2)
        assert is_fixture_pre_kickoff(kickoff, "FT") is False


class TestResolvePickStatus:
    def test_live_fixture_status(self):
        assert resolve_pick_status("pending", "1H") == "LIVE"

    def test_pending_before_start(self):
        assert resolve_pick_status("pending", "NS") == "PENDING"
