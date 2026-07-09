"""Pydantic models for the Combat Engine service."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── Sub-models (mirror Rules Engine structures for cross-service calls) ────────

class AbilityScores(BaseModel):
    strength: int = 10
    dexterity: int = 10
    constitution: int = 10
    intelligence: int = 10
    wisdom: int = 10
    charisma: int = 10


class CombatantStats(BaseModel):
    """Combat statistics for a single combatant — passed through to the Rules Engine.

    ``id`` must equal ``str(combatant_id)`` of the entity in the encounter.
    """
    id: str
    name: str
    ability_scores: AbilityScores = Field(default_factory=AbilityScores)
    proficiency_bonus: int = 2
    armor_class: int = 10
    max_hp: int = 10
    current_hp: int = 10
    speed: int = 30
    conditions: list[str] = Field(default_factory=list)
    proficient_skills: list[str] = Field(default_factory=list)
    proficient_saving_throws: list[str] = Field(default_factory=list)
    expertise_skills: list[str] = Field(default_factory=list)
    exhaustion_level: int = Field(default=0, ge=0, le=6)
    is_proficient_with_weapon: bool = True


class WeaponDefinition(BaseModel):
    name: str
    damage_dice: str         # e.g. "1d8", "2d6"
    damage_type: str         # e.g. "slashing", "piercing"
    ability_score: str = "strength"
    finesse: bool = False
    ranged: bool = False
    magical: bool = False
    attack_bonus: int = 0    # magic weapon bonus
    damage_bonus: int = 0


class TargetDefenses(BaseModel):
    damage_resistances: list[str] = Field(default_factory=list)
    damage_immunities: list[str] = Field(default_factory=list)
    damage_vulnerabilities: list[str] = Field(default_factory=list)


class CombatCapabilities(BaseModel):
    attacks_per_action: int = Field(default=1, ge=1)
    can_attack_as_bonus_action: bool = False
    can_dash_as_bonus_action: bool = False
    can_disengage_as_bonus_action: bool = False
    can_dodge_as_bonus_action: bool = False
    can_help_as_bonus_action: bool = False
    can_hide_as_bonus_action: bool = False
    can_ready_as_bonus_action: bool = False
    can_opportunity_attack: bool = True


class TurnState(BaseModel):
    movement_spent: int = Field(default=0, ge=0)
    extra_movement_budget: int = Field(default=0, ge=0)
    action_available: bool = True
    bonus_action_available: bool = True
    reaction_available: bool = True
    attacks_used_this_action: int = Field(default=0, ge=0)
    disengage_active: bool = False
    dodge_active: bool = False
    hidden: bool = False
    help_target_id: Optional[UUID] = None
    help_type: Optional[str] = None
    ready_trigger: Optional[str] = None
    ready_action: Optional[str] = None


# ── Encounter state (mirrors World State EncounterState for GET /combat) ──────

class CombatantStateOut(BaseModel):
    combatant_id: UUID
    name: str
    is_player: bool
    current_hp: int
    max_hp: int
    temp_hp: int = Field(default=0, ge=0)
    armor_class: int = 10
    speed: int = 30
    ability_scores: AbilityScores = Field(default_factory=AbilityScores)
    proficiency_bonus: int = 2
    conditions: list[str] = Field(default_factory=list)
    proficient_skills: list[str] = Field(default_factory=list)
    proficient_saving_throws: list[str] = Field(default_factory=list)
    expertise_skills: list[str] = Field(default_factory=list)
    exhaustion_level: int = Field(default=0, ge=0, le=6)
    is_proficient_with_weapon: bool = True
    concentration: Optional[str] = None
    spell_slots: dict[str, int] = Field(default_factory=dict)
    death_saves: dict[str, int] = Field(default_factory=dict)
    damage_resistances: list[str] = Field(default_factory=list)
    damage_immunities: list[str] = Field(default_factory=list)
    damage_vulnerabilities: list[str] = Field(default_factory=list)
    combat_capabilities: CombatCapabilities = Field(default_factory=CombatCapabilities)
    turn_state: TurnState = Field(default_factory=TurnState)
    position: Optional[dict[str, Any]] = None


class InitiativeEntryOut(BaseModel):
    combatant_id: UUID
    name: str
    total: int
    is_player: bool


class EncounterOut(BaseModel):
    """Proxied encounter state returned by GET /combat."""
    encounter_id: UUID
    campaign_id: UUID
    map_id: Optional[UUID] = None
    round: int
    current_turn_index: int
    initiative_order: list[InitiativeEntryOut]
    combatant_states: dict[str, CombatantStateOut]
    active: bool
    started_at: datetime
    updated_at: datetime


# ── Start combat ──────────────────────────────────────────────────────────────

class CombatantEntry(BaseModel):
    """One participant being added to a new encounter."""
    combatant_id: UUID
    name: str
    is_player: bool
    current_hp: int
    max_hp: int
    temp_hp: int = Field(default=0, ge=0)
    armor_class: int = 10
    speed: int = 30
    ability_scores: AbilityScores = Field(default_factory=AbilityScores)
    proficiency_bonus: int = 2
    conditions: list[str] = Field(default_factory=list)
    proficient_skills: list[str] = Field(default_factory=list)
    proficient_saving_throws: list[str] = Field(default_factory=list)
    expertise_skills: list[str] = Field(default_factory=list)
    exhaustion_level: int = Field(default=0, ge=0, le=6)
    is_proficient_with_weapon: bool = True
    concentration: Optional[str] = None
    spell_slots: dict[str, int] = Field(default_factory=dict)
    death_saves: dict[str, int] = Field(default_factory=lambda: {"successes": 0, "failures": 0})
    target_defenses: TargetDefenses = Field(default_factory=TargetDefenses)
    combat_capabilities: CombatCapabilities = Field(default_factory=CombatCapabilities)
    position: Optional[dict[str, Any]] = None  # {x, y, map_id}


class StartCombatRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    map_id: Optional[UUID] = None
    combatants: list[CombatantEntry] = Field(min_length=1)


class InitiativeEntryResult(BaseModel):
    combatant_id: UUID
    name: str
    total: int
    is_player: bool


class StartCombatResponse(BaseModel):
    encounter_id: UUID
    campaign_id: UUID
    initiative_order: list[InitiativeEntryResult]
    current_turn_combatant_id: UUID
    round: int = 1


# ── Attack action ─────────────────────────────────────────────────────────────

class TargetInfo(BaseModel):
    """Current state of the attack target — provided by the caller from their cached encounter state."""
    combatant_id: UUID
    name: str
    is_player: bool
    current_hp: int
    max_hp: int
    armor_class: int = 10
    conditions: list[str] = Field(default_factory=list)
    position: Optional[dict[str, Any]] = None


class AttackActionRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    attacker_id: UUID
    target_id: UUID
    weapon: WeaponDefinition
    action_cost: Literal["action", "bonus_action", "reaction", "none"] = "action"
    cover_bonus: Literal[0, 2, 5] = 0
    adjacent_to_hostile_creature: bool = False
    extra_damage_dice: list[str] = Field(default_factory=list)
    expected_updated_at: datetime     # optimistic lock on the encounter state


class AttackActionResponse(BaseModel):
    hit: bool
    critical_hit: bool
    damage_total: Optional[int] = None    # None on miss
    damage_modifier: str = "none"         # none / resistance / immunity / vulnerability
    effective_ac: int
    target_new_hp: int
    target_is_unconscious: bool           # new HP <= 0


# ── Next turn ─────────────────────────────────────────────────────────────────

class NextTurnRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID


class NextTurnResponse(BaseModel):
    round: int
    current_turn_index: int
    current_turn_combatant_id: UUID
    current_turn_combatant_name: str


# ── Conditions ────────────────────────────────────────────────────────────────

class ApplyConditionRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    combatant_id: UUID
    is_player: bool
    condition: str


class RemoveConditionRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    combatant_id: UUID
    is_player: bool
    condition: str


class ConditionResponse(BaseModel):
    combatant_id: UUID
    conditions: list[str]


# ── Movement ──────────────────────────────────────────────────────────────────

class MoveActionRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    combatant_id: UUID
    distance_feet: int = Field(ge=1)
    difficult_terrain: bool = False
    standing_from_prone: bool = False    # paying the cost to stand from prone this turn
    new_position: Optional[dict[str, Any]] = None  # {x, y, map_id} — None = no map tracking


class MoveActionResponse(BaseModel):
    valid: bool
    effective_speed: int
    movement_cost: int
    rejection_reason: Optional[str] = None


# ── Death saving throw ────────────────────────────────────────────────────────

class DeathSaveActionRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    combatant_id: UUID    # must be a player character


class DeathSaveActionResponse(BaseModel):
    success: bool
    critical_stabilize: bool   # natural 20 → regain 1 HP
    critical_failure: bool     # natural 1 → two failures
    new_successes: int
    new_failures: int
    stabilized: bool
    dead: bool


class GrappleActionRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    attacker_id: UUID
    target_id: UUID
    action_cost: Literal["action", "bonus_action", "reaction", "none"] = "action"
    defender_uses_acrobatics: bool = False
    expected_updated_at: datetime


class GrappleActionResponse(BaseModel):
    grapple_succeeds: bool
    target_conditions: list[str]


class ShoveActionRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    attacker_id: UUID
    target_id: UUID
    action_cost: Literal["action", "bonus_action", "reaction", "none"] = "action"
    shove_type: Literal["knock_prone", "push_away"] = "knock_prone"
    defender_uses_acrobatics: bool = False
    expected_updated_at: datetime
    new_position: Optional[dict[str, Any]] = None


class ShoveActionResponse(BaseModel):
    shove_succeeds: bool
    shove_type: str
    target_conditions: list[str]
    target_position: Optional[dict[str, Any]] = None


class SpellCastActionRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    caster_id: UUID
    spell_name: str
    spell_level: int = Field(ge=0, le=9)
    action_cost: Literal["action", "bonus_action", "reaction", "none"] = "action"
    is_concentration: bool = False
    requires_verbal: bool = True
    requires_somatic: bool = True
    expected_updated_at: datetime


class SpellCastActionResponse(BaseModel):
    valid: bool
    rejection_reason: Optional[str] = None
    breaks_concentration: bool = False
    slot_consumed: Optional[int] = None
    concentration: Optional[str] = None


class DashActionRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    combatant_id: UUID
    action_cost: Literal["action", "bonus_action"] = "action"
    expected_updated_at: datetime


class DashActionResponse(BaseModel):
    movement_budget: int
    extra_movement_budget: int


class DisengageActionRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    combatant_id: UUID
    action_cost: Literal["action", "bonus_action"] = "action"
    expected_updated_at: datetime


class DisengageActionResponse(BaseModel):
    disengage_active: bool


class DodgeActionRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    combatant_id: UUID
    action_cost: Literal["action", "bonus_action"] = "action"
    expected_updated_at: datetime


class DodgeActionResponse(BaseModel):
    dodge_active: bool


class HelpActionRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    combatant_id: UUID
    target_id: UUID
    help_type: Literal["attack", "ability_check"] = "attack"
    action_cost: Literal["action", "bonus_action"] = "action"
    expected_updated_at: datetime


class HelpActionResponse(BaseModel):
    help_target_id: UUID
    help_type: str


class HideActionRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    combatant_id: UUID
    action_cost: Literal["action", "bonus_action"] = "action"
    dc: int = Field(default=10, ge=1)
    advantage_state: Literal["normal", "advantage", "disadvantage"] = "normal"
    expected_updated_at: datetime


class HideActionResponse(BaseModel):
    hidden: bool
    total: int
    dc: int
    success: bool


class ReadyActionRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    combatant_id: UUID
    trigger: str = Field(min_length=1)
    action_description: str = Field(min_length=1)
    action_cost: Literal["action", "bonus_action"] = "action"
    expected_updated_at: datetime


class ReadyActionResponse(BaseModel):
    ready_trigger: str
    ready_action: str


class OpportunityAttackRequest(BaseModel):
    campaign_id: UUID
    session_id: UUID
    user_id: UUID
    attacker_id: UUID
    target_id: UUID
    weapon: WeaponDefinition
    cover_bonus: Literal[0, 2, 5] = 0
    extra_damage_dice: list[str] = Field(default_factory=list)
    expected_updated_at: datetime


class OpportunityAttackResponse(BaseModel):
    hit: bool
    critical_hit: bool
    damage_total: Optional[int] = None
    target_new_hp: int
