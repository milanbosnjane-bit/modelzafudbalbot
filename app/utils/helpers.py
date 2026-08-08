"""Shared utilities."""

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger()


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def football_season_year(when: datetime | None = None) -> int:
    """Aug–Jul season start year (e.g. Jun 2026 -> 2025 for 2025/26)."""
    when = when or utc_now()
    return when.year if when.month >= 8 else when.year - 1


def normalize_selection(selection: str | int | float) -> str:
    return str(selection).lower().strip()


def football_season_candidates(when: datetime | None = None) -> list[int]:
    """Try current and adjacent seasons (off-season / transition)."""
    base = football_season_year(when)
    return [base, base + 1]


def last_completed_football_season(when: datetime | None = None) -> int:
    """Poslednja završena sezona (npr. jul 2026 → 2024/25 → 2024)."""
    return football_season_year(when) - 1


def implied_probability(odds: float) -> float:
    """Convert decimal odds to raw implied probability (includes vig)."""
    if odds <= 1.0:
        return 1.0
    return 1.0 / odds


def expected_value(probability: float, odds: float) -> float:
    """EV = (Probability * Odds) - 1. Use fair/devigged probability for edge detection."""
    return (probability * odds) - 1.0


def closing_line_value(
    bet_odds: float,
    closing_odds: float,
    closing_fair_prob: float | None = None,
) -> float:
    """
    RAW CLV (line shopping): (bet_odds / closing_odds) - 1.

    closing_fair_prob is ignored — use closing_fair_edge() for fair metric.
    """
    _ = closing_fair_prob
    if closing_odds <= 0 or bet_odds <= 0:
        return 0.0
    return (bet_odds / closing_odds) - 1.0


def odds_change_pct(opening_odds: float, current_odds: float) -> float:
    if opening_odds <= 0:
        return 0.0
    return (current_odds - opening_odds) / opening_odds


def kelly_stake(probability: float, odds: float, bankroll: float = 1.0) -> float:
    if odds <= 1.0:
        return 0.0
    b = odds - 1.0
    q = 1.0 - probability
    kelly = (b * probability - q) / b
    return max(0.0, kelly * bankroll)


def fractional_kelly_stake(
    probability: float,
    odds: float,
    fraction: float = 0.25,
    bankroll: float = 1.0,
    max_pct: float | None = None,
) -> float:
    """Fractional Kelly with hard cap as % of bankroll (default 2%)."""
    from app.config import get_settings
    settings = get_settings()
    max_pct = max_pct if max_pct is not None else settings.max_stake_pct_bankroll

    raw = kelly_stake(probability, odds, bankroll) * fraction
    cap = bankroll * max_pct
    return min(raw, cap)


def capped_stake(
    probability: float,
    odds: float,
    method: str = "fractional_kelly",
    bankroll: float | None = None,
) -> float:
    from app.config import get_settings
    settings = get_settings()
    bankroll = bankroll or settings.default_bankroll

    if method == "flat":
        return flat_stake(1.0)
    if method == "kelly":
        raw = kelly_stake(probability, odds, bankroll)
        return min(raw, bankroll * settings.max_stake_pct_bankroll)
    return fractional_kelly_stake(probability, odds, settings.kelly_fraction, bankroll)


def flat_stake(units: float = 1.0) -> float:
    return units


def model_agreement(probabilities: list[float]) -> float:
    if len(probabilities) < 2:
        return 1.0
    mean = sum(probabilities) / len(probabilities)
    variance = sum((p - mean) ** 2 for p in probabilities) / len(probabilities)
    std = math.sqrt(variance)
    spread = max(probabilities) - min(probabilities)
    std_score = max(0.0, 1.0 - (std / 0.20))
    spread_score = max(0.0, 1.0 - (spread / 0.22))
    return min(1.0, (std_score + spread_score) / 2)


def pick_rank_score(expected_value: float, confidence: float, agreement: float) -> float:
    """
    Heuristic ranking score — NOT realized ROI.
    Used only to sort candidates; thresholds tuned on walk-forward OOS.
    """
    return expected_value * 0.5 + confidence * 0.3 + agreement * 0.2


# Backward-compatible alias (deprecated name)
roi_score = pick_rank_score


def cache_key(prefix: str, **kwargs: Any) -> str:
    payload = json.dumps(kwargs, sort_keys=True, default=str)
    digest = hashlib.md5(payload.encode()).hexdigest()[:12]
    return f"{prefix}:{digest}"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def decision_time(fixture_date: datetime, hours_before: float = 1.0) -> datetime:
    """Default bet decision window: T-1h before kickoff."""
    from datetime import timedelta
    return fixture_date - timedelta(hours=hours_before)
