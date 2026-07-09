"""Pydantic models for the Story State Service."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class QuestStatus(str, Enum):
    hidden = "hidden"
    active = "active"
    completed = "completed"
    failed = "failed"


class HookStatus(str, Enum):
    open = "open"
    resolved = "resolved"
    dismissed = "dismissed"


class HookPriority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class StoryEntryType(str, Enum):
    narration = "narration"
    combat_summary = "combat_summary"
    quest_update = "quest_update"
    hook_note = "hook_note"
    session_summary = "session_summary"


class EventMeta(BaseModel):
    campaign_id: UUID
    session_id: Optional[UUID] = None
    user_id: Optional[UUID] = None


# ── Quest models ─────────────────────────────────────────────────────────────

class ObjectiveCreate(BaseModel):
    description: str
    sequence_order: int = 0


class QuestCreate(BaseModel):
    campaign_id: UUID
    title: str
    description: Optional[str] = None
    status: QuestStatus = QuestStatus.active
    giver_npc_id: Optional[UUID] = None
    reward_description: Optional[str] = None
    objectives: List[ObjectiveCreate] = Field(default_factory=list)
    meta: Optional[EventMeta] = None


class QuestUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[QuestStatus] = None
    reward_description: Optional[str] = None
    meta: Optional[EventMeta] = None


class ObjectivePatch(BaseModel):
    completed: bool


class ObjectiveOut(BaseModel):
    objective_id: UUID
    quest_id: UUID
    campaign_id: UUID
    description: str
    sequence_order: int
    completed_at: Optional[datetime] = None


class QuestOut(BaseModel):
    quest_id: UUID
    campaign_id: UUID
    title: str
    description: Optional[str] = None
    status: QuestStatus
    giver_npc_id: Optional[UUID] = None
    reward_description: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    updated_at: datetime
    objectives: List[ObjectiveOut] = Field(default_factory=list)


# ── Hook models ──────────────────────────────────────────────────────────────

class HookCreate(BaseModel):
    campaign_id: UUID
    content: str
    priority: HookPriority = HookPriority.medium
    source_event_id: Optional[UUID] = None
    meta: Optional[EventMeta] = None


class HookUpdate(BaseModel):
    content: Optional[str] = None
    status: Optional[HookStatus] = None
    priority: Optional[HookPriority] = None
    meta: Optional[EventMeta] = None


class HookOut(BaseModel):
    hook_id: UUID
    campaign_id: UUID
    content: str
    status: HookStatus
    priority: HookPriority
    source_event_id: Optional[UUID] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None


# ── Story Log models ──────────────────────────────────────────────────────────

class StoryLogEntry(BaseModel):
    campaign_id: UUID
    session_id: Optional[UUID] = None
    entry_type: StoryEntryType
    content: str


class StoryLogBatch(BaseModel):
    entries: List[StoryLogEntry] = Field(min_length=1)
    meta: Optional[EventMeta] = None


class StoryLogOut(BaseModel):
    entry_id: UUID
    campaign_id: UUID
    session_id: Optional[UUID] = None
    entry_type: StoryEntryType
    content: str
    created_at: datetime


# ── DM context snapshot ──────────────────────────────────────────────────────

class DMContext(BaseModel):
    """Narrative context snapshot returned to the DM Service each turn."""
    campaign_id: UUID
    active_quests: List[QuestOut]        # status=active, player-visible
    open_hooks: List[HookOut]            # status=open, sorted by priority
    recent_log: List[StoryLogOut]        # most recent entries, chronological
