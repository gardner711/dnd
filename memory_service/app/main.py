"""FastAPI application — Memory Service entry point."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from app import database, embeddings, stream_consumer
from app.config import settings
from app.dependencies import get_db_conn
from app.models import MemoryIn, MemoryOut, MemoryUpdate, RecallResult, SubjectType, WriteMemoryResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_consumer_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _consumer_task

    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await database.run_migrations(conn)

    _consumer_task = asyncio.create_task(stream_consumer.run())
    logger.info("Memory Service ready")
    yield

    if _consumer_task:
        _consumer_task.cancel()
        try:
            await _consumer_task
        except asyncio.CancelledError:
            pass
    await database.close_pool()
    logger.info("Memory Service stopped")


app = FastAPI(
    title="Memory Service",
    description="Persistent campaign memory with pgvector semantic recall",
    version="0.1.0",
    lifespan=lifespan,
)

DbConn = Annotated[asyncpg.Connection, Depends(get_db_conn)]


# ── Health ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> JSONResponse:
    """Liveness probe — checks database and embedding model."""
    db_ok = False
    embeddings_ok = False

    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception as exc:
        logger.warning("Health: database unavailable: %s", exc)

    try:
        embeddings.embed("health check")
        embeddings_ok = True
    except Exception as exc:
        logger.warning("Health: embedding model unavailable: %s", exc)

    overall = "ok" if (db_ok and embeddings_ok) else "degraded"
    return JSONResponse(
        content={
            "status": overall,
            "service": "memory-service",
            "checks": {"database": db_ok, "embedding_model": embeddings_ok},
        },
        status_code=200 if overall == "ok" else 503,
    )


# ── Write ───────────────────────────────────────────────────────────────────

@app.post("/memories", response_model=WriteMemoryResponse, status_code=201)
async def write_memory(memory: MemoryIn, conn: DbConn) -> WriteMemoryResponse:
    """Write a memory record. The embedding is generated automatically from content."""
    embedding = embeddings.embed(memory.content)
    result = await database.insert_memory(conn, memory, embedding)
    return WriteMemoryResponse(memory_id=str(result.memory_id))


# ── Recall ───────────────────────────────────────────────────────────────────

@app.get("/memories/recall", response_model=RecallResult)
async def recall_memories(
    conn: DbConn,
    campaign_id: UUID = Query(..., description="Campaign to search within (required)"),
    query: str = Query(..., min_length=1, description="Natural language recall query"),
    subject_type: SubjectType | None = Query(None, description="Narrow to one subject type"),
    subject_id: UUID | None = Query(None, description="Narrow to one specific entity"),
    top_k: int = Query(default=5, ge=1, le=20, description="Number of memories to return"),
) -> RecallResult:
    """Return the top-K memories most semantically relevant to the query.

    Used by the DM Service and NPC Service to inject relevant past events
    into LLM prompts without exceeding the context window.
    """
    query_embedding = embeddings.embed(query)
    memories = await database.recall_memories(
        conn, campaign_id, query_embedding, subject_type, subject_id, top_k
    )
    return RecallResult(memories=memories, query=query, top_k=top_k)


# ── List ─────────────────────────────────────────────────────────────────────

@app.get("/memories", response_model=list[MemoryOut])
async def list_memories(
    conn: DbConn,
    campaign_id: UUID = Query(...),
    subject_type: SubjectType | None = Query(None),
    subject_id: UUID | None = Query(None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[MemoryOut]:
    """List memories for a campaign scope, most recent first. For admin/debug use."""
    return await database.list_memories(
        conn, campaign_id, subject_type, subject_id, limit, offset
    )


# ── Single memory ───────────────────────────────────────────────────────────

@app.get("/memories/{memory_id}", response_model=MemoryOut)
async def get_memory(
    memory_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(..., description="Required to enforce campaign isolation"),
) -> MemoryOut:
    """Fetch a single memory record by ID."""
    result = await database.get_memory(conn, memory_id, campaign_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Memory not found in this campaign")
    return result


@app.patch("/memories/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: UUID,
    update: MemoryUpdate,
    conn: DbConn,
    campaign_id: UUID = Query(..., description="Required to enforce campaign isolation"),
) -> MemoryOut:
    """Update importance and/or content. Regenerates embedding when content changes."""
    new_embedding = embeddings.embed(update.content) if update.content is not None else None
    result = await database.update_memory(
        conn, memory_id, campaign_id,
        update.importance, update.content, new_embedding,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Memory not found in this campaign")
    return result


# ── Delete ────────────────────────────────────────────────────────────────────

@app.delete("/memories/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: UUID,
    conn: DbConn,
    campaign_id: UUID = Query(..., description="Required to enforce campaign isolation"),
) -> None:
    """Remove a memory record. campaign_id is required to prevent cross-campaign deletion."""
    deleted = await database.delete_memory(conn, memory_id, campaign_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found in this campaign")
