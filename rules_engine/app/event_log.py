"""Async HTTP client for writing events to the Event Log Service.

Events are fire-and-forget — a failure to write is logged but does NOT
block the rules resolution response. The caller must not depend on the
write having completed before returning its own response.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)

_EVENT_LOG_URL = os.getenv("EVENT_LOG_URL", "http://event-log-service:8000")
_SERVICE_NAME = os.getenv("SERVICE_NAME", "rules-engine")
_EMIT_TIMEOUT_SECONDS = 2.0


async def emit(
    event_type: str,
    aggregate_id: str,
    aggregate_type: str,
    campaign_id: str,
    session_id: str,
    user_id: str,
    payload: dict[str, Any],
) -> None:
    """Post a single event to the Event Log Service.

    Args:
        event_type:      e.g. "dice.rolled", "attack.resolved"
        aggregate_id:    ID of the affected entity (character_id, npc_id, etc.)
        aggregate_type:  "character" | "npc" | "combat" | "story" | "world"
        campaign_id:     Active campaign UUID (from JWT claim)
        session_id:      Active session UUID
        user_id:         Acting player UUID (sub claim from JWT)
        payload:         Full event data — rule inputs, roll results, outcomes
    """
    event = {
        "event_id": str(uuid4()),
        "campaign_id": campaign_id,
        "session_id": session_id,
        "user_id": user_id,
        "event_type": event_type,
        "aggregate_id": aggregate_id,
        "aggregate_type": aggregate_type,
        "payload": payload,
        "source_service": _SERVICE_NAME,
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=_EMIT_TIMEOUT_SECONDS) as client:
            response = await client.post(f"{_EVENT_LOG_URL}/events", json=event)
            response.raise_for_status()
    except Exception as exc:
        logger.warning(
            "Failed to emit event '%s' to Event Log Service: %s",
            event_type,
            exc,
        )
