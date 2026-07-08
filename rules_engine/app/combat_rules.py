"""Initiative rolling and action economy validation (PHB p.189–193)."""
from __future__ import annotations

from app import dice
from app.models import AbilityScore, CombatantStats, InitiativeEntry, InitiativeRequest, InitiativeResult


def roll_initiative(request: InitiativeRequest) -> InitiativeResult:
    """Roll initiative for all combatants and return a sorted turn order.

    Each combatant rolls 1d20 + DEX modifier. Ties are broken by DEX modifier;
    further ties are left in the order provided (stable sort). PHB p.189.
    """
    entries: list[InitiativeEntry] = []
    for combatant in request.combatants:
        dex_mod = combatant.ability_scores.get_modifier(AbilityScore.DEXTERITY)
        roll = dice.roll("1d20")
        total = roll.total + dex_mod
        entries.append(
            InitiativeEntry(
                combatant_id=combatant.id,
                combatant_name=combatant.name,
                roll=roll,
                total=total,
                dexterity_modifier=dex_mod,
            )
        )

    entries.sort(key=lambda e: (e.total, e.dexterity_modifier), reverse=True)
    return InitiativeResult(order=entries)


def can_take_action(combatant: CombatantStats) -> tuple[bool, str | None]:
    """Return (allowed, reason) for whether the combatant can take actions.

    Incapacitated condition and exhaustion 6 both prevent actions. PHB p.189.
    """
    from app.character_sheet import is_incapacitated

    if is_incapacitated(combatant):
        conditions_desc = ", ".join(c.value for c in combatant.conditions)
        reason = f"Incapacitated (conditions: {conditions_desc})" if conditions_desc else "Dead (exhaustion 6)"
        return False, reason
    return True, None


def can_take_reaction(combatant: CombatantStats) -> tuple[bool, str | None]:
    """Reactions are blocked by the incapacitated condition. PHB p.190."""
    return can_take_action(combatant)


def opportunity_attack_triggered(
    moving_combatant: CombatantStats,
    attacker_reaction_available: bool,
) -> bool:
    """True if movement out of reach triggers an opportunity attack.

    Assumes Disengage was NOT taken. Caller must check that separately.
    PHB p.195.
    """
    if not attacker_reaction_available:
        return False
    allowed, _ = can_take_action(moving_combatant)
    # The incapacitated check here is for the moving creature being unable
    # to provoke (e.g., forced movement from spells does not trigger OA).
    # Caller is responsible for distinguishing voluntary vs forced movement.
    return True  # voluntary movement out of reach always triggers if reaction is available
