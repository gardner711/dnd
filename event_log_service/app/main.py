"""FastAPI application — Event Log Service entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from app import database, streams
from app.dependencies import get_db_conn
from app.models import EventIn, EventOut, WriteEventResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await database.run_migrations(conn)
    logger.info("Event Log Service ready")
    yield
    await database.close_pool()
    await streams.close_redis()
    logger.info("Event Log Service stopped")


app = FastAPI(
    title="Event Log Service",
    description="Append-only audit trail for all game events. No updates. No deletes.",
    version="0.1.0",
    lifespan=lifespan,
)

DbConn = Annotated[asyncpg.Connection, Depends(get_db_conn)]


# ── Health ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Liveness/readiness probe. Returns 503 if either dependency is unreachable."""
    db_ok = False
    redis_ok = False

    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception as exc:
        logger.warning("Health check: database unavailable: %s", exc)

    try:
        client = await streams.get_redis()
        await client.ping()
        redis_ok = True
    except Exception as exc:
        logger.warning("Health check: Redis unavailable: %s", exc)

    overall = "ok" if (db_ok and redis_ok) else "degraded"
    return JSONResponse(
        content={
            "status": overall,
            "service": "event-log-service",
            "checks": {"database": db_ok, "redis": redis_ok},
        },
        status_code=200 if overall == "ok" else 503,
    )


# ── Write ───────────────────────────────────────────────────────────────────

@app.post("/events", status_code=201, response_model=WriteEventResponse)
async def write_event(event: EventIn, conn: DbConn) -> WriteEventResponse:
    """Append a single event to the event store and publish it to Redis Streams.

    Duplicate event_id submissions are silently ignored (idempotent write).
    Redis publish failure is logged but does not fail the request — the
    PostgreSQL write is the authoritative record.
    """
    await database.insert_event(conn, event)

    try:
        await streams.publish_event(
            str(event.campaign_id), event.model_dump(mode="json")
        )
    except Exception as exc:
        logger.warning(
            "Redis publish failed for event %s (%s): %s",
            event.event_id,
            event.event_type,
            exc,
        )

    return WriteEventResponse(event_id=str(event.event_id))


# ── Read ────────────────────────────────────────────────────────────────────

@app.get("/events", response_model=list[EventOut])
async def read_events(
    conn: DbConn,
    campaign_id: UUID = Query(..., description="Campaign to query (required)"),
    session_id: UUID | None = Query(None, description="Filter by play session"),
    aggregate_id: UUID | None = Query(None, description="Filter by entity ID"),
    aggregate_type: str | None = Query(
        None, description="Entity type: character | npc | combat | story | world"
    ),
    event_type: str | None = Query(
        None, description="Narrow by event type e.g. dice.rolled, attack.resolved"
    ),
    limit: int = Query(default=50, ge=1, le=500, description="Max events to return"),
) -> list[EventOut]:
    """Return events most-recent-first.

    Exactly one primary filter group must be provided:
      - session_id  (for DM Service prompt context)
      - aggregate_id + aggregate_type  (for entity history / campaign replay)

    Optionally narrow by event_type (e.g. 'dice.rolled').
    """
    if session_id is not None:
        rows = await database.fetch_by_session(
            conn, campaign_id, session_id, limit, event_type
        )
    elif aggregate_id is not None and aggregate_type is not None:
        rows = await database.fetch_by_aggregate(
            conn, campaign_id, aggregate_id, aggregate_type, limit, event_type
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide session_id, or both aggregate_id and aggregate_type",
        )

    return [EventOut(**row) for row in rows]
