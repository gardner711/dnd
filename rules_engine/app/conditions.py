"""All 15 D&D 5e SRD conditions and their mechanical effects.

Each condition is represented as an immutable ConditionEffect dataclass.
Callers query this module rather than hardcoding condition logic inline.
Reference: SRD p.358-359 / PHB Appendix A.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import AdvantageState, Condition


@dataclass(frozen=True)
class ConditionEffect:
    # Attack rolls made BY this combatant
    attack_roll_disadvantage: bool = False
    attack_roll_advantage: bool = False
    # Attack rolls made AGAINST this combatant
    attacks_against_have_advantage: bool = False
    attacks_against_have_disadvantage: bool = False
    # Ability checks
    ability_check_disadvantage: bool = False
    # Saving throws
    str_save_auto_fail: bool = False
    dex_save_auto_fail: bool = False
    dex_save_disadvantage: bool = False   # RESTRAINED: disadvantage (not auto-fail)
    # Movement
    speed_zero: bool = False
    # Actions
    incapacitated: bool = False           # cannot take actions or reactions
    # Special attack interactions
    auto_crit_within_5ft: bool = False    # PARALYZED, UNCONSCIOUS
    # Damage
    all_damage_resistance: bool = False   # PETRIFIED
    immune_poison_condition: bool = False
    immune_disease: bool = False
    # Contextual (resolved by caller)
    cant_move_toward_source: bool = False  # FRIGHTENED


CONDITION_EFFECTS: dict[Condition, ConditionEffect] = {
    Condition.BLINDED: ConditionEffect(
        attack_roll_disadvantage=True,
        attacks_against_have_advantage=True,
    ),
    Condition.CHARMED: ConditionEffect(
        # Can't attack the charmer; charmer has advantage on social checks.
        # Both effects are context-dependent — handled by calling service.
    ),
    Condition.DEAFENED: ConditionEffect(
        # Automatically fails hearing-based checks — context-dependent.
    ),
    Condition.EXHAUSTION: ConditionEffect(
        # Level-based effects; see EXHAUSTION_THRESHOLDS below.
    ),
    Condition.FRIGHTENED: ConditionEffect(
        attack_roll_disadvantage=True,
        ability_check_disadvantage=True,
        cant_move_toward_source=True,
    ),
    Condition.GRAPPLED: ConditionEffect(
        speed_zero=True,
    ),
    Condition.INCAPACITATED: ConditionEffect(
        incapacitated=True,
    ),
    Condition.INVISIBLE: ConditionEffect(
        attack_roll_advantage=True,
        attacks_against_have_disadvantage=True,
    ),
    Condition.PARALYZED: ConditionEffect(
        incapacitated=True,
        speed_zero=True,
        str_save_auto_fail=True,
        dex_save_auto_fail=True,
        attacks_against_have_advantage=True,
        auto_crit_within_5ft=True,
    ),
    Condition.PETRIFIED: ConditionEffect(
        incapacitated=True,
        speed_zero=True,
        str_save_auto_fail=True,
        dex_save_auto_fail=True,
        attacks_against_have_advantage=True,
        all_damage_resistance=True,
        immune_poison_condition=True,
        immune_disease=True,
    ),
    Condition.POISONED: ConditionEffect(
        attack_roll_disadvantage=True,
        ability_check_disadvantage=True,
    ),
    Condition.PRONE: ConditionEffect(
        attack_roll_disadvantage=True,
        # Melee attacks within 5 ft have advantage; ranged attacks have disadvantage.
        # This is resolved contextually in attack.py based on attacking_from_melee_range.
    ),
    Condition.RESTRAINED: ConditionEffect(
        speed_zero=True,
        attack_roll_disadvantage=True,
        attacks_against_have_advantage=True,
        dex_save_disadvantage=True,   # disadvantage, NOT auto-fail (PHB p.292)
    ),
    Condition.STUNNED: ConditionEffect(
        incapacitated=True,
        speed_zero=True,
        str_save_auto_fail=True,
        dex_save_auto_fail=True,
        attacks_against_have_advantage=True,
    ),
    Condition.UNCONSCIOUS: ConditionEffect(
        incapacitated=True,
        speed_zero=True,
        str_save_auto_fail=True,
        dex_save_auto_fail=True,
        attacks_against_have_advantage=True,
        auto_crit_within_5ft=True,
    ),
}

# Cumulative effects per exhaustion level (PHB p.291)
EXHAUSTION_THRESHOLDS: dict[int, str] = {
    1: "Disadvantage on ability checks",
    2: "Speed halved",
    3: "Disadvantage on attack rolls and saving throws",
    4: "Hit point maximum halved",
    5: "Speed reduced to 0",
    6: "Death",
}


def resolve_advantage_state(
    base_state: AdvantageState,
    conditions: list[Condition],
    check_type: str = "ability",  # "ability" | "save" | "attack"
    exhaustion_level: int = 0,
) -> AdvantageState:
    """Merge a base advantage state with condition-imposed modifiers.

    D&D 5e rule: any number of advantage sources vs any number of
    disadvantage sources cancel each other out to NORMAL. PHB p.173.
    """
    has_adv = base_state == AdvantageState.ADVANTAGE
    has_dis = base_state == AdvantageState.DISADVANTAGE

    for cond in conditions:
        effect = CONDITION_EFFECTS.get(cond)
        if not effect:
            continue
        if check_type == "ability" and effect.ability_check_disadvantage:
            has_dis = True
        if check_type == "attack":
            if effect.attack_roll_disadvantage:
                has_dis = True
            if effect.attack_roll_advantage:
                has_adv = True

    # Exhaustion level 1+ → disadvantage on ability checks
    if check_type == "ability" and exhaustion_level >= 1:
        has_dis = True
    # Exhaustion level 3+ → disadvantage on attack rolls and saving throws
    if check_type in ("attack", "save") and exhaustion_level >= 3:
        has_dis = True

    if has_adv and has_dis:
        return AdvantageState.NORMAL
    if has_adv:
        return AdvantageState.ADVANTAGE
    if has_dis:
        return AdvantageState.DISADVANTAGE
    return base_state
