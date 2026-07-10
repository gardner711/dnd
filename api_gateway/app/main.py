"""FastAPI application — API Gateway / Session API (without auth)."""
from __future__ import annotations

from datetime import UTC, datetime
import logging
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query

from app import service_clients, session_store
from app.models import (
    CombatActionRequest,
    GatewayHealthChecks,
    GatewayHealthResponse,
    SessionContext,
    SessionStartRequest,
    SessionStartResponse,
    SessionStateResponse,
    TurnRequest,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="API Gateway / Session API",
    description="Gateway orchestration layer for integration testing (auth deferred)",
    version="0.1.0",
)


@app.get("/health", response_model=GatewayHealthResponse)
async def health() -> GatewayHealthResponse:
    checks = GatewayHealthChecks(
        dm_service=await service_clients.health_check(f"{service_clients.settings.dm_service_url}/health"),
        combat_engine=await service_clients.health_check(f"{service_clients.settings.combat_engine_url}/health"),
        world_state=await service_clients.health_check(f"{service_clients.settings.world_state_url}/health"),
        story_state=await service_clients.health_check(f"{service_clients.settings.story_state_url}/health"),
        map_service=await service_clients.health_check(f"{service_clients.settings.map_service_url}/health"),
        memory_service=await service_clients.health_check(f"{service_clients.settings.memory_service_url}/health"),
        npc_service=await service_clients.health_check(f"{service_clients.settings.npc_service_url}/health"),
        rules_engine=await service_clients.health_check(f"{service_clients.settings.rules_engine_url}/health"),
        event_log=await service_clients.health_check(f"{service_clients.settings.event_log_url}/health"),
    )
    status = "ok" if all(checks.model_dump().values()) else "degraded"
    return GatewayHealthResponse(
        status=status,
        service="api-gateway",
        checks=checks,
        active_sessions=session_store.session_count(),
        checked_at=datetime.now(UTC),
    )


@app.post("/session/start", response_model=SessionStartResponse)
async def session_start(body: SessionStartRequest) -> SessionStartResponse:
    character = await service_clients.get_character(body.campaign_id, body.character_id)
    if character is None:
        raise HTTPException(404, "Character not found in this campaign")
    if str(character.get("user_id")) != str(body.user_id):
        raise HTTPException(409, "Character does not belong to this user")

    session = session_store.create_session(body.user_id, body.campaign_id, body.character_id, body.map_hint_id)
    return SessionStartResponse(session=session)


@app.get("/session/{session_id}", response_model=SessionContext)
async def get_session(session_id: UUID) -> SessionContext:
    session = session_store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    return session


@app.delete("/session/{session_id}", status_code=204)
async def end_session(session_id: UUID) -> None:
    deleted = session_store.delete_session(session_id)
    if not deleted:
        raise HTTPException(404, "Session not found")


@app.get("/session/{session_id}/state", response_model=SessionStateResponse)
async def get_session_state(session_id: UUID, event_limit: int = Query(default=20, ge=1, le=200)) -> SessionStateResponse:
    session = session_store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")

    character = await service_clients.get_character(session.campaign_id, session.character_id)
    encounter = await service_clients.get_active_encounter(session.campaign_id)
    map_snapshot = await service_clients.get_map_snapshot(session.campaign_id, session.character_id, session.map_hint_id)
    story_context = await service_clients.get_story_context(session.campaign_id)
    events = await service_clients.list_recent_events(session.campaign_id, session_id, limit=event_limit)

    return SessionStateResponse(
        session=session,
        character=character,
        active_encounter=encounter,
        map_snapshot=map_snapshot,
        story_context=story_context,
        recent_events=events,
    )


@app.post("/session/turn")
async def session_turn(body: TurnRequest):
    session = session_store.get_session(body.session_id)
    if session is None:
        raise HTTPException(404, "Session not found")

    if body.map_hint_id is not None:
        session = session_store.update_session_map_hint(body.session_id, body.map_hint_id) or session

    return await service_clients.run_dm_turn(
        campaign_id=session.campaign_id,
        session_id=session.session_id,
        user_id=session.user_id,
        character_id=session.character_id,
        turn_id=body.turn_id,
        input_text=body.input_text,
        map_hint_id=body.map_hint_id or session.map_hint_id,
    )


@app.post("/session/combat/action")
async def session_combat_action(body: CombatActionRequest):
    session = session_store.get_session(body.session_id)
    if session is None:
        raise HTTPException(404, "Session not found")

    return await service_clients.run_combat_action(
        action=body.action,
        campaign_id=session.campaign_id,
        session_id=session.session_id,
        user_id=session.user_id,
        payload=body.payload,
    )


@app.get("/session/{session_id}/combat")
async def session_combat_state(session_id: UUID):
    session = session_store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    return await service_clients.get_active_encounter(session.campaign_id)


@app.get("/session/{session_id}/map")
async def session_map_snapshot(session_id: UUID, map_hint_id: UUID | None = Query(default=None)):
    session = session_store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    chosen_hint = map_hint_id or session.map_hint_id
    if map_hint_id is not None:
        session_store.update_session_map_hint(session.session_id, map_hint_id)
    return await service_clients.get_map_snapshot(session.campaign_id, session.character_id, chosen_hint)