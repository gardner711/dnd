"""Loader for 5e SRD reference data (spells, monsters, equipment, classes).

Data source: https://github.com/bagelbits/5e-database
Place the extracted JSON files in the rules_engine/data/ directory.

All lookups are case-insensitive and cache their results after the first load.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"


@lru_cache(maxsize=None)
def _load(filename: str) -> list[dict]:
    path = _DATA_DIR / filename
    if not path.exists():
        logger.warning(
            "SRD data file not found: %s — download from https://github.com/bagelbits/5e-database",
            path,
        )
        return []
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def get_spell(name: str) -> dict | None:
    """Look up a spell by name (case-insensitive)."""
    target = name.lower()
    return next(
        (s for s in _load("spells.json") if s.get("name", "").lower() == target),
        None,
    )


def get_monster(name: str) -> dict | None:
    """Look up a monster stat block by name (case-insensitive)."""
    target = name.lower()
    return next(
        (m for m in _load("monsters.json") if m.get("name", "").lower() == target),
        None,
    )


def get_equipment(name: str) -> dict | None:
    """Look up a weapon or equipment item by name (case-insensitive)."""
    target = name.lower()
    return next(
        (e for e in _load("equipment.json") if e.get("name", "").lower() == target),
        None,
    )


def get_class(name: str) -> dict | None:
    """Look up a character class definition by name (case-insensitive)."""
    target = name.lower()
    return next(
        (c for c in _load("classes.json") if c.get("name", "").lower() == target),
        None,
    )


def list_spells() -> list[str]:
    """Return all spell names from the SRD dataset."""
    return [s.get("name", "") for s in _load("spells.json")]


def list_monsters() -> list[str]:
    """Return all monster names from the SRD dataset."""
    return [m.get("name", "") for m in _load("monsters.json")]
