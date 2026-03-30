import json
from redis.asyncio import Redis
from app.config import settings

redis_client: Redis = None


async def get_redis() -> Redis:
    global redis_client
    if redis_client is None:
        redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return redis_client


async def close_redis():
    global redis_client
    if redis_client:
        await redis_client.aclose()
        redis_client = None


PROFILE_CACHE_TTL = 60  # seconds


async def get_cached_profile(redis: Redis, merchant_id: str) -> dict | None:
    data = await redis.get(f"merchant:profile:{merchant_id}")
    return json.loads(data) if data else None


async def set_cached_profile(redis: Redis, merchant_id: str, data: dict):
    await redis.setex(f"merchant:profile:{merchant_id}", PROFILE_CACHE_TTL, json.dumps(data))


async def invalidate_profile_cache(redis: Redis, merchant_id: str):
    await redis.delete(f"merchant:profile:{merchant_id}")
