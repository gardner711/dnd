"""Redis client for short-term NPC dialogue history.

Key pattern: npc:dialogue:{campaign_id}:{npc_id}:{session_id}
Each key holds a Redis list of JSON-serialised {role, content, ts} objects.
TTL is 24 hours; lists are trimmed to the configured history limit.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None
_DIALOGUE_TTL_SECS = 86400  # 24 hours


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = await aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def _key(campaign_id: UUID, npc_id: UUID, session_id: UUID) -> str:
    return f"npc:dialogue:{campaign_id}:{npc_id}:{session_id}"


async def get_dialogue_history(
    campaign_id: UUID,
    npc_id: UUID,
    session_id: UUID,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return the last `limit` turns as a list of {role, content, ts} dicts."""
    r = await get_redis()
    raw = await r.lrange(_key(campaign_id, npc_id, session_id), -(limit * 2), -1)
    return [json.loads(item) for item in raw]


async def append_dialogue_turn(
    campaign_id: UUID,
    npc_id: UUID,
    session_id: UUID,
    player_message: str,
    npc_response: str,
    history_limit: int = 20,
) -> None:
    """Append player + NPC messages and trim the list to history_limit turns."""
    r = await get_redis()
    key = _key(campaign_id, npc_id, session_id)
    ts = datetime.now(UTC).isoformat()
    pipe = r.pipeline()
    pipe.rpush(key, json.dumps({"role": "player", "content": player_message, "ts": ts}))
    pipe.rpush(key, json.dumps({"role": "npc",    "content": npc_response,  "ts": ts}))
    pipe.ltrim(key, -(history_limit * 2), -1)
    pipe.expire(key, _DIALOGUE_TTL_SECS)
    await pipe.execute()


async def clear_dialogue(
    campaign_id: UUID,
    npc_id: UUID,
    session_id: UUID,
) -> None:
    """Delete the dialogue history key (called at session end)."""
    r = await get_redis()
    await r.delete(_key(campaign_id, npc_id, session_id))
