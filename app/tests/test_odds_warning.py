"""Unit tests for pre-kickoff adverse odds warning helpers."""

from datetime import datetime, timedelta

from app.services.odds_warning import (
    WINDOW_MAX_MINUTES,
    WINDOW_MIN_MINUTES,
    format_odds_warning_message,
    in_pre_kickoff_window,
    minutes_to_kickoff,
    odds_jump_pct,
)


class TestOddsJumpPct:
    def test_triggers_at_three_percent(self):
        # 2.10 -> 2.17 = +3.333...%
        jump = odds_jump_pct(2.10, 2.17)
        assert jump is not None
        assert jump >= 3.0

    def test_below_threshold(self):
        # 2.10 -> 2.15 = +2.38%
        jump = odds_jump_pct(2.10, 2.15)
        assert jump is not None
        assert jump < 3.0

    def test_exact_three_percent(self):
        jump = odds_jump_pct(2.0, 2.06)
        assert jump is not None
        assert abs(jump - 3.0) < 1e-9

    def test_invalid_odds(self):
        assert odds_jump_pct(1.0, 2.0) is None
        assert odds_jump_pct(2.0, 0.5) is None
        assert odds_jump_pct(None, 2.0) is None  # type: ignore[arg-type]


class TestPreKickoffWindow:
    def test_inside_window(self):
        now = datetime(2026, 8, 9, 12, 0, 0)
        kickoff = now + timedelta(minutes=30)
        assert in_pre_kickoff_window(kickoff, now=now) is True
        assert WINDOW_MIN_MINUTES <= minutes_to_kickoff(kickoff, now=now) <= WINDOW_MAX_MINUTES

    def test_outside_window_too_early(self):
        now = datetime(2026, 8, 9, 12, 0, 0)
        kickoff = now + timedelta(minutes=40)
        assert in_pre_kickoff_window(kickoff, now=now) is False

    def test_outside_window_too_late(self):
        now = datetime(2026, 8, 9, 12, 0, 0)
        kickoff = now + timedelta(minutes=20)
        assert in_pre_kickoff_window(kickoff, now=now) is False

    def test_boundaries(self):
        now = datetime(2026, 8, 9, 12, 0, 0)
        assert in_pre_kickoff_window(now + timedelta(minutes=25), now=now) is True
        assert in_pre_kickoff_window(now + timedelta(minutes=35), now=now) is True
        assert in_pre_kickoff_window(now + timedelta(minutes=24.9), now=now) is False
        assert in_pre_kickoff_window(now + timedelta(minutes=35.1), now=now) is False

    def test_missing_fixture_date(self):
        assert in_pre_kickoff_window(None) is False


class TestWarningMessage:
    def test_format_contains_required_lines(self):
        text = format_odds_warning_message(
            home="Team A",
            away="Team B",
            market="match_winner",
            selection="home",
            line=None,
            initial_odds=2.10,
            current_odds=2.17,
            jump_pct=3.333,
        )
        assert "⚠️ UPOZORENJE / NE UPLAĆIVATI!" in text
        assert "⚽ Meč: Team A vs Team B" in text
        assert "🎯 TIP:" in text
        assert "📉 Prvobitna kvota: 2.10" in text
        assert "📈 Trenutna kvota: 2.17 (+3.3% skok)" in text
        assert "🚫 Savet:" in text
        assert "preskoči" in text
