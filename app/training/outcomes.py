"""Resolve betting outcomes from final scores."""


def resolve_market_outcome(
    home_goals: int,
    away_goals: int,
    market: str,
    selection: str,
    line: float | None = None,
) -> str:
    if market == "btts":
        if "yes" in selection.lower():
            return "win" if home_goals > 0 and away_goals > 0 else "lose"
        return "win" if home_goals == 0 or away_goals == 0 else "lose"

    if market == "over_under":
        total = home_goals + away_goals
        ou_line = line if line is not None else 2.5
        if "over" in selection.lower():
            if total == ou_line:
                return "push"
            return "win" if total > ou_line else "lose"
        if total == ou_line:
            return "push"
        return "win" if total < ou_line else "lose"

    if market == "match_winner":
        sel = selection.lower()
        if sel == "home" and home_goals > away_goals:
            return "win"
        if sel == "away" and away_goals > home_goals:
            return "win"
        if sel == "draw" and home_goals == away_goals:
            return "win"
        return "lose"

    return "void"
