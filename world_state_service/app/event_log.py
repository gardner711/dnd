"""Async fire-and-forget client for the Event Log Service."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from app.config import settings

logger = logging.getLogger(__name__)
_TIMEOUT = 2.0


async def emit(
    event_type: str,
    aggregate_id: str,
    aggregate_type: str,
    campaign_id: str,
    session_id: str,
    user_id: str,
    payload: dict[str, Any],
) -> None:
    """POST a single event to the Event Log Service. Non-blocking on failure."""
    event = {
        "event_id": str(uuid4()),
        "campaign_id": campaign_id,
        "session_id": session_id,
        "user_id": user_id,
        "event_type": event_type,
        "aggregate_id": aggregate_id,
        "aggregate_type": aggregate_type,
        "payload": payload,
        "source_service": settings.service_name,
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(f"{settings.event_log_url}/events", json=event)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to emit event '%s': %s", event_type, exc)
