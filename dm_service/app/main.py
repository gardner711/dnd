"""FastAPI application — Dungeon Master Service."""
from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query

from app import database, event_log, service_clients
from app.dependencies import get_db_conn
from app.dispatcher import dispatch_plan
from app.models import DMContextResponse, HealthChecks, HealthResponse, SideEffectResult, TurnInput, TurnLedgerRecord, TurnResult
from app.planner import plan_turn
from app.prompt_builder import build_prompt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await database.run_migrations(conn)
    logger.info("DM Service ready")
    yield
    await database.close_pool()
    logger.info("DM Service stopped")

app = FastAPI(
    title="Dungeon Master Service",
    description="Coordinates gameplay and assembles context across services",
    version="0.1.0",
    lifespan=lifespan,
)

DbConn = Annotated[asyncpg.Connection, Depends(get_db_conn)]


@app.get("/health", response_model=HealthResponse)
async def health(conn: DbConn) -> HealthResponse:
    db_ok = False
    try:
        await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception as exc:
        logger.warning("Health: database unavailable: %s", exc)

    checks = HealthChecks(
        story_state=await service_clients.health_check(f"{service_clients.settings.story_state_url}/health"),
        world_state=await service_clients.health_check(f"{service_clients.settings.world_state_url}/health"),
        npc_service=await service_clients.health_check(f"{service_clients.settings.npc_service_url}/health"),
        memory_service=await service_clients.health_check(f"{service_clients.settings.memory_service_url}/health"),
        map_service=await service_clients.health_check(f"{service_clients.settings.map_service_url}/health"),
        combat_engine=await service_clients.health_check(f"{service_clients.settings.combat_engine_url}/health"),
        event_log=await service_clients.health_check(f"{service_clients.settings.event_log_url}/health"),
    )
    status = "ok" if (db_ok and all(checks.model_dump().values())) else "degraded"
    return HealthResponse(status=status, service="dm-service", checks=checks, checked_at=datetime.now(UTC))


@app.post("/context", response_model=DMContextResponse)
async def build_context(body: TurnInput) -> DMContextResponse:
    return await _assemble_context(body)


@app.post("/turn", response_model=TurnResult)
async def run_turn(body: TurnInput, conn: DbConn) -> TurnResult:
    existing = await database.get_turn(conn, str(body.campaign_id), str(body.session_id), body.turn_id)
    if existing is not None:
        return TurnResult(
            turn_id=existing.turn_id,
            campaign_id=existing.campaign_id,
            session_id=existing.session_id,
            user_id=existing.user_id,
            narration=existing.narration,
            selected_action=existing.selected_action,
            llm_model=existing.llm_model,
            llm_prompt_hash=existing.llm_prompt_hash,
            side_effects=[SideEffectResult(**e) for e in existing.side_effects],
            context=DMContextResponse(**existing.context),
        )

    context = await _assemble_context(body)
    prompt_bundle = build_prompt(body, context)
    prompt_hash = hashlib.sha256(str(prompt_bundle.prompt_material).encode("utf-8")).hexdigest()
    plan = await plan_turn(body, context, prompt_text=prompt_bundle.system_prompt + "\n\n" + prompt_bundle.user_prompt)
    side_effects = await dispatch_plan(body, context, plan)

    saved = await database.save_turn(
        conn,
        campaign_id=str(body.campaign_id),
        session_id=str(body.session_id),
        turn_id=body.turn_id,
        user_id=str(body.user_id),
        character_id=str(body.character_id),
        input_text=body.input_text,
        selected_action=plan.selected_action,
        narration=plan.narration,
        llm_model=plan.llm_model,
        llm_prompt_hash=prompt_hash,
        side_effects=[e.model_dump(mode="json") for e in side_effects],
        context=context.model_dump(mode="json"),
    )

    await event_log.emit(
        event_type="dm.narration_generated",
        aggregate_id=str(body.campaign_id),
        aggregate_type="story",
        campaign_id=str(body.campaign_id),
        session_id=str(body.session_id),
        user_id=str(body.user_id),
        payload={
            "turn_id": body.turn_id,
            "selected_action": plan.selected_action,
            "input_preview": body.input_text[:200],
            "has_encounter": context.active_encounter is not None,
            "memory_items": len(context.memory_recall),
            "side_effects": [e.model_dump(mode="json") for e in side_effects],
        },
        llm_prompt_hash=prompt_hash,
    )

    return TurnResult(
        turn_id=saved.turn_id,
        campaign_id=body.campaign_id,
        session_id=body.session_id,
        user_id=body.user_id,
        narration=saved.narration,
        selected_action=saved.selected_action,
        llm_model=saved.llm_model,
        llm_prompt_hash=saved.llm_prompt_hash,
        side_effects=[SideEffectResult(**e) for e in saved.side_effects],
        context=context,
    )


@app.get("/turns", response_model=list[TurnLedgerRecord])
async def list_turns(
    conn: DbConn,
    campaign_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str | None = Query(default=None),
    character_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[TurnLedgerRecord]:
    return await database.list_turns(conn, campaign_id, session_id, user_id, character_id, limit, offset)


@app.get("/turns/{turn_id}", response_model=TurnLedgerRecord)
async def get_turn_record(
    turn_id: str,
    conn: DbConn,
    campaign_id: str = Query(...),
    session_id: str = Query(...),
) -> TurnLedgerRecord:
    result = await database.get_turn(conn, campaign_id, session_id, turn_id)
    if result is None:
        raise HTTPException(404, "Turn ledger record not found")
    return result


@app.delete("/turns/{turn_id}", status_code=204)
async def delete_turn_record(
    turn_id: str,
    conn: DbConn,
    campaign_id: str = Query(...),
    session_id: str = Query(...),
) -> None:
    deleted = await database.delete_turn(conn, campaign_id, session_id, turn_id)
    if not deleted:
        raise HTTPException(404, "Turn ledger record not found")


async def _assemble_context(body: TurnInput) -> DMContextResponse:
    story_context = await service_clients.get_story_context(body.campaign_id)
    character_state = await service_clients.get_character_state(body.campaign_id, body.character_id)
    map_snapshot = await service_clients.get_map_snapshot(body.campaign_id, body.character_id, body.map_hint_id)
    npc_context = await service_clients.get_npc_context(body.campaign_id, body.character_id)
    memory_recall = await service_clients.recall_memories(body.campaign_id, body.character_id, body.input_text)
    active_encounter = await service_clients.get_active_encounter(body.campaign_id)
    recent_events = await service_clients.list_recent_events(body.campaign_id, body.session_id)

    return DMContextResponse(
        campaign_id=body.campaign_id,
        session_id=body.session_id,
        user_id=body.user_id,
        character_id=body.character_id,
        story_context=story_context,
        world_character_state=character_state,
        map_snapshot=map_snapshot,
        npc_context=npc_context,
        memory_recall=memory_recall,
        active_encounter=active_encounter,
        recent_events=recent_events,
    )