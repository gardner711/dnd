"""Route tests for the Combat Engine.

All upstream HTTP calls are mocked through the service client module; no
real network or database I/O occurs here.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.event_log as event_log_module
import app.service_clients as clients
from app.main import app

_NOW = datetime.now(UTC)
_CAMP = uuid4()
_SESSION = uuid4()
_USER = uuid4()
_P1 = uuid4()
_P2 = uuid4()
_ENCOUNTER = uuid4()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(event_log_module, "emit", AsyncMock(return_value=None))
    return TestClient(app)


@pytest.fixture
def encounter_dict() -> dict:
    return {
        "encounter_id": str(_ENCOUNTER),
        "campaign_id": str(_CAMP),
        "map_id": None,
        "round": 1,
        "current_turn_index": 0,
        "initiative_order": [
            {"combatant_id": str(_P1), "name": "Aria", "total": 18, "is_player": True},
            {"combatant_id": str(_P2), "name": "Goblin", "total": 12, "is_player": False},
        ],
        "combatant_states": {
            str(_P1): {
                "combatant_id": str(_P1),
                "name": "Aria",
                "is_player": True,
                "current_hp": 20,
                "max_hp": 24,
                "temp_hp": 0,
                "armor_class": 16,
                "speed": 30,
                "ability_scores": {"strength": 16, "dexterity": 12, "constitution": 14, "intelligence": 10, "wisdom": 10, "charisma": 8},
                "proficiency_bonus": 2,
                "conditions": [],
                "proficient_skills": [],
                "proficient_saving_throws": [],
                "expertise_skills": [],
                "exhaustion_level": 0,
                "is_proficient_with_weapon": True,
                "concentration": None,
                "spell_slots": {f"level_{i}": 0 for i in range(1, 10)},
                "death_saves": {"successes": 0, "failures": 0},
                "damage_resistances": [],
                "damage_immunities": [],
                "damage_vulnerabilities": [],
                "turn_state": {"movement_spent": 0, "action_available": True, "bonus_action_available": True, "reaction_available": True},
                "position": None,
            },
            str(_P2): {
                "combatant_id": str(_P2),
                "name": "Goblin",
                "is_player": False,
                "current_hp": 7,
                "max_hp": 7,
                "temp_hp": 0,
                "armor_class": 13,
                "speed": 30,
                "ability_scores": {"strength": 8, "dexterity": 14, "constitution": 10, "intelligence": 8, "wisdom": 8, "charisma": 8},
                "proficiency_bonus": 2,
                "conditions": [],
                "proficient_skills": [],
                "proficient_saving_throws": [],
                "expertise_skills": [],
                "exhaustion_level": 0,
                "is_proficient_with_weapon": True,
                "concentration": None,
                "spell_slots": {f"level_{i}": 0 for i in range(1, 10)},
                "death_saves": {"successes": 0, "failures": 0},
                "damage_resistances": [],
                "damage_immunities": [],
                "damage_vulnerabilities": [],
                "turn_state": {"movement_spent": 0, "action_available": True, "bonus_action_available": True, "reaction_available": True},
                "position": None,
            },
        },
        "active": True,
        "started_at": _NOW.isoformat(),
        "updated_at": _NOW.isoformat(),
    }


@pytest.fixture
def character_dict() -> dict:
    return {
        "character_id": str(_P1),
        "campaign_id": str(_CAMP),
        "user_id": str(_USER),
        "name": "Aria",
        "class_name": "fighter",
        "level": 3,
        "xp": 0,
        "current_hp": 20,
        "max_hp": 24,
        "temp_hp": 0,
        "armor_class": 16,
        "speed": 30,
        "ability_scores": {"strength": 16, "dexterity": 12, "constitution": 14, "intelligence": 10, "wisdom": 10, "charisma": 8},
        "conditions": [],
        "exhaustion_level": 0,
        "spell_slots": {f"level_{i}": 0 for i in range(1, 10)},
        "concentration": None,
        "death_saves": {"successes": 0, "failures": 0},
        "position": None,
        "inventory": [],
        "currency": {"cp": 0, "sp": 0, "ep": 0, "gp": 0, "pp": 0},
        "active_effects": [],
        "proficiency_bonus": 2,
        "proficient_skills": [],
        "proficient_saving_throws": [],
        "expertise_skills": [],
        "updated_at": _NOW.isoformat(),
    }


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "combat-engine"


def test_get_combat_404(client, monkeypatch):
    monkeypatch.setattr(clients, "get_encounter", AsyncMock(return_value=None))
    response = client.get("/combat", params={"campaign_id": str(_CAMP)})
    assert response.status_code == 404
    assert "No active encounter" in response.json()["detail"]


def test_start_combat_builds_order_and_authoritative_state(client, monkeypatch):
    roll_initiative = AsyncMock(return_value=[
        {"combatant_id": str(_P2), "combatant_name": "Goblin", "total": 19, "dexterity_modifier": 2, "roll": {}},
        {"combatant_id": str(_P1), "combatant_name": "Aria", "total": 19, "dexterity_modifier": 1, "roll": {}},
    ])
    create_encounter = AsyncMock(return_value={"encounter_id": str(_ENCOUNTER), "round": 1})
    monkeypatch.setattr(clients, "roll_initiative", roll_initiative)
    monkeypatch.setattr(clients, "create_encounter", create_encounter)

    response = client.post(
        "/combat/start",
        json={
            "campaign_id": str(_CAMP),
            "session_id": str(_SESSION),
            "user_id": str(_USER),
            "combatants": [
                {"combatant_id": str(_P1), "name": "Aria", "is_player": True, "current_hp": 20, "max_hp": 24, "armor_class": 16, "speed": 30, "temp_hp": 5},
                {"combatant_id": str(_P2), "name": "Goblin", "is_player": False, "current_hp": 7, "max_hp": 7, "armor_class": 13, "speed": 30},
            ],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["initiative_order"][0]["combatant_id"] == str(_P2)
    create_payload = create_encounter.await_args.args[1]
    assert create_payload["combatant_states"][str(_P1)]["temp_hp"] == 5
    assert create_payload["combatant_states"][str(_P1)]["turn_state"]["action_available"] is True


def test_next_turn_wraps_round_and_resets_turn_state(client, monkeypatch, encounter_dict):
    encounter = deepcopy(encounter_dict)
    encounter["round"] = 1
    encounter["current_turn_index"] = 1
    updated = deepcopy(encounter)
    updated["round"] = 2
    updated["current_turn_index"] = 0
    monkeypatch.setattr(clients, "get_encounter", AsyncMock(return_value=encounter))
    update_encounter = AsyncMock(return_value=updated)
    monkeypatch.setattr(clients, "update_encounter", update_encounter)

    response = client.post("/combat/next-turn", json={"campaign_id": str(_CAMP), "session_id": str(_SESSION), "user_id": str(_USER)})

    assert response.status_code == 200
    payload = update_encounter.await_args.args[1]
    assert payload["combatant_states"][str(_P1)]["turn_state"]["movement_spent"] == 0
    assert response.json()["current_turn_combatant_id"] == str(_P1)


def test_attack_uses_encounter_state_and_handles_concentration(client, monkeypatch, encounter_dict, character_dict):
    encounter = deepcopy(encounter_dict)
    encounter["combatant_states"][str(_P2)]["current_hp"] = 13
    encounter["combatant_states"][str(_P2)]["temp_hp"] = 2
    encounter["combatant_states"][str(_P2)]["concentration"] = "Bless"
    monkeypatch.setattr(clients, "get_encounter", AsyncMock(return_value=encounter))
    monkeypatch.setattr(clients, "resolve_attack", AsyncMock(return_value={"hit": True, "critical_hit": False, "damage_total": 5, "damage_modifier": "none", "effective_ac": 13}))
    monkeypatch.setattr(clients, "resolve_concentration_check", AsyncMock(return_value={"dc": 10, "total": 7, "success": False, "concentration_maintained": False, "roll": {}}))
    update_encounter = AsyncMock(return_value=encounter)
    monkeypatch.setattr(clients, "update_encounter", update_encounter)
    monkeypatch.setattr(clients, "get_character", AsyncMock(return_value=character_dict))
    monkeypatch.setattr(clients, "patch_character", AsyncMock(return_value=character_dict))

    response = client.post(
        "/combat/attack",
        json={
            "campaign_id": str(_CAMP),
            "session_id": str(_SESSION),
            "user_id": str(_USER),
            "attacker_id": str(_P1),
            "target_id": str(_P2),
            "weapon": {"name": "Longsword", "damage_dice": "1d8", "damage_type": "slashing"},
            "expected_updated_at": _NOW.isoformat(),
        },
    )

    assert response.status_code == 200
    patch = update_encounter.await_args.args[1]["combatant_states"][str(_P2)]
    assert patch["temp_hp"] == 0
    assert patch["current_hp"] == 10
    assert patch["concentration"] is None


def test_attack_rejects_out_of_turn(client, monkeypatch, encounter_dict):
    encounter = deepcopy(encounter_dict)
    encounter["current_turn_index"] = 1
    monkeypatch.setattr(clients, "get_encounter", AsyncMock(return_value=encounter))

    response = client.post(
        "/combat/attack",
        json={
            "campaign_id": str(_CAMP),
            "session_id": str(_SESSION),
            "user_id": str(_USER),
            "attacker_id": str(_P1),
            "target_id": str(_P2),
            "weapon": {"name": "Longsword", "damage_dice": "1d8", "damage_type": "slashing"},
            "expected_updated_at": _NOW.isoformat(),
        },
    )

    assert response.status_code == 409
    assert "turn" in response.json()["detail"]


def test_move_tracks_remaining_movement(client, monkeypatch, encounter_dict, character_dict):
    encounter = deepcopy(encounter_dict)
    encounter["combatant_states"][str(_P1)]["turn_state"]["movement_spent"] = 20
    monkeypatch.setattr(clients, "get_encounter", AsyncMock(return_value=encounter))
    monkeypatch.setattr(clients, "validate_movement", AsyncMock(return_value={"valid": True, "effective_speed": 30, "distance_requested": 15, "movement_cost": 15, "rejection_reason": None}))
    update_encounter = AsyncMock(return_value=encounter)
    monkeypatch.setattr(clients, "update_encounter", update_encounter)
    monkeypatch.setattr(clients, "get_character", AsyncMock(return_value=character_dict))
    monkeypatch.setattr(clients, "patch_character", AsyncMock(return_value=character_dict))

    response = client.post(
        "/combat/move",
        json={"campaign_id": str(_CAMP), "session_id": str(_SESSION), "user_id": str(_USER), "combatant_id": str(_P1), "distance_feet": 15},
    )

    assert response.status_code == 200
    assert response.json()["valid"] is False
    update_encounter.assert_not_awaited()


def test_dash_adds_extra_movement_budget(client, monkeypatch, encounter_dict):
    encounter = deepcopy(encounter_dict)
    monkeypatch.setattr(clients, "get_encounter", AsyncMock(return_value=encounter))
    update_encounter = AsyncMock(return_value=encounter)
    monkeypatch.setattr(clients, "update_encounter", update_encounter)

    response = client.post(
        "/combat/dash",
        json={"campaign_id": str(_CAMP), "session_id": str(_SESSION), "user_id": str(_USER), "combatant_id": str(_P1), "expected_updated_at": _NOW.isoformat()},
    )

    assert response.status_code == 200
    patch = update_encounter.await_args.args[1]["combatant_states"][str(_P1)]
    assert patch["turn_state"]["extra_movement_budget"] == 30
    assert response.json()["movement_budget"] == 60


def test_hide_uses_ability_check_and_marks_hidden(client, monkeypatch, encounter_dict):
    monkeypatch.setattr(clients, "get_encounter", AsyncMock(return_value=encounter_dict))
    monkeypatch.setattr(clients, "resolve_ability_check", AsyncMock(return_value={"total": 17, "dc": 15, "success": True}))
    update_encounter = AsyncMock(return_value=encounter_dict)
    monkeypatch.setattr(clients, "update_encounter", update_encounter)

    response = client.post(
        "/combat/hide",
        json={"campaign_id": str(_CAMP), "session_id": str(_SESSION), "user_id": str(_USER), "combatant_id": str(_P1), "dc": 15, "expected_updated_at": _NOW.isoformat()},
    )

    assert response.status_code == 200
    assert response.json()["hidden"] is True
    assert update_encounter.await_args.args[1]["combatant_states"][str(_P1)]["turn_state"]["hidden"] is True


def test_attack_tracks_extra_attack_without_consuming_action_on_first_attack(client, monkeypatch, encounter_dict):
    encounter = deepcopy(encounter_dict)
    encounter["combatant_states"][str(_P1)]["combat_capabilities"] = {"attacks_per_action": 2}
    monkeypatch.setattr(clients, "get_encounter", AsyncMock(return_value=encounter))
    monkeypatch.setattr(clients, "resolve_attack", AsyncMock(return_value={"hit": True, "critical_hit": False, "damage_total": 3, "damage_modifier": "none", "effective_ac": 13}))
    monkeypatch.setattr(clients, "resolve_concentration_check", AsyncMock(return_value={"concentration_maintained": True}))
    update_encounter = AsyncMock(return_value=encounter)
    monkeypatch.setattr(clients, "update_encounter", update_encounter)
    monkeypatch.setattr(clients, "get_character", AsyncMock(return_value=None))

    response = client.post(
        "/combat/attack",
        json={
            "campaign_id": str(_CAMP),
            "session_id": str(_SESSION),
            "user_id": str(_USER),
            "attacker_id": str(_P1),
            "target_id": str(_P2),
            "weapon": {"name": "Longsword", "damage_dice": "1d8", "damage_type": "slashing"},
            "expected_updated_at": _NOW.isoformat(),
        },
    )

    assert response.status_code == 200
    attacker_patch = update_encounter.await_args.args[1]["combatant_states"][str(_P1)]
    assert attacker_patch["turn_state"]["attacks_used_this_action"] == 1
    assert attacker_patch["turn_state"]["action_available"] is True


def test_opportunity_attack_blocked_by_disengage(client, monkeypatch, encounter_dict):
    encounter = deepcopy(encounter_dict)
    encounter["combatant_states"][str(_P2)]["turn_state"]["disengage_active"] = True
    monkeypatch.setattr(clients, "get_encounter", AsyncMock(return_value=encounter))

    response = client.post(
        "/combat/opportunity-attack",
        json={
            "campaign_id": str(_CAMP),
            "session_id": str(_SESSION),
            "user_id": str(_USER),
            "attacker_id": str(_P1),
            "target_id": str(_P2),
            "weapon": {"name": "Longsword", "damage_dice": "1d8", "damage_type": "slashing"},
            "expected_updated_at": _NOW.isoformat(),
        },
    )

    assert response.status_code == 409
    assert "disengage" in response.json()["detail"]


def test_death_save_uses_authoritative_encounter_state(client, monkeypatch, encounter_dict, character_dict):
    encounter = deepcopy(encounter_dict)
    encounter["combatant_states"][str(_P1)]["current_hp"] = 0
    encounter["combatant_states"][str(_P1)]["conditions"] = ["unconscious"]
    encounter["combatant_states"][str(_P1)]["death_saves"] = {"successes": 2, "failures": 0}
    monkeypatch.setattr(clients, "get_encounter", AsyncMock(return_value=encounter))
    monkeypatch.setattr(clients, "resolve_death_save", AsyncMock(return_value={"success": True, "critical_stabilize": True, "critical_failure": False, "new_successes": 0, "new_failures": 0, "stabilized": True, "dead": False}))
    update_encounter = AsyncMock(return_value=encounter)
    monkeypatch.setattr(clients, "update_encounter", update_encounter)
    monkeypatch.setattr(clients, "get_character", AsyncMock(return_value={**character_dict, "current_hp": 0, "conditions": ["unconscious"]}))
    patch_character = AsyncMock(return_value=character_dict)
    monkeypatch.setattr(clients, "patch_character", patch_character)

    response = client.post(
        "/combat/death-save",
        json={"campaign_id": str(_CAMP), "session_id": str(_SESSION), "user_id": str(_USER), "combatant_id": str(_P1)},
    )

    assert response.status_code == 200
    clients.resolve_death_save.assert_awaited_once_with(str(_P1), 2, 0)
    patch = update_encounter.await_args.args[1]["combatant_states"][str(_P1)]
    assert patch["current_hp"] == 1
    assert patch_character.await_args.args[2]["current_hp"] == 1


def test_grapple_applies_condition(client, monkeypatch, encounter_dict):
    monkeypatch.setattr(clients, "get_encounter", AsyncMock(return_value=encounter_dict))
    monkeypatch.setattr(clients, "resolve_grapple", AsyncMock(return_value={"grapple_succeeds": True, "contest": {}}))
    update_encounter = AsyncMock(return_value=encounter_dict)
    monkeypatch.setattr(clients, "update_encounter", update_encounter)
    monkeypatch.setattr(clients, "get_character", AsyncMock(return_value=None))

    response = client.post(
        "/combat/grapple",
        json={"campaign_id": str(_CAMP), "session_id": str(_SESSION), "user_id": str(_USER), "attacker_id": str(_P1), "target_id": str(_P2), "expected_updated_at": _NOW.isoformat()},
    )

    assert response.status_code == 200
    assert response.json()["grapple_succeeds"] is True
    assert "grappled" in update_encounter.await_args.args[1]["combatant_states"][str(_P2)]["conditions"]


def test_spell_cast_consumes_slot_and_sets_concentration(client, monkeypatch, encounter_dict, character_dict):
    encounter = deepcopy(encounter_dict)
    encounter["combatant_states"][str(_P1)]["spell_slots"]["level_1"] = 2
    monkeypatch.setattr(clients, "get_encounter", AsyncMock(return_value=encounter))
    monkeypatch.setattr(clients, "validate_spell_cast", AsyncMock(return_value={"valid": True, "rejection_reason": None, "breaks_concentration": False, "slot_consumed": 1}))
    update_encounter = AsyncMock(return_value=encounter)
    monkeypatch.setattr(clients, "update_encounter", update_encounter)
    monkeypatch.setattr(clients, "get_character", AsyncMock(return_value=character_dict))
    patch_character = AsyncMock(return_value=character_dict)
    monkeypatch.setattr(clients, "patch_character", patch_character)

    response = client.post(
        "/combat/spell-cast",
        json={
            "campaign_id": str(_CAMP),
            "session_id": str(_SESSION),
            "user_id": str(_USER),
            "caster_id": str(_P1),
            "spell_name": "Bless",
            "spell_level": 1,
            "is_concentration": True,
            "expected_updated_at": _NOW.isoformat(),
        },
    )

    assert response.status_code == 200
    payload = update_encounter.await_args.args[1]["combatant_states"][str(_P1)]
    assert payload["spell_slots"]["level_1"] == 1
    assert payload["concentration"] == "Bless"
    assert patch_character.await_args.args[2]["concentration"] == "Bless"


def test_move_syncs_map_token_best_effort_when_position_changes(client, monkeypatch, encounter_dict, character_dict):
    encounter = deepcopy(encounter_dict)
    encounter["map_id"] = str(uuid4())
    monkeypatch.setattr(clients, "get_encounter", AsyncMock(return_value=encounter))
    monkeypatch.setattr(clients, "validate_movement", AsyncMock(return_value={"valid": True, "effective_speed": 30, "distance_requested": 10, "movement_cost": 10, "rejection_reason": None}))
    monkeypatch.setattr(clients, "update_encounter", AsyncMock(return_value=encounter))
    monkeypatch.setattr(clients, "get_character", AsyncMock(return_value=character_dict))
    monkeypatch.setattr(clients, "patch_character", AsyncMock(return_value=character_dict))
    upsert_token = AsyncMock(return_value=None)
    monkeypatch.setattr(clients, "upsert_map_token_best_effort", upsert_token)

    response = client.post(
        "/combat/move",
        json={
            "campaign_id": str(_CAMP),
            "session_id": str(_SESSION),
            "user_id": str(_USER),
            "combatant_id": str(_P1),
            "distance_feet": 10,
            "new_position": {"x": 4, "y": 6, "map_id": encounter["map_id"]},
        },
    )

    assert response.status_code == 200
    upsert_token.assert_awaited_once()


def test_end_combat_deletes_active_encounter(client, monkeypatch, encounter_dict):
    monkeypatch.setattr(clients, "get_encounter", AsyncMock(return_value=encounter_dict))
    delete_encounter = AsyncMock(return_value=True)
    monkeypatch.setattr(clients, "delete_encounter", delete_encounter)

    response = client.delete("/combat/end", params={"campaign_id": str(_CAMP), "session_id": str(_SESSION), "user_id": str(_USER)})

    assert response.status_code == 204
    delete_encounter.assert_awaited_once()
