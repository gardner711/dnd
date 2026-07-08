"""Tests for EventIn / EventOut model validation — no I/O required."""
import pytest
from datetime import datetime, UTC
from uuid import uuid4

from pydantic import ValidationError

from app.models import EventIn


def _valid_event(**overrides) -> dict:
    base = dict(
        event_id=str(uuid4()),
        campaign_id=str(uuid4()),
        session_id=str(uuid4()),
        user_id=str(uuid4()),
        event_type="dice.rolled",
        aggregate_id=str(uuid4()),
        aggregate_type="character",
        payload={"notation": "1d20", "total": 15},
        source_service="rules-engine",
        occurred_at=datetime.now(UTC).isoformat(),
    )
    base.update(overrides)
    return base


def test_valid_event_parses():
    event = EventIn(**_valid_event())
    assert event.event_type == "dice.rolled"
    assert event.source_service == "rules-engine"


def test_llm_prompt_hash_defaults_to_none():
    event = EventIn(**_valid_event())
    assert event.llm_prompt_hash is None


def test_llm_prompt_hash_can_be_set():
    event = EventIn(**_valid_event(llm_prompt_hash="sha256:abc123"))
    assert event.llm_prompt_hash == "sha256:abc123"


def test_payload_accepts_nested_dicts():
    event = EventIn(**_valid_event(payload={"roll": {"dice": [15], "total": 15}}))
    assert event.payload["roll"]["total"] == 15


def test_missing_event_type_raises():
    data = _valid_event()
    del data["event_type"]
    with pytest.raises(ValidationError):
        EventIn(**data)


def test_missing_campaign_id_raises():
    data = _valid_event()
    del data["campaign_id"]
    with pytest.raises(ValidationError):
        EventIn(**data)


def test_invalid_uuid_raises():
    with pytest.raises(ValidationError):
        EventIn(**_valid_event(event_id="not-a-uuid"))


def test_all_event_type_strings_accepted():
    """Event type is freeform text — no enum constraint at the model layer."""
    for event_type in [
        "dice.rolled", "attack.resolved", "combat.state_changed",
        "npc.disposition_changed", "dm.narration_generated", "session.started",
    ]:
        event = EventIn(**_valid_event(event_type=event_type))
        assert event.event_type == event_type
