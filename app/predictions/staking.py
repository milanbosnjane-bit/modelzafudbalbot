"""Staking module: flat, Kelly, fractional Kelly with 2% bankroll hard cap."""

from enum import Enum

from app.config import get_settings
from app.utils.helpers import capped_stake, flat_stake

settings = get_settings()


class StakeMethod(str, Enum):
    FLAT = "flat"
    KELLY = "kelly"
    FRACTIONAL_KELLY = "fractional_kelly"


class StakingCalculator:
    """
    stake = min(fractional_kelly, bankroll * max_stake_pct)
    Default hard cap: 2% of bankroll per bet.
    """

    def __init__(
        self,
        bankroll: float | None = None,
        flat_units: float = 1.0,
        kelly_fraction: float | None = None,
        max_pct: float | None = None,
    ):
        self.bankroll = bankroll or settings.default_bankroll
        self.flat_units = flat_units
        self.kelly_fraction = kelly_fraction or settings.kelly_fraction
        self.max_pct = max_pct or settings.max_stake_pct_bankroll

    def calculate(
        self,
        probability: float,
        odds: float,
        method: StakeMethod | str = StakeMethod.FRACTIONAL_KELLY,
    ) -> float:
        method = StakeMethod(method) if isinstance(method, str) else method
        if method == StakeMethod.FLAT:
            return flat_stake(self.flat_units)
        return capped_stake(probability, odds, str(method.value), self.bankroll)

    def calculate_batch(
        self,
        bets: list[dict],
        method: StakeMethod | str = StakeMethod.FRACTIONAL_KELLY,
    ) -> list[dict]:
        return [
            {**bet, "stake_units": self.calculate(bet["probability"], bet["odds"], method),
             "stake_method": str(method)}
            for bet in bets
        ]
