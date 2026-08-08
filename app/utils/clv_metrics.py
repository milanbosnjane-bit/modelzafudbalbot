"""CLV metrics — RAW line shopping vs closing fair edge (separate)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import structlog

from app.utils.helpers import normalize_selection
from app.utils.odds import implied_probability, proportional_devig

logger = structlog.get_logger()

# Max deviation of no-vig fair from raw implied at closing odds
_FAIR_ABS_TOLERANCE = 0.08
_FAIR_REL_TOLERANCE = 0.35

_MATCH_WINNER_CANON = {
    "home": "home",
    "1": "home",
    "draw": "draw",
    "x": "draw",
    "away": "away",
    "2": "away",
}

_OU_OVER = frozenset({"over", "over 2.5"})
_OU_UNDER = frozenset({"under", "under 2.5"})
_BTTS_YES = frozenset({"yes", "btts yes"})
_BTTS_NO = frozenset({"no", "btts no"})


def clv_raw(entry_odds: float, closing_odds: float) -> float:
    """Klasični CLV: (entry / closing) - 1."""
    if entry_odds <= 0 or closing_odds <= 0:
        return 0.0
    return (entry_odds / closing_odds) - 1.0


def closing_fair_edge(entry_odds: float, closing_fair_prob: float | None) -> float | None:
    """Edge vs no-vig closing fair — NIJE CLV."""
    if closing_fair_prob is None or entry_odds <= 0:
        return None
    return (entry_odds * closing_fair_prob) - 1.0


def fair_prob_matches_closing_odds(fair_prob: float, closing_odds: float) -> bool:
    implied = implied_probability(closing_odds)
    if fair_prob <= 0 or fair_prob >= 1 or implied <= 0:
        return False
    max_delta = max(_FAIR_ABS_TOLERANCE, implied * _FAIR_REL_TOLERANCE)
    return abs(fair_prob - implied) <= max_delta


def _canon_match_winner(selection: str) -> str | None:
    return _MATCH_WINNER_CANON.get(normalize_selection(selection))


def _canon_ou(selection: str) -> str | None:
    s = normalize_selection(selection)
    if "over" in s:
        return "over"
    if "under" in s:
        return "under"
    return None


def _canon_btts(selection: str) -> str | None:
    s = normalize_selection(selection)
    if s in _BTTS_YES or s == "yes":
        return "yes"
    if s in _BTTS_NO or s == "no":
        return "no"
    return None


@dataclass
class SnapshotOutcome:
    selection: str
    odds: float
    line: float | None = None


def market_outcomes_complete(market: str, outcomes: list[SnapshotOutcome]) -> bool:
    if market == "match_winner":
        canon = {_canon_match_winner(o.selection) for o in outcomes}
        return {"home", "draw", "away"}.issubset(canon)
    if market == "over_under":
        canon = {_canon_ou(o.selection) for o in outcomes}
        lines = {o.line for o in outcomes if o.line is not None}
        return "over" in canon and "under" in canon and len(lines) <= 1
    if market == "btts":
        canon = {_canon_btts(o.selection) for o in outcomes}
        return "yes" in canon and "no" in canon
    return len(outcomes) >= 2


def compute_no_vig_fair_prob(
    market: str,
    pick_selection: str,
    outcomes: Iterable[SnapshotOutcome],
    *,
    pick_line: float | None = None,
) -> float | None:
    """No-vig fair probability for pick selection from same snapshot group."""
    rows = list(outcomes)
    if not market_outcomes_complete(market, rows):
        return None

    if market == "over_under" and pick_line is not None:
        rows = [r for r in rows if r.line is None or abs(r.line - pick_line) < 0.01]
        if not market_outcomes_complete(market, rows):
            return None

    odds_list: list[float] = []
    selection_order: list[str] = []
    for row in rows:
        if row.odds <= 1.0:
            continue
        odds_list.append(row.odds)
        selection_order.append(row.selection)

    if len(odds_list) < 2:
        return None

    fair_list = proportional_devig(odds_list)
    if not fair_list:
        return None

    pick_canon = (
        _canon_match_winner(pick_selection)
        if market == "match_winner"
        else _canon_ou(pick_selection)
        if market == "over_under"
        else _canon_btts(pick_selection)
        if market == "btts"
        else normalize_selection(pick_selection)
    )

    for sel, fair in zip(selection_order, fair_list):
        row_canon = (
            _canon_match_winner(sel)
            if market == "match_winner"
            else _canon_ou(sel)
            if market == "over_under"
            else _canon_btts(sel)
            if market == "btts"
            else normalize_selection(sel)
        )
        if row_canon == pick_canon:
            return fair
    return None


def validated_closing_fair_prob(
    market: str,
    pick_selection: str,
    closing_odds: float,
    outcomes: Iterable[SnapshotOutcome],
    *,
    pick_line: float | None = None,
    pick_id: int | None = None,
    fixture_id: int | None = None,
) -> float | None:
    fair = compute_no_vig_fair_prob(
        market, pick_selection, outcomes, pick_line=pick_line
    )
    if fair is None:
        logger.warning(
            "FAIR_PROB_INCOMPLETE_MARKET",
            pick_id=pick_id,
            fixture_id=fixture_id,
            market=market,
            selection=pick_selection,
        )
        return None
    if not fair_prob_matches_closing_odds(fair, closing_odds):
        logger.warning(
            "FAIR_PROB_INVALID",
            pick_id=pick_id,
            fixture_id=fixture_id,
            market=market,
            selection=pick_selection,
            closing_odds=closing_odds,
            fair_prob=fair,
            implied=implied_probability(closing_odds),
        )
        return None
    return fair
