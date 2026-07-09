"""Redis Stream consumer — processes game events and persists memorable ones.

Runs as a background asyncio task alongside the FastAPI app. Consumes
from the unified 'events:all' stream (published by the Event Log Service)
using the 'memory-service' consumer group.

Each message is processed exactly once: if processing succeeds the message
is acknowledged (XACK); if it fails the message is left unacknowledged so
it will be retried by the next consumer read or a pending-entry claim job.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from redis.exceptions import ResponseError

from app import database, embeddings
from app.config import settings
from app.event_handlers import event_to_memory

logger = logging.getLogger(__name__)


async def _get_redis():
    import redis.asyncio as aioredis
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def _ensure_group(client, stream_key: str, group: str) -> None:
    """Create the consumer group if it does not already exist."""
    try:
        await client.xgroup_create(stream_key, group, id="0", mkstream=True)
        logger.info("Created consumer group '%s' on stream '%s'", group, stream_key)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def run(stop_event: asyncio.Event | None = None) -> None:
    """Consume the unified events stream until stop_event is set (or forever)."""
    client = await _get_redis()
    stream_key = settings.stream_key
    group = settings.consumer_group
    consumer = f"memory-service-{os.getenv('HOSTNAME', 'local')}"

    await _ensure_group(client, stream_key, group)
    logger.info("Stream consumer started — stream: '%s', consumer: '%s'", stream_key, consumer)

    pool = await database.get_pool()
    iteration = 0

    while not (stop_event and stop_event.is_set()):
        try:
            # Every 10 iterations, reclaim messages that have been pending > 60 s
            # (i.e. were delivered but never acknowledged, likely due to a prior crash).
            iteration += 1
            if iteration % 10 == 0:
                await _reclaim_pending(client, stream_key, group, consumer, pool)

            results = await client.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={stream_key: ">"},
                count=10,
                block=1000,  # block up to 1 s waiting for new messages
            )
            for _stream, messages in (results or []):
                for msg_id, msg_data in messages:
                    await _process(pool, client, stream_key, group, msg_id, msg_data)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Stream consumer error: %s — retrying in 5 s", exc)
            await asyncio.sleep(5)

    logger.info("Stream consumer stopped")


async def _process(pool, client, stream_key, group, msg_id, msg_data) -> None:
    try:
        event = json.loads(msg_data.get("data", "{}"))
        memory_in = event_to_memory(event)

        if memory_in:
            embedding = embeddings.embed(memory_in.content)
            async with pool.acquire() as conn:
                await database.insert_memory(conn, memory_in, embedding)
            logger.debug(
                "Stored memory from '%s' (campaign %s)",
                event.get("event_type"), event.get("campaign_id"),
            )

        await client.xack(stream_key, group, msg_id)

    except Exception as exc:
        logger.error("Failed to process message %s: %s", msg_id, exc)
        # Do not XACK — message stays pending and will be reclaimed by _reclaim_pending


async def _reclaim_pending(
    client, stream_key: str, group: str, consumer: str, pool
) -> None:
    """Claim and reprocess messages that have been pending for > 60 s.

    Uses XAUTOCLAIM (Redis 7.0+). Messages from crashed consumers are
    transferred to this consumer and reprocessed.
    """
    try:
        result = await client.xautoclaim(
            stream_key, group, consumer,
            min_idle_time=60_000,  # 60 seconds in milliseconds
            start_id="0-0",
            count=10,
        )
        # xautoclaim returns [next_start_id, [(id, data), ...], [deleted_ids]]
        messages = result[1] if result and len(result) > 1 else []
        if messages:
            logger.info("Reclaiming %d pending message(s)", len(messages))
        for msg_id, msg_data in messages:
            await _process(pool, client, stream_key, group, msg_id, msg_data)
    except Exception as exc:
        logger.warning("Pending message reclaim failed: %s", exc)
