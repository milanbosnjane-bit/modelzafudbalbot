"""Market/selection encoding for tree models."""

MARKET_IDS = {
    "match_winner": 0,
    "double_chance": 1,
    "over_under": 2,
    "btts": 3,
    "asian_handicap": 4,
}


from app.utils.helpers import normalize_selection


def encode_market_selection(market: str, selection: str | int) -> dict[str, float]:
    sel = normalize_selection(selection).replace(" ", "_")
    return {
        "market_id": float(MARKET_IDS.get(market, -1)),
        "sel_is_home": 1.0 if sel in ("home", "1", "1x", "home_draw") else 0.0,
        "sel_is_away": 1.0 if sel in ("away", "2", "x2", "draw_away") else 0.0,
        "sel_is_draw": 1.0 if sel in ("draw", "x") else 0.0,
        "sel_is_over": 1.0 if "over" in sel else 0.0,
        "sel_is_under": 1.0 if "under" in sel else 0.0,
        "sel_is_yes": 1.0 if "yes" in sel else 0.0,
        "sel_is_no": 1.0 if "no" in sel else 0.0,
    }


def augment_features(features: dict, market: str, selection: str) -> dict:
    return {**features, **encode_market_selection(market, selection)}
