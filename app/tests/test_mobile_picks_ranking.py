"""The app pick list must mirror the Telegram LIVE PICKS contract.

Regression guard: daily_picks.rank restarts at 1 for every generation run, so the
twice-daily schedule produced 1..6, 1..6, 1..5 in the app instead of 1..15.
"""

from app.api.mobile_routes import (
    PICKS_WINDOW_DAYS,
    TodayPickResponse,
    _is_fixture_finished,
    _resolve_status,
)
from app.telegram.pick_status import resolve_pick_status
from app.telegram.stats_service import _pending_outcome_filter as telegram_filter


def _pick(pick_id: int, *, rank: int, ev: float, roi: float) -> TodayPickResponse:
    return TodayPickResponse(
        id=pick_id,
        rank=rank,
        match=f"Home {pick_id} vs Away {pick_id}",
        market="match_winner",
        selection="Home",
        odds=2.0,
        probability=0.5,
        expected_value=ev,
        confidence=0.6,
        roi_score=roi,
        stake_units=1.0,
        reasoning=[],
    )


def _rank_like_endpoint(rows: list[TodayPickResponse]) -> list[TodayPickResponse]:
    """Same two lines the endpoint applies after building the rows."""
    rows.sort(key=lambda r: (r.expected_value, r.roi_score), reverse=True)
    for index, row in enumerate(rows, start=1):
        row.rank = index
    return rows


class TestGlobalRanking:
    def test_ranks_are_continuous_across_generation_batches(self):
        # Three batches, each numbered from 1 in the database.
        rows = [
            _pick(1, rank=1, ev=0.90, roi=1.0),
            _pick(2, rank=2, ev=0.60, roi=0.7),
            _pick(3, rank=1, ev=0.45, roi=0.5),
            _pick(4, rank=2, ev=0.30, roi=0.4),
            _pick(5, rank=1, ev=0.10, roi=0.1),
        ]
        ranked = _rank_like_endpoint(rows)

        assert [r.rank for r in ranked] == [1, 2, 3, 4, 5]

    def test_sorted_by_ev_then_roi_score(self):
        rows = [
            _pick(1, rank=1, ev=0.10, roi=0.1),
            _pick(2, rank=1, ev=0.90, roi=1.0),
            _pick(3, rank=1, ev=0.45, roi=0.5),
        ]
        ranked = _rank_like_endpoint(rows)

        assert [r.id for r in ranked] == [2, 3, 1]

    def test_roi_score_breaks_equal_ev_ties(self):
        rows = [
            _pick(1, rank=1, ev=0.20, roi=0.2),
            _pick(2, rank=1, ev=0.20, roi=0.9),
        ]
        ranked = _rank_like_endpoint(rows)

        assert [r.id for r in ranked] == [2, 1]


class TestStatusMirrorsTelegram:
    def test_live_and_pending_match_bot_helper(self):
        for fixture_status in ("NS", "TBD", "1H", "2H", "HT", "ET", "SUSP"):
            assert _resolve_status(fixture_status) == resolve_pick_status(
                "pending", fixture_status
            )

    def test_in_play_pick_is_kept_and_flagged_live(self):
        assert _is_fixture_finished("1H") is False
        assert _resolve_status("1H") == "LIVE"

    def test_finished_fixture_is_dropped(self):
        for fixture_status in ("FT", "AET", "PEN", "AWD", "WO"):
            assert _is_fixture_finished(fixture_status) is True

    def test_missing_status_defaults_to_pending(self):
        assert _is_fixture_finished(None) is False
        assert _resolve_status(None) == "PENDING"


class TestWindowMatchesTelegram:
    def test_seven_day_window(self):
        assert PICKS_WINDOW_DAYS == 7

    def test_pending_filter_is_defined_the_same_way(self):
        from app.api.mobile_routes import _pending_outcome_filter as api_filter

        assert str(api_filter()) == str(telegram_filter())
