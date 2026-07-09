"""PostgreSQL data access for the NPC Interaction Service.

All queries are campaign-scoped. Secrets are stored server-side and
never returned in player-facing responses — only the context assembly
endpoint injects them into the system prompt.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

import asyncpg

from app.config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS npc_profiles (
    npc_id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id          UUID        NOT NULL,
    name                 TEXT        NOT NULL,
    role                 TEXT        NOT NULL,
    physical_description TEXT,
    personality_prompt   TEXT        NOT NULL
                             CHECK (char_length(personality_prompt) <= 2000),
    is_active            BOOLEAN     NOT NULL DEFAULT true,
    faction_id           UUID,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_npc_profiles_campaign
    ON npc_profiles (campaign_id, is_active);
-- Idempotent migration: add faction_id if the table already exists without it
ALTER TABLE npc_profiles ADD COLUMN IF NOT EXISTS faction_id UUID;

CREATE TABLE IF NOT EXISTS npc_secrets (
    secret_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    npc_id                 UUID        NOT NULL
                               REFERENCES npc_profiles(npc_id) ON DELETE CASCADE,
    campaign_id            UUID        NOT NULL,
    content                TEXT        NOT NULL,
    condition_type         TEXT        NOT NULL
                               CHECK (condition_type IN
                                   ('always','disposition_gte','quest_status')),
    condition_value        INT,
    condition_quest_title  TEXT,
    condition_quest_status TEXT,
    revealed_at            TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_npc_secrets_campaign_npc
    ON npc_secrets (campaign_id, npc_id);
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


# ── NPC Profile functions ─────────────────────────────────────────────────────

async def create_npc(
    conn: asyncpg.Connection,
    campaign_id: UUID,
    name: str,
    role: str,
    physical_description: Optional[str],
    personality_prompt: str,
    faction_id: Optional[UUID] = None,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        INSERT INTO npc_profiles
            (campaign_id, name, role, physical_description, personality_prompt, faction_id)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        campaign_id, name, role, physical_description, personality_prompt, faction_id,
    )


async def list_npcs(
    conn: asyncpg.Connection,
    campaign_id: UUID,
    active_only: bool = True,
) -> list[asyncpg.Record]:
    if active_only:
        return await conn.fetch(
            """
            SELECT * FROM npc_profiles
            WHERE campaign_id = $1 AND is_active = true
            ORDER BY name
            """,
            campaign_id,
        )
    return await conn.fetch(
        "SELECT * FROM npc_profiles WHERE campaign_id = $1 ORDER BY name",
        campaign_id,
    )


async def get_npc(
    conn: asyncpg.Connection,
    npc_id: UUID,
    campaign_id: UUID,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT * FROM npc_profiles WHERE npc_id = $1 AND campaign_id = $2",
        npc_id, campaign_id,
    )


async def patch_npc(
    conn: asyncpg.Connection,
    npc_id: UUID,
    campaign_id: UUID,
    name: Optional[str] = None,
    role: Optional[str] = None,
    physical_description: Optional[str] = None,
    clear_physical_description: bool = False,
    personality_prompt: Optional[str] = None,
    is_active: Optional[bool] = None,
    faction_id: Optional[UUID] = None,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        UPDATE npc_profiles SET
            name                 = COALESCE($1, name),
            role                 = COALESCE($2, role),
            physical_description = CASE
                WHEN $3 = true THEN NULL
                ELSE COALESCE($4, physical_description)
            END,
            personality_prompt   = COALESCE($5, personality_prompt),
            is_active            = COALESCE($6, is_active),
            faction_id           = COALESCE($7, faction_id),
            updated_at           = now()
        WHERE npc_id = $8 AND campaign_id = $9
        RETURNING *
        """,
        name, role, clear_physical_description, physical_description,
        personality_prompt, is_active, faction_id, npc_id, campaign_id,
    )


async def deactivate_npc(
    conn: asyncpg.Connection,
    npc_id: UUID,
    campaign_id: UUID,
) -> bool:
    result = await conn.execute(
        """
        UPDATE npc_profiles SET is_active = false, updated_at = now()
        WHERE npc_id = $1 AND campaign_id = $2 AND is_active = true
        """,
        npc_id, campaign_id,
    )
    return result == "UPDATE 1"


# ── Secret functions ──────────────────────────────────────────────────────────

async def create_secret(
    conn: asyncpg.Connection,
    npc_id: UUID,
    campaign_id: UUID,
    content: str,
    condition_type: str,
    condition_value: Optional[int],
    condition_quest_title: Optional[str],
    condition_quest_status: Optional[str],
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        INSERT INTO npc_secrets
            (npc_id, campaign_id, content, condition_type,
             condition_value, condition_quest_title, condition_quest_status)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING *
        """,
        npc_id, campaign_id, content, condition_type,
        condition_value, condition_quest_title, condition_quest_status,
    )


async def list_secrets(
    conn: asyncpg.Connection,
    npc_id: UUID,
    campaign_id: UUID,
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT * FROM npc_secrets
        WHERE npc_id = $1 AND campaign_id = $2
        ORDER BY secret_id
        """,
        npc_id, campaign_id,
    )


async def patch_secret(
    conn: asyncpg.Connection,
    secret_id: UUID,
    npc_id: UUID,
    campaign_id: UUID,
    content: Optional[str] = None,
    condition_type: Optional[str] = None,
    condition_value: Optional[int] = None,
    condition_quest_title: Optional[str] = None,
    condition_quest_status: Optional[str] = None,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        UPDATE npc_secrets SET
            content                = COALESCE($1, content),
            condition_type         = COALESCE($2, condition_type),
            condition_value        = COALESCE($3, condition_value),
            condition_quest_title  = COALESCE($4, condition_quest_title),
            condition_quest_status = COALESCE($5, condition_quest_status)
        WHERE secret_id = $6 AND npc_id = $7 AND campaign_id = $8
        RETURNING *
        """,
        content, condition_type, condition_value,
        condition_quest_title, condition_quest_status,
        secret_id, npc_id, campaign_id,
    )


async def delete_secret(
    conn: asyncpg.Connection,
    secret_id: UUID,
    npc_id: UUID,
    campaign_id: UUID,
) -> bool:
    result = await conn.execute(
        "DELETE FROM npc_secrets WHERE secret_id = $1 AND npc_id = $2 AND campaign_id = $3",
        secret_id, npc_id, campaign_id,
    )
    return result == "DELETE 1"


async def mark_secret_revealed(
    conn: asyncpg.Connection,
    secret_id: UUID,
    npc_id: UUID,
    campaign_id: UUID,
) -> bool:
    """Set revealed_at = now() only if not already set. Idempotent."""
    result = await conn.execute(
        """
        UPDATE npc_secrets SET revealed_at = now()
        WHERE secret_id = $1 AND npc_id = $2 AND campaign_id = $3
          AND revealed_at IS NULL
        """,
        secret_id, npc_id, campaign_id,
    )
    return result == "UPDATE 1"
