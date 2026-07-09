"""FastAPI application — NPC Interaction Service."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Optional
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from app import database, event_log, prompt_builder, redis_client, service_clients
from app.dependencies import get_db_conn
from app.models import (
    DialogueAppend, DialogueTurn,
    NPCContextRequest, NPCContextResponse,
    NPCCreate, NPCOut, NPCUpdate,
    SecretCreate, SecretOut, SecretSummary, SecretUpdate,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await database.run_migrations(conn)
    logger.info("NPC Service ready")
    yield
    await database.close_pool()
    await redis_client.close_redis()
    logger.info("NPC Service stopped")


app = FastAPI(
    title="NPC Interaction Service",
    description="NPC profiles, secrets, dialogue history, and prompt assembly",
    version="0.1.0",
    lifespan=lifespan,
)

DbConn = Annotated[asyncpg.Connection, Depends(get_db_conn)]


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> JSONResponse:
    db_ok = redis_ok = False
    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception as exc:
        logger.warning("Health: DB unavailable: %s", exc)
    try:
        r = await redis_client.get_redis()
        await r.ping()
        redis_ok = True
    except Exception as exc:
        logger.warning("Health: Redis unavailable: %s", exc)
    overall = "ok" if (db_ok and redis_ok) else "degraded"
    return JSONResponse(
        content={
            "status": overall,
            "service": "npc-service",
            "checks": {"database": db_ok, "redis": redis_ok},
        },
        status_code=200 if overall == "ok" else 503,
    )


# ── NPC Profiles ──────────────────────────────────────────────────────────────

@app.post("/npcs", status_code=201)
async def create_npc(body: NPCCreate, conn: DbConn) -> NPCOut:
    row = await database.create_npc(
        conn,
        campaign_id=body.campaign_id,
        name=body.name,
        role=body.role,
        physical_description=body.physical_description,
        personality_prompt=body.personality_prompt,
        faction_id=body.faction_id,
    )
    await event_log.emit(
        event_type="npc.created",
        aggregate_id=str(row["npc_id"]),
        aggregate_type="npc",
        campaign_id=str(body.campaign_id),
        session_id=str(body.meta.session_id) if body.meta and body.meta.session_id else None,
        user_id=str(body.meta.user_id) if body.meta and body.meta.user_id else None,
        payload={"npc_id": str(row["npc_id"]), "name": body.name, "role": body.role},
    )
    return NPCOut(**dict(row))


@app.get("/npcs")
async def list_npcs(
    conn: DbConn,
    campaign_id: UUID = Query(...),
    active_only: bool = Query(default=True),
) -> list[NPCOut]:
    rows = await database.list_npcs(conn, campaign_id, active_only=active_only)
    return [NPCOut(**dict(r)) for r in rows]


@app.get("/npcs/{npc_id}")
async def get_npc(
    npc_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> NPCOut:
    row = await database.get_npc(conn, npc_id, campaign_id)
    if row is None:
        raise HTTPException(404, "NPC not found in this campaign")
    return NPCOut(**dict(row))


@app.patch("/npcs/{npc_id}")
async def patch_npc(
    npc_id: UUID,
    body: NPCUpdate,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> NPCOut:
    clear_desc = (
        "physical_description" in body.model_fields_set
        and body.physical_description is None
    )
    row = await database.patch_npc(
        conn, npc_id, campaign_id,
        name=body.name,
        role=body.role,
        physical_description=body.physical_description,
        clear_physical_description=clear_desc or body.clear_physical_description,
        personality_prompt=body.personality_prompt,
        is_active=body.is_active,
        faction_id=body.faction_id,
    )
    if row is None:
        raise HTTPException(404, "NPC not found in this campaign")
    await event_log.emit(
        event_type="npc.updated",
        aggregate_id=str(npc_id),
        aggregate_type="npc",
        campaign_id=str(campaign_id),
        session_id=str(body.meta.session_id) if body.meta and body.meta.session_id else None,
        user_id=str(body.meta.user_id) if body.meta and body.meta.user_id else None,
        payload={"npc_id": str(npc_id)},
    )
    return NPCOut(**dict(row))


@app.delete("/npcs/{npc_id}", status_code=204)
async def deactivate_npc(
    npc_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> None:
    deactivated = await database.deactivate_npc(conn, npc_id, campaign_id)
    if not deactivated:
        raise HTTPException(404, "NPC not found or already inactive")


# ── Secrets (DM-privileged — never appear in player-facing responses) ─────────

@app.post("/npcs/{npc_id}/secrets", status_code=201)
async def add_secret(
    npc_id: UUID,
    body: SecretCreate,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> SecretOut:
    npc = await database.get_npc(conn, npc_id, campaign_id)
    if npc is None:
        raise HTTPException(404, "NPC not found in this campaign")
    row = await database.create_secret(
        conn, npc_id, campaign_id,
        content=body.content,
        condition_type=body.condition_type,
        condition_value=body.condition_value,
        condition_quest_title=body.condition_quest_title,
        condition_quest_status=body.condition_quest_status,
    )
    return SecretOut(**dict(row))


@app.get("/npcs/{npc_id}/secrets")
async def list_secrets(
    npc_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> list[SecretOut]:
    rows = await database.list_secrets(conn, npc_id, campaign_id)
    return [SecretOut(**dict(r)) for r in rows]


@app.patch("/npcs/{npc_id}/secrets/{secret_id}")
async def patch_secret_route(
    npc_id: UUID,
    secret_id: UUID,
    body: SecretUpdate,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> SecretOut:
    row = await database.patch_secret(
        conn, secret_id, npc_id, campaign_id,
        content=body.content,
        condition_type=body.condition_type,
        condition_value=body.condition_value,
        condition_quest_title=body.condition_quest_title,
        condition_quest_status=body.condition_quest_status,
    )
    if row is None:
        raise HTTPException(404, "Secret not found")
    return SecretOut(**dict(row))


@app.delete("/npcs/{npc_id}/secrets/{secret_id}", status_code=204)
async def delete_secret_route(
    npc_id: UUID,
    secret_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> None:
    deleted = await database.delete_secret(conn, secret_id, npc_id, campaign_id)
    if not deleted:
        raise HTTPException(404, "Secret not found")


# ── Context Assembly (hot path — called on every NPC interaction turn) ────────

@app.post("/npcs/{npc_id}/context")
async def assemble_context(
    npc_id: UUID,
    body: NPCContextRequest,
    conn: DbConn,
) -> NPCContextResponse:
    """Build the full prompt context for one NPC interaction turn.

    Calls World State (disposition), Story State (quest map), and Memory
    Service (recalled memories). All three fail gracefully when unavailable.
    """
    npc = await database.get_npc(conn, npc_id, body.campaign_id)
    if npc is None:
        raise HTTPException(404, "NPC not found in this campaign")

    secrets = await database.list_secrets(conn, npc_id, body.campaign_id)

    # External calls — all fail gracefully
    char_score, disposition_notes = await service_clients.get_npc_disposition(
        npc_id, body.character_id, body.campaign_id,
    )
    # Faction disposition fallback: if the NPC belongs to a faction and the
    # player has no direct score, use the faction standing as the baseline.
    faction_standing: int | None = None
    if npc["faction_id"]:
        faction_standing = await service_clients.get_faction_standing(
            npc["faction_id"], body.campaign_id,
        )
    disposition_score = char_score if char_score is not None else faction_standing

    quest_map  = await service_clients.get_quest_map(body.campaign_id)
    memory_ctx = await service_clients.recall_memories(
        npc_id, body.campaign_id, body.player_message, body.memory_limit,
    )

    # Evaluate reveal conditions; track and emit first-time reveals
    applicable: list[dict] = []
    summaries:  list[SecretSummary] = []
    for s in secrets:
        if prompt_builder.evaluate_condition(dict(s), disposition_score, quest_map):
            first = s["revealed_at"] is None
            if first:
                await database.mark_secret_revealed(
                    conn, s["secret_id"], npc_id, body.campaign_id,
                )
                await event_log.emit(
                    event_type="npc.secret_revealed",
                    aggregate_id=str(npc_id),
                    aggregate_type="npc",
                    campaign_id=str(body.campaign_id),
                    session_id=str(body.session_id),
                    user_id=None,
                    payload={
                        "secret_id":     str(s["secret_id"]),
                        "npc_id":        str(npc_id),
                        "condition_type": s["condition_type"],
                    },
                )
            applicable.append(dict(s))
            summaries.append(SecretSummary(
                secret_id=s["secret_id"],
                content=s["content"],
                condition_type=s["condition_type"],
                first_revealed=first,
            ))

    label = prompt_builder.disposition_label(disposition_score)
    system_prompt = prompt_builder.build_system_prompt(
        dict(npc), applicable, memory_ctx, disposition_score, label,
        disposition_notes=disposition_notes,
    )
    history = await redis_client.get_dialogue_history(
        body.campaign_id, npc_id, body.session_id,
        limit=body.dialogue_history_limit,
    )

    return NPCContextResponse(
        npc_id=npc_id,
        npc_name=npc["name"],
        system_prompt=system_prompt,
        dialogue_history=[DialogueTurn(**t) for t in history],
        disposition_score=disposition_score,
        disposition_label=label,
        disposition_notes=disposition_notes,
        faction_standing=faction_standing,
        secrets_injected_count=len(summaries),
        secrets_injected=summaries,
        memory_context=memory_ctx,
    )


# ── Dialogue History ──────────────────────────────────────────────────────────
@app.get("/npcs/{npc_id}/dialogue")
async def get_dialogue(
    npc_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
    session_id: UUID = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[DialogueTurn]:
    """Retrieve dialogue history directly from Redis without triggering context assembly."""
    history = await redis_client.get_dialogue_history(
        campaign_id, npc_id, session_id, limit=limit,
    )
    return [DialogueTurn(**t) for t in history]

@app.post("/npcs/{npc_id}/dialogue", status_code=201)
async def append_dialogue(
    npc_id: UUID,
    body: DialogueAppend,
    conn: DbConn,
) -> JSONResponse:
    """Store a completed turn (player + NPC messages) in Redis."""
    npc = await database.get_npc(conn, npc_id, body.campaign_id)
    if npc is None:
        raise HTTPException(404, "NPC not found in this campaign")
    await redis_client.append_dialogue_turn(
        body.campaign_id, npc_id, body.session_id,
        body.player_message, body.npc_response,
    )
    return JSONResponse(status_code=201, content={"status": "appended"})


@app.delete("/npcs/{npc_id}/dialogue", status_code=204)
async def clear_dialogue(
    npc_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
    session_id: UUID = Query(...),
) -> None:
    """Clear Redis dialogue history for this NPC + session (called at session end)."""
    await redis_client.clear_dialogue(campaign_id, npc_id, session_id)
