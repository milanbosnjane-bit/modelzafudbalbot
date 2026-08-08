"""Weather API client for match conditions."""

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.utils.cache import cache

logger = structlog.get_logger()
settings = get_settings()


class WeatherClient:
    BASE_URL = "https://api.openweathermap.org/data/2.5/weather"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.weather_api_key

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5))
    async def get_weather(self, lat: float, lon: float) -> dict | None:
        if not self.api_key:
            return None

        async def fetch():
            params = {
                "lat": lat,
                "lon": lon,
                "appid": self.api_key,
                "units": "metric",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(self.BASE_URL, params=params)
                response.raise_for_status()
                data = response.json()
                return {
                    "temperature": data["main"]["temp"],
                    "humidity": data["main"]["humidity"],
                    "wind_speed": data["wind"]["speed"],
                    "precipitation": data.get("rain", {}).get("1h", 0.0),
                    "conditions": data["weather"][0]["main"] if data.get("weather") else None,
                }

        return await cache.get_or_set(
            "weather",
            fetch,
            ttl=3600,
            lat=round(lat, 2),
            lon=round(lon, 2),
        )
