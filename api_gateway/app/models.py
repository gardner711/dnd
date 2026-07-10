"""Pydantic models for API Gateway / Session API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SessionStartRequest(BaseModel):
    user_id: UUID
    campaign_id: UUID
    character_id: UUID
    map_hint_id: Optional[UUID] = None


class SessionContext(BaseModel):
    session_id: UUID
    user_id: UUID
    campaign_id: UUID
    character_id: UUID
    map_hint_id: Optional[UUID] = None
    started_at: datetime
    updated_at: datetime


class SessionStartResponse(BaseModel):
    session: SessionContext


class SessionStateResponse(BaseModel):
    session: SessionContext
    character: Optional[dict[str, Any]] = None
    active_encounter: Optional[dict[str, Any]] = None
    map_snapshot: Optional[dict[str, Any]] = None
    story_context: dict[str, Any] = Field(default_factory=dict)
    recent_events: list[dict[str, Any]] = Field(default_factory=list)


class TurnRequest(BaseModel):
    session_id: UUID
    turn_id: str = Field(min_length=1, max_length=200)
    input_text: str = Field(min_length=1)
    map_hint_id: Optional[UUID] = None


class CombatActionRequest(BaseModel):
    session_id: UUID
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class GatewayHealthChecks(BaseModel):
    dm_service: bool
    combat_engine: bool
    world_state: bool
    story_state: bool
    map_service: bool
    memory_service: bool
    npc_service: bool
    rules_engine: bool
    event_log: bool


class GatewayHealthResponse(BaseModel):
    status: str
    service: str
    checks: GatewayHealthChecks
    active_sessions: int
    checked_at: datetime