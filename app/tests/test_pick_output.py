"""Tests for LIVE PICKS / ROI output contract."""

from dataclasses import dataclass
from datetime import datetime

from app.predictions.pick_selector import SelectedPick
from app.telegram.pick_output import (
    assign_global_ranks,
    dedupe_picks,
    prepare_live_picks,
)


@dataclass
class _Row:
    status: str
    pick: SelectedPick


def _pick(
    fixture_id: int,
    rank: int,
    *,
    market: str = "over_under",
    selection: str = "Under 2.5",
    stake: float = 1.5,
    status: str = "PENDING",
    expected_value: float = 0.05,
    pick_rank_score: float = 0.04,
) -> _Row:
    sp = SelectedPick(
        fixture_id=fixture_id,
        match_label="A vs B",
        market=market,
        selection=selection,
        odds=1.85,
        opening_odds=1.90,
        fair_implied_prob=0.5,
        line=2.5,
        expected_return=expected_value,
        probability=0.55,
        expected_value=expected_value,
        confidence=0.7,
        pick_rank_score=pick_rank_score,
        stake_units=stake,
        stake_method="fractional_kelly",
        market_regime="moderate",
        reasoning=[],
        rank=rank,
        fixture_date=datetime(2026, 6, 28, 18, 0),
        status=status,
    )
    return _Row(status=status, pick=sp)


class TestPickOutputContract:
    def test_global_rank_never_resets(self):
        rows = [_pick(1, 1), _pick(2, 1), _pick(3, 1)]
        ranked = assign_global_ranks(rows)
        assert [r.pick.rank for r in ranked] == [1, 2, 3]

    def test_dedupe_same_fixture_market(self):
        rows = [_pick(10, 1), _pick(10, 2, selection="Under 2.5")]
        deduped, removed = dedupe_picks(rows)
        assert removed == 1
        assert len(deduped) == 1

    def test_prepare_live_picks_includes_live_status(self):
        rows = [
            _pick(1, 1, status="PENDING"),
            _pick(2, 2, status="LIVE"),
            _pick(3, 3, status="SETTLED"),
        ]
        out, stats = prepare_live_picks(rows)
        assert stats["total_render"] == 2
        assert {r.status for r in out} == {"PENDING", "LIVE"}

    def test_prepare_live_picks_flat_sequential(self):
        rows = [
            _pick(1, 3, status="PENDING"),
            _pick(2, 1, status="PENDING"),
            _pick(3, 2, status="SETTLED"),
            _pick(4, 1, stake=0.0, status="PENDING"),
        ]
        out, stats = prepare_live_picks(rows)
        assert stats["duplicates_removed"] == 0
        assert stats["total_render"] == 3
        assert [r.pick.rank for r in out] == [1, 2, 3]

    def test_prepare_live_picks_unlimited_shows_all(self):
        rows = [
            _Row(
                status="PENDING",
                pick=SelectedPick(
                    fixture_id=i,
                    match_label="A vs B",
                    market="over_under",
                    selection=f"Under {i}",
                    odds=1.85,
                    opening_odds=1.90,
                    fair_implied_prob=0.5,
                    line=2.5,
                    expected_return=ev,
                    probability=0.55,
                    expected_value=ev,
                    confidence=0.7,
                    pick_rank_score=ev,
                    stake_units=1.0,
                    stake_method="fractional_kelly",
                    market_regime="moderate",
                    reasoning=[],
                    rank=i,
                    fixture_date=datetime(2026, 6, 28, 18, 0),
                    status="PENDING",
                ),
            )
            for i, ev in enumerate([0.01, 0.12, 0.03, 0.15, 0.08, 0.20, 0.05, 0.11], start=1)
        ]
        out, stats = prepare_live_picks(rows, max_display=None)
        assert len(out) == 8
        assert stats["ev_trimmed"] == 0
        assert stats["unlimited"] is True

    def test_top_six_by_ev_score(self):
        rows = [
            _Row(
                status="PENDING",
                pick=SelectedPick(
                    fixture_id=i,
                    match_label="A vs B",
                    market="over_under",
                    selection=f"Under {i}",
                    odds=1.85,
                    opening_odds=1.90,
                    fair_implied_prob=0.5,
                    line=2.5,
                    expected_return=ev,
                    probability=0.55,
                    expected_value=ev,
                    confidence=0.7,
                    pick_rank_score=ev,
                    stake_units=1.0,
                    stake_method="fractional_kelly",
                    market_regime="moderate",
                    reasoning=[],
                    rank=i,
                    fixture_date=datetime(2026, 6, 28, 18, 0),
                    status="PENDING",
                ),
            )
            for i, ev in enumerate([0.01, 0.12, 0.03, 0.15, 0.08, 0.20, 0.05, 0.11], start=1)
        ]
        out, stats = prepare_live_picks(rows, max_display=6)
        assert len(out) == 6
        assert stats["ev_trimmed"] == 2
        evs = [r.pick.expected_value for r in out]
        assert evs == sorted(evs, reverse=True)
        assert evs[0] == 0.20
