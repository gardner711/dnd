"""Route tests — all I/O mocked, no real DB / Redis / HTTP."""
from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.database as db_module
import app.event_log as event_log_module
import app.redis_client as redis_client_module
import app.service_clients as service_clients_module
from app.dependencies import get_db_conn
from app.main import app

_NOW     = datetime.now(UTC)
_CAMP    = uuid4()
_NPC     = uuid4()
_CHAR    = uuid4()
_SECRET  = uuid4()
_SESSION = uuid4()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _npc_row(**kw) -> dict:
    return {
        "npc_id": _NPC, "campaign_id": _CAMP,
        "name": "Elara the Innkeeper", "role": "innkeeper",
        "physical_description": "Silver-haired woman",
        "personality_prompt": "Warm but suspicious of strangers.",
        "is_active": True, "faction_id": None,
        "created_at": _NOW, "updated_at": _NOW,
        **kw,
    }


def _secret_row(**kw) -> dict:
    return {
        "secret_id": _SECRET, "npc_id": _NPC, "campaign_id": _CAMP,
        "content": "Her daughter is missing",
        "condition_type": "always",
        "condition_value": None, "condition_quest_title": None,
        "condition_quest_status": None, "revealed_at": None,
        **kw,
    }


_NPC_CREATE = {
    "campaign_id": str(_CAMP),
    "name": "Elara the Innkeeper",
    "role": "innkeeper",
    "personality_prompt": "Warm but suspicious.",
}

_CONTEXT_REQUEST = {
    "campaign_id": str(_CAMP),
    "session_id": str(_SESSION),
    "character_id": str(_CHAR),
    "player_message": "Have you seen anything unusual lately?",
}


# ── Fixtures ──────────────────────────────────────────────────────────────────

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
    cm.__aexit__  = AsyncMock(return_value=None)
    pool.acquire  = MagicMock(return_value=cm)
    return pool


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.ping = AsyncMock(return_value=True)
    return r


@pytest.fixture
def client(mock_pool, mock_conn, mock_redis, monkeypatch):
    monkeypatch.setattr(db_module,        "get_pool",       AsyncMock(return_value=mock_pool))
    monkeypatch.setattr(db_module,        "run_migrations", AsyncMock(return_value=None))
    monkeypatch.setattr(db_module,        "close_pool",     AsyncMock(return_value=None))
    monkeypatch.setattr(event_log_module, "emit",           AsyncMock(return_value=None))
    # Redis
    monkeypatch.setattr(redis_client_module, "get_redis",            AsyncMock(return_value=mock_redis))
    monkeypatch.setattr(redis_client_module, "close_redis",          AsyncMock(return_value=None))
    monkeypatch.setattr(redis_client_module, "get_dialogue_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(redis_client_module, "append_dialogue_turn", AsyncMock(return_value=None))
    monkeypatch.setattr(redis_client_module, "clear_dialogue",       AsyncMock(return_value=None))
    # External service clients
    monkeypatch.setattr(service_clients_module, "get_npc_disposition", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(service_clients_module, "get_quest_map",       AsyncMock(return_value={}))
    monkeypatch.setattr(service_clients_module, "recall_memories",     AsyncMock(return_value=None))
    monkeypatch.setattr(service_clients_module, "get_faction_standing", AsyncMock(return_value=None))

    async def override_db():
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_db
    with TestClient(app) as c:
        yield c, mock_conn
    app.dependency_overrides.clear()


# ── Health ────────────────────────────────────────────────────────────────────

def test_health_ok(client):
    c, _ = client
    resp = c.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["checks"]["database"] is True
    assert data["checks"]["redis"] is True


def test_health_db_down(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_pool", AsyncMock(side_effect=Exception("db down")))
    resp = c.get("/health")
    assert resp.status_code == 503
    assert resp.json()["checks"]["database"] is False


def test_health_redis_down(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(redis_client_module, "get_redis", AsyncMock(side_effect=Exception("redis down")))
    resp = c.get("/health")
    assert resp.status_code == 503
    assert resp.json()["checks"]["redis"] is False


# ── NPC Profiles ──────────────────────────────────────────────────────────────

def test_create_npc_201(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "create_npc", AsyncMock(return_value=_npc_row()))
    resp = c.post("/npcs", json=_NPC_CREATE)
    assert resp.status_code == 201
    assert resp.json()["name"] == "Elara the Innkeeper"


def test_create_npc_emits_created_event(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "create_npc", AsyncMock(return_value=_npc_row()))
    c.post("/npcs", json=_NPC_CREATE)
    event_log_module.emit.assert_called_once()
    assert event_log_module.emit.call_args.kwargs["event_type"] == "npc.created"


def test_create_npc_personality_too_long_422(client):
    c, _ = client
    resp = c.post("/npcs", json={**_NPC_CREATE, "personality_prompt": "x" * 2001})
    assert resp.status_code == 422


def test_list_npcs_returns_200(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "list_npcs", AsyncMock(return_value=[_npc_row()]))
    resp = c.get(f"/npcs?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_list_npcs_active_only_by_default(client, monkeypatch):
    c, _ = client
    mock_list = AsyncMock(return_value=[])
    monkeypatch.setattr(db_module, "list_npcs", mock_list)
    c.get(f"/npcs?campaign_id={_CAMP}")
    _, kwargs = mock_list.call_args
    assert kwargs.get("active_only") is True


def test_list_npcs_requires_campaign_id(client):
    c, _ = client
    assert c.get("/npcs").status_code == 422


def test_get_npc_found(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_npc", AsyncMock(return_value=_npc_row()))
    resp = c.get(f"/npcs/{_NPC}?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert resp.json()["npc_id"] == str(_NPC)


def test_get_npc_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_npc", AsyncMock(return_value=None))
    assert c.get(f"/npcs/{_NPC}?campaign_id={_CAMP}").status_code == 404


def test_get_npc_requires_campaign_id(client):
    c, _ = client
    assert c.get(f"/npcs/{_NPC}").status_code == 422


def test_patch_npc_200(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "patch_npc", AsyncMock(return_value=_npc_row(name="Renamed")))
    resp = c.patch(f"/npcs/{_NPC}?campaign_id={_CAMP}", json={"name": "Renamed"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"
    event_log_module.emit.assert_called_once()
    assert event_log_module.emit.call_args.kwargs["event_type"] == "npc.updated"


def test_patch_npc_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "patch_npc", AsyncMock(return_value=None))
    assert c.patch(f"/npcs/{_NPC}?campaign_id={_CAMP}", json={"name": "X"}).status_code == 404


def test_patch_npc_personality_too_long_422(client):
    c, _ = client
    resp = c.patch(f"/npcs/{_NPC}?campaign_id={_CAMP}", json={"personality_prompt": "y" * 2001})
    assert resp.status_code == 422


def test_deactivate_npc_204(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "deactivate_npc", AsyncMock(return_value=True))
    assert c.delete(f"/npcs/{_NPC}?campaign_id={_CAMP}").status_code == 204


def test_deactivate_npc_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "deactivate_npc", AsyncMock(return_value=False))
    assert c.delete(f"/npcs/{_NPC}?campaign_id={_CAMP}").status_code == 404


# ── Secrets ───────────────────────────────────────────────────────────────────

def test_add_secret_201(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_npc",       AsyncMock(return_value=_npc_row()))
    monkeypatch.setattr(db_module, "create_secret", AsyncMock(return_value=_secret_row()))
    resp = c.post(
        f"/npcs/{_NPC}/secrets?campaign_id={_CAMP}",
        json={"content": "Her daughter is missing", "condition_type": "always"},
    )
    assert resp.status_code == 201
    assert resp.json()["content"] == "Her daughter is missing"


def test_add_secret_npc_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_npc", AsyncMock(return_value=None))
    resp = c.post(
        f"/npcs/{_NPC}/secrets?campaign_id={_CAMP}",
        json={"content": "Secret", "condition_type": "always"},
    )
    assert resp.status_code == 404


def test_add_secret_requires_campaign_id(client):
    c, _ = client
    resp = c.post(f"/npcs/{_NPC}/secrets", json={"content": "X", "condition_type": "always"})
    assert resp.status_code == 422


def test_list_secrets_200(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "list_secrets", AsyncMock(return_value=[_secret_row()]))
    resp = c.get(f"/npcs/{_NPC}/secrets?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_patch_secret_200(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "patch_secret",
                        AsyncMock(return_value=_secret_row(content="Updated secret")))
    resp = c.patch(
        f"/npcs/{_NPC}/secrets/{_SECRET}?campaign_id={_CAMP}",
        json={"content": "Updated secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == "Updated secret"


def test_patch_secret_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "patch_secret", AsyncMock(return_value=None))
    assert c.patch(
        f"/npcs/{_NPC}/secrets/{_SECRET}?campaign_id={_CAMP}", json={"content": "X"},
    ).status_code == 404


def test_delete_secret_204(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "delete_secret", AsyncMock(return_value=True))
    assert c.delete(f"/npcs/{_NPC}/secrets/{_SECRET}?campaign_id={_CAMP}").status_code == 204


def test_delete_secret_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "delete_secret", AsyncMock(return_value=False))
    assert c.delete(f"/npcs/{_NPC}/secrets/{_SECRET}?campaign_id={_CAMP}").status_code == 404


# ── Context Assembly ──────────────────────────────────────────────────────────

def test_context_basic_200(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_npc",      AsyncMock(return_value=_npc_row()))
    monkeypatch.setattr(db_module, "list_secrets", AsyncMock(return_value=[]))
    resp = c.post(f"/npcs/{_NPC}/context", json=_CONTEXT_REQUEST)
    assert resp.status_code == 200
    body = resp.json()
    assert body["npc_name"] == "Elara the Innkeeper"
    assert "system_prompt" in body
    assert body["secrets_injected_count"] == 0


def test_context_npc_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_npc", AsyncMock(return_value=None))
    assert c.post(f"/npcs/{_NPC}/context", json=_CONTEXT_REQUEST).status_code == 404


def test_context_always_secret_injected(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_npc",             AsyncMock(return_value=_npc_row()))
    monkeypatch.setattr(db_module, "list_secrets",        AsyncMock(return_value=[_secret_row()]))
    monkeypatch.setattr(db_module, "mark_secret_revealed", AsyncMock(return_value=True))
    resp = c.post(f"/npcs/{_NPC}/context", json=_CONTEXT_REQUEST)
    assert resp.status_code == 200
    body = resp.json()
    assert body["secrets_injected_count"] == 1
    assert body["secrets_injected"][0]["content"] == "Her daughter is missing"
    assert body["secrets_injected"][0]["first_revealed"] is True
    assert "Her daughter is missing" in body["system_prompt"]


def test_context_disposition_gte_met(client, monkeypatch):
    c, _ = client
    secret = _secret_row(condition_type="disposition_gte", condition_value=70)
    monkeypatch.setattr(db_module, "get_npc",              AsyncMock(return_value=_npc_row()))
    monkeypatch.setattr(db_module, "list_secrets",         AsyncMock(return_value=[secret]))
    monkeypatch.setattr(db_module, "mark_secret_revealed", AsyncMock(return_value=True))
    monkeypatch.setattr(service_clients_module, "get_npc_disposition", AsyncMock(return_value=(75, "Was kind to her cat")))
    resp = c.post(f"/npcs/{_NPC}/context", json=_CONTEXT_REQUEST)
    assert resp.json()["secrets_injected_count"] == 1
    assert resp.json()["disposition_score"] == 75
    assert resp.json()["disposition_label"] == "friendly"
    assert resp.json()["disposition_notes"] == "Was kind to her cat"


def test_context_disposition_gte_not_met(client, monkeypatch):
    c, _ = client
    secret = _secret_row(condition_type="disposition_gte", condition_value=70)
    monkeypatch.setattr(db_module, "get_npc",      AsyncMock(return_value=_npc_row()))
    monkeypatch.setattr(db_module, "list_secrets", AsyncMock(return_value=[secret]))
    monkeypatch.setattr(service_clients_module, "get_npc_disposition", AsyncMock(return_value=(40, None)))
    resp = c.post(f"/npcs/{_NPC}/context", json=_CONTEXT_REQUEST)
    assert resp.json()["secrets_injected_count"] == 0


def test_context_quest_status_met(client, monkeypatch):
    c, _ = client
    secret = _secret_row(
        condition_type="quest_status",
        condition_quest_title="Find the artifact",
        condition_quest_status="completed",
    )
    monkeypatch.setattr(db_module, "get_npc",              AsyncMock(return_value=_npc_row()))
    monkeypatch.setattr(db_module, "list_secrets",         AsyncMock(return_value=[secret]))
    monkeypatch.setattr(db_module, "mark_secret_revealed", AsyncMock(return_value=True))
    monkeypatch.setattr(service_clients_module, "get_quest_map",
                        AsyncMock(return_value={"Find the artifact": "completed"}))
    resp = c.post(f"/npcs/{_NPC}/context", json=_CONTEXT_REQUEST)
    assert resp.json()["secrets_injected_count"] == 1


def test_context_first_reveal_emits_event(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_npc",              AsyncMock(return_value=_npc_row()))
    monkeypatch.setattr(db_module, "list_secrets",         AsyncMock(return_value=[_secret_row()]))
    monkeypatch.setattr(db_module, "mark_secret_revealed", AsyncMock(return_value=True))
    c.post(f"/npcs/{_NPC}/context", json=_CONTEXT_REQUEST)
    event_log_module.emit.assert_called_once()
    assert event_log_module.emit.call_args.kwargs["event_type"] == "npc.secret_revealed"


def test_context_repeated_reveal_no_event(client, monkeypatch):
    c, _ = client
    # revealed_at is already set → first_revealed=False → no event
    already_revealed = _secret_row(revealed_at=_NOW)
    monkeypatch.setattr(db_module, "get_npc",      AsyncMock(return_value=_npc_row()))
    monkeypatch.setattr(db_module, "list_secrets", AsyncMock(return_value=[already_revealed]))
    c.post(f"/npcs/{_NPC}/context", json=_CONTEXT_REQUEST)
    event_log_module.emit.assert_not_called()


def test_context_graceful_when_services_down(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_npc",      AsyncMock(return_value=_npc_row()))
    monkeypatch.setattr(db_module, "list_secrets", AsyncMock(return_value=[]))
    monkeypatch.setattr(service_clients_module, "get_npc_disposition", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(service_clients_module, "get_quest_map",       AsyncMock(return_value={}))
    monkeypatch.setattr(service_clients_module, "recall_memories",     AsyncMock(return_value=None))
    resp = c.post(f"/npcs/{_NPC}/context", json=_CONTEXT_REQUEST)
    assert resp.status_code == 200
    body = resp.json()
    assert body["disposition_score"] is None
    assert body["disposition_label"] == "unknown"
    assert body["memory_context"] is None


def test_context_includes_dialogue_history(client, monkeypatch):
    c, _ = client
    history = [
        {"role": "player", "content": "Hello", "ts": _NOW.isoformat()},
        {"role": "npc",    "content": "Greetings", "ts": _NOW.isoformat()},
    ]
    monkeypatch.setattr(db_module, "get_npc",      AsyncMock(return_value=_npc_row()))
    monkeypatch.setattr(db_module, "list_secrets", AsyncMock(return_value=[]))
    monkeypatch.setattr(redis_client_module, "get_dialogue_history", AsyncMock(return_value=history))
    resp = c.post(f"/npcs/{_NPC}/context", json=_CONTEXT_REQUEST)
    assert len(resp.json()["dialogue_history"]) == 2


def test_context_includes_memory(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_npc",      AsyncMock(return_value=_npc_row()))
    monkeypatch.setattr(db_module, "list_secrets", AsyncMock(return_value=[]))
    monkeypatch.setattr(service_clients_module, "recall_memories",
                        AsyncMock(return_value="Party helped her last winter."))
    resp = c.post(f"/npcs/{_NPC}/context", json=_CONTEXT_REQUEST)
    body = resp.json()
    assert body["memory_context"] == "Party helped her last winter."
    assert "Party helped her last winter." in body["system_prompt"]


# ── Dialogue History ──────────────────────────────────────────────────────────

def test_append_dialogue_201(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_npc", AsyncMock(return_value=_npc_row()))
    resp = c.post(f"/npcs/{_NPC}/dialogue", json={
        "campaign_id": str(_CAMP), "session_id": str(_SESSION),
        "player_message": "Hello", "npc_response": "Good day.",
    })
    assert resp.status_code == 201
    redis_client_module.append_dialogue_turn.assert_called_once()


def test_append_dialogue_npc_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_npc", AsyncMock(return_value=None))
    resp = c.post(f"/npcs/{_NPC}/dialogue", json={
        "campaign_id": str(_CAMP), "session_id": str(_SESSION),
        "player_message": "Hello", "npc_response": "Hi.",
    })
    assert resp.status_code == 404


def test_clear_dialogue_204(client):
    c, _ = client
    resp = c.delete(f"/npcs/{_NPC}/dialogue?campaign_id={_CAMP}&session_id={_SESSION}")
    assert resp.status_code == 204
    redis_client_module.clear_dialogue.assert_called_once()


def test_clear_dialogue_requires_session_id(client):
    c, _ = client
    assert c.delete(f"/npcs/{_NPC}/dialogue?campaign_id={_CAMP}").status_code == 422


# ── GET /dialogue ─────────────────────────────────────────────────────────────

def test_get_dialogue_200(client, monkeypatch):
    c, _ = client
    history = [
        {"role": "player", "content": "Hello", "ts": _NOW.isoformat()},
        {"role": "npc",    "content": "Greetings", "ts": _NOW.isoformat()},
    ]
    monkeypatch.setattr(redis_client_module, "get_dialogue_history", AsyncMock(return_value=history))
    resp = c.get(f"/npcs/{_NPC}/dialogue?campaign_id={_CAMP}&session_id={_SESSION}")
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    assert resp.json()[0]["role"] == "player"


def test_get_dialogue_requires_session_id(client):
    c, _ = client
    assert c.get(f"/npcs/{_NPC}/dialogue?campaign_id={_CAMP}").status_code == 422


def test_get_dialogue_requires_campaign_id(client):
    c, _ = client
    assert c.get(f"/npcs/{_NPC}/dialogue?session_id={_SESSION}").status_code == 422


def test_get_dialogue_empty(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(redis_client_module, "get_dialogue_history", AsyncMock(return_value=[]))
    resp = c.get(f"/npcs/{_NPC}/dialogue?campaign_id={_CAMP}&session_id={_SESSION}")
    assert resp.status_code == 200
    assert resp.json() == []


# ── clear_physical_description ────────────────────────────────────────────────

def test_patch_npc_clear_physical_description(client, monkeypatch):
    c, _ = client
    cleared = _npc_row(physical_description=None)
    mock_patch = AsyncMock(return_value=cleared)
    monkeypatch.setattr(db_module, "patch_npc", mock_patch)
    resp = c.patch(
        f"/npcs/{_NPC}?campaign_id={_CAMP}",
        json={"clear_physical_description": True},
    )
    assert resp.status_code == 200
    assert resp.json()["physical_description"] is None
    _, kwargs = mock_patch.call_args
    assert kwargs.get("clear_physical_description") is True


# ── Faction disposition fallback ──────────────────────────────────────────────

def test_context_faction_fallback_used_when_no_char_score(client, monkeypatch):
    c, _ = client
    # NPC belongs to a faction
    npc_with_faction = _npc_row(faction_id=uuid4())
    monkeypatch.setattr(db_module, "get_npc",      AsyncMock(return_value=npc_with_faction))
    monkeypatch.setattr(db_module, "list_secrets", AsyncMock(return_value=[]))
    # No character-specific score, but faction has 65 standing
    monkeypatch.setattr(service_clients_module, "get_npc_disposition",  AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(service_clients_module, "get_faction_standing",  AsyncMock(return_value=65))
    resp = c.post(f"/npcs/{_NPC}/context", json=_CONTEXT_REQUEST)
    assert resp.status_code == 200
    body = resp.json()
    assert body["disposition_score"] == 65         # faction fallback used
    assert body["faction_standing"] == 65
    assert body["disposition_label"] == "friendly"  # 65 falls in friendly range (61-80)


def test_context_char_score_takes_priority_over_faction(client, monkeypatch):
    c, _ = client
    npc_with_faction = _npc_row(faction_id=uuid4())
    monkeypatch.setattr(db_module, "get_npc",      AsyncMock(return_value=npc_with_faction))
    monkeypatch.setattr(db_module, "list_secrets", AsyncMock(return_value=[]))
    # Both scores present — character-specific wins
    monkeypatch.setattr(service_clients_module, "get_npc_disposition",  AsyncMock(return_value=(80, None)))
    monkeypatch.setattr(service_clients_module, "get_faction_standing",  AsyncMock(return_value=40))
    resp = c.post(f"/npcs/{_NPC}/context", json=_CONTEXT_REQUEST)
    body = resp.json()
    assert body["disposition_score"] == 80         # character score wins
    assert body["faction_standing"] == 40


def test_context_faction_not_fetched_when_no_faction_id(client, monkeypatch):
    c, _ = client
    # NPC has no faction_id
    monkeypatch.setattr(db_module, "get_npc",      AsyncMock(return_value=_npc_row(faction_id=None)))
    monkeypatch.setattr(db_module, "list_secrets", AsyncMock(return_value=[]))
    c.post(f"/npcs/{_NPC}/context", json=_CONTEXT_REQUEST)
    service_clients_module.get_faction_standing.assert_not_called()


# ── Disposition notes in prompt ───────────────────────────────────────────────

def test_context_disposition_notes_in_response(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_npc",      AsyncMock(return_value=_npc_row()))
    monkeypatch.setattr(db_module, "list_secrets", AsyncMock(return_value=[]))
    monkeypatch.setattr(service_clients_module, "get_npc_disposition",
                        AsyncMock(return_value=(70, "Helped recover lost goods last winter")))
    resp = c.post(f"/npcs/{_NPC}/context", json=_CONTEXT_REQUEST)
    body = resp.json()
    assert body["disposition_notes"] == "Helped recover lost goods last winter"
    assert "Helped recover lost goods last winter" in body["system_prompt"]
