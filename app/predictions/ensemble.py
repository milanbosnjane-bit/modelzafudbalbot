"""Probability engine — Dixon-Coles model + calibrated EV."""

from dataclasses import dataclass, field

import structlog

from app.config import get_settings
from app.models.dixon_coles_model import DixonColesModel
from app.predictions.probability_layer import (
    compute_ev,
    is_disabled_market,
    is_legacy_clamped_ev,
    is_valid_probability,
)
from app.utils.feature_values import first_present, numeric_feature
from app.training.targets import expected_return_from_probability
from app.utils.helpers import pick_rank_score, normalize_selection
from app.utils.odds import shrink_probability

logger = structlog.get_logger()
settings = get_settings()


@dataclass
class ModelOutputs:
    model_prob: float | None
    # Legacy aliases for audit / DB compatibility
    poisson_prob: float | None = None
    lightgbm_prob: float | None = None
    xgboost_prob: float | None = None
    neural_prob: float | None = None


@dataclass
class EnsembleResult:
    expected_return: float
    probability: float
    calibrated_probability: float
    confidence: float
    agreement: float
    model_outputs: ModelOutputs
    expected_value: float
    fair_implied_prob: float | None
    bookmaker_odds: float
    pick_rank_score: float
    rejected: bool = False
    rejection_reason: str | None = None
    reasoning: list[str] = field(default_factory=list)

    @property
    def implied_prob(self) -> float | None:
        return self.fair_implied_prob

    @property
    def roi_score(self) -> float:
        return self.pick_rank_score


class ProbabilityEngine:
    """Dixon-Coles score model → shrink → EV. Jedini prediktivni engine u produkciji."""

    def __init__(self):
        self.score_model = DixonColesModel.from_params_file()
        self._models_loaded = False
        self.loaded_models: list[str] = []
        self._simulation_cache: dict[tuple, object] = {}

    def warmup(self) -> list[str]:
        if self._models_loaded:
            return self.loaded_models

        params_path = settings.model_dir / settings.dc_params_file
        self.score_model = DixonColesModel.from_params_file(params_path)
        self._simulation_cache.clear()
        self.loaded_models = ["dixon_coles"]
        self._models_loaded = True
        logger.info("probability_engine_ready", models=self.loaded_models, params=str(params_path))
        return self.loaded_models

    def _confidence(self, model_prob: float, fair_implied: float, odds: float) -> float:
        """
        Skor 0–1 baziran na edge-u (model vs fair), ne na 'sirovoj verovatnoći pobede'.
        Blaga penalizacija za ekstremna odstupanja model↔tržište.
        """
        edge = max(0.0, model_prob - fair_implied)
        edge_scale = 0.12 if odds < 3.0 else 0.08
        edge_component = min(1.0, edge / edge_scale)

        divergence = abs(model_prob - fair_implied)
        calibration_penalty = max(0.35, 1.0 - divergence * 0.5)

        raw = 0.55 * edge_component + 0.45 * calibration_penalty
        return max(0.35, min(0.95, raw))

    def _model_prob_for_features(
        self,
        features: dict,
        market: str,
        selection: str,
        line: float | None,
    ) -> float | None:
        home_xg = first_present(features, "home_venue_adjusted_xg", "home_weighted_xG_last5")
        away_xg = first_present(features, "away_venue_adjusted_xg", "away_weighted_xG_last5")
        if home_xg is None or away_xg is None:
            return None
        if home_xg < settings.min_team_xg_threshold or away_xg < settings.min_team_xg_threshold:
            return None

        league_id: int | None = None
        raw_league = features.get("league_id")
        if raw_league is not None:
            try:
                league_id = int(raw_league)
            except (TypeError, ValueError):
                league_id = None

        home_inj = numeric_feature(features, "home_injury_impact_score", default=0.0)
        away_inj = numeric_feature(features, "away_injury_impact_score", default=0.0)
        lambdas = self.score_model.lambdas_from_features(features)
        if lambdas is None:
            return None
        lambda_h, lambda_a = lambdas

        cache_key = (
            round(lambda_h, 3),
            round(lambda_a, 3),
            league_id,
            round(home_inj, 3),
            round(away_inj, 3),
        )
        if cache_key not in self._simulation_cache:
            self._simulation_cache[cache_key] = self.score_model.simulate(
                lambda_h, lambda_a, league_id=league_id
            )
        pred = self._simulation_cache[cache_key]
        return self.score_model._extract_probability(pred, market, selection, line=line)

    def _fair_implied_for_selection(
        self, features: dict, market: str, selection: str
    ) -> float | None:
        sel = normalize_selection(selection)
        key_map = {
            ("match_winner", ("home", "1")): "fair_prob_home",
            ("match_winner", ("away", "2")): "fair_prob_away",
            ("match_winner", ("draw", "x")): "fair_prob_draw",
        }
        for (m, sels), key in key_map.items():
            if market == m and sel in sels:
                val = features.get(key)
                return float(val) if val is not None else None
        if market == "btts" and "yes" in sel:
            val = features.get("fair_prob_btts_yes")
            return float(val) if val is not None else None
        if market == "over_under" and "over" in sel:
            val = features.get("fair_prob_over_2_5")
            return float(val) if val is not None else None
        return None

    def predict(
        self,
        features: dict,
        market: str,
        selection: str,
        bookmaker_odds: float,
        fair_implied_prob: float | None = None,
        line: float | None = None,
        ev_threshold: float | None = None,
        confidence_threshold: float | None = None,
    ) -> EnsembleResult:
        if is_disabled_market(market):
            return self._rejected("invalid_market", bookmaker_odds)

        if bookmaker_odds <= 1.0:
            return self._rejected("invalid_odds", bookmaker_odds)

        model_prob = self._model_prob_for_features(features, market, selection, line=line)
        if model_prob is None or not is_valid_probability(model_prob):
            return self._rejected("missing_probability", bookmaker_odds)

        fair_implied = fair_implied_prob
        if fair_implied is None:
            fair_implied = self._fair_implied_for_selection(features, market, selection)
        if fair_implied is None or not (0.0 < fair_implied < 1.0):
            return self._rejected("missing_fair_implied", bookmaker_odds)

        calibrated = shrink_probability(
            model_prob, fair_implied, weight=settings.probability_shrink_weight
        )
        if not is_valid_probability(calibrated):
            return self._rejected("probability_out_of_bounds", bookmaker_odds)

        ev = compute_ev(calibrated, bookmaker_odds)
        if ev is None or is_legacy_clamped_ev(ev):
            return self._rejected("fallback_ev_used", bookmaker_odds)

        confidence = self._confidence(model_prob, fair_implied, bookmaker_odds)
        agreement = 1.0
        ensemble_return = expected_return_from_probability(calibrated, bookmaker_odds)

        rejected = False
        rejection_reason = None
        min_ev = ev_threshold if ev_threshold is not None else settings.min_ev_threshold
        min_conf = (
            confidence_threshold
            if confidence_threshold is not None
            else settings.min_confidence_threshold
        )
        if ev < min_ev:
            rejected = True
            rejection_reason = f"EV {ev:.1%} below threshold {min_ev:.1%}"
        if confidence_threshold is not None and confidence < min_conf:
            rejected = True
            rejection_reason = f"Confidence {confidence:.0%} below {min_conf:.0%}"

        reasoning = self._build_reasoning(
            features, market, selection, model_prob, fair_implied, ev
        )
        score = pick_rank_score(ev, confidence, agreement)

        outputs = ModelOutputs(model_prob=model_prob, poisson_prob=model_prob)

        return EnsembleResult(
            expected_return=ensemble_return,
            probability=model_prob,
            calibrated_probability=calibrated,
            confidence=confidence,
            agreement=agreement,
            model_outputs=outputs,
            expected_value=ev,
            fair_implied_prob=fair_implied,
            bookmaker_odds=bookmaker_odds,
            pick_rank_score=score,
            rejected=rejected,
            rejection_reason=rejection_reason,
            reasoning=reasoning,
        )

    def _rejected(
        self,
        reason: str,
        odds: float,
        outputs: ModelOutputs | None = None,
    ) -> EnsembleResult:
        return EnsembleResult(
            expected_return=0.0,
            probability=0.0,
            calibrated_probability=0.0,
            confidence=0.0,
            agreement=0.0,
            model_outputs=outputs or ModelOutputs(model_prob=None),
            expected_value=0.0,
            fair_implied_prob=None,
            bookmaker_odds=odds,
            pick_rank_score=0.0,
            rejected=True,
            rejection_reason=reason,
        )

    def _build_reasoning(
        self,
        features: dict,
        market: str,
        selection: str,
        model_prob: float,
        fair_implied: float,
        ev: float,
    ) -> list[str]:
        reasons: list[str] = []
        home_xg = first_present(features, "home_weighted_xG_last5", "home_venue_adjusted_xg")
        away_xg = first_present(features, "away_weighted_xG_last5", "away_venue_adjusted_xg")
        if home_xg is not None and away_xg is not None:
            reasons.append(f"Dixon-Coles λ: domaćin {home_xg:.2f} — gost {away_xg:.2f}")
        reasons.append(
            f"DC procena {model_prob:.0%} vs fair {fair_implied:.0%}"
        )
        edge_pct = (model_prob - fair_implied) * 100
        if edge_pct > 0:
            reasons.append(f"Edge vs fair: +{edge_pct:.1f} pp")
        if ev > 0:
            reasons.append(f"Očekivani povrat: +{ev:.1%} po jedinici")
        sel = normalize_selection(selection)
        if market == "match_winner" and sel in ("draw", "x"):
            reasons.append("Fokus: nerešen ishod (DC ρ korekcija)")
        if market == "over_under" and "under" in sel:
            reasons.append("Fokus: nizak očekivani broj golova")
        return reasons[:5]


# Backward-compatible alias — pipeline i pick_selector koriste ovaj naziv
EnsemblePredictor = ProbabilityEngine

# Uklonjen ML hybrid; zadržavamo izuzetak za legacy importe u testovima
class InsufficientModelsError(RuntimeError):
    """Deprecated — ML ensemble više nije podržan."""
