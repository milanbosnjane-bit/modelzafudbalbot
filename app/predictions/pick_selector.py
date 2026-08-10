"""Pick selection — up to 6 picks; hard fallback ensures picks when pool is non-empty."""

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import func, or_, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import DailyPick, Fixture, OddsSnapshot, Prediction, Team
from app.predictions.ensemble import EnsemblePredictor, EnsembleResult
from app.utils.feature_values import first_present, has_usable_match_xg
from app.utils.legacy_data import LEGACY_BOOKMAKERS
from app.predictions.market_selection import (
    is_eligible_selection,
    passes_prediction_type_filter,
)
from app.predictions.probability_layer import (
    compute_ev,
    is_disabled_market,
    is_legacy_clamped_ev,
    is_supported_market,
    is_valid_probability,
)
from app.predictions.context_gates import ContextGateInput, passes_context_gates
from app.predictions.regime import MarketRegime, RegimeDetector, REGIME_WEIGHTS
from app.utils.edge import compute_edge_metrics
from app.utils.helpers import capped_stake, decision_time, utc_now
from app.utils.odds import median_odds, fair_probs_from_selection_odds

logger = structlog.get_logger()
settings = get_settings()

DROP_REASONS = frozenset({
    "missing_probability",
    "fallback_ev_used",
    "invalid_market",
    "missing_confidence",
    "missing_fair_implied",
    "probability_out_of_bounds",
    "invalid_odds",
})


@dataclass
class PickCandidate:
    fixture_id: int
    home_team: str
    away_team: str
    fixture_date: datetime
    market: str
    selection: str
    odds: float
    opening_odds: float | None
    fair_implied_prob: float
    line: float | None
    market_regime: str
    ensemble: EnsembleResult
    final_score: float = 0.0
    # home_xG + away_xG for BTTS total-xG guardrail (optional)
    total_xg: float | None = None


def confidence_weight(confidence: float) -> float:
    return 0.8 + confidence * 0.4


def regime_weight(regime: str | MarketRegime) -> float:
    if isinstance(regime, str):
        try:
            regime = MarketRegime(regime)
        except ValueError:
            return REGIME_WEIGHTS[MarketRegime.MODERATE]
    return REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS[MarketRegime.MODERATE])


def compute_final_score(ev: float, confidence: float, regime: str | MarketRegime) -> float:
    return ev * confidence_weight(confidence) * regime_weight(regime)


def ev_distribution_stats(ev_values: list[float]) -> tuple[float, float, float]:
    """Return (median, p25, p75) for a pool of EV values."""
    if not ev_values:
        return 0.0, 0.0, 0.0
    ordered = sorted(ev_values)
    median_ev = statistics.median(ordered)
    if len(ordered) == 1:
        return median_ev, median_ev, median_ev
    p25 = statistics.quantiles(ordered, n=4)[0]
    p75 = statistics.quantiles(ordered, n=4)[2]
    return median_ev, p25, p75


def compute_dynamic_ev_threshold(ev_values: list[float], floor: float = 0.0) -> float:
    """Dynamic EV cutoff: max(floor, median of candidate pool)."""
    if not ev_values:
        return floor
    median_ev, _, _ = ev_distribution_stats(ev_values)
    return max(floor, median_ev)


def filter_by_ev(candidates: list["PickCandidate"], threshold: float) -> list["PickCandidate"]:
    return [c for c in candidates if c.ensemble.expected_value >= threshold]


def rank_candidates(candidates: list["PickCandidate"]) -> list["PickCandidate"]:
    return sorted(
        candidates,
        key=lambda c: (
            c.final_score or c.ensemble.pick_rank_score,
            c.ensemble.expected_value,
        ),
        reverse=True,
    )


MAX_PICKS_PER_MARKET = 2
MAX_DRAW_PICKS = 2
DRAW_SELECTIONS = frozenset({"draw", "x"})


def is_draw_pick(candidate: "PickCandidate") -> bool:
    return (
        candidate.market == "match_winner"
        and candidate.selection.lower().strip() in DRAW_SELECTIONS
    )


def apply_diversity_rules(
    candidates: list["PickCandidate"],
    max_picks: int,
) -> list["PickCandidate"]:
    """
    Diversity: max 1 pick per match; max 2 Draw tips total; ranked by EV then score.
    """
    if not candidates:
        return []

    result: list[PickCandidate] = []
    match_counter: dict[int, int] = {}
    draw_count = 0

    for c in sorted(
        candidates,
        key=lambda x: (
            x.ensemble.expected_value,
            x.final_score or x.ensemble.pick_rank_score,
        ),
        reverse=True,
    ):
        match_id = c.fixture_id

        if match_counter.get(match_id, 0) >= 1:
            continue

        if is_draw_pick(c) and draw_count >= MAX_DRAW_PICKS:
            continue

        result.append(c)
        match_counter[match_id] = 1
        if is_draw_pick(c):
            draw_count += 1

        if len(result) >= max_picks:
            break

    return result


def top_k_candidates(
    pool: list["PickCandidate"],
    max_picks: int,
) -> list["PickCandidate"]:
    """Best max_picks from pool by EV/score with diversity rules."""
    if not pool:
        return []

    scored: list[PickCandidate] = []
    for candidate in pool:
        candidate.final_score = compute_final_score(
            candidate.ensemble.expected_value,
            candidate.ensemble.confidence,
            candidate.market_regime,
        )
        scored.append(candidate)

    return apply_diversity_rules(scored, max_picks)


def select_candidates(
    pool: list["PickCandidate"],
    *,
    max_picks: int,
    ev_floor: float = 0.0,
) -> tuple[list["PickCandidate"], dict]:
    """
    Four-step ladder — never block the run with an empty pick list when pool is non-empty.

    1. EV >= ev_floor (default 0.0) + diversity
    2. if >= max_picks → return
    3. EV >= 0.0 + diversity
    4. hard fallback: full pool + diversity (best available by score)
    """
    empty_meta = {
        "step": "empty",
        "ev_floor": ev_floor,
        "used_fallback": False,
        "relaxed_ev": False,
    }
    if not pool:
        return [], empty_meta

    # Korak 1: EV >= ev_floor (podrazumevano 0.0) + raznovrsnost
    positive_pool = filter_by_ev(pool, ev_floor)
    step1 = apply_diversity_rules(positive_pool, max_picks)
    if step1:
        return step1, {
            "step": "normal_ev",
            "ev_floor": ev_floor,
            "used_fallback": False,
            "relaxed_ev": False,
            "count": len(step1),
        }

    # Nema pikova sa pozitivnim EV — vraćamo praznu listu, ne forsiramo loše tikete.
    return [], {
        "step": "no_positive_ev",
        "ev_floor": ev_floor,
        "used_fallback": False,
        "relaxed_ev": False,
        "count": 0,
    }


def apply_ev_selection(
    candidates: list["PickCandidate"],
    *,
    max_picks: int,
    floor: float = 0.0,
) -> tuple[list["PickCandidate"], dict]:
    """
    Filter by dynamic EV threshold; if none pass, take top max_picks by EV rank.
    """
    if not candidates:
        return [], {
            "median_ev": 0.0,
            "p25_ev": 0.0,
            "p75_ev": 0.0,
            "dynamic_threshold": floor,
            "used_fallback": False,
        }

    ev_values = [c.ensemble.expected_value for c in candidates]
    median_ev, p25_ev, p75_ev = ev_distribution_stats(ev_values)
    dynamic_threshold = compute_dynamic_ev_threshold(ev_values, floor=floor)

    threshold_pool = [
        c for c in candidates if c.ensemble.expected_value >= dynamic_threshold
    ]
    used_fallback = False
    if threshold_pool:
        selected = threshold_pool
    else:
        used_fallback = True
        selected = sorted(
            candidates,
            key=lambda c: c.ensemble.expected_value,
            reverse=True,
        )[:max_picks]

    meta = {
        "median_ev": median_ev,
        "p25_ev": p25_ev,
        "p75_ev": p75_ev,
        "dynamic_threshold": dynamic_threshold,
        "used_fallback": used_fallback,
    }
    return selected, meta


def apply_market_diversity_cap(
    candidates: list["PickCandidate"],
    max_picks: int,
    max_per_market: int = MAX_PICKS_PER_MARKET,
    max_per_fixture: int = 1,
) -> list["PickCandidate"]:
    """Keep at most max_per_market picks per market and max_per_fixture per match."""
    sorted_pool = sorted(
        candidates,
        key=lambda c: (
            c.ensemble.expected_value,
            c.final_score or c.ensemble.pick_rank_score,
        ),
        reverse=True,
    )
    market_counts: dict[str, int] = {}
    fixture_counts: dict[int, int] = {}
    filtered: list[PickCandidate] = []
    for candidate in sorted_pool:
        market = candidate.market
        fixture_id = candidate.fixture_id
        if market_counts.get(market, 0) >= max_per_market:
            continue
        if fixture_counts.get(fixture_id, 0) >= max_per_fixture:
            continue
        filtered.append(candidate)
        market_counts[market] = market_counts.get(market, 0) + 1
        fixture_counts[fixture_id] = fixture_counts.get(fixture_id, 0) + 1
        if len(filtered) >= max_picks:
            break
    return filtered


def map_drop_reason(rejection_reason: str | None, market: str) -> str:
    if is_disabled_market(market):
        return "invalid_market"
    mapping = {
        "missing_model_probability": "missing_probability",
        "missing_probability": "missing_probability",
        "invalid_market": "invalid_market",
        "exact_score_disabled": "invalid_market",
        "invalid_ev": "fallback_ev_used",
        "fallback_ev_used": "fallback_ev_used",
        "missing_confidence": "missing_confidence",
    }
    return mapping.get(rejection_reason or "", rejection_reason or "missing_probability")


def candidate_passes_validation(
    result: EnsembleResult,
    market: str,
    decimal_odds: float,
) -> tuple[bool, str | None]:
    if is_disabled_market(market):
        return False, "invalid_market"
    if result.rejection_reason:
        if result.rejection_reason.startswith("EV ") and "below threshold" in result.rejection_reason:
            pass
        else:
            return False, map_drop_reason(result.rejection_reason, market)
    if not is_valid_probability(result.calibrated_probability):
        return False, "missing_probability"
    if result.confidence is None or result.confidence <= 0.0:
        return False, "missing_confidence"
    if math.isnan(result.confidence):
        return False, "missing_confidence"
    recomputed_ev = compute_ev(result.calibrated_probability, decimal_odds)
    if recomputed_ev is None:
        return False, "fallback_ev_used"
    if is_legacy_clamped_ev(recomputed_ev) or is_legacy_clamped_ev(result.expected_value):
        return False, "fallback_ev_used"
    if abs(recomputed_ev - result.expected_value) > 1e-6:
        return False, "fallback_ev_used"
    return True, None


@dataclass
class SelectedPick:
    fixture_id: int
    match_label: str
    market: str
    selection: str
    odds: float
    opening_odds: float | None
    fair_implied_prob: float
    line: float | None
    expected_return: float
    probability: float
    expected_value: float
    confidence: float
    pick_rank_score: float
    stake_units: float
    stake_method: str
    market_regime: str
    reasoning: list[str]
    rank: int
    fixture_date: datetime | None = None
    status: str = "PENDING"
    pick_id: int | None = None
    calibrated_confidence: float | None = None
    calibrated_ev: float | None = None

    @property
    def roi_score(self) -> float:
        return self.pick_rank_score


MIN_PICK_STAKE_UNITS = 1.0

# Absolute floor — never tip below 2.00 on any market.
GLOBAL_MIN_ODDS = 2.0
# Hard EV ceiling — cuts longshot mirages.
MAX_EV = float(settings.max_ev_threshold)
# Default cap for Home / Away / BTTS Yes.
DEFAULT_MAX_ODDS = 4.50
# Tighter Draw (X) cap — eliminate longshot remiji.
DRAW_MAX_ODDS = 3.60

# Draw (X) — odds 2.00–3.60, EV/edge 3.0%
DRAW_RULES = {
    "min_ev": 0.030,
    "min_edge_pp": 3.0,
    "max_odds": DRAW_MAX_ODDS,
}

# BTTS Yes — odds 2.00–4.50, EV/edge 1.5%, require combined xG >= 2.20
BTTS_MIN_TOTAL_XG = 2.20
BTTS_YES_RULES = {
    "min_ev": 0.015,
    "min_edge_pp": 1.5,
    "max_odds": DEFAULT_MAX_ODDS,
    "min_total_xg": BTTS_MIN_TOTAL_XG,
}

# Under 2.5 (paused from tips, still ingestable)
UNDER_RULES = {
    "min_ev": 0.015,
    "min_edge_pp": 1.5,
    "max_odds": DEFAULT_MAX_ODDS,
}

# Home/Away — odds 2.00–4.50, EV/edge 1.0%
SELECTION_QUALITY_FILTERS: dict[tuple[str, str], dict] = {
    ("match_winner", "home"): {
        "min_ev": 0.010,
        "min_edge_pp": 1.0,
        "max_odds": DEFAULT_MAX_ODDS,
    },
    ("match_winner", "away"): {
        "min_ev": 0.010,
        "min_edge_pp": 1.0,
        "max_odds": DEFAULT_MAX_ODDS,
    },
}


def _edge_pp(model_prob: float, fair_implied: float) -> float:
    return (model_prob - fair_implied) * 100.0


def _apply_flat_rules(
    *,
    odds: float,
    ev: float,
    edge: float,
    min_ev: float,
    min_edge_pp: float,
    max_odds: float,
) -> tuple[bool, str | None]:
    if odds > max_odds:
        return False, f"selection_odds_too_high ({odds:.2f} > {max_odds})"
    if ev < min_ev:
        return False, f"selection_ev_too_low ({ev:.3f} < {min_ev})"
    if edge < min_edge_pp:
        return False, f"selection_edge_too_low ({edge:.1f}pp < {min_edge_pp}pp)"
    return True, None


def dynamic_quality_rule(candidate: "PickCandidate") -> tuple[bool, str | None]:
    """Strict min odds 2.00, MAX_EV, and per-selection EV/edge/odds caps."""
    if not passes_prediction_type_filter(
        candidate.market, candidate.selection, candidate.line
    ):
        return False, "btts_no_blocked"

    odds = candidate.odds
    if odds < GLOBAL_MIN_ODDS:
        return False, f"odds_below_floor ({odds:.2f} < {GLOBAL_MIN_ODDS})"

    ev = candidate.ensemble.expected_value
    if ev > MAX_EV:
        return False, f"selection_ev_too_high ({ev:.3f} > {MAX_EV})"

    fair = candidate.fair_implied_prob
    if fair is None or not (0.0 < fair < 1.0):
        return False, "missing_fair_implied"
    model_prob = candidate.ensemble.calibrated_probability
    edge = _edge_pp(model_prob, fair)
    sel = candidate.selection.lower().strip()

    if candidate.market == "match_winner" and sel in DRAW_SELECTIONS:
        return _apply_flat_rules(
            odds=odds,
            ev=ev,
            edge=edge,
            min_ev=DRAW_RULES["min_ev"],
            min_edge_pp=DRAW_RULES["min_edge_pp"],
            max_odds=DRAW_RULES["max_odds"],
        )

    if candidate.market == "btts" and sel in {"yes", "btts yes"}:
        ok, reason = _apply_flat_rules(
            odds=odds,
            ev=ev,
            edge=edge,
            min_ev=BTTS_YES_RULES["min_ev"],
            min_edge_pp=BTTS_YES_RULES["min_edge_pp"],
            max_odds=BTTS_YES_RULES["max_odds"],
        )
        if not ok:
            return ok, reason
        min_xg = float(BTTS_YES_RULES["min_total_xg"])
        total_xg = candidate.total_xg
        if total_xg is None or total_xg < min_xg:
            shown = "None" if total_xg is None else f"{total_xg:.2f}"
            return False, f"btts_total_xg_too_low ({shown} < {min_xg:.2f})"
        return True, None

    if candidate.market == "over_under" and "under" in sel:
        return _apply_flat_rules(
            odds=odds,
            ev=ev,
            edge=edge,
            min_ev=UNDER_RULES["min_ev"],
            min_edge_pp=UNDER_RULES["min_edge_pp"],
            max_odds=UNDER_RULES["max_odds"],
        )

    key = (candidate.market, sel)
    rules = SELECTION_QUALITY_FILTERS.get(key)
    if rules is None:
        # Unknown selection — still enforce global floor/cap/EV already checked.
        if odds > DEFAULT_MAX_ODDS:
            return False, f"selection_odds_too_high ({odds:.2f} > {DEFAULT_MAX_ODDS})"
        return True, None

    return _apply_flat_rules(
        odds=odds,
        ev=ev,
        edge=edge,
        min_ev=rules["min_ev"],
        min_edge_pp=rules["min_edge_pp"],
        max_odds=rules["max_odds"],
    )


def passes_selection_filter(candidate: "PickCandidate") -> tuple[bool, str | None]:
    """Alias — poziva dynamic_quality_rule (kompatibilnost sa pipeline-om)."""
    return dynamic_quality_rule(candidate)


class PickSelectionEngine:
    """
    Returns UP TO max_daily_picks (6) — never pads with sub-threshold bets.
    Uses median available odds at decision_time (not max = unrealistic line shopping).
    """

    MIN_LIQUIDITY_BOOKMAKERS = 2
    # Core markets for live tip selection. over_under paused 2026-08 after
    # −22% ROI on settled sample; odds for OU may still be ingested via
    # supported_markets for features, but PICK_MARKETS blocks tip generation.
    PICK_MARKETS = frozenset({"match_winner", "btts"})
    REJECTED_REASONS = frozenset({
        "invalid_market",
        "invalid_odds",
        "missing_probability",
        "missing_model_probability",
        "missing_confidence",
        "missing_fair_implied",
        "probability_out_of_bounds",
        "invalid_ev",
        "fallback_ev_used",
        "exact_score_disabled",
    })

    def __init__(self, session: AsyncSession, exclude_legacy_bookmakers: bool = False):
        self.session = session
        self.exclude_legacy_bookmakers = exclude_legacy_bookmakers
        self.ensemble = EnsemblePredictor()
        self.ensemble.warmup()
        self.regime_detector = RegimeDetector()
        self._history_backfill_attempted: set[int] = set()

    async def _try_backfill_fixture_history(
        self,
        fixture_id: int,
        as_of: datetime,
        features_map: dict[int, dict],
    ) -> dict:
        """Pre insufficient_xg odbacivanja — povuci istoriju oba tima i rebuild feature-a."""
        if fixture_id in self._history_backfill_attempted:
            return features_map.get(fixture_id, {})
        self._history_backfill_attempted.add(fixture_id)

        fixture = await self.session.get(Fixture, fixture_id)
        if not fixture:
            return features_map.get(fixture_id, {})

        from app.features.engineer import FeatureEngineer
        from app.services.ingestion import DataIngestionService

        ingestion = DataIngestionService(self.session)
        for team_id in (fixture.home_team_id, fixture.away_team_id):
            team_result = await ingestion.ingest_team_recent_history(team_id)
            logger.info(
                "on_demand_team_history",
                fixture_id=fixture_id,
                team_id=team_id,
                **team_result,
            )

        engineer = FeatureEngineer(self.session, historical_mode=False)
        features = await engineer.build_features(fixture_id, as_of=as_of, persist=True)
        features_map[fixture_id] = features
        logger.info(
            "on_demand_features_rebuilt",
            fixture_id=fixture_id,
            home_xg=first_present(features, "home_venue_adjusted_xg", "home_weighted_xG_last5"),
            away_xg=first_present(features, "away_venue_adjusted_xg", "away_weighted_xG_last5"),
        )
        return features

    async def get_fixture_ids_picked_today(self, as_of: datetime | None = None) -> set[int]:
        """Fixture IDs that already have a daily pick for the UTC calendar day."""
        ref = as_of or utc_now()
        day_start = datetime.combine(ref.date(), datetime.min.time())
        day_end = day_start + timedelta(days=1)
        result = await self.session.execute(
            select(DailyPick.fixture_id).where(
                DailyPick.pick_date >= day_start,
                DailyPick.pick_date < day_end,
            ).distinct()
        )
        return set(result.scalars().all())

    def _log_dropped(
        self,
        dropped_reason: str,
        *,
        fixture_id: int,
        market: str,
        selection: str,
        detail: str | None = None,
    ) -> None:
        logger.debug(
            "dropped_candidate",
            dropped_reason=dropped_reason,
            fixture_id=fixture_id,
            market=market,
            selection=selection,
            detail=detail,
        )

    def _preflight_ok(
        self,
        market: str,
        features: dict,
        odds_info: dict,
    ) -> bool:
        if not features:
            return False
        if odds_info.get("fair_prob") is None:
            return False
        if market in ("match_winner", "over_under", "btts"):
            if not has_usable_match_xg(features, settings.min_team_xg_threshold):
                return False
        return True

    async def generate_candidates(
        self,
        fixture_ids: list[int],
        features_map: dict[int, dict],
        as_of_map: dict[int, datetime] | None = None,
    ) -> list[PickCandidate]:
        valid_candidates: list[PickCandidate] = []
        pool_candidates: list[PickCandidate] = []
        ev_list: list[float] = []
        conf_list: list[float] = []
        supported_markets = set(settings.supported_markets) & self.PICK_MARKETS
        drop_counts: dict[str, int] = {}
        max_picks = settings.max_daily_picks

        odds_map = await self._load_all_decision_odds(fixture_ids, as_of_map or {})

        fixture_result = await self.session.execute(
            select(Fixture).where(Fixture.id.in_(fixture_ids))
        )
        fixtures_by_id = {f.id: f for f in fixture_result.scalars().all()}

        team_ids = {
            tid
            for f in fixtures_by_id.values()
            for tid in (f.home_team_id, f.away_team_id)
        }
        team_result = await self.session.execute(
            select(Team).where(Team.id.in_(team_ids))
        )
        teams_by_id = {t.id: t for t in team_result.scalars().all()}

        excluded_leagues = set(settings.exclude_league_ids)

        for fixture_id in fixture_ids:
            fixture = fixtures_by_id.get(fixture_id)
            if not fixture:
                continue
            if fixture.league_id in excluded_leagues:
                continue

            as_of = (as_of_map or {}).get(fixture_id) or decision_time(
                fixture.fixture_date, settings.decision_hours_before_kickoff
            )

            home = teams_by_id.get(fixture.home_team_id)
            away = teams_by_id.get(fixture.away_team_id)
            features = features_map.get(fixture_id, {})

            if odds_map.get(fixture_id) and not has_usable_match_xg(
                features, settings.min_team_xg_threshold
            ):
                features = await self._try_backfill_fixture_history(
                    fixture_id, as_of, features_map
                )

            regime = self.regime_detector.detect(features, fixture.league_id, fixture_id)

            odds_by_market = odds_map.get(fixture_id, {})
            if not odds_by_market:
                continue

            for market, selections in odds_by_market.items():
                if not is_supported_market(market, supported_markets):
                    continue
                for selection, odds_info in selections.items():
                    line = odds_info.get("line")
                    if not passes_prediction_type_filter(market, selection, line):
                        drop_counts["btts_no_blocked"] = (
                            drop_counts.get("btts_no_blocked", 0) + 1
                        )
                        continue
                    if odds_info["bookmaker_count"] < self.MIN_LIQUIDITY_BOOKMAKERS:
                        continue
                    if not is_eligible_selection(market, selection, line, live=True):
                        continue
                    if not self._preflight_ok(market, features, odds_info):
                        home_xg, away_xg = (
                            first_present(features, "home_venue_adjusted_xg", "home_weighted_xG_last5"),
                            first_present(features, "away_venue_adjusted_xg", "away_weighted_xG_last5"),
                        )
                        if (
                            market in ("match_winner", "over_under", "btts")
                            and home_xg is not None
                            and away_xg is not None
                            and (
                                home_xg < settings.min_team_xg_threshold
                                or away_xg < settings.min_team_xg_threshold
                            )
                        ):
                            drop_counts["insufficient_xg"] = (
                                drop_counts.get("insufficient_xg", 0) + 1
                            )
                        else:
                            drop_counts["missing_probability"] = (
                                drop_counts.get("missing_probability", 0) + 1
                            )
                        continue

                    decimal_odds = odds_info["odds"]
                    result = self.ensemble.predict(
                        features=features,
                        market=market,
                        selection=selection,
                        bookmaker_odds=decimal_odds,
                        fair_implied_prob=odds_info.get("fair_prob"),
                        line=odds_info.get("line"),
                        ev_threshold=regime.ev_threshold,
                        confidence_threshold=None,
                    )

                    if (
                        decimal_odds > 1.0
                        and not is_disabled_market(market)
                        and is_valid_probability(result.calibrated_probability)
                    ):
                        pool_candidates.append(
                            PickCandidate(
                                fixture_id=fixture_id,
                                home_team=home.name if home else "Home",
                                away_team=away.name if away else "Away",
                                fixture_date=fixture.fixture_date,
                                market=market,
                                selection=selection,
                                odds=decimal_odds,
                                opening_odds=odds_info.get("opening_odds"),
                                fair_implied_prob=odds_info.get(
                                    "fair_prob", result.fair_implied_prob
                                ),
                                line=odds_info.get("line"),
                                market_regime=regime.regime.value,
                                ensemble=result,
                            )
                        )

                    if result.rejection_reason in self.REJECTED_REASONS:
                        reason = map_drop_reason(result.rejection_reason, market)
                        drop_counts[reason] = drop_counts.get(reason, 0) + 1
                        continue

                    ok, drop_reason = candidate_passes_validation(result, market, decimal_odds)
                    if not ok:
                        drop_counts[drop_reason or "missing_probability"] = (
                            drop_counts.get(drop_reason or "missing_probability", 0) + 1
                        )
                        continue

                    # Per-selekcijski filter (Home/Away/Draw/BTTS + xG guardrail).
                    home_xg = first_present(
                        features, "home_venue_adjusted_xg", "home_weighted_xG_last5"
                    )
                    away_xg = first_present(
                        features, "away_venue_adjusted_xg", "away_weighted_xG_last5"
                    )
                    total_xg = None
                    if home_xg is not None and away_xg is not None:
                        total_xg = float(home_xg) + float(away_xg)
                    _sel_tmp = PickCandidate(
                        fixture_id=fixture_id,
                        home_team="",
                        away_team="",
                        fixture_date=fixture.fixture_date,
                        market=market,
                        selection=selection,
                        odds=decimal_odds,
                        opening_odds=None,
                        fair_implied_prob=odds_info.get(
                            "fair_prob", result.fair_implied_prob
                        )
                        or result.fair_implied_prob,
                        line=odds_info.get("line"),
                        market_regime=regime.regime.value,
                        ensemble=result,
                        total_xg=total_xg,
                    )
                    sel_ok, sel_reason = passes_selection_filter(_sel_tmp)
                    if not sel_ok:
                        drop_counts["selection_quality_filter"] = (
                            drop_counts.get("selection_quality_filter", 0) + 1
                        )
                        self._log_dropped(
                            "selection_quality_filter",
                            fixture_id=fixture_id,
                            market=market,
                            selection=selection,
                            detail=sel_reason,
                        )
                        continue

                    gate_input = ContextGateInput(
                        market=market,
                        selection=selection,
                        fixture_date=fixture.fixture_date,
                    )
                    gate = passes_context_gates(
                        gate_input,
                        features,
                        opening_odds=odds_info.get("opening_odds"),
                        current_odds=decimal_odds,
                    )
                    if not gate.passed:
                        reason = gate.drop_reason or "context_gate"
                        drop_counts[reason] = drop_counts.get(reason, 0) + 1
                        self._log_dropped(
                            reason,
                            fixture_id=fixture_id,
                            market=market,
                            selection=selection,
                        )
                        continue

                    ev_list.append(result.expected_value)
                    conf_list.append(result.confidence)

                    candidate = PickCandidate(
                        fixture_id=fixture_id,
                        home_team=home.name if home else "Home",
                        away_team=away.name if away else "Away",
                        fixture_date=fixture.fixture_date,
                        market=market,
                        selection=selection,
                        odds=decimal_odds,
                        opening_odds=odds_info.get("opening_odds"),
                        fair_implied_prob=odds_info.get("fair_prob", result.fair_implied_prob),
                        line=odds_info.get("line"),
                        market_regime=regime.regime.value,
                        ensemble=result,
                    )
                    if gate.notes:
                        candidate.ensemble.reasoning = list(result.reasoning or [])
                        for note in gate.notes[:3]:
                            if note not in candidate.ensemble.reasoning:
                                candidate.ensemble.reasoning.append(note)
                    valid_candidates.append(candidate)

        median_ev, p25_ev, p75_ev = ev_distribution_stats(ev_list)
        logger.info(
            "EV_DISTRIBUTION",
            median_ev=median_ev,
            p25_ev=p25_ev,
            p75_ev=p75_ev,
            dynamic_threshold=compute_dynamic_ev_threshold(ev_list),
            pool_size=len(valid_candidates),
        )

        scored_candidates: list[PickCandidate] = []
        for candidate in valid_candidates:
            score = compute_final_score(
                candidate.ensemble.expected_value,
                candidate.ensemble.confidence,
                candidate.market_regime,
            )
            candidate.final_score = score
            scored_candidates.append(candidate)

        candidates, selection_meta = select_candidates(
            scored_candidates,
            max_picks=max_picks,
        )

        if selection_meta["step"] == "relaxed_ev":
            logger.info("EV_RELAXED_TO_ZERO", count=selection_meta["count"])
        elif selection_meta["step"] == "hard_fallback":
            logger.info(
                "EV_HARD_FALLBACK",
                count=selection_meta["count"],
                max_picks=max_picks,
            )

        logger.info(
            "candidate_count_before_regime",
            count=len(scored_candidates),
            selection_step=selection_meta["step"],
            used_fallback=selection_meta["used_fallback"],
            drop_summary=drop_counts,
        )

        if ev_list:
            logger.info(
                "EV_STATS",
                max_ev=max(ev_list),
                mean_ev=sum(ev_list) / len(ev_list),
                median_ev=median_ev,
                p25_ev=p25_ev,
                p75_ev=p75_ev,
                positive_ev_count=len([x for x in ev_list if x > 0.03]),
            )
            logger.info(
                "EV_BUCKETS",
                **{
                    "ev>0": len([x for x in ev_list if x > 0]),
                    "ev>0.01": len([x for x in ev_list if x > 0.01]),
                    "ev>0.05": len([x for x in ev_list if x > 0.05]),
                    "ev>0.1": len([x for x in ev_list if x > 0.1]),
                },
            )
        if conf_list:
            logger.info(
                "CONFIDENCE_STATS",
                max_conf=max(conf_list),
                mean_conf=sum(conf_list) / len(conf_list),
            )
        logger.info(
            "FILTER_BREAKDOWN",
            valid=len(valid_candidates),
            pool=len(pool_candidates),
            selection_step=selection_meta["step"],
            final=len(candidates),
            used_fallback=selection_meta["used_fallback"],
        )

        return candidates

    async def select_top_picks(
        self,
        candidates: list[PickCandidate],
        stake_method: str | None = None,
        max_picks: int | None = None,
    ) -> list[SelectedPick]:
        max_picks = max_picks if max_picks is not None else settings.max_daily_picks
        stake_method = stake_method or settings.default_stake_method

        sorted_candidates = sorted(
            candidates,
            key=lambda c: c.final_score or c.ensemble.pick_rank_score,
            reverse=True,
        )
        top = apply_diversity_rules(sorted_candidates, max_picks)
        picks = []

        for rank, candidate in enumerate(top, start=1):
            if is_disabled_market(candidate.market):
                logger.warning(
                    "dropped_candidate",
                    dropped_reason="invalid_market",
                    fixture_id=candidate.fixture_id,
                    market=candidate.market,
                    selection=candidate.selection,
                    detail="blocked_at_pick_output",
                )
                continue
            ok, drop_reason = candidate_passes_validation(
                candidate.ensemble, candidate.market, candidate.odds
            )
            if not ok:
                logger.warning(
                    "dropped_candidate",
                    dropped_reason=drop_reason,
                    fixture_id=candidate.fixture_id,
                    market=candidate.market,
                    selection=candidate.selection,
                    detail="blocked_at_pick_output",
                )
                continue
            stake = capped_stake(
                candidate.ensemble.calibrated_probability,
                candidate.odds,
                stake_method,
            )
            stake = max(stake, MIN_PICK_STAKE_UNITS)
            rank_score = candidate.final_score or candidate.ensemble.pick_rank_score
            picks.append(
                SelectedPick(
                    fixture_id=candidate.fixture_id,
                    match_label=f"{candidate.home_team} vs {candidate.away_team}",
                    market=candidate.market,
                    selection=candidate.selection,
                    odds=candidate.odds,
                    opening_odds=candidate.opening_odds,
                    fair_implied_prob=candidate.fair_implied_prob,
                    line=candidate.line,
                    expected_return=candidate.ensemble.expected_return,
                    probability=candidate.ensemble.calibrated_probability,
                    expected_value=candidate.ensemble.expected_value,
                    confidence=candidate.ensemble.confidence,
                    pick_rank_score=rank_score,
                    stake_units=round(stake, 2),
                    stake_method=stake_method,
                    market_regime=candidate.market_regime,
                    reasoning=candidate.ensemble.reasoning,
                    rank=len(picks) + 1,
                    fixture_date=candidate.fixture_date,
                )
            )

        if not picks and top:
            logger.info(
                "select_top_picks_validation_fallback",
                candidates=len(top),
            )
            for candidate in top:
                if is_disabled_market(candidate.market):
                    continue
                stake = capped_stake(
                    candidate.ensemble.calibrated_probability,
                    candidate.odds,
                    stake_method,
                )
                stake = max(stake, MIN_PICK_STAKE_UNITS)
                rank_score = candidate.final_score or candidate.ensemble.pick_rank_score
                picks.append(
                    SelectedPick(
                        fixture_id=candidate.fixture_id,
                        match_label=f"{candidate.home_team} vs {candidate.away_team}",
                        market=candidate.market,
                        selection=candidate.selection,
                        odds=candidate.odds,
                        opening_odds=candidate.opening_odds,
                        fair_implied_prob=candidate.fair_implied_prob,
                        line=candidate.line,
                        expected_return=candidate.ensemble.expected_return,
                        probability=candidate.ensemble.calibrated_probability,
                        expected_value=candidate.ensemble.expected_value,
                        confidence=candidate.ensemble.confidence,
                        pick_rank_score=rank_score,
                        stake_units=round(stake, 2),
                        stake_method=stake_method,
                        market_regime=candidate.market_regime,
                        reasoning=candidate.ensemble.reasoning,
                        rank=len(picks) + 1,
                        fixture_date=candidate.fixture_date,
                    )
                )

        logger.info(
            "picks_selected",
            total_candidates=len(candidates),
            selected=len(picks),
            max_allowed=max_picks,
        )
        return picks

    async def persist_picks(
        self, picks: list[SelectedPick], pick_date: datetime | None = None
    ) -> list[SelectedPick]:
        pick_date = pick_date or utc_now()
        already_picked = await self.get_fixture_ids_picked_today(pick_date)
        persisted: list[SelectedPick] = []

        for pick in picks:
            if pick.fixture_id in already_picked:
                logger.info(
                    "skip_duplicate_fixture_persist",
                    fixture_id=pick.fixture_id,
                    market=pick.market,
                    selection=pick.selection,
                )
                continue

            edge = compute_edge_metrics(pick.probability, pick.fair_implied_prob, None)
            rank = len(persisted) + 1
            record = DailyPick(
                pick_date=pick_date,
                fixture_id=pick.fixture_id,
                market=pick.market,
                selection=pick.selection,
                line=pick.line,
                odds=pick.odds,
                opening_odds=pick.opening_odds,
                fair_implied_prob=pick.fair_implied_prob,
                probability=pick.probability,
                expected_value=pick.expected_value,
                confidence=pick.confidence,
                roi_score=pick.pick_rank_score,
                stake_units=pick.stake_units,
                stake_method=pick.stake_method,
                reasoning=pick.reasoning,
                rank=rank,
                model_edge=edge.model_edge,
                closing_edge=edge.closing_edge,
                edge_capture=edge.adjusted_edge_capture,
                raw_edge_capture=edge.raw_edge_capture,
                adjusted_edge_capture=edge.adjusted_edge_capture,
                market_regime=pick.market_regime,
                is_paper=True,
            )
            self.session.add(record)

            pred = Prediction(
                fixture_id=pick.fixture_id,
                market=pick.market,
                selection=pick.selection,
                prob_ensemble=pick.probability,
                bookmaker_odds=pick.odds,
                implied_prob=pick.fair_implied_prob,
                expected_value=pick.expected_value,
                confidence=pick.confidence,
                roi_score=pick.pick_rank_score,
            )
            self.session.add(pred)
            await self.session.flush()

            persisted.append(
                SelectedPick(
                    fixture_id=pick.fixture_id,
                    match_label=pick.match_label,
                    market=pick.market,
                    selection=pick.selection,
                    odds=pick.odds,
                    opening_odds=pick.opening_odds,
                    fair_implied_prob=pick.fair_implied_prob,
                    line=pick.line,
                    expected_return=pick.expected_return,
                    probability=pick.probability,
                    expected_value=pick.expected_value,
                    confidence=pick.confidence,
                    pick_rank_score=pick.pick_rank_score,
                    stake_units=pick.stake_units,
                    stake_method=pick.stake_method,
                    market_regime=pick.market_regime,
                    reasoning=pick.reasoning,
                    rank=rank,
                    fixture_date=pick.fixture_date,
                    pick_id=record.id,
                )
            )
            already_picked.add(pick.fixture_id)

        if persisted:
            await self.session.commit()
        elif picks:
            logger.info("persist_picks_skipped_all_duplicates", attempted=len(picks))

        return persisted

    def _odds_query_filters(self, fixture_id: int, as_of: datetime, supported_markets: list[str]):
        market_clauses = []
        for market in supported_markets:
            if market == "over_under":
                market_clauses.append(
                    and_(OddsSnapshot.market == "over_under", OddsSnapshot.line == 2.5)
                )
            elif market == "btts":
                market_clauses.append(
                    and_(
                        OddsSnapshot.market == "btts",
                        func.lower(OddsSnapshot.selection).in_(("yes", "no")),
                    )
                )
            elif market == "match_winner":
                market_clauses.append(
                    and_(
                        OddsSnapshot.market == "match_winner",
                        func.lower(OddsSnapshot.selection).in_(
                            ("home", "draw", "away", "1", "2", "x")
                        ),
                    )
                )
            else:
                market_clauses.append(OddsSnapshot.market == market)
        return (
            OddsSnapshot.fixture_id == fixture_id,
            OddsSnapshot.captured_at <= as_of,
            or_(*market_clauses),
        )

    def _market_clauses(self, supported_markets: list[str]):
        clauses = []
        for market in supported_markets:
            if market == "over_under":
                clauses.append(
                    and_(OddsSnapshot.market == "over_under", OddsSnapshot.line == 2.5)
                )
            elif market == "btts":
                clauses.append(
                    and_(
                        OddsSnapshot.market == "btts",
                        func.lower(OddsSnapshot.selection).in_(("yes", "no")),
                    )
                )
            elif market == "match_winner":
                clauses.append(
                    and_(
                        OddsSnapshot.market == "match_winner",
                        func.lower(OddsSnapshot.selection).in_(
                            ("home", "draw", "away", "1", "2", "x")
                        ),
                    )
                )
            else:
                clauses.append(OddsSnapshot.market == market)
        return clauses

    async def _load_all_decision_odds(
        self,
        fixture_ids: list[int],
        as_of_map: dict[int, datetime],
    ) -> dict[int, dict]:
        if not fixture_ids:
            return {}
        supported_markets = [
            m for m in settings.supported_markets if m in self.PICK_MARKETS
        ]
        max_as_of = max(as_of_map.get(fid, utc_now()) for fid in fixture_ids)
        result = await self.session.execute(
            select(OddsSnapshot).where(
                OddsSnapshot.fixture_id.in_(fixture_ids),
                OddsSnapshot.captured_at <= max_as_of,
                or_(*self._market_clauses(supported_markets)),
            )
        )
        by_fixture: dict[int, list[OddsSnapshot]] = {}
        for snap in result.scalars().all():
            if self.exclude_legacy_bookmakers and snap.bookmaker in LEGACY_BOOKMAKERS:
                continue
            as_of = as_of_map.get(snap.fixture_id)
            if as_of is not None and snap.captured_at > as_of:
                continue
            by_fixture.setdefault(snap.fixture_id, []).append(snap)
        return {
            fid: self._group_odds_snapshots(snaps)
            for fid, snaps in by_fixture.items()
        }

    def _group_odds_snapshots(self, snapshots: list[OddsSnapshot]) -> dict:
        if not snapshots:
            return {}
        supported_markets = set(settings.supported_markets) & self.PICK_MARKETS
        latest: dict[tuple, OddsSnapshot] = {}
        opening: dict[tuple, float] = {}
        for snap in snapshots:
            key = (snap.bookmaker, snap.market, snap.selection, snap.line)
            if key not in opening:
                opening[key] = snap.opening_odds or snap.current_odds
            if key not in latest or snap.captured_at > latest[key].captured_at:
                latest[key] = snap

        grouped: dict[str, dict[str, dict]] = {}
        for key, snap in latest.items():
            _, market, selection, line = key
            if not is_supported_market(market, supported_markets):
                continue
            if is_disabled_market(market):
                continue
            if not is_eligible_selection(market, selection, line, live=True):
                continue
            if market not in grouped:
                grouped[market] = {}
            if selection not in grouped[market]:
                grouped[market][selection] = {
                    "odds_list": [],
                    "fair_probs": [],
                    "opening_odds": opening.get(key),
                    "line": line,
                }
            grouped[market][selection]["odds_list"].append(snap.current_odds)
            if snap.fair_prob:
                grouped[market][selection]["fair_probs"].append(snap.fair_prob)

        result_map: dict[str, dict[str, dict]] = {}
        for market, selections in grouped.items():
            result_map[market] = {}
            for selection, data in selections.items():
                odds_list = data["odds_list"]
                result_map[market][selection] = {
                    "odds": median_odds(odds_list),
                    "opening_odds": data["opening_odds"],
                    "fair_prob": (
                        sum(data["fair_probs"]) / len(data["fair_probs"])
                        if data["fair_probs"]
                        else None
                    ),
                    "bookmaker_count": len(odds_list),
                    "line": data["line"],
                }
            # Always re-derive fair probs from median market prices (proportional
            # devig). Overwrites any snapshot fair that may have fallen back to
            # raw 1/odds during ingest.
            median_by_sel = {
                sel: info["odds"]
                for sel, info in result_map[market].items()
                if info.get("odds") and info["odds"] > 1.0
            }
            devigged = fair_probs_from_selection_odds(median_by_sel)
            for sel, fair_p in devigged.items():
                result_map[market][sel]["fair_prob"] = fair_p
        return result_map

    async def _get_decision_odds(self, fixture_id: int, as_of: datetime) -> dict:
        supported_markets = [
            m for m in settings.supported_markets if m in self.PICK_MARKETS
        ]
        result = await self.session.execute(
            select(OddsSnapshot).where(*self._odds_query_filters(fixture_id, as_of, supported_markets))
        )
        return self._group_odds_snapshots(list(result.scalars().all()))

