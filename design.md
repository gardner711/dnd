# AI Dungeon Master Platform Design

## Overview

This document describes a self-hosted, Kubernetes-native architecture
for an AI-driven tabletop RPG platform where one or more human players
interact with an AI Dungeon Master that follows D&D 5e SRD rules.

## Design Goals

-   AI-driven storytelling
-   Deterministic rule enforcement
-   Persistent campaign memory
-   Containerized microservices
-   Self-hosted on K3s
-   Mobile-first client
-   Authenticated users with session and character ownership
-   Multiple concurrent campaigns with complete data isolation

## High-Level Architecture

``` mermaid
flowchart TD
    A[Flutter Mobile App] --> B[Traefik Ingress]
    B --> AUTH[Auth Service]
    AUTH --> B
    B --> C[API Gateway / Session API]

    C --> DM[Dungeon Master Service]
    C --> CE[Combat Engine]
    C --> RE[Rules Engine]
    C --> NPC[NPC Interaction Service]
    C --> SS[Story State Manager]
    C --> WS[World State Service]
    C --> MAP[Map Service]
    C --> MEM[Memory Service]

    DM --> RE
    DM --> SS
    DM --> MEM
    DM --> NPC
    DM --> MAP
    DM --> WS

    CE --> RE
    CE --> WS

    SS --> DB[(PostgreSQL + pgvector)]
    MEM --> DB
    WS --> DB
    MAP --> OBJ[(MinIO)]

    C --> REDIS[(Redis Streams)]

    DM --> EL[Event Log Service]
    CE --> EL
    RE --> EL
    NPC --> EL
    SS --> EL
    WS --> EL
    EL --> EVTDB[(Event Store — PostgreSQL)]
```

## Technology Stack

  Layer             Technology
  ----------------- -----------------------------
  Mobile            Flutter
  API               FastAPI
  Orchestration     LangGraph
  Auth              Keycloak (OIDC / OAuth 2.0)
  Database          PostgreSQL + pgvector
  Event Store       PostgreSQL (append-only events table)
  Cache/Event Bus   Redis Streams
  Object Storage    MinIO
  Deployment        K3s
  Ingress           Traefik
  Monitoring        Prometheus + Grafana + Loki

## AI Philosophy

The LLM is responsible for creativity: - Storytelling - Dialogue -
Narration - Improvisation

Deterministic services handle: - Dice - Combat - Movement - Spell
validation - Character sheets - Inventory - Rule enforcement

## Core Services

### Auth Service

Handles user registration, login, and token issuance using
[Keycloak](https://www.keycloak.org/) as the identity provider. Traefik
validates the JWT on every inbound request using the Keycloak JWKS
endpoint — no request reaches a backend service without a valid token.

**Identity model:**

-   **User** — a registered player account (`user_id`, email,
    display name). Global; one account per person.
-   **Character** — created by a user for a **specific campaign**.
    A user may have many characters, but each belongs to exactly one
    campaign and cannot move between campaigns
    (`character_id`, `user_id`, `campaign_id`, name, class, stats).
-   **Campaign** — a long-running adventure owned by one user
    (the DM/host); other users join as participants
    (`campaign_id`, `owner_id`, list of `participant_ids`, status).

**Token claims used by downstream services:**

  Claim            Purpose
  ---------------- -------------------------------------------
  `sub`            Canonical `user_id` across all services
  `campaign_id`    Active campaign (set at session start)
  `character_id`   Active character for this session
  `roles`          `player` or `dm_override` (admin debug)

All services treat `sub` as the partition boundary for user-scoped
data. No service accepts a `user_id` from the request body — it is
always read from the verified JWT claims.

### Multi-Campaign Architecture

Every piece of data in the platform is partitioned by `campaign_id`.
No service query ever crosses campaign boundaries. This is the
foundational isolation mechanism that allows many independent groups
of players to use the platform simultaneously without any risk of
data leakage between campaigns.

**Campaign lifecycle:**

1.  **Create** — A registered user creates a campaign and becomes its
    DM/owner. The campaign receives a unique `campaign_id` and starts
    in `active` status.
2.  **Invite** — The DM shares an invite code or link. Players with
    accounts accept the invite and are added to `participant_ids`.
3.  **Character creation** — Each participant creates one or more
    characters scoped to this campaign. The character record carries
    both `user_id` and `campaign_id`. The same player will have
    completely separate characters in different campaigns.
4.  **Play** — A player starts a session by selecting a campaign and
    one of their characters in that campaign. The Session API issues
    a session-scoped JWT enriched with `campaign_id` and
    `character_id`. All subsequent requests carry this context.
5.  **Archive** — When a campaign concludes it is marked `archived`.
    All data is retained for replay and history but no new sessions
    can start. A player can be in an active campaign and an archived
    one simultaneously.

**Data ownership and scoping:**

  Entity        Scope            Rule
  ------------- ---------------- -----------------------------------------------
  User          Global           One account per person; login credential only
  Campaign      Global           Owned by one user; many participants
  Character     Per-campaign     Belongs to exactly one campaign; never migrated
  NPC           Per-campaign     Exists within one campaign; recurring NPCs must be re-created per campaign
  Memory        Per-campaign     `campaign_id` required on every write and read
  World State   Per-campaign     Each campaign’s world evolves independently
  Event Log     Per-campaign     Partitioned by `campaign_id`; streamed per campaign
  Story State   Per-campaign     Plot hooks and quest flags are campaign-local
  Maps          Per-campaign     Maps are created for and belong to one campaign

**Session context — base JWT vs session JWT:**

A user holds a long-lived **base JWT** (from Keycloak) that identifies
them but carries no campaign context. When entering a campaign, the
client calls `POST /session/start` with `campaign_id` and
`character_id`. The Session API:

1.  Validates the user is a participant in that campaign.
2.  Validates the character belongs to this user **and** this campaign.
3.  Issues a short-lived **session JWT** adding `campaign_id` and
    `character_id` claims to the base identity.

All gameplay requests use the session JWT. A user can hold multiple
session JWTs simultaneously (one per active campaign tab/device).

**Isolation guarantee:**

Every service that persists data includes `campaign_id` in every
query predicate. A SELECT, INSERT, or UPDATE that omits the
`campaign_id` filter is treated as a critical bug equivalent to a
data breach. The Memory Service, World State, Event Log, Story State,
and NPC Service all enforce this at the data-access layer.

### Dungeon Master Service

Coordinates gameplay and calls supporting services instead of
implementing rules.

### Rules Engine

Implements D&D 5e SRD deterministic mechanics. All rule evaluation is stateless — the service receives the current state as input and returns a deterministic result. No database writes; all persistence is handled by the calling service.

**Mechanics implemented:**

-   **Dice** — Full d20 notation (`3d6kh2+4`, advantage, disadvantage) via the avrae `d20` library.
-   **Ability checks and saving throws** — DC comparison, proficiency bonus, advantage/disadvantage.
-   **Attack rolls** — finesse weapons (higher of STR/DEX modifier), ranged-in-melee disadvantage, cover bonus (+2 half / +5 three-quarters), damage resistance/immunity/vulnerability.
-   **Conditions** — All 15 PHB conditions plus exhaustion levels 1–6.
-   **Concentration** — CON save DC = max(10, damage ÷ 2).
-   **Grapple / shove** — Contested Athletics vs Athletics/Acrobatics; ties go to the defender.
-   **Movement validation** — speed budget, difficult terrain, prone movement penalty.
-   **Spell validation** — slot level, range, and concentration conflict check.
-   **Initiative** — DEX modifier + d20.
-   **Death saves** — three successes (stabilise) or three failures (dead).

**API routes:**

All routes accept an optional `event_context` field; when supplied, the Rules Engine emits an audit event to the Event Log Service.

**`EventContext`** (optional on every request):

| Field | Type | Notes |
|-------|------|-------|
| `campaign_id` | `str` | |
| `session_id` | `str` | |
| `user_id` | `str` | JWT `sub` claim of acting player |
| `aggregate_id` | `str` | ID of the primary entity (character, NPC) |
| `aggregate_type` | `str` | `character` / `npc` / `combat` / `story` / `world` (default `character`) |

**`CombatantStats`** (passed on all combat routes):

| Field | Type | Default |
|-------|------|---------|
| `id` | `str` | required |
| `name` | `str` | required |
| `ability_scores` | `{strength … charisma: int}` | all 10 |
| `proficiency_bonus` | `int` | 2 |
| `armor_class` | `int` | 10 |
| `max_hp` / `current_hp` | `int` | 10 |
| `speed` | `int` | 30 |
| `conditions` | `list[Condition]` | [] |
| `proficient_skills` | `list[Skill]` | [] |
| `proficient_saving_throws` | `list[AbilityScore]` | [] |
| `expertise_skills` | `list[Skill]` | [] |
| `exhaustion_level` | `int` 0–6 | 0 |
| `is_proficient_with_weapon` | `bool` | true (caller asserts) |

**`WeaponDefinition`**:

| Field | Type | Notes |
|-------|------|-------|
| `name` | `str` | |
| `damage_dice` | `str` | e.g. `"1d8"`, `"2d6"` |
| `damage_type` | `DamageType` | one of 13 PHB types |
| `ability_score` | `AbilityScore` | default `strength` |
| `finesse` | `bool` | rolls with higher of STR/DEX modifier |
| `ranged` | `bool` | triggers disadvantage if adjacent to hostile creature |
| `magical` | `bool` | bypasses non-magical resistance |
| `attack_bonus` / `damage_bonus` | `int` | magic weapon bonuses |

**Route request / response detail:**

| Route | Key request fields | Key response fields |
|-------|-------------------|---------------------|
| `POST /roll` | `notation: str`, `purpose?` | `total: int`, `dice_values: list[int]`, `expression: str` |
| `POST /ability-check` | `combatant: CombatantStats`, `ability: AbilityScore`, `dc: int`, `skill?: Skill`, `advantage_state?` | `total: int`, `dc: int`, `success: bool`, `proficiency_applied: int` |
| `POST /saving-throw` | `combatant`, `ability`, `dc`, `advantage_state?` | `total, dc, success` |
| `POST /attack` | `attacker: CombatantStats`, `weapon: WeaponDefinition`, `target_ac: int`, `target_defenses?`, `cover_bonus: 0/2/5`, `adjacent_to_hostile_creature?`, `extra_damage_dice?: list[str]` | `hit, critical_hit, damage_total, damage_modifier: none/resistance/immunity/vulnerability, effective_ac` |
| `POST /initiative` | `combatants: list[CombatantStats]` | `order: list[{combatant_id, total, dexterity_modifier}]` sorted high→low |
| `POST /death-save` | `combatant_id`, `current_successes`, `current_failures` | `success, critical_stabilize, critical_failure, new_successes, new_failures, stabilized, dead` |
| `POST /movement/validate` | `combatant`, `distance: int`, `difficult_terrain: bool`, `is_prone: bool` | `valid, cost, remaining_speed` |
| `POST /spell/validate` | `caster`, `spell_name`, `spell_level`, `available_slots: SpellSlots`, `is_concentration`, `concentration_active?` | `valid, rejection_reason?, breaks_concentration, slot_consumed?` |
| `POST /concentration-check` | `caster`, `damage_taken: int` | `dc: int`, `roll, total, success` — DC = max(10, damage ÷ 2) |
| `POST /grapple` | `attacker: CombatantStats`, `target: CombatantStats`, `defender_uses_acrobatics?` | `grapple_succeeds`, `contest: {attacker_total, defender_total, attacker_wins}` |
| `POST /shove` | `attacker`, `target`, `shove_type: knock_prone/push_away` | `shove_succeeds`, `contest: ContestResult` |

### Combat Engine

Coordinates **authoritative encounter-time combat state** across initiative, attacks, movement, conditions, spell casting, and action economy. The service does not own a database; instead it reads and writes the active encounter row in the World State Service and calls the Rules Engine for deterministic resolution.

**Authoritative combat model:**

-   **Encounter-owned combatants** — The client does **not** submit authoritative AC, HP, conditions, spell slots, or death-save counts on each action. The Combat Engine reads the acting combatant and target directly from the active encounter snapshot.
-   **Turn-gated actions** — Attack, move, death save, grapple, shove, spell cast, dash, disengage, dodge, help, hide, and ready all validate whose turn it is before mutating state. Opportunity attacks are the exception; they consume a reaction and may occur outside the acting creature’s turn.
-   **Per-turn state** — Each combatant carries transient turn data: movement spent, extra movement from Dash, action / bonus action / reaction availability, attacks used within the current action, disengage / dodge flags, hidden state, help target, and readied trigger metadata.
-   **Capability-driven economy** — The encounter snapshot stores lightweight combat capabilities (`attacks_per_action`, `can_dash_as_bonus_action`, `can_attack_as_bonus_action`, etc.) so the service can support mechanics such as Extra Attack and rogue-style bonus-action mobility without hard-coding class logic into every route.
-   **Player sync** — When a player character’s HP, temp HP, conditions, concentration, spell slots, death saves, or position change in combat, the Combat Engine also patches the corresponding character row in the World State Service.

**Encounter combatant snapshot** (stored inside `encounter_state.combatant_states` in World State):

| Field | Type | Notes |
|-------|------|-------|
| `combatant_id`, `name`, `is_player` | scalar | Identity + player/NPC flag |
| `current_hp`, `max_hp`, `temp_hp` | int | Temp HP is consumed before normal HP |
| `armor_class`, `speed` | int | Used directly for combat resolution |
| `ability_scores`, `proficiency_bonus` | object / int | Passed to Rules Engine |
| `conditions` | `list[str]` | Includes combat conditions such as `prone`, `grappled`, `unconscious` |
| `proficient_skills`, `proficient_saving_throws`, `expertise_skills` | `list[str]` | Used for grapple/shove/hide checks |
| `exhaustion_level` | int 0–6 | Affects movement and ability checks |
| `is_proficient_with_weapon` | bool | Caller asserts equipment proficiency |
| `concentration` | `str?` | Active concentration spell name |
| `spell_slots` | `dict[level_n -> int]` | Remaining slots used by `/combat/spell-cast` |
| `death_saves` | `{successes, failures}` | Authoritative death-save counter |
| `damage_resistances`, `damage_immunities`, `damage_vulnerabilities` | `list[str]` | Passed through to Rules Engine attacks |
| `combat_capabilities` | object | `attacks_per_action`, bonus-action permissions, reaction/opportunity permissions |
| `turn_state` | object | Movement spent, extra movement, action flags, hidden/disengage/dodge/help/ready state |
| `position` | `{x, y, map_id}?` | Optional encounter-map coordinate |

**`combat_capabilities`**:

| Field | Type | Default |
|-------|------|---------|
| `attacks_per_action` | `int >= 1` | `1` |
| `can_attack_as_bonus_action` | `bool` | `false` |
| `can_dash_as_bonus_action` | `bool` | `false` |
| `can_disengage_as_bonus_action` | `bool` | `false` |
| `can_dodge_as_bonus_action` | `bool` | `false` |
| `can_help_as_bonus_action` | `bool` | `false` |
| `can_hide_as_bonus_action` | `bool` | `false` |
| `can_ready_as_bonus_action` | `bool` | `false` |
| `can_opportunity_attack` | `bool` | `true` |

**`turn_state`**:

| Field | Type | Meaning |
|-------|------|---------|
| `movement_spent` | `int` | Movement already spent this turn |
| `extra_movement_budget` | `int` | Additional feet granted by Dash |
| `action_available` / `bonus_action_available` / `reaction_available` | `bool` | Remaining action economy |
| `attacks_used_this_action` | `int` | Used with `attacks_per_action` to support Extra Attack |
| `disengage_active` | `bool` | Suppresses opportunity attacks against this creature until turn end |
| `dodge_active` | `bool` | Stored for later consumers of attack disadvantage |
| `hidden` | `bool` | Result of successful Hide action |
| `help_target_id`, `help_type` | `UUID?`, `str?` | Tracks Help grant for later attack/check consumption |
| `ready_trigger`, `ready_action` | `str?`, `str?` | Stores readied action metadata |

**Key design decisions:**

-   **Rules Engine stays stateless** — The Combat Engine transforms encounter combatants into Rules Engine request payloads; the Rules Engine never reads persistence directly.
-   **Movement is cumulative across the turn** — `/combat/move` validates one move with the Rules Engine, then checks that `movement_spent + movement_cost <= speed + extra_movement_budget`.
-   **Dash is additive, not a one-shot flag** — `/combat/dash` increases `extra_movement_budget` by the combatant’s speed; later move actions consume against the combined budget.
-   **Extra Attack is action-scoped** — A weapon attack using `action_cost=action` increments `attacks_used_this_action`; the action is only consumed once that counter reaches `attacks_per_action`.
-   **Concentration is resolved on damage** — Any damaging attack against a concentrating target triggers a concentration check; dropping to 0 HP breaks concentration immediately.
-   **Disengage is stored as state** — Opportunity attacks reject against targets whose `turn_state.disengage_active=true`.
-   **Monsters vs players at 0 HP** — Player characters gain `unconscious` plus reset death saves at 0 HP. Non-player combatants currently just hit 0 HP; kill/remove semantics remain a higher-layer DM policy.

**Representative request models:**

`POST /combat/start` body — `StartCombatRequest`:

| Field | Type | Notes |
|-------|------|-------|
| `campaign_id`, `session_id`, `user_id` | `UUID` | Audit context |
| `map_id` | `UUID?` | Optional encounter map |
| `combatants` | `list[CombatantEntry]` | Minimum 1 |

Each `CombatantEntry` includes the full initial combat snapshot: HP/temp HP, AC, speed, ability scores, proficiency metadata, concentration, spell slots, death saves, target defenses, capabilities, and optional `position`.

`POST /combat/attack` body — `AttackActionRequest`:

| Field | Type | Notes |
|-------|------|-------|
| `campaign_id`, `session_id`, `user_id` | `UUID` | |
| `attacker_id`, `target_id` | `UUID` | IDs must exist in active encounter |
| `weapon` | `WeaponDefinition` | Passed to Rules Engine |
| `action_cost` | `action / bonus_action / reaction / none` | Default `action` |
| `cover_bonus` | `0 / 2 / 5` | Cover applied to target AC |
| `adjacent_to_hostile_creature` | `bool` | Ranged-in-melee disadvantage hook |
| `extra_damage_dice` | `list[str]` | Sneak Attack, Divine Smite-style extras |
| `expected_updated_at` | `datetime` | Optimistic lock on encounter row |

`POST /combat/spell-cast` body — `SpellCastActionRequest`:

| Field | Type | Notes |
|-------|------|-------|
| `caster_id` | `UUID` | Must be current turn combatant |
| `spell_name` | `str` | |
| `spell_level` | `0–9` | |
| `action_cost` | `action / bonus_action / reaction / none` | |
| `is_concentration` | `bool` | Sets `concentration` on success |
| `requires_verbal`, `requires_somatic` | `bool` | Passed through to Rules Engine |
| `expected_updated_at` | `datetime` | |

**API routes:**

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/combat` | Returns full active encounter snapshot |
| `POST` | `/combat/start` | Rolls initiative, creates encounter, seeds authoritative combatant state |
| `DELETE` | `/combat/end` | Ends active encounter |
| `POST` | `/combat/next-turn` | Advances initiative index; resets the new active combatant’s turn state |
| `POST` | `/combat/attack` | Weapon attack; supports Extra Attack, temp HP, concentration damage handling |
| `POST` | `/combat/move` | Validates movement, tracks cumulative movement spent, clears `prone` when standing |
| `POST` | `/combat/dash` | Adds extra movement budget for the turn |
| `POST` | `/combat/disengage` | Marks `disengage_active` until turn end |
| `POST` | `/combat/dodge` | Marks `dodge_active` until turn end |
| `POST` | `/combat/help` | Stores Help target + help type |
| `POST` | `/combat/hide` | Runs Stealth ability check through Rules Engine; stores `hidden` |
| `POST` | `/combat/ready` | Stores ready trigger + description |
| `POST` | `/combat/opportunity-attack` | Consumes attacker reaction; blocked by target disengage |
| `POST` | `/combat/death-save` | Uses authoritative death-save counters from encounter state |
| `POST` | `/combat/grapple` | Contest via Rules Engine; applies `grappled` on success |
| `POST` | `/combat/shove` | Contest via Rules Engine; applies `prone` or updates pushed position |
| `POST` | `/combat/spell-cast` | Validates slots / concentration and mutates spell-slot + concentration state |

**Events emitted:**

-   `combat.state_changed` — encounter start/end, turn advance, movement, dash/disengage/dodge/help/hide/ready, death saves, grapple/shove, and any combat-state mutation not already represented by a more specific Rules Engine event.
-   `attack.resolved` — enriched attack result including post-damage HP/temp HP and concentration-loss summary.

**Current boundaries:**

-   `hidden`, `dodge_active`, `help_target_id`, and `ready_*` are persisted so later services can consume them, but the Combat Engine does not yet automatically apply all of those modifiers during downstream attacks.
-   Opportunity attacks are implemented as an explicit route rather than an automatic trigger system.
-   No integration tests are defined yet; route tests mock World State and Rules Engine clients.

### Story State Manager

Tracks narrative progression — quests with objectives, plot hooks, and a structured story log. All data is per-campaign. The DM Service is the primary writer; player clients never call this service directly.

**Database schema:**

```sql
CREATE TABLE quests (
    quest_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id        UUID NOT NULL,
    title              TEXT NOT NULL,
    description        TEXT,
    status             TEXT NOT NULL DEFAULT 'active'
                           CHECK (status IN ('hidden','active','completed','failed')),
    giver_npc_id       UUID,
    reward_description TEXT,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ,   -- auto-set by PATCH when status → completed/failed
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Index: (campaign_id, status)

CREATE TABLE quest_objectives (
    objective_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quest_id       UUID NOT NULL REFERENCES quests ON DELETE CASCADE,
    campaign_id    UUID NOT NULL,     -- denormalized for efficient campaign queries
    description    TEXT NOT NULL,
    sequence_order INT NOT NULL DEFAULT 0,
    completed_at   TIMESTAMPTZ        -- NULL = incomplete
);
-- Index: (campaign_id, quest_id)

CREATE TABLE plot_hooks (
    hook_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id     UUID NOT NULL,
    content         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','resolved','dismissed')),
    priority        TEXT NOT NULL DEFAULT 'medium'
                        CHECK (priority IN ('low','medium','high','critical')),
    source_event_id UUID,             -- optional link to the event that created the hook
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ       -- auto-set when status → resolved/dismissed
);
-- Index: (campaign_id, status, priority)

CREATE TABLE story_log (
    entry_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL,
    session_id  UUID,
    entry_type  TEXT NOT NULL CHECK (entry_type IN (
                    'narration','combat_summary','quest_update','hook_note','session_summary')),
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Index: (campaign_id, session_id, created_at)
```

**Key design decisions:**

-   **Hidden quests** — `status=hidden` rows are excluded from all player-visible routes. Only DM-privileged routes (`/dm/quests`) return them, enabling pre-creation before player discovery.
-   **Acts deferred** — hierarchical act/scene structure is not implemented in v1.
-   **N+1 avoided on list routes** — objectives for a quest list are fetched in a single `WHERE quest_id = ANY($1::uuid[])` query and grouped in Python.
-   **DM context endpoint** — `GET /context` returns active quests + open hooks + recent log in one call, eliminating three round trips on the DM Service hot path.
-   **COALESCE PATCH** — `completed_at` is auto-set (`now()`) when status transitions to `completed` or `failed`. `resolved_at` is auto-set on hooks.

**Key request models:**

`POST /quests` body — `QuestCreate`:

| Field | Type | Default |
|-------|------|---------|
| `campaign_id` | `UUID` | required |
| `title` | `str` | required |
| `description` | `str?` | — |
| `status` | `QuestStatus` | `active` |
| `giver_npc_id` | `UUID?` | — |
| `reward_description` | `str?` | — |
| `objectives` | `list[{description, sequence_order}]` | [] — created inline |
| `meta` | `EventMeta?` | — |

`POST /story-log` body — `StoryLogBatch`:

| Field | Type | Notes |
|-------|------|-------|
| `entries` | `list[StoryLogEntry]` | minimum 1 required |
| `meta` | `EventMeta?` | |

Each `StoryLogEntry`: `{ campaign_id, session_id?, entry_type, content }`.

`GET /context` query params: `campaign_id` (required), `session_id?`, `log_limit=20` (max 100).

`GET /context` response — `DMContext`:

| Field | Type | Notes |
|-------|------|-------|
| `campaign_id` | `UUID` | |
| `active_quests` | `list[QuestOut]` | status=active, with objectives attached |
| `open_hooks` | `list[HookOut]` | status=open, sorted critical→high→medium→low |
| `recent_log` | `list[StoryLogOut]` | Most recent `log_limit` entries, returned in chronological order |

**API routes:**

| Method | Path | Query params | Notes |
|--------|------|-------------|-------|
| `POST` | `/quests` | — | `QuestCreate` body; 201 returns `QuestOut` with objectives |
| `GET` | `/quests` | `campaign_id`, `status?` | Excludes hidden; `QuestOut` includes objectives |
| `GET` | `/quests/{id}` | `campaign_id` | 404 if hidden |
| `PATCH` | `/quests/{id}` | `campaign_id` | `QuestUpdate` body; emits status-change event |
| `DELETE` | `/quests/{id}` | `campaign_id` | Hard delete; 204 |
| `POST` | `/quests/{id}/objectives` | `campaign_id` | Add objective to existing quest; 201 |
| `PATCH` | `/quests/{id}/objectives/{obj_id}` | `campaign_id` | `{completed: bool}`; emits `story.objective_completed` |
| `DELETE` | `/quests/{id}/objectives/{obj_id}` | `campaign_id` | 204 |
| `GET` | `/dm/quests` | `campaign_id`, `status?` | All quests including hidden |
| `GET` | `/dm/quests/{id}` | `campaign_id` | Any quest including hidden |
| `POST` | `/hooks` | — | `HookCreate` body; 201 |
| `GET` | `/hooks` | `campaign_id`, `status?`, `priority?` | Sorted by priority |
| `GET` | `/hooks/{id}` | `campaign_id` | |
| `PATCH` | `/hooks/{id}` | `campaign_id` | `HookUpdate` body; emits `story.hook_resolved` when status changes |
| `DELETE` | `/hooks/{id}` | `campaign_id` | 204 |
| `POST` | `/story-log` | — | `StoryLogBatch` body (min 1 entry); returns `list[StoryLogOut]`; 201 |
| `GET` | `/story-log` | `campaign_id`, `session_id?`, `entry_type?`, `limit=50` (max 500) | Ordered DESC (newest first) |
| `GET` | `/context` | `campaign_id`, `session_id?`, `log_limit=20` | `DMContext` snapshot |
| `GET` | `/health` | — | DB connectivity check |

**Events emitted:**

| Event | Trigger |
|-------|---------|
| `story.quest_started` | Quest created as active, or status transitions to active |
| `story.quest_completed` | Status → completed |
| `story.quest_failed` | Status → failed |
| `story.objective_completed` | Objective `completed_at` set |
| `story.hook_created` | Hook created |
| `story.hook_resolved` | Hook status → resolved or dismissed |
| `story.session_summary_created` | Story log batch contains a `session_summary` entry |

### Memory Service

Provides long-term semantic campaign memory using pgvector. Consumes the unified `events:all` Redis Stream asynchronously — services never call it directly to write memories. The DM Service and NPC Service call it to recall memories for prompt context.

**Database schema:**

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE memories (
    memory_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id      UUID NOT NULL,
    subject_type     TEXT NOT NULL,      -- 'campaign'|'character'|'npc'|'world'
    subject_id       UUID NOT NULL,      -- entity the memory is about
    content          TEXT NOT NULL,      -- 1–2000 chars
    embedding        vector(384),        -- BAAI/bge-small-en-v1.5, generated server-side
    importance       INT  NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
    source_event_ids UUID[] NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- B-tree: (campaign_id), (campaign_id, subject_type, subject_id)
-- HNSW:   USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)
```

**`POST /memories` body — `MemoryIn`:**

| Field | Type | Notes |
|-------|------|-------|
| `campaign_id` | `UUID` | |
| `subject_type` | `SubjectType` | `campaign` / `character` / `npc` / `world` |
| `subject_id` | `UUID` | Entity the memory is about |
| `content` | `str` | 1–2000 chars; embedding generated automatically |
| `importance` | `int` 1–5 | default 3; higher = ranked above equally-distant memories |
| `source_event_ids` | `list[UUID]` | Event IDs that produced this memory |

**`GET /memories/recall` query params:**

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `campaign_id` | `UUID` | required | |
| `query` | `str` | required | Text embedded and compared against stored embeddings |
| `subject_type` | `str?` | — | Filter to one subject type |
| `subject_id` | `UUID?` | — | Filter to one subject |
| `limit` | `int` | 5 (max 20) | |

**Recall SQL** (importance-weighted):
```sql
ORDER BY (embedding <=> query_embedding) / importance
LIMIT $limit
```
Higher `importance` divides the cosine distance, making important memories rank above merely-similar ones.

**`PATCH /memories/{id}` body — `MemoryUpdate`:**

| Field | Type | Notes |
|-------|------|-------|
| `importance` | `int?` 1–5 | |
| `content` | `str?` | Re-embeds automatically when content changes |

**Event handlers — auto-create memories from the stream:**

| Event consumed | Memory created |
|---------------|----------------|
| `npc.disposition_changed` | NPC attitude shift toward a character |
| `story.hook_created` | New narrative thread |
| `story.hook_resolved` | Hook resolution summary |
| `dm.narration_generated` | Significant DM narration passages |
| `session.started` / `session.ended` | Session bookmarks |
| `combat.state_changed` | Combat deaths and significant outcomes |
| `world.state_changed` | Notable world changes |

**API routes:**

| Method | Path | Query params / body | Response |
|--------|------|---------------------|----------|
| `POST` | `/memories` | `MemoryIn` body | `201 { memory_id: str }` |
| `GET` | `/memories/recall` | `campaign_id`, `query`, `subject_type?`, `subject_id?`, `limit=5` | `{ memories: list[MemoryOut], query, top_k }` |
| `GET` | `/memories` | `campaign_id`, `subject_id?`, `subject_type?` | `list[MemoryOut]` |
| `GET` | `/memories/{id}` | `campaign_id` (query param) | `MemoryOut` |
| `PATCH` | `/memories/{id}` | `MemoryUpdate` body + `campaign_id` query param | `MemoryOut` |
| `DELETE` | `/memories/{id}` | `campaign_id` query param | `204` |
| `GET` | `/health` | — | `{ status, checks: { database, redis } }` |

**Stream consumer:** `XREADGROUP` + `XAUTOCLAIM` from `events:all`. Consumer group: `memory-service`. Requires Redis 7.0+.

### NPC Interaction Service

Provides NPC identity persistence, prompt assembly, and dialogue history management. This service builds the LLM context; the DM Service calls the LLM. NPCs have a persistent identity that survives across sessions.

**Database schema:**

```sql
CREATE TABLE npc_profiles (
    npc_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id          UUID NOT NULL,
    name                 TEXT NOT NULL,
    role                 TEXT NOT NULL,         -- freetext: innkeeper, villain, quest_giver, etc.
    physical_description TEXT,
    personality_prompt   TEXT NOT NULL CHECK (char_length(personality_prompt) <= 2000),
    is_active            BOOLEAN NOT NULL DEFAULT true,   -- soft-delete flag
    faction_id           UUID,                  -- optional link to World State faction_standing
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Index: (campaign_id, is_active)
-- Migration: ALTER TABLE npc_profiles ADD COLUMN IF NOT EXISTS faction_id UUID

CREATE TABLE npc_secrets (
    secret_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    npc_id                 UUID NOT NULL REFERENCES npc_profiles ON DELETE CASCADE,
    campaign_id            UUID NOT NULL,        -- denormalized
    content                TEXT NOT NULL,
    condition_type         TEXT NOT NULL CHECK (condition_type IN
                               ('always','disposition_gte','quest_status')),
    condition_value        INT,                  -- threshold for disposition_gte
    condition_quest_title  TEXT,                 -- quest title for quest_status
    condition_quest_status TEXT,                 -- expected quest status for quest_status
    revealed_at            TIMESTAMPTZ           -- set on first injection; immutable thereafter
);
-- Index: (campaign_id, npc_id)
```

**Redis dialogue history:**

```
Key:    npc:dialogue:{campaign_id}:{npc_id}:{session_id}
Type:   Redis List — each element is JSON: {role: "player"|"npc", content: str, ts: ISO8601}
TTL:    24 hours (refreshed on every append)
Trim:   LTRIM to last 20 turns (40 messages) after each append
```

**Structured reveal conditions** (evaluated at prompt-assembly time):

| `condition_type` | Parameters | Satisfied when |
|-----------------|------------|----------------|
| `always` | — | Always injected |
| `disposition_gte` | `condition_value: int` | Character's disposition score ≥ threshold |
| `quest_status` | `condition_quest_title: str`, `condition_quest_status: str` | Named quest is at the specified status |

**Disposition and faction roll-up:**

-   Disposition scores live in **World State Service** (`npc_disposition` table), not here. Ranges: 0–30 hostile, 31–60 neutral, 61–80 friendly, 81–100 trusted.
-   If no character-specific score exists, the NPC's **faction standing** (from `GET {world_state}/factions/{faction_id}`) is used as a fallback baseline.
-   Disposition `notes` (freetext reason from World State) are injected into the assembled prompt.

**`POST /npcs/{id}/context` request — `NPCContextRequest`:**

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `campaign_id` | `UUID` | required | |
| `session_id` | `UUID` | required | |
| `character_id` | `UUID` | required | Whose disposition to read |
| `player_message` | `str` | required | Used as memory recall semantic query |
| `dialogue_history_limit` | `int` | 20 (max 100) | Turns to load from Redis |
| `memory_limit` | `int` | 5 (max 20) | Memories to recall from pgvector |

**`POST /npcs/{id}/context` response — `NPCContextResponse`:**

| Field | Type | Notes |
|-------|------|-------|
| `npc_id` | `UUID` | |
| `npc_name` | `str` | |
| `system_prompt` | `str` | **Fully assembled** — DM Service passes this directly to the LLM |
| `dialogue_history` | `list[{role, content, ts}]` | From Redis — append to LLM message list |
| `disposition_score` | `int?` | Character-specific score, or faction standing fallback |
| `disposition_label` | `str` | `hostile` / `neutral` / `friendly` / `trusted` / `unknown` |
| `disposition_notes` | `str?` | Freetext reason from World State; injected into system prompt |
| `faction_standing` | `int?` | Faction score used as fallback (returned for DM transparency) |
| `secrets_injected_count` | `int` | |
| `secrets_injected` | `list[SecretSummary]` | Full content — for DM transparency **only**; never forward to player |
| `memory_context` | `str?` | Joined memory recall text, already injected into `system_prompt` |

`SecretSummary` fields: `{ secret_id, content, condition_type, first_revealed: bool }`. `first_revealed=true` means this call is the first time this secret's condition was ever satisfied.

**External calls during context assembly** (all fail-gracefully):

| Call | Purpose | Fallback |
|------|---------|---------|
| `GET {world_state}/npcs/{npc_id}/dispositions?campaign_id=` | Character-specific score + notes | `(None, None)` |
| `GET {world_state}/factions/{faction_id}?campaign_id=` | Faction standing fallback | `None` |
| `GET {story_state}/quests?campaign_id=` | `{title: status}` map for quest_status conditions | `{}` |
| `GET {memory_service}/memories/recall?campaign_id=&subject_id=&query=` | Past interaction summary | `None` |

**API routes:**

| Method | Path | Query params | Notes |
|--------|------|-------------|-------|
| `POST` | `/npcs` | — | `NPCCreate` body; 201 |
| `GET` | `/npcs` | `campaign_id`, `active_only=true` | |
| `GET` | `/npcs/{id}` | `campaign_id` | No secrets in response |
| `PATCH` | `/npcs/{id}` | `campaign_id` | `clear_physical_description: true` to NULL the field |
| `DELETE` | `/npcs/{id}` | `campaign_id` | Soft-delete (`is_active=false`); 404 if already inactive |
| `POST` | `/npcs/{id}/secrets` | `campaign_id` | DM-privileged; 201 |
| `GET` | `/npcs/{id}/secrets` | `campaign_id` | DM-privileged; returns reveal conditions |
| `PATCH` | `/npcs/{id}/secrets/{secret_id}` | `campaign_id` | Update condition or content |
| `DELETE` | `/npcs/{id}/secrets/{secret_id}` | `campaign_id` | Hard delete; 204 |
| `POST` | `/npcs/{id}/context` | — | **Hot path**; `NPCContextRequest` body; assembles full prompt |
| `GET` | `/npcs/{id}/dialogue` | `campaign_id`, `session_id`, `limit=20` | Reads Redis directly; no external calls |
| `POST` | `/npcs/{id}/dialogue` | — | `DialogueAppend` body; appends turn to Redis; 201 |
| `DELETE` | `/npcs/{id}/dialogue` | `campaign_id`, `session_id` | Clears Redis key; 204 |
| `GET` | `/health` | — | Checks DB + Redis |

**Events emitted:**

| Event | Trigger |
|-------|---------|
| `npc.created` | Profile created |
| `npc.updated` | Profile patched |
| `npc.secret_revealed` | Secret injected for the first time (`revealed_at` was NULL) |

### World State Service

Maintains the authoritative, mutable runtime state of the game across five domains. Every domain is campaign-scoped. State is persisted directly in PostgreSQL; reads are not cached to guarantee freshness.

**Database schema:**

```sql
-- One row per (character_id, campaign_id) — composite PK
CREATE TABLE character_state (
    character_id UUID, campaign_id UUID, user_id UUID,
    name TEXT, class_name TEXT DEFAULT '',
    level INT DEFAULT 1, xp INT DEFAULT 0,
    current_hp INT, max_hp INT DEFAULT 1, temp_hp INT DEFAULT 0,
    armor_class INT DEFAULT 10, speed INT DEFAULT 30,
    ability_scores JSONB,             -- {strength, dexterity, constitution, intelligence, wisdom, charisma}
    conditions TEXT[], exhaustion_level INT DEFAULT 0,
    spell_slots JSONB,                -- {level_1 … level_9}
    concentration TEXT,               -- active spell name, or NULL
    death_saves JSONB,                -- {successes: 0–3, failures: 0–3}
    position JSONB,                   -- {x, y, map_id} or NULL
    inventory JSONB, currency JSONB,  -- {cp, sp, ep, gp, pp}
    active_effects JSONB,             -- list[{name, duration_rounds?, source}]
    proficiency_bonus INT DEFAULT 2,
    proficient_skills TEXT[], proficient_saving_throws TEXT[], expertise_skills TEXT[],
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (character_id, campaign_id)
);

CREATE TABLE npc_disposition (
    npc_id UUID, campaign_id UUID, character_id UUID,
    score INT DEFAULT 50 CHECK (score BETWEEN 0 AND 100),
    notes TEXT DEFAULT '',            -- freetext reason for current score
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (npc_id, campaign_id, character_id)
);

CREATE TABLE world_flags (
    campaign_id UUID, key TEXT, value JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (campaign_id, key)
);

CREATE TABLE encounter_state (
    encounter_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL UNIQUE,   -- one active encounter per campaign
    map_id UUID,
    round INT DEFAULT 1, current_turn_index INT DEFAULT 0,
    initiative_order JSONB,             -- list[{combatant_id, name, total, is_player}]
    combatant_states JSONB,             -- {str(combatant_id): {combatant_id, name, is_player, current_hp, max_hp, conditions, position}}
    active BOOL DEFAULT TRUE,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE faction_standing (
    campaign_id UUID, faction_id TEXT,
    standing INT DEFAULT 0 CHECK (standing BETWEEN -100 AND 100),
    notes TEXT DEFAULT '',
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (campaign_id, faction_id)
);
```

**Key design decisions:**

-   **Atomic character PATCH** — uses `SELECT FOR UPDATE` inside an explicit transaction to serialise concurrent HP changes. Supports `expected_updated_at` for explicit optimistic concurrency; returns 409 on mismatch.
-   **Encounter combatant merge** — `combatant_states` is JSONB; partial updates use `combatant_states || $patch::jsonb` (merge operator, not replace). Returns 409 if `updated_at` has changed.
-   **World flags** — single JSONB column per campaign; PATCH merges keys rather than replacing the whole document.

**Key request models:**

`PATCH /characters/{id}` body — `CharacterUpdate` (all fields optional):

```
current_hp?, max_hp?, temp_hp?, armor_class?, speed?,
conditions?, exhaustion_level?, spell_slots?, concentration?,
death_saves?, position?, inventory?, currency?, active_effects?,
xp?, level?, ability_scores?, proficiency_bonus?,
proficient_skills?, proficient_saving_throws?, expertise_skills?,
expected_updated_at?   ← optimistic concurrency; 409 if row changed
event_meta?: { session_id, user_id }
```

`PUT /encounter` / `POST-equivalent` body — `EncounterCreate`:
```
campaign_id, map_id?,
initiative_order: list[{combatant_id, name, total, is_player}],
combatant_states: {str(combatant_id): CombatantState},
event_meta?
```

`PATCH /encounter` body — `EncounterUpdate`:
```
round?, current_turn_index?,
combatant_states?  ← merged into existing JSONB column (|| operator)
expected_updated_at  ← REQUIRED; 409 if row was updated since last read
event_meta?
```

`PATCH /world/flags` body — `WorldFlagsUpdate`:
```
flags: { "key": value, … }   ← keys are merged/upserted; existing unmentioned keys preserved
event_meta?
```

`PATCH /npcs/{npc_id}/dispositions` body — `DispositionUpdate`:
```
character_id: UUID,
score: int 0–100,
reason: str,   ← stored in notes field; emitted in event payload
event_meta?
```

**API routes (18 total):**

| Method | Path | Query params | Notes |
|--------|------|-------------|-------|
| `POST` | `/characters` | — | Creates; body is `CharacterCreate` |
| `GET` | `/characters` | `campaign_id` | Returns `list[CharacterState]` |
| `GET` | `/characters/{id}` | `campaign_id` | Returns `CharacterState` |
| `PUT` | `/characters/{id}` | — | Full replace; body is `CharacterCreate`; 201 |
| `PATCH` | `/characters/{id}` | `campaign_id` | Atomic update; 409 on concurrency conflict |
| `DELETE` | `/characters/{id}` | `campaign_id` | Hard delete; 204 |
| `GET` | `/npcs/{npc_id}/dispositions` | `campaign_id` | Returns `{npc_id, campaign_id, dispositions: list[DispositionRecord]}` |
| `PATCH` | `/npcs/{npc_id}/dispositions` | — | Upsert score; emits `npc.disposition_changed` |
| `GET` | `/world/flags` | `campaign_id` | Returns `{campaign_id, flags: dict}` |
| `GET` | `/world/flags/{key}` | `campaign_id` | Returns `{campaign_id, key, value}` — 404 if absent |
| `PATCH` | `/world/flags` | — | Merge-update; body is `WorldFlagsUpdate` |
| `DELETE` | `/world/flags/{key}` | `campaign_id` | Deletes one flag; 204 |
| `GET` | `/encounter` | `campaign_id` | Returns full `EncounterState` |
| `PUT` | `/encounter` | — | Create or replace; body is `EncounterCreate` |
| `PATCH` | `/encounter` | — | Partial update with combatant merge; 409 on version conflict |
| `DELETE` | `/encounter` | `campaign_id` | Ends encounter; 204 |
| `GET` | `/factions/{id}` | `campaign_id` | Returns `FactionStandingRecord` |
| `PATCH` | `/factions/{id}` | `campaign_id` | Upsert standing score |

### Event Log Service

Provides an **append-only audit trail** of every significant game event across all services. No service modifies or deletes event rows. The log serves two purposes: **debugging LLM decisions** (why did the DM say that?) and **campaign replay** (reconstruct world state at any point in time).

**Database schema:**

```sql
CREATE TABLE events (
    event_id        UUID PRIMARY KEY,          -- generated by emitting service
    campaign_id     UUID NOT NULL,
    session_id      UUID NOT NULL,
    user_id         UUID NOT NULL,             -- JWT sub claim
    event_type      TEXT NOT NULL,
    aggregate_id    UUID NOT NULL,
    aggregate_type  TEXT NOT NULL,             -- 'character'|'npc'|'combat'|'story'|'world'
    payload         JSONB NOT NULL DEFAULT '{}',
    source_service  TEXT NOT NULL,
    llm_prompt_hash TEXT,                      -- SHA-256 of LLM prompt; only DM Service sets this
    occurred_at     TIMESTAMPTZ NOT NULL
);
-- Indexes: (campaign_id, session_id, occurred_at DESC)
--          (campaign_id, aggregate_id, occurred_at DESC)
```

**`POST /events` body — `EventIn`:**

| Field | Type | Notes |
|-------|------|-------|
| `event_id` | `UUID` | Generated by the emitting service (idempotent: duplicate silently ignored) |
| `campaign_id` | `UUID` | |
| `session_id` | `UUID` | |
| `user_id` | `UUID` | JWT `sub` claim |
| `event_type` | `str` | See taxonomy below |
| `aggregate_id` | `UUID` | ID of the affected entity |
| `aggregate_type` | `str` | `character` / `npc` / `combat` / `story` / `world` |
| `payload` | `dict` | Full event data — rule inputs, dice rolls, outcomes |
| `source_service` | `str` | Which microservice emitted the event |
| `llm_prompt_hash` | `str?` | SHA-256 of LLM prompt; only set by DM Service |
| `occurred_at` | `datetime` | Wall time of the emitting service |

**API routes:**

| Method | Path | Query params | Response |
|--------|------|-------------|----------|
| `POST` | `/events` | — | `201 { event_id: str }` |
| `GET` | `/events` | `campaign_id` (required), `session_id?`, `aggregate_id?`, `aggregate_type?`, `event_type?`, `limit=100` | `list[EventOut]` ordered by `occurred_at DESC` |
| `GET` | `/health` | — | `{ status, checks: { database, redis } }` |

**Redis publishing** — every written event is simultaneously published to:
-   `events:campaign:{campaign_id}` — per-campaign stream, consumed by session-level listeners.
-   `events:all` — unified cross-campaign stream, consumed by the Memory Service (`XREADGROUP`, consumer group `memory-service`).

**Event taxonomy:**

-   `dice.rolled` — result, sides, modifier, purpose
-   `ability_check.resolved` — DC, roll, pass/fail, rule reference
-   `attack.resolved` — attacker, target, to-hit roll, damage, hit/miss
-   `spell.cast` — spell name, slot used, targets, rule validation result
-   `combat.state_changed` — initiative order, HP delta, condition applied/removed
-   `story.quest_started` / `story.quest_completed` / `story.quest_failed` — quest lifecycle
-   `story.objective_completed` — individual objective checked off
-   `story.hook_created` / `story.hook_resolved` — plot hook lifecycle
-   `story.session_summary_created` — session summary appended to story log
-   `npc.created` / `npc.updated` — NPC profile changes
-   `npc.disposition_changed` — old score, new score, reason
-   `npc.secret_revealed` — first time a secret condition is satisfied
-   `world.state_changed` — which world-state key changed and to what value
-   `dm.narration_generated` — prompt hash, model used, token count
-   `session.started` / `session.ended`

**Design rules:**

-   Events are written **synchronously** before the service returns its response — a failed write is a failed request.
-   `ON CONFLICT (event_id) DO NOTHING` — duplicate events from retries are silently dropped.
-   Services call `event_log.emit(...)` as a fire-and-forget (non-blocking on failure) from their own processes; only the Event Log Service writes to the database.
-   The Event Log Service exposes a read API used by the DM Service to retrieve the last N events for a session (prompt context) and by admin tooling for campaign replay.

### Map Service

Manages map data, player-visible state (fog-of-war), and encounter
token placement. Geometric computation (line of sight, area-of-effect)
is intentionally **deferred to the client** to avoid over-engineering
the server and to leverage GPU-accelerated rendering on the device.

**Scope — server responsibilities:**

-   Store map definitions: tile layers, walls, doors, and light sources
    as structured data (GeoJSON-style feature collections) in
    PostgreSQL; large tile/image assets in MinIO.
-   Maintain **fog-of-war state** per `(campaign_id, character_id)` as
    a bitmask or explored-cell set — what each character has seen,
    persisted across sessions.
-   Store **encounter token positions** — authoritative `(x, y)` for
    every combatant, updated by the Combat Engine on each move action.
-   Expose a **map snapshot API** that returns the current map
    definition, token positions, and the requesting character's
    fog-of-war state in a single response.

**Scope — client responsibilities (Flutter):**

-   **Line-of-sight calculation** — computed on-device each frame using
    the wall/door geometry received from the server. Libraries such as
    [dart_earcut](https://pub.dev/packages/dart_earcut) or a simple
    shadowcasting algorithm keep this off the server entirely.
-   **Area-of-effect overlays** — spell radius, cone, and line shapes
    are rendered client-side from the spell definition and target point.
-   **Tile rendering and zoom/pan** — handled by the Flutter canvas;
    tile images are fetched directly from MinIO via pre-signed URLs.

**Deferred features (post-MVP):**

-   Procedural map generation.
-   Server-side LoS validation for cheat prevention (anti-cheat is
    not a priority for a self-hosted, trusted-player platform).
-   Dynamic lighting and shadow rendering beyond basic fog-of-war.

**Map data model:**

  Entity            Fields
  ----------------- -------------------------------------------------------
  `map`             `map_id`, `campaign_id`, name, width, height, tile_size
  `map_layer`       `layer_id`, `map_id`, type (`terrain`/`object`/`roof`), GeoJSON features
  `fog_of_war`      `map_id`, `character_id`, explored cell bitmask
  `encounter`       `encounter_id`, `map_id`, `campaign_id`, active bool
  `token`           `token_id`, `encounter_id`, `aggregate_id`, `aggregate_type`, x, y, visible

## Sequence: Event Audit (Combat Example)

``` mermaid
sequenceDiagram
    participant P as Player
    participant API as Session API
    participant CE as Combat Engine
    participant RE as Rules Engine
    participant EL as Event Log Service
    participant REDIS as Redis Stream
    participant MEM as Memory Service

    P->>API: Attack goblin
    API->>CE: Execute attack
    CE->>RE: Validate & roll
    RE->>EL: Write dice.rolled event (payload: d20+4=17)
    RE->>EL: Write ability_check.resolved (hit)
    RE->>EL: Write dice.rolled event (payload: d6+2=5 dmg)
    RE-->>CE: Hit + 5 damage
    CE->>EL: Write attack.resolved event
    CE->>EL: Write combat.state_changed (goblin HP 12→7)
    CE->>REDIS: Publish events to stream
    REDIS-->>MEM: Async: update pgvector summary
    CE-->>API: Updated combat state
    API-->>P: Result
```

## Sequence: Login and Session Start

``` mermaid
sequenceDiagram
    participant P as Player (Flutter)
    participant T as Traefik
    participant AUTH as Keycloak
    participant API as Session API
    participant DB as PostgreSQL

    P->>AUTH: POST /token (username + password)
    AUTH-->>P: JWT (access_token + refresh_token)
    P->>T: POST /session/start (Bearer JWT)
    T->>AUTH: Validate JWT (JWKS)
    AUTH-->>T: Valid
    T->>API: Request + decoded claims
    API->>DB: Load character & campaign for user_id
    DB-->>API: Character + campaign state
    API-->>P: Session established
```

## Sequence: Exploration

``` mermaid
sequenceDiagram
    participant P as Player
    participant API as Session API
    participant DM as Dungeon Master
    participant MEM as Memory
    participant WS as World State
    participant MAP as Map

    P->>API: Enter room
    API->>WS: Load world state
    API->>MAP: Load room
    API->>MEM: Retrieve relevant memories
    API->>DM: Build prompt
    DM-->>API: Narrative description
    API-->>P: Display room
```

## Sequence: Combat

``` mermaid
sequenceDiagram
    participant P as Player
    participant API as Session API
    participant DM as Dungeon Master
    participant CE as Combat Engine
    participant RE as Rules Engine

    P->>API: Attack goblin
    API->>CE: Execute attack
    CE->>RE: Validate rules & roll
    RE-->>CE: Hit + damage
    CE-->>API: Updated combat state
    API->>DM: Narrate outcome
    DM-->>API: Combat narration
    API-->>P: Result
```

## Sequence: NPC Conversation

``` mermaid
sequenceDiagram
    participant P as Player
    participant API as Session API
    participant DB as PostgreSQL
    participant REDIS as Redis
    participant MEM as Memory Service
    participant NPC as NPC Service
    participant DM as Dungeon Master

    P->>API: Talk to innkeeper
    API->>DB: Load NPC profile, secrets, disposition score
    API->>REDIS: Load recent dialogue history (last N turns)
    API->>MEM: Retrieve long-term NPC memory (pgvector)
    API->>NPC: Build prompt (persona + eligible secrets + memory + disposition + history)
    NPC-->>API: Dialogue response + intent flags
    API->>DM: Narrate response in world context
    DM-->>API: Final narrative
    API->>DB: Update disposition score (if changed)
    API->>REDIS: Append turn to dialogue history
    API-->>P: Dialogue + narration
    Note over API,MEM: On conversation end: write summary to Memory Service
```

## Deployment

Each service is containerized and independently scalable within K3s.
