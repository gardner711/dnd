"""PostgreSQL data access for the World State Service — five state domains.

All queries are campaign-scoped. The fetch-modify-write pattern is used
for character, NPC, world flags, and factions. Encounter state uses an
atomic UPDATE with an optimistic-concurrency version check (updated_at).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from app.config import settings
from app.models import (
    AbilityScores, ActiveEffect, CharacterCreate, CharacterState,
    CombatantState, CurrencyPurse, DeathSaves, DispositionRecord,
    DispositionsResponse, EncounterCreate, EncounterState, FactionStandingRecord,
    InitiativeEntry, Position, SpellSlots,
)

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

# ── Schema ─────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS character_state (
    character_id             UUID NOT NULL,
    campaign_id              UUID NOT NULL,
    user_id                  UUID NOT NULL,
    name                     TEXT NOT NULL,
    class_name               TEXT NOT NULL DEFAULT '',
    level                    INT  NOT NULL DEFAULT 1,
    xp                       INT  NOT NULL DEFAULT 0,
    current_hp               INT  NOT NULL DEFAULT 0,
    max_hp                   INT  NOT NULL DEFAULT 1,
    temp_hp                  INT  NOT NULL DEFAULT 0,
    armor_class              INT  NOT NULL DEFAULT 10,
    speed                    INT  NOT NULL DEFAULT 30,
    ability_scores           JSONB NOT NULL DEFAULT '{}',
    conditions               TEXT[] NOT NULL DEFAULT '{}',
    exhaustion_level         INT  NOT NULL DEFAULT 0,
    spell_slots              JSONB NOT NULL DEFAULT '{}',
    concentration            TEXT,
    death_saves              JSONB NOT NULL DEFAULT '{"successes":0,"failures":0}',
    position                 JSONB,
    inventory                JSONB NOT NULL DEFAULT '[]',
    currency                 JSONB NOT NULL DEFAULT '{"cp":0,"sp":0,"ep":0,"gp":0,"pp":0}',
    active_effects           JSONB NOT NULL DEFAULT '[]',
    proficiency_bonus        INT  NOT NULL DEFAULT 2,
    proficient_skills        TEXT[] NOT NULL DEFAULT '{}',
    proficient_saving_throws TEXT[] NOT NULL DEFAULT '{}',
    expertise_skills         TEXT[] NOT NULL DEFAULT '{}',
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (character_id, campaign_id)
);

CREATE INDEX IF NOT EXISTS idx_char_state_campaign ON character_state (campaign_id);

CREATE TABLE IF NOT EXISTS npc_disposition (
    npc_id       UUID NOT NULL,
    campaign_id  UUID NOT NULL,
    character_id UUID NOT NULL,
    score        INT  NOT NULL DEFAULT 50 CHECK (score BETWEEN 0 AND 100),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (npc_id, campaign_id, character_id)
);

CREATE TABLE IF NOT EXISTS world_flags (
    campaign_id UUID NOT NULL,
    key         TEXT NOT NULL,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (campaign_id, key)
);

CREATE TABLE IF NOT EXISTS encounter_state (
    encounter_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id        UUID NOT NULL UNIQUE,
    map_id             UUID,
    round              INT  NOT NULL DEFAULT 1,
    current_turn_index INT  NOT NULL DEFAULT 0,
    initiative_order   JSONB NOT NULL DEFAULT '[]',
    combatant_states   JSONB NOT NULL DEFAULT '{}',
    active             BOOL NOT NULL DEFAULT TRUE,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS faction_standing (
    campaign_id UUID NOT NULL,
    faction_id  TEXT NOT NULL,
    standing    INT  NOT NULL DEFAULT 0 CHECK (standing BETWEEN -100 AND 100),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (campaign_id, faction_id)
);
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


async def run_migrations(conn: asyncpg.Connection) -> None:
    await conn.execute(SCHEMA_SQL)
    logger.info("World State Service migrations applied")


# ── Character state ─────────────────────────────────────────────────────────

_GET_CHAR = "SELECT * FROM character_state WHERE character_id=$1::uuid AND campaign_id=$2::uuid"

# Locks the row for the duration of the caller's transaction — prevents concurrent HP loss
_GET_CHAR_FOR_UPDATE = "SELECT * FROM character_state WHERE character_id=$1::uuid AND campaign_id=$2::uuid FOR UPDATE"

_LIST_CHARS = "SELECT * FROM character_state WHERE campaign_id=$1::uuid ORDER BY name"

_UPSERT_CHAR = """
INSERT INTO character_state (
    character_id, campaign_id, user_id, name, class_name, level, xp,
    current_hp, max_hp, temp_hp, armor_class, speed,
    ability_scores, conditions, exhaustion_level, spell_slots, concentration,
    death_saves, position, inventory, currency, active_effects,
    proficiency_bonus, proficient_skills, proficient_saving_throws, expertise_skills,
    updated_at
) VALUES (
    $1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7,
    $8, $9, $10, $11, $12,
    $13::jsonb, $14, $15, $16::jsonb, $17,
    $18::jsonb, $19::jsonb, $20::jsonb, $21::jsonb, $22::jsonb,
    $23, $24, $25, $26,
    NOW()
)
ON CONFLICT (character_id, campaign_id) DO UPDATE SET
    user_id=$3::uuid, name=$4, class_name=$5, level=$6, xp=$7,
    current_hp=$8, max_hp=$9, temp_hp=$10, armor_class=$11, speed=$12,
    ability_scores=$13::jsonb, conditions=$14, exhaustion_level=$15,
    spell_slots=$16::jsonb, concentration=$17, death_saves=$18::jsonb,
    position=$19::jsonb, inventory=$20::jsonb, currency=$21::jsonb,
    active_effects=$22::jsonb, proficiency_bonus=$23,
    proficient_skills=$24, proficient_saving_throws=$25, expertise_skills=$26,
    updated_at=NOW()
RETURNING *
"""

_DELETE_CHAR = "DELETE FROM character_state WHERE character_id=$1::uuid AND campaign_id=$2::uuid RETURNING character_id"


async def get_character(conn: asyncpg.Connection, character_id: UUID, campaign_id: UUID) -> CharacterState | None:
    row = await conn.fetchrow(_GET_CHAR, str(character_id), str(campaign_id))
    return _row_to_character(row) if row else None


async def get_character_for_update(conn: asyncpg.Connection, character_id: UUID, campaign_id: UUID) -> CharacterState | None:
    """Fetch character state and lock the row. Must be called inside a transaction."""
    row = await conn.fetchrow(_GET_CHAR_FOR_UPDATE, str(character_id), str(campaign_id))
    return _row_to_character(row) if row else None


async def list_characters(conn: asyncpg.Connection, campaign_id: UUID) -> list[CharacterState]:
    """Return all character state records for a campaign, ordered by name."""
    rows = await conn.fetch(_LIST_CHARS, str(campaign_id))
    return [_row_to_character(row) for row in rows]


async def upsert_character(conn: asyncpg.Connection, char_id: UUID, body: CharacterCreate) -> CharacterState:
    row = await conn.fetchrow(
        _UPSERT_CHAR,
        str(char_id), str(body.campaign_id), str(body.user_id),
        body.name, body.class_name, body.level, body.xp,
        body.current_hp, body.max_hp, body.temp_hp, body.armor_class, body.speed,
        json.dumps(body.ability_scores.model_dump()),
        list(body.conditions),
        body.exhaustion_level,
        json.dumps(body.spell_slots.model_dump()),
        body.concentration,
        json.dumps(body.death_saves.model_dump()),
        json.dumps(body.position.model_dump()) if body.position else None,
        json.dumps(body.inventory),
        json.dumps(body.currency.model_dump()),
        json.dumps([e.model_dump() for e in body.active_effects]),
        body.proficiency_bonus,
        list(body.proficient_skills),
        list(body.proficient_saving_throws),
        list(body.expertise_skills),
    )
    return _row_to_character(row)


async def delete_character(conn: asyncpg.Connection, character_id: UUID, campaign_id: UUID) -> bool:
    row = await conn.fetchrow(_DELETE_CHAR, str(character_id), str(campaign_id))
    return row is not None


# ── NPC disposition ─────────────────────────────────────────────────────────

_GET_DISPOSITIONS = "SELECT * FROM npc_disposition WHERE npc_id=$1::uuid AND campaign_id=$2::uuid"

_UPSERT_DISPOSITION = """
INSERT INTO npc_disposition (npc_id, campaign_id, character_id, score, updated_at)
VALUES ($1::uuid, $2::uuid, $3::uuid, $4, NOW())
ON CONFLICT (npc_id, campaign_id, character_id) DO UPDATE SET score=EXCLUDED.score, updated_at=NOW()
RETURNING *
"""


async def get_npc_dispositions(conn: asyncpg.Connection, npc_id: UUID, campaign_id: UUID) -> DispositionsResponse:
    rows = await conn.fetch(_GET_DISPOSITIONS, str(npc_id), str(campaign_id))
    return DispositionsResponse(
        npc_id=npc_id, campaign_id=campaign_id,
        dispositions=[_row_to_disposition(r) for r in rows],
    )


async def upsert_npc_disposition(
    conn: asyncpg.Connection, npc_id: UUID, campaign_id: UUID, character_id: UUID, score: int
) -> DispositionRecord:
    row = await conn.fetchrow(_UPSERT_DISPOSITION, str(npc_id), str(campaign_id), str(character_id), score)
    return _row_to_disposition(row)


# ── World flags ──────────────────────────────────────────────────────────────

_GET_FLAGS   = "SELECT key, value FROM world_flags WHERE campaign_id=$1::uuid"
_GET_FLAG    = "SELECT value FROM world_flags WHERE campaign_id=$1::uuid AND key=$2"
_UPSERT_FLAG = """
INSERT INTO world_flags (campaign_id, key, value, updated_at)
VALUES ($1::uuid, $2, $3::jsonb, NOW())
ON CONFLICT (campaign_id, key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()
"""
_DELETE_FLAG = "DELETE FROM world_flags WHERE campaign_id=$1::uuid AND key=$2 RETURNING key"


async def get_world_flags(conn: asyncpg.Connection, campaign_id: UUID) -> dict[str, Any]:
    rows = await conn.fetch(_GET_FLAGS, str(campaign_id))
    return {row["key"]: row["value"] for row in rows}


async def get_world_flag(conn: asyncpg.Connection, campaign_id: UUID, key: str) -> Any | None:
    """Fetch a single flag value, or None if it doesn't exist."""
    row = await conn.fetchrow(_GET_FLAG, str(campaign_id), key)
    return row["value"] if row else None


async def upsert_world_flags(conn: asyncpg.Connection, campaign_id: UUID, flags: dict[str, Any]) -> dict[str, Any]:
    for key, value in flags.items():
        await conn.execute(_UPSERT_FLAG, str(campaign_id), key, json.dumps(value))
    return await get_world_flags(conn, campaign_id)


async def delete_world_flag(conn: asyncpg.Connection, campaign_id: UUID, key: str) -> bool:
    row = await conn.fetchrow(_DELETE_FLAG, str(campaign_id), key)
    return row is not None


# ── Encounter state ──────────────────────────────────────────────────────────

_GET_ENCOUNTER = "SELECT * FROM encounter_state WHERE campaign_id=$1::uuid"

_INSERT_ENCOUNTER = """
INSERT INTO encounter_state (campaign_id, map_id, round, current_turn_index, initiative_order, combatant_states)
VALUES ($1::uuid, $2::uuid, 1, 0, $3::jsonb, $4::jsonb)
RETURNING *
"""

_UPDATE_ENCOUNTER = """
UPDATE encounter_state
SET round              = COALESCE($3, round),
    current_turn_index = COALESCE($4, current_turn_index),
    combatant_states   = CASE WHEN $5::jsonb IS NULL
                              THEN combatant_states
                              ELSE combatant_states || $5::jsonb
                         END,
    updated_at         = NOW()
WHERE campaign_id = $1::uuid AND updated_at = $2
RETURNING *
"""

_DELETE_ENCOUNTER = "DELETE FROM encounter_state WHERE campaign_id=$1::uuid RETURNING encounter_id"


async def get_encounter(conn: asyncpg.Connection, campaign_id: UUID) -> EncounterState | None:
    row = await conn.fetchrow(_GET_ENCOUNTER, str(campaign_id))
    return _row_to_encounter(row) if row else None


async def create_encounter(conn: asyncpg.Connection, body: EncounterCreate) -> EncounterState:
    row = await conn.fetchrow(
        _INSERT_ENCOUNTER,
        str(body.campaign_id),
        str(body.map_id) if body.map_id else None,
        json.dumps([e.model_dump() for e in body.initiative_order], default=str),
        json.dumps({k: v.model_dump() for k, v in body.combatant_states.items()}, default=str),
    )
    return _row_to_encounter(row)


async def update_encounter(
    conn: asyncpg.Connection,
    campaign_id: UUID,
    expected_updated_at: datetime,
    round_: int | None,
    turn_index: int | None,
    combatant_states: dict | None,
) -> EncounterState | None:
    """Returns None if the row was concurrently modified (optimistic concurrency failure)."""
    combatant_json = json.dumps(combatant_states, default=str) if combatant_states is not None else None
    row = await conn.fetchrow(
        _UPDATE_ENCOUNTER,
        str(campaign_id), expected_updated_at, round_, turn_index, combatant_json,
    )
    return _row_to_encounter(row) if row else None


async def delete_encounter(conn: asyncpg.Connection, campaign_id: UUID) -> bool:
    row = await conn.fetchrow(_DELETE_ENCOUNTER, str(campaign_id))
    return row is not None


# ── Faction standing ──────────────────────────────────────────────────────────

_GET_FACTIONS   = "SELECT * FROM faction_standing WHERE campaign_id=$1::uuid ORDER BY faction_id"
_UPSERT_FACTION = """
INSERT INTO faction_standing (campaign_id, faction_id, standing, updated_at)
VALUES ($1::uuid, $2, $3, NOW())
ON CONFLICT (campaign_id, faction_id) DO UPDATE SET standing=EXCLUDED.standing, updated_at=NOW()
RETURNING *
"""


async def get_factions(conn: asyncpg.Connection, campaign_id: UUID) -> list[FactionStandingRecord]:
    rows = await conn.fetch(_GET_FACTIONS, str(campaign_id))
    return [_row_to_faction(r) for r in rows]


async def upsert_faction(
    conn: asyncpg.Connection, campaign_id: UUID, faction_id: str, standing: int
) -> FactionStandingRecord:
    row = await conn.fetchrow(_UPSERT_FACTION, str(campaign_id), faction_id, standing)
    return _row_to_faction(row)


# ── Row converters ────────────────────────────────────────────────────────────

def _row_to_character(row) -> CharacterState:
    d = dict(row)
    return CharacterState(
        character_id=str(d["character_id"]),
        campaign_id=str(d["campaign_id"]),
        user_id=str(d["user_id"]),
        name=d["name"],
        class_name=d["class_name"],
        level=d["level"],
        xp=d["xp"],
        current_hp=d["current_hp"],
        max_hp=d["max_hp"],
        temp_hp=d["temp_hp"],
        armor_class=d["armor_class"],
        speed=d["speed"],
        ability_scores=AbilityScores(**(d["ability_scores"] or {})),
        conditions=list(d["conditions"] or []),
        exhaustion_level=d["exhaustion_level"],
        spell_slots=SpellSlots(**(d["spell_slots"] or {})),
        concentration=d.get("concentration"),
        death_saves=DeathSaves(**(d["death_saves"] or {})),
        position=Position(**d["position"]) if d.get("position") else None,
        inventory=list(d["inventory"] or []),
        currency=CurrencyPurse(**(d["currency"] or {})),
        active_effects=[ActiveEffect(**e) for e in (d["active_effects"] or [])],
        proficiency_bonus=d["proficiency_bonus"],
        proficient_skills=list(d["proficient_skills"] or []),
        proficient_saving_throws=list(d["proficient_saving_throws"] or []),
        expertise_skills=list(d["expertise_skills"] or []),
        updated_at=d["updated_at"],
    )


def _row_to_disposition(row) -> DispositionRecord:
    d = dict(row)
    return DispositionRecord(
        npc_id=str(d["npc_id"]),
        campaign_id=str(d["campaign_id"]),
        character_id=str(d["character_id"]),
        score=d["score"],
        updated_at=d["updated_at"],
    )


def _row_to_encounter(row) -> EncounterState:
    d = dict(row)
    initiative = [InitiativeEntry(**e) for e in (d["initiative_order"] or [])]
    combatants = {k: CombatantState(**v) for k, v in (d["combatant_states"] or {}).items()}
    return EncounterState(
        encounter_id=str(d["encounter_id"]),
        campaign_id=str(d["campaign_id"]),
        map_id=str(d["map_id"]) if d.get("map_id") else None,
        round=d["round"],
        current_turn_index=d["current_turn_index"],
        initiative_order=initiative,
        combatant_states=combatants,
        active=d["active"],
        started_at=d["started_at"],
        updated_at=d["updated_at"],
    )


def _row_to_faction(row) -> FactionStandingRecord:
    d = dict(row)
    return FactionStandingRecord(
        campaign_id=str(d["campaign_id"]),
        faction_id=d["faction_id"],
        standing=d["standing"],
        updated_at=d["updated_at"],
    )
