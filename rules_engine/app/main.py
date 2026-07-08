"""FastAPI application — Rules Engine service entry point."""
from __future__ import annotations

import logging

from fastapi import FastAPI

from app import ability_check, attack, combat_rules, concentration, dice, event_log, grapple, movement, spells
from app.models import (
    AbilityCheckRequest,
    AbilityCheckResult,
    AttackRequest,
    AttackResult,
    ConcentrationCheckRequest,
    ConcentrationCheckResult,
    DeathSaveRequest,
    DeathSaveResult,
    DiceRollRequest,
    DiceRollResponse,
    GrappleAttemptRequest,
    GrappleResult,
    InitiativeRequest,
    InitiativeResult,
    MoveRequest,
    MoveResult,
    RollResult,
    SavingThrowRequest,
    SavingThrowResult,
    ShoveAttemptRequest,
    ShoveResult,
    SpellCastRequest,
    SpellValidationResult,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Rules Engine",
    description="D&D 5e SRD deterministic rules resolution microservice",
    version="0.1.0",
)


# ── Health ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "rules-engine"}


# ── Dice ───────────────────────────────────────────────────────────────────

@app.post("/roll", response_model=DiceRollResponse)
async def roll_dice(request: DiceRollRequest) -> DiceRollResponse:
    """Roll dice using standard notation (e.g. '2d6+3', '4d6kh3')."""
    result = dice.roll(request.notation)
    if request.event_context:
        ctx = request.event_context
        await event_log.emit(
            event_type="dice.rolled",
            aggregate_id=ctx.aggregate_id,
            aggregate_type=ctx.aggregate_type,
            campaign_id=ctx.campaign_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            payload={**result.model_dump(mode="json"), "purpose": request.purpose},
        )
    return DiceRollResponse(result=result, purpose=request.purpose)


# ── Ability Checks & Saving Throws ─────────────────────────────────────────

@app.post("/ability-check", response_model=AbilityCheckResult)
async def resolve_ability_check(request: AbilityCheckRequest) -> AbilityCheckResult:
    """Resolve an ability check or skill check against a DC. PHB p.174."""
    result = ability_check.check(request)
    if request.event_context:
        ctx = request.event_context
        await event_log.emit(
            event_type="ability_check.resolved",
            aggregate_id=ctx.aggregate_id,
            aggregate_type=ctx.aggregate_type,
            campaign_id=ctx.campaign_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            payload=result.model_dump(mode="json"),
        )
    return result


@app.post("/saving-throw", response_model=SavingThrowResult)
async def resolve_saving_throw(request: SavingThrowRequest) -> SavingThrowResult:
    """Resolve a saving throw against a DC. PHB p.179."""
    result = ability_check.saving_throw(request)
    if request.event_context:
        ctx = request.event_context
        await event_log.emit(
            event_type="saving_throw.resolved",
            aggregate_id=ctx.aggregate_id,
            aggregate_type=ctx.aggregate_type,
            campaign_id=ctx.campaign_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            payload=result.model_dump(mode="json"),
        )
    return result


# ── Combat ─────────────────────────────────────────────────────────────────

@app.post("/attack", response_model=AttackResult)
async def resolve_attack(request: AttackRequest) -> AttackResult:
    """Resolve a weapon attack: to-hit roll, hit/miss determination, damage. PHB p.194."""
    result = attack.resolve_attack(request)
    if request.event_context:
        ctx = request.event_context
        await event_log.emit(
            event_type="attack.resolved",
            aggregate_id=ctx.aggregate_id,
            aggregate_type=ctx.aggregate_type,
            campaign_id=ctx.campaign_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            payload=result.model_dump(mode="json"),
        )
    return result


@app.post("/initiative", response_model=InitiativeResult)
async def roll_initiative(request: InitiativeRequest) -> InitiativeResult:
    """Roll initiative for all combatants and return a sorted turn order. PHB p.189."""
    result = combat_rules.roll_initiative(request)
    if request.event_context:
        ctx = request.event_context
        await event_log.emit(
            event_type="combat.initiative_rolled",
            aggregate_id=ctx.aggregate_id,
            aggregate_type=ctx.aggregate_type,
            campaign_id=ctx.campaign_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            payload=result.model_dump(mode="json"),
        )
    return result


@app.post("/death-save", response_model=DeathSaveResult)
async def resolve_death_save(request: DeathSaveRequest) -> DeathSaveResult:
    """Resolve a death saving throw. PHB p.197."""
    result = _death_save(request)
    if request.event_context:
        ctx = request.event_context
        await event_log.emit(
            event_type="death_save.resolved",
            aggregate_id=ctx.aggregate_id,
            aggregate_type=ctx.aggregate_type,
            campaign_id=ctx.campaign_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            payload=result.model_dump(mode="json"),
        )
    return result


# ── Movement ───────────────────────────────────────────────────────────────

@app.post("/movement/validate", response_model=MoveResult)
def validate_movement(request: MoveRequest) -> MoveResult:
    """Validate a movement action against the combatant's available speed. PHB p.190."""
    return movement.validate_move(request)


# ── Spells ─────────────────────────────────────────────────────────────────

@app.post("/spell/validate", response_model=SpellValidationResult)
async def validate_spell_cast(request: SpellCastRequest) -> SpellValidationResult:
    """Validate whether a spell can be cast (slots, concentration, conditions). PHB p.201."""
    result = spells.validate_cast(request)
    if request.event_context:
        ctx = request.event_context
        await event_log.emit(
            event_type="spell.validated",
            aggregate_id=ctx.aggregate_id,
            aggregate_type=ctx.aggregate_type,
            campaign_id=ctx.campaign_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            payload={**result.model_dump(mode="json"), "spell_name": request.spell_name},
        )
    return result


# ── Concentration ─────────────────────────────────────────────────────

@app.post("/concentration-check", response_model=ConcentrationCheckResult)
async def resolve_concentration_check(
    request: ConcentrationCheckRequest,
) -> ConcentrationCheckResult:
    """Resolve a concentration saving throw after a caster takes damage. PHB p.203."""
    result = concentration.concentration_check(request)
    if request.event_context:
        ctx = request.event_context
        await event_log.emit(
            event_type="concentration_check.resolved",
            aggregate_id=ctx.aggregate_id,
            aggregate_type=ctx.aggregate_type,
            campaign_id=ctx.campaign_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            payload={**result.model_dump(mode="json"), "damage_taken": request.damage_taken},
        )
    return result


# ── Grapple & Shove ──────────────────────────────────────────────────

@app.post("/grapple", response_model=GrappleResult)
async def resolve_grapple(request: GrappleAttemptRequest) -> GrappleResult:
    """Contested Athletics vs Athletics/Acrobatics. On success, apply GRAPPLED. PHB p.195."""
    result = grapple.attempt_grapple(request)
    if request.event_context:
        ctx = request.event_context
        await event_log.emit(
            event_type="grapple.resolved",
            aggregate_id=ctx.aggregate_id,
            aggregate_type=ctx.aggregate_type,
            campaign_id=ctx.campaign_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            payload=result.model_dump(mode="json"),
        )
    return result


@app.post("/shove", response_model=ShoveResult)
async def resolve_shove(request: ShoveAttemptRequest) -> ShoveResult:
    """Contested Athletics vs Athletics/Acrobatics. On success, knock prone or push 5 ft. PHB p.195."""
    result = grapple.attempt_shove(request)
    if request.event_context:
        ctx = request.event_context
        await event_log.emit(
            event_type="shove.resolved",
            aggregate_id=ctx.aggregate_id,
            aggregate_type=ctx.aggregate_type,
            campaign_id=ctx.campaign_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            payload=result.model_dump(mode="json"),
        )
    return result


# ── Internal helpers ───────────────────────────────────────────────────────

def _death_save(request: DeathSaveRequest) -> DeathSaveResult:
    """
    PHB p.197:
      - Roll d20. 10+ = success, 9 or lower = failure.
      - Natural 1 = two failures.
      - Natural 20 = regain 1 HP (immediate stabilization).
      - 3 successes = stable. 3 failures = dead.
    """
    roll = dice.roll("1d20")
    natural = roll.total

    critical_stabilize = natural == 20
    critical_failure = natural == 1
    success = natural >= 10

    if critical_stabilize:
        new_successes = 3
        new_failures = request.current_failures
    elif critical_failure:
        new_successes = request.current_successes
        new_failures = min(3, request.current_failures + 2)
    elif success:
        new_successes = min(3, request.current_successes + 1)
        new_failures = request.current_failures
    else:
        new_successes = request.current_successes
        new_failures = min(3, request.current_failures + 1)

    stabilized = critical_stabilize or new_successes >= 3
    dead = new_failures >= 3

    return DeathSaveResult(
        roll=roll,
        success=success or critical_stabilize,
        critical_stabilize=critical_stabilize,
        critical_failure=critical_failure,
        new_successes=new_successes,
        new_failures=new_failures,
        stabilized=stabilized,
        dead=dead,
    )
