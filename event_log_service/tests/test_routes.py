"""Route tests — database and Redis are mocked; no real I/O required."""
from __future__ import annotations

import pytest
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

import app.database as db_module
import app.streams as streams_module
from app.dependencies import get_db_conn
from app.main import app


# ── Fixtures ────────────────────────────────────────────────────────────────

def _event_payload(**overrides) -> dict:
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


@pytest.fixture
def mock_conn():
    """Async mock of a single asyncpg connection."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=1)  # SELECT 1 for health check
    return conn


@pytest.fixture
def mock_redis_client():
    """Async mock Redis client for health check and stream publishing."""
    client = AsyncMock()
    client.ping = AsyncMock(return_value=True)
    return client


@pytest.fixture
def mock_pool(mock_conn):
    """Async mock of an asyncpg pool whose acquire() returns mock_conn."""
    pool = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool


@pytest.fixture
def client(mock_pool, mock_conn, mock_redis_client, monkeypatch):
    """TestClient with database and Redis fully mocked."""
    monkeypatch.setattr(db_module, "get_pool", AsyncMock(return_value=mock_pool))
    monkeypatch.setattr(db_module, "run_migrations", AsyncMock(return_value=None))
    monkeypatch.setattr(db_module, "close_pool", AsyncMock(return_value=None))
    monkeypatch.setattr(streams_module, "publish_event", AsyncMock(return_value=None))
    monkeypatch.setattr(streams_module, "close_redis", AsyncMock(return_value=None))
    monkeypatch.setattr(streams_module, "get_redis", AsyncMock(return_value=mock_redis_client))

    # Override the db connection dependency so routes receive mock_conn directly
    async def override_db():
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_db

    with TestClient(app) as c:
        yield c, mock_conn

    app.dependency_overrides.clear()


# ── Health ───────────────────────────────────────────────────────────────────

def test_health_ok(client):
    c, _ = client
    resp = c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["service"] == "event-log-service"
    assert resp.json()["checks"]["database"] is True
    assert resp.json()["checks"]["redis"] is True


def test_health_degraded_when_db_down(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_pool", AsyncMock(side_effect=Exception("db down")))
    resp = c.get("/health")
    assert resp.status_code == 503
    assert resp.json()["checks"]["database"] is False
    assert resp.json()["checks"]["redis"] is True


def test_health_degraded_when_redis_down(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(streams_module, "get_redis", AsyncMock(side_effect=Exception("redis down")))
    resp = c.get("/health")
    assert resp.status_code == 503
    assert resp.json()["checks"]["redis"] is False
    assert resp.json()["checks"]["database"] is True


# ── POST /events ─────────────────────────────────────────────────────────────

def test_write_event_returns_201(client):
    c, _ = client
    resp = c.post("/events", json=_event_payload())
    assert resp.status_code == 201


def test_write_event_returns_event_id(client):
    c, _ = client
    payload = _event_payload()
    resp = c.post("/events", json=payload)
    assert resp.json()["event_id"] == payload["event_id"]


def test_write_event_calls_db_execute(client):
    c, mock_conn = client
    c.post("/events", json=_event_payload())
    mock_conn.execute.assert_called_once()


def test_write_event_invalid_body_returns_422(client):
    c, _ = client
    resp = c.post("/events", json={"not": "a valid event"})
    assert resp.status_code == 422


def test_write_event_invalid_uuid_returns_422(client):
    c, _ = client
    resp = c.post("/events", json=_event_payload(event_id="bad-uuid"))
    assert resp.status_code == 422


def test_write_event_redis_failure_still_returns_201(client, monkeypatch):
    """A Redis publish failure must not fail the HTTP response."""
    c, _ = client
    monkeypatch.setattr(
        streams_module, "publish_event", AsyncMock(side_effect=Exception("redis down"))
    )
    resp = c.post("/events", json=_event_payload())
    assert resp.status_code == 201


# ── GET /events ───────────────────────────────────────────────────────────────

def test_read_events_requires_campaign_id(client):
    c, _ = client
    resp = c.get("/events")
    assert resp.status_code == 422  # campaign_id is a required query param


def test_read_events_by_session_returns_200(client):
    c, _ = client
    resp = c.get(f"/events?campaign_id={uuid4()}&session_id={uuid4()}")
    assert resp.status_code == 200
    assert resp.json() == []


def test_read_events_by_aggregate_returns_200(client):
    c, _ = client
    resp = c.get(
        f"/events?campaign_id={uuid4()}&aggregate_id={uuid4()}&aggregate_type=character"
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_read_events_no_filter_returns_400(client):
    c, _ = client
    resp = c.get(f"/events?campaign_id={uuid4()}")
    assert resp.status_code == 400


def test_read_events_aggregate_id_without_type_returns_400(client):
    """aggregate_id without aggregate_type should be rejected."""
    c, _ = client
    resp = c.get(f"/events?campaign_id={uuid4()}&aggregate_id={uuid4()}")
    assert resp.status_code == 400


def test_read_events_limit_respected(client):
    c, mock_conn = client
    c.get(f"/events?campaign_id={uuid4()}&session_id={uuid4()}&limit=10")
    mock_conn.fetch.assert_called_once()
    args = mock_conn.fetch.call_args[0]
    assert args[-1] == 10  # limit is always the last positional arg


def test_read_events_event_type_filter_passed_to_db(client):
    c, mock_conn = client
    c.get(f"/events?campaign_id={uuid4()}&session_id={uuid4()}&event_type=dice.rolled")
    args = mock_conn.fetch.call_args[0]
    assert "dice.rolled" in args


def test_read_events_no_event_type_passes_none(client):
    """When event_type is omitted the DB receives None, which the SQL treats as no filter."""
    c, mock_conn = client
    c.get(f"/events?campaign_id={uuid4()}&session_id={uuid4()}")
    args = mock_conn.fetch.call_args[0]
    assert None in args


def test_read_events_event_type_filter_on_aggregate(client):
    c, mock_conn = client
    c.get(
        f"/events?campaign_id={uuid4()}&aggregate_id={uuid4()}"
        f"&aggregate_type=character&event_type=attack.resolved"
    )
    args = mock_conn.fetch.call_args[0]
    assert "attack.resolved" in args
