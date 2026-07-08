"""Helper functions for derived D&D 5e character statistics.

These are pure functions — they compute values from a CombatantStats
snapshot but do not mutate it. Persistent state lives in World State.
"""
from __future__ import annotations

from app.models import AbilityScore, CombatantStats, Skill, SKILL_ABILITY_MAP


def proficiency_bonus_for_level(level: int) -> int:
    """Standard proficiency bonus by character/CR level (PHB p.15)."""
    return max(2, (level - 1) // 4 + 2)


def passive_score(combatant: CombatantStats, skill: Skill) -> int:
    """Passive skill score: 10 + ability modifier + proficiency (PHB p.175)."""
    ability = SKILL_ABILITY_MAP[skill]
    ability_mod = combatant.ability_scores.get_modifier(ability)
    if skill in combatant.expertise_skills:
        prof = combatant.proficiency_bonus * 2
    elif skill in combatant.proficient_skills:
        prof = combatant.proficiency_bonus
    else:
        prof = 0
    return 10 + ability_mod + prof


def spell_save_dc(combatant: CombatantStats, spellcasting_ability: AbilityScore) -> int:
    """8 + proficiency bonus + spellcasting ability modifier (PHB p.205)."""
    return (
        8
        + combatant.proficiency_bonus
        + combatant.ability_scores.get_modifier(spellcasting_ability)
    )


def spell_attack_bonus(combatant: CombatantStats, spellcasting_ability: AbilityScore) -> int:
    """Proficiency bonus + spellcasting ability modifier (PHB p.205)."""
    return (
        combatant.proficiency_bonus
        + combatant.ability_scores.get_modifier(spellcasting_ability)
    )


def effective_speed(combatant: CombatantStats) -> int:
    """Compute movement speed after applying conditions and exhaustion."""
    from app.conditions import CONDITION_EFFECTS

    speed = combatant.speed

    # Exhaustion 5+: speed = 0; exhaustion 2+: speed halved (PHB p.291)
    if combatant.exhaustion_level >= 5:
        return 0
    if combatant.exhaustion_level >= 2:
        speed //= 2

    for cond in combatant.conditions:
        effect = CONDITION_EFFECTS.get(cond)
        if effect and effect.speed_zero:
            return 0

    return speed


def is_incapacitated(combatant: CombatantStats) -> bool:
    """True if any condition prevents the combatant from taking actions."""
    from app.conditions import CONDITION_EFFECTS

    if combatant.exhaustion_level >= 6:
        return True
    for cond in combatant.conditions:
        effect = CONDITION_EFFECTS.get(cond)
        if effect and effect.incapacitated:
            return True
    return False
