"""Context gates — fatigue, market confirmation, lineup/injury (API-Football only)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.config import get_settings
from app.utils.feature_values import numeric_feature
from app.utils.helpers import normalize_selection, utc_now

settings = get_settings()


@dataclass
class ContextGateInput:
    market: str
    selection: str
    fixture_date: datetime | None = None


@dataclass
class ContextGateResult:
    passed: bool
    drop_reason: str | None = None
    notes: list[str] | None = None


def _fatigue(features: dict, side: str) -> float:
    return numeric_feature(features, f"{side}_fatigue_score", 0.0) or 0.0


def _motivation(features: dict, side: str) -> float:
    return numeric_feature(features, f"{side}_motivation_score", 0.5) or 0.5


def _injury(features: dict, side: str) -> float:
    return numeric_feature(features, f"{side}_injury_impact_score", 0.0) or 0.0


def _rotation(features: dict, side: str) -> float:
    return numeric_feature(features, f"{side}_rotation_score", 0.0) or 0.0


def _picked_side(market: str, selection: str) -> str | None:
    m = (market or "").lower().replace("-", "_")
    sel = normalize_selection(selection)
    if m == "match_winner":
        if sel in ("home", "1"):
            return "home"
        if sel in ("away", "2"):
            return "away"
        if sel in ("draw", "x"):
            return "draw"
    return None


def _is_low_scoring_market(market: str, selection: str) -> bool:
    m = (market or "").lower().replace("-", "_")
    sel = normalize_selection(selection)
    if m == "over_under" and "under" in sel:
        return True
    if m == "btts" and sel in ("no", "ng"):
        return True
    if m == "match_winner" and sel in ("draw", "x"):
        return True
    return False


def passes_fatigue_gate(candidate: ContextGateInput, features: dict) -> ContextGateResult:
    home_f = _fatigue(features, "home")
    away_f = _fatigue(features, "away")
    home_m = _motivation(features, "home")
    away_m = _motivation(features, "away")
    avg_f = (home_f + away_f) / 2.0
    notes: list[str] = []

    if avg_f >= 0.35:
        notes.append(f"Umor oba tima: {avg_f:.0%} (podržava manje golova)")
    elif max(home_f, away_f) >= 0.45:
        tired = "domaćin" if home_f >= away_f else "gost"
        notes.append(f"Viši umor ({tired}) — kontekst za under/draw")

    side = _picked_side(candidate.market, candidate.selection)
    if side in ("home", "away"):
        fatigue = _fatigue(features, side)
        if fatigue >= settings.fatigue_block_side_threshold:
            return ContextGateResult(
                passed=False,
                drop_reason="fatigue_picked_side_exhausted",
                notes=notes,
            )

    if _is_low_scoring_market(candidate.market, candidate.selection):
        if home_f < settings.fatigue_block_under_threshold and away_f < settings.fatigue_block_under_threshold:
            if home_m > settings.motivation_high_threshold and away_m > settings.motivation_high_threshold:
                return ContextGateResult(
                    passed=False,
                    drop_reason="fatigue_fresh_high_motivation",
                    notes=notes,
                )
        if avg_f >= settings.fatigue_support_under_threshold:
            notes.append("Kontekst podržava nizak broj golova")

    return ContextGateResult(passed=True, notes=notes)


def passes_market_confirmation_gate(
    *,
    opening_odds: float | None,
    current_odds: float,
) -> ContextGateResult:
    notes: list[str] = []
    if opening_odds is None or opening_odds <= 1.0:
        return ContextGateResult(passed=True, notes=notes)

    move = (current_odds - opening_odds) / opening_odds
    if move <= -settings.market_confirm_shortening_pct:
        notes.append(
            f"Tržište potvrđuje: kvota {opening_odds:.2f} → {current_odds:.2f}"
        )
    elif move >= settings.market_adverse_move_pct:
        return ContextGateResult(
            passed=False,
            drop_reason="market_adverse_move",
            notes=notes,
        )
    elif move < 0:
        notes.append(f"Blago skraćenje kvote ({move:.1%})")

    return ContextGateResult(passed=True, notes=notes)


def passes_lineup_gate(candidate: ContextGateInput, features: dict) -> ContextGateResult:
    notes: list[str] = []
    now = utc_now()
    hours_to_kick = None
    if candidate.fixture_date:
        hours_to_kick = (candidate.fixture_date - now).total_seconds() / 3600.0

    side = _picked_side(candidate.market, candidate.selection)
    if side in ("home", "away"):
        injury = _injury(features, side)
        rotation = _rotation(features, side)
        if injury >= settings.lineup_injury_block_threshold:
            return ContextGateResult(
                passed=False,
                drop_reason="lineup_injury_picked_side",
                notes=notes,
            )
        if rotation >= settings.lineup_rotation_block_threshold:
            return ContextGateResult(
                passed=False,
                drop_reason="lineup_heavy_rotation",
                notes=notes,
            )
        if injury >= 0.35:
            notes.append(f"Povrede/suspenzije ({side}): {injury:.0%} uticaj")
        if rotation >= 0.4:
            notes.append(f"Rotacija ({side}): {rotation:.0%}")

    home_inj = _injury(features, "home")
    away_inj = _injury(features, "away")
    home_rot = _rotation(features, "home")
    away_rot = _rotation(features, "away")

    if _is_low_scoring_market(candidate.market, candidate.selection):
        if home_inj >= 0.35 or away_inj >= 0.35:
            notes.append("Povrede u sastavu — podržava under/NG")
        if home_rot >= 0.45 and away_rot >= 0.45:
            notes.append("Rotacija oba tima — podržava under/draw")

    if (
        hours_to_kick is not None
        and hours_to_kick <= settings.lineup_window_hours
        and side in ("home", "away")
        and home_rot == 0.0
        and away_rot == 0.0
        and home_inj < 0.2
        and away_inj < 0.2
    ):
        return ContextGateResult(
            passed=False,
            drop_reason="lineup_missing_near_kickoff",
            notes=notes,
        )

    return ContextGateResult(passed=True, notes=notes)


def passes_context_gates(
    candidate: ContextGateInput,
    features: dict,
    *,
    opening_odds: float | None,
    current_odds: float,
) -> ContextGateResult:
    if not settings.context_gates_enabled:
        return ContextGateResult(passed=True)

    all_notes: list[str] = []

    if settings.fatigue_gate_enabled:
        result = passes_fatigue_gate(candidate, features)
        if not result.passed:
            return result
        if result.notes:
            all_notes.extend(result.notes)

    if settings.market_confirmation_gate_enabled:
        result = passes_market_confirmation_gate(
            opening_odds=opening_odds, current_odds=current_odds
        )
        if not result.passed:
            return result
        if result.notes:
            all_notes.extend(result.notes)

    if settings.lineup_gate_enabled:
        result = passes_lineup_gate(candidate, features)
        if not result.passed:
            return result
        if result.notes:
            all_notes.extend(result.notes)

    seen: set[str] = set()
    unique_notes = []
    for note in all_notes:
        if note not in seen:
            seen.add(note)
            unique_notes.append(note)

    return ContextGateResult(passed=True, notes=unique_notes or None)
