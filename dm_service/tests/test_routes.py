"""Route tests for the Dungeon Master Service.

All upstream calls and database operations are mocked.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.database as db_module
import app.event_log as event_log_module
import app.service_clients as service_clients
from app.dependencies import get_db_conn
from app.main import app
from app.models import DMContextResponse, PlannedAction, PlannedActionType, SideEffectResult, TurnPlan

_CAMP = uuid4()
_SESSION = uuid4()
_USER = uuid4()
_CHAR = uuid4()
_NOW = datetime.now(UTC)


def _context() -> DMContextResponse:
    return DMContextResponse(
        campaign_id=_CAMP,
        session_id=_SESSION,
        user_id=_USER,
        character_id=_CHAR,
        story_context={"active_quests": [], "open_hooks": [], "recent_log": []},
        world_character_state={"character_id": str(_CHAR)},
        map_snapshot=None,
        npc_context=None,
        memory_recall=[],
        active_encounter=None,
        recent_events=[],
    )


@pytest.fixture
def mock_conn():
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)
    return conn


@pytest.fixture
def mock_pool(mock_conn):
    pool = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool


@pytest.fixture
def client(monkeypatch, mock_conn, mock_pool):
    monkeypatch.setattr(db_module, "get_pool", AsyncMock(return_value=mock_pool))
    monkeypatch.setattr(db_module, "run_migrations", AsyncMock(return_value=None))
    monkeypatch.setattr(db_module, "close_pool", AsyncMock(return_value=None))
    monkeypatch.setattr(event_log_module, "emit", AsyncMock(return_value=None))

    async def override_db():
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_db
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()


def test_health_degraded_when_one_dep_down(client, monkeypatch):
    monkeypatch.setattr(service_clients, "health_check", AsyncMock(side_effect=[True, True, True, False, True, True, True]))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["memory_service"] is False


def test_context_aggregates_upstream_data(client, monkeypatch):
    monkeypatch.setattr(service_clients, "get_story_context", AsyncMock(return_value={"active_quests": [], "open_hooks": [], "recent_log": []}))
    monkeypatch.setattr(service_clients, "get_character_state", AsyncMock(return_value={"character_id": str(_CHAR)}))
    monkeypatch.setattr(service_clients, "get_map_snapshot", AsyncMock(return_value={"map": {"map_id": "m"}}))
    monkeypatch.setattr(service_clients, "get_npc_context", AsyncMock(return_value={"npcs": []}))
    monkeypatch.setattr(service_clients, "recall_memories", AsyncMock(return_value=[{"content": "old event"}]))
    monkeypatch.setattr(service_clients, "get_active_encounter", AsyncMock(return_value=None))
    monkeypatch.setattr(service_clients, "list_recent_events", AsyncMock(return_value=[]))

    response = client.post(
        "/context",
        json={
            "turn_id": "turn-001",
            "campaign_id": str(_CAMP),
            "session_id": str(_SESSION),
            "user_id": str(_USER),
            "character_id": str(_CHAR),
            "input_text": "I search the room",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["campaign_id"] == str(_CAMP)
    assert body["world_character_state"]["character_id"] == str(_CHAR)
    assert body["memory_recall"][0]["content"] == "old event"


def test_turn_returns_cached_result_when_turn_id_replayed(client, monkeypatch):
    existing = {
        "campaign_id": _CAMP,
        "session_id": _SESSION,
        "turn_id": "turn-002",
        "user_id": _USER,
        "character_id": _CHAR,
        "input_text": "repeat",
        "selected_action": "narrate",
        "narration": "cached narration",
        "llm_model": "dm-stub-v1",
        "llm_prompt_hash": "abc123",
        "side_effects": [{"action_type": "narrate", "success": True, "detail": "no_op"}],
        "context": _context().model_dump(mode="json"),
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    monkeypatch.setattr(db_module, "get_turn", AsyncMock(return_value=type("Row", (), existing)()))

    response = client.post(
        "/turn",
        json={
            "turn_id": "turn-002",
            "campaign_id": str(_CAMP),
            "session_id": str(_SESSION),
            "user_id": str(_USER),
            "character_id": str(_CHAR),
            "input_text": "repeat",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["narration"] == "cached narration"
    event_log_module.emit.assert_not_awaited()


def test_turn_plans_dispatches_persists_and_emits_event(client, monkeypatch):
    monkeypatch.setattr(db_module, "get_turn", AsyncMock(return_value=None))
    monkeypatch.setattr(db_module, "save_turn", AsyncMock(return_value=type(
        "Saved", (),
        {
            "turn_id": "turn-003",
            "campaign_id": _CAMP,
            "session_id": _SESSION,
            "user_id": _USER,
            "narration": "A narrated response",
            "selected_action": "story_log_append",
            "llm_model": "dm-stub-v1",
            "llm_prompt_hash": "hash123",
            "side_effects": [{"action_type": "story_log_append", "success": True, "detail": "story_log_appended"}],
        },
    )()))

    monkeypatch.setattr(service_clients, "get_story_context", AsyncMock(return_value={"active_quests": [], "open_hooks": [], "recent_log": []}))
    monkeypatch.setattr(service_clients, "get_character_state", AsyncMock(return_value={"character_id": str(_CHAR)}))
    monkeypatch.setattr(service_clients, "get_map_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(service_clients, "get_npc_context", AsyncMock(return_value=None))
    monkeypatch.setattr(service_clients, "recall_memories", AsyncMock(return_value=[]))
    monkeypatch.setattr(service_clients, "get_active_encounter", AsyncMock(return_value=None))
    monkeypatch.setattr(service_clients, "list_recent_events", AsyncMock(return_value=[]))

    import app.main as main_module
    monkeypatch.setattr(main_module, "plan_turn", AsyncMock(return_value=TurnPlan(
        selected_action="story_log_append",
        narration="A narrated response",
        actions=[PlannedAction(action_type=PlannedActionType.STORY_LOG_APPEND, args={"entry_type": "narration", "content": "A narrated response"})],
        llm_model="dm-stub-v1",
    )))
    monkeypatch.setattr(main_module, "dispatch_plan", AsyncMock(return_value=[SideEffectResult(action_type="story_log_append", success=True, detail="story_log_appended")]))

    response = client.post(
        "/turn",
        json={
            "turn_id": "turn-003",
            "campaign_id": str(_CAMP),
            "session_id": str(_SESSION),
            "user_id": str(_USER),
            "character_id": str(_CHAR),
            "input_text": "Describe the hall",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["turn_id"] == "turn-003"
    assert body["selected_action"] == "story_log_append"
    assert body["narration"] == "A narrated response"
    assert body["side_effects"][0]["success"] is True
    db_module.save_turn.assert_awaited_once()
    event_log_module.emit.assert_awaited_once()


def test_list_turns_returns_records(client, monkeypatch):
    monkeypatch.setattr(
        db_module,
        "list_turns",
        AsyncMock(
            return_value=[
                type(
                    "Turn",
                    (),
                    {
                        "campaign_id": _CAMP,
                        "session_id": _SESSION,
                        "turn_id": "turn-list-1",
                        "user_id": _USER,
                        "character_id": _CHAR,
                        "input_text": "input",
                        "selected_action": "narrate",
                        "narration": "n",
                        "llm_model": "m",
                        "llm_prompt_hash": "h",
                        "side_effects": [],
                        "context": _context().model_dump(mode="json"),
                        "created_at": _NOW,
                        "updated_at": _NOW,
                    },
                )()
            ]
        ),
    )

    response = client.get("/turns", params={"campaign_id": str(_CAMP), "session_id": str(_SESSION)})
    assert response.status_code == 200
    assert response.json()[0]["turn_id"] == "turn-list-1"


def test_get_turn_record_404_when_missing(client, monkeypatch):
    monkeypatch.setattr(db_module, "get_turn", AsyncMock(return_value=None))

    response = client.get("/turns/missing", params={"campaign_id": str(_CAMP), "session_id": str(_SESSION)})
    assert response.status_code == 404


def test_delete_turn_record_204(client, monkeypatch):
    monkeypatch.setattr(db_module, "delete_turn", AsyncMock(return_value=True))

    response = client.delete("/turns/turn-del", params={"campaign_id": str(_CAMP), "session_id": str(_SESSION)})
    assert response.status_code == 204