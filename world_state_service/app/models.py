"""Pydantic models for the World State Service — all five state domains."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── Shared sub-models ──────────────────────────────────────────────────────

class AbilityScores(BaseModel):
    strength: int = 10
    dexterity: int = 10
    constitution: int = 10
    intelligence: int = 10
    wisdom: int = 10
    charisma: int = 10


class Position(BaseModel):
    x: int
    y: int
    map_id: UUID


class SpellSlots(BaseModel):
    """Remaining spell slots per level (1-9)."""
    level_1: int = 0
    level_2: int = 0
    level_3: int = 0
    level_4: int = 0
    level_5: int = 0
    level_6: int = 0
    level_7: int = 0
    level_8: int = 0
    level_9: int = 0


class DeathSaves(BaseModel):
    successes: int = Field(default=0, ge=0, le=3)
    failures: int = Field(default=0, ge=0, le=3)


class CurrencyPurse(BaseModel):
    cp: int = 0  # copper
    sp: int = 0  # silver
    ep: int = 0  # electrum
    gp: int = 0  # gold
    pp: int = 0  # platinum


class ActiveEffect(BaseModel):
    name: str
    duration_rounds: Optional[int] = None  # None = until dispelled
    source: str = ""


class EventMeta(BaseModel):
    """Optional event log context. When provided, state changes emit audit events."""
    session_id: str
    user_id: str


# ── Character state ────────────────────────────────────────────────────────

class CharacterState(BaseModel):
    """Full runtime character state — returned by all character endpoints."""
    character_id: UUID
    campaign_id: UUID
    user_id: UUID
    name: str
    class_name: str = ""
    level: int = Field(default=1, ge=1, le=20)
    xp: int = Field(default=0, ge=0)
    current_hp: int = 0
    max_hp: int = Field(default=1, ge=1)
    temp_hp: int = Field(default=0, ge=0)
    armor_class: int = Field(default=10, ge=0)
    speed: int = Field(default=30, ge=0)
    ability_scores: AbilityScores = Field(default_factory=AbilityScores)
    conditions: list[str] = Field(default_factory=list)
    exhaustion_level: int = Field(default=0, ge=0, le=6)
    spell_slots: SpellSlots = Field(default_factory=SpellSlots)
    concentration: Optional[str] = None
    death_saves: DeathSaves = Field(default_factory=DeathSaves)
    position: Optional[Position] = None
    inventory: list[dict] = Field(default_factory=list)
    currency: CurrencyPurse = Field(default_factory=CurrencyPurse)
    active_effects: list[ActiveEffect] = Field(default_factory=list)
    proficiency_bonus: int = Field(default=2, ge=0)
    proficient_skills: list[str] = Field(default_factory=list)
    proficient_saving_throws: list[str] = Field(default_factory=list)
    expertise_skills: list[str] = Field(default_factory=list)
    updated_at: datetime


class CharacterCreate(BaseModel):
    """Input model for PUT /characters/{id} — creates or fully replaces runtime state."""
    campaign_id: UUID
    user_id: UUID
    name: str
    class_name: str = ""
    level: int = Field(default=1, ge=1, le=20)
    xp: int = 0
    current_hp: int
    max_hp: int = Field(ge=1)
    temp_hp: int = 0
    armor_class: int = 10
    speed: int = 30
    ability_scores: AbilityScores = Field(default_factory=AbilityScores)
    conditions: list[str] = Field(default_factory=list)
    exhaustion_level: int = Field(default=0, ge=0, le=6)
    spell_slots: SpellSlots = Field(default_factory=SpellSlots)
    concentration: Optional[str] = None
    death_saves: DeathSaves = Field(default_factory=DeathSaves)
    position: Optional[Position] = None
    inventory: list[dict] = Field(default_factory=list)
    currency: CurrencyPurse = Field(default_factory=CurrencyPurse)
    active_effects: list[ActiveEffect] = Field(default_factory=list)
    proficiency_bonus: int = 2
    proficient_skills: list[str] = Field(default_factory=list)
    proficient_saving_throws: list[str] = Field(default_factory=list)
    expertise_skills: list[str] = Field(default_factory=list)


class CharacterUpdate(BaseModel):
    """Partial update — only supplied fields are changed.
    Set concentration to '' (empty string) to clear it.
    """
    current_hp: Optional[int] = None
    max_hp: Optional[int] = Field(default=None, ge=1)
    temp_hp: Optional[int] = Field(default=None, ge=0)
    armor_class: Optional[int] = None
    speed: Optional[int] = Field(default=None, ge=0)
    conditions: Optional[list[str]] = None
    exhaustion_level: Optional[int] = Field(default=None, ge=0, le=6)
    spell_slots: Optional[SpellSlots] = None
    concentration: Optional[str] = None   # null = clear; string = set
    death_saves: Optional[DeathSaves] = None
    position: Optional[Position] = None
    inventory: Optional[list[dict]] = None
    currency: Optional[CurrencyPurse] = None
    active_effects: Optional[list[ActiveEffect]] = None
    xp: Optional[int] = Field(default=None, ge=0)
    level: Optional[int] = Field(default=None, ge=1, le=20)
    proficiency_bonus: Optional[int] = None
    ability_scores: Optional[AbilityScores] = None
    proficient_skills: Optional[list[str]] = None
    proficient_saving_throws: Optional[list[str]] = None
    expertise_skills: Optional[list[str]] = None
    expected_updated_at: Optional[datetime] = None  # when set, PATCH uses optimistic concurrency
    event_meta: Optional[EventMeta] = None


# ── NPC disposition ────────────────────────────────────────────────────────

class DispositionRecord(BaseModel):
    npc_id: UUID
    campaign_id: UUID
    character_id: UUID
    score: int = Field(ge=0, le=100)
    updated_at: datetime


class DispositionsResponse(BaseModel):
    npc_id: UUID
    campaign_id: UUID
    dispositions: list[DispositionRecord]


class DispositionUpdate(BaseModel):
    character_id: UUID
    score: int = Field(ge=0, le=100)
    reason: str = ""
    event_meta: Optional[EventMeta] = None


# ── World flags ────────────────────────────────────────────────────────────

class WorldFlagsResponse(BaseModel):
    campaign_id: UUID
    flags: dict[str, Any]


class WorldFlagsUpdate(BaseModel):
    """Upsert any number of flags in one call. Values may be bool, str, int, or dict."""
    flags: dict[str, Any]
    event_meta: Optional[EventMeta] = None


# ── Encounter ──────────────────────────────────────────────────────────────

class CombatantState(BaseModel):
    combatant_id: UUID
    name: str
    is_player: bool
    current_hp: int
    max_hp: int
    conditions: list[str] = Field(default_factory=list)
    position: Optional[Position] = None


class InitiativeEntry(BaseModel):
    combatant_id: UUID
    name: str
    total: int
    is_player: bool


class EncounterState(BaseModel):
    encounter_id: UUID
    campaign_id: UUID
    map_id: Optional[UUID]
    round: int
    current_turn_index: int
    initiative_order: list[InitiativeEntry]
    combatant_states: dict[str, CombatantState]  # str(combatant_id) → state
    active: bool
    started_at: datetime
    updated_at: datetime


class EncounterCreate(BaseModel):
    campaign_id: UUID
    map_id: Optional[UUID] = None
    initiative_order: list[InitiativeEntry]
    combatant_states: dict[str, CombatantState]
    event_meta: Optional[EventMeta] = None


class EncounterUpdate(BaseModel):
    """Partial update with mandatory optimistic-concurrency version check."""
    round: Optional[int] = Field(default=None, ge=1)
    current_turn_index: Optional[int] = Field(default=None, ge=0)
    combatant_states: Optional[dict[str, CombatantState]] = None
    expected_updated_at: datetime  # request is rejected with 409 if row has since changed
    event_meta: Optional[EventMeta] = None


# ── Factions ───────────────────────────────────────────────────────────────

class FactionStandingRecord(BaseModel):
    campaign_id: UUID
    faction_id: str
    standing: int = Field(ge=-100, le=100)
    updated_at: datetime


class FactionsResponse(BaseModel):
    campaign_id: UUID
    factions: list[FactionStandingRecord]


class FactionUpdate(BaseModel):
    standing: int = Field(ge=-100, le=100)
