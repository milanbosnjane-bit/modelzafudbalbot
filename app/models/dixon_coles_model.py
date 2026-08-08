"""Dixon-Coles goal model — analitička score matrica + λ iz venue-adjusted xG."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.stats import poisson

from app.utils.feature_values import first_present, numeric_feature
from app.utils.helpers import normalize_selection


@dataclass
class DixonColesPrediction:
    home_goals_lambda: float
    away_goals_lambda: float
    rho: float
    score_matrix: np.ndarray
    probabilities: dict[str, float]


class DixonColesModel:
    """
    Dixon-Coles korekcija za niske rezultate (0-0, 0-1, 1-0, 1-1).

    P(h,a) ∝ τ(h,a,ρ) · Poisson(h;λ_h) · Poisson(a;λ_a), normalizovano na (MAX_GOALS+1)².
    """

    MAX_GOALS = 10
    DEFAULT_RHO = -0.13
    DEFAULT_XG_SCALE = 1.0
    DEFAULT_HOME_ADVANTAGE = 1.08

    def __init__(
        self,
        *,
        rho_by_league: dict[int, float] | None = None,
        default_rho: float = DEFAULT_RHO,
        xg_scale: float = DEFAULT_XG_SCALE,
        home_advantage: float = DEFAULT_HOME_ADVANTAGE,
    ):
        self.rho_by_league = rho_by_league or {}
        self.default_rho = default_rho
        self.xg_scale = xg_scale
        self.home_advantage = home_advantage

    @classmethod
    def from_params_file(cls, path: Path | None = None) -> DixonColesModel:
        """Učitaj kalibrisane parametre iz data/models/dc_params.json (opciono)."""
        if path is None:
            path = Path("./data/models/dc_params.json")
        if not path.is_file():
            return cls()

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()

        rho_map = {int(k): float(v) for k, v in (data.get("rho_by_league") or {}).items()}
        return cls(
            rho_by_league=rho_map,
            default_rho=float(data.get("default_rho", cls.DEFAULT_RHO)),
            xg_scale=float(data.get("xg_scale", cls.DEFAULT_XG_SCALE)),
            home_advantage=float(data.get("home_advantage", cls.DEFAULT_HOME_ADVANTAGE)),
        )

    def _tau(self, home: int, away: int, lambda_h: float, lambda_a: float, rho: float) -> float:
        if home == 0 and away == 0:
            return 1.0 - lambda_h * lambda_a * rho
        if home == 0 and away == 1:
            return 1.0 + lambda_h * rho
        if home == 1 and away == 0:
            return 1.0 + lambda_a * rho
        if home == 1 and away == 1:
            return 1.0 - rho
        return 1.0

    def build_score_matrix(
        self,
        home_lambda: float,
        away_lambda: float,
        rho: float,
    ) -> np.ndarray:
        lambda_h = max(0.05, home_lambda)
        lambda_a = max(0.05, away_lambda)
        matrix = np.zeros((self.MAX_GOALS + 1, self.MAX_GOALS + 1))

        for h in range(self.MAX_GOALS + 1):
            for a in range(self.MAX_GOALS + 1):
                matrix[h, a] = (
                    self._tau(h, a, lambda_h, lambda_a, rho)
                    * poisson.pmf(h, lambda_h)
                    * poisson.pmf(a, lambda_a)
                )

        total = matrix.sum()
        if total <= 0:
            return matrix
        return matrix / total

    def _resolve_rho(self, league_id: int | None, rho: float | None) -> float:
        if rho is not None:
            return rho
        if league_id is not None and league_id in self.rho_by_league:
            return self.rho_by_league[league_id]
        return self.default_rho

    def lambdas_from_features(self, features: dict) -> tuple[float, float] | None:
        """λ iz venue_adjusted_xG + injury korekcija (isto kao PoissonModel)."""
        home_xg = first_present(features, "home_venue_adjusted_xg", "home_weighted_xG_last5")
        away_xg = first_present(features, "away_venue_adjusted_xg", "away_weighted_xG_last5")
        if home_xg is None or away_xg is None:
            return None

        home_inj = numeric_feature(features, "home_injury_impact_score", default=0.0)
        away_inj = numeric_feature(features, "away_injury_impact_score", default=0.0)
        home_xg = home_xg * (1.0 - home_inj * 0.15)
        away_xg = away_xg * (1.0 - away_inj * 0.15)

        scale = numeric_feature(features, "dc_xg_scale", default=self.xg_scale)
        home_adv = numeric_feature(features, "dc_home_advantage", default=self.home_advantage)

        lambda_h = max(0.05, home_xg * scale * home_adv)
        lambda_a = max(0.05, away_xg * scale)
        return lambda_h, lambda_a

    def simulate(
        self,
        home_lambda: float,
        away_lambda: float,
        *,
        rho: float | None = None,
        league_id: int | None = None,
    ) -> DixonColesPrediction:
        rho_val = self._resolve_rho(league_id, rho)
        score_matrix = self.build_score_matrix(home_lambda, away_lambda, rho_val)

        probs = {
            "home_win": float(np.tril(score_matrix, -1).sum()),
            "draw": float(np.trace(score_matrix)),
            "away_win": float(np.triu(score_matrix, 1).sum()),
            "btts_yes": float(score_matrix[1:, 1:].sum()),
            "btts_no": float(score_matrix[0, :].sum() + score_matrix[1:, 0].sum()),
        }

        for threshold in (0.5, 1.5, 2.5, 3.5, 4.5):
            over = 0.0
            for h in range(self.MAX_GOALS + 1):
                for a in range(self.MAX_GOALS + 1):
                    if h + a > threshold:
                        over += score_matrix[h, a]
            probs[f"over_{threshold}"] = float(over)
            probs[f"under_{threshold}"] = float(1.0 - over)

        return DixonColesPrediction(
            home_goals_lambda=home_lambda,
            away_goals_lambda=away_lambda,
            rho=rho_val,
            score_matrix=score_matrix,
            probabilities=probs,
        )

    def predict(
        self,
        home_xg: float,
        away_xg: float,
        market: str,
        selection: str,
        line: float | None = None,
        *,
        league_id: int | None = None,
    ) -> float | None:
        pred = self.simulate(home_xg, away_xg, league_id=league_id)
        return self._extract_probability(pred, market, selection, line)

    def predict_from_features(
        self,
        features: dict,
        market: str,
        selection: str,
        line: float | None = None,
        *,
        league_id: int | None = None,
    ) -> float | None:
        lambdas = self.lambdas_from_features(features)
        if lambdas is None:
            return None
        lambda_h, lambda_a = lambdas
        league = league_id
        if league is None:
            raw = features.get("league_id")
            if raw is not None:
                try:
                    league = int(raw)
                except (TypeError, ValueError):
                    league = None
        pred = self.simulate(lambda_h, lambda_a, league_id=league)
        return self._extract_probability(pred, market, selection, line)

    def _extract_probability(
        self,
        pred: DixonColesPrediction,
        market: str,
        selection: str,
        line: float | None = None,
    ) -> float | None:
        sel = normalize_selection(selection).replace(" ", "_")

        if market == "match_winner":
            mapping = {
                "home": "home_win",
                "1": "home_win",
                "away": "away_win",
                "2": "away_win",
                "draw": "draw",
                "x": "draw",
            }
            key = mapping.get(sel)
            if key is None or key not in pred.probabilities:
                return None
            return pred.probabilities[key]

        if market == "btts":
            key = "btts_yes" if "yes" in sel else "btts_no"
            return pred.probabilities.get(key)

        if market == "over_under":
            threshold = line if line is not None else 2.5
            key = f"over_{threshold}" if "over" in sel else f"under_{threshold}"
            return pred.probabilities.get(key)

        if market == "double_chance":
            hw = pred.probabilities.get("home_win")
            dr = pred.probabilities.get("draw")
            aw = pred.probabilities.get("away_win")
            if hw is None or dr is None or aw is None:
                return None
            if "1x" in sel or "home_draw" in sel:
                return hw + dr
            if "x2" in sel or "draw_away" in sel:
                return dr + aw
            if "12" in sel or "home_away" in sel:
                return hw + aw
            return None

        if market == "asian_handicap":
            hw = pred.probabilities.get("home_win")
            dr = pred.probabilities.get("draw")
            aw = pred.probabilities.get("away_win")
            if hw is None or dr is None or aw is None:
                return None
            handicap = line if line is not None else 0.0
            if handicap < 0:
                return hw * 0.7 + dr * 0.3
            return aw * 0.7 + dr * 0.3

        return None
