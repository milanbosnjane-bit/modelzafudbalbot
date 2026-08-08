"""Canonical market/selection filters — same rules as ingestion, applied before predict."""

from __future__ import annotations

import re

from app.utils.helpers import normalize_selection

ALLOWED_OU_LINES = frozenset({2.5})
LIVE_OU_LINES = frozenset({2.5})
ALLOWED_AH_LINES = frozenset({-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5})

MATCH_WINNER_SELECTIONS = frozenset({"home", "away", "draw", "1", "2", "x"})
BTTS_SELECTIONS = frozenset({"yes", "no"})


def parse_ou_line(selection: str, sel_norm: str | None = None) -> float | None:
    sel_norm = sel_norm or normalize_selection(selection)
    for part in sel_norm.replace("_", " ").split():
        try:
            val = float(part)
            if 0.5 <= val <= 10.0:
                return val
        except ValueError:
            continue
    for part in str(selection).split():
        try:
            val = float(part)
            if 0.5 <= val <= 10.0:
                return val
        except ValueError:
            continue
    return None


def _normalize_line(line: float | None) -> float | None:
    if line is None:
        return None
    return round(float(line), 2)


def format_prediction_selection(
    market: str,
    selection: str,
    line: float | None = None,
) -> str:
    """Canonical label for system-wide prediction filters (e.g. 'BTTS No')."""
    m = (market or "").strip().lower().replace("-", "_")
    sel = normalize_selection(selection)

    if m == "btts":
        if sel in ("no", "ng"):
            return "BTTS No"
        if sel in ("yes", "gg"):
            return "BTTS Yes"

    if m == "over_under":
        ou_line = _normalize_line(line) or _normalize_line(parse_ou_line(selection, sel))
        if ou_line == 2.5 or ou_line is None:
            if "under" in sel:
                return "Under 2.5"
            if "over" in sel:
                return "Over 2.5"

    if m == "match_winner":
        if sel in ("draw", "x"):
            return "Draw"
        if sel in ("home", "1"):
            return "Home"
        if sel in ("away", "2"):
            return "Away"

    return f"{market}/{selection}"


def passes_prediction_type_filter(
    market: str,
    selection: str,
    line: float | None = None,
) -> bool:
    """System-wide blocklist — applied before model inference or DB writes."""
    prediction_selection = format_prediction_selection(market, selection, line)
    if "BTTS No" in prediction_selection:
        return False
    return True


def is_eligible_selection(
    market: str,
    selection: str,
    line: float | None = None,
    *,
    live: bool = False,
) -> bool:
    """Return True only for selections the models and ingestion pipeline support."""
    if not passes_prediction_type_filter(market, selection, line):
        return False

    sel = normalize_selection(selection)

    if market == "match_winner":
        return sel in MATCH_WINNER_SELECTIONS

    if market == "btts":
        if "/" in sel:
            return False
        return sel in BTTS_SELECTIONS

    if market == "over_under":
        if "/" in sel or "more" in sel or "less" in sel:
            return False
        if not ("over" in sel or "under" in sel):
            return False
        ou_line = _normalize_line(line) or _normalize_line(parse_ou_line(selection, sel))
        allowed = LIVE_OU_LINES if live else ALLOWED_OU_LINES
        return ou_line in allowed

    if market == "asian_handicap":
        ah_line = _normalize_line(line)
        if ah_line is None:
            m = re.search(r"[+-]?\d+(?:\.\d+)?", selection)
            if m:
                ah_line = _normalize_line(float(m.group()))
        if ah_line is None or abs(ah_line) > 1.5:
            return False
        if ah_line not in ALLOWED_AH_LINES:
            return False
        return sel.startswith("home") or sel.startswith("away") or sel.startswith("draw")

    return False
