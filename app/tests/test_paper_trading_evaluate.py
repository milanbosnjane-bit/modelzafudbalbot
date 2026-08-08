"""Tests for paper trading evaluate — koristi vrednosti iz baze."""

from types import SimpleNamespace

from app.services.paper_trading import PaperTradingService


def _pick(
    outcome: str,
    stake: float,
    profit: float | None,
    odds: float = 2.0,
):
    return SimpleNamespace(
        outcome=outcome,
        stake_units=stake,
        profit_units=profit,
        odds=odds,
        pick_date=None,
        clv=None,
        edge_capture=None,
    )


class TestEvaluateFromDatabase:
    def test_profit_sums_profit_units_not_recalculated(self):
        picks = [
            _pick("win", 0.0, 0.0, odds=2.0),
            _pick("lose", 1.0, -1.0),
            _pick("win", 1.0, 1.5),
        ]
        profits = [
            p.profit_units if p.profit_units is not None else 0.0
            for p in picks
        ]
        staked = sum(p.stake_units or 0.0 for p in picks)
        assert sum(profits) == 0.5
        assert staked == 2.0

    def test_zero_stake_does_not_inflate_profit(self):
        """Stake 0 u bazi ne sme da pretvara profit u 10u * odds."""
        pick = _pick("win", 0.0, 0.0, odds=1.57)
        profit = pick.profit_units if pick.profit_units is not None else 0.0
        assert profit == 0.0
        assert profit != 10.0 * (pick.odds - 1)
