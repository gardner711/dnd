"""Prompt assembly for DM turn planning."""
from __future__ import annotations

from app.models import DMContextResponse, PromptBundle, TurnInput


def build_prompt(turn: TurnInput, context: DMContextResponse) -> PromptBundle:
    system_prompt = (
        "You are a Dungeon Master orchestration agent. "
        "Choose structured actions and concise narration. "
        "Never mutate state outside explicit actions. "
        "You must return JSON only with keys: selected_action, narration, actions, llm_model. "
        "selected_action must be one of: narrate, story_log_append, story_hook_create, story_hook_update, world_flag_update, combat_action, map_update. "
        "Each action item must include action_type and args object."
    )
    user_prompt = turn.input_text
    prompt_material = {
        "turn_id": turn.turn_id,
        "campaign_id": str(turn.campaign_id),
        "session_id": str(turn.session_id),
        "user_id": str(turn.user_id),
        "character_id": str(turn.character_id),
        "input_text": turn.input_text,
        "story_context": context.story_context,
        "world_character_state": context.world_character_state,
        "map_snapshot": context.map_snapshot,
        "npc_context": context.npc_context,
        "memory_recall": context.memory_recall,
        "active_encounter": context.active_encounter,
        "recent_events": context.recent_events,
    }
    return PromptBundle(system_prompt=system_prompt, user_prompt=user_prompt, prompt_material=prompt_material)