"""Fail-graceful HTTP clients for upstream services.

All functions return None / empty collections on any error so that
NPC context assembly degrades gracefully when a dependency is down.
"""
from __future__ import annotations

import logging
from uuid import UUID

import httpx

from app.config import settings

logger = logging.getLogger(__name__)
_TIMEOUT = 3.0


async def get_npc_disposition(
    npc_id: UUID,
    character_id: UUID,
    campaign_id: UUID,
) -> tuple[int | None, str | None]:
    """Return (score, notes) for this NPC toward character_id, or (None, None)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{settings.world_state_url}/npcs/{npc_id}/dispositions",
                params={"campaign_id": str(campaign_id)},
            )
            resp.raise_for_status()
            data = resp.json()
            for d in data.get("dispositions", []):
                if d["character_id"] == str(character_id):
                    return int(d["score"]), d.get("notes") or None
            return None, None
    except Exception as exc:
        logger.warning("get_npc_disposition failed: %s", exc)
        return None, None


async def get_faction_standing(faction_id: UUID, campaign_id: UUID) -> int | None:
    """Return the faction standing_score, or None on failure."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{settings.world_state_url}/factions/{faction_id}",
                params={"campaign_id": str(campaign_id)},
            )
            resp.raise_for_status()
            data = resp.json()
            score = data.get("standing_score")
            return int(score) if score is not None else None
    except Exception as exc:
        logger.warning("get_faction_standing failed: %s", exc)
        return None


async def get_quest_map(campaign_id: UUID) -> dict[str, str]:
    """Return {quest_title: status} for all visible quests, or {} on failure."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{settings.story_state_url}/quests",
                params={"campaign_id": str(campaign_id)},
            )
            resp.raise_for_status()
            return {q["title"]: q["status"] for q in resp.json()}
    except Exception as exc:
        logger.warning("get_quest_map failed: %s", exc)
        return {}


async def recall_memories(
    npc_id: UUID,
    campaign_id: UUID,
    query: str,
    limit: int = 5,
) -> str | None:
    """Return a newline-joined memory summary string, or None on failure."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{settings.memory_service_url}/memories/recall",
                params={
                    "campaign_id": str(campaign_id),
                    "subject_id":  str(npc_id),
                    "query":       query,
                    "limit":       limit,
                },
            )
            resp.raise_for_status()
            memories = resp.json()
            if not memories:
                return None
            return "\n".join(m["content"] for m in memories)
    except Exception as exc:
        logger.warning("recall_memories failed: %s", exc)
        return None
