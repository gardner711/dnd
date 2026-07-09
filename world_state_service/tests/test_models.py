import pytest
from pydantic import ValidationError
from uuid import uuid4
from app.models import (
    AbilityScores, CharacterCreate, CharacterUpdate, DeathSaves,
    DispositionUpdate, EncounterUpdate, EventMeta, FactionUpdate,
    SpellSlots, WorldFlagsUpdate,
)
from datetime import datetime, UTC


def _char_create(**kw) -> CharacterCreate:
    return CharacterCreate(**{
        "campaign_id": uuid4(), "user_id": uuid4(),
        "name": "Aria", "current_hp": 20, "max_hp": 28,
        **kw,
    })


# ── AbilityScores ─────────────────────────────────────────────────────────

def test_ability_scores_defaults():
    a = AbilityScores()
    assert a.strength == 10

def test_ability_scores_custom():
    a = AbilityScores(strength=18, dexterity=14)
    assert a.strength == 18

# ── CharacterCreate ───────────────────────────────────────────────────────

def test_character_create_valid():
    c = _char_create()
    assert c.name == "Aria"
    assert c.level == 1

def test_character_create_max_hp_minimum():
    with pytest.raises(ValidationError):
        _char_create(max_hp=0)

def test_character_create_level_range():
    with pytest.raises(ValidationError):
        _char_create(level=21)
    with pytest.raises(ValidationError):
        _char_create(level=0)

def test_character_create_exhaustion_range():
    with pytest.raises(ValidationError):
        _char_create(exhaustion_level=7)

# ── CharacterUpdate ───────────────────────────────────────────────────────

def test_character_update_all_optional():
    u = CharacterUpdate()
    assert u.current_hp is None
    assert u.conditions is None

def test_character_update_partial():
    u = CharacterUpdate(current_hp=15, conditions=["poisoned"])
    assert u.current_hp == 15
    assert u.conditions == ["poisoned"]

def test_character_update_exclude_unset():
    u = CharacterUpdate(current_hp=5)
    d = u.model_dump(exclude_unset=True)
    assert "current_hp" in d
    assert "conditions" not in d

# ── DispositionUpdate ─────────────────────────────────────────────────────

def test_disposition_score_bounds():
    with pytest.raises(ValidationError):
        DispositionUpdate(character_id=uuid4(), score=101)
    with pytest.raises(ValidationError):
        DispositionUpdate(character_id=uuid4(), score=-1)

def test_disposition_valid():
    d = DispositionUpdate(character_id=uuid4(), score=75)
    assert d.score == 75

# ── EncounterUpdate ───────────────────────────────────────────────────────

def test_encounter_update_requires_expected_updated_at():
    with pytest.raises(ValidationError):
        EncounterUpdate()  # missing required expected_updated_at

def test_encounter_update_round_minimum():
    with pytest.raises(ValidationError):
        EncounterUpdate(round=0, expected_updated_at=datetime.now(UTC))

# ── FactionUpdate ─────────────────────────────────────────────────────────

def test_faction_standing_bounds():
    with pytest.raises(ValidationError):
        FactionUpdate(standing=101)
    with pytest.raises(ValidationError):
        FactionUpdate(standing=-101)

# ── WorldFlagsUpdate ──────────────────────────────────────────────────────

def test_world_flags_accepts_any_json_values():
    u = WorldFlagsUpdate(flags={
        "boss_defeated": True,
        "town_name": "Millhaven",
        "week": 3,
        "nested": {"key": "val"},
    })
    assert u.flags["boss_defeated"] is True
    assert u.flags["week"] == 3
