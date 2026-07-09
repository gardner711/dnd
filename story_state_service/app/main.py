"""FastAPI application — Story State Service."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Optional
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from app import database, event_log
from app.dependencies import get_db_conn
from app.models import (
    DMContext,
    HookCreate, HookOut, HookStatus, HookUpdate,
    ObjectiveCreate, ObjectiveOut, ObjectivePatch,
    QuestCreate, QuestOut, QuestStatus, QuestUpdate,
    StoryLogBatch, StoryLogOut,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await database.run_migrations(conn)
    logger.info("Story State Service ready")
    yield
    await database.close_pool()
    logger.info("Story State Service stopped")


app = FastAPI(
    title="Story State Service",
    description="Narrative state — quests, plot hooks, and story log",
    version="0.1.0",
    lifespan=lifespan,
)

DbConn = Annotated[asyncpg.Connection, Depends(get_db_conn)]


async def _quest_out(conn: asyncpg.Connection, row, campaign_id: UUID) -> QuestOut:
    """Attach objectives to a quest row and return a fully-populated QuestOut."""
    obj_rows = await database.get_quest_objectives(conn, row["quest_id"], campaign_id)
    return QuestOut(
        **dict(row),
        objectives=[ObjectiveOut(**dict(o)) for o in obj_rows],
    )


async def _build_quest_list_out(
    conn: asyncpg.Connection, rows, campaign_id: UUID
) -> list[QuestOut]:
    """Build QuestOut list with one bulk objectives query instead of N+1."""
    if not rows:
        return []
    quest_ids = [r["quest_id"] for r in rows]
    all_objs = await database.get_quest_objectives_bulk(conn, quest_ids, campaign_id)
    obj_map: dict = {}
    for o in all_objs:
        obj_map.setdefault(o["quest_id"], []).append(ObjectiveOut(**dict(o)))
    return [
        QuestOut(**dict(r), objectives=obj_map.get(r["quest_id"], []))
        for r in rows
    ]


def _meta_strs(meta) -> tuple[str | None, str | None]:
    """Return (session_id_str, user_id_str) from an optional EventMeta."""
    if meta is None:
        return None, None
    return (
        str(meta.session_id) if meta.session_id else None,
        str(meta.user_id) if meta.user_id else None,
    )


# ── Health ────────────────────────────────────────────────────────────────────

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
        content={
            "status": "ok" if db_ok else "degraded",
            "service": "story-state-service",
            "checks": {"database": db_ok},
        },
        status_code=200 if db_ok else 503,
    )


# ── Quests (player-visible — hidden quests are never exposed) ─────────────────

@app.post("/quests", status_code=201)
async def create_quest(body: QuestCreate, conn: DbConn) -> QuestOut:
    row = await database.create_quest(
        conn,
        campaign_id=body.campaign_id,
        title=body.title,
        description=body.description,
        status=body.status,
        giver_npc_id=body.giver_npc_id,
        reward_description=body.reward_description,
    )
    for obj in body.objectives:
        await database.add_objective(
            conn, row["quest_id"], body.campaign_id,
            obj.description, obj.sequence_order,
        )
    if body.status != QuestStatus.hidden:
        sid, uid = _meta_strs(body.meta)
        await event_log.emit(
            event_type="story.quest_started",
            aggregate_id=str(row["quest_id"]),
            aggregate_type="story",
            campaign_id=str(body.campaign_id),
            session_id=sid,
            user_id=uid,
            payload={"quest_id": str(row["quest_id"]), "title": body.title},
        )
    return await _quest_out(conn, row, body.campaign_id)


@app.get("/quests")
async def list_quests(
    conn: DbConn,
    campaign_id: UUID = Query(...),
    status: Optional[str] = Query(default=None),
) -> list[QuestOut]:
    rows = await database.list_quests(conn, campaign_id, status, include_hidden=False)
    return await _build_quest_list_out(conn, rows, campaign_id)


@app.get("/quests/{quest_id}")
async def get_quest(
    quest_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> QuestOut:
    row = await database.get_quest(conn, quest_id, campaign_id, include_hidden=False)
    if row is None:
        raise HTTPException(404, "Quest not found in this campaign")
    return await _quest_out(conn, row, campaign_id)


@app.patch("/quests/{quest_id}")
async def patch_quest_route(
    quest_id: UUID,
    body: QuestUpdate,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> QuestOut:
    row = await database.patch_quest(
        conn, quest_id, campaign_id,
        title=body.title,
        description=body.description,
        status=body.status,
        reward_description=body.reward_description,
    )
    if row is None:
        raise HTTPException(404, "Quest not found in this campaign")

    if body.status is not None:
        _QUEST_STATUS_EVENTS = {
            QuestStatus.active:    "story.quest_started",
            QuestStatus.completed: "story.quest_completed",
            QuestStatus.failed:    "story.quest_failed",
        }
        event_type = _QUEST_STATUS_EVENTS.get(body.status)
        if event_type:
            sid, uid = _meta_strs(body.meta)
            await event_log.emit(
                event_type=event_type,
                aggregate_id=str(quest_id),
                aggregate_type="story",
                campaign_id=str(campaign_id),
                session_id=sid,
                user_id=uid,
                payload={"quest_id": str(quest_id), "new_status": body.status},
            )
    return await _quest_out(conn, row, campaign_id)


@app.delete("/quests/{quest_id}", status_code=204)
async def delete_quest(
    quest_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> None:
    deleted = await database.delete_quest(conn, quest_id, campaign_id)
    if not deleted:
        raise HTTPException(404, "Quest not found in this campaign")


@app.patch("/quests/{quest_id}/objectives/{objective_id}")
async def patch_objective(
    quest_id: UUID,
    objective_id: UUID,
    body: ObjectivePatch,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> ObjectiveOut:
    row = await database.patch_objective(
        conn, objective_id, quest_id, campaign_id, body.completed,
    )
    if row is None:
        raise HTTPException(404, "Objective not found")
    if body.completed:
        await event_log.emit(
            event_type="story.objective_completed",
            aggregate_id=str(quest_id),
            aggregate_type="story",
            campaign_id=str(campaign_id),
            session_id=None,
            user_id=None,
            payload={"objective_id": str(objective_id), "quest_id": str(quest_id)},
        )
    return ObjectiveOut(**dict(row))


@app.post("/quests/{quest_id}/objectives", status_code=201)
async def add_quest_objective(
    quest_id: UUID,
    body: ObjectiveCreate,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> ObjectiveOut:
    quest = await database.get_quest(conn, quest_id, campaign_id, include_hidden=True)
    if quest is None:
        raise HTTPException(404, "Quest not found in this campaign")
    row = await database.add_objective(
        conn, quest_id, campaign_id, body.description, body.sequence_order,
    )
    return ObjectiveOut(**dict(row))


@app.delete("/quests/{quest_id}/objectives/{objective_id}", status_code=204)
async def delete_quest_objective(
    quest_id: UUID,
    objective_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> None:
    deleted = await database.delete_objective(conn, objective_id, quest_id, campaign_id)
    if not deleted:
        raise HTTPException(404, "Objective not found")


# ── DM quests (all statuses, including hidden) ────────────────────────────────

@app.get("/dm/quests")
async def dm_list_quests(
    conn: DbConn,
    campaign_id: UUID = Query(...),
    status: Optional[str] = Query(default=None),
) -> list[QuestOut]:
    rows = await database.list_quests(conn, campaign_id, status, include_hidden=True)
    return await _build_quest_list_out(conn, rows, campaign_id)


@app.get("/dm/quests/{quest_id}")
async def dm_get_quest(
    quest_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> QuestOut:
    row = await database.get_quest(conn, quest_id, campaign_id, include_hidden=True)
    if row is None:
        raise HTTPException(404, "Quest not found in this campaign")
    return await _quest_out(conn, row, campaign_id)


# ── Plot Hooks ────────────────────────────────────────────────────────────────

@app.post("/hooks", status_code=201)
async def create_hook(body: HookCreate, conn: DbConn) -> HookOut:
    row = await database.create_hook(
        conn,
        campaign_id=body.campaign_id,
        content=body.content,
        priority=body.priority,
        source_event_id=body.source_event_id,
    )
    sid, uid = _meta_strs(body.meta)
    await event_log.emit(
        event_type="story.hook_created",
        aggregate_id=str(row["hook_id"]),
        aggregate_type="story",
        campaign_id=str(body.campaign_id),
        session_id=sid,
        user_id=uid,
        payload={
            "hook_id": str(row["hook_id"]),
            "content": body.content,
            "priority": body.priority,
        },
    )
    return HookOut(**dict(row))


@app.get("/hooks")
async def list_hooks(
    conn: DbConn,
    campaign_id: UUID = Query(...),
    status: Optional[str] = Query(default=None),
    priority: Optional[str] = Query(default=None),
) -> list[HookOut]:
    rows = await database.list_hooks(conn, campaign_id, status, priority)
    return [HookOut(**dict(r)) for r in rows]


@app.get("/hooks/{hook_id}")
async def get_hook(
    hook_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> HookOut:
    row = await database.get_hook(conn, hook_id, campaign_id)
    if row is None:
        raise HTTPException(404, "Plot hook not found in this campaign")
    return HookOut(**dict(row))


@app.patch("/hooks/{hook_id}")
async def patch_hook_route(
    hook_id: UUID,
    body: HookUpdate,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> HookOut:
    row = await database.patch_hook(
        conn, hook_id, campaign_id,
        content=body.content,
        status=body.status,
        priority=body.priority,
    )
    if row is None:
        raise HTTPException(404, "Plot hook not found in this campaign")
    if body.status in (HookStatus.resolved, HookStatus.dismissed):
        sid, uid = _meta_strs(body.meta)
        await event_log.emit(
            event_type="story.hook_resolved",
            aggregate_id=str(hook_id),
            aggregate_type="story",
            campaign_id=str(campaign_id),
            session_id=sid,
            user_id=uid,
            payload={"hook_id": str(hook_id), "resolution": body.status},
        )
    return HookOut(**dict(row))


@app.delete("/hooks/{hook_id}", status_code=204)
async def delete_hook(
    hook_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(...),
) -> None:
    deleted = await database.delete_hook(conn, hook_id, campaign_id)
    if not deleted:
        raise HTTPException(404, "Plot hook not found in this campaign")


# ── Story Log ─────────────────────────────────────────────────────────────────

@app.post("/story-log", status_code=201)
async def post_story_log(body: StoryLogBatch, conn: DbConn) -> list[StoryLogOut]:
    raw = [e.model_dump() for e in body.entries]
    rows = await database.insert_story_log_batch(conn, raw)
    for e in body.entries:
        if e.entry_type == "session_summary":
            sid, uid = _meta_strs(body.meta)
            await event_log.emit(
                event_type="story.session_summary_created",
                aggregate_id=str(e.campaign_id),
                aggregate_type="story",
                campaign_id=str(e.campaign_id),
                session_id=str(e.session_id) if e.session_id else sid,
                user_id=uid,
                payload={"content": e.content[:200]},
            )
            break  # one event per batch even if multiple session_summary entries
    return [StoryLogOut(**dict(r)) for r in rows]


@app.get("/story-log")
async def get_story_log(
    conn: DbConn,
    campaign_id: UUID = Query(...),
    session_id: Optional[UUID] = Query(default=None),
    entry_type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[StoryLogOut]:
    rows = await database.list_story_log(conn, campaign_id, session_id, entry_type, limit)
    return [StoryLogOut(**dict(r)) for r in rows]


# ── DM Context ───────────────────────────────────────────────────────────────

@app.get("/context")
async def get_dm_context(
    conn: DbConn,
    campaign_id: UUID = Query(...),
    session_id: Optional[UUID] = Query(default=None),
    log_limit: int = Query(default=20, ge=1, le=100),
) -> DMContext:
    """Single call that returns everything the DM Service needs to build a turn prompt."""
    quest_rows = await database.list_quests(conn, campaign_id, status="active", include_hidden=False)
    hook_rows  = await database.list_hooks(conn, campaign_id, status="open")
    log_rows   = await database.list_story_log(
        conn, campaign_id, session_id=session_id, limit=log_limit,
    )
    return DMContext(
        campaign_id=campaign_id,
        active_quests=await _build_quest_list_out(conn, quest_rows, campaign_id),
        open_hooks=[HookOut(**dict(r)) for r in hook_rows],
        # log_rows are DESC (newest first); reverse so DM reads chronologically
        recent_log=[StoryLogOut(**dict(r)) for r in reversed(log_rows)],
    )
