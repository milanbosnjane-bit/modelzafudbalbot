"""Model A: Poisson/xG simulation for goal-based markets."""

from dataclasses import dataclass

import numpy as np
from scipy.stats import poisson

from app.utils.feature_values import first_present, numeric_feature
from app.utils.helpers import normalize_selection


@dataclass
class PoissonPrediction:
    home_goals_lambda: float
    away_goals_lambda: float
    probabilities: dict[str, float]


class PoissonModel:
    """Simulates match outcomes using Poisson distribution on xG lambdas."""

    MAX_GOALS = 10

    def predict(
        self,
        home_xg: float,
        away_xg: float,
        market: str,
        selection: str,
        line: float | None = None,
    ) -> float | None:
        pred = self.simulate(home_xg, away_xg)
        return self._extract_probability(pred, market, selection, line)

    def simulate(self, home_xg: float, away_xg: float) -> PoissonPrediction:
        home_xg = max(0.1, home_xg)
        away_xg = max(0.1, away_xg)

        score_matrix = np.zeros((self.MAX_GOALS + 1, self.MAX_GOALS + 1))
        for h in range(self.MAX_GOALS + 1):
            for a in range(self.MAX_GOALS + 1):
                score_matrix[h, a] = poisson.pmf(h, home_xg) * poisson.pmf(a, away_xg)

        probs = {
            "home_win": float(np.tril(score_matrix, -1).sum()),
            "draw": float(np.trace(score_matrix)),
            "away_win": float(np.triu(score_matrix, 1).sum()),
            "btts_yes": float(score_matrix[1:, 1:].sum()),
            "btts_no": float(score_matrix[0, :].sum() + score_matrix[1:, 0].sum()),
        }

        for threshold in [0.5, 1.5, 2.5, 3.5, 4.5]:
            over = 0.0
            for h in range(self.MAX_GOALS + 1):
                for a in range(self.MAX_GOALS + 1):
                    if h + a > threshold:
                        over += score_matrix[h, a]
            probs[f"over_{threshold}"] = float(over)
            probs[f"under_{threshold}"] = float(1.0 - over)

        return PoissonPrediction(
            home_goals_lambda=home_xg,
            away_goals_lambda=away_xg,
            probabilities=probs,
        )

    def _extract_probability(
        self,
        pred: PoissonPrediction,
        market: str,
        selection: str,
        line: float | None = None,
    ) -> float | None:
        sel = normalize_selection(selection).replace(" ", "_")

        if market == "match_winner":
            mapping = {
                "home": "home_win", "1": "home_win",
                "away": "away_win", "2": "away_win",
                "draw": "draw", "x": "draw",
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

    def predict_from_features(
        self,
        features: dict,
        market: str,
        selection: str,
        line: float | None = None,
    ) -> float | None:
        home_xg = first_present(features, "home_venue_adjusted_xg", "home_weighted_xG_last5")
        away_xg = first_present(features, "away_venue_adjusted_xg", "away_weighted_xG_last5")
        if home_xg is None or away_xg is None:
            return None
        home_inj = numeric_feature(features, "home_injury_impact_score", default=0.0)
        away_inj = numeric_feature(features, "away_injury_impact_score", default=0.0)
        home_xg = home_xg * (1.0 - home_inj * 0.15)
        away_xg = away_xg * (1.0 - away_inj * 0.15)
        return self.predict(home_xg, away_xg, market, selection, line=line)
