import pytest
from app import dice
from app.models import AdvantageState, RollResult


def test_roll_returns_roll_result():
    result = dice.roll("1d20")
    assert isinstance(result, RollResult)


def test_roll_d6_within_range():
    for _ in range(50):
        result = dice.roll("1d6")
        assert 1 <= result.total <= 6


def test_roll_notation_stored():
    result = dice.roll("2d6+3")
    assert result.notation == "2d6+3"


def test_roll_total_matches_expression():
    result = dice.roll("1d8+2")
    assert result.expression != ""
    assert result.total >= 3  # minimum 1+2


def test_roll_advantage_skews_high():
    """Over 100 trials, advantage rolls should average well above 10.5."""
    results = [dice.roll_d20(AdvantageState.ADVANTAGE).total for _ in range(100)]
    assert sum(results) / 100 > 12, "Advantage should produce above-average d20 results"


def test_roll_disadvantage_skews_low():
    results = [dice.roll_d20(AdvantageState.DISADVANTAGE).total for _ in range(100)]
    assert sum(results) / 100 < 9, "Disadvantage should produce below-average d20 results"


def test_roll_normal_d20_within_range():
    result = dice.roll_d20(AdvantageState.NORMAL)
    assert 1 <= result.total <= 20


def test_keep_highest_3_of_4d6():
    for _ in range(30):
        result = dice.roll("4d6kh3")
        assert 3 <= result.total <= 18


def test_dice_values_populated():
    result = dice.roll("3d6")
    assert len(result.dice_values) == 3
    for v in result.dice_values:
        assert 1 <= v <= 6
