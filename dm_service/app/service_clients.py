"""Fail-graceful HTTP clients for DM Service orchestration dependencies."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx

from app.config import settings

logger = logging.getLogger(__name__)
_TIMEOUT = 3.0


async def _get(url: str, params: dict[str, Any] | None = None) -> Any | None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("GET %s failed: %s", url, exc)
        return None


async def _post(url: str, payload: dict[str, Any], params: dict[str, Any] | None = None) -> Any | None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload, params=params)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json() if resp.content else None
    except Exception as exc:
        logger.warning("POST %s failed: %s", url, exc)
        return None


async def _patch(url: str, payload: dict[str, Any], params: dict[str, Any] | None = None) -> Any | None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.patch(url, json=payload, params=params)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json() if resp.content else None
    except Exception as exc:
        logger.warning("PATCH %s failed: %s", url, exc)
        return None


async def get_story_context(campaign_id: UUID) -> dict[str, Any]:
    data = await _get(f"{settings.story_state_url}/context", params={"campaign_id": str(campaign_id)})
    return data or {"campaign_id": str(campaign_id), "active_quests": [], "open_hooks": [], "recent_log": []}


async def get_character_state(campaign_id: UUID, character_id: UUID) -> dict[str, Any] | None:
    return await _get(
        f"{settings.world_state_url}/characters/{character_id}",
        params={"campaign_id": str(campaign_id)},
    )


async def get_active_encounter(campaign_id: UUID) -> dict[str, Any] | None:
    return await _get(f"{settings.combat_engine_url}/combat", params={"campaign_id": str(campaign_id)})


async def get_map_snapshot(campaign_id: UUID, character_id: UUID, map_hint_id: UUID | None) -> dict[str, Any] | None:
    if map_hint_id:
        return await _get(
            f"{settings.map_service_url}/maps/{map_hint_id}/snapshot",
            params={"campaign_id": str(campaign_id), "character_id": str(character_id)},
        )
    return await _get(
        f"{settings.map_service_url}/maps/active/snapshot",
        params={"campaign_id": str(campaign_id), "character_id": str(character_id)},
    )


async def get_npc_context(campaign_id: UUID, character_id: UUID) -> dict[str, Any] | None:
    return await _get(
        f"{settings.npc_service_url}/context",
        params={"campaign_id": str(campaign_id), "character_id": str(character_id)},
    )


async def recall_memories(campaign_id: UUID, character_id: UUID, query: str, limit: int = 5) -> list[dict[str, Any]]:
    data = await _get(
        f"{settings.memory_service_url}/memories/recall",
        params={
            "campaign_id": str(campaign_id),
            "subject_id": str(character_id),
            "query": query,
            "top_k": limit,
        },
    )
    if not data:
        return []
    if isinstance(data, dict):
        return data.get("memories", [])
    return data


async def list_recent_events(campaign_id: UUID, session_id: UUID, limit: int = 20) -> list[dict[str, Any]]:
    data = await _get(
        f"{settings.event_log_url}/events",
        params={"campaign_id": str(campaign_id), "session_id": str(session_id), "limit": limit},
    )
    return data or []


async def health_check(url: str) -> bool:
    data = await _get(url)
    return bool(data) and data.get("status") in {"ok", "degraded"}


async def append_story_log(
    campaign_id: UUID,
    session_id: UUID,
    user_id: UUID,
    entry_type: str,
    content: str,
) -> dict[str, Any] | None:
    return await _post(
        f"{settings.story_state_url}/story-log",
        {
            "entries": [
                {
                    "campaign_id": str(campaign_id),
                    "session_id": str(session_id),
                    "entry_type": entry_type,
                    "content": content,
                }
            ],
            "meta": {
                "campaign_id": str(campaign_id),
                "session_id": str(session_id),
                "user_id": str(user_id),
            },
        },
    )

async def create_story_hook(
    campaign_id: UUID,
    session_id: UUID,
    user_id: UUID,
    content: str,
    priority: str = "medium",
) -> dict[str, Any] | None:
    return await _post(
        f"{settings.story_state_url}/hooks",
        {
            "campaign_id": str(campaign_id),
            "content": content,
            "priority": priority,
            "meta": {
                "campaign_id": str(campaign_id),
                "session_id": str(session_id),
                "user_id": str(user_id),
            },
        },
    )

async def update_story_hook(
    campaign_id: UUID,
    session_id: UUID,
    user_id: UUID,
    hook_id: UUID,
    updates: dict[str, Any],
) -> dict[str, Any] | None:
    payload = {
        **updates,
        "meta": {
            "campaign_id": str(campaign_id),
            "session_id": str(session_id),
            "user_id": str(user_id),
        },
    }
    return await _patch(
        f"{settings.story_state_url}/hooks/{hook_id}",
        payload,
        params={"campaign_id": str(campaign_id)},
    )


async def update_world_flags(
    campaign_id: UUID,
    session_id: UUID,
    user_id: UUID,
    flags: dict[str, Any],
) -> dict[str, Any] | None:
    return await _patch(
        f"{settings.world_state_url}/world/flags",
        {
            "flags": flags,
            "event_meta": {
                "session_id": str(session_id),
                "user_id": str(user_id),
            },
        },
        params={"campaign_id": str(campaign_id)},
    )

async def select_active_map(
    campaign_id: UUID,
    session_id: UUID,
    user_id: UUID,
    map_id: UUID,
    character_id: UUID | None = None,
) -> dict[str, Any] | None:
    payload: dict[str, Any] = {
        "campaign_id": str(campaign_id),
        "map_id": str(map_id),
        "meta": {
            "session_id": str(session_id),
            "user_id": str(user_id),
        },
    }
    if character_id is not None:
        payload["character_id"] = str(character_id)
    return await _put(f"{settings.map_service_url}/maps/active", payload)

async def patch_map_fog(
    campaign_id: UUID,
    session_id: UUID,
    user_id: UUID,
    map_id: UUID,
    character_id: UUID,
    add_cells: list[str],
) -> dict[str, Any] | None:
    return await _patch(
        f"{settings.map_service_url}/maps/{map_id}/fog",
        {
            "campaign_id": str(campaign_id),
            "character_id": str(character_id),
            "add_cells": add_cells,
            "meta": {
                "session_id": str(session_id),
                "user_id": str(user_id),
            },
        },
    )

async def upsert_map_token(
    campaign_id: UUID,
    session_id: UUID,
    user_id: UUID,
    map_id: UUID,
    aggregate_id: UUID,
    aggregate_type: str,
    x: int,
    y: int,
    encounter_id: UUID | None = None,
    visible: bool = True,
) -> dict[str, Any] | None:
    payload: dict[str, Any] = {
        "campaign_id": str(campaign_id),
        "aggregate_id": str(aggregate_id),
        "aggregate_type": aggregate_type,
        "x": x,
        "y": y,
        "visible": visible,
        "meta": {
            "session_id": str(session_id),
            "user_id": str(user_id),
        },
    }
    if encounter_id is not None:
        payload["encounter_id"] = str(encounter_id)
    return await _put(f"{settings.map_service_url}/maps/{map_id}/tokens", payload)

async def _put(url: str, payload: dict[str, Any], params: dict[str, Any] | None = None) -> Any | None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.put(url, json=payload, params=params)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json() if resp.content else None
    except Exception as exc:
        logger.warning("PUT %s failed: %s", url, exc)
        return None


async def execute_combat_action(
    action: str,
    campaign_id: UUID,
    session_id: UUID,
    user_id: UUID,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    payload = payload or {}
    routes = {
        "start_combat": "/combat/start",
        "next_turn": "/combat/next-turn",
        "attack": "/combat/attack",
        "move": "/combat/move",
        "apply_condition": "/combat/conditions/apply",
        "remove_condition": "/combat/conditions/remove",
        "dash": "/combat/dash",
        "disengage": "/combat/disengage",
        "dodge": "/combat/dodge",
        "help": "/combat/help",
        "hide": "/combat/hide",
        "ready": "/combat/ready",
        "opportunity_attack": "/combat/opportunity-attack",
        "death_save": "/combat/death-save",
        "grapple": "/combat/grapple",
        "shove": "/combat/shove",
        "spell_cast": "/combat/spell-cast",
        "end_combat": "/combat/end",
    }
    route = routes.get(action)
    if route is None:
        raise ValueError(f"Unsupported combat action: {action}")

    if action == "end_combat":
        # Combat end is DELETE with query params.
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.delete(
                    f"{settings.combat_engine_url}{route}",
                    params={
                        "campaign_id": str(campaign_id),
                        "session_id": str(session_id),
                        "user_id": str(user_id),
                    },
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return {"status": "ended"}
        except Exception as exc:
            logger.warning("DELETE %s failed: %s", route, exc)
            return None

    return await _post(
        f"{settings.combat_engine_url}{route}",
        {
            "campaign_id": str(campaign_id),
            "session_id": str(session_id),
            "user_id": str(user_id),
            **payload,
        },
    )