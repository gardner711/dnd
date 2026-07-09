"""Deterministic tool/action dispatcher for DM plans."""
from __future__ import annotations

from uuid import UUID

from app import service_clients
from app.models import DMContextResponse, PlannedActionType, SideEffectResult, TurnInput, TurnPlan


async def dispatch_plan(turn: TurnInput, context: DMContextResponse, plan: TurnPlan) -> list[SideEffectResult]:
    results: list[SideEffectResult] = []

    for action in plan.actions:
        try:
            if action.action_type == PlannedActionType.STORY_LOG_APPEND:
                content = action.args.get("content") or plan.narration
                entry_type = action.args.get("entry_type", "narration")
                resp = await service_clients.append_story_log(
                    campaign_id=turn.campaign_id,
                    session_id=turn.session_id,
                    user_id=turn.user_id,
                    entry_type=entry_type,
                    content=content,
                )
                results.append(_result(action.action_type.value, resp, "story_log_appended"))

            elif action.action_type == PlannedActionType.STORY_HOOK_CREATE:
                resp = await service_clients.create_story_hook(
                    campaign_id=turn.campaign_id,
                    session_id=turn.session_id,
                    user_id=turn.user_id,
                    content=action.args.get("content", ""),
                    priority=action.args.get("priority", "medium"),
                )
                results.append(_result(action.action_type.value, resp, "story_hook_created"))

            elif action.action_type == PlannedActionType.STORY_HOOK_UPDATE:
                hook_id = action.args.get("hook_id")
                if not hook_id:
                    results.append(SideEffectResult(action_type=action.action_type.value, success=False, detail="hook_id_required"))
                else:
                    resp = await service_clients.update_story_hook(
                        campaign_id=turn.campaign_id,
                        session_id=turn.session_id,
                        user_id=turn.user_id,
                        hook_id=UUID(str(hook_id)),
                        updates=action.args.get("updates", {}),
                    )
                    results.append(_result(action.action_type.value, resp, "story_hook_updated"))

            elif action.action_type == PlannedActionType.WORLD_FLAG_UPDATE:
                flags = action.args.get("flags", {})
                resp = await service_clients.update_world_flags(
                    campaign_id=turn.campaign_id,
                    session_id=turn.session_id,
                    user_id=turn.user_id,
                    flags=flags,
                )
                results.append(_result(action.action_type.value, resp, "world_flags_updated"))

            elif action.action_type == PlannedActionType.COMBAT_ACTION:
                combat_action = action.args.get("action", "next_turn")
                payload = action.args.get("payload", {})
                resp = await service_clients.execute_combat_action(
                    action=combat_action,
                    campaign_id=turn.campaign_id,
                    session_id=turn.session_id,
                    user_id=turn.user_id,
                    payload=payload,
                )
                results.append(_result(action.action_type.value, resp, f"combat_{combat_action}"))

            elif action.action_type == PlannedActionType.MAP_UPDATE:
                sub_action = action.args.get("action")
                if sub_action == "select_active_map":
                    map_id = action.args.get("map_id")
                    if not map_id:
                        results.append(SideEffectResult(action_type=action.action_type.value, success=False, detail="map_id_required"))
                    else:
                        resp = await service_clients.select_active_map(
                            campaign_id=turn.campaign_id,
                            session_id=turn.session_id,
                            user_id=turn.user_id,
                            map_id=UUID(str(map_id)),
                            character_id=UUID(str(action.args["character_id"])) if action.args.get("character_id") else None,
                        )
                        results.append(_result(action.action_type.value, resp, "map_active_selected"))
                elif sub_action == "patch_fog":
                    map_id = action.args.get("map_id")
                    character_id = action.args.get("character_id") or str(turn.character_id)
                    add_cells = action.args.get("add_cells", [])
                    if not map_id:
                        results.append(SideEffectResult(action_type=action.action_type.value, success=False, detail="map_id_required"))
                    else:
                        resp = await service_clients.patch_map_fog(
                            campaign_id=turn.campaign_id,
                            session_id=turn.session_id,
                            user_id=turn.user_id,
                            map_id=UUID(str(map_id)),
                            character_id=UUID(str(character_id)),
                            add_cells=add_cells,
                        )
                        results.append(_result(action.action_type.value, resp, "map_fog_patched"))
                elif sub_action == "upsert_token":
                    map_id = action.args.get("map_id")
                    aggregate_id = action.args.get("aggregate_id")
                    if not map_id or not aggregate_id:
                        results.append(SideEffectResult(action_type=action.action_type.value, success=False, detail="map_id_and_aggregate_id_required"))
                    else:
                        resp = await service_clients.upsert_map_token(
                            campaign_id=turn.campaign_id,
                            session_id=turn.session_id,
                            user_id=turn.user_id,
                            map_id=UUID(str(map_id)),
                            aggregate_id=UUID(str(aggregate_id)),
                            aggregate_type=action.args.get("aggregate_type", "character"),
                            x=int(action.args.get("x", 0)),
                            y=int(action.args.get("y", 0)),
                            encounter_id=UUID(str(action.args["encounter_id"])) if action.args.get("encounter_id") else None,
                            visible=bool(action.args.get("visible", True)),
                        )
                        results.append(_result(action.action_type.value, resp, "map_token_upserted"))
                else:
                    results.append(SideEffectResult(action_type=action.action_type.value, success=False, detail="unsupported_map_update_action"))

            elif action.action_type == PlannedActionType.NARRATE:
                results.append(SideEffectResult(action_type=action.action_type.value, success=True, detail="no_op"))

            else:
                results.append(SideEffectResult(action_type=action.action_type.value, success=False, detail="unsupported_action"))
        except Exception as exc:
            results.append(SideEffectResult(action_type=action.action_type.value, success=False, detail=str(exc)))

        last = results[-1]
        if not last.success:
            compensation = await _compensate_failure(turn, action_type=last.action_type, detail=last.detail)
            last.compensated = compensation.success
            last.compensation_detail = compensation.detail
            if action.args.get("halt_on_error", False):
                break

    return results


def _result(action_type: str, response: dict | None, success_detail: str) -> SideEffectResult:
    if response is None:
        return SideEffectResult(action_type=action_type, success=False, detail="upstream_call_failed")
    return SideEffectResult(action_type=action_type, success=True, response=response, detail=success_detail)


async def _compensate_failure(turn: TurnInput, action_type: str, detail: str) -> SideEffectResult:
    """Best-effort compensation log entry for failed side effects."""
    compensation_text = (
        f"[DM dispatcher] Action '{action_type}' failed for turn {turn.turn_id}. "
        f"Failure detail: {detail}."
    )
    resp = await service_clients.append_story_log(
        campaign_id=turn.campaign_id,
        session_id=turn.session_id,
        user_id=turn.user_id,
        entry_type="hook_note",
        content=compensation_text,
    )
    if resp is None:
        return SideEffectResult(action_type="compensation", success=False, detail="compensation_log_failed")
    return SideEffectResult(action_type="compensation", success=True, detail="compensation_logged", response=resp)