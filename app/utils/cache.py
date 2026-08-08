"""Redis caching layer with in-memory fallback when Redis is unavailable."""

import json
from typing import Any

import structlog

from app.config import get_settings
from app.utils.helpers import cache_key

logger = structlog.get_logger()
settings = get_settings()


class MemoryCache:
    """Sync in-memory store — no I/O, used when Redis is off."""

    def __init__(self):
        self._store: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self._store.get(key)

    def set(self, key: str, value: Any) -> None:
        self._store[key] = value

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


class RedisCache:
    def __init__(self, url: str | None = None, ttl: int | None = None):
        self.url = url or settings.redis_url
        self.ttl = ttl or settings.cache_ttl_seconds
        self._client = None
        self._use_memory = settings.use_memory_cache

    async def connect(self) -> None:
        if self._use_memory or self._client is not None:
            return
        try:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(self.url, decode_responses=True)
            await self._client.ping()
        except Exception as e:
            logger.warning("redis_unavailable_using_memory", error=str(e))
            self._use_memory = True
            self._client = None

    async def disconnect(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None
        _memory_cache.clear()

    async def get(self, key: str) -> Any | None:
        if self._use_memory:
            return _memory_cache.get(key)
        await self.connect()
        if self._use_memory:
            return _memory_cache.get(key)
        assert self._client is not None
        data = await self._client.get(key)
        if data is None:
            return None
        return json.loads(data)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        if self._use_memory:
            _memory_cache.set(key, value)
            return
        await self.connect()
        if self._use_memory:
            _memory_cache.set(key, value)
            return
        assert self._client is not None
        await self._client.set(key, json.dumps(value, default=str), ex=ttl or self.ttl)

    async def delete(self, key: str) -> None:
        if self._use_memory:
            _memory_cache.delete(key)
            return
        await self.connect()
        if self._use_memory:
            _memory_cache.delete(key)
            return
        assert self._client is not None
        await self._client.delete(key)

    async def get_or_set(
        self,
        prefix: str,
        fetch_fn,
        ttl: int | None = None,
        **kwargs: Any,
    ) -> Any:
        key = cache_key(prefix, **kwargs)
        cached = await self.get(key)
        if cached is not None:
            return cached
        result = await fetch_fn()
        await self.set(key, result, ttl=ttl)
        return result


_memory_cache = MemoryCache()
cache = RedisCache()
