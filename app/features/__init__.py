"""Feature engineering package."""

from app.features.engineer import TEAM_FEATURE_KEYS, FeatureEngineer

FEATURE_NAMES = TEAM_FEATURE_KEYS  # backward compat for imports

__all__ = ["FeatureEngineer", "TEAM_FEATURE_KEYS", "FEATURE_NAMES"]
