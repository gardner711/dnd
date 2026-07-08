import pytest
from unittest.mock import patch
from app.grapple import attempt_grapple, attempt_shove
from app.models import (
    AbilityScores,
    CombatantStats,
    GrappleAttemptRequest,
    RollResult,
    ShoveAttemptRequest,
    Skill,
)


def _fighter(**kwargs) -> CombatantStats:
    defaults = dict(
        id="fighter-1",
        name="Fighter",
        ability_scores=AbilityScores(strength=20),  # +5
        proficiency_bonus=4,
        proficient_skills=[Skill.ATHLETICS],
    )
    defaults.update(kwargs)
    return CombatantStats(**defaults)


def _goblin(**kwargs) -> CombatantStats:
    defaults = dict(
        id="goblin-1",
        name="Goblin",
        ability_scores=AbilityScores(strength=8),  # -1
        proficiency_bonus=2,
    )
    defaults.update(kwargs)
    return CombatantStats(**defaults)


# ── Grapple ─────────────────────────────────────────────────────────────────

def test_grapple_result_has_contest_rolls():
    result = attempt_grapple(GrappleAttemptRequest(attacker=_fighter(), target=_goblin()))
    assert result.contest.attacker_roll is not None
    assert result.contest.defender_roll is not None


def test_strong_fighter_usually_beats_weak_goblin():
    """Fighter +9 (5+4) vs goblin -1: attacker wins most contests."""
    wins = sum(
        1 for _ in range(100)
        if attempt_grapple(GrappleAttemptRequest(attacker=_fighter(), target=_goblin())).grapple_succeeds
    )
    assert wins >= 75


def test_grapple_succeeds_matches_contest_attacker_wins():
    result = attempt_grapple(GrappleAttemptRequest(attacker=_fighter(), target=_goblin()))
    assert result.grapple_succeeds == result.contest.attacker_wins


def test_tie_goes_to_defender():
    """Equal totals must not give the win to the attacker (PHB p.195)."""
    fixed = RollResult(notation="1d20", dice_values=[10], modifier=0, total=10, expression="10")
    import app.dice as dice_module
    # Both get +0 modifier (STR 10, no proficiency) → tie on fixed roll of 10
    balanced = CombatantStats(
        id="a", name="A",
        ability_scores=AbilityScores(strength=10),
        proficiency_bonus=2,
    )
    with patch.object(dice_module, "roll", return_value=fixed):
        result = attempt_grapple(GrappleAttemptRequest(attacker=balanced, target=balanced))
    assert not result.grapple_succeeds
    assert not result.contest.attacker_wins


def test_grapple_with_acrobatics_defender():
    """Defender using Acrobatics should still produce a valid result."""
    result = attempt_grapple(GrappleAttemptRequest(
        attacker=_fighter(), target=_goblin(), defender_uses_acrobatics=True,
    ))
    assert result.contest.defender_roll is not None


# ── Shove ────────────────────────────────────────────────────────────────────

def test_shove_knock_prone_result():
    result = attempt_shove(ShoveAttemptRequest(
        attacker=_fighter(), target=_goblin(), shove_type="knock_prone",
    ))
    assert result.shove_type == "knock_prone"


def test_shove_push_away_result():
    result = attempt_shove(ShoveAttemptRequest(
        attacker=_fighter(), target=_goblin(), shove_type="push_away",
    ))
    assert result.shove_type == "push_away"


def test_shove_succeeds_matches_contest():
    result = attempt_shove(ShoveAttemptRequest(attacker=_fighter(), target=_goblin()))
    assert result.shove_succeeds == result.contest.attacker_wins


def test_strong_fighter_usually_shoves_goblin():
    wins = sum(
        1 for _ in range(100)
        if attempt_shove(ShoveAttemptRequest(attacker=_fighter(), target=_goblin())).shove_succeeds
    )
    assert wins >= 75
