from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import app.provider as provider_module
from app.models import DMContextResponse, TurnInput


def _turn() -> TurnInput:
    return TurnInput(
        turn_id="turn-provider-1",
        campaign_id=uuid4(),
        session_id=uuid4(),
        user_id=uuid4(),
        character_id=uuid4(),
        input_text="Describe the chamber",
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


def test_extract_json_object_rejects_non_json():
    with pytest.raises(ValueError):
        provider_module._extract_json_object("not json")


def test_extract_json_object_accepts_fenced_json():
    payload = {"selected_action": "narrate", "narration": "Hi", "actions": [], "llm_model": "m"}
    text = "```json\n" + json.dumps(payload) + "\n```"
    assert provider_module._extract_json_object(text) == payload


@pytest.mark.asyncio
async def test_openai_provider_parses_valid_response(monkeypatch):
    turn = _turn()
    context = _context(turn)
    monkeypatch.setattr(provider_module.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(provider_module.settings, "llm_api_base", "http://fake-llm")
    monkeypatch.setattr(provider_module.settings, "llm_model", "test-model")

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "selected_action": "story_log_append",
                                    "narration": "Narration",
                                    "actions": [
                                        {
                                            "action_type": "story_log_append",
                                            "args": {"entry_type": "narration", "content": "Narration"},
                                        }
                                    ],
                                    "llm_model": "test-model",
                                }
                            )
                        }
                    }
                ]
            }

    post_mock = AsyncMock(return_value=_Resp())

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        post = post_mock

    monkeypatch.setattr(provider_module.httpx, "AsyncClient", _Client)

    provider = provider_module.OpenAICompatibleLLMProvider()
    plan = await provider.plan(turn, context, "prompt")
    assert plan.selected_action == "story_log_append"
    assert plan.actions[0].action_type.value == "story_log_append"