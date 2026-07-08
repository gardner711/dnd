"""Shared Pydantic models and enumerations for the Rules Engine service."""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ── Enumerations ───────────────────────────────────────────────────────────

class AdvantageState(str, Enum):
    NORMAL = "normal"
    ADVANTAGE = "advantage"
    DISADVANTAGE = "disadvantage"


class DamageType(str, Enum):
    ACID = "acid"
    BLUDGEONING = "bludgeoning"
    COLD = "cold"
    FIRE = "fire"
    FORCE = "force"
    LIGHTNING = "lightning"
    NECROTIC = "necrotic"
    PIERCING = "piercing"
    POISON = "poison"
    PSYCHIC = "psychic"
    RADIANT = "radiant"
    SLASHING = "slashing"
    THUNDER = "thunder"


class Condition(str, Enum):
    BLINDED = "blinded"
    CHARMED = "charmed"
    DEAFENED = "deafened"
    EXHAUSTION = "exhaustion"
    FRIGHTENED = "frightened"
    GRAPPLED = "grappled"
    INCAPACITATED = "incapacitated"
    INVISIBLE = "invisible"
    PARALYZED = "paralyzed"
    PETRIFIED = "petrified"
    POISONED = "poisoned"
    PRONE = "prone"
    RESTRAINED = "restrained"
    STUNNED = "stunned"
    UNCONSCIOUS = "unconscious"


class AbilityScore(str, Enum):
    STRENGTH = "strength"
    DEXTERITY = "dexterity"
    CONSTITUTION = "constitution"
    INTELLIGENCE = "intelligence"
    WISDOM = "wisdom"
    CHARISMA = "charisma"


class Skill(str, Enum):
    ACROBATICS = "acrobatics"
    ANIMAL_HANDLING = "animal_handling"
    ARCANA = "arcana"
    ATHLETICS = "athletics"
    DECEPTION = "deception"
    HISTORY = "history"
    INSIGHT = "insight"
    INTIMIDATION = "intimidation"
    INVESTIGATION = "investigation"
    MEDICINE = "medicine"
    NATURE = "nature"
    PERCEPTION = "perception"
    PERFORMANCE = "performance"
    PERSUASION = "persuasion"
    RELIGION = "religion"
    SLEIGHT_OF_HAND = "sleight_of_hand"
    STEALTH = "stealth"
    SURVIVAL = "survival"


# Skill → governing ability score (PHB p.174)
SKILL_ABILITY_MAP: dict[Skill, AbilityScore] = {
    Skill.ATHLETICS: AbilityScore.STRENGTH,
    Skill.ACROBATICS: AbilityScore.DEXTERITY,
    Skill.SLEIGHT_OF_HAND: AbilityScore.DEXTERITY,
    Skill.STEALTH: AbilityScore.DEXTERITY,
    Skill.ARCANA: AbilityScore.INTELLIGENCE,
    Skill.HISTORY: AbilityScore.INTELLIGENCE,
    Skill.INVESTIGATION: AbilityScore.INTELLIGENCE,
    Skill.NATURE: AbilityScore.INTELLIGENCE,
    Skill.RELIGION: AbilityScore.INTELLIGENCE,
    Skill.ANIMAL_HANDLING: AbilityScore.WISDOM,
    Skill.INSIGHT: AbilityScore.WISDOM,
    Skill.MEDICINE: AbilityScore.WISDOM,
    Skill.PERCEPTION: AbilityScore.WISDOM,
    Skill.SURVIVAL: AbilityScore.WISDOM,
    Skill.DECEPTION: AbilityScore.CHARISMA,
    Skill.INTIMIDATION: AbilityScore.CHARISMA,
    Skill.PERFORMANCE: AbilityScore.CHARISMA,
    Skill.PERSUASION: AbilityScore.CHARISMA,
}


# ── Core stat block ────────────────────────────────────────────────────────

class EventContext(BaseModel):
    """Session context passed by callers so the Rules Engine can emit audit events.

    Optional on all requests — if omitted, no event is written to the Event Log.
    Values originate from the JWT claims the calling service received from Traefik.
    """
    campaign_id: str
    session_id: str
    user_id: str
    aggregate_id: str
    aggregate_type: str = "character"  # character | npc | combat | story | world


class RollResult(BaseModel):
    notation: str
    dice_values: list[int]
    modifier: int = 0
    total: int
    expression: str  # human-readable breakdown, e.g. "2d6 (3, 4) + 3 = 10"


class AbilityScores(BaseModel):
    strength: int = 10
    dexterity: int = 10
    constitution: int = 10
    intelligence: int = 10
    wisdom: int = 10
    charisma: int = 10

    def get_score(self, ability: AbilityScore) -> int:
        return getattr(self, ability.value)

    def get_modifier(self, ability: AbilityScore) -> int:
        return (self.get_score(ability) - 10) // 2


class CombatantStats(BaseModel):
    id: str
    name: str
    ability_scores: AbilityScores
    proficiency_bonus: int = 2
    armor_class: int = 10
    max_hp: int = 10
    current_hp: int = 10
    speed: int = 30
    conditions: list[Condition] = Field(default_factory=list)
    proficient_skills: list[Skill] = Field(default_factory=list)
    proficient_saving_throws: list[AbilityScore] = Field(default_factory=list)
    expertise_skills: list[Skill] = Field(default_factory=list)
    exhaustion_level: int = Field(default=0, ge=0, le=6)
    is_proficient_with_weapon: bool = True  # caller asserts weapon proficiency


class WeaponDefinition(BaseModel):
    name: str
    damage_dice: str                          # e.g. "1d8", "2d6"
    damage_type: DamageType
    ability_score: AbilityScore = AbilityScore.STRENGTH
    finesse: bool = False
    ranged: bool = False
    magical: bool = False
    attack_bonus: int = 0                     # magic weapon bonus (+1, +2, etc.)
    damage_bonus: int = 0


class SpellSlots(BaseModel):
    level_1: int = 0
    level_2: int = 0
    level_3: int = 0
    level_4: int = 0
    level_5: int = 0
    level_6: int = 0
    level_7: int = 0
    level_8: int = 0
    level_9: int = 0

    def available(self, level: int) -> int:
        return getattr(self, f"level_{level}", 0)


# ── Attack ─────────────────────────────────────────────────────────────────

class TargetDefenses(BaseModel):
    """Damage modifiers for the attack target (PHB p.197)."""
    damage_resistances: list[DamageType] = Field(default_factory=list)
    damage_immunities: list[DamageType] = Field(default_factory=list)
    damage_vulnerabilities: list[DamageType] = Field(default_factory=list)


class AttackRequest(BaseModel):
    attacker: CombatantStats
    weapon: WeaponDefinition
    target_ac: int
    target_conditions: list[Condition] = Field(default_factory=list)
    target_defenses: TargetDefenses = Field(default_factory=TargetDefenses)
    attacking_from_melee_range: bool = True
    adjacent_to_hostile_creature: bool = False  # ranged-in-melee disadvantage (PHB p.195)
    cover_bonus: int = Field(default=0, ge=0, le=5)  # 0, +2 half cover, or +5 three-quarters (PHB p.196)
    advantage_state: AdvantageState = AdvantageState.NORMAL
    extra_damage_dice: list[str] = Field(default_factory=list)  # e.g. ["2d6"] for Sneak Attack
    event_context: Optional[EventContext] = None


class AttackResult(BaseModel):
    to_hit_roll: RollResult
    to_hit_total: int
    effective_ac: int                        # target_ac + cover_bonus
    hit: bool
    critical_hit: bool
    critical_miss: bool
    damage_roll: Optional[RollResult] = None
    damage_total: Optional[int] = None       # final damage after resistance/immunity/vulnerability
    damage_type: Optional[DamageType] = None
    damage_modifier: Literal["none", "resistance", "immunity", "vulnerability"] = "none"
    rule_reference: str = "PHB p.194"


# ── Ability Check ───────────────────────────────────────────────────────────

class AbilityCheckRequest(BaseModel):
    combatant: CombatantStats
    ability: AbilityScore
    dc: int
    skill: Optional[Skill] = None
    advantage_state: AdvantageState = AdvantageState.NORMAL
    event_context: Optional[EventContext] = None


class AbilityCheckResult(BaseModel):
    roll: RollResult
    ability_modifier: int
    proficiency_applied: int
    total: int
    dc: int
    success: bool
    rule_reference: str = "PHB p.174"


# ── Saving Throw ────────────────────────────────────────────────────────────

class SavingThrowRequest(BaseModel):
    combatant: CombatantStats
    ability: AbilityScore
    dc: int
    advantage_state: AdvantageState = AdvantageState.NORMAL
    event_context: Optional[EventContext] = None


class SavingThrowResult(BaseModel):
    roll: RollResult
    ability_modifier: int
    proficiency_applied: int
    total: int
    dc: int
    success: bool
    rule_reference: str = "PHB p.179"


# ── Spell Validation ────────────────────────────────────────────────────────

class SpellCastRequest(BaseModel):
    caster: CombatantStats
    spell_name: str
    spell_level: int = Field(ge=0, le=9)
    available_slots: SpellSlots
    concentration_active: Optional[str] = None  # name of current concentration spell
    is_concentration: bool = False
    requires_verbal: bool = True
    requires_somatic: bool = True
    event_context: Optional[EventContext] = None


class SpellValidationResult(BaseModel):
    valid: bool
    rejection_reason: Optional[str] = None
    breaks_concentration: bool = False
    slot_consumed: Optional[int] = None
    rule_reference: str = "PHB p.201"


# ── Concentration ────────────────────────────────────────────────────

class ConcentrationCheckRequest(BaseModel):
    caster: CombatantStats
    damage_taken: int = Field(ge=1)
    event_context: Optional[EventContext] = None


class ConcentrationCheckResult(BaseModel):
    roll: RollResult
    dc: int
    total: int
    success: bool
    concentration_maintained: bool
    rule_reference: str = "PHB p.203"


# ── Initiative ──────────────────────────────────────────────────────────────

class InitiativeRequest(BaseModel):
    combatants: list[CombatantStats]
    event_context: Optional[EventContext] = None


class InitiativeEntry(BaseModel):
    combatant_id: str
    combatant_name: str
    roll: RollResult
    total: int
    dexterity_modifier: int


class InitiativeResult(BaseModel):
    order: list[InitiativeEntry]  # sorted highest to lowest, ties broken by DEX modifier


# ── Grapple & Shove ───────────────────────────────────────────────────

class ContestResult(BaseModel):
    attacker_roll: RollResult
    attacker_total: int
    defender_roll: RollResult
    defender_total: int
    attacker_wins: bool  # ties go to the defender (attacker must EXCEED, not match)


class GrappleAttemptRequest(BaseModel):
    attacker: CombatantStats
    target: CombatantStats
    defender_uses_acrobatics: bool = False  # defender chooses Athletics or Acrobatics
    event_context: Optional[EventContext] = None


class GrappleResult(BaseModel):
    contest: ContestResult
    grapple_succeeds: bool
    rule_reference: str = "PHB p.195"


class ShoveAttemptRequest(BaseModel):
    attacker: CombatantStats
    target: CombatantStats
    shove_type: Literal["knock_prone", "push_away"] = "knock_prone"
    defender_uses_acrobatics: bool = False
    event_context: Optional[EventContext] = None


class ShoveResult(BaseModel):
    contest: ContestResult
    shove_succeeds: bool
    shove_type: str
    rule_reference: str = "PHB p.195"


# ── Movement ────────────────────────────────────────────────────────────────

class MoveRequest(BaseModel):
    combatant: CombatantStats
    distance_feet: int
    difficult_terrain: bool = False
    standing_from_prone: bool = False
    event_context: Optional[EventContext] = None


class MoveResult(BaseModel):
    valid: bool
    effective_speed: int
    distance_requested: int
    movement_cost: int
    rejection_reason: Optional[str] = None
    rule_reference: str = "PHB p.190"


# ── Death Save ──────────────────────────────────────────────────────────────

class DeathSaveRequest(BaseModel):
    combatant_id: str
    current_successes: int = Field(default=0, ge=0, le=3)
    current_failures: int = Field(default=0, ge=0, le=3)
    event_context: Optional[EventContext] = None


class DeathSaveResult(BaseModel):
    roll: RollResult
    success: bool
    critical_stabilize: bool   # natural 20 → regain 1 HP immediately
    critical_failure: bool     # natural 1 → counts as two failures
    new_successes: int
    new_failures: int
    stabilized: bool
    dead: bool
    rule_reference: str = "PHB p.197"


# ── Generic Dice Roll ───────────────────────────────────────────────────────

class DiceRollRequest(BaseModel):
    notation: str
    purpose: Optional[str] = None
    event_context: Optional[EventContext] = None


class DiceRollResponse(BaseModel):
    result: RollResult
    purpose: Optional[str] = None
