"""Unit tests for pure prompt-builder functions — no I/O."""
from app.prompt_builder import build_system_prompt, disposition_label, evaluate_condition

_PROFILE = {
    "name": "Elara",
    "role": "innkeeper",
    "physical_description": "Silver-haired woman",
    "personality_prompt": "Warm but suspicious of strangers.",
}


# ── disposition_label ─────────────────────────────────────────────────────────

def test_label_hostile():      assert disposition_label(15)  == "hostile"
def test_label_neutral():      assert disposition_label(50)  == "neutral"
def test_label_friendly():     assert disposition_label(70)  == "friendly"
def test_label_trusted():      assert disposition_label(90)  == "trusted"
def test_label_boundary_30():  assert disposition_label(30)  == "hostile"
def test_label_boundary_31():  assert disposition_label(31)  == "neutral"
def test_label_boundary_80():  assert disposition_label(80)  == "friendly"
def test_label_boundary_81():  assert disposition_label(81)  == "trusted"
def test_label_none():         assert disposition_label(None) == "unknown"


# ── evaluate_condition ────────────────────────────────────────────────────────

def test_condition_always():
    assert evaluate_condition({"condition_type": "always"}, None, {}) is True


def test_condition_disposition_gte_met():
    s = {"condition_type": "disposition_gte", "condition_value": 70}
    assert evaluate_condition(s, 75, {}) is True


def test_condition_disposition_gte_exact():
    s = {"condition_type": "disposition_gte", "condition_value": 70}
    assert evaluate_condition(s, 70, {}) is True


def test_condition_disposition_gte_not_met():
    s = {"condition_type": "disposition_gte", "condition_value": 70}
    assert evaluate_condition(s, 65, {}) is False


def test_condition_disposition_gte_no_score():
    s = {"condition_type": "disposition_gte", "condition_value": 70}
    assert evaluate_condition(s, None, {}) is False


def test_condition_disposition_gte_no_threshold():
    s = {"condition_type": "disposition_gte", "condition_value": None}
    assert evaluate_condition(s, 90, {}) is False


def test_condition_quest_status_met():
    s = {
        "condition_type": "quest_status",
        "condition_quest_title": "Find the artifact",
        "condition_quest_status": "completed",
    }
    assert evaluate_condition(s, None, {"Find the artifact": "completed"}) is True


def test_condition_quest_status_not_met():
    s = {
        "condition_type": "quest_status",
        "condition_quest_title": "Find the artifact",
        "condition_quest_status": "completed",
    }
    assert evaluate_condition(s, None, {"Find the artifact": "active"}) is False


def test_condition_quest_status_not_in_map():
    s = {
        "condition_type": "quest_status",
        "condition_quest_title": "Find the artifact",
        "condition_quest_status": "completed",
    }
    assert evaluate_condition(s, None, {}) is False


def test_condition_unknown_type_returns_false():
    assert evaluate_condition({"condition_type": "magic"}, 100, {}) is False


# ── build_system_prompt ───────────────────────────────────────────────────────

def test_prompt_contains_npc_name():
    prompt = build_system_prompt(_PROFILE, [], None, None, "unknown")
    assert "Elara" in prompt


def test_prompt_contains_role():
    prompt = build_system_prompt(_PROFILE, [], None, None, "unknown")
    assert "innkeeper" in prompt


def test_prompt_contains_personality():
    prompt = build_system_prompt(_PROFILE, [], None, None, "unknown")
    assert "suspicious of strangers" in prompt


def test_prompt_contains_physical_description():
    prompt = build_system_prompt(_PROFILE, [], None, None, "unknown")
    assert "Silver-haired" in prompt


def test_prompt_with_secrets():
    secrets = [{"content": "Her daughter is missing"}]
    prompt = build_system_prompt(_PROFILE, secrets, None, None, "unknown")
    assert "Her daughter is missing" in prompt


def test_prompt_with_memory():
    prompt = build_system_prompt(_PROFILE, [], "Met once in the tavern", None, "unknown")
    assert "Met once in the tavern" in prompt


def test_prompt_with_disposition_score():
    prompt = build_system_prompt(_PROFILE, [], None, 75, "friendly")
    assert "75/100" in prompt
    assert "warm" in prompt.lower() or "friendly" in prompt.lower()


def test_prompt_hostile_disposition():
    prompt = build_system_prompt(_PROFILE, [], None, 20, "hostile")
    assert "hostile" in prompt.lower() or "distrustful" in prompt.lower()


def test_prompt_unknown_disposition_no_score_section():
    # unknown disposition → no phrase, no score line
    prompt = build_system_prompt(_PROFILE, [], None, None, "unknown")
    assert "/100" not in prompt


def test_prompt_no_secrets_no_secret_section():
    prompt = build_system_prompt(_PROFILE, [], None, None, "unknown")
    assert "selectively" not in prompt


def test_prompt_with_disposition_notes():
    prompt = build_system_prompt(_PROFILE, [], None, 72, "friendly",
                                  disposition_notes="Saved her cat from a river")
    assert "Saved her cat from a river" in prompt


def test_prompt_notes_not_shown_without_disposition_label():
    # notes only appear if there's a recognized disposition phrase
    prompt = build_system_prompt(_PROFILE, [], None, None, "unknown",
                                  disposition_notes="Some note")
    assert "Some note" not in prompt
