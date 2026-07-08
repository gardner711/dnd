"""Concentration check resolution (PHB p.203).

When a concentrating spellcaster takes damage, they must make a CON saving throw.
DC = max(10, half the damage taken), rounded down.
The Combat Engine calls this endpoint after every hit on a concentrating caster.
"""
from __future__ import annotations

from app import ability_check
from app.models import (
    AbilityScore,
    ConcentrationCheckRequest,
    ConcentrationCheckResult,
    SavingThrowRequest,
)


def concentration_check(request: ConcentrationCheckRequest) -> ConcentrationCheckResult:
    """Resolve a concentration saving throw after the caster takes damage. PHB p.203."""
    dc = max(10, request.damage_taken // 2)

    save = ability_check.saving_throw(
        SavingThrowRequest(
            combatant=request.caster,
            ability=AbilityScore.CONSTITUTION,
            dc=dc,
        )
    )

    return ConcentrationCheckResult(
        roll=save.roll,
        dc=dc,
        total=save.total,
        success=save.success,
        concentration_maintained=save.success,
    )
