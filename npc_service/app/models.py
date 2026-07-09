"""Pydantic models for the NPC Interaction Service."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ConditionType(str, Enum):
    always         = "always"
    disposition_gte = "disposition_gte"
    quest_status   = "quest_status"


class EventMeta(BaseModel):
    campaign_id: UUID
    session_id: Optional[UUID] = None
    user_id: Optional[UUID] = None


# ── NPC Profile models ────────────────────────────────────────────────────────

class NPCCreate(BaseModel):
    campaign_id: UUID
    name: str
    role: str
    physical_description: Optional[str] = None
    personality_prompt: str = Field(..., max_length=2000)
    faction_id: Optional[UUID] = None   # optional faction affiliation in World State
    meta: Optional[EventMeta] = None


class NPCUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    physical_description: Optional[str] = None
    clear_physical_description: bool = False  # set True to explicitly NULL the field
    personality_prompt: Optional[str] = Field(default=None, max_length=2000)
    is_active: Optional[bool] = None
    faction_id: Optional[UUID] = None
    meta: Optional[EventMeta] = None


class NPCOut(BaseModel):
    npc_id: UUID
    campaign_id: UUID
    name: str
    role: str
    physical_description: Optional[str] = None
    personality_prompt: str
    is_active: bool
    faction_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


# ── Secret models ─────────────────────────────────────────────────────────────

class SecretCreate(BaseModel):
    content: str
    condition_type: ConditionType = ConditionType.always
    condition_value: Optional[int] = None         # threshold for disposition_gte
    condition_quest_title: Optional[str] = None   # quest title for quest_status
    condition_quest_status: Optional[str] = None  # expected quest status value


class SecretUpdate(BaseModel):
    content: Optional[str] = None
    condition_type: Optional[ConditionType] = None
    condition_value: Optional[int] = None
    condition_quest_title: Optional[str] = None
    condition_quest_status: Optional[str] = None


class SecretOut(BaseModel):
    secret_id: UUID
    npc_id: UUID
    campaign_id: UUID
    content: str
    condition_type: ConditionType
    condition_value: Optional[int] = None
    condition_quest_title: Optional[str] = None
    condition_quest_status: Optional[str] = None
    revealed_at: Optional[datetime] = None


# ── Context assembly models ───────────────────────────────────────────────────

class NPCContextRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    character_id: UUID
    player_message: str
    dialogue_history_limit: int = Field(default=20, ge=1, le=100)
    memory_limit: int = Field(default=5, ge=1, le=20)


class DialogueTurn(BaseModel):
    role: str     # "player" or "npc"
    content: str
    ts: str       # ISO 8601 string


class SecretSummary(BaseModel):
    secret_id: UUID
    content: str
    condition_type: ConditionType
    first_revealed: bool  # True if this call first unlocked this secret


class NPCContextResponse(BaseModel):
    npc_id: UUID
    npc_name: str
    system_prompt: str
    dialogue_history: List[DialogueTurn]
    disposition_score: Optional[int] = None
    disposition_label: str            # hostile/neutral/friendly/trusted/unknown
    disposition_notes: Optional[str] = None   # freetext reason from World State
    faction_standing: Optional[int] = None    # faction score used as fallback
    secrets_injected_count: int
    secrets_injected: List[SecretSummary]   # full content for DM transparency
    memory_context: Optional[str] = None


# ── Dialogue models ───────────────────────────────────────────────────────────

class DialogueAppend(BaseModel):
    campaign_id: UUID
    session_id: UUID
    player_message: str
    npc_response: str
    meta: Optional[EventMeta] = None
