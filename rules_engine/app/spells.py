"""Spell casting validation (PHB p.201–211).

This module validates whether a spell CAN be cast — slot availability,
concentration conflicts, and incapacitation. It does NOT implement
individual spell effects; those are handled by the DM Service via LLM
for complex spells, or by dedicated handlers for common ones.
"""
from __future__ import annotations

from app.conditions import CONDITION_EFFECTS
from app.models import Condition, SpellCastRequest, SpellValidationResult


def validate_cast(request: SpellCastRequest) -> SpellValidationResult:
    """Validate whether the caster can cast this spell right now."""
    caster = request.caster

    # Incapacitated casters cannot cast spells
    for cond in caster.conditions:
        effect = CONDITION_EFFECTS.get(cond)
        if effect and effect.incapacitated:
            return SpellValidationResult(
                valid=False,
                rejection_reason=f"Caster is {cond.value} and cannot cast spells",
            )

    # Silenced / no verbal component available
    # Deafened does not prevent verbal components — silence spell would, but
    # that requires the caller to pass the combatant's environmental flags.

    # Cantrips (level 0) consume no spell slots
    if request.spell_level == 0:
        breaks_concentration = (
            request.is_concentration and request.concentration_active is not None
        )
        return SpellValidationResult(
            valid=True,
            breaks_concentration=breaks_concentration,
            slot_consumed=None,
        )

    # Check slot availability
    available = request.available_slots.available(request.spell_level)
    if available <= 0:
        return SpellValidationResult(
            valid=False,
            rejection_reason=(
                f"No level-{request.spell_level} spell slots remaining"
            ),
        )

    breaks_concentration = (
        request.is_concentration and request.concentration_active is not None
    )

    return SpellValidationResult(
        valid=True,
        breaks_concentration=breaks_concentration,
        slot_consumed=request.spell_level,
    )
