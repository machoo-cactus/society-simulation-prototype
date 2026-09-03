# Research Data

**Owner:** Current dataset and SQLite behavior, privacy, queries, exports,
aggregation, deletion, and capture limitations.

Identifiers are listed in [Current contracts](CURRENT_CONTRACTS.md).

Research data observes execution. It is never live simulation authority,
character perception, memory input merely by being recorded, or
character-controller context.

## Contract and flow

```text
prepared scenario + deterministic runner
  -> domain events + read-only phase hooks + private application traces
  -> immutable versioned dataset records
  -> current SQLite normalized and derived projections
  -> REST queries, server-rendered explorer, exports, aggregation
```

`RunDataCollector` subscribes before the run starts. Raw records are the source
of truth; relational and derived tables are query projections. Capture must not
change system order, physical outcomes, perception, memory routing, or telemetry
frequency.

## Dataset envelope

Every immutable dataset record contains:

- `record_id`, `run_id`, and positive monotonic `sequence`;
- `record_type`, per-record `schema_id` and `schema_version`;
- `category`, `source`, runner `phase`, and `visibility`;
- simulation tick/time and optional nondeterministic wall time;
- primary and related entity IDs;
- source-event, causation, and correlation IDs;
- typed goal, plan, action, decision, model, tool, interaction, engagement,
  engagement-group, engagement-invocation, perception, memory,
  transaction-request, and operator-intervention join IDs;
- producer metadata and a complete JSON payload.

Categories are `RUN`, `PROVENANCE`, `EVENT`, `STATE`, `TRANSITION`, `GOAL`,
`DECISION`, `MODEL`, `TOOL`, `ACTION`, `INTERACTION`, `PERCEPTION`, `MEMORY`,
`INFORMATION`, `ENVIRONMENT`, `OPPORTUNITY`, `POPULATION`, and `OTHER`.

Capture phases are:

| Phase | Boundary |
| --- | --- |
| `run_initial` | Materialized initial state |
| `tick_pre_systems` | Clock advanced, before ordered systems |
| `tick_post_systems` | Ordered systems complete, before cognition settlement |
| `tick_post_cognition` | Global cognition barrier settled and `simulation.tick` emitted |
| `run_final` | Collector finalization |
| `unspecified` | Not associated with a phase hook |

## Privacy

Visibility classes are:

- `PUBLIC`: safe for ordinary disclosure;
- `OPERATOR`: omniscient operator/research data, not character knowledge;
- `PRIVATE_RESEARCH`: may contain profiles, synthesized situations, prompts,
  rendered/model messages, tool calls, reasons, retrieved memories or
  information, embeddings, hidden closed-container contents, ownership,
  custody, held-object state, raw engagement intent/reason, compiler scene,
  proposed summary/response, rejected groups, normalized compiler arguments,
  and detailed authoritative state.

A domain event whose payload declares private visibility is captured and
projected as `PRIVATE_RESEARCH`; an event cannot downgrade its private payload
to an operator-visible projection.

Filtered raw/normalized queries, the dataset explorer, filtered NDJSON, and
analysis bundles exclude `PRIVATE_RESEARCH` by default. Summary/entity and
physical distributions and observed-schema counts in the data dictionary apply
the same default. Static table/field definitions remain visible. Explicit
access requires `include_private=true`; asking for private visibility without
the opt-in is rejected.

The complete export is intentionally unfiltered and identifies that fact in
its manifest plus `X-Stage0-Private-Included` and
`X-Stage0-Privacy-Warning` headers:

```http
GET /simulation/runs/{run_id}/exports/complete
```

Treat complete/private-enabled exports and SQLite databases as restricted
research artifacts. Visibility flags are not a substitute for authentication,
authorization, encryption, consent, retention, or redaction policy.

## SQLite schema

The configured database accepts only the schema listed in
[Current contracts](CURRENT_CONTRACTS.md). A new empty database is initialized
directly. A populated database with any other version fails explicitly and is
neither migrated nor recreated.

Important projection groups:

| Group | Tables |
| --- | --- |
| Raw and relations | `records`, `record_relations` |
| State | `state_samples`, `state_deltas`, `physical_object_states`, `physical_relation_samples` |
| Goals/plans/actions | `goals`, `goal_transitions`, `plans`, `goal_action_links`, `action_instances`, `action_transitions` |
| Cognition/tools | `decisions`, `decision_options`, `model_requests`, `model_turns`, `tool_executions` |
| Interactions | `interactions`, `interaction_participants`, `interaction_events` |
| Engagements | `engagements`, `engagement_groups`, `engagement_invocations` |
| Perception/memory/information | `perception_facts`, `perception_deliveries`, `memory_operations`, `memory_relations`, `information_retrievals` |
| In-world text | private `text_content_snapshots` plus canonical `text.*` action and delivery records |
| Derived research features | `opportunity_samples`, `transition_samples`, `action_episodes`, `decision_episodes`, `goal_episodes`, `interaction_episodes`, `population_samples`, `resource_samples`, `resource_flows` |

Episodic-memory, information-document, and in-world text snapshot persistence
is separate from the analytical run projections. Text snapshots preserve
artifact revision history, collections, addresses, groups, and unread state;
they remain research records rather than checkpoint authority.

Derived feature contracts are independently versioned. They cover consecutive
state transitions, choice opportunities/non-choices, terminal action/decision/
goal/interaction episodes, population states, resource utilization, and
resource flows. Physical object feature v2 records capture stable object identity,
microcell pose/footprint/occupied cells, effective obstruction, open/locked
state, semantic mass/dimensions, wearable and scent capabilities,
movement/vision/hearing/smell transmission, slots, live
parent/custody/held/equipped relations, and `SpatialIndex` revisions. Character
physical-state v2 additionally captures effective senses, equipment, carried
load, compact movement, posture, hands, interaction lineage, and the
independent abstract-possession representation. They are research
observations, never checkpoint authority.

`physical_object_states` normalizes each captured object observation: identity
and classification; microcell pose, cardinal orientation, local footprint and
occupied cells; effective movement/vision obstruction; open/locked state;
capabilities and slot occupancy; live parent relation; custody/held state;
descriptive ownership; interaction anchors; and `SpatialIndex` indexed,
dynamic, revision, and topology-revision values.

`physical_relation_samples` normalizes parent/relation/slot, room, custody, and
held edges for stable filtering and reconstruction of analytical relations.
Rows use deterministic object ordering. They are observations of ECS/index
truth, not a mutable object graph or restart format.

`engagements` normalizes actor and action/plan/decision/tool/compiler lineage,
references, scene/catalog/prompt versions, compilation and terminal status,
ticks/times, and private compiler material. `engagement_groups` records stable
ordinal and required-atomic identity, validation/execution status, private
rejection/proposal details, failure reason, and grounded outcome.
`engagement_invocations` records stable invocation identity, capability,
consequence tier, subject, private target/proposed/normalized/result material,
status, and grounded outcome. Public/default queries remove the columns marked
private by the store rather than returning redacted-looking values.

Engagements also produce normalized `interaction_episodes` with
`interaction_type=engagement`, terminal status including `partial`, and
`initiating_engagement_id` plus ordinary action/decision/tool/correlation
lineage. These records describe observed execution; they are not resumable
programs.

Character state samples include
`stage0.feature.character_physical_state.v2`: 5×5 body pose/occupied cells,
posture/support, hands, live parent relation, interaction request/execution
lineage, microcell movement with compact path segments, navigation state, and
index revisions. Its hybrid-possession section deliberately records abstract
item quantities, physically held object IDs, and physically custodied object
IDs as independent representations.

## Query API

All per-run research queries use:

```text
/simulation/runs/{run_id}/data
```

| Route | Result |
| --- | --- |
| `/data` | Summary, counts, outcomes, capture completeness |
| `/data/schema` | Generated data dictionary and observed schemas |
| `/data/records` | Immutable raw records |
| `/data/goals`, `/decisions`, `/actions`, `/interactions` | Normalized lifecycles |
| `/data/engagements` | Normalized engagement lifecycle and compiler/action lineage |
| `/data/engagement-groups` | Validation and execution status per atomic group |
| `/data/engagement-invocations` | Capability invocation status and grounded outcomes |
| `/data/state?kind=sample|delta` | State samples or deltas |
| `/data/physical-object-states` | Normalized physical object states |
| `/data/physical-relations` | Normalized parent, slot, custody, and held relations |
| `/data/transitions?kind=state|goal|action` | State/lifecycle transitions |
| `/data/aggregates?family=population|resource_samples|resource_flows` | Aggregate features |
| `/data/episodes/{family}` | `actions`, `decisions`, `goals`, or `interactions` |
| `/data/model-requests`, `/tool-executions` | Model/tool projections |
| `/data/perception?kind=facts|deliveries` | Perception projections |
| `/data/memory?kind=operations|retrievals` | Memory/information retrieval |
| `/data/opportunities` | Opportunity and non-choice samples |

Common filters cover record/category/schema, primary or related entity,
physical object/room/parent/relation/phase/open/locked state, physical
interaction verb/type, tick/time bounds, visibility, status/outcome, typed
lineage IDs including `engagement_id`, `engagement_group_id`, and
`engagement_invocation_id`, cursor, and `limit` from 1 through 1000. Raw-record
cursors are integer sequences; analytical cursors are stable opaque values.

```powershell
curl "http://127.0.0.1:8000/simulation/runs/RUN/data/actions?entity_id=agent-001&limit=50"
curl "http://127.0.0.1:8000/simulation/runs/RUN/data/engagement-groups?engagement_id=ENGAGEMENT"
curl "http://127.0.0.1:8000/simulation/runs/RUN/data/records?category=MODEL&include_private=true"
```

Persisted event history and research data remain queryable after a managed run
stops.

## Explorer and exports

Open `/ui/datasets/{run_id}/` for summary, schema, raw records, lifecycle
timelines, transitions, population/resources, filters, details, pagination, and
downloads. The private-data checkbox is an explicit opt-in. Ordinary GET forms
remain the no-JavaScript fallback.

Exports:

| Route | Contents | Privacy |
| --- | --- | --- |
| `/simulation/runs/{run_id}/exports/complete` | Manifest then all ordered raw JSONL records | Unfiltered |
| `/simulation/runs/{run_id}/exports/records` | Filtered NDJSON, maximum 1000 records | Private excluded by default |
| `/simulation/runs/{run_id}/exports/bundle` | Manifest, schema, filtered raw NDJSON, normalized/derived CSVs | Private excluded by default |

JSON-valued CSV columns contain canonical JSON strings. Bundle file ordering is
stable. Engagement lineage filters apply to filtered records and bundles.
Private-enabled exports can include raw controller/compiler content and must be
handled as restricted research artifacts. The complete export is always
private-inclusive.

## Data management

`/ui/data/` and `/simulation/data/*` use the configured shared database as the
catalog authority across application restarts. Ownership leases distinguish
live processes from abandoned owners. Closed, missing, or expired ownership may
be reconciled as interrupted capture.

Data management supports filtering, cross-page selection, compatibility
grouping, pooled observation-weighted and per-run macro aggregation, JSON/CSV
aggregate exports, deletion preview, and atomic permanent deletion.
Catalog entries expose `mainline` or `branch` lineage plus denormalized root
run, immediate parent run, and parent checkpoint identifiers. Catalog and UI
filters can select mainline and branch runs independently.

Private-derived statistics are excluded by default and require an explicit
operator opt-in, which adds a warning. Raw private payloads are never rendered
in aggregate views.
Mixed schema/scenario/capture selections remain explicit rather than silently
combined.

Deletion requires a fresh exact-selection token, checkbox, and typed
confirmation. Active or not-fully-finalized runs are rejected. Successful
deletion clears operator-session references and allows SQLite page reuse but
does not automatically run `VACUUM`.

Canonical management routes are listed in [API and UI workflows](API_AND_UI.md).

## Rebuild and completeness

`SQLiteDatasetStore.rebuild_run_projections(run_id)` transactionally rebuilds
the supported derived and selected normalized projections from ordered raw
records. Failure rolls back without changing raw data or replacing previous
projections. Physical object states, physical relations, and physical
interaction lifecycle rows are rebuildable and idempotent. Not every directly
captured lifecycle table is rebuildable yet.

`capture_complete` is true only when a run ended `completed` or `stopped` and
recorded `run_final`. A paused exact-head checkpoint may instead leave capture
`suspended`; same-run restoration reclaims the dataset and continues at the
next sequence. Historical restoration creates a branch-local dataset at the
restored state rather than copying or truncating parent records. Summaries also
report coverage-manifest presence, sequence gaps, final tick/time, and capture
failure. Sink or projection errors are explicit and mark the run failed for
research capture.

## Current limitations

- Datasets can be large; there is no retention, compression, sampling,
  deduplication, Parquet, or distributed-storage policy.
- Live provider choices require recording/replay for reproduction.
- Projection rebuild is not complete for every normalized table.
- Tier 2+ engagement effects such as injury, theft/custody transfer, forced
  movement, relationships/reputation, and arbitrary object mutation are not
  present in the current records because the runtime does not implement them.
- Statistical prior fitting and approximate populations are future work.
- Datasets are not checkpoint authority; continuation is governed by the
  separate strict checkpoint contract and exact persisted-head validation.
