"""PostgreSQL data access for the Story State Service.

All queries are campaign-scoped — every SELECT, INSERT, and UPDATE
includes campaign_id in the predicate.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Optional
from uuid import UUID

import asyncpg

from app.config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS quests (
    quest_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id         UUID        NOT NULL,
    title               TEXT        NOT NULL,
    description         TEXT,
    status              TEXT        NOT NULL DEFAULT 'active'
                            CHECK (status IN ('hidden','active','completed','failed')),
    giver_npc_id        UUID,
    reward_description  TEXT,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_quests_campaign_status
    ON quests (campaign_id, status);

CREATE TABLE IF NOT EXISTS quest_objectives (
    objective_id    UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    quest_id        UUID    NOT NULL REFERENCES quests(quest_id) ON DELETE CASCADE,
    campaign_id     UUID    NOT NULL,
    description     TEXT    NOT NULL,
    sequence_order  INT     NOT NULL DEFAULT 0,
    completed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_quest_objectives_campaign_quest
    ON quest_objectives (campaign_id, quest_id);

CREATE TABLE IF NOT EXISTS plot_hooks (
    hook_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id     UUID        NOT NULL,
    content         TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','resolved','dismissed')),
    priority        TEXT        NOT NULL DEFAULT 'medium'
                        CHECK (priority IN ('low','medium','high','critical')),
    source_event_id UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_plot_hooks_campaign_status
    ON plot_hooks (campaign_id, status, priority);

CREATE TABLE IF NOT EXISTS story_log (
    entry_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID        NOT NULL,
    session_id  UUID,
    entry_type  TEXT        NOT NULL
                    CHECK (entry_type IN (
                        'narration','combat_summary','quest_update',
                        'hook_note','session_summary')),
    content     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_story_log_campaign_session
    ON story_log (campaign_id, session_id, created_at);
"""


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def run_migrations(conn: asyncpg.Connection) -> None:
    await conn.execute(SCHEMA_SQL)


# ── Quest functions ───────────────────────────────────────────────────────────

async def create_quest(
    conn: asyncpg.Connection,
    campaign_id: UUID,
    title: str,
    description: Optional[str],
    status: str,
    giver_npc_id: Optional[UUID],
    reward_description: Optional[str],
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        INSERT INTO quests
            (campaign_id, title, description, status, giver_npc_id, reward_description)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        campaign_id, title, description, status, giver_npc_id, reward_description,
    )


async def add_objective(
    conn: asyncpg.Connection,
    quest_id: UUID,
    campaign_id: UUID,
    description: str,
    sequence_order: int,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        INSERT INTO quest_objectives (quest_id, campaign_id, description, sequence_order)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        quest_id, campaign_id, description, sequence_order,
    )


async def get_quest_objectives(
    conn: asyncpg.Connection,
    quest_id: UUID,
    campaign_id: UUID,
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT * FROM quest_objectives
        WHERE quest_id = $1 AND campaign_id = $2
        ORDER BY sequence_order, objective_id
        """,
        quest_id, campaign_id,
    )


async def list_quests(
    conn: asyncpg.Connection,
    campaign_id: UUID,
    status: Optional[str] = None,
    include_hidden: bool = False,
) -> list[asyncpg.Record]:
    if include_hidden:
        return await conn.fetch(
            """
            SELECT * FROM quests
            WHERE campaign_id = $1
              AND ($2::text IS NULL OR status = $2)
            ORDER BY started_at
            """,
            campaign_id, status,
        )
    return await conn.fetch(
        """
        SELECT * FROM quests
        WHERE campaign_id = $1
          AND status != 'hidden'
          AND ($2::text IS NULL OR status = $2)
        ORDER BY started_at
        """,
        campaign_id, status,
    )


async def get_quest(
    conn: asyncpg.Connection,
    quest_id: UUID,
    campaign_id: UUID,
    include_hidden: bool = False,
) -> asyncpg.Record | None:
    if include_hidden:
        return await conn.fetchrow(
            "SELECT * FROM quests WHERE quest_id = $1 AND campaign_id = $2",
            quest_id, campaign_id,
        )
    return await conn.fetchrow(
        "SELECT * FROM quests WHERE quest_id = $1 AND campaign_id = $2 AND status != 'hidden'",
        quest_id, campaign_id,
    )


async def patch_quest(
    conn: asyncpg.Connection,
    quest_id: UUID,
    campaign_id: UUID,
    title: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
    reward_description: Optional[str] = None,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        UPDATE quests SET
            title              = COALESCE($1, title),
            description        = COALESCE($2, description),
            status             = COALESCE($3, status),
            reward_description = COALESCE($4, reward_description),
            completed_at       = CASE
                WHEN $3 IN ('completed', 'failed') AND completed_at IS NULL THEN now()
                ELSE completed_at
            END,
            updated_at         = now()
        WHERE quest_id = $5 AND campaign_id = $6
        RETURNING *
        """,
        title, description, status, reward_description, quest_id, campaign_id,
    )


async def delete_quest(
    conn: asyncpg.Connection,
    quest_id: UUID,
    campaign_id: UUID,
) -> bool:
    result = await conn.execute(
        "DELETE FROM quests WHERE quest_id = $1 AND campaign_id = $2",
        quest_id, campaign_id,
    )
    return result == "DELETE 1"


async def patch_objective(
    conn: asyncpg.Connection,
    objective_id: UUID,
    quest_id: UUID,
    campaign_id: UUID,
    completed: bool,
) -> asyncpg.Record | None:
    completed_at = datetime.now(UTC) if completed else None
    return await conn.fetchrow(
        """
        UPDATE quest_objectives
        SET completed_at = $1
        WHERE objective_id = $2 AND quest_id = $3 AND campaign_id = $4
        RETURNING *
        """,
        completed_at, objective_id, quest_id, campaign_id,
    )


async def get_quest_objectives_bulk(
    conn: asyncpg.Connection,
    quest_ids: list[UUID],
    campaign_id: UUID,
) -> list[asyncpg.Record]:
    """Fetch objectives for multiple quests in a single query."""
    if not quest_ids:
        return []
    return await conn.fetch(
        """
        SELECT * FROM quest_objectives
        WHERE quest_id = ANY($1::uuid[]) AND campaign_id = $2
        ORDER BY quest_id, sequence_order, objective_id
        """,
        quest_ids, campaign_id,
    )


async def delete_objective(
    conn: asyncpg.Connection,
    objective_id: UUID,
    quest_id: UUID,
    campaign_id: UUID,
) -> bool:
    result = await conn.execute(
        "DELETE FROM quest_objectives WHERE objective_id = $1 AND quest_id = $2 AND campaign_id = $3",
        objective_id, quest_id, campaign_id,
    )
    return result == "DELETE 1"


# ── Hook functions ────────────────────────────────────────────────────────────

async def create_hook(
    conn: asyncpg.Connection,
    campaign_id: UUID,
    content: str,
    priority: str,
    source_event_id: Optional[UUID],
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        INSERT INTO plot_hooks (campaign_id, content, priority, source_event_id)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        campaign_id, content, priority, source_event_id,
    )


async def list_hooks(
    conn: asyncpg.Connection,
    campaign_id: UUID,
    status: Optional[str] = None,
    priority: Optional[str] = None,
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT * FROM plot_hooks
        WHERE campaign_id = $1
          AND ($2::text IS NULL OR status = $2)
          AND ($3::text IS NULL OR priority = $3)
        ORDER BY
            CASE priority
                WHEN 'critical' THEN 0 WHEN 'high'   THEN 1
                WHEN 'medium'   THEN 2 ELSE                3
            END,
            created_at
        """,
        campaign_id, status, priority,
    )


async def get_hook(
    conn: asyncpg.Connection,
    hook_id: UUID,
    campaign_id: UUID,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT * FROM plot_hooks WHERE hook_id = $1 AND campaign_id = $2",
        hook_id, campaign_id,
    )


async def patch_hook(
    conn: asyncpg.Connection,
    hook_id: UUID,
    campaign_id: UUID,
    content: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        UPDATE plot_hooks SET
            content     = COALESCE($1, content),
            status      = COALESCE($2, status),
            priority    = COALESCE($3, priority),
            resolved_at = CASE
                WHEN $2 IN ('resolved', 'dismissed') AND resolved_at IS NULL THEN now()
                ELSE resolved_at
            END
        WHERE hook_id = $4 AND campaign_id = $5
        RETURNING *
        """,
        content, status, priority, hook_id, campaign_id,
    )


async def delete_hook(
    conn: asyncpg.Connection,
    hook_id: UUID,
    campaign_id: UUID,
) -> bool:
    result = await conn.execute(
        "DELETE FROM plot_hooks WHERE hook_id = $1 AND campaign_id = $2",
        hook_id, campaign_id,
    )
    return result == "DELETE 1"


# ── Story Log functions ───────────────────────────────────────────────────────

async def insert_story_log_batch(
    conn: asyncpg.Connection,
    entries: list[dict],
) -> list[asyncpg.Record]:
    rows = []
    for e in entries:
        row = await conn.fetchrow(
            """
            INSERT INTO story_log (campaign_id, session_id, entry_type, content)
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            e["campaign_id"], e.get("session_id"), e["entry_type"], e["content"],
        )
        rows.append(row)
    return rows


async def list_story_log(
    conn: asyncpg.Connection,
    campaign_id: UUID,
    session_id: Optional[UUID] = None,
    entry_type: Optional[str] = None,
    limit: int = 50,
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT * FROM story_log
        WHERE campaign_id = $1
          AND ($2::uuid IS NULL OR session_id = $2)
          AND ($3::text IS NULL OR entry_type = $3)
        ORDER BY created_at DESC
        LIMIT $4
        """,
        campaign_id, session_id, entry_type, limit,
    )
