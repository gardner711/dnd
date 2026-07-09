"""Route tests for the Map Service.

All database operations are mocked; no real database or network I/O occurs.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.database as db_module
import app.event_log as event_log_module
from app.dependencies import get_db_conn
from app.main import app
from app.models import AggregateType, FogStateOut, LayerOut, LayerType, MapKind, MapOut, MapSelectionOut, TokenOut

_NOW = datetime.now(UTC)
_CAMP = uuid4()
_MAP = uuid4()
_LAYER = uuid4()
_TOKEN = uuid4()
_CHAR = uuid4()
_ENCOUNTER = uuid4()


def _map_out(**kw) -> MapOut:
    return MapOut(
        map_id=_MAP,
        campaign_id=_CAMP,
        name="Ruined Keep",
        kind=MapKind.DUNGEON,
        width=40,
        height=30,
        tile_size=5,
        description="",
        background_asset_key=None,
        active=True,
        created_at=_NOW,
        updated_at=_NOW,
        **kw,
    )


def _layer_out(**kw) -> LayerOut:
    return LayerOut(
        layer_id=_LAYER,
        map_id=_MAP,
        campaign_id=_CAMP,
        type=LayerType.WALL,
        name="Walls",
        z_index=10,
        visible=True,
        features={"type": "FeatureCollection", "features": []},
        created_at=_NOW,
        updated_at=_NOW,
        **kw,
    )


def _fog_out(**kw) -> FogStateOut:
    data = {
        "map_id": _MAP,
        "campaign_id": _CAMP,
        "character_id": _CHAR,
        "explored_cells": ["1,1", "1,2"],
        "updated_at": _NOW,
    }
    data.update(kw)
    return FogStateOut(**data)


def _token_out(**kw) -> TokenOut:
    return TokenOut(
        token_id=_TOKEN,
        map_id=_MAP,
        campaign_id=_CAMP,
        encounter_id=_ENCOUNTER,
        aggregate_id=_CHAR,
        aggregate_type=AggregateType.CHARACTER,
        x=5,
        y=8,
        visible=True,
        created_at=_NOW,
        updated_at=_NOW,
        **kw,
    )


def _selection_out(**kw) -> MapSelectionOut:
    data = {
        "campaign_id": _CAMP,
        "map_id": _MAP,
        "character_id": _CHAR,
        "scope": "character",
        "updated_at": _NOW,
    }
    data.update(kw)
    return MapSelectionOut(**data)


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
def client(mock_pool, mock_conn, monkeypatch):
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


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "map-service"


def test_create_map(client, monkeypatch):
    monkeypatch.setattr(db_module, "create_map", AsyncMock(return_value=_map_out()))

    response = client.post(
        "/maps",
        json={
            "campaign_id": str(_CAMP),
            "name": "Ruined Keep",
            "kind": "dungeon",
            "width": 40,
            "height": 30,
            "tile_size": 5,
        },
    )

    assert response.status_code == 201
    assert response.json()["map_id"] == str(_MAP)
    db_module.create_map.assert_awaited_once()


def test_list_maps(client, monkeypatch):
    monkeypatch.setattr(db_module, "list_maps", AsyncMock(return_value=[_map_out()]))

    response = client.get("/maps", params={"campaign_id": str(_CAMP)})

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["name"] == "Ruined Keep"


def test_patch_fog_merges_cells(client, monkeypatch):
    monkeypatch.setattr(db_module, "get_map", AsyncMock(return_value=_map_out()))
    monkeypatch.setattr(db_module, "get_fog", AsyncMock(return_value=_fog_out(explored_cells=["1,1"])))
    put_fog = AsyncMock(return_value=_fog_out(explored_cells=["1,1", "2,2"]))
    monkeypatch.setattr(db_module, "put_fog", put_fog)

    response = client.patch(
        f"/maps/{_MAP}/fog",
        json={
            "campaign_id": str(_CAMP),
            "character_id": str(_CHAR),
            "add_cells": ["2,2", "1,1"],
        },
    )

    assert response.status_code == 200
    assert sorted(response.json()["explored_cells"]) == ["1,1", "2,2"]
    assert put_fog.await_args.args[4] == ["1,1", "2,2"]


def test_upsert_token(client, monkeypatch):
    monkeypatch.setattr(db_module, "get_map", AsyncMock(return_value=_map_out()))
    monkeypatch.setattr(db_module, "upsert_token", AsyncMock(return_value=_token_out()))

    response = client.put(
        f"/maps/{_MAP}/tokens",
        json={
            "campaign_id": str(_CAMP),
            "encounter_id": str(_ENCOUNTER),
            "aggregate_id": str(_CHAR),
            "aggregate_type": "character",
            "x": 5,
            "y": 8,
            "visible": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["token_id"] == str(_TOKEN)


def test_snapshot_aggregates_map_layers_fog_and_tokens(client, monkeypatch):
    monkeypatch.setattr(db_module, "get_map", AsyncMock(return_value=_map_out()))
    monkeypatch.setattr(db_module, "list_layers", AsyncMock(return_value=[_layer_out()]))
    monkeypatch.setattr(db_module, "get_fog", AsyncMock(return_value=_fog_out()))
    monkeypatch.setattr(db_module, "list_tokens", AsyncMock(return_value=[_token_out()]))

    response = client.get(
        f"/maps/{_MAP}/snapshot",
        params={"campaign_id": str(_CAMP), "character_id": str(_CHAR), "encounter_id": str(_ENCOUNTER)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["map"]["map_id"] == str(_MAP)
    assert len(body["layers"]) == 1
    assert body["fog_of_war"]["character_id"] == str(_CHAR)
    assert len(body["tokens"]) == 1


def test_upsert_and_get_active_map_selection(client, monkeypatch):
    monkeypatch.setattr(db_module, "get_map", AsyncMock(return_value=_map_out()))
    monkeypatch.setattr(db_module, "upsert_active_map", AsyncMock(return_value=_selection_out()))
    monkeypatch.setattr(db_module, "get_active_map_selection", AsyncMock(return_value=_selection_out()))

    put_response = client.put(
        "/maps/active",
        json={"campaign_id": str(_CAMP), "map_id": str(_MAP), "character_id": str(_CHAR)},
    )
    assert put_response.status_code == 200
    assert put_response.json()["scope"] == "character"

    get_response = client.get("/maps/active", params={"campaign_id": str(_CAMP), "character_id": str(_CHAR)})
    assert get_response.status_code == 200
    assert get_response.json()["map_id"] == str(_MAP)


def test_active_snapshot_resolves_selected_map(client, monkeypatch):
    monkeypatch.setattr(db_module, "get_active_map_selection", AsyncMock(return_value=_selection_out()))
    monkeypatch.setattr(db_module, "get_map", AsyncMock(return_value=_map_out()))
    monkeypatch.setattr(db_module, "list_layers", AsyncMock(return_value=[_layer_out()]))
    monkeypatch.setattr(db_module, "get_fog", AsyncMock(return_value=_fog_out()))
    monkeypatch.setattr(db_module, "list_tokens", AsyncMock(return_value=[_token_out()]))

    response = client.get(
        "/maps/active/snapshot",
        params={"campaign_id": str(_CAMP), "character_id": str(_CHAR), "encounter_id": str(_ENCOUNTER)},
    )

    assert response.status_code == 200
    assert response.json()["map"]["map_id"] == str(_MAP)


def test_patch_layer_404_when_missing(client, monkeypatch):
    monkeypatch.setattr(db_module, "patch_layer", AsyncMock(return_value=None))

    response = client.patch(
        f"/maps/{_MAP}/layers/{_LAYER}",
        params={"campaign_id": str(_CAMP)},
        json={"name": "Doors"},
    )

    assert response.status_code == 404
    assert "Layer not found" in response.json()["detail"]