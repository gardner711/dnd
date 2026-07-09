"""Route tests — all database operations mocked, no real I/O."""
import pytest
from datetime import datetime, UTC, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

import app.database as db_module
import app.event_log as event_log_module
from app.dependencies import get_db_conn
from app.main import app
from app.models import (
    CharacterState, AbilityScores, SpellSlots, DeathSaves, CurrencyPurse,
    DispositionRecord, DispositionsResponse, EncounterState, InitiativeEntry,
    CombatantState, FactionStandingRecord,
)

_NOW     = datetime.now(UTC)
_CAMP    = uuid4()
_CHAR    = uuid4()
_USER    = uuid4()
_NPC     = uuid4()
_ENC     = uuid4()


def _char_state(**kw) -> CharacterState:
    return CharacterState(**{
        "character_id": _CHAR, "campaign_id": _CAMP, "user_id": _USER,
        "name": "Aria", "current_hp": 20, "max_hp": 28,
        "ability_scores": AbilityScores(), "spell_slots": SpellSlots(),
        "death_saves": DeathSaves(), "currency": CurrencyPurse(),
        "updated_at": _NOW, **kw,
    })


def _encounter_state() -> EncounterState:
    return EncounterState(
        encounter_id=_ENC, campaign_id=_CAMP, map_id=None,
        round=1, current_turn_index=0,
        initiative_order=[
            InitiativeEntry(combatant_id=_CHAR, name="Aria", total=18, is_player=True),
        ],
        combatant_states={str(_CHAR): CombatantState(
            combatant_id=_CHAR, name="Aria", is_player=True, current_hp=20, max_hp=28,
        )},
        active=True, started_at=_NOW, updated_at=_NOW,
    )


@pytest.fixture
def mock_conn():
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)
    # asyncpg connection.transaction() must return an async context manager
    txn_cm = MagicMock()
    txn_cm.__aenter__ = AsyncMock(return_value=None)
    txn_cm.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn_cm)
    return conn


@pytest.fixture
def mock_pool(mock_conn):
    pool = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__  = AsyncMock(return_value=None)
    pool.acquire  = MagicMock(return_value=cm)
    return pool


@pytest.fixture
def client(mock_pool, mock_conn, monkeypatch):
    monkeypatch.setattr(db_module, "get_pool",        AsyncMock(return_value=mock_pool))
    monkeypatch.setattr(db_module, "run_migrations",  AsyncMock(return_value=None))
    monkeypatch.setattr(db_module, "close_pool",      AsyncMock(return_value=None))
    monkeypatch.setattr(event_log_module, "emit",     AsyncMock(return_value=None))

    async def override_db():
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_db
    with TestClient(app) as c:
        yield c, mock_conn
    app.dependency_overrides.clear()


# ── Health ──────────────────────────────────────────────────────────────────

def test_health_ok(client):
    c, _ = client
    resp = c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_degraded_db_down(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_pool", AsyncMock(side_effect=Exception("db down")))
    resp = c.get("/health")
    assert resp.status_code == 503
    assert resp.json()["checks"]["database"] is False


# ── Characters ──────────────────────────────────────────────────────────────

def test_get_character_found(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_character", AsyncMock(return_value=_char_state()))
    resp = c.get(f"/characters/{_CHAR}?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Aria"


def test_get_character_not_found(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_character", AsyncMock(return_value=None))
    resp = c.get(f"/characters/{_CHAR}?campaign_id={_CAMP}")
    assert resp.status_code == 404


def test_get_character_requires_campaign_id(client):
    c, _ = client
    resp = c.get(f"/characters/{_CHAR}")
    assert resp.status_code == 422


def test_put_character_returns_201(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "upsert_character", AsyncMock(return_value=_char_state()))
    payload = {
        "campaign_id": str(_CAMP), "user_id": str(_USER),
        "name": "Aria", "current_hp": 20, "max_hp": 28,
    }
    resp = c.put(f"/characters/{_CHAR}", json=payload)
    assert resp.status_code == 201


def test_patch_character_applies_update(client, monkeypatch):
    c, _ = client
    updated = _char_state(current_hp=15)
    monkeypatch.setattr(db_module, "get_character_for_update", AsyncMock(return_value=_char_state()))
    monkeypatch.setattr(db_module, "upsert_character", AsyncMock(return_value=updated))
    resp = c.patch(f"/characters/{_CHAR}?campaign_id={_CAMP}", json={"current_hp": 15})
    assert resp.status_code == 200
    assert resp.json()["current_hp"] == 15


def test_patch_character_emits_event_on_hp_change(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_character_for_update", AsyncMock(return_value=_char_state(current_hp=20)))
    monkeypatch.setattr(db_module, "upsert_character", AsyncMock(return_value=_char_state(current_hp=5)))
    c.patch(f"/characters/{_CHAR}?campaign_id={_CAMP}", json={
        "current_hp": 5,
        "event_meta": {"session_id": "sess-1", "user_id": str(_USER)},
    })
    event_log_module.emit.assert_called_once()
    call_kwargs = event_log_module.emit.call_args[1]
    assert call_kwargs["event_type"] == "combat.state_changed"


def test_patch_character_no_event_without_meta(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_character_for_update", AsyncMock(return_value=_char_state(current_hp=20)))
    monkeypatch.setattr(db_module, "upsert_character", AsyncMock(return_value=_char_state(current_hp=5)))
    c.patch(f"/characters/{_CHAR}?campaign_id={_CAMP}", json={"current_hp": 5})
    event_log_module.emit.assert_not_called()


def test_delete_character_204(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "delete_character", AsyncMock(return_value=True))
    resp = c.delete(f"/characters/{_CHAR}?campaign_id={_CAMP}")
    assert resp.status_code == 204


def test_delete_character_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "delete_character", AsyncMock(return_value=False))
    resp = c.delete(f"/characters/{_CHAR}?campaign_id={_CAMP}")
    assert resp.status_code == 404


# ── NPC Dispositions ────────────────────────────────────────────────────────

def test_get_dispositions(client, monkeypatch):
    c, _ = client
    resp_data = DispositionsResponse(npc_id=_NPC, campaign_id=_CAMP, dispositions=[])
    monkeypatch.setattr(db_module, "get_npc_dispositions", AsyncMock(return_value=resp_data))
    resp = c.get(f"/npcs/{_NPC}/dispositions?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert resp.json()["dispositions"] == []


def test_update_disposition(client, monkeypatch):
    c, _ = client
    record = DispositionRecord(npc_id=_NPC, campaign_id=_CAMP, character_id=_CHAR, score=75, updated_at=_NOW)
    monkeypatch.setattr(db_module, "upsert_npc_disposition", AsyncMock(return_value=record))
    resp = c.patch(f"/npcs/{_NPC}/dispositions?campaign_id={_CAMP}", json={
        "character_id": str(_CHAR), "score": 75,
    })
    assert resp.status_code == 200
    assert resp.json()["score"] == 75


def test_update_disposition_emits_event(client, monkeypatch):
    c, _ = client
    record = DispositionRecord(npc_id=_NPC, campaign_id=_CAMP, character_id=_CHAR, score=20, updated_at=_NOW)
    monkeypatch.setattr(db_module, "upsert_npc_disposition", AsyncMock(return_value=record))
    c.patch(f"/npcs/{_NPC}/dispositions?campaign_id={_CAMP}", json={
        "character_id": str(_CHAR), "score": 20,
        "event_meta": {"session_id": "sess-1", "user_id": str(_USER)},
    })
    event_log_module.emit.assert_called_once()
    assert event_log_module.emit.call_args[1]["event_type"] == "npc.disposition_changed"


# ── World Flags ─────────────────────────────────────────────────────────────

def test_get_flags(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_world_flags", AsyncMock(return_value={"boss_defeated": True}))
    resp = c.get(f"/world/flags?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert resp.json()["flags"]["boss_defeated"] is True


def test_patch_flags(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "upsert_world_flags", AsyncMock(return_value={"boss_defeated": True}))
    resp = c.patch(f"/world/flags?campaign_id={_CAMP}", json={"flags": {"boss_defeated": True}})
    assert resp.status_code == 200


def test_delete_flag_204(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "delete_world_flag", AsyncMock(return_value=True))
    resp = c.delete(f"/world/flags/boss_defeated?campaign_id={_CAMP}")
    assert resp.status_code == 204


def test_delete_flag_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "delete_world_flag", AsyncMock(return_value=False))
    resp = c.delete(f"/world/flags/missing_key?campaign_id={_CAMP}")
    assert resp.status_code == 404


# ── Encounter ───────────────────────────────────────────────────────────────

def test_get_encounter_found(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_encounter", AsyncMock(return_value=_encounter_state()))
    resp = c.get(f"/encounter?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert resp.json()["round"] == 1


def test_get_encounter_not_found(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_encounter", AsyncMock(return_value=None))
    resp = c.get(f"/encounter?campaign_id={_CAMP}")
    assert resp.status_code == 404


def test_create_encounter_201(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "create_encounter", AsyncMock(return_value=_encounter_state()))
    payload = {
        "campaign_id": str(_CAMP),
        "initiative_order": [{"combatant_id": str(_CHAR), "name": "Aria", "total": 18, "is_player": True}],
        "combatant_states": {str(_CHAR): {
            "combatant_id": str(_CHAR), "name": "Aria", "is_player": True,
            "current_hp": 20, "max_hp": 28,
        }},
    }
    resp = c.put("/encounter", json=payload)
    assert resp.status_code == 201


def test_update_encounter_conflict_409(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "update_encounter", AsyncMock(return_value=None))
    resp = c.patch(f"/encounter?campaign_id={_CAMP}", json={
        "round": 2,
        "expected_updated_at": _NOW.isoformat(),
    })
    assert resp.status_code == 409


def test_update_encounter_success(client, monkeypatch):
    c, _ = client
    enc = _encounter_state()
    enc.round = 2
    monkeypatch.setattr(db_module, "update_encounter", AsyncMock(return_value=enc))
    resp = c.patch(f"/encounter?campaign_id={_CAMP}", json={
        "round": 2,
        "expected_updated_at": _NOW.isoformat(),
    })
    assert resp.status_code == 200
    assert resp.json()["round"] == 2


def test_delete_encounter_204(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "delete_encounter", AsyncMock(return_value=True))
    resp = c.delete(f"/encounter?campaign_id={_CAMP}")
    assert resp.status_code == 204


# ── Factions ────────────────────────────────────────────────────────────────

def test_get_factions(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_factions", AsyncMock(return_value=[]))
    resp = c.get(f"/factions?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert resp.json()["factions"] == []


def test_patch_faction(client, monkeypatch):
    c, _ = client
    record = FactionStandingRecord(campaign_id=_CAMP, faction_id="thieves_guild", standing=-20, updated_at=_NOW)
    monkeypatch.setattr(db_module, "upsert_faction", AsyncMock(return_value=record))
    resp = c.patch(f"/factions/thieves_guild?campaign_id={_CAMP}", json={"standing": -20})
    assert resp.status_code == 200
    assert resp.json()["standing"] == -20


# ── Fix 1: GET /characters list ─────────────────────────────────────────────

def test_list_characters_returns_200(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "list_characters", AsyncMock(return_value=[_char_state()]))
    resp = c.get(f"/characters?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["name"] == "Aria"


def test_list_characters_requires_campaign_id(client):
    c, _ = client
    resp = c.get("/characters")
    assert resp.status_code == 422


def test_list_characters_empty(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "list_characters", AsyncMock(return_value=[]))
    resp = c.get(f"/characters?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert resp.json() == []


# ── Fix 2: atomic character PATCH with version check ──────────────────────

def test_patch_character_version_conflict_409(client, monkeypatch):
    """expected_updated_at mismatch must return 409, not silently overwrite."""
    c, _ = client
    stale_ts = datetime(2020, 1, 1, tzinfo=UTC)
    # DB has _NOW as updated_at; client sends 2020 — mismatch should 409
    monkeypatch.setattr(db_module, "get_character_for_update", AsyncMock(return_value=_char_state()))
    resp = c.patch(f"/characters/{_CHAR}?campaign_id={_CAMP}", json={
        "current_hp": 5,
        "expected_updated_at": stale_ts.isoformat(),
    })
    assert resp.status_code == 409


def test_patch_character_no_version_check_succeeds(client, monkeypatch):
    """Without expected_updated_at, patch proceeds regardless of updated_at."""
    c, _ = client
    updated = _char_state(current_hp=15)
    monkeypatch.setattr(db_module, "get_character_for_update", AsyncMock(return_value=_char_state()))
    monkeypatch.setattr(db_module, "upsert_character", AsyncMock(return_value=updated))
    resp = c.patch(f"/characters/{_CHAR}?campaign_id={_CAMP}", json={"current_hp": 15})
    assert resp.status_code == 200


# ── Fix 3: GET /world/flags/{key} ──────────────────────────────────────────

def test_get_single_flag_found(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_world_flag", AsyncMock(return_value=True))
    resp = c.get(f"/world/flags/boss_defeated?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert resp.json()["key"] == "boss_defeated"
    assert resp.json()["value"] is True


def test_get_single_flag_not_found(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(db_module, "get_world_flag", AsyncMock(return_value=None))
    resp = c.get(f"/world/flags/missing?campaign_id={_CAMP}")
    assert resp.status_code == 404


def test_get_single_flag_requires_campaign_id(client):
    c, _ = client
    resp = c.get("/world/flags/boss_defeated")
    assert resp.status_code == 422


def test_get_all_flags_still_works(client, monkeypatch):
    """GET /world/flags (no key) must not be shadowed by /world/flags/{key}."""
    c, _ = client
    monkeypatch.setattr(db_module, "get_world_flags", AsyncMock(return_value={"week": 3}))
    resp = c.get(f"/world/flags?campaign_id={_CAMP}")
    assert resp.status_code == 200
    assert resp.json()["flags"]["week"] == 3


# ── Fix 4: encounter combatant partial update (merge, not replace) ─────────

def test_patch_encounter_partial_combatant_update(client, monkeypatch):
    """Only specified combatants are sent to DB; SQL || merge preserves the rest."""
    c, _ = client
    enc = _encounter_state()
    monkeypatch.setattr(db_module, "update_encounter", AsyncMock(return_value=enc))
    resp = c.patch(f"/encounter?campaign_id={_CAMP}", json={
        "expected_updated_at": _NOW.isoformat(),
        "combatant_states": {
            str(_CHAR): {
                "combatant_id": str(_CHAR), "name": "Aria", "is_player": True,
                "current_hp": 5, "max_hp": 28,
            }
        },
    })
    assert resp.status_code == 200
    db_module.update_encounter.assert_called_once()
    # The route passes only the updated combatant to the DB;
    # the SQL-level || merge is responsible for preserving existing combatants
    positional_args = db_module.update_encounter.call_args[0]
    combatant_json = positional_args[5]   # 6th arg: combatant_states dict
    assert str(_CHAR) in combatant_json
