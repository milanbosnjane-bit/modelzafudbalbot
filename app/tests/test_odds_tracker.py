"""Odds tracker returns proposed pick selection only (not generic 1X2 board)."""

from app.api.mobile_routes import OddsTrackerRow, _odds_change_pct, _pick_selection_label


class TestPickSelectionLabel:
    def test_over_under(self):
        assert _pick_selection_label("over_under", "Over 2.5", 2.5) == "Over 2.5"
        assert _pick_selection_label("over_under", "under", 2.5) == "Under 2.5"

    def test_match_winner_serbian(self):
        assert _pick_selection_label("match_winner", "home", None) == "Pobeda Domaćina"
        assert _pick_selection_label("match_winner", "away", None) == "Pobeda Gosta"
        assert _pick_selection_label("match_winner", "draw", None) == "Nerešeno"

    def test_btts(self):
        assert _pick_selection_label("btts", "yes", None) == "BTTS Yes"
        assert _pick_selection_label("btts", "no", None) == "BTTS No"


class TestOddsChangePct:
    def test_up_move(self):
        assert _odds_change_pct(2.10, 2.18) == round((2.18 - 2.10) / 2.10, 6)

    def test_flat_when_invalid(self):
        assert _odds_change_pct(0, 2.0) == 0.0
        assert _odds_change_pct(2.0, 0) == 0.0


class TestOddsTrackerRowSchema:
    def test_response_fields_match_mobile_contract(self):
        row = OddsTrackerRow(
            fixture_id=101,
            match_title="Zvezda vs Partizan",
            pick_selection="Over 2.5",
            initial_odds=2.10,
            current_odds=2.18,
            odds_change_pct=0.038095,
        )
        payload = row.model_dump()
        assert set(payload.keys()) == {
            "fixture_id",
            "match_title",
            "pick_selection",
            "initial_odds",
            "current_odds",
            "odds_change_pct",
        }
        assert "home" not in payload
        assert "draw" not in payload
        assert "away" not in payload
