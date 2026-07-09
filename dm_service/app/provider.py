"""LLM provider abstraction for DM planning."""
from __future__ import annotations

from abc import ABC, abstractmethod
import json
import logging

import httpx

from app.config import settings
from app.models import DMContextResponse, PlannedAction, PlannedActionType, TurnInput, TurnPlan

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    @abstractmethod
    async def plan(self, turn: TurnInput, context: DMContextResponse, prompt_text: str) -> TurnPlan:
        raise NotImplementedError


class StubLLMProvider(LLMProvider):
    """Heuristic provider used until real LLM integration is enabled."""

    async def plan(self, turn: TurnInput, context: DMContextResponse, prompt_text: str) -> TurnPlan:
        text = turn.input_text.lower()

        if context.active_encounter and any(k in text for k in ("end turn", "next turn", "pass turn")):
            actions = [
                PlannedAction(
                    action_type=PlannedActionType.COMBAT_ACTION,
                    args={"action": "next_turn"},
                )
            ]
            narration = "The round advances as the turn is passed to the next combatant."
            selected = "combat_action"
        elif any(k in text for k in ("mark", "flag", "set world")):
            actions = [
                PlannedAction(
                    action_type=PlannedActionType.WORLD_FLAG_UPDATE,
                    args={"flags": {"dm.last_note": turn.input_text}},
                ),
                PlannedAction(
                    action_type=PlannedActionType.STORY_LOG_APPEND,
                    args={"entry_type": "narration", "content": turn.input_text},
                ),
            ]
            narration = "The world state is updated and the event is recorded in the story log."
            selected = "world_flag_update"
        else:
            actions = [
                PlannedAction(
                    action_type=PlannedActionType.STORY_LOG_APPEND,
                    args={
                        "entry_type": "narration",
                        "content": "The Dungeon Master acknowledges the action and advances the scene.",
                    },
                )
            ]
            narration = "The Dungeon Master acknowledges the action and advances the scene."
            selected = "narrate"

        return TurnPlan(
            selected_action=selected,
            narration=narration,
            actions=actions,
            llm_model=settings.llm_model,
        )


class OpenAICompatibleLLMProvider(LLMProvider):
    """Provider for OpenAI-compatible Chat Completions APIs."""

    async def plan(self, turn: TurnInput, context: DMContextResponse, prompt_text: str) -> TurnPlan:
        if not settings.llm_api_key:
            raise RuntimeError("llm_api_key is required for OpenAI-compatible provider")

        headers = {
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.llm_model,
            "temperature": settings.llm_temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only valid JSON matching this schema: "
                        "{selected_action: string, narration: string, "
                        "actions: [{action_type: string, args: object}], llm_model: string}. "
                        "allowed action_type values: narrate, story_log_append, world_flag_update, combat_action, map_update."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt_text,
                },
            ],
        }

        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            resp = await client.post(f"{settings.llm_api_base}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        plan_json = _extract_json_object(content)
        if "llm_model" not in plan_json:
            plan_json["llm_model"] = settings.llm_model
        return TurnPlan.model_validate(plan_json)


def get_llm_provider() -> LLMProvider:
    if settings.llm_provider.lower() in {"openai", "openai_compatible"}:
        return OpenAICompatibleLLMProvider()
    return StubLLMProvider()


def _extract_json_object(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop leading and trailing fenced code markers.
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("LLM response was not valid JSON: %s", exc)
        raise ValueError("LLM response was not valid JSON") from exc

    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON must be an object")
    return parsed