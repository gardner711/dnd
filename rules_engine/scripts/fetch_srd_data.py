"""Download 5e SRD reference data from the bagelbits/5e-database GitHub repository.

Usage (from the rules_engine directory):
    python scripts/fetch_srd_data.py

Output files written to rules_engine/data/:
    spells.json    — all SRD spells
    monsters.json  — all SRD monster stat blocks
    equipment.json — weapons, armour, and adventuring gear
    classes.json   — character class definitions
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

BASE_URL = "https://raw.githubusercontent.com/bagelbits/5e-database/master/src"
FILES = ["spells.json", "monsters.json", "equipment.json", "classes.json"]
DATA_DIR = Path(__file__).parent.parent / "data"


def fetch() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    for filename in FILES:
        url = f"{BASE_URL}/{filename}"
        dest = DATA_DIR / filename
        print(f"Fetching {filename} ...", end=" ", flush=True)
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            with dest.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            count = len(data) if isinstance(data, list) else "?"
            print(f"OK ({count} records) -> {dest}")
        except Exception as exc:
            print(f"FAILED: {exc}")


if __name__ == "__main__":
    fetch()
