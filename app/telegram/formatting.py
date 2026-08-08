"""Telegram pick display — jasan format za klađenje (srpski)."""

from __future__ import annotations

import re
from datetime import datetime

from app.utils.helpers import normalize_selection

PICK_SEPARATOR = "━━━━━━━━━━━━━━━"


def _format_ou_line(line: float | None) -> str:
    if line is None:
        return "2.5"
    return f"{line:g}"


def _format_handicap(line: float | None) -> str:
    if line is None:
        return "0"
    if line > 0:
        return f"+{line:g}"
    return f"{line:g}"


def _parse_ou_line_from_selection(selection: str) -> float | None:
    for part in selection.replace("_", " ").split():
        try:
            val = float(part)
            if 0.5 <= val <= 10.0:
                return val
        except ValueError:
            continue
    return None


def parse_match_label(match_label: str) -> tuple[str, str]:
    if " vs " in match_label:
        home, away = match_label.split(" vs ", 1)
        return home.strip(), away.strip()
    return match_label.strip(), ""


def _escape_md(text: str) -> str:
    for ch in ("_", "*", "[", "`"):
        text = text.replace(ch, f"\\{ch}")
    return text


def fmt_team(name: str) -> str:
    """Bold + velika slova (Telegram Markdown)."""
    return f"*{_escape_md(name.upper())}*"


def format_kickoff_time(fixture_date: datetime | None) -> str:
    if not fixture_date:
        return "🕒 POČETAK: N/A"
    from datetime import timezone, timedelta
    srb = timezone(timedelta(hours=2))
    local = fixture_date.replace(tzinfo=timezone.utc).astimezone(srb)
    return f"🕒 POČETAK: {local.strftime('%H:%M')} (srpsko vreme)"


def _canonical_tip_key(market: str, selection: str, line: float | None = None) -> str:
    """Map internal market/selection to canonical tip keys."""
    m = (market or "").upper().replace("-", "_")
    if m in ("DRAW", "UNDER_2_5", "OVER_2_5", "NG", "GG"):
        return m

    internal = (market or "").lower().replace("-", "_")
    sel = normalize_selection(selection)

    if internal == "match_winner" and sel in ("draw", "x"):
        return "DRAW"

    if internal == "btts" and sel in ("no", "ng"):
        return "NG"
    if internal == "btts" and sel in ("yes", "gg"):
        return "GG"

    if internal == "over_under":
        ou_line = line if line is not None else _parse_ou_line_from_selection(sel)
        if ou_line is None or abs(float(ou_line) - 2.5) < 1e-9:
            if "under" in sel:
                return "UNDER_2_5"
            if "over" in sel:
                return "OVER_2_5"

    return m


def format_tip(market: str, selection: str = "", line: float | None = None) -> str:
    """Jedna linija tipa za Telegram — 🎯 TIP: ..."""
    key = _canonical_tip_key(market, selection, line)

    if key == "UNDER_2_5":
        return "🎯 TIP: Under 2.5 golova (0–2 gola)"
    if key == "OVER_2_5":
        return "🎯 TIP: Over 2.5 golova (3+ gola)"
    if key == "DRAW":
        return "🎯 TIP: X (Nerešeno)"
    if key == "NG":
        return "🎯 TIP: BTTS — NE (oba tima ne daju gol)"
    if key == "GG":
        return "🎯 TIP: BTTS — DA (oba tima daju gol)"

    label = format_betting_tip(market, selection, line) if selection else market
    return f"🎯 TIP: {label}"


def tip_reason(market: str, selection: str = "", line: float | None = None) -> str:
    """Kratko objašnjenje ispod tipa."""
    key = _canonical_tip_key(market, selection, line)

    if key == "NG":
        return "• Očekuje se da bar jedan tim ne postigne gol"
    if key == "GG":
        return "• Očekuje se da oba tima postignu gol"
    if key == "UNDER_2_5":
        return "• Maks 2 gola na meču"
    if key == "OVER_2_5":
        return "• Potrebna su 3+ gola na meču"
    if key == "DRAW":
        return "• Utakmica se završava nerešeno"

    internal = (market or "").lower().replace("-", "_")
    sel = normalize_selection(selection)
    if internal == "match_winner":
        if sel in ("home", "1"):
            return "• Domaćin mora pobediti"
        if sel in ("away", "2"):
            return "• Gost mora pobediti"

    return f"• {tip_explanation(market, selection, line)}"


def format_betting_tip(market: str, selection: str, line: float | None = None) -> str:
    """Tekst tipa za kladionicu (prikazuje se pod IGRAJ:)."""
    m = (market or "").lower().replace("-", "_")
    sel = normalize_selection(selection)

    if m == "match_winner":
        if sel in ("home", "1"):
            return "1 (Pobeda domaćina)"
        if sel in ("away", "2"):
            return "2 (Pobeda gosta)"
        if sel in ("draw", "x"):
            return "X (Nerešeno)"

    if m == "btts":
        if sel in ("yes", "gg"):
            return "GG"
        if sel in ("no", "ng"):
            return "NG"

    if m == "over_under":
        ou_line = line if line is not None else _parse_ou_line_from_selection(sel)
        line_str = _format_ou_line(ou_line)
        if "over" in sel:
            return f"Ukupno golova — Više od {line_str}"
        if "under" in sel:
            return f"Ukupno golova — Manje od {line_str}"

    if m == "asian_handicap":
        ah_line = line
        if ah_line is None:
            match = re.search(r"[+-]?\d+(?:\.\d+)?", selection)
            if match:
                ah_line = float(match.group())
        handicap = _format_handicap(ah_line)
        if sel.startswith("home") or sel in ("1", "home"):
            return f"Hendikep domaćin {handicap}"
        if sel.startswith("away") or sel in ("2", "away"):
            return f"Hendikep gost {handicap}"

    return selection.replace("_", " ").strip().title()


def tip_explanation(market: str, selection: str, line: float | None = None) -> str:
    """Kratko objašnjenje šta tip znači u kladionici."""
    m = (market or "").lower().replace("-", "_")
    sel = normalize_selection(selection)

    if m == "match_winner":
        if sel in ("home", "1"):
            return "Domaćin mora pobediti"
        if sel in ("away", "2"):
            return "Gost mora pobediti"
        if sel in ("draw", "x"):
            return "Utakmica završava nerešeno"

    if m == "btts":
        if sel in ("yes", "gg"):
            return "Oba tima daju gol"
        if sel in ("no", "ng"):
            return "Najmanje jedan tim ne daje gol"

    if m == "over_under":
        ou_line = line if line is not None else _parse_ou_line_from_selection(sel)
        line_str = _format_ou_line(ou_line)
        if "over" in sel:
            return f"Potrebna su najmanje {int(float(line_str) + 0.5)} gola na meču"
        if "under" in sel:
            max_goals = int(float(line_str))
            return f"Maksimalno {max_goals} gola na meču"

    if m == "asian_handicap":
        if sel.startswith("home") or sel in ("1", "home"):
            return "Domaćin mora pokriti hendikep"
        if sel.startswith("away") or sel in ("2", "away"):
            return "Gost mora pokriti hendikep"

    return "Proveri uslov tipa u kladionici pre odigravanja"


def confidence_strength(confidence: float) -> tuple[str, int]:
    """Vrati (zvezdice, broj/5) na osnovu pouzdanosti u procentima."""
    pct = confidence * 100
    if pct < 15:
        return "⭐⭐", 2
    if pct <= 25:
        return "⭐⭐⭐", 3
    if pct <= 40:
        return "⭐⭐⭐⭐", 4
    return "⭐⭐⭐⭐⭐", 5


def format_ev_percent(ev: float) -> str:
    pct = round(ev * 100)
    if pct >= 0:
        return f"+{pct}"
    return str(pct)


def format_probability_percent(probability: float) -> str:
    return str(round(probability * 100))


def format_implied_from_odds(odds: float) -> str:
    if odds <= 1.0:
        return "—"
    return str(round(100 / odds))


def format_edge_pp(model_prob: float, fair_implied: float | None) -> str:
    if fair_implied is None:
        return "—"
    edge = (model_prob - fair_implied) * 100
    sign = "+" if edge >= 0 else ""
    return f"{sign}{edge:.1f}pp"


def format_confidence_percent(confidence: float) -> str:
    return str(round(confidence * 100))


def format_kickoff(fixture_date: datetime | None) -> str:
    """Legacy pun datum — koristi format_kickoff_time za pick poruke."""
    return format_kickoff_time(fixture_date)
