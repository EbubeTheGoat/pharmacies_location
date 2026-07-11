import os
import json
from dotenv import load_dotenv
import redis
from redis.exceptions import RedisError

from logger_config import get_logger

load_dotenv()

ENV = os.getenv("ENV", "development")
REDIS_URL = os.getenv("REDIS_URL")
logger = get_logger("cache")

if not REDIS_URL:
    if ENV == "production":
        raise RuntimeError("REDIS_URL is required in production")
    REDIS_URL = "redis://localhost:6379"
    logger.warning("REDIS_URL not set — using local Redis for development")


def _create_redis_client(url: str) -> redis.Redis:
    # A bad URL is a config bug: fail immediately and loudly.
    try:
        pool = redis.ConnectionPool.from_url(
            url,
            decode_responses=True,
            socket_timeout=2,
            retry_on_timeout=True,
        )
    except ValueError as e:
        raise RuntimeError(f"Invalid REDIS_URL '{url}'") from e

    client = redis.Redis(connection_pool=pool)

    # Redis being temporarily unreachable at startup is NOT a config bug.
    # Log it and let the per-call handlers below degrade gracefully.
    try:
        client.ping()
        logger.info("Connected to Redis")
    except RedisError as e:
        logger.warning(f"Redis unreachable at startup, running without cache: {e}")

    return client


_client = _create_redis_client(REDIS_URL)


def get_user_cache(key: str) -> dict | None:
    try:
        data = _client.get(f"user:{key}")
        if data:
            return json.loads(data)
        return None
    except (RedisError, json.JSONDecodeError) as e:
        logger.error(f"Redis get error for key user:{key}: {e}")
        return None


def set_user_cache(key: str, data: dict, ttl: int = 3600) -> None:
    try:
        _client.set(f"user:{key}", json.dumps(data), ex=ttl)
    except RedisError as e:
        logger.error(f"Redis set error for key user:{key}: {e}")
