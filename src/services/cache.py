import json
from typing import Optional

from src.config import settings


class RedisCache:
    def __init__(self, url: str):
        import redis.asyncio as redis

        self.redis = redis.from_url(url)

    async def get(self, key: str) -> Optional[dict]:
        value = await self.redis.get(key)
        if value:
            return json.loads(value)
        return None

    async def set(self, key: str, value: dict, ttl: int = 3600) -> None:
        await self.redis.setex(key, ttl, json.dumps(value))

    async def delete(self, key: str) -> None:
        await self.redis.delete(key)

    async def increment(self, key: str, amount: int = 1) -> int:
        return await self.redis.incrby(key, amount)


class CacheService:
    """Единый интерфейс сессий; бэкенд выбирает settings.cache_backend."""

    def __init__(self):
        if settings.cache_backend == "redis":
            self._impl = RedisCache(settings.redis_url)
        elif settings.cache_backend == "sqlite":
            from src.services.sqlite_store import SqliteCache

            self._impl = SqliteCache(settings.sqlite_path)
        else:
            raise ValueError(f"Неизвестный CACHE_BACKEND: {settings.cache_backend}")

    async def get(self, key: str) -> Optional[dict]:
        return await self._impl.get(key)

    async def set(self, key: str, value: dict, ttl: int = 3600) -> None:
        await self._impl.set(key, value, ttl)

    async def delete(self, key: str) -> None:
        await self._impl.delete(key)

    async def increment(self, key: str, amount: int = 1) -> int:
        return await self._impl.increment(key, amount)
