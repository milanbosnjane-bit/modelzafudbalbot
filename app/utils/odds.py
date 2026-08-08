"""Bookmaker margin removal and fair probability normalization."""

from __future__ import annotations


def implied_probability(odds: float) -> float:
    if odds <= 1.0:
        return 1.0
    return 1.0 / odds


def proportional_devig(outcome_odds: list[float]) -> list[float]:
    """
    Remove overround via proportional (multiplicative) devigging.
    fair_p_i = raw_p_i / sum(raw_p_j)
    """
    raw = [implied_probability(o) for o in outcome_odds if o > 1.0]
    if not raw:
        return []
    total = sum(raw)
    if total <= 0:
        return raw
    return [p / total for p in raw]


def devig_selection_fair_prob(outcome_odds: list[float], selection_index: int) -> float | None:
    fair = proportional_devig(outcome_odds)
    if not fair or selection_index >= len(fair):
        return None
    return fair[selection_index]


def market_overround(outcome_odds: list[float]) -> float:
    """Bookmaker margin: sum(raw implied) - 1."""
    raw = sum(implied_probability(o) for o in outcome_odds if o > 1.0)
    return max(0.0, raw - 1.0)


def fair_odds_from_prob(probability: float) -> float:
    if probability <= 0:
        return 999.0
    return 1.0 / probability


def closing_line_value_raw(bet_odds: float, closing_odds: float) -> float:
    """Raw odds ratio CLV (line shopping metric)."""
    if closing_odds <= 0 or bet_odds <= 0:
        return 0.0
    return (bet_odds / closing_odds) - 1.0


def closing_line_value_fair(
    bet_odds: float,
    closing_outcome_odds: list[float],
    selection_index: int,
) -> float:
    """
    CLV vs devigged closing fair probability.
    CLV_fair = (bet_odds * fair_p_close) - 1
    """
    fair_p = devig_selection_fair_prob(closing_outcome_odds, selection_index)
    if fair_p is None:
        return 0.0
    return (bet_odds * fair_p) - 1.0


def shrink_probability(model_prob: float, fair_implied: float, weight: float = 0.35) -> float:
    """Shrink model probability toward fair market to reduce overbetting."""
    w = min(1.0, max(0.0, weight))
    return (1.0 - w) * model_prob + w * fair_implied


def median_odds(odds_list: list[float]) -> float:
    if not odds_list:
        return 0.0
    sorted_odds = sorted(odds_list)
    n = len(sorted_odds)
    mid = n // 2
    if n % 2:
        return sorted_odds[mid]
    return (sorted_odds[mid - 1] + sorted_odds[mid]) / 2.0
