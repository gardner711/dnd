"""Turn planning policy entry point."""
from __future__ import annotations

import logging

from app.models import DMContextResponse, TurnInput, TurnPlan
from app.provider import StubLLMProvider, get_llm_provider

logger = logging.getLogger(__name__)


async def plan_turn(turn: TurnInput, context: DMContextResponse, prompt_text: str) -> TurnPlan:
    provider = get_llm_provider()
    try:
        return await provider.plan(turn, context, prompt_text)
    except Exception as exc:
        logger.warning("Primary planner provider failed; falling back to stub policy: %s", exc)
        return await StubLLMProvider().plan(turn, context, prompt_text)