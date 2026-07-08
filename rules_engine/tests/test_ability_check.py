import pytest
from app.ability_check import check, saving_throw
from app.models import (
    AbilityCheckRequest,
    AbilityScore,
    AbilityScores,
    AdvantageState,
    Condition,
    CombatantStats,
    SavingThrowRequest,
    Skill,
)


def _combatant(**kwargs) -> CombatantStats:
    defaults = dict(
        id="test-1",
        name="Test Character",
        ability_scores=AbilityScores(
            strength=16,   # +3
            dexterity=14,  # +2
            constitution=14,
            intelligence=10,
            wisdom=12,
            charisma=8,
        ),
        proficiency_bonus=2,
    )
    defaults.update(kwargs)
    return CombatantStats(**defaults)


# ── Ability Checks ──────────────────────────────────────────────────────────

def test_check_dc_1_always_succeeds():
    result = check(AbilityCheckRequest(
        combatant=_combatant(),
        ability=AbilityScore.STRENGTH,
        dc=1,
    ))
    assert result.success


def test_check_dc_30_always_fails():
    result = check(AbilityCheckRequest(
        combatant=_combatant(),
        ability=AbilityScore.STRENGTH,
        dc=30,
    ))
    assert not result.success


def test_check_includes_ability_modifier():
    combatant = _combatant()
    result = check(AbilityCheckRequest(
        combatant=combatant,
        ability=AbilityScore.STRENGTH,
        dc=1,
    ))
    assert result.ability_modifier == 3  # (16-10)//2


def test_check_proficiency_applied_for_skill():
    combatant = _combatant(proficient_skills=[Skill.ATHLETICS], proficiency_bonus=3)
    result = check(AbilityCheckRequest(
        combatant=combatant,
        ability=AbilityScore.STRENGTH,
        skill=Skill.ATHLETICS,
        dc=1,
    ))
    assert result.proficiency_applied == 3


def test_check_no_proficiency_without_training():
    combatant = _combatant()  # no proficient_skills
    result = check(AbilityCheckRequest(
        combatant=combatant,
        ability=AbilityScore.STRENGTH,
        skill=Skill.ATHLETICS,
        dc=1,
    ))
    assert result.proficiency_applied == 0


def test_check_expertise_doubles_proficiency():
    combatant = _combatant(expertise_skills=[Skill.ATHLETICS], proficiency_bonus=3)
    result = check(AbilityCheckRequest(
        combatant=combatant,
        ability=AbilityScore.STRENGTH,
        skill=Skill.ATHLETICS,
        dc=1,
    ))
    assert result.proficiency_applied == 6


# ── Saving Throws ───────────────────────────────────────────────────────────

def test_save_dc_1_always_succeeds():
    result = saving_throw(SavingThrowRequest(
        combatant=_combatant(),
        ability=AbilityScore.WISDOM,
        dc=1,
    ))
    assert result.success


def test_save_proficiency_applied():
    combatant = _combatant(
        proficient_saving_throws=[AbilityScore.CONSTITUTION],
        proficiency_bonus=4,
    )
    result = saving_throw(SavingThrowRequest(
        combatant=combatant,
        ability=AbilityScore.CONSTITUTION,
        dc=1,
    ))
    assert result.proficiency_applied == 4
    assert result.success


def test_save_auto_fail_str_when_paralyzed():
    combatant = _combatant(conditions=[Condition.PARALYZED])
    result = saving_throw(SavingThrowRequest(
        combatant=combatant,
        ability=AbilityScore.STRENGTH,
        dc=5,
    ))
    assert not result.success


def test_save_auto_fail_dex_when_unconscious():
    combatant = _combatant(conditions=[Condition.UNCONSCIOUS])
    result = saving_throw(SavingThrowRequest(
        combatant=combatant,
        ability=AbilityScore.DEXTERITY,
        dc=5,
    ))
    assert not result.success


def test_save_constitution_not_auto_fail_when_paralyzed():
    """Paralyzed only auto-fails STR and DEX saves, not CON."""
    combatant = _combatant(conditions=[Condition.PARALYZED])
    result = saving_throw(SavingThrowRequest(
        combatant=combatant,
        ability=AbilityScore.CONSTITUTION,
        dc=1,
    ))
    assert result.success
