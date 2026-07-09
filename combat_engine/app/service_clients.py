"""HTTP clients for the Rules Engine and World State Service.

Rules Engine calls raise HTTPException(502) on failure — combat actions
cannot proceed without deterministic rule resolution.

World State calls also raise on failure, except ``update_encounter`` which
returns ``None`` on a 409 optimistic-concurrency conflict so the caller can
propagate a clean 409 to the client.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

_RE_TIMEOUT = 5.0    # Rules Engine — synchronous computation, should be fast
_WS_TIMEOUT = 5.0    # World State — simple DB reads/writes
_MAP_TIMEOUT = 5.0   # Map Service — simple DB reads/writes


# ── Rules Engine ─────────────────────────────────────────────────────────────

async def roll_initiative(combatants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the ordered initiative list from the Rules Engine.

    Each entry: ``{combatant_id, combatant_name, total, dexterity_modifier, roll}``.
    """
    try:
        async with httpx.AsyncClient(timeout=_RE_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.rules_engine_url}/initiative",
                json={"combatants": combatants},
            )
            resp.raise_for_status()
            return resp.json()["order"]
    except httpx.HTTPStatusError as exc:
        logger.error("Rules Engine /initiative HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"Rules Engine error: {exc.response.status_code}") from exc
    except Exception as exc:
        logger.error("Rules Engine /initiative unreachable: %s", exc)
        raise HTTPException(502, "Rules Engine unavailable") from exc


async def resolve_ability_check(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve an ability/skill check via the Rules Engine."""
    try:
        async with httpx.AsyncClient(timeout=_RE_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.rules_engine_url}/ability-check",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Rules Engine /ability-check HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"Rules Engine error: {exc.response.status_code}") from exc
    except Exception as exc:
        logger.error("Rules Engine /ability-check unreachable: %s", exc)
        raise HTTPException(502, "Rules Engine unavailable") from exc


async def resolve_attack(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve a weapon attack via the Rules Engine and return the full result dict."""
    try:
        async with httpx.AsyncClient(timeout=_RE_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.rules_engine_url}/attack",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Rules Engine /attack HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"Rules Engine error: {exc.response.status_code}") from exc
    except Exception as exc:
        logger.error("Rules Engine /attack unreachable: %s", exc)
        raise HTTPException(502, "Rules Engine unavailable") from exc


async def validate_movement(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a movement action via the Rules Engine."""
    try:
        async with httpx.AsyncClient(timeout=_RE_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.rules_engine_url}/movement/validate",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Rules Engine /movement/validate HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"Rules Engine error: {exc.response.status_code}") from exc
    except Exception as exc:
        logger.error("Rules Engine /movement/validate unreachable: %s", exc)
        raise HTTPException(502, "Rules Engine unavailable") from exc


async def resolve_concentration_check(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve a concentration check via the Rules Engine."""
    try:
        async with httpx.AsyncClient(timeout=_RE_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.rules_engine_url}/concentration-check",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Rules Engine /concentration-check HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"Rules Engine error: {exc.response.status_code}") from exc
    except Exception as exc:
        logger.error("Rules Engine /concentration-check unreachable: %s", exc)
        raise HTTPException(502, "Rules Engine unavailable") from exc


async def resolve_grapple(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve a grapple attempt via the Rules Engine."""
    try:
        async with httpx.AsyncClient(timeout=_RE_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.rules_engine_url}/grapple",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Rules Engine /grapple HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"Rules Engine error: {exc.response.status_code}") from exc
    except Exception as exc:
        logger.error("Rules Engine /grapple unreachable: %s", exc)
        raise HTTPException(502, "Rules Engine unavailable") from exc


async def resolve_shove(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve a shove attempt via the Rules Engine."""
    try:
        async with httpx.AsyncClient(timeout=_RE_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.rules_engine_url}/shove",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Rules Engine /shove HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"Rules Engine error: {exc.response.status_code}") from exc
    except Exception as exc:
        logger.error("Rules Engine /shove unreachable: %s", exc)
        raise HTTPException(502, "Rules Engine unavailable") from exc


async def validate_spell_cast(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a spell cast via the Rules Engine."""
    try:
        async with httpx.AsyncClient(timeout=_RE_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.rules_engine_url}/spell/validate",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Rules Engine /spell/validate HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"Rules Engine error: {exc.response.status_code}") from exc
    except Exception as exc:
        logger.error("Rules Engine /spell/validate unreachable: %s", exc)
        raise HTTPException(502, "Rules Engine unavailable") from exc


async def resolve_death_save(combatant_id: str, successes: int, failures: int) -> dict[str, Any]:
    """Roll a death saving throw via the Rules Engine."""
    try:
        async with httpx.AsyncClient(timeout=_RE_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.rules_engine_url}/death-save",
                json={
                    "combatant_id": combatant_id,
                    "current_successes": successes,
                    "current_failures": failures,
                },
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Rules Engine /death-save HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"Rules Engine error: {exc.response.status_code}") from exc
    except Exception as exc:
        logger.error("Rules Engine /death-save unreachable: %s", exc)
        raise HTTPException(502, "Rules Engine unavailable") from exc


# ── World State — Encounter ───────────────────────────────────────────────────

async def get_encounter(campaign_id: UUID) -> dict[str, Any] | None:
    """Return the active encounter dict, or None if no encounter exists (404)."""
    try:
        async with httpx.AsyncClient(timeout=_WS_TIMEOUT) as client:
            resp = await client.get(
                f"{settings.world_state_url}/encounter",
                params={"campaign_id": str(campaign_id)},
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("World State GET /encounter HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"World State error: {exc.response.status_code}") from exc
    except Exception as exc:
        logger.error("World State GET /encounter unreachable: %s", exc)
        raise HTTPException(502, "World State unavailable") from exc


async def create_encounter(campaign_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
    """PUT /encounter — create a new encounter. Propagates 409 if one already exists."""
    try:
        async with httpx.AsyncClient(timeout=_WS_TIMEOUT) as client:
            resp = await client.put(
                f"{settings.world_state_url}/encounter",
                json=payload,
            )
            if resp.status_code == 409:
                raise HTTPException(409, "An active encounter already exists. End it first.")
            resp.raise_for_status()
            return resp.json()
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        logger.error("World State PUT /encounter HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"World State error: {exc.response.status_code}") from exc
    except Exception as exc:
        logger.error("World State PUT /encounter unreachable: %s", exc)
        raise HTTPException(502, "World State unavailable") from exc


async def update_encounter(
    campaign_id: UUID, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """PATCH /encounter. Returns ``None`` on 409 (optimistic-concurrency conflict)."""
    try:
        async with httpx.AsyncClient(timeout=_WS_TIMEOUT) as client:
            resp = await client.patch(
                f"{settings.world_state_url}/encounter",
                params={"campaign_id": str(campaign_id)},
                json=payload,
            )
            if resp.status_code == 409:
                return None
            if resp.status_code == 404:
                raise HTTPException(404, "No active encounter for this campaign")
            resp.raise_for_status()
            return resp.json()
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        logger.error("World State PATCH /encounter HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"World State error: {exc.response.status_code}") from exc
    except Exception as exc:
        logger.error("World State PATCH /encounter unreachable: %s", exc)
        raise HTTPException(502, "World State unavailable") from exc


async def delete_encounter(campaign_id: UUID) -> bool:
    """DELETE /encounter. Returns False on 404."""
    try:
        async with httpx.AsyncClient(timeout=_WS_TIMEOUT) as client:
            resp = await client.delete(
                f"{settings.world_state_url}/encounter",
                params={"campaign_id": str(campaign_id)},
            )
            if resp.status_code == 404:
                return False
            resp.raise_for_status()
            return True
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        logger.error("World State DELETE /encounter HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"World State error: {exc.response.status_code}") from exc
    except Exception as exc:
        logger.error("World State DELETE /encounter unreachable: %s", exc)
        raise HTTPException(502, "World State unavailable") from exc


# ── World State — Characters ──────────────────────────────────────────────────

async def get_character(character_id: UUID, campaign_id: UUID) -> dict[str, Any] | None:
    """Return a character state dict, or None on 404."""
    try:
        async with httpx.AsyncClient(timeout=_WS_TIMEOUT) as client:
            resp = await client.get(
                f"{settings.world_state_url}/characters/{character_id}",
                params={"campaign_id": str(campaign_id)},
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        logger.error("World State GET /characters HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"World State error: {exc.response.status_code}") from exc
    except Exception as exc:
        logger.error("World State GET /characters unreachable: %s", exc)
        raise HTTPException(502, "World State unavailable") from exc


async def patch_character(
    character_id: UUID, campaign_id: UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH /characters/{id}. Raises on any error including 409."""
    try:
        async with httpx.AsyncClient(timeout=_WS_TIMEOUT) as client:
            resp = await client.patch(
                f"{settings.world_state_url}/characters/{character_id}",
                params={"campaign_id": str(campaign_id)},
                json=payload,
            )
            if resp.status_code == 404:
                raise HTTPException(404, f"Character {character_id} not found in this campaign")
            if resp.status_code == 409:
                raise HTTPException(409, "Character state was concurrently modified. Retry.")
            resp.raise_for_status()
            return resp.json()
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        logger.error("World State PATCH /characters HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"World State error: {exc.response.status_code}") from exc
    except Exception as exc:
        logger.error("World State PATCH /characters unreachable: %s", exc)
        raise HTTPException(502, "World State unavailable") from exc


# ── Map Service — best effort token sync ─────────────────────────────────────

async def upsert_map_token_best_effort(map_id: UUID, payload: dict[str, Any]) -> None:
    """Best-effort token sync to the Map Service; logs and returns on any failure."""
    try:
        async with httpx.AsyncClient(timeout=_MAP_TIMEOUT) as client:
            resp = await client.put(
                f"{settings.map_service_url}/maps/{map_id}/tokens",
                json=payload,
            )
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("Map Service token upsert failed for map %s: %s", map_id, exc)


async def list_map_tokens_best_effort(
    map_id: UUID,
    campaign_id: UUID,
    encounter_id: UUID | None,
) -> list[dict[str, Any]]:
    """Best-effort token listing. Returns [] on any failure."""
    try:
        async with httpx.AsyncClient(timeout=_MAP_TIMEOUT) as client:
            resp = await client.get(
                f"{settings.map_service_url}/maps/{map_id}/tokens",
                params={
                    "campaign_id": str(campaign_id),
                    **({"encounter_id": str(encounter_id)} if encounter_id else {}),
                },
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("Map Service token list failed for map %s: %s", map_id, exc)
        return []


async def delete_map_token_best_effort(map_id: UUID, token_id: UUID, campaign_id: UUID) -> None:
    """Best-effort token deletion. Logs and returns on any failure."""
    try:
        async with httpx.AsyncClient(timeout=_MAP_TIMEOUT) as client:
            resp = await client.delete(
                f"{settings.map_service_url}/maps/{map_id}/tokens/{token_id}",
                params={"campaign_id": str(campaign_id)},
            )
            if resp.status_code not in (204, 404):
                resp.raise_for_status()
    except Exception as exc:
        logger.warning("Map Service token delete failed for map %s token %s: %s", map_id, token_id, exc)
