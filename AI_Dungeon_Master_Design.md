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

Implements D&D 5e SRD mechanics.

### Combat Engine

Handles initiative, attacks, damage, conditions, and movement.

### Story State Manager

Tracks plot, quests, unresolved hooks, and campaign progression.

### Memory Service

Uses pgvector to retrieve long-term memories and relationships.

### NPC Interaction Service

Roleplays any NPC in the campaign. Each NPC has a persistent identity
that survives across sessions — personality, secrets, and per-character
relationships all influence how the NPC speaks and what they reveal.

**NPC identity model (stored in PostgreSQL):**

-   **Profile** — `npc_id`, `campaign_id`, name, role (e.g., innkeeper,
    villain, quest giver), physical description, and a freetext
    `personality_prompt` that is injected verbatim into the LLM system
    prompt (e.g., *"Gruff, distrustful of magic users, secretly grieving
    a lost daughter"*).
-   **Secrets** — a list of `{ secret_id, content, reveal_condition }`
    rows. The service only injects a secret into the prompt when its
    `reveal_condition` is met (e.g., disposition score ≥ 70, or a
    specific quest flag is set). Secrets are never sent to the client
    directly.
-   **Disposition scores** — a `(npc_id, character_id, score int)`
    table tracking each NPC's attitude toward each player character.
    Score ranges: 0–30 hostile, 31–60 neutral, 61–80 friendly,
    81–100 trusted. Scores are updated by the DM Service after each
    interaction based on player choices.
-   **Faction memberships** — optional link to a faction record in
    World State; disposition toward the faction rolls up to the NPC.

**Per-conversation state:**

-   Short-term dialogue context (last N turns) is held in **Redis**
    for the duration of the session.
-   Long-term memory (summarised past interactions) is retrieved from
    **pgvector** via the Memory Service at conversation start.
-   After the conversation ends, the DM Service writes a summary back
    to the Memory Service and persists any disposition score changes.

**Prompt assembly order:**

1.  NPC system persona (`personality_prompt`)
2.  Applicable secrets (filtered by reveal condition)
3.  Long-term memory summary from pgvector
4.  Current disposition level toward the active character
5.  Recent dialogue history from Redis
6.  Player's latest message

### World State Service

Maintains canonical state of the world.

### Event Log Service

Provides an **append-only audit trail** of every significant game event
across all services. No service modifies or deletes event rows.
The log serves two purposes: **debugging LLM decisions** (why did the
DM say that?) and **campaign replay** (reconstruct world state at any
point in time).

**Event record schema:**

  Field              Type        Description
  ------------------ ----------- --------------------------------------------------
  `event_id`         UUID        Globally unique, generated by the emitting service
  `campaign_id`      UUID        Campaign this event belongs to
  `session_id`       UUID        Play session within the campaign
  `user_id`          UUID        Acting player (`sub` claim from JWT)
  `event_type`       enum        See event taxonomy below
  `aggregate_id`     UUID        ID of the affected entity (character, NPC, etc.)
  `aggregate_type`   string      `character`, `npc`, `combat`, `story`, `world`
  `payload`          JSONB       Full event data (rule inputs, dice rolls, outcomes)
  `llm_prompt_hash`  text        SHA-256 of the prompt sent to the LLM (nullable)
  `occurred_at`      timestamptz Emitting service wall time

**Event taxonomy:**

-   `dice.rolled` — result, sides, modifier, purpose
-   `ability_check.resolved` — DC, roll, pass/fail, rule reference
-   `attack.resolved` — attacker, target, to-hit roll, damage, hit/miss
-   `spell.cast` — spell name, slot used, targets, rule validation result
-   `combat.state_changed` — initiative order, HP delta, condition applied/removed
-   `story.hook_created` / `story.hook_resolved` — quest/plot changes
-   `npc.disposition_changed` — old score, new score, reason
-   `world.state_changed` — which world-state key changed and to what value
-   `dm.narration_generated` — prompt hash, model used, token count
-   `session.started` / `session.ended`

**Design rules:**

-   Events are written **synchronously** before the service returns its
    response — a failed write is a failed request.
-   Services publish events to a **Redis Stream** (`events:campaign:{id}`)
    in addition to writing to PostgreSQL; the Memory Service consumes
    the stream to update pgvector summaries asynchronously.
-   The Event Log Service exposes a read API used by the DM Service
    to retrieve the last N events for a session (used for prompt
    context) and by an admin endpoint for campaign replay.

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
