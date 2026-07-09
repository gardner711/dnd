"""PostgreSQL data access for the Map Service."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg

from app.config import settings
from app.models import (
    AggregateType,
    FogStateOut,
    LayerOut,
    LayerType,
    MapKind,
    MapOut,
    MapSelectionOut,
    TokenOut,
)

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS maps (
    map_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id           UUID NOT NULL,
    name                  TEXT NOT NULL,
    kind                  TEXT NOT NULL,
    width                 INT NOT NULL,
    height                INT NOT NULL,
    tile_size             INT NOT NULL DEFAULT 5,
    description           TEXT NOT NULL DEFAULT '',
    background_asset_key  TEXT,
    active                BOOL NOT NULL DEFAULT TRUE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_maps_campaign ON maps (campaign_id, active, updated_at DESC);

CREATE TABLE IF NOT EXISTS map_layers (
    layer_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    map_id        UUID NOT NULL REFERENCES maps(map_id) ON DELETE CASCADE,
    campaign_id   UUID NOT NULL,
    type          TEXT NOT NULL,
    name          TEXT NOT NULL,
    z_index       INT NOT NULL DEFAULT 0,
    visible       BOOL NOT NULL DEFAULT TRUE,
    features      JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_layers_map ON map_layers (campaign_id, map_id, z_index, created_at);

CREATE TABLE IF NOT EXISTS fog_of_war (
    map_id          UUID NOT NULL REFERENCES maps(map_id) ON DELETE CASCADE,
    campaign_id     UUID NOT NULL,
    character_id    UUID NOT NULL,
    explored_cells  JSONB NOT NULL DEFAULT '[]',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (map_id, campaign_id, character_id)
);

CREATE TABLE IF NOT EXISTS map_tokens (
    token_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    map_id           UUID NOT NULL REFERENCES maps(map_id) ON DELETE CASCADE,
    campaign_id      UUID NOT NULL,
    encounter_id     UUID,
    aggregate_id     UUID NOT NULL,
    aggregate_type   TEXT NOT NULL,
    x                INT NOT NULL,
    y                INT NOT NULL,
    visible          BOOL NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (map_id, campaign_id, aggregate_id)
);
CREATE INDEX IF NOT EXISTS idx_tokens_map ON map_tokens (campaign_id, map_id, encounter_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS campaign_active_map (
    campaign_id   UUID PRIMARY KEY,
    map_id        UUID NOT NULL REFERENCES maps(map_id) ON DELETE CASCADE,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS character_active_map (
    campaign_id   UUID NOT NULL,
    character_id  UUID NOT NULL,
    map_id        UUID NOT NULL REFERENCES maps(map_id) ON DELETE CASCADE,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (campaign_id, character_id)
);
"""


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.database_url)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def run_migrations(conn: asyncpg.Connection) -> None:
    await conn.execute(SCHEMA_SQL)
    logger.info("Map Service migrations applied")


_INSERT_MAP = """
INSERT INTO maps (campaign_id, name, kind, width, height, tile_size, description, background_asset_key)
VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8)
RETURNING *
"""
_LIST_MAPS = "SELECT * FROM maps WHERE campaign_id=$1::uuid AND ($2::bool IS FALSE OR active=TRUE) ORDER BY updated_at DESC"
_GET_MAP = "SELECT * FROM maps WHERE map_id=$1::uuid AND campaign_id=$2::uuid"
_PATCH_MAP = """
UPDATE maps
SET name                 = COALESCE($3, name),
    kind                 = COALESCE($4, kind),
    width                = COALESCE($5, width),
    height               = COALESCE($6, height),
    tile_size            = COALESCE($7, tile_size),
    description          = COALESCE($8, description),
    background_asset_key = COALESCE($9, background_asset_key),
    active               = COALESCE($10, active),
    updated_at           = NOW()
WHERE map_id=$1::uuid AND campaign_id=$2::uuid
RETURNING *
"""
_DELETE_MAP = "DELETE FROM maps WHERE map_id=$1::uuid AND campaign_id=$2::uuid RETURNING map_id"

_INSERT_LAYER = """
INSERT INTO map_layers (map_id, campaign_id, type, name, z_index, visible, features)
VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7::jsonb)
RETURNING *
"""
_LIST_LAYERS = "SELECT * FROM map_layers WHERE map_id=$1::uuid AND campaign_id=$2::uuid ORDER BY z_index, created_at"
_PATCH_LAYER = """
UPDATE map_layers
SET type       = COALESCE($4, type),
    name       = COALESCE($5, name),
    z_index    = COALESCE($6, z_index),
    visible    = COALESCE($7, visible),
    features   = COALESCE($8::jsonb, features),
    updated_at = NOW()
WHERE layer_id=$1::uuid AND map_id=$2::uuid AND campaign_id=$3::uuid
RETURNING *
"""
_DELETE_LAYER = "DELETE FROM map_layers WHERE layer_id=$1::uuid AND map_id=$2::uuid AND campaign_id=$3::uuid RETURNING layer_id"

_UPSERT_FOG = """
INSERT INTO fog_of_war (map_id, campaign_id, character_id, explored_cells, updated_at)
VALUES ($1::uuid, $2::uuid, $3::uuid, $4::jsonb, NOW())
ON CONFLICT (map_id, campaign_id, character_id)
DO UPDATE SET explored_cells=EXCLUDED.explored_cells, updated_at=NOW()
RETURNING *
"""
_GET_FOG = "SELECT * FROM fog_of_war WHERE map_id=$1::uuid AND campaign_id=$2::uuid AND character_id=$3::uuid"

_UPSERT_TOKEN = """
INSERT INTO map_tokens (map_id, campaign_id, encounter_id, aggregate_id, aggregate_type, x, y, visible, updated_at)
VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8, NOW())
ON CONFLICT (map_id, campaign_id, aggregate_id)
DO UPDATE SET encounter_id=EXCLUDED.encounter_id, aggregate_type=EXCLUDED.aggregate_type,
              x=EXCLUDED.x, y=EXCLUDED.y, visible=EXCLUDED.visible, updated_at=NOW()
RETURNING *
"""
_LIST_TOKENS = "SELECT * FROM map_tokens WHERE map_id=$1::uuid AND campaign_id=$2::uuid AND ($3::uuid IS NULL OR encounter_id=$3::uuid) ORDER BY updated_at DESC"
_PATCH_TOKEN = """
UPDATE map_tokens
SET x          = COALESCE($4, x),
    y          = COALESCE($5, y),
    visible    = COALESCE($6, visible),
    updated_at = NOW()
WHERE token_id=$1::uuid AND map_id=$2::uuid AND campaign_id=$3::uuid
RETURNING *
"""
_DELETE_TOKEN = "DELETE FROM map_tokens WHERE token_id=$1::uuid AND map_id=$2::uuid AND campaign_id=$3::uuid RETURNING token_id"
_UPSERT_CAMPAIGN_ACTIVE_MAP = """
INSERT INTO campaign_active_map (campaign_id, map_id, updated_at)
VALUES ($1::uuid, $2::uuid, NOW())
ON CONFLICT (campaign_id) DO UPDATE SET map_id=EXCLUDED.map_id, updated_at=NOW()
RETURNING campaign_id, map_id, updated_at
"""
_UPSERT_CHARACTER_ACTIVE_MAP = """
INSERT INTO character_active_map (campaign_id, character_id, map_id, updated_at)
VALUES ($1::uuid, $2::uuid, $3::uuid, NOW())
ON CONFLICT (campaign_id, character_id) DO UPDATE SET map_id=EXCLUDED.map_id, updated_at=NOW()
RETURNING campaign_id, character_id, map_id, updated_at
"""
_GET_CHARACTER_ACTIVE_MAP = "SELECT campaign_id, character_id, map_id, updated_at FROM character_active_map WHERE campaign_id=$1::uuid AND character_id=$2::uuid"
_GET_CAMPAIGN_ACTIVE_MAP = "SELECT campaign_id, NULL::uuid AS character_id, map_id, updated_at FROM campaign_active_map WHERE campaign_id=$1::uuid"


async def create_map(
    conn: asyncpg.Connection,
    campaign_id: UUID,
    name: str,
    kind: MapKind,
    width: int,
    height: int,
    tile_size: int,
    description: str,
    background_asset_key: str | None,
) -> MapOut:
    row = await conn.fetchrow(
        _INSERT_MAP,
        str(campaign_id),
        name,
        kind.value,
        width,
        height,
        tile_size,
        description,
        background_asset_key,
    )
    return _row_to_map(row)


async def list_maps(conn: asyncpg.Connection, campaign_id: UUID, active_only: bool) -> list[MapOut]:
    rows = await conn.fetch(_LIST_MAPS, str(campaign_id), active_only)
    return [_row_to_map(r) for r in rows]


async def get_map(conn: asyncpg.Connection, map_id: UUID, campaign_id: UUID) -> MapOut | None:
    row = await conn.fetchrow(_GET_MAP, str(map_id), str(campaign_id))
    return _row_to_map(row) if row else None


async def patch_map(
    conn: asyncpg.Connection,
    map_id: UUID,
    campaign_id: UUID,
    name: str | None,
    kind: MapKind | None,
    width: int | None,
    height: int | None,
    tile_size: int | None,
    description: str | None,
    background_asset_key: str | None,
    active: bool | None,
) -> MapOut | None:
    row = await conn.fetchrow(
        _PATCH_MAP,
        str(map_id),
        str(campaign_id),
        name,
        kind.value if kind else None,
        width,
        height,
        tile_size,
        description,
        background_asset_key,
        active,
    )
    return _row_to_map(row) if row else None


async def delete_map(conn: asyncpg.Connection, map_id: UUID, campaign_id: UUID) -> bool:
    row = await conn.fetchrow(_DELETE_MAP, str(map_id), str(campaign_id))
    return row is not None


async def create_layer(
    conn: asyncpg.Connection,
    map_id: UUID,
    campaign_id: UUID,
    type_: LayerType,
    name: str,
    z_index: int,
    visible: bool,
    features: dict[str, Any],
) -> LayerOut:
    row = await conn.fetchrow(
        _INSERT_LAYER,
        str(map_id),
        str(campaign_id),
        type_.value,
        name,
        z_index,
        visible,
        json.dumps(features),
    )
    return _row_to_layer(row)


async def list_layers(conn: asyncpg.Connection, map_id: UUID, campaign_id: UUID) -> list[LayerOut]:
    rows = await conn.fetch(_LIST_LAYERS, str(map_id), str(campaign_id))
    return [_row_to_layer(r) for r in rows]


async def patch_layer(
    conn: asyncpg.Connection,
    layer_id: UUID,
    map_id: UUID,
    campaign_id: UUID,
    type_: LayerType | None,
    name: str | None,
    z_index: int | None,
    visible: bool | None,
    features: dict[str, Any] | None,
) -> LayerOut | None:
    row = await conn.fetchrow(
        _PATCH_LAYER,
        str(layer_id),
        str(map_id),
        str(campaign_id),
        type_.value if type_ else None,
        name,
        z_index,
        visible,
        json.dumps(features) if features is not None else None,
    )
    return _row_to_layer(row) if row else None


async def delete_layer(conn: asyncpg.Connection, layer_id: UUID, map_id: UUID, campaign_id: UUID) -> bool:
    row = await conn.fetchrow(_DELETE_LAYER, str(layer_id), str(map_id), str(campaign_id))
    return row is not None


async def put_fog(
    conn: asyncpg.Connection,
    map_id: UUID,
    campaign_id: UUID,
    character_id: UUID,
    explored_cells: list[str],
) -> FogStateOut:
    row = await conn.fetchrow(
        _UPSERT_FOG,
        str(map_id),
        str(campaign_id),
        str(character_id),
        json.dumps(sorted(set(explored_cells))),
    )
    return _row_to_fog(row)


async def get_fog(conn: asyncpg.Connection, map_id: UUID, campaign_id: UUID, character_id: UUID) -> FogStateOut | None:
    row = await conn.fetchrow(_GET_FOG, str(map_id), str(campaign_id), str(character_id))
    return _row_to_fog(row) if row else None


async def upsert_token(
    conn: asyncpg.Connection,
    map_id: UUID,
    campaign_id: UUID,
    encounter_id: UUID | None,
    aggregate_id: UUID,
    aggregate_type: AggregateType,
    x: int,
    y: int,
    visible: bool,
) -> TokenOut:
    row = await conn.fetchrow(
        _UPSERT_TOKEN,
        str(map_id),
        str(campaign_id),
        str(encounter_id) if encounter_id else None,
        str(aggregate_id),
        aggregate_type.value,
        x,
        y,
        visible,
    )
    return _row_to_token(row)


async def list_tokens(
    conn: asyncpg.Connection,
    map_id: UUID,
    campaign_id: UUID,
    encounter_id: UUID | None,
) -> list[TokenOut]:
    rows = await conn.fetch(_LIST_TOKENS, str(map_id), str(campaign_id), str(encounter_id) if encounter_id else None)
    return [_row_to_token(r) for r in rows]


async def patch_token(
    conn: asyncpg.Connection,
    token_id: UUID,
    map_id: UUID,
    campaign_id: UUID,
    x: int | None,
    y: int | None,
    visible: bool | None,
) -> TokenOut | None:
    row = await conn.fetchrow(_PATCH_TOKEN, str(token_id), str(map_id), str(campaign_id), x, y, visible)
    return _row_to_token(row) if row else None


async def delete_token(conn: asyncpg.Connection, token_id: UUID, map_id: UUID, campaign_id: UUID) -> bool:
    row = await conn.fetchrow(_DELETE_TOKEN, str(token_id), str(map_id), str(campaign_id))
    return row is not None


async def upsert_active_map(
    conn: asyncpg.Connection,
    campaign_id: UUID,
    map_id: UUID,
    character_id: UUID | None,
) -> MapSelectionOut:
    if character_id is None:
        row = await conn.fetchrow(_UPSERT_CAMPAIGN_ACTIVE_MAP, str(campaign_id), str(map_id))
        return _row_to_selection(row, scope="campaign")
    row = await conn.fetchrow(_UPSERT_CHARACTER_ACTIVE_MAP, str(campaign_id), str(character_id), str(map_id))
    return _row_to_selection(row, scope="character")


async def get_active_map_selection(
    conn: asyncpg.Connection,
    campaign_id: UUID,
    character_id: UUID | None,
) -> MapSelectionOut | None:
    if character_id is not None:
        row = await conn.fetchrow(_GET_CHARACTER_ACTIVE_MAP, str(campaign_id), str(character_id))
        if row is not None:
            return _row_to_selection(row, scope="character")
    row = await conn.fetchrow(_GET_CAMPAIGN_ACTIVE_MAP, str(campaign_id))
    return _row_to_selection(row, scope="campaign") if row else None


def _row_to_map(row) -> MapOut:
    d = dict(row)
    return MapOut(
        map_id=str(d["map_id"]),
        campaign_id=str(d["campaign_id"]),
        name=d["name"],
        kind=MapKind(d["kind"]),
        width=d["width"],
        height=d["height"],
        tile_size=d["tile_size"],
        description=d["description"],
        background_asset_key=d.get("background_asset_key"),
        active=d["active"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


def _row_to_layer(row) -> LayerOut:
    d = dict(row)
    return LayerOut(
        layer_id=str(d["layer_id"]),
        map_id=str(d["map_id"]),
        campaign_id=str(d["campaign_id"]),
        type=LayerType(d["type"]),
        name=d["name"],
        z_index=d["z_index"],
        visible=d["visible"],
        features=d["features"] or {},
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


def _row_to_fog(row) -> FogStateOut:
    d = dict(row)
    return FogStateOut(
        map_id=str(d["map_id"]),
        campaign_id=str(d["campaign_id"]),
        character_id=str(d["character_id"]),
        explored_cells=list(d["explored_cells"] or []),
        updated_at=d["updated_at"],
    )


def _row_to_token(row) -> TokenOut:
    d = dict(row)
    return TokenOut(
        token_id=str(d["token_id"]),
        map_id=str(d["map_id"]),
        campaign_id=str(d["campaign_id"]),
        encounter_id=str(d["encounter_id"]) if d.get("encounter_id") else None,
        aggregate_id=str(d["aggregate_id"]),
        aggregate_type=AggregateType(d["aggregate_type"]),
        x=d["x"],
        y=d["y"],
        visible=d["visible"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


def _row_to_selection(row, scope: str) -> MapSelectionOut:
    d = dict(row)
    return MapSelectionOut(
        campaign_id=str(d["campaign_id"]),
        map_id=str(d["map_id"]),
        character_id=str(d["character_id"]) if d.get("character_id") else None,
        scope=scope,
        updated_at=d["updated_at"],
    )