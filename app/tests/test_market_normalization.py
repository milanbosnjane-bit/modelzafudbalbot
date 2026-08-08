"""Ingestion market allowlist — bet names below are real API-Football names (/odds/bets)."""

import pytest

from app.services.ingestion import DataIngestionService

# Names that previously leaked into a full-time market through substring matching.
LEAKED_INTO_OVER_UNDER = [
    "Goals Over/Under First Half",
    "Goals Over/Under - Second Half",
    "Home Team Total Goals(1st Half)",
    "Away Team Total Goals(1st Half)",
    "Home Team Total Goals(2nd Half)",
    "Result/Total Goals",
    "Result/Total Goals (2nd Half)",
    "Halftime Result/Total Goals",
    "Corners Over Under",
    "Home Corners Over/Under",
    "Cards Over/Under",
    "Yellow Over/Under",
    "Yellow Over/Under (1st Half)",
    "Exact Goals Number",
    "Exact Goals Number - First Half",
    "Total Goals (3 way)",
    "Total Goals Number By Ranges",
    "Number Of Goals In Match",
    "Number of Goals In Match (Range)",
    "10 Over/Under",
    "Over/Under 15m-30m",
    "Cards over/under between 0 and 10 m",
    "Double Chance/Total",
]

LEAKED_INTO_MATCH_WINNER = [
    "Home/Away",
    "Corners 1x2",
    "Corners 1x2 (1st Half)",
    "1x2 - 15 minutes",
    "1x2 - 30 minutes",
    "1x2 - 60 minutes",
    "1x2 - 75 minutes",
    "Yellow Cards 1x2",
    "Yellow Cards 1x2 (2nd Half)",
    "Offsides 1x2",
    "Fouls. 1x2",
    "ShotOnTarget 1x2",
    "Shots.1x2",
]

LEAKED_INTO_BTTS = [
    "Both Teams Score - First Half",
    "Both Teams To Score - Second Half",
    "Both Teams to Score 1st Half - 2nd Half",
    "Both Teams To Score in Both Halves",
    "Both Teams to Receive a Card",
    "Results/Both Teams Score",
    "Halftime Result/Both Teams Score",
    "Total Goals/Both Teams To Score",
    "Double Chance/Both Teams To Score",
]

OTHER_IGNORED = [
    "Asian Handicap",
    "Asian Handicap First Half",
    "Double Chance",
    "Double Chance - First Half",
    "Handicap Result",
    "Second Half Winner",
    "First Half Winner",
    "HT/FT Double",
    "Exact Score",
    "Correct Score - First Half",
    "Total - Home",
    "Total - Away",
    "Goal Line",
    "Odd/Even",
    "Anytime Goal Scorer",
    "Offsides Total",
    "Fouls. Total",
    "Total Corners (3 way)",
    "",
]


class TestMarketAllowlist:
    def test_full_time_markets_are_kept(self):
        svc = DataIngestionService.__new__(DataIngestionService)
        assert svc._normalize_market("Match Winner") == "match_winner"
        assert svc._normalize_market("Goals Over/Under") == "over_under"
        assert svc._normalize_market("Both Teams Score") == "btts"

    def test_name_matching_tolerates_case_and_spacing(self):
        svc = DataIngestionService.__new__(DataIngestionService)
        assert svc._normalize_market("  match   winner ") == "match_winner"
        assert svc._normalize_market("GOALS OVER/UNDER") == "over_under"
        assert svc._normalize_market("Both Teams To Score") == "btts"

    @pytest.mark.parametrize(
        "name",
        LEAKED_INTO_OVER_UNDER + LEAKED_INTO_MATCH_WINNER + LEAKED_INTO_BTTS + OTHER_IGNORED,
    )
    def test_everything_else_is_ignored(self, name):
        svc = DataIngestionService.__new__(DataIngestionService)
        assert svc._normalize_market(name) is None


class TestCanonicalSelection:
    @pytest.fixture
    def svc(self):
        return DataIngestionService.__new__(DataIngestionService)

    @pytest.mark.parametrize(
        "raw,expected",
        [("Home", "Home"), ("Draw", "Draw"), ("Away", "Away"),
         ("home", "Home"), ("1", "Home"), ("X", "Draw"), ("2", "Away")],
    )
    def test_match_winner(self, svc, raw, expected):
        assert svc._canonical_selection("match_winner", raw) == (expected, None)

    @pytest.mark.parametrize("raw", ["Home/Draw", "No goal", "1st Half", ""])
    def test_match_winner_rejects_foreign_values(self, svc, raw):
        assert svc._canonical_selection("match_winner", raw) is None

    @pytest.mark.parametrize(
        "raw,expected", [("Yes", "Yes"), ("No", "No"), ("yes", "Yes")]
    )
    def test_btts(self, svc, raw, expected):
        assert svc._canonical_selection("btts", raw) == (expected, None)

    @pytest.mark.parametrize(
        "raw,expected",
        [("Over 2.5", ("Over 2.5", 2.5)), ("Under 2.5", ("Under 2.5", 2.5)),
         ("over 1.5", ("Over 1.5", 1.5)), ("Under 3.5", ("Under 3.5", 3.5))],
    )
    def test_over_under(self, svc, raw, expected):
        assert svc._canonical_selection("over_under", raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "Away/Over 2.5",   # Result/Total Goals combo
            "Draw/Under 2.5",
            "Home Win/Over 2.5",
            "Over 0.5",        # line outside the allowed set
            "Over 4.5",
            "Over 2.25",       # asian line
            "Over",
            "2.5",
            "Yes",
        ],
    )
    def test_over_under_rejects_combos_and_foreign_lines(self, svc, raw):
        assert svc._canonical_selection("over_under", raw) is None

    def test_line_formatting_has_no_trailing_zero(self, svc):
        selection, line = svc._canonical_selection("over_under", "Over 2.50")
        assert selection == "Over 2.5"
        assert line == 2.5


class TestDevigGroupShape:
    """After the allowlist, a group holds exactly the outcomes of one real bet."""

    def test_group_sizes(self):
        svc = DataIngestionService.__new__(DataIngestionService)
        bets = {
            "Match Winner": ["Home", "Draw", "Away"],
            "Goals Over/Under": ["Over 2.5", "Under 2.5", "Over 1.5", "Under 1.5"],
            "Both Teams Score": ["Yes", "No"],
            # Noise that used to join the groups above:
            "Home/Away": ["Home", "Away"],
            "Goals Over/Under First Half": ["Over 2.5", "Under 2.5"],
            "Result/Total Goals": ["Away/Over 2.5", "Draw/Under 2.5"],
            "Corners Over Under": ["Over 2.5", "Under 2.5"],
        }

        groups: dict[tuple, list[str]] = {}
        for name, values in bets.items():
            market = svc._normalize_market(name)
            if market is None:
                continue
            for value in values:
                canonical = svc._canonical_selection(market, value)
                if canonical is None:
                    continue
                selection, line = canonical
                groups.setdefault((market, line), []).append(selection)

        assert groups == {
            ("match_winner", None): ["Home", "Draw", "Away"],
            ("over_under", 2.5): ["Over 2.5", "Under 2.5"],
            ("over_under", 1.5): ["Over 1.5", "Under 1.5"],
            ("btts", None): ["Yes", "No"],
        }
