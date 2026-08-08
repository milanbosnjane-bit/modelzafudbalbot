"""Dynamic market regime detection via KMeans clustering."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import joblib
import numpy as np
import structlog
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

REGIME_MODEL_PATH = settings.model_dir / "regime_kmeans.pkl"


class MarketRegime(str, Enum):
    STABLE = "stable"
    MODERATE = "moderate"
    HIGH_NOISE = "high_noise"


REGIME_THRESHOLDS = {
    MarketRegime.STABLE: {"ev": 0.015, "confidence": 0.55},
    MarketRegime.MODERATE: {"ev": 0.02, "confidence": 0.55},
    MarketRegime.HIGH_NOISE: {"ev": 0.04, "confidence": 0.55},
}

REGIME_WEIGHTS = {
    MarketRegime.STABLE: 1.05,
    MarketRegime.MODERATE: 1.0,
    MarketRegime.HIGH_NOISE: 0.85,
}

REGIME_FEATURE_KEYS = [
    "market_overround",
    "odds_volatility_7d",
    "average_line_movement",
    "liquidity_score",
    "goals_std_30d",
    "market_dispersion",
]


@dataclass
class RegimeProfile:
    regime: MarketRegime
    ev_threshold: float
    confidence_threshold: float
    cluster_id: int
    feature_vector: dict


def extract_regime_features(features: dict) -> dict:
    overround = float(features.get("market_overround_1x2", 0.05))
    line_move = abs(float(features.get("odds_change_pct_home", 0.0)))
    home_xg = float(features.get("home_weighted_xG_last5", 1.2))
    away_xg = float(features.get("away_weighted_xG_last5", 1.2))
    h2h = float(features.get("h2h_goal_avg", 2.5))

    return {
        "market_overround": overround,
        "odds_volatility_7d": min(1.0, line_move * 5 + overround),
        "average_line_movement": line_move,
        "liquidity_score": max(0.0, 1.0 - overround * 4),
        "goals_std_30d": abs(home_xg - away_xg) + (home_xg + away_xg) * 0.1,
        "market_dispersion": min(1.0, line_move * 3 + overround * 2),
    }


def _noise_score(vec: dict) -> float:
    return (
        vec["market_overround"] * 0.25
        + vec["odds_volatility_7d"] * 0.25
        + vec["market_dispersion"] * 0.25
        + (1.0 - vec["liquidity_score"]) * 0.25
    )


class RegimeDetector:
    """KMeans over regime feature vectors; maps clusters to stable/moderate/high_noise."""

    N_CLUSTERS = 3

    def __init__(self):
        self.scaler: StandardScaler | None = None
        self.kmeans: KMeans | None = None
        self.cluster_to_regime: dict[int, MarketRegime] = {}
        self._load_model()

    def fit(self, feature_rows: list[dict]) -> None:
        if len(feature_rows) < self.N_CLUSTERS * 5:
            logger.warning("regime_fit_insufficient_samples", n=len(feature_rows))
            return

        X = np.array([[r[k] for k in REGIME_FEATURE_KEYS] for r in feature_rows])
        self.scaler = StandardScaler()
        Xs = self.scaler.fit_transform(X)
        self.kmeans = KMeans(n_clusters=self.N_CLUSTERS, random_state=42, n_init=10)
        labels = self.kmeans.fit_predict(Xs)

        cluster_noise = {}
        for cid in range(self.N_CLUSTERS):
            members = [feature_rows[i] for i, lb in enumerate(labels) if lb == cid]
            cluster_noise[cid] = np.mean([_noise_score(m) for m in members])

        sorted_clusters = sorted(cluster_noise.items(), key=lambda x: x[1])
        self.cluster_to_regime = {
            sorted_clusters[0][0]: MarketRegime.STABLE,
            sorted_clusters[1][0]: MarketRegime.MODERATE,
            sorted_clusters[2][0]: MarketRegime.HIGH_NOISE,
        }
        self._save_model()
        logger.info("regime_kmeans_fitted", mapping=self.cluster_to_regime)

    def detect(self, features: dict, league_id: int, fixture_id: int | None = None) -> RegimeProfile:
        vec = extract_regime_features(features)
        regime, cluster_id = self._predict_regime(vec)
        thresholds = REGIME_THRESHOLDS[regime]

        profile = RegimeProfile(
            regime=regime,
            ev_threshold=thresholds["ev"],
            confidence_threshold=thresholds["confidence"],
            cluster_id=cluster_id,
            feature_vector=vec,
        )
        return profile

    def _predict_regime(self, vec: dict) -> tuple[MarketRegime, int]:
        if self.kmeans is None or self.scaler is None or not self.cluster_to_regime:
            return self._fallback_regime(vec)

        try:
            X = np.array([[vec[k] for k in REGIME_FEATURE_KEYS]])
            Xs = self.scaler.transform(X)
            cluster_id = int(self.kmeans.predict(Xs)[0])
            regime = self.cluster_to_regime.get(cluster_id)
            if regime is None:
                return self._fallback_regime(vec)
            return regime, cluster_id
        except Exception as e:
            logger.warning("regime_predict_fallback", error=str(e))
            return self._fallback_regime(vec)

    def _fallback_regime(self, vec: dict) -> tuple[MarketRegime, int]:
        return MarketRegime.MODERATE, 1

    def _save_model(self) -> None:
        if not self.kmeans or not self.scaler:
            return
        REGIME_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "cluster_to_regime": {str(k): v.value for k, v in self.cluster_to_regime.items()},
                "kmeans": self.kmeans,
                "scaler": self.scaler,
            },
            REGIME_MODEL_PATH,
        )

    def _load_model(self) -> None:
        if not REGIME_MODEL_PATH.exists():
            return
        try:
            data = joblib.load(REGIME_MODEL_PATH)
            self.cluster_to_regime = {
                int(k): MarketRegime(v) for k, v in data["cluster_to_regime"].items()
            }
            self.kmeans = data["kmeans"]
            self.scaler = data["scaler"]
        except Exception as e:
            logger.warning("regime_model_load_failed", error=str(e))
            self.kmeans = None
            self.scaler = None
            self.cluster_to_regime = {}

    async def persist_detection(
        self, session, profile: RegimeProfile, league_id: int, fixture_id: int | None
    ) -> None:
        from app.database.models import RegimeHistory

        session.add(RegimeHistory(
            fixture_id=fixture_id,
            league_id=league_id,
            cluster_id=profile.cluster_id,
            regime_label=profile.regime.value,
            ev_threshold=profile.ev_threshold,
            confidence_threshold=profile.confidence_threshold,
            features=profile.feature_vector,
        ))
