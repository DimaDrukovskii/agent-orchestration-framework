import redis.asyncio as redis
import json
from src.config import settings
from typing import Optional


class CacheService:
    def __init__(self):
        self.redis = redis.from_url(settings.redis_url)

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
