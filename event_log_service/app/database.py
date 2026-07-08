"""PostgreSQL pool management and all query functions.

The events table is append-only: INSERT with ON CONFLICT DO NOTHING.
No UPDATE or DELETE queries exist anywhere in this service.
"""
from __future__ import annotations

import json
import logging
from uuid import UUID

import asyncpg

from app.config import settings
from app.models import EventIn

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

# ── Schema ─────────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS events (
    event_id        UUID PRIMARY KEY,
    campaign_id     UUID NOT NULL,
    session_id      UUID NOT NULL,
    user_id         UUID NOT NULL,
    event_type      TEXT NOT NULL,
    aggregate_id    UUID NOT NULL,
    aggregate_type  TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}',
    source_service  TEXT NOT NULL,
    llm_prompt_hash TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_campaign_session
    ON events (campaign_id, session_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_aggregate
    ON events (campaign_id, aggregate_id, occurred_at DESC);
"""

# ── SQL statements ──────────────────────────────────────────────────────────

_INSERT = """
INSERT INTO events (
    event_id, campaign_id, session_id, user_id,
    event_type, aggregate_id, aggregate_type,
    payload, source_service, llm_prompt_hash, occurred_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11)
ON CONFLICT (event_id) DO NOTHING
"""

_SELECT_BY_SESSION = """
SELECT event_id, campaign_id, session_id, user_id,
       event_type, aggregate_id, aggregate_type,
       payload, source_service, llm_prompt_hash, occurred_at
FROM events
WHERE campaign_id = $1 AND session_id = $2
  AND ($3::text IS NULL OR event_type = $3)
ORDER BY occurred_at DESC
LIMIT $4
"""

_SELECT_BY_AGGREGATE = """
SELECT event_id, campaign_id, session_id, user_id,
       event_type, aggregate_id, aggregate_type,
       payload, source_service, llm_prompt_hash, occurred_at
FROM events
WHERE campaign_id = $1 AND aggregate_id = $2 AND aggregate_type = $3
  AND ($4::text IS NULL OR event_type = $4)
ORDER BY occurred_at DESC
LIMIT $5
"""

# ── Pool lifecycle ──────────────────────────────────────────────────────────

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


# ── Migrations ──────────────────────────────────────────────────────────────

async def run_migrations(conn: asyncpg.Connection) -> None:
    """Create the events table and indexes if they do not already exist."""
    await conn.execute(CREATE_TABLE_SQL)
    logger.info("Database migrations applied")


# ── Write ───────────────────────────────────────────────────────────────────

async def insert_event(conn: asyncpg.Connection, event: EventIn) -> None:
    """Append a single event row. Duplicate event_id is silently ignored."""
    await conn.execute(
        _INSERT,
        event.event_id,
        event.campaign_id,
        event.session_id,
        event.user_id,
        event.event_type,
        event.aggregate_id,
        event.aggregate_type,
        json.dumps(event.payload),
        event.source_service,
        event.llm_prompt_hash,
        event.occurred_at,
    )


# ── Read ────────────────────────────────────────────────────────────────────

async def fetch_by_session(
    conn: asyncpg.Connection,
    campaign_id: UUID,
    session_id: UUID,
    limit: int,
    event_type: str | None = None,
) -> list[dict]:
    rows = await conn.fetch(_SELECT_BY_SESSION, campaign_id, session_id, event_type, limit)
    return [dict(row) for row in rows]


async def fetch_by_aggregate(
    conn: asyncpg.Connection,
    campaign_id: UUID,
    aggregate_id: UUID,
    aggregate_type: str,
    limit: int,
    event_type: str | None = None,
) -> list[dict]:
    rows = await conn.fetch(
        _SELECT_BY_AGGREGATE, campaign_id, aggregate_id, aggregate_type, event_type, limit
    )
    return [dict(row) for row in rows]
