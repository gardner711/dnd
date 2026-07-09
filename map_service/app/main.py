"""FastAPI application — Map Service."""
from __future__ import annotations

from datetime import UTC, datetime
import logging
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from app import database, event_log
from app.dependencies import get_db_conn
from app.models import (
    FogStateOut,
    FogStatePatch,
    FogStatePut,
    LayerCreate,
    LayerOut,
    LayerUpdate,
    MapCreate,
    MapOut,
    MapSelectionOut,
    MapSelectionUpsert,
    MapSnapshot,
    MapUpdate,
    TokenOut,
    TokenUpdate,
    TokenUpsert,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await database.run_migrations(conn)
    logger.info("Map Service ready")
    yield
    await database.close_pool()
    logger.info("Map Service stopped")


app = FastAPI(
    title="Map Service",
    description="Map definitions, fog-of-war, and encounter token placement",
    version="0.1.0",
    lifespan=lifespan,
)

DbConn = Annotated[asyncpg.Connection, Depends(get_db_conn)]


def _meta(meta) -> tuple[str | None, str | None]:
    if meta is None:
        return None, None
    return meta.session_id, meta.user_id


def _empty_fog(map_id: UUID, campaign_id: UUID, character_id: UUID) -> FogStateOut:
    return FogStateOut(
        map_id=map_id,
        campaign_id=campaign_id,
        character_id=character_id,
        explored_cells=[],
        updated_at=datetime.now(UTC),
    )


@app.get("/health")
async def health() -> JSONResponse:
    db_ok = False
    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception as exc:
        logger.warning("Health: database unavailable: %s", exc)
    return JSONResponse(
        content={"status": "ok" if db_ok else "degraded", "service": "map-service", "checks": {"database": db_ok}},
        status_code=200 if db_ok else 503,
    )


@app.post("/maps", response_model=MapOut, status_code=201)
async def create_map(body: MapCreate, conn: DbConn) -> MapOut:
    result = await database.create_map(
        conn,
        body.campaign_id,
        body.name,
        body.kind,
        body.width,
        body.height,
        body.tile_size,
        body.description,
        body.background_asset_key,
    )
    sid, uid = _meta(body.meta)
    await event_log.emit("map.created", str(result.map_id), "world", str(body.campaign_id), sid, uid, {"map_id": str(result.map_id), "name": result.name, "kind": result.kind.value})
    return result


@app.get("/maps", response_model=list[MapOut])
async def list_maps(conn: DbConn, campaign_id: UUID = Query(...), active_only: bool = Query(default=True)) -> list[MapOut]:
    return await database.list_maps(conn, campaign_id, active_only)


@app.put("/maps/active", response_model=MapSelectionOut)
async def upsert_active_map(body: MapSelectionUpsert, conn: DbConn) -> MapSelectionOut:
    existing = await database.get_map(conn, body.map_id, body.campaign_id)
    if existing is None:
        raise HTTPException(404, "Map not found in this campaign")
    result = await database.upsert_active_map(conn, body.campaign_id, body.map_id, body.character_id)
    sid, uid = _meta(body.meta)
    await event_log.emit(
        "map.active_selected",
        str(body.map_id),
        "world",
        str(body.campaign_id),
        sid,
        uid,
        {"map_id": str(body.map_id), "character_id": str(body.character_id) if body.character_id else None, "scope": result.scope},
    )
    return result


@app.get("/maps/active", response_model=MapSelectionOut)
async def get_active_map(conn: DbConn, campaign_id: UUID = Query(...), character_id: UUID | None = Query(default=None)) -> MapSelectionOut:
    result = await database.get_active_map_selection(conn, campaign_id, character_id)
    if result is None:
        raise HTTPException(404, "No active map selected for this campaign scope")
    return result


@app.get("/maps/active/snapshot", response_model=MapSnapshot)
async def get_active_snapshot(
    conn: DbConn,
    campaign_id: UUID = Query(...),
    character_id: UUID = Query(...),
    encounter_id: UUID | None = Query(default=None),
) -> MapSnapshot:
    selection = await database.get_active_map_selection(conn, campaign_id, character_id)
    if selection is None:
        raise HTTPException(404, "No active map selected for this campaign scope")
    map_id = selection.map_id
    map_out = await database.get_map(conn, map_id, campaign_id)
    if map_out is None:
        raise HTTPException(404, "Active map not found in this campaign")
    fog = await database.get_fog(conn, map_id, campaign_id, character_id)
    if fog is None:
        fog = _empty_fog(map_id, campaign_id, character_id)
    return MapSnapshot(
        map=map_out,
        layers=await database.list_layers(conn, map_id, campaign_id),
        fog_of_war=fog,
        tokens=await database.list_tokens(conn, map_id, campaign_id, encounter_id),
    )


@app.get("/maps/{map_id}", response_model=MapOut)
async def get_map(map_id: UUID, conn: DbConn, campaign_id: UUID = Query(...)) -> MapOut:
    result = await database.get_map(conn, map_id, campaign_id)
    if result is None:
        raise HTTPException(404, "Map not found in this campaign")
    return result


@app.patch("/maps/{map_id}", response_model=MapOut)
async def patch_map(map_id: UUID, body: MapUpdate, conn: DbConn, campaign_id: UUID = Query(...)) -> MapOut:
    result = await database.patch_map(
        conn,
        map_id,
        campaign_id,
        body.name,
        body.kind,
        body.width,
        body.height,
        body.tile_size,
        body.description,
        body.background_asset_key,
        body.active,
    )
    if result is None:
        raise HTTPException(404, "Map not found in this campaign")
    sid, uid = _meta(body.meta)
    await event_log.emit("map.updated", str(map_id), "world", str(campaign_id), sid, uid, {"map_id": str(map_id)})
    return result


@app.delete("/maps/{map_id}", status_code=204)
async def delete_map(map_id: UUID, conn: DbConn, campaign_id: UUID = Query(...)) -> None:
    deleted = await database.delete_map(conn, map_id, campaign_id)
    if not deleted:
        raise HTTPException(404, "Map not found in this campaign")


@app.post("/maps/{map_id}/layers", response_model=LayerOut, status_code=201)
async def create_layer(map_id: UUID, body: LayerCreate, conn: DbConn, campaign_id: UUID = Query(...)) -> LayerOut:
    existing = await database.get_map(conn, map_id, campaign_id)
    if existing is None:
        raise HTTPException(404, "Map not found in this campaign")
    result = await database.create_layer(conn, map_id, campaign_id, body.type, body.name, body.z_index, body.visible, body.features)
    sid, uid = _meta(body.meta)
    await event_log.emit("map.layer_created", str(result.layer_id), "world", str(campaign_id), sid, uid, {"map_id": str(map_id), "layer_id": str(result.layer_id), "type": result.type.value})
    return result


@app.get("/maps/{map_id}/layers", response_model=list[LayerOut])
async def list_layers(map_id: UUID, conn: DbConn, campaign_id: UUID = Query(...)) -> list[LayerOut]:
    existing = await database.get_map(conn, map_id, campaign_id)
    if existing is None:
        raise HTTPException(404, "Map not found in this campaign")
    return await database.list_layers(conn, map_id, campaign_id)


@app.patch("/maps/{map_id}/layers/{layer_id}", response_model=LayerOut)
async def patch_layer(map_id: UUID, layer_id: UUID, body: LayerUpdate, conn: DbConn, campaign_id: UUID = Query(...)) -> LayerOut:
    result = await database.patch_layer(conn, layer_id, map_id, campaign_id, body.type, body.name, body.z_index, body.visible, body.features)
    if result is None:
        raise HTTPException(404, "Layer not found in this campaign map")
    sid, uid = _meta(body.meta)
    await event_log.emit("map.layer_updated", str(layer_id), "world", str(campaign_id), sid, uid, {"map_id": str(map_id), "layer_id": str(layer_id)})
    return result


@app.delete("/maps/{map_id}/layers/{layer_id}", status_code=204)
async def delete_layer(map_id: UUID, layer_id: UUID, conn: DbConn, campaign_id: UUID = Query(...)) -> None:
    deleted = await database.delete_layer(conn, layer_id, map_id, campaign_id)
    if not deleted:
        raise HTTPException(404, "Layer not found in this campaign map")


@app.put("/maps/{map_id}/fog", response_model=FogStateOut)
async def put_fog(map_id: UUID, body: FogStatePut, conn: DbConn) -> FogStateOut:
    existing = await database.get_map(conn, map_id, body.campaign_id)
    if existing is None:
        raise HTTPException(404, "Map not found in this campaign")
    result = await database.put_fog(conn, map_id, body.campaign_id, body.character_id, body.explored_cells)
    sid, uid = _meta(body.meta)
    await event_log.emit("map.fog_updated", str(map_id), "world", str(body.campaign_id), sid, uid, {"map_id": str(map_id), "character_id": str(body.character_id), "cell_count": len(result.explored_cells)})
    return result


@app.patch("/maps/{map_id}/fog", response_model=FogStateOut)
async def patch_fog(map_id: UUID, body: FogStatePatch, conn: DbConn) -> FogStateOut:
    existing = await database.get_map(conn, map_id, body.campaign_id)
    if existing is None:
        raise HTTPException(404, "Map not found in this campaign")
    current = await database.get_fog(conn, map_id, body.campaign_id, body.character_id)
    merged = set(current.explored_cells if current else [])
    merged.update(body.add_cells)
    result = await database.put_fog(conn, map_id, body.campaign_id, body.character_id, sorted(merged))
    sid, uid = _meta(body.meta)
    await event_log.emit("map.fog_updated", str(map_id), "world", str(body.campaign_id), sid, uid, {"map_id": str(map_id), "character_id": str(body.character_id), "cell_count": len(result.explored_cells)})
    return result


@app.get("/maps/{map_id}/fog", response_model=FogStateOut)
async def get_fog(map_id: UUID, conn: DbConn, campaign_id: UUID = Query(...), character_id: UUID = Query(...)) -> FogStateOut:
    result = await database.get_fog(conn, map_id, campaign_id, character_id)
    if result is None:
        return _empty_fog(map_id, campaign_id, character_id)
    return result


@app.put("/maps/{map_id}/tokens", response_model=TokenOut, status_code=201)
async def upsert_token(map_id: UUID, body: TokenUpsert, conn: DbConn) -> TokenOut:
    existing = await database.get_map(conn, map_id, body.campaign_id)
    if existing is None:
        raise HTTPException(404, "Map not found in this campaign")
    result = await database.upsert_token(conn, map_id, body.campaign_id, body.encounter_id, body.aggregate_id, body.aggregate_type, body.x, body.y, body.visible)
    sid, uid = _meta(body.meta)
    await event_log.emit("map.token_updated", str(result.token_id), "world", str(body.campaign_id), sid, uid, {"map_id": str(map_id), "token_id": str(result.token_id), "aggregate_id": str(body.aggregate_id), "x": result.x, "y": result.y})
    return result


@app.get("/maps/{map_id}/tokens", response_model=list[TokenOut])
async def list_tokens(map_id: UUID, conn: DbConn, campaign_id: UUID = Query(...), encounter_id: UUID | None = Query(default=None)) -> list[TokenOut]:
    existing = await database.get_map(conn, map_id, campaign_id)
    if existing is None:
        raise HTTPException(404, "Map not found in this campaign")
    return await database.list_tokens(conn, map_id, campaign_id, encounter_id)


@app.patch("/maps/{map_id}/tokens/{token_id}", response_model=TokenOut)
async def patch_token(map_id: UUID, token_id: UUID, body: TokenUpdate, conn: DbConn, campaign_id: UUID = Query(...)) -> TokenOut:
    result = await database.patch_token(conn, token_id, map_id, campaign_id, body.x, body.y, body.visible)
    if result is None:
        raise HTTPException(404, "Token not found in this campaign map")
    sid, uid = _meta(body.meta)
    await event_log.emit("map.token_updated", str(token_id), "world", str(campaign_id), sid, uid, {"map_id": str(map_id), "token_id": str(token_id), "x": result.x, "y": result.y, "visible": result.visible})
    return result


@app.delete("/maps/{map_id}/tokens/{token_id}", status_code=204)
async def delete_token(map_id: UUID, token_id: UUID, conn: DbConn, campaign_id: UUID = Query(...)) -> None:
    deleted = await database.delete_token(conn, token_id, map_id, campaign_id)
    if not deleted:
        raise HTTPException(404, "Token not found in this campaign map")


@app.get("/maps/{map_id}/snapshot", response_model=MapSnapshot)
async def get_snapshot(
    map_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
    character_id: UUID = Query(...),
    encounter_id: UUID | None = Query(default=None),
) -> MapSnapshot:
    map_out = await database.get_map(conn, map_id, campaign_id)
    if map_out is None:
        raise HTTPException(404, "Map not found in this campaign")
    fog = await database.get_fog(conn, map_id, campaign_id, character_id)
    if fog is None:
        fog = _empty_fog(map_id, campaign_id, character_id)
    return MapSnapshot(
        map=map_out,
        layers=await database.list_layers(conn, map_id, campaign_id),
        fog_of_war=fog,
        tokens=await database.list_tokens(conn, map_id, campaign_id, encounter_id),
    )

