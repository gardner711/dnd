import pytest
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import numpy as np
from fastapi.testclient import TestClient

import app.database as db_module
import app.embeddings as embeddings_module
import app.stream_consumer as consumer_module
from app.dependencies import get_db_conn
from app.main import app
from app.models import MemoryOut, SubjectType

_CAMPAIGN = uuid4()
_SUBJECT = uuid4()
_MEMORY_ID = uuid4()
_NOW = datetime.now(UTC)
_FAKE_EMBED = np.zeros(384, dtype=np.float32)


def _memory_out(**kw) -> MemoryOut:
    return MemoryOut(**{
        "memory_id": _MEMORY_ID, "campaign_id": _CAMPAIGN,
        "subject_type": SubjectType.NPC, "subject_id": _SUBJECT,
        "content": "Innkeeper became hostile", "importance": 3,
        "source_event_ids": [], "created_at": _NOW, "last_accessed_at": _NOW,
        **kw,
    })


@pytest.fixture
def mock_conn():
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
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
def client(mock_pool, mock_conn, monkeypatch):
    monkeypatch.setattr(db_module, "get_pool", AsyncMock(return_value=mock_pool))
    monkeypatch.setattr(db_module, "run_migrations", AsyncMock(return_value=None))
    monkeypatch.setattr(db_module, "close_pool", AsyncMock(return_value=None))
    monkeypatch.setattr(embeddings_module, "embed", MagicMock(return_value=_FAKE_EMBED))
    monkeypatch.setattr(consumer_module, "run", AsyncMock(return_value=None))

    async def override_db():
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_db
    with TestClient(app) as c:
        yield c, mock_conn
    app.dependency_overrides.clear()


# ── Health ─────────────────────────────────────────────────────────────────

def test_health_ok(client):
    c, _ = client
    resp = c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["checks"]["database"] is True
    assert resp.json()["checks"]["embedding_model"] is True


def test_health_degraded_db_down(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_pool", AsyncMock(side_effect=Exception("db down")))
    resp = c.get("/health")
    assert resp.status_code == 503
    assert resp.json()["checks"]["database"] is False


def test_health_degraded_embed_down(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(embeddings_module, "embed", MagicMock(side_effect=Exception("model error")))
    resp = c.get("/health")
    assert resp.status_code == 503
    assert resp.json()["checks"]["embedding_model"] is False


# ── POST /memories ──────────────────────────────────────────────────────────

def _mem_payload(**kw):
    return {
        "campaign_id": str(_CAMPAIGN), "subject_type": "npc",
        "subject_id": str(_SUBJECT),
        "content": "Innkeeper Marta became hostile toward the party.",
        **kw,
    }


def test_write_memory_returns_201(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "insert_memory", AsyncMock(return_value=_memory_out()))
    resp = c.post("/memories", json=_mem_payload())
    assert resp.status_code == 201
    assert "memory_id" in resp.json()


def test_write_memory_calls_embed(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "insert_memory", AsyncMock(return_value=_memory_out()))
    c.post("/memories", json=_mem_payload())
    embeddings_module.embed.assert_called_once_with(
        "Innkeeper Marta became hostile toward the party."
    )


def test_write_memory_invalid_body_422(client):
    c, _ = client
    resp = c.post("/memories", json={"bad": "payload"})
    assert resp.status_code == 422


def test_write_memory_empty_content_422(client):
    c, _ = client
    resp = c.post("/memories", json=_mem_payload(content=""))
    assert resp.status_code == 422


# ── GET /memories/recall ────────────────────────────────────────────────────

def test_recall_returns_200(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "recall_memories", AsyncMock(return_value=[]))
    resp = c.get(f"/memories/recall?campaign_id={_CAMPAIGN}&query=thieves+guild")
    assert resp.status_code == 200
    data = resp.json()
    assert data["memories"] == []
    assert data["query"] == "thieves guild"
    assert data["top_k"] == 5


def test_recall_returns_memories(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "recall_memories", AsyncMock(return_value=[_memory_out()]))
    resp = c.get(f"/memories/recall?campaign_id={_CAMPAIGN}&query=innkeeper")
    assert len(resp.json()["memories"]) == 1


def test_recall_calls_embed(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "recall_memories", AsyncMock(return_value=[]))
    c.get(f"/memories/recall?campaign_id={_CAMPAIGN}&query=dragon+attack")
    embeddings_module.embed.assert_called_with("dragon attack")


def test_recall_requires_campaign_id(client):
    c, _ = client
    resp = c.get("/memories/recall?query=test")
    assert resp.status_code == 422


def test_recall_requires_query(client):
    c, _ = client
    resp = c.get(f"/memories/recall?campaign_id={_CAMPAIGN}")
    assert resp.status_code == 422


def test_recall_top_k_capped_at_20(client):
    c, _ = client
    resp = c.get(f"/memories/recall?campaign_id={_CAMPAIGN}&query=test&top_k=21")
    assert resp.status_code == 422


# ── GET /memories ───────────────────────────────────────────────────────────

def test_list_returns_200(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "list_memories", AsyncMock(return_value=[]))
    resp = c.get(f"/memories?campaign_id={_CAMPAIGN}")
    assert resp.status_code == 200


def test_list_requires_campaign_id(client):
    c, _ = client
    resp = c.get("/memories")
    assert resp.status_code == 422


# ── DELETE /memories/{id} ───────────────────────────────────────────────────

def test_delete_existing_returns_204(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "delete_memory", AsyncMock(return_value=True))
    resp = c.delete(f"/memories/{_MEMORY_ID}?campaign_id={_CAMPAIGN}")
    assert resp.status_code == 204


def test_delete_missing_returns_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "delete_memory", AsyncMock(return_value=False))
    resp = c.delete(f"/memories/{_MEMORY_ID}?campaign_id={_CAMPAIGN}")
    assert resp.status_code == 404


def test_delete_requires_campaign_id(client):
    c, _ = client
    resp = c.delete(f"/memories/{_MEMORY_ID}")
    assert resp.status_code == 422


# ── GET /memories/{id} ─────────────────────────────────────────────────────

def test_get_memory_returns_200(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_memory", AsyncMock(return_value=_memory_out()))
    resp = c.get(f"/memories/{_MEMORY_ID}?campaign_id={_CAMPAIGN}")
    assert resp.status_code == 200
    assert resp.json()["content"] == "Innkeeper became hostile"


def test_get_memory_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_memory", AsyncMock(return_value=None))
    resp = c.get(f"/memories/{_MEMORY_ID}?campaign_id={_CAMPAIGN}")
    assert resp.status_code == 404


def test_get_memory_requires_campaign_id(client):
    c, _ = client
    resp = c.get(f"/memories/{_MEMORY_ID}")
    assert resp.status_code == 422


# ── PATCH /memories/{id} ───────────────────────────────────────────────────

def test_patch_memory_importance_returns_200(client, monkeypatch):
    c, _ = client
    updated = _memory_out(importance=5)
    monkeypatch.setattr(db_module, "update_memory", AsyncMock(return_value=updated))
    resp = c.patch(
        f"/memories/{_MEMORY_ID}?campaign_id={_CAMPAIGN}",
        json={"importance": 5},
    )
    assert resp.status_code == 200
    assert resp.json()["importance"] == 5


def test_patch_memory_content_calls_embed(client, monkeypatch):
    c, _ = client
    updated = _memory_out(content="New content about the dungeon.")
    monkeypatch.setattr(db_module, "update_memory", AsyncMock(return_value=updated))
    c.patch(
        f"/memories/{_MEMORY_ID}?campaign_id={_CAMPAIGN}",
        json={"content": "New content about the dungeon."},
    )
    embeddings_module.embed.assert_called_with("New content about the dungeon.")


def test_patch_memory_importance_only_no_embed(client, monkeypatch):
    c, _ = client
    updated = _memory_out(importance=5)
    monkeypatch.setattr(db_module, "update_memory", AsyncMock(return_value=updated))
    embeddings_module.embed.reset_mock()
    c.patch(
        f"/memories/{_MEMORY_ID}?campaign_id={_CAMPAIGN}",
        json={"importance": 5},
    )
    embeddings_module.embed.assert_not_called()


def test_patch_memory_not_found_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "update_memory", AsyncMock(return_value=None))
    resp = c.patch(
        f"/memories/{_MEMORY_ID}?campaign_id={_CAMPAIGN}",
        json={"importance": 5},
    )
    assert resp.status_code == 404
