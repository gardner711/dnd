"""FastAPI application — Combat Engine orchestrator.

This service coordinates deterministic combat actions by calling the
Rules Engine for rule resolution and World State for encounter/character
persistence.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query

from app import event_log, service_clients
from app.models import (
    ApplyConditionRequest,
    AttackActionRequest,
    AttackActionResponse,
    ConditionResponse,
    DashActionRequest,
    DashActionResponse,
    DeathSaveActionRequest,
    DeathSaveActionResponse,
    DisengageActionRequest,
    DisengageActionResponse,
    DodgeActionRequest,
    DodgeActionResponse,
    EncounterOut,
    GrappleActionRequest,
    GrappleActionResponse,
    HelpActionRequest,
    HelpActionResponse,
    HideActionRequest,
    HideActionResponse,
    InitiativeEntryResult,
    MoveActionRequest,
    MoveActionResponse,
    NextTurnRequest,
    NextTurnResponse,
    OpportunityAttackRequest,
    OpportunityAttackResponse,
    ReadyActionRequest,
    ReadyActionResponse,
    RemoveConditionRequest,
    ShoveActionRequest,
    ShoveActionResponse,
    SpellCastActionRequest,
    SpellCastActionResponse,
    StartCombatRequest,
    StartCombatResponse,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Combat Engine",
    description="Coordinates combat state, initiative, attacks, movement, and conditions",
    version="0.1.0",
)

_TURN_STATE_DEFAULTS = {
    "movement_spent": 0,
    "extra_movement_budget": 0,
    "action_available": True,
    "bonus_action_available": True,
    "reaction_available": True,
    "attacks_used_this_action": 0,
    "disengage_active": False,
    "dodge_active": False,
    "hidden": False,
    "help_target_id": None,
    "help_type": None,
    "ready_trigger": None,
    "ready_action": None,
}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "combat-engine"}


@app.get("/combat", response_model=EncounterOut)
async def get_combat(campaign_id: UUID = Query(...)) -> EncounterOut:
    encounter = await service_clients.get_encounter(campaign_id)
    if encounter is None:
        raise HTTPException(404, "No active encounter for this campaign")
    return EncounterOut(**encounter)


@app.post("/combat/start", response_model=StartCombatResponse, status_code=201)
async def start_combat(body: StartCombatRequest) -> StartCombatResponse:
    initiative_input = [_combatant_entry_to_rules_stats(c) for c in body.combatants]
    order = await service_clients.roll_initiative(initiative_input)

    combatants_by_id = {c.combatant_id: c for c in body.combatants}
    initiative_order = []
    for entry in order:
        combatant_id = UUID(entry["combatant_id"])
        combatant = combatants_by_id[combatant_id]
        initiative_order.append(
            {
                "combatant_id": str(combatant_id),
                "name": combatant.name,
                "total": entry["total"],
                "is_player": combatant.is_player,
            }
        )

    combatant_states = {str(c.combatant_id): _combatant_entry_to_state(c) for c in body.combatants}
    encounter = await service_clients.create_encounter(
        body.campaign_id,
        {
            "campaign_id": str(body.campaign_id),
            "map_id": str(body.map_id) if body.map_id else None,
            "initiative_order": initiative_order,
            "combatant_states": combatant_states,
            "event_meta": {"session_id": str(body.session_id), "user_id": str(body.user_id)},
        },
    )

    await event_log.emit(
        event_type="combat.state_changed",
        aggregate_id=str(encounter["encounter_id"]),
        aggregate_type="combat",
        campaign_id=str(body.campaign_id),
        session_id=str(body.session_id),
        user_id=str(body.user_id),
        payload={"action": "combat_started", "round": encounter["round"], "initiative_order": initiative_order},
    )

    current = initiative_order[0]
    return StartCombatResponse(
        encounter_id=UUID(encounter["encounter_id"]),
        campaign_id=body.campaign_id,
        initiative_order=[InitiativeEntryResult(**entry) for entry in initiative_order],
        current_turn_combatant_id=UUID(current["combatant_id"]),
        round=1,
    )


@app.delete("/combat/end", status_code=204)
async def end_combat(
    campaign_id: UUID = Query(...),
    session_id: UUID = Query(...),
    user_id: UUID = Query(...),
) -> None:
    encounter = await service_clients.get_encounter(campaign_id)
    if encounter is None:
        raise HTTPException(404, "No active encounter for this campaign")
    deleted = await service_clients.delete_encounter(campaign_id)
    if not deleted:
        raise HTTPException(404, "No active encounter for this campaign")
    await event_log.emit(
        event_type="combat.state_changed",
        aggregate_id=str(encounter["encounter_id"]),
        aggregate_type="combat",
        campaign_id=str(campaign_id),
        session_id=str(session_id),
        user_id=str(user_id),
        payload={"action": "combat_ended"},
    )


@app.post("/combat/next-turn", response_model=NextTurnResponse)
async def next_turn(body: NextTurnRequest) -> NextTurnResponse:
    encounter = await _require_encounter(body.campaign_id)
    initiative_order = encounter["initiative_order"]
    if not initiative_order:
        raise HTTPException(409, "Encounter has no initiative order")

    new_index = encounter["current_turn_index"] + 1
    new_round = encounter["round"]
    if new_index >= len(initiative_order):
        new_index = 0
        new_round += 1

    current = initiative_order[new_index]
    current_state = _require_combatant_state(encounter, UUID(current["combatant_id"]))
    refreshed_current = {**current_state, "turn_state": dict(_TURN_STATE_DEFAULTS)}

    updated = await service_clients.update_encounter(
        body.campaign_id,
        {
            "round": new_round,
            "current_turn_index": new_index,
            "combatant_states": {str(current["combatant_id"]): refreshed_current},
            "expected_updated_at": encounter["updated_at"],
            "event_meta": {"session_id": str(body.session_id), "user_id": str(body.user_id)},
        },
    )
    if updated is None:
        raise HTTPException(409, "Encounter was modified by another request. Fetch the latest state and retry.")

    current = updated["initiative_order"][updated["current_turn_index"]]
    await event_log.emit(
        event_type="combat.state_changed",
        aggregate_id=str(updated["encounter_id"]),
        aggregate_type="combat",
        campaign_id=str(body.campaign_id),
        session_id=str(body.session_id),
        user_id=str(body.user_id),
        payload={
            "action": "turn_advanced",
            "round": updated["round"],
            "current_turn_index": updated["current_turn_index"],
            "current_turn_combatant_id": current["combatant_id"],
        },
    )
    return NextTurnResponse(
        round=updated["round"],
        current_turn_index=updated["current_turn_index"],
        current_turn_combatant_id=UUID(current["combatant_id"]),
        current_turn_combatant_name=current["name"],
    )


@app.post("/combat/attack", response_model=AttackActionResponse)
async def attack(body: AttackActionRequest) -> AttackActionResponse:
    encounter = await _require_encounter(body.campaign_id)
    attacker_state = _require_combatant_state(encounter, body.attacker_id)
    target_state = _require_combatant_state(encounter, body.target_id)
    _require_current_turn(encounter, body.attacker_id)
    _require_action_cost_allowed(attacker_state, body.action_cost, "attack")

    attack_result = await service_clients.resolve_attack(
        {
            "attacker": _combatant_state_to_rules_stats(attacker_state),
            "weapon": body.weapon.model_dump(mode="json"),
            "target_ac": target_state.get("armor_class", 10),
            "target_conditions": target_state.get("conditions", []),
            "target_defenses": _target_defenses_from_state(target_state),
            "cover_bonus": body.cover_bonus,
            "adjacent_to_hostile_creature": body.adjacent_to_hostile_creature,
            "extra_damage_dice": body.extra_damage_dice,
            "event_context": {
                "campaign_id": str(body.campaign_id),
                "session_id": str(body.session_id),
                "user_id": str(body.user_id),
                "aggregate_id": str(body.attacker_id),
                "aggregate_type": "combat",
            },
        }
    )

    damage_total = attack_result.get("damage_total") or 0
    current_temp_hp = int(target_state.get("temp_hp", 0) or 0)
    spill_damage = max(0, damage_total - current_temp_hp)
    new_temp_hp = max(0, current_temp_hp - damage_total)
    target_new_hp = max(0, int(target_state.get("current_hp", 0)) - spill_damage)
    target_is_unconscious = bool(target_state.get("is_player")) and target_new_hp <= 0

    target_patch = {**target_state, "current_hp": target_new_hp, "temp_hp": new_temp_hp}
    if target_is_unconscious and "unconscious" not in target_patch.get("conditions", []):
        target_patch["conditions"] = [*target_patch.get("conditions", []), "unconscious"]
        target_patch["death_saves"] = {"successes": 0, "failures": 0}

    concentration_lost = False
    if damage_total > 0 and target_state.get("concentration"):
        if target_new_hp <= 0:
            concentration_lost = True
        else:
            concentration_result = await service_clients.resolve_concentration_check(
                {
                    "caster": _combatant_state_to_rules_stats(target_state),
                    "damage_taken": damage_total,
                    "event_context": {
                        "campaign_id": str(body.campaign_id),
                        "session_id": str(body.session_id),
                        "user_id": str(body.user_id),
                        "aggregate_id": str(body.target_id),
                        "aggregate_type": "combat",
                    },
                }
            )
            concentration_lost = not concentration_result["concentration_maintained"]
        if concentration_lost:
            target_patch["concentration"] = None

    patch = {
        str(body.attacker_id): _with_attack_registered(attacker_state, body.action_cost),
        str(body.target_id): target_patch,
    }
    updated = await service_clients.update_encounter(
        body.campaign_id,
        {
            "combatant_states": patch,
            "expected_updated_at": body.expected_updated_at.isoformat(),
            "event_meta": {"session_id": str(body.session_id), "user_id": str(body.user_id)},
        },
    )
    if updated is None:
        raise HTTPException(409, "Encounter was modified by another request. Fetch the latest state and retry.")

    if target_state.get("is_player"):
        await _sync_player_character(
            body.target_id,
            body.campaign_id,
            body.session_id,
            body.user_id,
            {
                "current_hp": target_new_hp,
                "temp_hp": new_temp_hp,
                "conditions": target_patch.get("conditions", []),
                "concentration": target_patch.get("concentration"),
                "death_saves": target_patch.get("death_saves", {"successes": 0, "failures": 0}),
            },
        )

    await event_log.emit(
        event_type="attack.resolved",
        aggregate_id=str(body.target_id),
        aggregate_type="combat",
        campaign_id=str(body.campaign_id),
        session_id=str(body.session_id),
        user_id=str(body.user_id),
        payload={
            "attacker_id": str(body.attacker_id),
            "target_id": str(body.target_id),
            "weapon": body.weapon.name,
            "hit": attack_result["hit"],
            "critical_hit": attack_result["critical_hit"],
            "damage_total": damage_total if attack_result["hit"] else None,
            "target_new_hp": target_new_hp,
            "target_new_temp_hp": new_temp_hp,
            "concentration_lost": concentration_lost,
        },
    )

    return AttackActionResponse(
        hit=attack_result["hit"],
        critical_hit=attack_result["critical_hit"],
        damage_total=attack_result.get("damage_total"),
        damage_modifier=attack_result.get("damage_modifier", "none"),
        effective_ac=attack_result["effective_ac"],
        target_new_hp=target_new_hp,
        target_is_unconscious=target_is_unconscious,
    )


@app.post("/combat/conditions/apply", response_model=ConditionResponse)
async def apply_condition(body: ApplyConditionRequest) -> ConditionResponse:
    encounter = await _require_encounter(body.campaign_id)
    state = _require_combatant_state(encounter, body.combatant_id)
    new_conditions = list(state.get("conditions", []))
    if body.condition not in new_conditions:
        new_conditions.append(body.condition)
    await _patch_conditions_in_encounter(encounter, body.campaign_id, body.session_id, body.user_id, body.combatant_id, state, new_conditions)
    await _patch_character_conditions_if_player(body.is_player, body.combatant_id, body.campaign_id, body.session_id, body.user_id, new_conditions)
    await event_log.emit(
        event_type="combat.state_changed",
        aggregate_id=str(body.combatant_id),
        aggregate_type="combat",
        campaign_id=str(body.campaign_id),
        session_id=str(body.session_id),
        user_id=str(body.user_id),
        payload={"action": "condition_applied", "condition": body.condition},
    )
    return ConditionResponse(combatant_id=body.combatant_id, conditions=new_conditions)


@app.post("/combat/conditions/remove", response_model=ConditionResponse)
async def remove_condition(body: RemoveConditionRequest) -> ConditionResponse:
    encounter = await _require_encounter(body.campaign_id)
    state = _require_combatant_state(encounter, body.combatant_id)
    new_conditions = [c for c in state.get("conditions", []) if c != body.condition]
    await _patch_conditions_in_encounter(encounter, body.campaign_id, body.session_id, body.user_id, body.combatant_id, state, new_conditions)
    await _patch_character_conditions_if_player(body.is_player, body.combatant_id, body.campaign_id, body.session_id, body.user_id, new_conditions)
    await event_log.emit(
        event_type="combat.state_changed",
        aggregate_id=str(body.combatant_id),
        aggregate_type="combat",
        campaign_id=str(body.campaign_id),
        session_id=str(body.session_id),
        user_id=str(body.user_id),
        payload={"action": "condition_removed", "condition": body.condition},
    )
    return ConditionResponse(combatant_id=body.combatant_id, conditions=new_conditions)


@app.post("/combat/move", response_model=MoveActionResponse)
async def move(body: MoveActionRequest) -> MoveActionResponse:
    encounter = await _require_encounter(body.campaign_id)
    state = _require_combatant_state(encounter, body.combatant_id)
    _require_current_turn(encounter, body.combatant_id)

    standing_from_prone = body.standing_from_prone and "prone" in state.get("conditions", [])
    result = await service_clients.validate_movement(
        {
            "combatant": _combatant_state_to_rules_stats(state),
            "distance_feet": body.distance_feet,
            "difficult_terrain": body.difficult_terrain,
            "standing_from_prone": standing_from_prone,
            "event_context": {
                "campaign_id": str(body.campaign_id),
                "session_id": str(body.session_id),
                "user_id": str(body.user_id),
                "aggregate_id": str(body.combatant_id),
                "aggregate_type": "combat",
            },
        }
    )

    turn_state = _turn_state(state)
    new_spent = turn_state["movement_spent"] + result["movement_cost"]
    total_budget = result["effective_speed"] + turn_state["extra_movement_budget"]
    if not result["valid"] or new_spent > total_budget:
        remaining = max(0, total_budget - turn_state["movement_spent"])
        return MoveActionResponse(
            valid=False,
            effective_speed=total_budget,
            movement_cost=result["movement_cost"],
            rejection_reason=result.get("rejection_reason") or f"Movement cost ({result['movement_cost']} ft) exceeds remaining movement ({remaining} ft)",
        )

    new_conditions = list(state.get("conditions", []))
    if standing_from_prone:
        new_conditions = [c for c in new_conditions if c != "prone"]
    updated_state = {
        **state,
        "conditions": new_conditions,
        "turn_state": {**turn_state, "movement_spent": new_spent},
        "position": body.new_position if body.new_position is not None else state.get("position"),
    }
    updated = await service_clients.update_encounter(
        body.campaign_id,
        {
            "combatant_states": {str(body.combatant_id): updated_state},
            "expected_updated_at": encounter["updated_at"],
            "event_meta": {"session_id": str(body.session_id), "user_id": str(body.user_id)},
        },
    )
    if updated is None:
        raise HTTPException(409, "Encounter was modified by another request. Fetch the latest state and retry.")

    if state.get("is_player"):
        sync_payload: dict[str, Any] = {"conditions": new_conditions}
        if body.new_position is not None:
            sync_payload["position"] = body.new_position
        await _sync_player_character(body.combatant_id, body.campaign_id, body.session_id, body.user_id, sync_payload)

    await event_log.emit(
        event_type="combat.state_changed",
        aggregate_id=str(body.combatant_id),
        aggregate_type="combat",
        campaign_id=str(body.campaign_id),
        session_id=str(body.session_id),
        user_id=str(body.user_id),
        payload={"action": "combatant_moved", "position": body.new_position, "movement_cost": result["movement_cost"], "movement_spent": new_spent},
    )
    return MoveActionResponse(valid=True, effective_speed=total_budget, movement_cost=result["movement_cost"], rejection_reason=None)


@app.post("/combat/dash", response_model=DashActionResponse)
async def dash(body: DashActionRequest) -> DashActionResponse:
    encounter = await _require_encounter(body.campaign_id)
    state = _require_combatant_state(encounter, body.combatant_id)
    _require_current_turn(encounter, body.combatant_id)
    _require_action_cost_allowed(state, body.action_cost, "dash")

    updated_state = _with_action_cost_consumed(state, body.action_cost)
    updated_turn_state = _turn_state(updated_state)
    updated_turn_state["extra_movement_budget"] += int(state.get("speed", 30))
    updated_state["turn_state"] = updated_turn_state

    updated = await service_clients.update_encounter(
        body.campaign_id,
        {
            "combatant_states": {str(body.combatant_id): updated_state},
            "expected_updated_at": body.expected_updated_at.isoformat(),
            "event_meta": {"session_id": str(body.session_id), "user_id": str(body.user_id)},
        },
    )
    if updated is None:
        raise HTTPException(409, "Encounter was modified by another request. Fetch the latest state and retry.")

    total_budget = int(state.get("speed", 30)) + updated_turn_state["extra_movement_budget"]
    return DashActionResponse(
        movement_budget=total_budget,
        extra_movement_budget=updated_turn_state["extra_movement_budget"],
    )


@app.post("/combat/disengage", response_model=DisengageActionResponse)
async def disengage(body: DisengageActionRequest) -> DisengageActionResponse:
    encounter = await _require_encounter(body.campaign_id)
    state = _require_combatant_state(encounter, body.combatant_id)
    _require_current_turn(encounter, body.combatant_id)
    _require_action_cost_allowed(state, body.action_cost, "disengage")

    updated_state = _with_action_cost_consumed(state, body.action_cost)
    updated_state["turn_state"] = {**_turn_state(updated_state), "disengage_active": True}
    await _update_single_combatant_state(encounter, body.campaign_id, body.session_id, body.user_id, body.combatant_id, updated_state, body.expected_updated_at)
    return DisengageActionResponse(disengage_active=True)


@app.post("/combat/dodge", response_model=DodgeActionResponse)
async def dodge(body: DodgeActionRequest) -> DodgeActionResponse:
    encounter = await _require_encounter(body.campaign_id)
    state = _require_combatant_state(encounter, body.combatant_id)
    _require_current_turn(encounter, body.combatant_id)
    _require_action_cost_allowed(state, body.action_cost, "dodge")

    updated_state = _with_action_cost_consumed(state, body.action_cost)
    updated_state["turn_state"] = {**_turn_state(updated_state), "dodge_active": True}
    await _update_single_combatant_state(encounter, body.campaign_id, body.session_id, body.user_id, body.combatant_id, updated_state, body.expected_updated_at)
    return DodgeActionResponse(dodge_active=True)


@app.post("/combat/help", response_model=HelpActionResponse)
async def help_action(body: HelpActionRequest) -> HelpActionResponse:
    encounter = await _require_encounter(body.campaign_id)
    state = _require_combatant_state(encounter, body.combatant_id)
    _require_combatant_state(encounter, body.target_id)
    _require_current_turn(encounter, body.combatant_id)
    _require_action_cost_allowed(state, body.action_cost, "help")

    updated_state = _with_action_cost_consumed(state, body.action_cost)
    updated_state["turn_state"] = {
        **_turn_state(updated_state),
        "help_target_id": str(body.target_id),
        "help_type": body.help_type,
    }
    await _update_single_combatant_state(encounter, body.campaign_id, body.session_id, body.user_id, body.combatant_id, updated_state, body.expected_updated_at)
    return HelpActionResponse(help_target_id=body.target_id, help_type=body.help_type)


@app.post("/combat/hide", response_model=HideActionResponse)
async def hide(body: HideActionRequest) -> HideActionResponse:
    encounter = await _require_encounter(body.campaign_id)
    state = _require_combatant_state(encounter, body.combatant_id)
    _require_current_turn(encounter, body.combatant_id)
    _require_action_cost_allowed(state, body.action_cost, "hide")

    result = await service_clients.resolve_ability_check(
        {
            "combatant": _combatant_state_to_rules_stats(state),
            "ability": "dexterity",
            "skill": "stealth",
            "dc": body.dc,
            "advantage_state": body.advantage_state,
            "event_context": {
                "campaign_id": str(body.campaign_id),
                "session_id": str(body.session_id),
                "user_id": str(body.user_id),
                "aggregate_id": str(body.combatant_id),
                "aggregate_type": "combat",
            },
        }
    )

    updated_state = _with_action_cost_consumed(state, body.action_cost)
    updated_state["turn_state"] = {**_turn_state(updated_state), "hidden": result["success"]}
    await _update_single_combatant_state(encounter, body.campaign_id, body.session_id, body.user_id, body.combatant_id, updated_state, body.expected_updated_at)
    return HideActionResponse(hidden=result["success"], total=result["total"], dc=result["dc"], success=result["success"])


@app.post("/combat/ready", response_model=ReadyActionResponse)
async def ready_action(body: ReadyActionRequest) -> ReadyActionResponse:
    encounter = await _require_encounter(body.campaign_id)
    state = _require_combatant_state(encounter, body.combatant_id)
    _require_current_turn(encounter, body.combatant_id)
    _require_action_cost_allowed(state, body.action_cost, "ready")

    updated_state = _with_action_cost_consumed(state, body.action_cost)
    updated_state["turn_state"] = {
        **_turn_state(updated_state),
        "ready_trigger": body.trigger,
        "ready_action": body.action_description,
    }
    await _update_single_combatant_state(encounter, body.campaign_id, body.session_id, body.user_id, body.combatant_id, updated_state, body.expected_updated_at)
    return ReadyActionResponse(ready_trigger=body.trigger, ready_action=body.action_description)


@app.post("/combat/opportunity-attack", response_model=OpportunityAttackResponse)
async def opportunity_attack(body: OpportunityAttackRequest) -> OpportunityAttackResponse:
    encounter = await _require_encounter(body.campaign_id)
    attacker_state = _require_combatant_state(encounter, body.attacker_id)
    target_state = _require_combatant_state(encounter, body.target_id)
    _require_action_cost_allowed(attacker_state, "reaction", "opportunity_attack")

    if not _combat_capabilities(attacker_state)["can_opportunity_attack"]:
        raise HTTPException(409, "Combatant cannot make opportunity attacks")
    if _turn_state(target_state)["disengage_active"]:
        raise HTTPException(409, "Target is protected by disengage this turn")

    attack_result = await service_clients.resolve_attack(
        {
            "attacker": _combatant_state_to_rules_stats(attacker_state),
            "weapon": body.weapon.model_dump(mode="json"),
            "target_ac": target_state.get("armor_class", 10),
            "target_conditions": target_state.get("conditions", []),
            "target_defenses": _target_defenses_from_state(target_state),
            "cover_bonus": body.cover_bonus,
            "adjacent_to_hostile_creature": False,
            "extra_damage_dice": body.extra_damage_dice,
            "event_context": {
                "campaign_id": str(body.campaign_id),
                "session_id": str(body.session_id),
                "user_id": str(body.user_id),
                "aggregate_id": str(body.attacker_id),
                "aggregate_type": "combat",
            },
        }
    )

    damage_total = attack_result.get("damage_total") or 0
    current_temp_hp = int(target_state.get("temp_hp", 0) or 0)
    spill_damage = max(0, damage_total - current_temp_hp)
    new_temp_hp = max(0, current_temp_hp - damage_total)
    target_new_hp = max(0, int(target_state.get("current_hp", 0)) - spill_damage)
    target_patch = {**target_state, "current_hp": target_new_hp, "temp_hp": new_temp_hp}
    patch = {
        str(body.attacker_id): _with_action_cost_consumed(attacker_state, "reaction"),
        str(body.target_id): target_patch,
    }
    updated = await service_clients.update_encounter(
        body.campaign_id,
        {
            "combatant_states": patch,
            "expected_updated_at": body.expected_updated_at.isoformat(),
            "event_meta": {"session_id": str(body.session_id), "user_id": str(body.user_id)},
        },
    )
    if updated is None:
        raise HTTPException(409, "Encounter was modified by another request. Fetch the latest state and retry.")

    if target_state.get("is_player"):
        await _sync_player_character(body.target_id, body.campaign_id, body.session_id, body.user_id, {"current_hp": target_new_hp, "temp_hp": new_temp_hp})
    return OpportunityAttackResponse(hit=attack_result["hit"], critical_hit=attack_result["critical_hit"], damage_total=attack_result.get("damage_total"), target_new_hp=target_new_hp)


@app.post("/combat/death-save", response_model=DeathSaveActionResponse)
async def death_save(body: DeathSaveActionRequest) -> DeathSaveActionResponse:
    encounter = await _require_encounter(body.campaign_id)
    state = _require_combatant_state(encounter, body.combatant_id)
    _require_current_turn(encounter, body.combatant_id)
    if not state.get("is_player"):
        raise HTTPException(400, "Death saves apply only to player characters")

    death_saves = state.get("death_saves") or {"successes": 0, "failures": 0}
    result = await service_clients.resolve_death_save(str(body.combatant_id), int(death_saves.get("successes", 0)), int(death_saves.get("failures", 0)))

    updated_state = {**state, "death_saves": {"successes": result["new_successes"], "failures": result["new_failures"]}}
    if result["critical_stabilize"]:
        updated_state["current_hp"] = 1
        updated_state["conditions"] = [c for c in state.get("conditions", []) if c != "unconscious"]

    updated = await service_clients.update_encounter(
        body.campaign_id,
        {
            "combatant_states": {str(body.combatant_id): updated_state},
            "expected_updated_at": encounter["updated_at"],
            "event_meta": {"session_id": str(body.session_id), "user_id": str(body.user_id)},
        },
    )
    if updated is None:
        raise HTTPException(409, "Encounter was modified by another request. Fetch the latest state and retry.")

    await _sync_player_character(
        body.combatant_id,
        body.campaign_id,
        body.session_id,
        body.user_id,
        {"death_saves": updated_state["death_saves"], **({"current_hp": 1, "conditions": updated_state["conditions"]} if result["critical_stabilize"] else {})},
    )

    await event_log.emit(
        event_type="combat.state_changed",
        aggregate_id=str(body.combatant_id),
        aggregate_type="character",
        campaign_id=str(body.campaign_id),
        session_id=str(body.session_id),
        user_id=str(body.user_id),
        payload={
            "action": "death_save_resolved",
            "success": result["success"],
            "critical_stabilize": result["critical_stabilize"],
            "critical_failure": result["critical_failure"],
            "new_successes": result["new_successes"],
            "new_failures": result["new_failures"],
            "dead": result["dead"],
        },
    )
    return DeathSaveActionResponse(
        success=result["success"],
        critical_stabilize=result["critical_stabilize"],
        critical_failure=result["critical_failure"],
        new_successes=result["new_successes"],
        new_failures=result["new_failures"],
        stabilized=result["stabilized"],
        dead=result["dead"],
    )


@app.post("/combat/grapple", response_model=GrappleActionResponse)
async def grapple(body: GrappleActionRequest) -> GrappleActionResponse:
    encounter = await _require_encounter(body.campaign_id)
    attacker = _require_combatant_state(encounter, body.attacker_id)
    target = _require_combatant_state(encounter, body.target_id)
    _require_current_turn(encounter, body.attacker_id)
    _require_action_cost_available(attacker, body.action_cost)

    result = await service_clients.resolve_grapple(
        {
            "attacker": _combatant_state_to_rules_stats(attacker),
            "target": _combatant_state_to_rules_stats(target),
            "defender_uses_acrobatics": body.defender_uses_acrobatics,
            "event_context": {
                "campaign_id": str(body.campaign_id),
                "session_id": str(body.session_id),
                "user_id": str(body.user_id),
                "aggregate_id": str(body.attacker_id),
                "aggregate_type": "combat",
            },
        }
    )

    target_conditions = list(target.get("conditions", []))
    if result["grapple_succeeds"] and "grappled" not in target_conditions:
        target_conditions.append("grappled")
    patch = {
        str(body.attacker_id): _with_action_cost_consumed(attacker, body.action_cost),
        str(body.target_id): {**target, "conditions": target_conditions},
    }
    updated = await service_clients.update_encounter(
        body.campaign_id,
        {
            "combatant_states": patch,
            "expected_updated_at": body.expected_updated_at.isoformat(),
            "event_meta": {"session_id": str(body.session_id), "user_id": str(body.user_id)},
        },
    )
    if updated is None:
        raise HTTPException(409, "Encounter was modified by another request. Fetch the latest state and retry.")

    if target.get("is_player"):
        await _sync_player_character(body.target_id, body.campaign_id, body.session_id, body.user_id, {"conditions": target_conditions})
    return GrappleActionResponse(grapple_succeeds=result["grapple_succeeds"], target_conditions=target_conditions)


@app.post("/combat/shove", response_model=ShoveActionResponse)
async def shove(body: ShoveActionRequest) -> ShoveActionResponse:
    encounter = await _require_encounter(body.campaign_id)
    attacker = _require_combatant_state(encounter, body.attacker_id)
    target = _require_combatant_state(encounter, body.target_id)
    _require_current_turn(encounter, body.attacker_id)
    _require_action_cost_available(attacker, body.action_cost)

    result = await service_clients.resolve_shove(
        {
            "attacker": _combatant_state_to_rules_stats(attacker),
            "target": _combatant_state_to_rules_stats(target),
            "shove_type": body.shove_type,
            "defender_uses_acrobatics": body.defender_uses_acrobatics,
            "event_context": {
                "campaign_id": str(body.campaign_id),
                "session_id": str(body.session_id),
                "user_id": str(body.user_id),
                "aggregate_id": str(body.attacker_id),
                "aggregate_type": "combat",
            },
        }
    )

    target_conditions = list(target.get("conditions", []))
    target_position = target.get("position")
    if result["shove_succeeds"]:
        if body.shove_type == "knock_prone" and "prone" not in target_conditions:
            target_conditions.append("prone")
        if body.shove_type == "push_away" and body.new_position is not None:
            target_position = body.new_position
    patch = {
        str(body.attacker_id): _with_action_cost_consumed(attacker, body.action_cost),
        str(body.target_id): {**target, "conditions": target_conditions, "position": target_position},
    }
    updated = await service_clients.update_encounter(
        body.campaign_id,
        {
            "combatant_states": patch,
            "expected_updated_at": body.expected_updated_at.isoformat(),
            "event_meta": {"session_id": str(body.session_id), "user_id": str(body.user_id)},
        },
    )
    if updated is None:
        raise HTTPException(409, "Encounter was modified by another request. Fetch the latest state and retry.")

    if target.get("is_player"):
        sync_payload: dict[str, Any] = {"conditions": target_conditions}
        if target_position is not None:
            sync_payload["position"] = target_position
        await _sync_player_character(body.target_id, body.campaign_id, body.session_id, body.user_id, sync_payload)
    return ShoveActionResponse(shove_succeeds=result["shove_succeeds"], shove_type=result["shove_type"], target_conditions=target_conditions, target_position=target_position)


@app.post("/combat/spell-cast", response_model=SpellCastActionResponse)
async def spell_cast(body: SpellCastActionRequest) -> SpellCastActionResponse:
    encounter = await _require_encounter(body.campaign_id)
    caster = _require_combatant_state(encounter, body.caster_id)
    _require_current_turn(encounter, body.caster_id)
    _require_action_cost_available(caster, body.action_cost)

    result = await service_clients.validate_spell_cast(
        {
            "caster": _combatant_state_to_rules_stats(caster),
            "spell_name": body.spell_name,
            "spell_level": body.spell_level,
            "available_slots": caster.get("spell_slots", {}),
            "concentration_active": caster.get("concentration"),
            "is_concentration": body.is_concentration,
            "requires_verbal": body.requires_verbal,
            "requires_somatic": body.requires_somatic,
            "event_context": {
                "campaign_id": str(body.campaign_id),
                "session_id": str(body.session_id),
                "user_id": str(body.user_id),
                "aggregate_id": str(body.caster_id),
                "aggregate_type": "combat",
            },
        }
    )

    if not result["valid"]:
        return SpellCastActionResponse(valid=False, rejection_reason=result.get("rejection_reason"), breaks_concentration=result.get("breaks_concentration", False), slot_consumed=result.get("slot_consumed"), concentration=caster.get("concentration"))

    new_spell_slots = dict(caster.get("spell_slots", {}))
    slot_consumed = result.get("slot_consumed")
    if slot_consumed:
        key = f"level_{slot_consumed}"
        new_spell_slots[key] = max(0, int(new_spell_slots.get(key, 0)) - 1)
    new_concentration = body.spell_name if body.is_concentration else caster.get("concentration")
    updated_caster = _with_action_cost_consumed(caster, body.action_cost)
    updated_caster["spell_slots"] = new_spell_slots
    updated_caster["concentration"] = new_concentration

    updated = await service_clients.update_encounter(
        body.campaign_id,
        {
            "combatant_states": {str(body.caster_id): updated_caster},
            "expected_updated_at": body.expected_updated_at.isoformat(),
            "event_meta": {"session_id": str(body.session_id), "user_id": str(body.user_id)},
        },
    )
    if updated is None:
        raise HTTPException(409, "Encounter was modified by another request. Fetch the latest state and retry.")

    if caster.get("is_player"):
        await _sync_player_character(body.caster_id, body.campaign_id, body.session_id, body.user_id, {"spell_slots": new_spell_slots, "concentration": new_concentration})
    return SpellCastActionResponse(valid=True, rejection_reason=None, breaks_concentration=result.get("breaks_concentration", False), slot_consumed=slot_consumed, concentration=new_concentration)


async def _require_encounter(campaign_id: UUID) -> dict[str, Any]:
    encounter = await service_clients.get_encounter(campaign_id)
    if encounter is None:
        raise HTTPException(404, "No active encounter for this campaign")
    return encounter


def _require_combatant_state(encounter: dict[str, Any], combatant_id: UUID) -> dict[str, Any]:
    state = encounter["combatant_states"].get(str(combatant_id))
    if state is None:
        raise HTTPException(404, "Combatant not found in active encounter")
    return state


def _require_current_turn(encounter: dict[str, Any], combatant_id: UUID) -> None:
    current = encounter["initiative_order"][encounter["current_turn_index"]]
    if str(combatant_id) != current["combatant_id"]:
        raise HTTPException(409, f"It is currently {current['name']}'s turn")


def _turn_state(state: dict[str, Any]) -> dict[str, Any]:
    return {**_TURN_STATE_DEFAULTS, **(state.get("turn_state") or {})}


def _combat_capabilities(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "attacks_per_action": 1,
        "can_attack_as_bonus_action": False,
        "can_dash_as_bonus_action": False,
        "can_disengage_as_bonus_action": False,
        "can_dodge_as_bonus_action": False,
        "can_help_as_bonus_action": False,
        "can_hide_as_bonus_action": False,
        "can_ready_as_bonus_action": False,
        "can_opportunity_attack": True,
        **(state.get("combat_capabilities") or {}),
    }


def _require_action_cost_available(state: dict[str, Any], action_cost: str) -> None:
    turn_state = _turn_state(state)
    key = {"action": "action_available", "bonus_action": "bonus_action_available", "reaction": "reaction_available", "none": None}[action_cost]
    if key and not turn_state[key]:
        raise HTTPException(409, f"Combatant has already used their {action_cost.replace('_', ' ')} this turn")


def _require_action_cost_allowed(state: dict[str, Any], action_cost: str, action_name: str) -> None:
    _require_action_cost_available(state, action_cost)
    capabilities = _combat_capabilities(state)
    bonus_action_permissions = {
        "attack": capabilities["can_attack_as_bonus_action"],
        "dash": capabilities["can_dash_as_bonus_action"],
        "disengage": capabilities["can_disengage_as_bonus_action"],
        "dodge": capabilities["can_dodge_as_bonus_action"],
        "help": capabilities["can_help_as_bonus_action"],
        "hide": capabilities["can_hide_as_bonus_action"],
        "ready": capabilities["can_ready_as_bonus_action"],
    }
    if action_cost == "bonus_action" and action_name in bonus_action_permissions and not bonus_action_permissions[action_name]:
        raise HTTPException(409, f"Combatant cannot use {action_name.replace('_', ' ')} as a bonus action")


def _with_action_cost_consumed(state: dict[str, Any], action_cost: str) -> dict[str, Any]:
    if action_cost == "none":
        return dict(state)
    key = {"action": "action_available", "bonus_action": "bonus_action_available", "reaction": "reaction_available"}[action_cost]
    turn_state = _turn_state(state)
    turn_state[key] = False
    return {**state, "turn_state": turn_state}


def _with_attack_registered(state: dict[str, Any], action_cost: str) -> dict[str, Any]:
    if action_cost != "action":
        return _with_action_cost_consumed(state, action_cost)

    capabilities = _combat_capabilities(state)
    turn_state = _turn_state(state)
    attacks_used = int(turn_state.get("attacks_used_this_action", 0)) + 1
    attacks_per_action = int(capabilities.get("attacks_per_action", 1))
    turn_state["attacks_used_this_action"] = attacks_used
    if attacks_used >= attacks_per_action:
        turn_state["action_available"] = False
    return {**state, "turn_state": turn_state}


def _combatant_entry_to_rules_stats(combatant) -> dict[str, Any]:
    return {
        "id": str(combatant.combatant_id),
        "name": combatant.name,
        "ability_scores": combatant.ability_scores.model_dump(mode="json"),
        "proficiency_bonus": combatant.proficiency_bonus,
        "armor_class": combatant.armor_class,
        "max_hp": combatant.max_hp,
        "current_hp": combatant.current_hp,
        "speed": combatant.speed,
        "conditions": combatant.conditions,
        "proficient_skills": combatant.proficient_skills,
        "proficient_saving_throws": combatant.proficient_saving_throws,
        "expertise_skills": combatant.expertise_skills,
        "exhaustion_level": combatant.exhaustion_level,
        "is_proficient_with_weapon": combatant.is_proficient_with_weapon,
    }


def _combatant_entry_to_state(combatant) -> dict[str, Any]:
    return {
        "combatant_id": str(combatant.combatant_id),
        "name": combatant.name,
        "is_player": combatant.is_player,
        "current_hp": combatant.current_hp,
        "max_hp": combatant.max_hp,
        "temp_hp": combatant.temp_hp,
        "armor_class": combatant.armor_class,
        "speed": combatant.speed,
        "ability_scores": combatant.ability_scores.model_dump(mode="json"),
        "proficiency_bonus": combatant.proficiency_bonus,
        "conditions": combatant.conditions,
        "proficient_skills": combatant.proficient_skills,
        "proficient_saving_throws": combatant.proficient_saving_throws,
        "expertise_skills": combatant.expertise_skills,
        "exhaustion_level": combatant.exhaustion_level,
        "is_proficient_with_weapon": combatant.is_proficient_with_weapon,
        "concentration": combatant.concentration,
        "spell_slots": combatant.spell_slots,
        "death_saves": combatant.death_saves,
        "damage_resistances": combatant.target_defenses.damage_resistances,
        "damage_immunities": combatant.target_defenses.damage_immunities,
        "damage_vulnerabilities": combatant.target_defenses.damage_vulnerabilities,
        "combat_capabilities": combatant.combat_capabilities.model_dump(mode="json"),
        "turn_state": dict(_TURN_STATE_DEFAULTS),
        "position": combatant.position,
    }


def _combatant_state_to_rules_stats(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(state["combatant_id"]),
        "name": state["name"],
        "ability_scores": state.get("ability_scores", {}),
        "proficiency_bonus": state.get("proficiency_bonus", 2),
        "armor_class": state.get("armor_class", 10),
        "max_hp": state.get("max_hp", 10),
        "current_hp": state.get("current_hp", 10),
        "speed": state.get("speed", 30),
        "conditions": state.get("conditions", []),
        "proficient_skills": state.get("proficient_skills", []),
        "proficient_saving_throws": state.get("proficient_saving_throws", []),
        "expertise_skills": state.get("expertise_skills", []),
        "exhaustion_level": state.get("exhaustion_level", 0),
        "is_proficient_with_weapon": state.get("is_proficient_with_weapon", True),
    }


def _target_defenses_from_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "damage_resistances": state.get("damage_resistances", []),
        "damage_immunities": state.get("damage_immunities", []),
        "damage_vulnerabilities": state.get("damage_vulnerabilities", []),
    }


async def _patch_conditions_in_encounter(encounter: dict[str, Any], campaign_id: UUID, session_id: UUID, user_id: UUID, combatant_id: UUID, current_state: dict[str, Any], new_conditions: list[str]) -> None:
    updated = await service_clients.update_encounter(
        campaign_id,
        {
            "combatant_states": {str(combatant_id): {**current_state, "conditions": new_conditions}},
            "expected_updated_at": encounter["updated_at"],
            "event_meta": {"session_id": str(session_id), "user_id": str(user_id)},
        },
    )
    if updated is None:
        raise HTTPException(409, "Encounter was modified by another request. Fetch the latest state and retry.")


async def _patch_character_conditions_if_player(is_player: bool, combatant_id: UUID, campaign_id: UUID, session_id: UUID, user_id: UUID, new_conditions: list[str]) -> None:
    if not is_player:
        return
    await _sync_player_character(combatant_id, campaign_id, session_id, user_id, {"conditions": new_conditions})


async def _sync_player_character(character_id: UUID, campaign_id: UUID, session_id: UUID, user_id: UUID, updates: dict[str, Any]) -> None:
    character = await service_clients.get_character(character_id, campaign_id)
    if character is None:
        return
    payload = {**updates, "event_meta": {"session_id": str(session_id), "user_id": str(user_id)}}
    if character.get("updated_at"):
        payload["expected_updated_at"] = character["updated_at"]
    await service_clients.patch_character(character_id, campaign_id, payload)


async def _update_single_combatant_state(
    encounter: dict[str, Any],
    campaign_id: UUID,
    session_id: UUID,
    user_id: UUID,
    combatant_id: UUID,
    updated_state: dict[str, Any],
    expected_updated_at,
) -> None:
    updated = await service_clients.update_encounter(
        campaign_id,
        {
            "combatant_states": {str(combatant_id): updated_state},
            "expected_updated_at": expected_updated_at.isoformat() if hasattr(expected_updated_at, "isoformat") else expected_updated_at,
            "event_meta": {"session_id": str(session_id), "user_id": str(user_id)},
        },
    )
    if updated is None:
        raise HTTPException(409, "Encounter was modified by another request. Fetch the latest state and retry.")
