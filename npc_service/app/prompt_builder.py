"""Pure prompt-assembly functions — no I/O, fully unit-testable."""
from __future__ import annotations

_DISPOSITION_PHRASES: dict[str, str] = {
    "hostile":  "You are hostile and deeply distrustful toward this character. Keep answers terse and guarded.",
    "neutral":  "You are cautious and measured toward this character.",
    "friendly": "You are warm and open toward this character.",
    "trusted":  "You trust this character implicitly and speak with full candour.",
}


def disposition_label(score: int | None) -> str:
    """Convert a numeric score (0–100) to a human-readable label."""
    if score is None:
        return "unknown"
    if score <= 30:
        return "hostile"
    if score <= 60:
        return "neutral"
    if score <= 80:
        return "friendly"
    return "trusted"


def evaluate_condition(
    secret: dict,
    disposition_score: int | None,
    quest_map: dict[str, str],
) -> bool:
    """Return True if the secret's reveal condition is currently satisfied."""
    ctype = secret.get("condition_type")
    if ctype == "always":
        return True
    if ctype == "disposition_gte":
        threshold = secret.get("condition_value")
        if disposition_score is None or threshold is None:
            return False
        return disposition_score >= threshold
    if ctype == "quest_status":
        title    = secret.get("condition_quest_title")
        expected = secret.get("condition_quest_status")
        if not title or not expected:
            return False
        return quest_map.get(title) == expected
    return False


def build_system_prompt(
    profile: dict,
    applicable_secrets: list[dict],
    memory_context: str | None,
    disposition_score: int | None,
    label: str,
    disposition_notes: str | None = None,
) -> str:
    """Assemble the final NPC system prompt in the canonical six-part order."""
    parts: list[str] = []

    # 1. NPC persona
    parts.append(
        f"You are {profile['name']}, a {profile['role']}. "
        "Stay in character at all times. Do not acknowledge that you are an AI or a game construct."
    )
    if profile.get("physical_description"):
        parts.append(f"Your appearance: {profile['physical_description']}")
    parts.append(f"Your personality and backstory:\n{profile['personality_prompt']}")

    # 2. Applicable secrets
    if applicable_secrets:
        parts.append(
            "Things you are aware of — reveal them selectively based on trust:\n"
            + "\n".join(f"- {s['content']}" for s in applicable_secrets)
        )

    # 3. Long-term memory summary from pgvector
    if memory_context:
        parts.append(
            f"Your recollections of this character from prior encounters:\n{memory_context}"
        )

    # 4. Current disposition toward the active character
    phrase = _DISPOSITION_PHRASES.get(label)
    if phrase:
        parts.append(phrase)
        if disposition_score is not None:
            parts.append(f"(Relationship score: {disposition_score}/100)")
        if disposition_notes:
            parts.append(f"History with this character: {disposition_notes}")

    return "\n\n".join(parts)
