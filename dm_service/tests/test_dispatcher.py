from __future__ import annotations

from uuid import uuid4

import pytest

import app.dispatcher as dispatcher
from app.models import DMContextResponse, PlannedAction, PlannedActionType, TurnInput, TurnPlan


def _turn() -> TurnInput:
    return TurnInput(
        turn_id="turn-dispatch-1",
        campaign_id=uuid4(),
        session_id=uuid4(),
        user_id=uuid4(),
        character_id=uuid4(),
        input_text="Do thing",
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


@pytest.mark.asyncio
async def test_dispatcher_compensates_on_failed_upstream(monkeypatch):
    turn = _turn()
    context = _context(turn)
    plan = TurnPlan(
        selected_action="story_log_append",
        narration="narration",
        actions=[PlannedAction(action_type=PlannedActionType.STORY_LOG_APPEND, args={"content": "c"})],
        llm_model="m",
    )

    calls = {"count": 0}

    async def append_story_log_switch(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return {"ok": True}

    monkeypatch.setattr(dispatcher.service_clients, "append_story_log", append_story_log_switch)

    results = await dispatcher.dispatch_plan(turn, context, plan)
    assert results[0].success is False
    assert results[0].compensated is True


@pytest.mark.asyncio
async def test_dispatcher_story_hook_create_success(monkeypatch):
    turn = _turn()
    context = _context(turn)
    plan = TurnPlan(
        selected_action="story_hook_create",
        narration="narration",
        actions=[PlannedAction(action_type=PlannedActionType.STORY_HOOK_CREATE, args={"content": "new hook", "priority": "high"})],
        llm_model="m",
    )

    async def create_story_hook(*args, **kwargs):
        return {"hook_id": str(uuid4())}

    monkeypatch.setattr(dispatcher.service_clients, "create_story_hook", create_story_hook)

    results = await dispatcher.dispatch_plan(turn, context, plan)
    assert results[0].success is True
    assert results[0].detail == "story_hook_created"