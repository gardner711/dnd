"""Dice rolling — wraps the d20 library for standard D&D 5e notation.

Supports: 1d20, 2d6+3, 4d6kh3 (drop lowest), 2d20kh1 (advantage),
          2d20kl1 (disadvantage), and any expression the d20 library accepts.
"""
from __future__ import annotations

import d20 as _d20

from app.models import AdvantageState, RollResult


def roll(notation: str) -> RollResult:
    """Roll dice and return a fully auditable RollResult."""
    result = _d20.roll(notation)
    die_values = _collect_die_values(result.expr)
    return RollResult(
        notation=notation,
        dice_values=die_values,
        modifier=0,  # full breakdown is in `expression`; modifier extraction
                     # is unreliable for kh/kl selectors, so left as 0.
        total=result.total,
        expression=str(result),
    )


def roll_d20(advantage_state: AdvantageState = AdvantageState.NORMAL) -> RollResult:
    """Roll a d20 respecting advantage or disadvantage (PHB p.173)."""
    match advantage_state:
        case AdvantageState.ADVANTAGE:
            return roll("2d20kh1")
        case AdvantageState.DISADVANTAGE:
            return roll("2d20kl1")
        case _:
            return roll("1d20")


def _collect_die_values(expr) -> list[int]:
    """Recursively extract all kept die face values from an expression tree.

    d20 AST structure (v1.1.x):
      Expression.roll → inner node (Dice | BinOp | Literal | ...)
      Dice.keptset    → list of Die objects that contributed to the total
      BinOp.left/.right → child nodes
    """
    if isinstance(expr, _d20.Expression):
        return _collect_die_values(expr.roll)
    if isinstance(expr, _d20.Dice):
        return [int(die.total) for die in expr.keptset]
    results: list[int] = []
    left = getattr(expr, "left", None)
    right = getattr(expr, "right", None)
    if left is not None:
        results.extend(_collect_die_values(left))
    if right is not None:
        results.extend(_collect_die_values(right))
    return results
