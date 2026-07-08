import pytest
from app.conditions import CONDITION_EFFECTS, EXHAUSTION_THRESHOLDS, resolve_advantage_state
from app.models import AdvantageState, Condition


def test_all_conditions_have_an_entry():
    for condition in Condition:
        assert condition in CONDITION_EFFECTS, f"{condition.value} missing from CONDITION_EFFECTS"


def test_paralyzed_auto_fails_str_and_dex():
    effect = CONDITION_EFFECTS[Condition.PARALYZED]
    assert effect.str_save_auto_fail
    assert effect.dex_save_auto_fail


def test_paralyzed_auto_crit_within_5ft():
    assert CONDITION_EFFECTS[Condition.PARALYZED].auto_crit_within_5ft


def test_unconscious_auto_crit_within_5ft():
    assert CONDITION_EFFECTS[Condition.UNCONSCIOUS].auto_crit_within_5ft


def test_blinded_attack_disadvantage_and_attackers_have_advantage():
    effect = CONDITION_EFFECTS[Condition.BLINDED]
    assert effect.attack_roll_disadvantage
    assert effect.attacks_against_have_advantage


def test_invisible_attack_advantage_and_attackers_have_disadvantage():
    effect = CONDITION_EFFECTS[Condition.INVISIBLE]
    assert effect.attack_roll_advantage
    assert effect.attacks_against_have_disadvantage


def test_grappled_sets_speed_zero():
    assert CONDITION_EFFECTS[Condition.GRAPPLED].speed_zero


def test_restrained_disadvantage_not_auto_fail_dex_save():
    effect = CONDITION_EFFECTS[Condition.RESTRAINED]
    assert effect.dex_save_disadvantage
    assert not effect.dex_save_auto_fail  # RESTRAINED is disadvantage, not auto-fail


def test_petrified_has_damage_resistance_and_immunities():
    effect = CONDITION_EFFECTS[Condition.PETRIFIED]
    assert effect.all_damage_resistance
    assert effect.immune_poison_condition
    assert effect.immune_disease


def test_incapacitated_blocks_actions():
    assert CONDITION_EFFECTS[Condition.INCAPACITATED].incapacitated


def test_stunned_is_incapacitated_and_speed_zero():
    effect = CONDITION_EFFECTS[Condition.STUNNED]
    assert effect.incapacitated
    assert effect.speed_zero


def test_exhaustion_has_all_6_levels():
    assert len(EXHAUSTION_THRESHOLDS) == 6
    assert set(EXHAUSTION_THRESHOLDS.keys()) == {1, 2, 3, 4, 5, 6}


# ── resolve_advantage_state ─────────────────────────────────────────────────

def test_advantage_and_disadvantage_cancel_to_normal():
    result = resolve_advantage_state(
        base_state=AdvantageState.ADVANTAGE,
        conditions=[Condition.BLINDED],   # imposes attack_roll_disadvantage
        check_type="attack",
    )
    assert result == AdvantageState.NORMAL


def test_no_conditions_preserves_base_state():
    result = resolve_advantage_state(
        base_state=AdvantageState.ADVANTAGE,
        conditions=[],
        check_type="attack",
    )
    assert result == AdvantageState.ADVANTAGE


def test_exhaustion_1_imposes_disadvantage_on_ability_checks():
    result = resolve_advantage_state(
        base_state=AdvantageState.NORMAL,
        conditions=[],
        check_type="ability",
        exhaustion_level=1,
    )
    assert result == AdvantageState.DISADVANTAGE


def test_exhaustion_3_imposes_disadvantage_on_attacks():
    result = resolve_advantage_state(
        base_state=AdvantageState.NORMAL,
        conditions=[],
        check_type="attack",
        exhaustion_level=3,
    )
    assert result == AdvantageState.DISADVANTAGE
