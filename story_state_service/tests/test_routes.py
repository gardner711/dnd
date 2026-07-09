"""Route tests — all database operations mocked, no real I/O."""
from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.database as db_module
import app.event_log as event_log_module
from app.dependencies import get_db_conn
from app.main import app

_NOW     = datetime.now(UTC)
_CAMP    = uuid4()
_SESSION = uuid4()
_QUEST   = uuid4()
_OBJ     = uuid4()
_HOOK    = uuid4()
_ENTRY   = uuid4()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _quest_row(**kw) -> dict:
    return {
        "quest_id": _QUEST, "campaign_id": _CAMP,
        "title": "Find the artifact", "description": "Retrieve it",
        "status": "active", "giver_npc_id": None, "reward_description": "100 gold",
        "started_at": _NOW, "completed_at": None, "updated_at": _NOW,
        **kw,
    }


def _obj_row(**kw) -> dict:
    return {
        "objective_id": _OBJ, "quest_id": _QUEST, "campaign_id": _CAMP,
        "description": "Reach the dungeon", "sequence_order": 0, "completed_at": None,
        **kw,
    }


def _hook_row(**kw) -> dict:
    return {
        "hook_id": _HOOK, "campaign_id": _CAMP,
        "content": "The innkeeper mentioned a missing shipment",
        "status": "open", "priority": "medium",
        "source_event_id": None, "created_at": _NOW, "resolved_at": None,
        **kw,
    }


def _log_row(**kw) -> dict:
    return {
        "entry_id": _ENTRY, "campaign_id": _CAMP, "session_id": _SESSION,
        "entry_type": "narration", "content": "The party entered the dungeon",
        "created_at": _NOW,
        **kw,
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
def client(mock_pool, mock_conn, monkeypatch):
    monkeypatch.setattr(db_module,        "get_pool",       AsyncMock(return_value=mock_pool))
    monkeypatch.setattr(db_module,        "run_migrations", AsyncMock(return_value=None))
    monkeypatch.setattr(db_module,        "close_pool",     AsyncMock(return_value=None))
    monkeypatch.setattr(event_log_module, "emit",           AsyncMock(return_value=None))
    # Default: quests have no objectives (overridden per-test when needed)
    monkeypatch.setattr(db_module, "get_quest_objectives",      AsyncMock(return_value=[]))
    monkeypatch.setattr(db_module, "get_quest_objectives_bulk", AsyncMock(return_value=[]))

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
    assert resp.json()["status"] == "ok"
    assert resp.json()["checks"]["database"] is True


def test_health_db_down(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_pool", AsyncMock(side_effect=Exception("db down")))
    resp = c.get("/health")
    assert resp.status_code == 503
    assert resp.json()["checks"]["database"] is False


# ── Quests (player-visible) ───────────────────────────────────────────────────

def test_create_quest_active_201(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "create_quest", AsyncMock(return_value=_quest_row()))
    monkeypatch.setattr(db_module, "add_objective", AsyncMock(return_value=None))
    resp = c.post("/quests", json={
        "campaign_id": str(_CAMP), "title": "Find the artifact",
        "description": "Retrieve it", "status": "active",
    })
    assert resp.status_code == 201
    assert resp.json()["title"] == "Find the artifact"
    assert resp.json()["status"] == "active"


def test_create_quest_emits_started_event(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "create_quest", AsyncMock(return_value=_quest_row()))
    monkeypatch.setattr(db_module, "add_objective", AsyncMock(return_value=None))
    c.post("/quests", json={
        "campaign_id": str(_CAMP), "title": "Find the artifact", "status": "active",
    })
    event_log_module.emit.assert_called_once()
    assert event_log_module.emit.call_args.kwargs["event_type"] == "story.quest_started"


def test_create_quest_hidden_no_event(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "create_quest", AsyncMock(return_value=_quest_row(status="hidden")))
    monkeypatch.setattr(db_module, "add_objective", AsyncMock(return_value=None))
    resp = c.post("/quests", json={
        "campaign_id": str(_CAMP), "title": "Secret quest", "status": "hidden",
    })
    assert resp.status_code == 201
    event_log_module.emit.assert_not_called()


def test_create_quest_with_objectives(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "create_quest", AsyncMock(return_value=_quest_row()))
    monkeypatch.setattr(db_module, "add_objective", AsyncMock(return_value=_obj_row()))
    monkeypatch.setattr(db_module, "get_quest_objectives", AsyncMock(return_value=[_obj_row()]))
    resp = c.post("/quests", json={
        "campaign_id": str(_CAMP), "title": "Find the artifact",
        "objectives": [{"description": "Reach the dungeon", "sequence_order": 0}],
    })
    assert resp.status_code == 201
    assert len(resp.json()["objectives"]) == 1
    assert resp.json()["objectives"][0]["description"] == "Reach the dungeon"


def test_list_quests_returns_200(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "list_quests", AsyncMock(return_value=[_quest_row()]))
    resp = c.get(f"/quests?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_list_quests_requires_campaign_id(client):
    c, _ = client
    resp = c.get("/quests")
    assert resp.status_code == 422


def test_list_quests_status_filter(client, monkeypatch):
    c, _ = client
    mock_list = AsyncMock(return_value=[])
    monkeypatch.setattr(db_module, "list_quests", mock_list)
    c.get(f"/quests?campaign_id={_CAMP}&status=active")
    mock_list.assert_called_once()
    _, kwargs = mock_list.call_args
    assert kwargs.get("status") == "active" or mock_list.call_args.args[2] == "active"


def test_get_quest_found(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_quest", AsyncMock(return_value=_quest_row()))
    resp = c.get(f"/quests/{_QUEST}?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert resp.json()["quest_id"] == str(_QUEST)


def test_get_quest_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_quest", AsyncMock(return_value=None))
    resp = c.get(f"/quests/{_QUEST}?campaign_id={_CAMP}")
    assert resp.status_code == 404


def test_get_quest_requires_campaign_id(client):
    c, _ = client
    resp = c.get(f"/quests/{_QUEST}")
    assert resp.status_code == 422


def test_patch_quest_completed_emits_event(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "patch_quest", AsyncMock(return_value=_quest_row(status="completed")))
    c.patch(f"/quests/{_QUEST}?campaign_id={_CAMP}", json={"status": "completed"})
    event_log_module.emit.assert_called_once()
    assert event_log_module.emit.call_args.kwargs["event_type"] == "story.quest_completed"


def test_patch_quest_failed_emits_event(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "patch_quest", AsyncMock(return_value=_quest_row(status="failed")))
    c.patch(f"/quests/{_QUEST}?campaign_id={_CAMP}", json={"status": "failed"})
    event_log_module.emit.assert_called_once()
    assert event_log_module.emit.call_args.kwargs["event_type"] == "story.quest_failed"


def test_patch_quest_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "patch_quest", AsyncMock(return_value=None))
    resp = c.patch(f"/quests/{_QUEST}?campaign_id={_CAMP}", json={"title": "New title"})
    assert resp.status_code == 404


def test_patch_quest_requires_campaign_id(client):
    c, _ = client
    resp = c.patch(f"/quests/{_QUEST}", json={"title": "New title"})
    assert resp.status_code == 422


def test_delete_quest_204(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "delete_quest", AsyncMock(return_value=True))
    resp = c.delete(f"/quests/{_QUEST}?campaign_id={_CAMP}")
    assert resp.status_code == 204


def test_delete_quest_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "delete_quest", AsyncMock(return_value=False))
    resp = c.delete(f"/quests/{_QUEST}?campaign_id={_CAMP}")
    assert resp.status_code == 404


def test_complete_objective(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "patch_objective", AsyncMock(return_value=_obj_row(completed_at=_NOW)))
    resp = c.patch(
        f"/quests/{_QUEST}/objectives/{_OBJ}?campaign_id={_CAMP}",
        json={"completed": True},
    )
    assert resp.status_code == 200
    assert resp.json()["objective_id"] == str(_OBJ)
    event_log_module.emit.assert_called_once()
    assert event_log_module.emit.call_args.kwargs["event_type"] == "story.objective_completed"


def test_complete_objective_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "patch_objective", AsyncMock(return_value=None))
    resp = c.patch(
        f"/quests/{_QUEST}/objectives/{_OBJ}?campaign_id={_CAMP}",
        json={"completed": True},
    )
    assert resp.status_code == 404


def test_uncomplete_objective_no_event(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "patch_objective", AsyncMock(return_value=_obj_row()))
    c.patch(
        f"/quests/{_QUEST}/objectives/{_OBJ}?campaign_id={_CAMP}",
        json={"completed": False},
    )
    event_log_module.emit.assert_not_called()


# ── DM quests (all statuses including hidden) ─────────────────────────────────

def test_dm_list_quests_returns_200(client, monkeypatch):
    c, _ = client
    mock_list = AsyncMock(return_value=[_quest_row(status="hidden"), _quest_row()])
    monkeypatch.setattr(db_module, "list_quests", mock_list)
    resp = c.get(f"/dm/quests?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    # Verify include_hidden=True was passed
    _, kwargs = mock_list.call_args
    assert kwargs.get("include_hidden") is True
    # Verify bulk objectives fetch was used (not per-row N+1)
    db_module.get_quest_objectives_bulk.assert_called_once()


def test_dm_list_quests_requires_campaign_id(client):
    c, _ = client
    resp = c.get("/dm/quests")
    assert resp.status_code == 422


def test_dm_get_quest_hidden_ok(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_quest", AsyncMock(return_value=_quest_row(status="hidden")))
    resp = c.get(f"/dm/quests/{_QUEST}?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "hidden"


def test_dm_get_quest_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_quest", AsyncMock(return_value=None))
    resp = c.get(f"/dm/quests/{_QUEST}?campaign_id={_CAMP}")
    assert resp.status_code == 404


def test_dm_get_quest_include_hidden_true(client, monkeypatch):
    c, _ = client
    mock_get = AsyncMock(return_value=_quest_row(status="hidden"))
    monkeypatch.setattr(db_module, "get_quest", mock_get)
    c.get(f"/dm/quests/{_QUEST}?campaign_id={_CAMP}")
    _, kwargs = mock_get.call_args
    assert kwargs.get("include_hidden") is True


# ── Plot Hooks ────────────────────────────────────────────────────────────────

def test_create_hook_201(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "create_hook", AsyncMock(return_value=_hook_row()))
    resp = c.post("/hooks", json={
        "campaign_id": str(_CAMP),
        "content": "The innkeeper mentioned a missing shipment",
        "priority": "medium",
    })
    assert resp.status_code == 201
    assert resp.json()["content"] == "The innkeeper mentioned a missing shipment"


def test_create_hook_emits_event(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "create_hook", AsyncMock(return_value=_hook_row()))
    c.post("/hooks", json={"campaign_id": str(_CAMP), "content": "Mystery", "priority": "high"})
    event_log_module.emit.assert_called_once()
    assert event_log_module.emit.call_args.kwargs["event_type"] == "story.hook_created"


def test_list_hooks_no_filter(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "list_hooks", AsyncMock(return_value=[_hook_row()]))
    resp = c.get(f"/hooks?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_list_hooks_requires_campaign_id(client):
    c, _ = client
    resp = c.get("/hooks")
    assert resp.status_code == 422


def test_list_hooks_status_filter(client, monkeypatch):
    c, _ = client
    mock_list = AsyncMock(return_value=[])
    monkeypatch.setattr(db_module, "list_hooks", mock_list)
    c.get(f"/hooks?campaign_id={_CAMP}&status=open")
    mock_list.assert_called_once()
    # status should be passed as 'open'
    args = mock_list.call_args.args
    assert "open" in args


def test_list_hooks_priority_filter(client, monkeypatch):
    c, _ = client
    mock_list = AsyncMock(return_value=[])
    monkeypatch.setattr(db_module, "list_hooks", mock_list)
    c.get(f"/hooks?campaign_id={_CAMP}&priority=critical")
    mock_list.assert_called_once()
    args = mock_list.call_args.args
    assert "critical" in args


def test_get_hook_found(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_hook", AsyncMock(return_value=_hook_row()))
    resp = c.get(f"/hooks/{_HOOK}?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert resp.json()["hook_id"] == str(_HOOK)


def test_get_hook_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_hook", AsyncMock(return_value=None))
    resp = c.get(f"/hooks/{_HOOK}?campaign_id={_CAMP}")
    assert resp.status_code == 404


def test_get_hook_requires_campaign_id(client):
    c, _ = client
    resp = c.get(f"/hooks/{_HOOK}")
    assert resp.status_code == 422


def test_patch_hook_resolve_emits_event(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "patch_hook", AsyncMock(return_value=_hook_row(status="resolved")))
    resp = c.patch(f"/hooks/{_HOOK}?campaign_id={_CAMP}", json={"status": "resolved"})
    assert resp.status_code == 200
    event_log_module.emit.assert_called_once()
    assert event_log_module.emit.call_args.kwargs["event_type"] == "story.hook_resolved"


def test_patch_hook_dismissed_emits_event(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "patch_hook", AsyncMock(return_value=_hook_row(status="dismissed")))
    c.patch(f"/hooks/{_HOOK}?campaign_id={_CAMP}", json={"status": "dismissed"})
    event_log_module.emit.assert_called_once()
    assert event_log_module.emit.call_args.kwargs["event_type"] == "story.hook_resolved"


def test_patch_hook_priority_no_event(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "patch_hook", AsyncMock(return_value=_hook_row(priority="high")))
    c.patch(f"/hooks/{_HOOK}?campaign_id={_CAMP}", json={"priority": "high"})
    event_log_module.emit.assert_not_called()


def test_patch_hook_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "patch_hook", AsyncMock(return_value=None))
    resp = c.patch(f"/hooks/{_HOOK}?campaign_id={_CAMP}", json={"priority": "high"})
    assert resp.status_code == 404


def test_delete_hook_204(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "delete_hook", AsyncMock(return_value=True))
    resp = c.delete(f"/hooks/{_HOOK}?campaign_id={_CAMP}")
    assert resp.status_code == 204


def test_delete_hook_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "delete_hook", AsyncMock(return_value=False))
    resp = c.delete(f"/hooks/{_HOOK}?campaign_id={_CAMP}")
    assert resp.status_code == 404


# ── Story Log ─────────────────────────────────────────────────────────────────

def test_post_story_log_201(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "insert_story_log_batch", AsyncMock(return_value=[_log_row()]))
    resp = c.post("/story-log", json={
        "entries": [{"campaign_id": str(_CAMP), "session_id": str(_SESSION),
                     "entry_type": "narration", "content": "The party entered the dungeon"}],
    })
    assert resp.status_code == 201
    assert len(resp.json()) == 1
    assert resp.json()[0]["entry_type"] == "narration"


def test_post_story_log_session_summary_emits_event(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "insert_story_log_batch",
                        AsyncMock(return_value=[_log_row(entry_type="session_summary")]))
    c.post("/story-log", json={
        "entries": [{"campaign_id": str(_CAMP), "session_id": str(_SESSION),
                     "entry_type": "session_summary", "content": "Session recap"}],
    })
    event_log_module.emit.assert_called_once()
    assert event_log_module.emit.call_args.kwargs["event_type"] == "story.session_summary_created"


def test_post_story_log_non_summary_no_event(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "insert_story_log_batch", AsyncMock(return_value=[_log_row()]))
    c.post("/story-log", json={
        "entries": [{"campaign_id": str(_CAMP), "entry_type": "narration", "content": "Text"}],
    })
    event_log_module.emit.assert_not_called()


def test_post_story_log_empty_batch_422(client):
    c, _ = client
    resp = c.post("/story-log", json={"entries": []})
    assert resp.status_code == 422


def test_post_story_log_batch_multiple(client, monkeypatch):
    c, _ = client
    rows = [_log_row(entry_id=uuid4()), _log_row(entry_id=uuid4())]
    monkeypatch.setattr(db_module, "insert_story_log_batch", AsyncMock(return_value=rows))
    resp = c.post("/story-log", json={
        "entries": [
            {"campaign_id": str(_CAMP), "entry_type": "narration", "content": "Part 1"},
            {"campaign_id": str(_CAMP), "entry_type": "combat_summary", "content": "Battle"},
        ],
    })
    assert resp.status_code == 201
    assert len(resp.json()) == 2


def test_get_story_log_by_campaign(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "list_story_log", AsyncMock(return_value=[_log_row()]))
    resp = c.get(f"/story-log?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_story_log_by_session(client, monkeypatch):
    c, _ = client
    mock_list = AsyncMock(return_value=[_log_row()])
    monkeypatch.setattr(db_module, "list_story_log", mock_list)
    c.get(f"/story-log?campaign_id={_CAMP}&session_id={_SESSION}")
    mock_list.assert_called_once()
    args = mock_list.call_args.args
    assert _SESSION in args


def test_get_story_log_requires_campaign_id(client):
    c, _ = client
    resp = c.get("/story-log")
    assert resp.status_code == 422


def test_get_story_log_entry_type_filter(client, monkeypatch):
    c, _ = client
    mock_list = AsyncMock(return_value=[])
    monkeypatch.setattr(db_module, "list_story_log", mock_list)
    c.get(f"/story-log?campaign_id={_CAMP}&entry_type=session_summary")
    mock_list.assert_called_once()
    args = mock_list.call_args.args
    assert "session_summary" in args


def test_get_story_log_with_limit(client, monkeypatch):
    c, _ = client
    mock_list = AsyncMock(return_value=[_log_row()])
    monkeypatch.setattr(db_module, "list_story_log", mock_list)
    c.get(f"/story-log?campaign_id={_CAMP}&limit=10")
    mock_list.assert_called_once()
    # limit=10 should be passed to the DB function
    assert 10 in mock_list.call_args.args or mock_list.call_args.kwargs.get("limit") == 10


def test_get_story_log_limit_out_of_range_422(client):
    c, _ = client
    resp = c.get(f"/story-log?campaign_id={_CAMP}&limit=0")
    assert resp.status_code == 422


# ── Objective management (add / delete on existing quests) ────────────────────

def test_add_objective_to_quest_201(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_quest", AsyncMock(return_value=_quest_row()))
    monkeypatch.setattr(db_module, "add_objective", AsyncMock(return_value=_obj_row()))
    resp = c.post(
        f"/quests/{_QUEST}/objectives?campaign_id={_CAMP}",
        json={"description": "Reach the dungeon", "sequence_order": 0},
    )
    assert resp.status_code == 201
    assert resp.json()["description"] == "Reach the dungeon"


def test_add_objective_quest_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_quest", AsyncMock(return_value=None))
    resp = c.post(
        f"/quests/{_QUEST}/objectives?campaign_id={_CAMP}",
        json={"description": "Find the key"},
    )
    assert resp.status_code == 404


def test_add_objective_requires_campaign_id(client):
    c, _ = client
    resp = c.post(f"/quests/{_QUEST}/objectives", json={"description": "Find the key"})
    assert resp.status_code == 422


def test_delete_objective_204(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "delete_objective", AsyncMock(return_value=True))
    resp = c.delete(f"/quests/{_QUEST}/objectives/{_OBJ}?campaign_id={_CAMP}")
    assert resp.status_code == 204


def test_delete_objective_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "delete_objective", AsyncMock(return_value=False))
    resp = c.delete(f"/quests/{_QUEST}/objectives/{_OBJ}?campaign_id={_CAMP}")
    assert resp.status_code == 404


def test_delete_objective_requires_campaign_id(client):
    c, _ = client
    resp = c.delete(f"/quests/{_QUEST}/objectives/{_OBJ}")
    assert resp.status_code == 422


# ── DM Context endpoint ───────────────────────────────────────────────────────

def test_get_context_returns_200(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "list_quests", AsyncMock(return_value=[_quest_row()]))
    monkeypatch.setattr(db_module, "list_hooks",  AsyncMock(return_value=[_hook_row()]))
    monkeypatch.setattr(db_module, "list_story_log", AsyncMock(return_value=[_log_row()]))
    resp = c.get(f"/context?campaign_id={_CAMP}")
    assert resp.status_code == 200
    body = resp.json()
    assert "active_quests" in body
    assert "open_hooks" in body
    assert "recent_log" in body
    assert len(body["active_quests"]) == 1
    assert len(body["open_hooks"]) == 1
    assert len(body["recent_log"]) == 1


def test_get_context_requires_campaign_id(client):
    c, _ = client
    resp = c.get("/context")
    assert resp.status_code == 422


def test_get_context_uses_bulk_objectives(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "list_quests", AsyncMock(return_value=[_quest_row(), _quest_row(quest_id=uuid4())]))
    monkeypatch.setattr(db_module, "list_hooks",     AsyncMock(return_value=[]))
    monkeypatch.setattr(db_module, "list_story_log", AsyncMock(return_value=[]))
    c.get(f"/context?campaign_id={_CAMP}")
    # bulk fetch called once for the list of quests, not per-quest
    db_module.get_quest_objectives_bulk.assert_called_once()
    db_module.get_quest_objectives.assert_not_called()


def test_get_context_log_limit_param(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "list_quests", AsyncMock(return_value=[]))
    monkeypatch.setattr(db_module, "list_hooks",  AsyncMock(return_value=[]))
    mock_log = AsyncMock(return_value=[])
    monkeypatch.setattr(db_module, "list_story_log", mock_log)
    c.get(f"/context?campaign_id={_CAMP}&log_limit=5")
    assert 5 in mock_log.call_args.args or mock_log.call_args.kwargs.get("limit") == 5


def test_get_context_with_session_id(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "list_quests", AsyncMock(return_value=[]))
    monkeypatch.setattr(db_module, "list_hooks",  AsyncMock(return_value=[]))
    mock_log = AsyncMock(return_value=[])
    monkeypatch.setattr(db_module, "list_story_log", mock_log)
    c.get(f"/context?campaign_id={_CAMP}&session_id={_SESSION}")
    # session_id should be forwarded to list_story_log
    args = mock_log.call_args.args
    kwargs = mock_log.call_args.kwargs
    assert _SESSION in args or kwargs.get("session_id") == _SESSION


def test_get_context_log_limit_out_of_range_422(client):
    c, _ = client
    resp = c.get(f"/context?campaign_id={_CAMP}&log_limit=0")
    assert resp.status_code == 422
