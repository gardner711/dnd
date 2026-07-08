"""Weapon attack resolution (PHB p.194–196)."""
from __future__ import annotations

from app import dice
from app.conditions import CONDITION_EFFECTS, resolve_advantage_state
from app.models import (
    AbilityScore,
    AdvantageState,
    AttackRequest,
    AttackResult,
    Condition,
    RollResult,
)


def resolve_attack(request: AttackRequest) -> AttackResult:
    """Resolve a weapon attack: to-hit roll then damage on a hit."""
    attacker = request.attacker
    weapon = request.weapon

    # Finesse: use whichever of STR or DEX gives a higher modifier (PHB p.147)
    if weapon.finesse:
        str_mod = attacker.ability_scores.get_modifier(AbilityScore.STRENGTH)
        dex_mod = attacker.ability_scores.get_modifier(AbilityScore.DEXTERITY)
        ability_mod = max(str_mod, dex_mod)
    else:
        ability_mod = attacker.ability_scores.get_modifier(weapon.ability_score)

    proficiency = attacker.proficiency_bonus if attacker.is_proficient_with_weapon else 0
    to_hit_modifier = ability_mod + proficiency + weapon.attack_bonus

    adv_state = _resolve_attack_advantage(request)
    d20_roll = dice.roll_d20(adv_state)

    # Natural roll is the d20 total before adding any modifiers
    natural_roll = d20_roll.total

    critical_miss = natural_roll == 1
    critical_hit = natural_roll == 20

    # Paralyzed or unconscious target within melee range → automatic critical hit
    if not critical_hit and request.attacking_from_melee_range:
        for cond in request.target_conditions:
            effect = CONDITION_EFFECTS.get(cond)
            if effect and effect.auto_crit_within_5ft:
                critical_hit = True
                break

    to_hit_total = natural_roll + to_hit_modifier
    effective_ac = request.target_ac + request.cover_bonus  # cover adds to effective AC (PHB p.196)
    hit = (not critical_miss) and (critical_hit or to_hit_total >= effective_ac)

    if not hit:
        return AttackResult(
            to_hit_roll=d20_roll,
            to_hit_total=to_hit_total,
            effective_ac=effective_ac,
            hit=False,
            critical_hit=False,
            critical_miss=critical_miss,
        )

    # ── Damage ──────────────────────────────────────────────────────────────
    # Critical hit: roll all damage dice twice, then add modifiers once (PHB p.196)
    if critical_hit:
        base_damage_notation = f"({weapon.damage_dice})+({weapon.damage_dice})"
        extra_parts = [f"({d})+({d})" for d in request.extra_damage_dice]
    else:
        base_damage_notation = weapon.damage_dice
        extra_parts = list(request.extra_damage_dice)

    all_parts = [base_damage_notation] + extra_parts
    damage_notation = "+".join(f"({p})" for p in all_parts)

    damage_mod = ability_mod + weapon.damage_bonus
    if damage_mod > 0:
        damage_notation += f"+{damage_mod}"
    elif damage_mod < 0:
        damage_notation += str(damage_mod)  # already has the minus sign

    damage_roll = dice.roll(damage_notation)
    damage_total = max(0, damage_roll.total)  # damage cannot go negative (PHB p.196)

    # Apply target's damage modifiers. Immunity wins over all; resistance and
    # vulnerability cancel each other out when both are present (PHB p.197).
    defenses = request.target_defenses
    damage_modifier = "none"
    if weapon.damage_type in defenses.damage_immunities:
        damage_total = 0
        damage_modifier = "immunity"
    elif (weapon.damage_type in defenses.damage_resistances
            and weapon.damage_type in defenses.damage_vulnerabilities):
        pass  # resistance + vulnerability cancel out
    elif weapon.damage_type in defenses.damage_resistances:
        damage_total = damage_total // 2
        damage_modifier = "resistance"
    elif weapon.damage_type in defenses.damage_vulnerabilities:
        damage_total = damage_total * 2
        damage_modifier = "vulnerability"

    return AttackResult(
        to_hit_roll=d20_roll,
        to_hit_total=to_hit_total,
        effective_ac=effective_ac,
        hit=True,
        critical_hit=critical_hit,
        critical_miss=False,
        damage_roll=damage_roll,
        damage_total=damage_total,
        damage_type=weapon.damage_type,
        damage_modifier=damage_modifier,
    )


def _resolve_attack_advantage(request: AttackRequest) -> AdvantageState:
    """Determine the final advantage state for the attack roll.

    D&D 5e: any source of advantage + any source of disadvantage = NORMAL.
    PHB p.173.
    """
    has_adv = request.advantage_state == AdvantageState.ADVANTAGE
    has_dis = request.advantage_state == AdvantageState.DISADVANTAGE

    # Attacker conditions
    for cond in request.attacker.conditions:
        effect = CONDITION_EFFECTS.get(cond)
        if not effect:
            continue
        if effect.attack_roll_advantage:
            has_adv = True
        if effect.attack_roll_disadvantage:
            has_dis = True

    # Target conditions affecting incoming attacks
    for cond in request.target_conditions:
        effect = CONDITION_EFFECTS.get(cond)
        if not effect:
            continue
        if effect.attacks_against_have_advantage:
            has_adv = True
        if effect.attacks_against_have_disadvantage:
            has_dis = True

    # Prone target: melee has advantage, ranged has disadvantage (PHB p.292)
    if Condition.PRONE in request.target_conditions:
        if request.attacking_from_melee_range:
            has_adv = True
        else:
            has_dis = True

    # Exhaustion 3+: disadvantage on attack rolls (PHB p.291)
    if request.attacker.exhaustion_level >= 3:
        has_dis = True

    # Ranged attack while adjacent to a hostile creature → disadvantage (PHB p.195)
    if request.weapon.ranged and request.adjacent_to_hostile_creature:
        has_dis = True

    if has_adv and has_dis:
        return AdvantageState.NORMAL
    if has_adv:
        return AdvantageState.ADVANTAGE
    if has_dis:
        return AdvantageState.DISADVANTAGE
    return AdvantageState.NORMAL
