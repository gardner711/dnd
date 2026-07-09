"""Pydantic models for the Memory Service."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SubjectType(str, Enum):
    CAMPAIGN = "campaign"
    CHARACTER = "character"
    NPC = "npc"
    WORLD = "world"


class MemoryIn(BaseModel):
    campaign_id: UUID
    subject_type: SubjectType
    subject_id: UUID
    content: str = Field(min_length=1, max_length=2000)
    importance: int = Field(default=3, ge=1, le=5)
    source_event_ids: list[UUID] = Field(default_factory=list)


class MemoryOut(BaseModel):
    memory_id: UUID
    campaign_id: UUID
    subject_type: SubjectType
    subject_id: UUID
    content: str
    importance: int
    source_event_ids: list[UUID]
    created_at: datetime
    last_accessed_at: datetime


class RecallResult(BaseModel):
    memories: list[MemoryOut]
    query: str
    top_k: int


class WriteMemoryResponse(BaseModel):
    memory_id: str


class MemoryUpdate(BaseModel):
    """Partial update for a memory record. Only supplied fields are changed.
    If content changes, the embedding is automatically regenerated.
    """
    importance: Optional[int] = Field(default=None, ge=1, le=5)
    content: Optional[str] = Field(default=None, min_length=1, max_length=2000)
