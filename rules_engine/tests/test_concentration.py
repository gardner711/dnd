import pytest
from app.concentration import concentration_check
from app.models import AbilityScore, AbilityScores, ConcentrationCheckRequest, CombatantStats


def _wizard(**kwargs) -> CombatantStats:
    defaults = dict(
        id="wizard-1",
        name="Wizard",
        ability_scores=AbilityScores(constitution=14),  # +2 modifier
        proficiency_bonus=4,
        proficient_saving_throws=[AbilityScore.CONSTITUTION],
    )
    defaults.update(kwargs)
    return CombatantStats(**defaults)


def test_dc_is_half_damage_when_above_10():
    result = concentration_check(ConcentrationCheckRequest(caster=_wizard(), damage_taken=30))
    assert result.dc == 15  # 30 // 2


def test_dc_is_10_when_half_damage_below_threshold():
    result = concentration_check(ConcentrationCheckRequest(caster=_wizard(), damage_taken=6))
    assert result.dc == 10  # max(10, 3) = 10


def test_dc_is_10_for_minimum_damage():
    result = concentration_check(ConcentrationCheckRequest(caster=_wizard(), damage_taken=1))
    assert result.dc == 10


def test_dc_boundary_at_20_damage():
    result = concentration_check(ConcentrationCheckRequest(caster=_wizard(), damage_taken=20))
    assert result.dc == 10  # max(10, 10) = 10


def test_dc_21_damage_gives_dc_10():
    result = concentration_check(ConcentrationCheckRequest(caster=_wizard(), damage_taken=21))
    assert result.dc == 10  # max(10, 10) = 10 (floor of 21//2=10)


def test_dc_22_damage_gives_dc_11():
    result = concentration_check(ConcentrationCheckRequest(caster=_wizard(), damage_taken=22))
    assert result.dc == 11


def test_always_fails_dc_30():
    # damage_taken=60 → dc=30; wizard max roll = 20+2+4=26 < 30
    results = [
        concentration_check(ConcentrationCheckRequest(caster=_wizard(), damage_taken=60))
        for _ in range(20)
    ]
    assert all(not r.concentration_maintained for r in results)


def test_always_passes_dc_10_with_high_bonus():
    # dc=10, CON +2 + prof +4 = +6 → minimum 1+6=7 < 10 fails sometimes
    # Use a wizard with CON 20 (+5) and prof +4 = +9, minimum roll 1+9=10
    supreme_wizard = _wizard(
        ability_scores=AbilityScores(constitution=20),  # +5
        proficiency_bonus=4,
    )
    results = [
        concentration_check(ConcentrationCheckRequest(caster=supreme_wizard, damage_taken=1))
        for _ in range(30)
    ]
    assert all(r.concentration_maintained for r in results)


def test_result_contains_roll_and_total():
    result = concentration_check(ConcentrationCheckRequest(caster=_wizard(), damage_taken=10))
    assert result.roll is not None
    assert isinstance(result.total, int)
    assert result.success == result.concentration_maintained
