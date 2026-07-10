"""HTTP clients for downstream services used by API Gateway."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)


async def _request(method: str, url: str, params: dict[str, Any] | None = None, payload: dict[str, Any] | None = None) -> Any:
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            resp = await client.request(method, url, params=params, json=payload)
            if resp.status_code >= 400:
                detail = None
                try:
                    detail = resp.json().get("detail")
                except Exception:
                    detail = resp.text or f"Downstream HTTP {resp.status_code}"
                raise HTTPException(resp.status_code, detail)
            if not resp.content:
                return None
            return resp.json()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Downstream request failed %s %s: %s", method, url, exc)
        raise HTTPException(502, f"Downstream unavailable: {url}") from exc


async def health_check(url: str) -> bool:
    try:
        data = await _request("GET", url)
        return bool(data) and data.get("status") in {"ok", "degraded"}
    except Exception:
        return False


async def get_character(campaign_id: UUID, character_id: UUID) -> dict[str, Any] | None:
    try:
        return await _request("GET", f"{settings.world_state_url}/characters/{character_id}", params={"campaign_id": str(campaign_id)})
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


async def get_story_context(campaign_id: UUID) -> dict[str, Any]:
    try:
        return await _request("GET", f"{settings.story_state_url}/context", params={"campaign_id": str(campaign_id)})
    except HTTPException:
        return {"campaign_id": str(campaign_id), "active_quests": [], "open_hooks": [], "recent_log": []}


async def get_active_encounter(campaign_id: UUID) -> dict[str, Any] | None:
    try:
        return await _request("GET", f"{settings.combat_engine_url}/combat", params={"campaign_id": str(campaign_id)})
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


async def get_map_snapshot(campaign_id: UUID, character_id: UUID, map_hint_id: UUID | None) -> dict[str, Any] | None:
    try:
        if map_hint_id:
            return await _request(
                "GET",
                f"{settings.map_service_url}/maps/{map_hint_id}/snapshot",
                params={"campaign_id": str(campaign_id), "character_id": str(character_id)},
            )
        return await _request(
            "GET",
            f"{settings.map_service_url}/maps/active/snapshot",
            params={"campaign_id": str(campaign_id), "character_id": str(character_id)},
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


async def list_recent_events(campaign_id: UUID, session_id: UUID, limit: int = 20) -> list[dict[str, Any]]:
    try:
        return await _request(
            "GET",
            f"{settings.event_log_url}/events",
            params={"campaign_id": str(campaign_id), "session_id": str(session_id), "limit": limit},
        )
    except HTTPException:
        return []


async def run_dm_turn(
    campaign_id: UUID,
    session_id: UUID,
    user_id: UUID,
    character_id: UUID,
    turn_id: str,
    input_text: str,
    map_hint_id: UUID | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "turn_id": turn_id,
        "campaign_id": str(campaign_id),
        "session_id": str(session_id),
        "user_id": str(user_id),
        "character_id": str(character_id),
        "input_text": input_text,
    }
    if map_hint_id is not None:
        payload["map_hint_id"] = str(map_hint_id)
    return await _request("POST", f"{settings.dm_service_url}/turn", payload=payload)


async def run_combat_action(action: str, campaign_id: UUID, session_id: UUID, user_id: UUID, payload: dict[str, Any]) -> dict[str, Any] | None:
    route_map = {
        "start_combat": ("POST", "/combat/start"),
        "next_turn": ("POST", "/combat/next-turn"),
        "attack": ("POST", "/combat/attack"),
        "move": ("POST", "/combat/move"),
        "apply_condition": ("POST", "/combat/conditions/apply"),
        "remove_condition": ("POST", "/combat/conditions/remove"),
        "dash": ("POST", "/combat/dash"),
        "disengage": ("POST", "/combat/disengage"),
        "dodge": ("POST", "/combat/dodge"),
        "help": ("POST", "/combat/help"),
        "hide": ("POST", "/combat/hide"),
        "ready": ("POST", "/combat/ready"),
        "opportunity_attack": ("POST", "/combat/opportunity-attack"),
        "death_save": ("POST", "/combat/death-save"),
        "grapple": ("POST", "/combat/grapple"),
        "shove": ("POST", "/combat/shove"),
        "spell_cast": ("POST", "/combat/spell-cast"),
        "end_combat": ("DELETE", "/combat/end"),
    }
    item = route_map.get(action)
    if item is None:
        raise HTTPException(400, f"Unsupported combat action: {action}")
    method, route = item
    url = f"{settings.combat_engine_url}{route}"

    if method == "DELETE":
        return await _request(
            "DELETE",
            url,
            params={"campaign_id": str(campaign_id), "session_id": str(session_id), "user_id": str(user_id)},
        )

    composed_payload = {
        "campaign_id": str(campaign_id),
        "session_id": str(session_id),
        "user_id": str(user_id),
        **payload,
    }
    return await _request(method, url, payload=composed_payload)