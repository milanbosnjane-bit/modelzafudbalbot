"""Numeric feature access — 0.0 is valid, only None is missing."""

from __future__ import annotations


def first_present(features: dict, *keys: str) -> float | None:
    """Return the first feature value that is not None (0.0 is kept)."""
    for key in keys:
        if key not in features:
            continue
        value = features[key]
        if value is not None:
            return float(value)
    return None


def numeric_feature(features: dict, key: str, default: float | None = None) -> float | None:
    """Read a numeric feature; None if missing unless default supplied."""
    if key not in features:
        return default
    value = features[key]
    if value is None:
        return default
    return float(value)


def match_xg_pair(features: dict) -> tuple[float | None, float | None]:
    """Home/away xG used by Poisson (venue-adjusted preferred)."""
    home = first_present(features, "home_venue_adjusted_xg", "home_weighted_xG_last5")
    away = first_present(features, "away_venue_adjusted_xg", "away_weighted_xG_last5")
    return home, away


def has_usable_match_xg(features: dict, min_xg: float) -> bool:
    """False when xG missing or too low — no reliable Poisson signal."""
    home, away = match_xg_pair(features)
    if home is None or away is None:
        return False
    return home >= min_xg and away >= min_xg
