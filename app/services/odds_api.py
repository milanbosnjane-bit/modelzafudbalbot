"""The Odds API client for market odds and movement."""

from typing import Any

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.utils.cache import cache

logger = structlog.get_logger()
settings = get_settings()


class OddsAPIClient:
    """Secondary source for sharp market odds and line movement."""

    SPORT_KEY_MAP = {
        39: "soccer_epl",
        140: "soccer_spain_la_liga",
        135: "soccer_italy_serie_a",
        78: "soccer_germany_bundesliga",
        61: "soccer_france_ligue_one",
        2: "soccer_uefa_champs_league",
    }

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.odds_api_key
        self.base_url = settings.odds_api_base_url

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _request(self, endpoint: str, params: dict | None = None) -> Any:
        params = params or {}
        params["apiKey"] = self.api_key
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def get_odds(
        self,
        league_id: int,
        markets: str = "h2h,totals,spreads",
        regions: str = "eu,uk",
    ) -> list[dict]:
        sport_key = self.SPORT_KEY_MAP.get(league_id)
        if not sport_key:
            return []

        async def fetch():
            return await self._request(
                f"sports/{sport_key}/odds",
                {"markets": markets, "regions": regions, "oddsFormat": "decimal"},
            )

        return await cache.get_or_set(
            "odds_api:odds",
            fetch,
            ttl=300,
            league_id=league_id,
            markets=markets,
        )

    async def get_historical_odds(
        self, league_id: int, date: str, markets: str = "h2h,totals"
    ) -> list[dict]:
        sport_key = self.SPORT_KEY_MAP.get(league_id)
        if not sport_key:
            return []

        async def fetch():
            return await self._request(
                f"historical/sports/{sport_key}/odds",
                {"date": date, "markets": markets, "oddsFormat": "decimal"},
            )

        return await cache.get_or_set(
            "odds_api:historical",
            fetch,
            ttl=86400,
            league_id=league_id,
            date=date,
        )
