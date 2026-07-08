"""Redis Streams publisher.

Every event written to PostgreSQL is also published to a per-campaign Redis
Stream so that the Memory Service can consume it asynchronously to update
pgvector summaries without blocking the write path.

Stream key format: events:campaign:{campaign_id}
"""
from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from app.config import settings

logger = logging.getLogger(__name__)

_client: aioredis.Redis | None = None

# Consumer groups that must exist on every campaign stream.
# Add new downstream consumers here as services are built.
_CONSUMER_GROUPS = ["memory-service"]

# Tracks which stream keys have already had their consumer groups created
# so we only call XGROUP CREATE once per campaign per process lifetime.
_initialized_streams: set[str] = set()


async def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _ensure_consumer_groups(client: aioredis.Redis, stream_key: str) -> None:
    """Create consumer groups for all known downstream consumers if they don't exist.

    Uses id="0" so consumers receive all events from the beginning of the stream,
    ensuring no events are missed if a consumer restarts.
    Silently ignores BUSYGROUP error (group already exists — idempotent).
    """
    for group in _CONSUMER_GROUPS:
        try:
            await client.xgroup_create(stream_key, group, id="0", mkstream=True)
            logger.info("Created consumer group '%s' on stream '%s'", group, stream_key)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise


async def publish_event(campaign_id: str, event_data: dict[str, Any]) -> None:
    """XADD the event to events:campaign:{campaign_id} stream.

    Lazily creates consumer groups on the first publish per campaign stream
    so that downstream consumers (e.g. Memory Service) can begin consuming
    immediately without any separate setup step.
    """
    client = await get_redis()
    stream_key = f"events:campaign:{campaign_id}"
    await client.xadd(stream_key, {"data": json.dumps(event_data, default=str)})
    logger.debug("Published %s to stream %s", event_data.get("event_type"), stream_key)

    if stream_key not in _initialized_streams:
        await _ensure_consumer_groups(client, stream_key)
        _initialized_streams.add(stream_key)
