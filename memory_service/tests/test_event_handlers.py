import pytest
from uuid import uuid4
from app.event_handlers import _disposition_label, event_to_memory
from app.models import SubjectType


def _event(event_type: str, **overrides) -> dict:
    base = dict(
        event_id=str(uuid4()), campaign_id=str(uuid4()),
        session_id=str(uuid4()), user_id=str(uuid4()),
        aggregate_id=str(uuid4()), aggregate_type="character",
        payload={}, source_service="test",
    )
    base["event_type"] = event_type
    base.update(overrides)
    return base


# ── Ignored events ────────────────────────────────────────────────────────────

def test_dice_rolled_ignored():
    assert event_to_memory(_event("dice.rolled", payload={"total": 15})) is None

def test_attack_resolved_ignored():
    assert event_to_memory(_event("attack.resolved")) is None

def test_ability_check_ignored():
    assert event_to_memory(_event("ability_check.resolved")) is None

def test_unknown_event_ignored():
    assert event_to_memory(_event("custom.event")) is None


# ── NPC disposition ───────────────────────────────────────────────────────────

def test_npc_disposition_changed():
    ev = _event("npc.disposition_changed", aggregate_type="npc", payload={
        "npc_name": "Innkeeper Marta", "character_name": "the party",
        "old_score": 60, "new_score": 20, "reason": "They burned the barn.",
    })
    m = event_to_memory(ev)
    assert m is not None
    assert m.subject_type == SubjectType.NPC
    assert "Innkeeper Marta" in m.content
    assert "hostile" in m.content
    assert "neutral" in m.content
    assert m.importance == 3


# ── Story hooks ───────────────────────────────────────────────────────────────

def test_story_hook_created():
    ev = _event("story.hook_created", payload={
        "hook_name": "Find the missing merchant", "description": "Last seen near the docks.",
    })
    m = event_to_memory(ev)
    assert m is not None
    assert m.importance == 4
    assert "missing merchant" in m.content


def test_story_hook_resolved():
    ev = _event("story.hook_resolved", payload={
        "hook_name": "Missing merchant", "outcome": "Found dead in the sewers.",
    })
    m = event_to_memory(ev)
    assert m is not None
    assert m.importance == 5
    assert "resolved" in m.content.lower()
    assert "sewers" in m.content


# ── DM narration ──────────────────────────────────────────────────────────────

def test_dm_narration_short_ignored():
    ev = _event("dm.narration_generated", payload={"narration": "Short text."})
    assert event_to_memory(ev) is None


def test_dm_narration_stored():
    long_narration = "The party enters a dimly lit tavern filled with the smell of ale. A bard plays a mournful tune in the corner while suspicious patrons eye the newcomers."
    ev = _event("dm.narration_generated", payload={"narration": long_narration})
    m = event_to_memory(ev)
    assert m is not None
    assert m.subject_type == SubjectType.CAMPAIGN
    assert m.importance == 2


# ── Session events ────────────────────────────────────────────────────────────

def test_session_started():
    ev = _event("session.started", payload={"player_names": "Alice, Bob, Carol"})
    m = event_to_memory(ev)
    assert m is not None
    assert "Alice" in m.content


def test_session_ended_with_summary():
    ev = _event("session.ended", payload={"summary": "The party defeated the bandits."})
    m = event_to_memory(ev)
    assert m is not None
    assert m.importance == 4
    assert "bandits" in m.content


# ── Combat deaths ─────────────────────────────────────────────────────────────

def test_combat_death_npc():
    ev = _event("combat.state_changed", aggregate_type="npc", payload={
        "current_hp": 0, "combatant_name": "Goblin Chief", "killer_name": "Aria",
    })
    m = event_to_memory(ev)
    assert m is not None
    assert m.importance == 5
    assert "Goblin Chief" in m.content
    assert m.subject_type == SubjectType.NPC


def test_combat_death_character():
    ev = _event("combat.state_changed", aggregate_type="character", payload={
        "current_hp": 0, "combatant_name": "Bob", "killer_name": "the dragon",
    })
    m = event_to_memory(ev)
    assert m is not None
    assert m.subject_type == SubjectType.CHARACTER


def test_combat_non_death_ignored():
    ev = _event("combat.state_changed", payload={"current_hp": 5})
    assert event_to_memory(ev) is None


def test_combat_missing_hp_ignored():
    ev = _event("combat.state_changed", payload={"condition": "poisoned"})
    assert event_to_memory(ev) is None


# ── World state ───────────────────────────────────────────────────────────────

def test_world_changed_with_description():
    ev = _event("world.state_changed", payload={
        "description": "The town of Millhaven was burned to the ground by the dragon."
    })
    m = event_to_memory(ev)
    assert m is not None
    assert "Millhaven" in m.content
    assert m.subject_type == SubjectType.WORLD


def test_world_changed_no_description_ignored():
    ev = _event("world.state_changed", payload={"key": "flag", "value": True})
    assert event_to_memory(ev) is None


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_missing_campaign_id_returns_none():
    ev = _event("npc.disposition_changed", payload={"npc_name": "X"})
    del ev["campaign_id"]
    assert event_to_memory(ev) is None


def test_missing_aggregate_id_returns_none():
    ev = _event("story.hook_created", payload={"hook_name": "X"})
    del ev["aggregate_id"]
    assert event_to_memory(ev) is None


# ── Disposition label ─────────────────────────────────────────────────────────

def test_disposition_hostile():
    assert _disposition_label(0) == "hostile"
    assert _disposition_label(30) == "hostile"

def test_disposition_neutral():
    assert _disposition_label(31) == "neutral"
    assert _disposition_label(60) == "neutral"

def test_disposition_friendly():
    assert _disposition_label(61) == "friendly"
    assert _disposition_label(80) == "friendly"

def test_disposition_trusted():
    assert _disposition_label(81) == "trusted"
    assert _disposition_label(100) == "trusted"
