"""PostgreSQL + pgvector data access for the Memory Service.

All queries are scoped to campaign_id. No query ever omits this filter.
"""
from __future__ import annotations

import logging
from uuid import UUID

import asyncpg
import numpy as np

from app.config import settings
from app.embeddings import to_pg_literal
from app.models import MemoryIn, MemoryOut, SubjectType

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

# ── Schema ──────────────────────────────────────────────────────────────────

SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memories (
    memory_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id      UUID NOT NULL,
    subject_type     TEXT NOT NULL,
    subject_id       UUID NOT NULL,
    content          TEXT NOT NULL,
    embedding        vector({settings.embedding_dimensions}),
    importance       INT NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
    source_event_ids UUID[] NOT NULL DEFAULT '{{}}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memories_campaign
    ON memories (campaign_id);

CREATE INDEX IF NOT EXISTS idx_memories_subject
    ON memories (campaign_id, subject_type, subject_id);

CREATE INDEX IF NOT EXISTS idx_memories_embedding
    ON memories USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
"""

# ── SQL ──────────────────────────────────────────────────────────────────────

_INSERT = """
INSERT INTO memories (
    campaign_id, subject_type, subject_id, content,
    embedding, importance, source_event_ids
) VALUES ($1::uuid, $2, $3::uuid, $4, $5::vector, $6, $7::uuid[])
RETURNING memory_id, created_at, last_accessed_at
"""

_RECALL = """
SELECT memory_id, campaign_id, subject_type, subject_id,
       content, importance, source_event_ids, created_at, last_accessed_at
FROM memories
WHERE campaign_id = $1::uuid
  AND ($2::text IS NULL OR subject_type = $2)
  AND ($3::text IS NULL OR subject_id::text = $3)
  AND embedding IS NOT NULL
ORDER BY (embedding <=> $4::vector) / importance
LIMIT $5
"""

_UPDATE_LAST_ACCESSED = """
UPDATE memories SET last_accessed_at = NOW()
WHERE memory_id = ANY($1::uuid[])
"""

_GET_BY_ID = """
SELECT memory_id, campaign_id, subject_type, subject_id,
       content, importance, source_event_ids, created_at, last_accessed_at
FROM memories
WHERE memory_id = $1::uuid AND campaign_id = $2::uuid
"""

_UPDATE_MEMORY = """
UPDATE memories
SET importance = COALESCE($3, importance),
    content    = COALESCE($4, content),
    embedding  = COALESCE($5::vector, embedding)
WHERE memory_id = $1::uuid AND campaign_id = $2::uuid
RETURNING memory_id, campaign_id, subject_type, subject_id,
          content, importance, source_event_ids, created_at, last_accessed_at
"""

_LIST = """
SELECT memory_id, campaign_id, subject_type, subject_id,
       content, importance, source_event_ids, created_at, last_accessed_at
FROM memories
WHERE campaign_id = $1::uuid
  AND ($2::text IS NULL OR subject_type = $2)
  AND ($3::text IS NULL OR subject_id::text = $3)
ORDER BY created_at DESC
LIMIT $4 OFFSET $5
"""

_DELETE = """
DELETE FROM memories
WHERE memory_id = $1::uuid AND campaign_id = $2::uuid
RETURNING memory_id
"""

# ── Pool lifecycle ────────────────────────────────────────────────────────────

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
    logger.info("Memory Service migrations applied")


# ── Operations ───────────────────────────────────────────────────────────────

async def insert_memory(
    conn: asyncpg.Connection,
    memory: MemoryIn,
    embedding: np.ndarray,
) -> MemoryOut:
    row = await conn.fetchrow(
        _INSERT,
        str(memory.campaign_id),
        memory.subject_type.value,
        str(memory.subject_id),
        memory.content,
        to_pg_literal(embedding),
        memory.importance,
        [str(eid) for eid in memory.source_event_ids],
    )
    return MemoryOut(
        memory_id=str(row["memory_id"]),
        campaign_id=memory.campaign_id,
        subject_type=memory.subject_type,
        subject_id=memory.subject_id,
        content=memory.content,
        importance=memory.importance,
        source_event_ids=memory.source_event_ids,
        created_at=row["created_at"],
        last_accessed_at=row["last_accessed_at"],
    )


async def recall_memories(
    conn: asyncpg.Connection,
    campaign_id: UUID,
    query_embedding: np.ndarray,
    subject_type: SubjectType | None,
    subject_id: UUID | None,
    top_k: int,
) -> list[MemoryOut]:
    rows = await conn.fetch(
        _RECALL,
        str(campaign_id),
        subject_type.value if subject_type else None,
        str(subject_id) if subject_id else None,
        to_pg_literal(query_embedding),
        top_k,
    )
    memories = [_row_to_memory(row) for row in rows]

    # Update last_accessed_at for returned memories (non-fatal on failure)
    if memories:
        try:
            ids = [str(m.memory_id) for m in memories]
            await conn.execute(_UPDATE_LAST_ACCESSED, ids)
        except Exception as exc:
            logger.warning("Failed to update last_accessed_at: %s", exc)

    return memories


async def get_memory(
    conn: asyncpg.Connection,
    memory_id: UUID,
    campaign_id: UUID,
) -> MemoryOut | None:
    row = await conn.fetchrow(_GET_BY_ID, str(memory_id), str(campaign_id))
    return _row_to_memory(row) if row else None


async def update_memory(
    conn: asyncpg.Connection,
    memory_id: UUID,
    campaign_id: UUID,
    new_importance: int | None,
    new_content: str | None,
    new_embedding: np.ndarray | None,
) -> MemoryOut | None:
    embedding_str = to_pg_literal(new_embedding) if new_embedding is not None else None
    row = await conn.fetchrow(
        _UPDATE_MEMORY,
        str(memory_id),
        str(campaign_id),
        new_importance,
        new_content,
        embedding_str,
    )
    return _row_to_memory(row) if row else None


async def list_memories(
    conn: asyncpg.Connection,
    campaign_id: UUID,
    subject_type: SubjectType | None,
    subject_id: UUID | None,
    limit: int,
    offset: int,
) -> list[MemoryOut]:
    rows = await conn.fetch(
        _LIST,
        str(campaign_id),
        subject_type.value if subject_type else None,
        str(subject_id) if subject_id else None,
        limit,
        offset,
    )
    return [_row_to_memory(row) for row in rows]


async def delete_memory(
    conn: asyncpg.Connection,
    memory_id: UUID,
    campaign_id: UUID,
) -> bool:
    row = await conn.fetchrow(_DELETE, str(memory_id), str(campaign_id))
    return row is not None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _row_to_memory(row) -> MemoryOut:
    return MemoryOut(
        memory_id=str(row["memory_id"]),
        campaign_id=str(row["campaign_id"]),
        subject_type=SubjectType(row["subject_type"]),
        subject_id=str(row["subject_id"]),
        content=row["content"],
        importance=row["importance"],
        source_event_ids=[str(eid) for eid in (row["source_event_ids"] or [])],
        created_at=row["created_at"],
        last_accessed_at=row["last_accessed_at"],
    )
