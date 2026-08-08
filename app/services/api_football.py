"""HTTP client for API-Football."""

from typing import Any

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import get_settings
from app.utils.cache import cache

logger = structlog.get_logger()
settings = get_settings()


class APIFootballClient:
    """Primary data source for fixtures, stats, odds, lineups, injuries."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.api_football_key
        self.base_url = settings.api_football_base_url
        self.headers = {
            "x-apisports-key": self.api_key,
            "Accept": "application/json",
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException)),
        reraise=False,
    )
    async def _request(self, endpoint: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self.headers, params=params or {})
                if response.status_code == 429:
                    logger.warning("api_football_rate_limit", endpoint=endpoint)
                    return {}
                if response.status_code >= 400:
                    logger.warning(
                        "api_football_http_error",
                        endpoint=endpoint,
                        status=response.status_code,
                    )
                    return {}
                response.raise_for_status()
                data = response.json()
                if data.get("errors"):
                    logger.warning("api_football_errors", errors=data["errors"])
                return data
        except (httpx.NetworkError, httpx.TimeoutException):
            raise
        except Exception as exc:
            logger.warning("api_football_unexpected_error", endpoint=endpoint, error=str(exc))
            return {}

    async def get_fixtures(
        self,
        league_id: int,
        season: int,
        date: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        params: dict[str, Any] = {"league": league_id, "season": season}
        if date:
            params["date"] = date
        if status:
            params["status"] = status

        async def fetch():
            data = await self._request("fixtures", params)
            return data.get("response", [])

        return await cache.get_or_set(
            "api_football:fixtures",
            fetch,
            ttl=1800,
            league_id=league_id,
            season=season,
            date=date,
            status=status,
        )

    async def get_fixtures_all_pages(self, league_id: int, season: int) -> list[dict]:
        """Sve utakmice lige/sezone (bez keša — za backfill)."""
        data = await self._request("fixtures", {"league": league_id, "season": season})
        items = list(data.get("response", []))
        paging = data.get("paging") or {}
        total_pages = int(paging.get("total") or 1)
        page = 2
        while page <= total_pages:
            next_data = await self._request(
                "fixtures",
                {"league": league_id, "season": season, "page": page},
            )
            if next_data.get("errors"):
                break
            batch = next_data.get("response", [])
            if not batch:
                break
            items.extend(batch)
            page += 1
        return items

    async def get_fixtures_by_date(
        self,
        date: str,
        league_id: int | None = None,
    ) -> list[dict]:
        params: dict[str, Any] = {"date": date}
        if league_id is not None:
            params["league"] = league_id

        async def fetch():
            data = await self._request("fixtures", params)
            return data.get("response", [])

        return await cache.get_or_set(
            "api_football:fixtures_by_date",
            fetch,
            ttl=1800,
            date=date,
            league_id=league_id or 0,
        )

    async def get_fixture_by_id(self, fixture_id: int) -> dict | None:
        async def fetch():
            data = await self._request("fixtures", {"id": fixture_id})
            results = data.get("response", [])
            return results[0] if results else None

        return await cache.get_or_set(
            "api_football:fixture",
            fetch,
            ttl=600,
            fixture_id=fixture_id,
        )

    async def get_odds(self, fixture_id: int) -> list[dict]:
        async def fetch():
            data = await self._request("odds", {"fixture": fixture_id})
            return data.get("response", [])

        return await cache.get_or_set(
            "api_football:odds",
            fetch,
            ttl=300,
            fixture_id=fixture_id,
        )

    async def get_fixture_statistics(self, fixture_id: int) -> list[dict]:
        async def fetch():
            data = await self._request("fixtures/statistics", {"fixture": fixture_id})
            return data.get("response", [])

        return await cache.get_or_set(
            "api_football:stats",
            fetch,
            ttl=3600,
            fixture_id=fixture_id,
        )

    async def get_lineups(self, fixture_id: int) -> list[dict]:
        async def fetch():
            data = await self._request("fixtures/lineups", {"fixture": fixture_id})
            return data.get("response", [])

        return await cache.get_or_set(
            "api_football:lineups",
            fetch,
            ttl=1800,
            fixture_id=fixture_id,
        )

    async def get_injuries(self, fixture_id: int) -> list[dict]:
        async def fetch():
            data = await self._request("injuries", {"fixture": fixture_id})
            return data.get("response", [])

        return await cache.get_or_set(
            "api_football:injuries",
            fetch,
            ttl=3600,
            fixture_id=fixture_id,
        )

    async def get_standings(self, league_id: int, season: int) -> list[dict]:
        async def fetch():
            data = await self._request("standings", {"league": league_id, "season": season})
            return data.get("response", [])

        return await cache.get_or_set(
            "api_football:standings",
            fetch,
            ttl=7200,
            league_id=league_id,
            season=season,
        )

    async def get_head_to_head(self, team1_id: int, team2_id: int, last: int = 10) -> list[dict]:
        async def fetch():
            data = await self._request(
                "fixtures/headtohead",
                {"h2h": f"{team1_id}-{team2_id}", "last": last},
            )
            return data.get("response", [])

        return await cache.get_or_set(
            "api_football:h2h",
            fetch,
            ttl=86400,
            team1=team1_id,
            team2=team2_id,
            last=last,
        )

    async def get_team_fixtures(
        self, team_id: int, season: int, last: int = 5
    ) -> list[dict]:
        async def fetch():
            data = await self._request(
                "fixtures",
                {"team": team_id, "season": season, "last": last},
            )
            return data.get("response", [])

        return await cache.get_or_set(
            "api_football:team_fixtures",
            fetch,
            ttl=3600,
            team_id=team_id,
            season=season,
            last=last,
        )

    async def get_team_fixtures_fresh(
        self, team_id: int, season: int, last: int = 10
    ) -> list[dict]:
        """Bez keša — za on-demand backfill istorije tima."""
        data = await self._request(
            "fixtures",
            {"team": team_id, "season": season, "last": last},
        )
        return data.get("response", [])

    async def get_leagues(self, league_id: int, season: int) -> dict | None:
        async def fetch():
            data = await self._request("leagues", {"id": league_id, "season": season})
            results = data.get("response", [])
            return results[0] if results else None

        return await cache.get_or_set(
            "api_football:league",
            fetch,
            ttl=86400,
            league_id=league_id,
            season=season,
        )
