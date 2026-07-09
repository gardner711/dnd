"""FastAPI application — World State Service."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from app import database, event_log
from app.dependencies import get_db_conn
from app.models import (
    CharacterCreate, CharacterState, CharacterUpdate,
    DispositionRecord, DispositionUpdate, DispositionsResponse,
    EncounterCreate, EncounterState, EncounterUpdate,
    FactionStandingRecord, FactionsResponse, FactionUpdate,
    WorldFlagsResponse, WorldFlagsUpdate,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await database.run_migrations(conn)
    logger.info("World State Service ready")
    yield
    await database.close_pool()
    logger.info("World State Service stopped")


app = FastAPI(
    title="World State Service",
    description="Authoritative mutable game state — characters, NPCs, world flags, encounter, factions",
    version="0.1.0",
    lifespan=lifespan,
)

DbConn = Annotated[asyncpg.Connection, Depends(get_db_conn)]


# ── Health ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> JSONResponse:
    db_ok = False
    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception as exc:
        logger.warning("Health: database unavailable: %s", exc)

    overall = "ok" if db_ok else "degraded"
    return JSONResponse(
        content={"status": overall, "service": "world-state-service", "checks": {"database": db_ok}},
        status_code=200 if db_ok else 503,
    )


# ── Characters ─────────────────────────────────────────────────────────────

@app.get("/characters", response_model=list[CharacterState])
async def list_characters(
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> list[CharacterState]:
    """List all characters in a campaign. Used by Combat Engine and Session API."""
    return await database.list_characters(conn, campaign_id)


@app.get("/characters/{character_id}", response_model=CharacterState)
async def get_character(
    character_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> CharacterState:
    """Fetch the full runtime state for a character."""
    result = await database.get_character(conn, character_id, campaign_id)
    if result is None:
        raise HTTPException(404, "Character not found in this campaign")
    return result


@app.put("/characters/{character_id}", response_model=CharacterState, status_code=201)
async def put_character(
    character_id: UUID,
    body: CharacterCreate,
    conn: DbConn,
) -> CharacterState:
    """Create or fully replace a character's runtime state."""
    return await database.upsert_character(conn, character_id, body)


@app.patch("/characters/{character_id}", response_model=CharacterState)
async def patch_character(
    character_id: UUID,
    body: CharacterUpdate,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> CharacterState:
    """Partially update a character state.

    Atomic: uses SELECT FOR UPDATE so concurrent HP changes are serialised,
    not silently dropped. Provide expected_updated_at for explicit version
    checking (returns 409 if the row changed since you last read it).
    """
    old_hp: int = 0
    updated: CharacterState

    async with conn.transaction():
        current = await database.get_character_for_update(conn, character_id, campaign_id)
        if current is None:
            raise HTTPException(404, "Character not found in this campaign")

        if body.expected_updated_at is not None and current.updated_at != body.expected_updated_at:
            raise HTTPException(
                409,
                "Character state was modified by another request. Fetch the latest and retry.",
            )

        old_hp = current.current_hp
        current_dict = current.model_dump()
        update_dict = body.model_dump(exclude_unset=True)
        update_dict.pop("event_meta", None)
        update_dict.pop("expected_updated_at", None)
        merged = {**current_dict, **update_dict}
        updated = await database.upsert_character(
            conn, character_id, _state_to_create(CharacterState(**merged))
        )

    if body.event_meta and updated.current_hp != old_hp:
        meta = body.event_meta
        await event_log.emit(
            event_type="combat.state_changed",
            aggregate_id=str(character_id),
            aggregate_type="character",
            campaign_id=str(campaign_id),
            session_id=meta.session_id,
            user_id=meta.user_id,
            payload={"old_hp": old_hp, "new_hp": updated.current_hp,
                     "combatant_name": updated.name},
        )
    return updated


@app.delete("/characters/{character_id}", status_code=204)
async def delete_character(
    character_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> None:
    deleted = await database.delete_character(conn, character_id, campaign_id)
    if not deleted:
        raise HTTPException(404, "Character not found in this campaign")


# ── NPC Dispositions ────────────────────────────────────────────────────────

@app.get("/npcs/{npc_id}/dispositions", response_model=DispositionsResponse)
async def get_dispositions(
    npc_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> DispositionsResponse:
    """Get all disposition scores for this NPC across all characters."""
    return await database.get_npc_dispositions(conn, npc_id, campaign_id)


@app.patch("/npcs/{npc_id}/dispositions", response_model=DispositionRecord)
async def update_disposition(
    npc_id: UUID,
    body: DispositionUpdate,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> DispositionRecord:
    """Set the disposition score between this NPC and one character."""
    record = await database.upsert_npc_disposition(
        conn, npc_id, campaign_id, body.character_id, body.score
    )
    if body.event_meta:
        meta = body.event_meta
        await event_log.emit(
            event_type="npc.disposition_changed",
            aggregate_id=str(npc_id),
            aggregate_type="npc",
            campaign_id=str(campaign_id),
            session_id=meta.session_id,
            user_id=meta.user_id,
            payload={
                "npc_id": str(npc_id),
                "character_id": str(body.character_id),
                "new_score": body.score,
                "reason": body.reason,
            },
        )
    return record


# ── World Flags ─────────────────────────────────────────────────────────────

@app.get("/world/flags", response_model=WorldFlagsResponse)
async def get_flags(
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> WorldFlagsResponse:
    flags = await database.get_world_flags(conn, campaign_id)
    return WorldFlagsResponse(campaign_id=campaign_id, flags=flags)


@app.get("/world/flags/{key}")
async def get_flag(
    key: str,
    conn: DbConn,
    campaign_id: UUID = Query(...),
):
    """Fetch a single world flag value. Returns the raw JSON value."""
    value = await database.get_world_flag(conn, campaign_id, key)
    if value is None:
        raise HTTPException(404, f"Flag '{key}' not found")
    return {"key": key, "value": value}


@app.patch("/world/flags", response_model=WorldFlagsResponse)
async def update_flags(
    body: WorldFlagsUpdate,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> WorldFlagsResponse:
    """Upsert any number of flags in one call."""
    flags = await database.upsert_world_flags(conn, campaign_id, body.flags)
    if body.event_meta:
        meta = body.event_meta
        for key, value in body.flags.items():
            await event_log.emit(
                event_type="world.state_changed",
                aggregate_id=str(campaign_id),
                aggregate_type="world",
                campaign_id=str(campaign_id),
                session_id=meta.session_id,
                user_id=meta.user_id,
                payload={"key": key, "value": value},
            )
    return WorldFlagsResponse(campaign_id=campaign_id, flags=flags)


@app.delete("/world/flags/{key}", status_code=204)
async def delete_flag(
    key: str,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> None:
    deleted = await database.delete_world_flag(conn, campaign_id, key)
    if not deleted:
        raise HTTPException(404, f"Flag '{key}' not found")


# ── Encounter ───────────────────────────────────────────────────────────────

@app.get("/encounter", response_model=EncounterState)
async def get_encounter(
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> EncounterState:
    result = await database.get_encounter(conn, campaign_id)
    if result is None:
        raise HTTPException(404, "No active encounter for this campaign")
    return result


@app.put("/encounter", response_model=EncounterState, status_code=201)
async def create_encounter(body: EncounterCreate, conn: DbConn) -> EncounterState:
    """Start a new encounter. Fails if one already exists (DELETE it first)."""
    try:
        return await database.create_encounter(conn, body)
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "An active encounter already exists. DELETE it first.")


@app.patch("/encounter", response_model=EncounterState)
async def update_encounter(
    body: EncounterUpdate,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> EncounterState:
    """Update turn, round, or combatant states. Requires expected_updated_at for optimistic concurrency."""
    combatant_json = (
        {k: v.model_dump() for k, v in body.combatant_states.items()}
        if body.combatant_states else None
    )
    result = await database.update_encounter(
        conn, campaign_id, body.expected_updated_at,
        body.round, body.current_turn_index, combatant_json,
    )
    if result is None:
        raise HTTPException(
            409,
            "Encounter was modified by another request. Fetch the latest state and retry.",
        )
    if body.event_meta:
        meta = body.event_meta
        await event_log.emit(
            event_type="combat.state_changed",
            aggregate_id=str(result.encounter_id),
            aggregate_type="combat",
            campaign_id=str(campaign_id),
            session_id=meta.session_id,
            user_id=meta.user_id,
            payload={"round": result.round, "current_turn_index": result.current_turn_index},
        )
    return result


@app.delete("/encounter", status_code=204)
async def delete_encounter(
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> None:
    deleted = await database.delete_encounter(conn, campaign_id)
    if not deleted:
        raise HTTPException(404, "No active encounter for this campaign")


# ── Factions ────────────────────────────────────────────────────────────────

@app.get("/factions", response_model=FactionsResponse)
async def get_factions(
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> FactionsResponse:
    factions = await database.get_factions(conn, campaign_id)
    return FactionsResponse(campaign_id=campaign_id, factions=factions)


@app.patch("/factions/{faction_id}", response_model=FactionStandingRecord)
async def update_faction(
    faction_id: str,
    body: FactionUpdate,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> FactionStandingRecord:
    return await database.upsert_faction(conn, campaign_id, faction_id, body.standing)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _state_to_create(state: CharacterState) -> CharacterCreate:
    """Convert a full CharacterState back to a CharacterCreate for upsert."""
    data = state.model_dump(exclude={"character_id", "updated_at"})
    return CharacterCreate(**data)
