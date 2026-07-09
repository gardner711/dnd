"""Maps game event types to Memory Service records.

Only high-signal events create memories. Low-signal events (dice rolls,
individual attack rolls, movement) are silently ignored to avoid noise.
No LLM calls are made here — content is formatted directly from event payload.
Richer narrative summaries come from the DM Service via POST /memories.
"""
from __future__ import annotations

import logging
from uuid import UUID

from app.models import MemoryIn, SubjectType

logger = logging.getLogger(__name__)

_MEMORABLE_EVENTS = frozenset({
    "npc.disposition_changed",
    "story.hook_created",
    "story.hook_resolved",
    "dm.narration_generated",
    "session.started",
    "session.ended",
    "combat.state_changed",   # only on death (current_hp == 0)
    "world.state_changed",    # only when a description is provided
})


def event_to_memory(event: dict) -> MemoryIn | None:
    """Convert a raw event dict to a MemoryIn record, or None if not memorable."""
    event_type = event.get("event_type", "")
    if event_type not in _MEMORABLE_EVENTS:
        return None

    payload = event.get("payload", {})
    campaign_id_str = event.get("campaign_id")
    event_id_str = event.get("event_id")
    aggregate_id_str = event.get("aggregate_id")
    aggregate_type = event.get("aggregate_type", "character")

    if not campaign_id_str or not aggregate_id_str:
        return None

    try:
        campaign_id = UUID(campaign_id_str)
        aggregate_id = UUID(aggregate_id_str)
        source_ids = [UUID(event_id_str)] if event_id_str else []

        handlers = {
            "npc.disposition_changed": _npc_disposition,
            "story.hook_created": _story_hook_created,
            "story.hook_resolved": _story_hook_resolved,
            "dm.narration_generated": _dm_narration,
            "session.started": _session_started,
            "session.ended": _session_ended,
            "combat.state_changed": lambda c, a, p, s: _combat_death(c, a, aggregate_type, p, s),
            "world.state_changed": _world_changed,
        }
        return handlers[event_type](campaign_id, aggregate_id, payload, source_ids)

    except (ValueError, KeyError) as exc:
        logger.warning("Failed to parse event '%s': %s", event_type, exc)
        return None


# ── Disposition label ─────────────────────────────────────────────────────────

def _disposition_label(score: int) -> str:
    if score <= 30:
        return "hostile"
    if score <= 60:
        return "neutral"
    if score <= 80:
        return "friendly"
    return "trusted"


# ── Individual handlers ───────────────────────────────────────────────────────

def _npc_disposition(
    campaign_id: UUID, npc_id: UUID, payload: dict, source_ids: list[UUID]
) -> MemoryIn:
    npc_name = payload.get("npc_name", "An NPC")
    character_name = payload.get("character_name", "the party")
    old_label = _disposition_label(payload.get("old_score", 50))
    new_label = _disposition_label(payload.get("new_score", 50))
    reason = payload.get("reason", "")
    content = (
        f"{npc_name} became {new_label} toward {character_name} "
        f"(previously {old_label}). {reason}"
    ).strip()
    return MemoryIn(
        campaign_id=campaign_id, subject_type=SubjectType.NPC, subject_id=npc_id,
        content=content, importance=3, source_event_ids=source_ids,
    )


def _story_hook_created(
    campaign_id: UUID, aggregate_id: UUID, payload: dict, source_ids: list[UUID]
) -> MemoryIn:
    hook_name = payload.get("hook_name", "A new quest")
    description = payload.get("description", "")
    content = f"Quest hook: {hook_name}. {description}".strip().rstrip(".")
    return MemoryIn(
        campaign_id=campaign_id, subject_type=SubjectType.CAMPAIGN, subject_id=aggregate_id,
        content=content, importance=4, source_event_ids=source_ids,
    )


def _story_hook_resolved(
    campaign_id: UUID, aggregate_id: UUID, payload: dict, source_ids: list[UUID]
) -> MemoryIn:
    hook_name = payload.get("hook_name", "A quest")
    outcome = payload.get("outcome", "completed")
    return MemoryIn(
        campaign_id=campaign_id, subject_type=SubjectType.CAMPAIGN, subject_id=aggregate_id,
        content=f"Quest resolved \u2014 {hook_name}: {outcome}",
        importance=5, source_event_ids=source_ids,
    )


def _dm_narration(
    campaign_id: UUID, aggregate_id: UUID, payload: dict, source_ids: list[UUID]
) -> MemoryIn | None:
    narration = payload.get("narration", "")
    if len(narration) < 20:
        return None  # too short to be worth embedding
    return MemoryIn(
        campaign_id=campaign_id, subject_type=SubjectType.CAMPAIGN, subject_id=aggregate_id,
        content=narration[:2000], importance=2, source_event_ids=source_ids,
    )


def _session_started(
    campaign_id: UUID, aggregate_id: UUID, payload: dict, source_ids: list[UUID]
) -> MemoryIn:
    player_names = payload.get("player_names", "")
    content = f"Session started. Players: {player_names}" if player_names else "Session started."
    return MemoryIn(
        campaign_id=campaign_id, subject_type=SubjectType.CAMPAIGN, subject_id=aggregate_id,
        content=content, importance=2, source_event_ids=source_ids,
    )


def _session_ended(
    campaign_id: UUID, aggregate_id: UUID, payload: dict, source_ids: list[UUID]
) -> MemoryIn:
    summary = payload.get("summary", "")
    content = f"Session ended. {summary}".strip() if summary else "Session ended."
    return MemoryIn(
        campaign_id=campaign_id, subject_type=SubjectType.CAMPAIGN, subject_id=aggregate_id,
        content=content, importance=4, source_event_ids=source_ids,
    )


def _combat_death(
    campaign_id: UUID, aggregate_id: UUID, aggregate_type: str,
    payload: dict, source_ids: list[UUID],
) -> MemoryIn | None:
    current_hp = payload.get("current_hp")
    if current_hp is None or current_hp > 0:
        return None  # only store deaths
    combatant_name = payload.get("combatant_name", "A combatant")
    killer_name = payload.get("killer_name", "unknown")
    subject_type = SubjectType.CHARACTER if aggregate_type == "character" else SubjectType.NPC
    return MemoryIn(
        campaign_id=campaign_id, subject_type=subject_type, subject_id=aggregate_id,
        content=f"{combatant_name} was slain by {killer_name}.",
        importance=5, source_event_ids=source_ids,
    )


def _world_changed(
    campaign_id: UUID, aggregate_id: UUID, payload: dict, source_ids: list[UUID]
) -> MemoryIn | None:
    description = payload.get("description", "")
    if not description:
        return None
    return MemoryIn(
        campaign_id=campaign_id, subject_type=SubjectType.WORLD, subject_id=aggregate_id,
        content=f"World state change: {description}",
        importance=3, source_event_ids=source_ids,
    )
