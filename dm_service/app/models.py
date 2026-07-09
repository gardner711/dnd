"""Pydantic models for the Dungeon Master Service."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class PlannedActionType(str, Enum):
    NARRATE = "narrate"
    STORY_LOG_APPEND = "story_log_append"
    STORY_HOOK_CREATE = "story_hook_create"
    STORY_HOOK_UPDATE = "story_hook_update"
    WORLD_FLAG_UPDATE = "world_flag_update"
    COMBAT_ACTION = "combat_action"
    MAP_UPDATE = "map_update"


class PlannedAction(BaseModel):
    action_type: PlannedActionType
    args: dict[str, Any] = Field(default_factory=dict)


class TurnPlan(BaseModel):
    selected_action: str
    narration: str
    actions: list[PlannedAction] = Field(default_factory=list)
    llm_model: str

    @field_validator("selected_action")
    @classmethod
    def selected_action_must_be_known(cls, value: str) -> str:
        allowed = {a.value for a in PlannedActionType}
        if value not in allowed:
            raise ValueError(f"selected_action must be one of: {sorted(allowed)}")
        return value

    @field_validator("narration")
    @classmethod
    def narration_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("narration must not be empty")
        return value


class PromptBundle(BaseModel):
    system_prompt: str
    user_prompt: str
    prompt_material: dict[str, Any]


class SideEffectResult(BaseModel):
    action_type: str
    success: bool
    detail: str = ""
    response: Optional[dict[str, Any]] = None
    compensated: bool = False
    compensation_detail: Optional[str] = None


class TurnInput(BaseModel):
    turn_id: str = Field(min_length=1, max_length=200)
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    character_id: UUID
    input_text: str = Field(min_length=1)
    map_hint_id: Optional[UUID] = None


class DMContextResponse(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    character_id: UUID
    story_context: dict[str, Any] = Field(default_factory=dict)
    world_character_state: Optional[dict[str, Any]] = None
    map_snapshot: Optional[dict[str, Any]] = None
    npc_context: Optional[dict[str, Any]] = None
    memory_recall: list[dict[str, Any]] = Field(default_factory=list)
    active_encounter: Optional[dict[str, Any]] = None
    recent_events: list[dict[str, Any]] = Field(default_factory=list)


class TurnResult(BaseModel):
    turn_id: str
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    narration: str
    selected_action: str
    llm_model: str
    llm_prompt_hash: str
    side_effects: list[SideEffectResult] = Field(default_factory=list)
    context: DMContextResponse


class TurnLedgerRecord(BaseModel):
    campaign_id: UUID
    session_id: UUID
    turn_id: str
    user_id: UUID
    character_id: UUID
    input_text: str
    selected_action: str
    narration: str
    llm_model: str
    llm_prompt_hash: str
    side_effects: list[dict[str, Any]] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class HealthChecks(BaseModel):
    story_state: bool
    world_state: bool
    npc_service: bool
    memory_service: bool
    map_service: bool
    combat_engine: bool
    event_log: bool


class HealthResponse(BaseModel):
    status: str
    service: str
    checks: HealthChecks
    checked_at: datetime