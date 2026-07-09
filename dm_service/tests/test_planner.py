from __future__ import annotations

from uuid import uuid4

import pytest

import app.planner as planner_module
import app.provider as provider_module
from app.models import DMContextResponse, TurnInput


def _turn() -> TurnInput:
    return TurnInput(
        turn_id="turn-planner-1",
        campaign_id=uuid4(),
        session_id=uuid4(),
        user_id=uuid4(),
        character_id=uuid4(),
        input_text="Proceed",
    )


def _context(turn: TurnInput) -> DMContextResponse:
    return DMContextResponse(
        campaign_id=turn.campaign_id,
        session_id=turn.session_id,
        user_id=turn.user_id,
        character_id=turn.character_id,
        story_context={"active_quests": [], "open_hooks": [], "recent_log": []},
        world_character_state=None,
        map_snapshot=None,
        npc_context=None,
        memory_recall=[],
        active_encounter=None,
        recent_events=[],
    )


class _FailingProvider(provider_module.LLMProvider):
    async def plan(self, turn, context, prompt_text):
        raise RuntimeError("provider down")


@pytest.mark.asyncio
async def test_plan_turn_falls_back_to_stub(monkeypatch):
    turn = _turn()
    context = _context(turn)
    monkeypatch.setattr(planner_module, "get_llm_provider", lambda: _FailingProvider())

    plan = await planner_module.plan_turn(turn, context, prompt_text="prompt")
    assert plan.selected_action in {"narrate", "story_log_append", "world_flag_update", "combat_action", "map_update"}
    assert plan.narration