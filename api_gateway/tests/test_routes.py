"""Route tests for API Gateway / Session API."""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.service_clients as clients
import app.session_store as session_store
from app.main import app

_CAMP = uuid4()
_USER = uuid4()
_CHAR = uuid4()


@pytest.fixture(autouse=True)
def clear_sessions():
    session_store.clear_sessions()
    yield
    session_store.clear_sessions()


@pytest.fixture
def client():
    return TestClient(app)


def test_health_reports_checks(client, monkeypatch):
    monkeypatch.setattr(clients, "health_check", AsyncMock(side_effect=[True, True, True, True, True, True, True, True, True]))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_session_start_validates_character_ownership(client, monkeypatch):
    monkeypatch.setattr(clients, "get_character", AsyncMock(return_value={"character_id": str(_CHAR), "campaign_id": str(_CAMP), "user_id": str(_USER)}))

    response = client.post(
        "/session/start",
        json={"user_id": str(_USER), "campaign_id": str(_CAMP), "character_id": str(_CHAR)},
    )

    assert response.status_code == 200
    assert response.json()["session"]["campaign_id"] == str(_CAMP)


def test_session_state_aggregates_services(client, monkeypatch):
    monkeypatch.setattr(clients, "get_character", AsyncMock(return_value={"character_id": str(_CHAR), "campaign_id": str(_CAMP), "user_id": str(_USER)}))
    start = client.post("/session/start", json={"user_id": str(_USER), "campaign_id": str(_CAMP), "character_id": str(_CHAR)})
    session_id = start.json()["session"]["session_id"]

    monkeypatch.setattr(clients, "get_active_encounter", AsyncMock(return_value={"encounter_id": "e1"}))
    monkeypatch.setattr(clients, "get_map_snapshot", AsyncMock(return_value={"map": {"map_id": "m1"}}))
    monkeypatch.setattr(clients, "get_story_context", AsyncMock(return_value={"active_quests": [], "open_hooks": [], "recent_log": []}))
    monkeypatch.setattr(clients, "list_recent_events", AsyncMock(return_value=[]))

    response = client.get(f"/session/{session_id}/state")

    assert response.status_code == 200
    body = response.json()
    assert body["active_encounter"]["encounter_id"] == "e1"
    assert body["map_snapshot"]["map"]["map_id"] == "m1"


def test_session_turn_forwards_to_dm(client, monkeypatch):
    monkeypatch.setattr(clients, "get_character", AsyncMock(return_value={"character_id": str(_CHAR), "campaign_id": str(_CAMP), "user_id": str(_USER)}))
    start = client.post("/session/start", json={"user_id": str(_USER), "campaign_id": str(_CAMP), "character_id": str(_CHAR)})
    session_id = start.json()["session"]["session_id"]

    monkeypatch.setattr(clients, "run_dm_turn", AsyncMock(return_value={"selected_action": "narrate", "narration": "ok"}))

    response = client.post(
        "/session/turn",
        json={"session_id": session_id, "turn_id": "t1", "input_text": "I move forward"},
    )

    assert response.status_code == 200
    assert response.json()["selected_action"] == "narrate"


def test_session_combat_action_forwards(client, monkeypatch):
    monkeypatch.setattr(clients, "get_character", AsyncMock(return_value={"character_id": str(_CHAR), "campaign_id": str(_CAMP), "user_id": str(_USER)}))
    start = client.post("/session/start", json={"user_id": str(_USER), "campaign_id": str(_CAMP), "character_id": str(_CHAR)})
    session_id = start.json()["session"]["session_id"]
    monkeypatch.setattr(clients, "run_combat_action", AsyncMock(return_value={"round": 2, "current_turn_index": 0}))

    response = client.post(
        "/session/combat/action",
        json={"session_id": session_id, "action": "next_turn", "payload": {}},
    )

    assert response.status_code == 200
    assert response.json()["round"] == 2