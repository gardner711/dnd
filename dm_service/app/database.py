"""PostgreSQL data access for DM Service turn ledger."""
from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

from app.config import settings
from app.models import TurnLedgerRecord

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dm_turn_ledger (
    campaign_id      UUID NOT NULL,
    session_id       UUID NOT NULL,
    turn_id          TEXT NOT NULL,
    user_id          UUID NOT NULL,
    character_id     UUID NOT NULL,
    input_text       TEXT NOT NULL,
    selected_action  TEXT NOT NULL,
    narration        TEXT NOT NULL,
    llm_model        TEXT NOT NULL,
    llm_prompt_hash  TEXT NOT NULL,
    side_effects     JSONB NOT NULL DEFAULT '[]',
    context          JSONB NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (campaign_id, session_id, turn_id)
);
CREATE INDEX IF NOT EXISTS idx_dm_turn_ledger_campaign_session_created
    ON dm_turn_ledger (campaign_id, session_id, created_at DESC);
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
    logger.info("DM Service migrations applied")


_GET_TURN = """
SELECT * FROM dm_turn_ledger
WHERE campaign_id=$1::uuid AND session_id=$2::uuid AND turn_id=$3
"""

_LIST_TURNS = """
SELECT * FROM dm_turn_ledger
WHERE campaign_id=$1::uuid
    AND session_id=$2::uuid
    AND ($3::uuid IS NULL OR user_id=$3::uuid)
    AND ($4::uuid IS NULL OR character_id=$4::uuid)
ORDER BY created_at DESC
LIMIT $5 OFFSET $6
"""

_DELETE_TURN = """
DELETE FROM dm_turn_ledger
WHERE campaign_id=$1::uuid AND session_id=$2::uuid AND turn_id=$3
RETURNING turn_id
"""

_INSERT_TURN = """
INSERT INTO dm_turn_ledger (
    campaign_id, session_id, turn_id, user_id, character_id,
    input_text, selected_action, narration, llm_model, llm_prompt_hash,
    side_effects, context, created_at, updated_at
) VALUES (
    $1::uuid, $2::uuid, $3, $4::uuid, $5::uuid,
    $6, $7, $8, $9, $10,
    $11::jsonb, $12::jsonb, NOW(), NOW()
)
ON CONFLICT (campaign_id, session_id, turn_id) DO NOTHING
RETURNING *
"""


async def get_turn(
    conn: asyncpg.Connection,
    campaign_id: str,
    session_id: str,
    turn_id: str,
) -> TurnLedgerRecord | None:
    row = await conn.fetchrow(_GET_TURN, campaign_id, session_id, turn_id)
    return _row_to_turn(row) if row else None


async def save_turn(
    conn: asyncpg.Connection,
    campaign_id: str,
    session_id: str,
    turn_id: str,
    user_id: str,
    character_id: str,
    input_text: str,
    selected_action: str,
    narration: str,
    llm_model: str,
    llm_prompt_hash: str,
    side_effects: list[dict[str, Any]],
    context: dict[str, Any],
) -> TurnLedgerRecord:
    row = await conn.fetchrow(
        _INSERT_TURN,
        campaign_id,
        session_id,
        turn_id,
        user_id,
        character_id,
        input_text,
        selected_action,
        narration,
        llm_model,
        llm_prompt_hash,
        json.dumps(side_effects),
        json.dumps(context),
    )
    if row:
        return _row_to_turn(row)
    # Another concurrent request already inserted this turn; return that result.
    existing = await get_turn(conn, campaign_id, session_id, turn_id)
    if existing is None:
        raise RuntimeError("Failed to persist or fetch DM turn ledger record")
    return existing


async def list_turns(
    conn: asyncpg.Connection,
    campaign_id: str,
    session_id: str,
    user_id: str | None,
    character_id: str | None,
    limit: int,
    offset: int,
) -> list[TurnLedgerRecord]:
    rows = await conn.fetch(_LIST_TURNS, campaign_id, session_id, user_id, character_id, limit, offset)
    return [_row_to_turn(r) for r in rows]


async def delete_turn(
    conn: asyncpg.Connection,
    campaign_id: str,
    session_id: str,
    turn_id: str,
) -> bool:
    row = await conn.fetchrow(_DELETE_TURN, campaign_id, session_id, turn_id)
    return row is not None


def _row_to_turn(row) -> TurnLedgerRecord:
    d = dict(row)
    return TurnLedgerRecord(
        campaign_id=str(d["campaign_id"]),
        session_id=str(d["session_id"]),
        turn_id=d["turn_id"],
        user_id=str(d["user_id"]),
        character_id=str(d["character_id"]),
        input_text=d["input_text"],
        selected_action=d["selected_action"],
        narration=d["narration"],
        llm_model=d["llm_model"],
        llm_prompt_hash=d["llm_prompt_hash"],
        side_effects=list(d["side_effects"] or []),
        context=d["context"] or {},
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )