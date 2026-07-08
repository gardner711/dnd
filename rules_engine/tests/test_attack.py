import pytest
from app.attack import resolve_attack
from app.models import (
    AbilityScore,
    AbilityScores,
    AdvantageState,
    AttackRequest,
    Condition,
    CombatantStats,
    DamageType,
    TargetDefenses,
    WeaponDefinition,
)


def _fighter(conditions: list[Condition] | None = None) -> CombatantStats:
    return CombatantStats(
        id="fighter-1",
        name="Fighter",
        ability_scores=AbilityScores(strength=18),  # +4 modifier
        proficiency_bonus=3,
        armor_class=16,
        is_proficient_with_weapon=True,
        conditions=conditions or [],
    )


def _longsword() -> WeaponDefinition:
    return WeaponDefinition(
        name="Longsword",
        damage_dice="1d8",
        damage_type=DamageType.SLASHING,
        ability_score=AbilityScore.STRENGTH,
    )


# ── Hit / Miss ──────────────────────────────────────────────────────────────

def test_attack_always_hits_ac_0_unless_nat1():
    results = [resolve_attack(AttackRequest(
        attacker=_fighter(), weapon=_longsword(), target_ac=0,
    )) for _ in range(50)]
    non_nat1_hits = [r for r in results if not r.critical_miss]
    assert all(r.hit for r in non_nat1_hits)


def test_attack_never_hits_ac_30_without_nat20():
    # Max to-hit: d20(19) + STR(4) + prof(3) = 26 < 30
    results = [resolve_attack(AttackRequest(
        attacker=_fighter(), weapon=_longsword(), target_ac=30,
    )) for _ in range(100)]
    normal_hits = [r for r in results if r.hit and not r.critical_hit]
    assert len(normal_hits) == 0


def test_nat_1_is_always_miss():
    # Run many trials and verify critical misses always result in no hit
    results = [resolve_attack(AttackRequest(
        attacker=_fighter(), weapon=_longsword(), target_ac=0,
    )) for _ in range(200)]
    for r in results:
        if r.critical_miss:
            assert not r.hit


def test_nat_20_is_always_hit():
    results = [resolve_attack(AttackRequest(
        attacker=_fighter(), weapon=_longsword(), target_ac=30,
    )) for _ in range(200)]
    crits = [r for r in results if r.critical_hit]
    assert all(r.hit for r in crits)


# ── Damage ──────────────────────────────────────────────────────────────────

def test_damage_on_hit_is_at_least_1():
    for _ in range(30):
        result = resolve_attack(AttackRequest(
            attacker=_fighter(), weapon=_longsword(), target_ac=0,
        ))
        if result.hit:
            assert result.damage_total is not None
            assert result.damage_total >= 1


def test_damage_not_set_on_miss():
    # Force a miss by sending a critical-miss scenario in 200 trials
    found_miss = False
    for _ in range(200):
        result = resolve_attack(AttackRequest(
            attacker=_fighter(), weapon=_longsword(), target_ac=30,
        ))
        if not result.hit and not result.critical_hit:
            assert result.damage_total is None
            found_miss = True
            break
    assert found_miss, "Expected at least one non-hit in 200 attacks vs AC 30"


# ── Conditions ──────────────────────────────────────────────────────────────

def test_paralyzed_target_within_5ft_is_autocrit():
    for _ in range(30):
        result = resolve_attack(AttackRequest(
            attacker=_fighter(),
            weapon=_longsword(),
            target_ac=25,  # would not normally hit
            target_conditions=[Condition.PARALYZED],
            attacking_from_melee_range=True,
        ))
        if result.hit:
            assert result.critical_hit


def test_blinded_attacker_lower_hit_rate():
    """Blinded attacker (disadvantage) should hit AC 15 at a lower rate than normal."""
    normal_hits = sum(
        1 for _ in range(200)
        if resolve_attack(AttackRequest(
            attacker=_fighter(), weapon=_longsword(), target_ac=15,
        )).hit
    )
    blinded_hits = sum(
        1 for _ in range(200)
        if resolve_attack(AttackRequest(
            attacker=_fighter(conditions=[Condition.BLINDED]),
            weapon=_longsword(),
            target_ac=15,
        )).hit
    )
    assert blinded_hits < normal_hits, "Blinded attacker should hit less often"


def test_extra_damage_dice_applied_on_hit():
    for _ in range(20):
        result = resolve_attack(AttackRequest(
            attacker=_fighter(),
            weapon=_longsword(),
            target_ac=0,
            extra_damage_dice=["2d6"],  # simulate Sneak Attack
        ))
        if result.hit and not result.critical_hit:
            assert result.damage_total is not None
            # minimum: 1 (d8) + 2 (2d6 minimum) + 4 (STR mod) = 7
            assert result.damage_total >= 7


# ── Bug fix: finesse ─────────────────────────────────────────────────────────

def test_finesse_uses_dex_when_dex_is_higher():
    """Rapier (finesse) with DEX 18 (+4) > STR 10 (+0) should use DEX modifier."""
    duelist = CombatantStats(
        id="d1", name="Duelist",
        ability_scores=AbilityScores(strength=10, dexterity=18),
        proficiency_bonus=2,
        is_proficient_with_weapon=True,
    )
    rapier = WeaponDefinition(
        name="Rapier", damage_dice="1d8",
        damage_type=DamageType.PIERCING,
        ability_score=AbilityScore.STRENGTH,  # default field, finesse overrides it
        finesse=True,
    )
    for _ in range(20):
        result = resolve_attack(AttackRequest(attacker=duelist, weapon=rapier, target_ac=0))
        if result.hit:
            # to_hit = d20 + DEX(+4) + prof(+2) → minimum 1+4+2=7
            assert result.to_hit_total >= 7


def test_finesse_uses_str_when_str_is_higher():
    """Finesse weapon with STR 18 (+4) > DEX 8 (-1) should use STR modifier."""
    wrestler = CombatantStats(
        id="w1", name="Wrestler",
        ability_scores=AbilityScores(strength=18, dexterity=8),
        proficiency_bonus=2,
        is_proficient_with_weapon=True,
    )
    dagger = WeaponDefinition(
        name="Dagger", damage_dice="1d4",
        damage_type=DamageType.PIERCING,
        ability_score=AbilityScore.STRENGTH,
        finesse=True,
    )
    for _ in range(20):
        result = resolve_attack(AttackRequest(attacker=wrestler, weapon=dagger, target_ac=0))
        if result.hit:
            assert result.to_hit_total >= 7  # d20(1) + STR(+4) + prof(+2) = 7


# ── Bug fix: ranged in melee ──────────────────────────────────────────────────

def test_ranged_attack_in_melee_lower_hit_rate():
    archer = CombatantStats(
        id="a1", name="Archer",
        ability_scores=AbilityScores(dexterity=20),  # +5
        proficiency_bonus=3,
        is_proficient_with_weapon=True,
    )
    bow = WeaponDefinition(
        name="Longbow", damage_dice="1d8",
        damage_type=DamageType.PIERCING,
        ability_score=AbilityScore.DEXTERITY,
        ranged=True,
    )
    normal_hits = sum(
        1 for _ in range(200)
        if resolve_attack(AttackRequest(
            attacker=archer, weapon=bow, target_ac=15,
            adjacent_to_hostile_creature=False,
        )).hit
    )
    melee_hits = sum(
        1 for _ in range(200)
        if resolve_attack(AttackRequest(
            attacker=archer, weapon=bow, target_ac=15,
            adjacent_to_hostile_creature=True,
        )).hit
    )
    assert melee_hits < normal_hits


# ── Bug fix: damage resistance / immunity / vulnerability ─────────────────────

def test_immunity_sets_damage_to_zero():
    for _ in range(20):
        result = resolve_attack(AttackRequest(
            attacker=_fighter(), weapon=_longsword(), target_ac=0,
            target_defenses=TargetDefenses(damage_immunities=[DamageType.SLASHING]),
        ))
        if result.hit:
            assert result.damage_total == 0
            assert result.damage_modifier == "immunity"


def test_resistance_halves_damage():
    for _ in range(30):
        result = resolve_attack(AttackRequest(
            attacker=_fighter(), weapon=_longsword(), target_ac=0,
            target_defenses=TargetDefenses(damage_resistances=[DamageType.SLASHING]),
        ))
        if result.hit and result.damage_roll is not None:
            raw = max(0, result.damage_roll.total)
            assert result.damage_total == raw // 2
            assert result.damage_modifier == "resistance"


def test_vulnerability_doubles_damage():
    for _ in range(20):
        result = resolve_attack(AttackRequest(
            attacker=_fighter(), weapon=_longsword(), target_ac=0,
            target_defenses=TargetDefenses(damage_vulnerabilities=[DamageType.SLASHING]),
        ))
        if result.hit and result.damage_roll is not None:
            raw = max(0, result.damage_roll.total)
            assert result.damage_total == raw * 2
            assert result.damage_modifier == "vulnerability"


def test_resistance_and_vulnerability_cancel():
    for _ in range(20):
        result = resolve_attack(AttackRequest(
            attacker=_fighter(), weapon=_longsword(), target_ac=0,
            target_defenses=TargetDefenses(
                damage_resistances=[DamageType.SLASHING],
                damage_vulnerabilities=[DamageType.SLASHING],
            ),
        ))
        if result.hit and result.damage_roll is not None:
            raw = max(0, result.damage_roll.total)
            assert result.damage_total == raw  # unchanged
            assert result.damage_modifier == "none"


# ── Cover bonus ───────────────────────────────────────────────────────────────

def test_cover_bonus_reduces_hit_rate():
    no_cover = sum(
        1 for _ in range(200)
        if resolve_attack(AttackRequest(
            attacker=_fighter(), weapon=_longsword(), target_ac=20, cover_bonus=0,
        )).hit
    )
    three_quarters_cover = sum(
        1 for _ in range(200)
        if resolve_attack(AttackRequest(
            attacker=_fighter(), weapon=_longsword(), target_ac=20, cover_bonus=5,
        )).hit
    )
    assert three_quarters_cover < no_cover


def test_effective_ac_includes_cover():
    result = resolve_attack(AttackRequest(
        attacker=_fighter(), weapon=_longsword(), target_ac=10, cover_bonus=5,
    ))
    assert result.effective_ac == 15
