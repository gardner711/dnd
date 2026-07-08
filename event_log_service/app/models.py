"""Pydantic models for the Event Log Service."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel


class EventIn(BaseModel):
    """Payload accepted by POST /events — matches what event_log.py in each service sends."""
    event_id: UUID
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    event_type: str
    aggregate_id: UUID
    aggregate_type: str          # "character" | "npc" | "combat" | "story" | "world"
    payload: dict[str, Any]
    source_service: str
    llm_prompt_hash: Optional[str] = None   # SHA-256 of LLM prompt, set by DM Service
    occurred_at: datetime


class EventOut(EventIn):
    """Event record returned by GET /events — identical shape to EventIn."""
    pass


class WriteEventResponse(BaseModel):
    event_id: str
