"""Grapple and shove mechanics (PHB p.195).

Both use a contested ability check:
  - Attacker: Athletics (STR + proficiency if proficient)
  - Defender: Athletics or Acrobatics (their choice; caller sets defender_uses_acrobatics)

Ties go to the defender — the attacker must EXCEED the defender's roll, not merely match it.

Preconditions the CALLER (Combat Engine) must enforce before calling:
  - Attacker is not incapacitated (use combat_rules.can_take_action)
  - Target is at most one size category larger than the attacker
  - Attacker has a free hand (for grapple)
"""
from __future__ import annotations

from app import dice
from app.models import (
    AbilityScore,
    ContestResult,
    CombatantStats,
    GrappleAttemptRequest,
    GrappleResult,
    ShoveAttemptRequest,
    ShoveResult,
    Skill,
)


def _contested_athletics(
    attacker: CombatantStats,
    defender: CombatantStats,
    defender_uses_acrobatics: bool,
) -> ContestResult:
    """Roll Athletics (attacker) vs Athletics or Acrobatics (defender). PHB p.195."""
    # Attacker: STR modifier + Athletics proficiency if proficient
    str_mod = attacker.ability_scores.get_modifier(AbilityScore.STRENGTH)
    attacker_prof = (
        attacker.proficiency_bonus if Skill.ATHLETICS in attacker.proficient_skills else 0
    )
    attacker_roll = dice.roll("1d20")
    attacker_total = attacker_roll.total + str_mod + attacker_prof

    # Defender: their choice of Athletics (STR) or Acrobatics (DEX)
    if defender_uses_acrobatics:
        def_mod = defender.ability_scores.get_modifier(AbilityScore.DEXTERITY)
        def_prof = (
            defender.proficiency_bonus if Skill.ACROBATICS in defender.proficient_skills else 0
        )
    else:
        def_mod = defender.ability_scores.get_modifier(AbilityScore.STRENGTH)
        def_prof = (
            defender.proficiency_bonus if Skill.ATHLETICS in defender.proficient_skills else 0
        )

    defender_roll = dice.roll("1d20")
    defender_total = defender_roll.total + def_mod + def_prof

    return ContestResult(
        attacker_roll=attacker_roll,
        attacker_total=attacker_total,
        defender_roll=defender_roll,
        defender_total=defender_total,
        attacker_wins=attacker_total > defender_total,  # ties favour the defender
    )


def attempt_grapple(request: GrappleAttemptRequest) -> GrappleResult:
    """Resolve a grapple attempt. On success, apply GRAPPLED to the target. PHB p.195."""
    contest = _contested_athletics(
        request.attacker, request.target, request.defender_uses_acrobatics
    )
    return GrappleResult(contest=contest, grapple_succeeds=contest.attacker_wins)


def attempt_shove(request: ShoveAttemptRequest) -> ShoveResult:
    """Resolve a shove attempt (knock prone or push 5 ft away). PHB p.195."""
    contest = _contested_athletics(
        request.attacker, request.target, request.defender_uses_acrobatics
    )
    return ShoveResult(
        contest=contest,
        shove_succeeds=contest.attacker_wins,
        shove_type=request.shove_type,
    )
