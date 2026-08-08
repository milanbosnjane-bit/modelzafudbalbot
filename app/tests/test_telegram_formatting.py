"""Tests for Telegram betting tip formatting."""

from datetime import datetime
from unittest.mock import patch

import pytest

from app.telegram.formatting import (
    confidence_strength,
    fmt_team,
    format_betting_tip,
    format_ev_percent,
    format_kickoff,
    format_kickoff_time,
    format_tip,
    tip_explanation,
    tip_reason,
)
from app.telegram.bot import TelegramNotifier
from app.predictions.pick_selector import SelectedPick


class TestBettingTipFormat:
    @pytest.mark.parametrize(
        "market,selection,line,expected",
        [
            ("match_winner", "home", None, "1 (Pobeda domaćina)"),
            ("match_winner", "away", None, "2 (Pobeda gosta)"),
            ("match_winner", "draw", None, "X (Nerešeno)"),
            ("btts", "yes", None, "GG"),
            ("btts", "no", None, "NG"),
            ("over_under", "over", 2.5, "Ukupno golova — Više od 2.5"),
            ("over_under", "under", 2.5, "Ukupno golova — Manje od 2.5"),
            ("asian_handicap", "home", -1.0, "Hendikep domaćin -1"),
            ("asian_handicap", "away", 1.0, "Hendikep gost +1"),
        ],
    )
    def test_tip_mapping(self, market, selection, line, expected):
        assert format_betting_tip(market, selection, line) == expected

    @pytest.mark.parametrize(
        "market,selection,line,expected",
        [
            ("match_winner", "draw", None, "Utakmica završava nerešeno"),
            ("match_winner", "home", None, "Domaćin mora pobediti"),
            ("match_winner", "away", None, "Gost mora pobediti"),
            ("over_under", "under", 2.5, "Maksimalno 2 gola na meču"),
            ("over_under", "over", 2.5, "Potrebna su najmanje 3 gola na meču"),
            ("btts", "yes", None, "Oba tima daju gol"),
            ("btts", "no", None, "Najmanje jedan tim ne daje gol"),
        ],
    )
    def test_tip_explanation(self, market, selection, line, expected):
        assert tip_explanation(market, selection, line) == expected

    def test_confidence_strength(self):
        stars, count = confidence_strength(0.10)
        assert count == 2
        assert stars == "⭐⭐"
        stars, count = confidence_strength(0.20)
        assert count == 3
        stars, count = confidence_strength(0.35)
        assert count == 4
        stars, count = confidence_strength(0.50)
        assert count == 5

    def test_format_ev_percent(self):
        assert format_ev_percent(0.11) == "+11"
        assert format_ev_percent(-0.02) == "-2"

    def test_format_kickoff_time(self):
        assert format_kickoff_time(datetime(2026, 6, 27, 18, 30)) == "🕒 POČETAK: 20:30 (srpsko vreme)"
        assert format_kickoff_time(None) == "🕒 POČETAK: N/A"

    def test_fmt_team(self):
        assert fmt_team("América RJ") == "*AMÉRICA RJ*"

    def test_tip_reason_ng(self):
        assert tip_reason("btts", "no") == "• Očekuje se da bar jedan tim ne postigne gol"
        assert tip_reason("over_under", "under", 2.5) == "• Maks 2 gola na meču"


class TestFormatTip:
    @pytest.mark.parametrize(
        "market,selection,line,expected",
        [
            ("DRAW", "", None, "🎯 TIP: X (Nerešeno)"),
            ("match_winner", "draw", None, "🎯 TIP: X (Nerešeno)"),
            ("UNDER_2_5", "", None, "🎯 TIP: Under 2.5 golova (0–2 gola)"),
            ("over_under", "under", 2.5, "🎯 TIP: Under 2.5 golova (0–2 gola)"),
            ("OVER_2_5", "", None, "🎯 TIP: Over 2.5 golova (3+ gola)"),
            ("over_under", "over", 2.5, "🎯 TIP: Over 2.5 golova (3+ gola)"),
            ("btts", "yes", None, "🎯 TIP: BTTS — DA (oba tima daju gol)"),
            ("btts", "no", None, "🎯 TIP: BTTS — NE (oba tima ne daju gol)"),
            ("NG", "", None, "🎯 TIP: BTTS — NE (oba tima ne daju gol)"),
        ],
    )
    def test_format_tip(self, market, selection, line, expected):
        assert format_tip(market, selection, line) == expected


class TestTelegramPickMessage:
    @patch("app.telegram.bot.settings.use_calibrated_confidence", False)
    def test_format_pick_layout(self):
        pick = SelectedPick(
            fixture_id=1,
            match_label="XV de Piracicaba vs América RJ",
            market="over_under",
            selection="under",
            odds=1.57,
            opening_odds=1.57,
            fair_implied_prob=0.5,
            line=2.5,
            expected_return=-0.02,
            probability=0.63,
            expected_value=-0.02,
            confidence=0.34,
            pick_rank_score=0.5,
            stake_units=0.0,
            stake_method="fractional_kelly",
            market_regime="moderate",
            reasoning=[],
            rank=1,
            fixture_date=datetime(2026, 6, 28, 18, 30),
        )
        text = TelegramNotifier().format_pick(pick)
        assert "#1 *XV DE PIRACICABA* vs *AMÉRICA RJ*" in text
        assert "🕒 POČETAK: 20:30 (srpsko vreme)" in text
        assert "🎯 TIP: Under 2.5 golova (0–2 gola)" in text
        assert "💰 KVOTA (bot): 1.57" in text
        assert "DC/Fair: 63% / 50%" in text
        assert "📈 EV: -2%" in text
        assert "🔒 CONF: 34%" in text
        assert "💵 PREPORUKA: 0.00u" in text
        assert "• Maks 2 gola na meču" in text

    def test_header_format(self):
        header = TelegramNotifier().format_header("2026-06-27")
        assert header == "⚽ FOOTBALL PICKS | 2026-06-27 | Dixon-Coles\n\n━━━━━━━━━━━━━━━"

    def test_full_message_layout(self):
        pick = SelectedPick(
            fixture_id=1,
            match_label="Home vs Away",
            market="over_under",
            selection="under",
            odds=1.95,
            opening_odds=1.95,
            fair_implied_prob=0.5,
            line=2.5,
            expected_return=0.05,
            probability=0.52,
            expected_value=0.05,
            confidence=0.25,
            pick_rank_score=0.5,
            stake_units=1.0,
            stake_method="fractional_kelly",
            market_regime="moderate",
            reasoning=[],
            rank=1,
            fixture_date=datetime(2026, 6, 28, 20, 0),
        )
        notifier = TelegramNotifier()
        msg = (
            notifier.format_header("2026-06-28")
            + "\n\n"
            + notifier.format_pick(pick)
            + "\n\n"
            + "━━━━━━━━━━━━━━━"
        )
        assert "⚽ FOOTBALL PICKS | 2026-06-28" in msg
        assert "🕒 POČETAK: 22:00 (srpsko vreme)" in msg
        assert "🎯 TIP: Under 2.5 golova (0–2 gola)" in msg
