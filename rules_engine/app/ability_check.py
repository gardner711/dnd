"""Ability check and saving throw resolution (PHB p.174–179)."""
from __future__ import annotations

from app import dice
from app.conditions import CONDITION_EFFECTS, resolve_advantage_state
from app.models import (
    AbilityCheckRequest,
    AbilityCheckResult,
    AbilityScore,
    AdvantageState,
    Condition,
    RollResult,
    SavingThrowRequest,
    SavingThrowResult,
    Skill,
)


def check(request: AbilityCheckRequest) -> AbilityCheckResult:
    """Resolve an ability check or skill check against a DC (PHB p.174)."""
    combatant = request.combatant
    ability = request.ability

    ability_mod = combatant.ability_scores.get_modifier(ability)

    # Proficiency: double for expertise, normal for proficiency, 0 otherwise
    proficiency = 0
    if request.skill:
        if request.skill in combatant.expertise_skills:
            proficiency = combatant.proficiency_bonus * 2
        elif request.skill in combatant.proficient_skills:
            proficiency = combatant.proficiency_bonus

    adv_state = resolve_advantage_state(
        base_state=request.advantage_state,
        conditions=combatant.conditions,
        check_type="ability",
        exhaustion_level=combatant.exhaustion_level,
    )

    d20_roll = dice.roll_d20(adv_state)
    total = d20_roll.total + ability_mod + proficiency

    return AbilityCheckResult(
        roll=d20_roll,
        ability_modifier=ability_mod,
        proficiency_applied=proficiency,
        total=total,
        dc=request.dc,
        success=total >= request.dc,
    )


def saving_throw(request: SavingThrowRequest) -> SavingThrowResult:
    """Resolve a saving throw against a DC (PHB p.179)."""
    combatant = request.combatant
    ability = request.ability

    ability_mod = combatant.ability_scores.get_modifier(ability)

    proficiency = 0
    if ability in combatant.proficient_saving_throws:
        proficiency = combatant.proficiency_bonus

    # Check for conditions that auto-fail STR or DEX saves before rolling
    for cond in combatant.conditions:
        effect = CONDITION_EFFECTS.get(cond)
        if not effect:
            continue
        if ability == AbilityScore.STRENGTH and effect.str_save_auto_fail:
            return _auto_fail(request, ability_mod, proficiency)
        if ability == AbilityScore.DEXTERITY and effect.dex_save_auto_fail:
            return _auto_fail(request, ability_mod, proficiency)

    adv_state = resolve_advantage_state(
        base_state=request.advantage_state,
        conditions=combatant.conditions,
        check_type="save",
        exhaustion_level=combatant.exhaustion_level,
    )

    # RESTRAINED imposes disadvantage (not auto-fail) on DEX saves
    if ability == AbilityScore.DEXTERITY and Condition.RESTRAINED in combatant.conditions:
        if adv_state == AdvantageState.ADVANTAGE:
            adv_state = AdvantageState.NORMAL  # adv + disadv cancel
        else:
            adv_state = AdvantageState.DISADVANTAGE

    d20_roll = dice.roll_d20(adv_state)
    total = d20_roll.total + ability_mod + proficiency

    return SavingThrowResult(
        roll=d20_roll,
        ability_modifier=ability_mod,
        proficiency_applied=proficiency,
        total=total,
        dc=request.dc,
        success=total >= request.dc,
    )


def _auto_fail(
    request: SavingThrowRequest,
    ability_mod: int,
    proficiency: int,
) -> SavingThrowResult:
    """Return a guaranteed failure — used when conditions auto-fail a save."""
    placeholder_roll = dice.roll("1d20")
    return SavingThrowResult(
        roll=placeholder_roll,
        ability_modifier=ability_mod,
        proficiency_applied=proficiency,
        total=0,
        dc=request.dc,
        success=False,
        rule_reference="PHB p.179 — automatic failure due to condition",
    )
