"""Movement validation against D&D 5e movement rules (PHB p.190–192)."""
from __future__ import annotations

from app.character_sheet import effective_speed
from app.models import Condition, MoveRequest, MoveResult


def validate_move(request: MoveRequest) -> MoveResult:
    """Validate a movement action against the combatant's available speed."""
    combatant = request.combatant
    speed = effective_speed(combatant)

    if speed == 0:
        return MoveResult(
            valid=False,
            effective_speed=0,
            distance_requested=request.distance_feet,
            movement_cost=0,
            rejection_reason="Speed is 0 — combatant cannot move",
        )

    # Standing up from prone costs half the combatant's speed (PHB p.191)
    stand_cost = (speed // 2) if request.standing_from_prone else 0

    # Crawling (prone but not standing) costs 2 ft per ft of movement (PHB p.191)
    is_crawling = (
        Condition.PRONE in combatant.conditions and not request.standing_from_prone
    )

    travel_cost = request.distance_feet * (2 if is_crawling else 1)

    # Difficult terrain costs an extra 1 ft per ft (total 2 ft per ft) (PHB p.182)
    if request.difficult_terrain:
        travel_cost *= 2

    total_cost = stand_cost + travel_cost

    if total_cost > speed:
        return MoveResult(
            valid=False,
            effective_speed=speed,
            distance_requested=request.distance_feet,
            movement_cost=total_cost,
            rejection_reason=(
                f"Movement cost ({total_cost} ft) exceeds available speed ({speed} ft)"
            ),
        )

    return MoveResult(
        valid=True,
        effective_speed=speed,
        distance_requested=request.distance_feet,
        movement_cost=total_cost,
    )
