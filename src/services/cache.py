import redis.asyncio as redis
import json
from src.config import settings
from src.utils import logger
from typing import Optional


class CacheService:
    def __init__(self):
        self.redis = redis.from_url(settings.redis_url)

    async def get(self, key: str) -> Optional[dict]:
        """Get value from cache"""
        try:
            value = await self.redis.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Cache get error: {str(e)}")
            return None
    
    async def set(self, key: str, value: dict, ttl: int = 3600) -> bool:
        """Set value in cache with TTL"""
        try:
            await self.redis.setex(
                key,
                ttl,
                json.dumps(value)
            )
            return True
        except Exception as e:
            logger.error(f"Cache set error: {str(e)}")
            return False
    
    async def delete(self, key: str) -> bool:
        """Delete value from cache"""
        try:
            await self.redis.delete(key)
            return True
        except Exception as e:
            logger.error(f"Cache delete error: {str(e)}")
            return False
    
    async def increment(self, key: str, amount: int = 1) -> int:
        """Increment counter (for rate limiting)"""
        try:
            return await self.redis.incrby(key, amount)
        except Exception as e:
            logger.error(f"Cache increment error: {str(e)}")
            return 0
