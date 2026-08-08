"""Probability, EV, and confidence correctness — no fallback defaults."""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.config import get_settings

PROB_FLOOR = 0.05
PROB_CEIL = 0.95
# Legacy clamp bounds — used only to detect corrupted/clamped EV in stored data.
LEGACY_EV_CLAMP_MIN = -0.5
LEGACY_EV_CLAMP_MAX = 0.5

BLOCKED_MARKET_IDS = frozenset({
    "exact_score",
    "correct_score",
    "ht_ft",
    "half_time_full_time",
    "final_score",
    "double_chance",
})

DISABLED_MARKET_KEYWORDS = (
    "correct score",
    "exact score",
    "final score",
    "half time/full time",
    "ht/ft",
    "ht ft",
    "double chance",
)


def normalize_market_id(market: str) -> str:
    return (market or "").lower().replace("-", "_").replace(" ", "_")


def is_disabled_market(market: str) -> bool:
    norm = normalize_market_id(market)
    if norm in BLOCKED_MARKET_IDS:
        return True
    if any(
        token in norm
        for token in (
            "exact_score",
            "correct_score",
            "ht_ft",
            "htft",
            "half_time_full_time",
            "double_chance",
        )
    ):
        return True
    name = (market or "").lower().replace("_", " ")
    return any(kw in name for kw in DISABLED_MARKET_KEYWORDS)


def is_supported_market(market: str, supported: set[str] | None = None) -> bool:
    if is_disabled_market(market):
        return False
    allowed = supported if supported is not None else set(get_settings().supported_markets)
    return market in allowed


def is_valid_probability(p: float | None) -> bool:
    if p is None or math.isnan(p) or math.isinf(p):
        return False
    return PROB_FLOOR <= p <= PROB_CEIL


def sigmoid(x: float) -> float:
    x = max(-20.0, min(20.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def probability_from_return(expected_return: float, odds: float) -> float | None:
    """Map expected return to probability via logit-sigmoid (no hard clip to constants)."""
    if odds <= 1.0 or math.isnan(expected_return) or math.isinf(expected_return):
        return None
    raw_p = (expected_return + 1.0) / odds
    if raw_p <= 0.0 or raw_p >= 1.0:
        raw_p = min(1.0 - 1e-9, max(1e-9, raw_p))
    logit = math.log(raw_p / (1.0 - raw_p))
    return sigmoid(logit)


def compute_ev(calibrated_probability: float, decimal_odds: float) -> float | None:
    """Raw EV from calibrated probability and decimal odds — never clamped or defaulted."""
    if decimal_odds <= 1.0 or not is_valid_probability(calibrated_probability):
        return None
    ev = (calibrated_probability * decimal_odds) - 1.0
    if math.isnan(ev) or math.isinf(ev):
        return None
    return ev


def is_legacy_clamped_ev(ev: float) -> bool:
    """Detect EV values produced by the removed production clamp (±50%)."""
    if math.isnan(ev) or math.isinf(ev):
        return True
    return abs(ev - LEGACY_EV_CLAMP_MAX) < 1e-9 or abs(ev - LEGACY_EV_CLAMP_MIN) < 1e-9


def confidence_from_uncertainty(model_probabilities: list[float]) -> float | None:
    """Confidence from ensemble disagreement only — None if uncertainty unavailable."""
    valid = [p for p in model_probabilities if p is not None and not math.isnan(p)]
    if len(valid) < 2:
        return None
    mean_p = sum(valid) / len(valid)
    variance = sum((p - mean_p) ** 2 for p in valid) / len(valid)
    std = math.sqrt(variance)
    conf = 1.0 - min(1.0, std / 0.25)
    return max(0.0, min(1.0, conf))


def ev_variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def weighted_probability_average(
    probabilities: list[tuple[float, float]],
) -> float | None:
    """Weighted average of (probability, weight) pairs."""
    usable = [(p, w) for p, w in probabilities if p is not None and w > 0]
    if not usable:
        return None
    total_w = sum(w for _, w in usable)
    if total_w <= 0:
        return None
    return sum(p * w for p, w in usable) / total_w


@dataclass
class ProbabilityBundle:
    ensemble_probability: float
    calibrated_probability: float
    expected_value: float
    confidence: float
    agreement: float
    model_probabilities: list[float]
    valid: bool
    rejection_reason: str | None = None
