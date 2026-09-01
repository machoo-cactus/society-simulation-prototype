# Research Data Collection

Stage 0 records exhaustive, versioned ground-truth data for analysis of detailed
simulation runs. The dataset is an observation of authoritative execution, not
a source of simulation behavior, character perception, or character-controller
context.

This foundation is intended to support later research. Statistical priors,
approximate populations, focus selection, materialisation/dematerialisation,
and live checkpoint/resume are deliberately deferred.

## Architecture and data flow

```text
scenario + resolved characters + run configuration
  -> deterministic runner and ordered domain systems
       -> authoritative domain events
       -> read-only runner phase hooks
       -> application-only research recorder
  -> RunDataCollector
       -> immutable raw records
       -> normalized SQLite projections
       -> derived analysis-ready features
  -> query service
       -> REST API
       -> server-rendered dataset explorer
       -> NDJSON, JSONL, and ZIP/CSV exports
```

The domain event bus remains the authority for physical and lifecycle events.
Read-only phase hooks capture authoritative state without becoming ordered
systems. The application-only research recorder captures information that must
not be broadcast as a domain event, including complete controller requests,
rendered model requests and normalized model turns, retrieval context, and
private character/profile/situation context.

`RunDataCollector` subscribes before the run begins. It mirrors domain events,
drains research traces at deterministic application boundaries, captures phase
state, and writes the raw record and its relational projection to
`SQLiteDatasetStore`. Research capture must not change system order, physical
outcomes, perception, memory routing, or telemetry frequency.

## Dataset and raw record contract

The current dataset contract is `stage0.dataset.v2`. Every immutable raw record
has a per-record `schema_id` and `schema_version` plus this envelope:

| Field | Meaning |
| --- | --- |
| `record_id` | Stable run-local record ID, generated from run ID and sequence when not supplied |
| `run_id` | Simulation run identity |
| `sequence` | Positive, monotonic raw-record order within the run |
| `record_type` | Concrete capture kind, such as `event`, `state_sample`, or `model_turn` |
| `schema_id`, `schema_version` | Payload contract identity and version |
| `category` | High-level taxonomy: `RUN`, `PROVENANCE`, `EVENT`, `STATE`, `TRANSITION`, `GOAL`, `DECISION`, `MODEL`, `TOOL`, `ACTION`, `INTERACTION`, `PERCEPTION`, `MEMORY`, `INFORMATION`, `ENVIRONMENT`, `OPPORTUNITY`, `POPULATION`, or `OTHER` |
| `source` | Producer: dataset collector, domain event, runner, application, model provider, derived projector, operator, or import |
| `phase` | Runner boundary associated with capture |
| `simulation_tick`, `simulation_time` | Deterministic fixed-clock position |
| `wall_time` | Optional nondeterministic diagnostic time |
| `visibility` | `PUBLIC`, `OPERATOR`, or `PRIVATE_RESEARCH` |
| `agent_id` | Legacy primary-character alias retained for compatibility |
| `subject_id` | Primary entity described by the record |
| `related_entity_ids` | Ordered secondary entity identities |
| `source_event_id` | Domain event mirrored or summarized by the record |
| `causation_id`, `correlation_id` | Causal predecessor and root/workflow correlation |
| typed join IDs | Goal, plan, action, decision, model request, tool call, interaction, perception fact, memory, transaction request, and operator intervention IDs |
| `source_metadata` | Optional producer/provider metadata |
| `payload` | Complete forward-compatible JSON object |

Simulation-owned IDs, record order, ticks, simulation time, and serialized
authoritative state are canonical. Wall-clock values, provider request IDs,
latency, and provider-assigned metadata are explicit nondeterministic fields and
must not be used for canonical run comparison.

The data dictionary is generated from the live record contract and SQLite
schema:

```http
GET /simulation/runs/{run_id}/data/schema
```

It reports envelope fields, visibility/category/phase values, observed record
schemas, normalized table fields, and derived feature schema versions.

## Capture phases

State is captured at these read-only runner boundaries:

| Phase | Timing |
| --- | --- |
| `run_initial` | Initial materialized state before the first tick |
| `tick_pre_systems` | After the fixed clock advances and before ordered systems |
| `tick_post_systems` | Immediately after ordered physical/perception/goal systems, before slow cognition is drained and before the global-barrier `simulation.tick` event |
| `tick_post_cognition` | After `simulation.tick` and the tick's cognition drain/commit boundary; global-barrier work has settled, while background requests may remain pending |
| `run_final` | Final state during collector finalization |
| `unspecified` | Records not tied to a phase hook |

Phase hooks observe state only. They do not change ordered system semantics.
The same tick can therefore have pre-system, post-system, and post-cognition
samples with different state.

## Visibility and private research handling

- `PUBLIC` is safe for ordinary disclosure.
- `OPERATOR` is omniscient operator/research data that is not character
  knowledge.
- `PRIVATE_RESEARCH` may contain prompts, rendered messages, model text/tool
  calls, profile or synthesized-situation context, retrieved memories and
  information, embeddings, reasons, or detailed authoritative snapshots.

Private research traces are not domain events. They never enter character
perception, episodic-memory routing, or normal realtime telemetry. Telemetry is
an independent operator projection and is never character-controller context.

REST queries, normalized-table queries, the explorer, filtered NDJSON, and
analysis bundles exclude `PRIVATE_RESEARCH` by default. To request it, set
`include_private=true`. Selecting `visibility=PRIVATE_RESEARCH` without that
opt-in is rejected.

The server-rendered Data Management page at `/ui/data/` uses only the configured
shared API/UI SQLite database. It catalogs historical runs after restart,
uses dataset-store ownership leases to distinguish live foreign processes from
abandoned owners, reconciles only closed, missing, or expired prior leases as
incomplete `interrupted` captures, and supports server-side cross-page
selection. Aggregate summaries include private-derived statistics by default
but never render raw private payloads; operators can exclude those derived rows
with the labeled privacy control. Numeric aggregates distinguish pooled
observation weighting from equally weighted per-run macro values, and mixed
selections retain explicit compatibility groups and warnings.

Permanent deletion is available only after an exact-selection preview and
confirmation token, required checkbox, and typed phrase. Current running,
paused, cognition-settling, or otherwise not-fully-finalized managed runs are
rejected, and mixed bulk deletion is atomic. Successful deletion clears
operator-session references. SQLite can reuse freed pages, but the application
does not automatically run `VACUUM`.

The compatibility full-run JSONL endpoint is intentionally complete and is not
a privacy-filtered export. Treat it, databases, and private-enabled bundles as
restricted research artifacts:

```http
GET /simulation/runs/{run_id}/export
```

Never publish these artifacts without reviewing their contents and applying
the research project's consent, access-control, retention, and redaction rules.

## Identity, causality, and lineage

Stable run-local identities connect the complete lifecycle:

```text
goal
  -> plan + revision
  -> decision
  -> model request/turn rounds
  -> tool call
  -> accepted intent
  -> action instance
  -> navigation/travel/affordance/speech/transaction execution
  -> authoritative terminal event
  -> state delta and transition sample
  -> decision/action/goal/interaction episode
```

An `ActionInstance` is distinct from its immutable action specification. It
records origin (`scenario`, `planner`, `controller`, `system1`, or `operator`),
creation tick/time, plan and revision, declared or contextual goal links,
decision and tool-call IDs, and a root correlation ID. Goal links label
controller-declared intent separately from contextual association.

`causation_id` links a record/event to its immediate cause.
`correlation_id` groups a workflow. The typed join columns support direct
queries without parsing payload text. `record_relations` retains additional
ordered typed edges.

## Structured goals and criteria

`planner.goals` accepts strict structured goals:

```json
{
  "id": "reach-lounge",
  "description": "Reach the lounge before the work session ends",
  "priority": 8,
  "tags": ["rest"],
  "deadline_time": 300,
  "completion_policy": "all",
  "criteria": [
    {
      "type": "location_match",
      "location_id": "lounge",
      "location_kind": "zone"
    }
  ]
}
```

Goal IDs must be unique per character. Priority is `0` through `100`.
Activation/deadline values are simulation times. `completion_policy` is `all`
or `any`. Each criterion has an optional `effect` of `success` or `failure`.
The closed criterion vocabulary is:

| Criterion | Fields and meaning |
| --- | --- |
| `event_match` | `event_type` and optional recursive `payload_subset` |
| `state_comparison` | Allowed `homeostasis`, `activity`, `controller`, or `planner` field; comparator `eq`, `ne`, `lt`, `lte`, `gt`, or `gte`; typed value |
| `location_match` | `location_id` and `location_kind` of `any`, `zone`, or `place` |
| `possession_threshold` | `item_id`, non-negative `quantity`, and supported comparator |
| `action_outcome` | Closed action name, `completed` or `failed`, and optional target |
| `interaction_count` | `speech`, `dialogue`, or `transaction`, positive minimum count, and optional target |
| `simulation_time` | Ordered comparison against a non-negative simulation time |

The goal evaluator observes authoritative state/events after physical and
perception systems. It updates only goal runtime state and emits
`goal.activated`, `goal.progressed`, `goal.succeeded`, `goal.failed`,
`goal.expired`, or `goal.retired`.

Legacy `daily_goals` and `current_priorities` remain valid. They receive stable
generated IDs and are exposed to planning/controller context, but because they
have no measurable criteria their terminal result remains `unknown`; the
system does not infer success from prose.

## Lifecycle semantics

### Decisions, model calls, and tools

Every cognition eligibility evaluation is research data, including ineligible
reasons. A decision capture can include the complete structured observation,
choice set, goals, possessions, perceived context, retrieval capsules, and
available tools.

Each model round has a `model_request` and normalized `model_turn` or
`model_error`. Read-tool rounds are recorded as
`tool.read_requested`/`tool.read_completed`. Provider completion does not mean a
physical action succeeded.

The state-changing lifecycle is:

```text
tool.proposed
  -> schema/offering/reference/current-state validation
  -> tool.accepted
  -> tool.committed
  -> action.queued
  -> action.started / action progress
  -> action.completed | action.failed | action.cancelled | action.interrupted
```

Rejection, stale state, timeout, cancellation, or `skip` closes the decision
without inventing an action outcome. A committed tool means the application
accepted a proposal; only authoritative terminal action events define the
world result.

### Interactions

Interaction records and episodes cover:

- direct speech and delivered/failed utterances;
- generated dialogue;
- automated and staffed transactions;
- co-presence and observer/subject visibility intervals;
- shared-resource contention.

They retain ordered participant roles, constituent event IDs, location/context,
content visibility, start/end simulation time, and terminal status. Interaction
data does not make private content perceptible.

### Perception, memory, and information

Perceptible facts and observer-specific deliveries are separate. Captures retain
modality, disclosure, observer/subject identity, delivery/drop context, and
fact lineage. Memory records connect requests, source perceptible facts/events,
generated episodes, embeddings, retrieval candidates, selected capsules, and
later request use. Information retrieval retains document revision and source
provenance. Privacy rules still determine what a character receives.

## SQLite normalized and derived tables

The immutable `records` table is the source of truth. Query-oriented tables
retain filterable scalar columns and complete JSON:

- relations and state: `record_relations`, `state_samples`, `state_deltas`;
- goals/plans/actions: `goals`, `goal_transitions`, `plans`,
  `goal_action_links`, `action_instances`, `action_transitions`;
- cognition/tools: `decisions`, `decision_options`, `model_requests`,
  `model_turns`, `tool_executions`;
- interactions: `interactions`, `interaction_participants`,
  `interaction_events`;
- perception/memory/information: `perception_facts`,
  `perception_deliveries`, `memory_operations`, `memory_relations`,
  `information_retrievals`;
- opportunities and derived features: `opportunity_samples`,
  `transition_samples`, `action_episodes`, `decision_episodes`,
  `goal_episodes`, `interaction_episodes`, `population_samples`,
  `resource_samples`, `resource_flows`.

Existing episodic-memory and information-document persistence remains separate
from the run's analytical table set.

## Derived feature definitions

Derived feature schemas are independently versioned, currently at version `1`.

- **Transition sample:** a pair of consecutive state samples with
  `state_before`, action context, exogenous environment context, `state_after`,
  elapsed simulation time, outcome label, and state delta.
- **Opportunity sample:** an omniscient opportunity/choice set with options,
  selection flags, and a `choice_status`; unselected opportunities provide
  non-choice denominators. Character-perspective choices remain distinct from
  omniscient availability.
- **Action episode:** one action instance from creation to authoritative
  completion, failure, cancellation, or interruption, including elapsed
  simulation time and source events.
- **Decision episode:** one cognition opportunity through rejection, skip,
  failure/cancellation, or the terminal result of its linked action.
- **Goal episode:** one structured or legacy goal with progress/evidence and a
  terminal status; unresolved legacy prose ends `unknown`.
- **Interaction episode:** participants and constituent events from start to
  delivered/completed/failed/cancelled/timed-out/ended result.
- **Population sample:** aggregate materialized-entity state at a runner phase.
- **Resource sample:** station or transaction-point capacity, occupancy, queue
  length, and utilization at a phase.
- **Resource flow:** authoritative resource movement, including transaction
  transfers, with involved entities/resources and amounts.

These records describe observed detailed execution. They are not themselves
estimated priors or approximate transition models.

## Query API

All endpoints are below `/simulation/runs/{run_id}`. `/data/...` aliases are
also available for schema, records, and analytical routes.

| Endpoint | Result |
| --- | --- |
| `/data` | Summary, counts, outcomes, and capture completeness |
| `/data/schema` | Data dictionary and observed schemas |
| `/data/records` | Raw records |
| `/data/goals`, `/data/decisions`, `/data/actions`, `/data/interactions` | Normalized lifecycles |
| `/data/state?kind=sample|delta` | State samples or deltas |
| `/data/transitions?kind=state|goal|action` | Derived state or lifecycle transitions |
| `/data/aggregates?family=population|resource_samples|resource_flows` | Aggregate/resource features |
| `/data/episodes/{actions|decisions|goals|interactions}` | Derived terminal episodes |
| `/data/model-requests`, `/data/tool-executions` | Model/tool projections |
| `/data/perception?kind=facts|deliveries` | Perception projections |
| `/data/memory?kind=operations|retrievals` | Memory or information retrieval |
| `/data/opportunities` | Opportunity/non-choice samples |

Common filters include record/category/schema, primary and related entity,
minimum/maximum tick or simulation time, visibility, status/outcome, every
typed lineage ID, cursor, and `limit` (`1` through `1000`). Raw-record cursors
are integer sequences. Analytical-table cursors are stable opaque strings.

Examples:

```bash
curl "http://127.0.0.1:8000/simulation/runs/RUN/data/actions?entity_id=agent-001&limit=50"

curl "http://127.0.0.1:8000/simulation/runs/RUN/data/records?category=MODEL&include_private=true&limit=100"

curl "http://127.0.0.1:8000/simulation/runs/RUN/data/episodes/decisions?status=completed"
```

Persisted event history remains queryable after a managed run stops; research
inspection is not limited to the bounded in-memory telemetry/event buffers.

## Server-rendered dataset explorer

Open:

```text
http://127.0.0.1:8000/ui/datasets/{run_id}/
```

The explorer provides run/capture summary, raw records, goal/decision/action/
interaction timelines, state transitions, population/resource views, schema
inspection, lineage/time/entity/status filters, stable pagination, and filtered
downloads.

The **Include private research data** checkbox is an explicit opt-in and
displays a warning. Filters and downloads carry the opt-in only while selected.
The page is server-rendered; ordinary GET forms and links are the
no-JavaScript fallback. Progressive enhancement replaces the named query region
without creating client-side dataset state.

## Exports

### Complete compatibility JSONL

```http
GET /simulation/runs/{run_id}/export
```

This preserves the existing format: a `stage0.run.manifest` line followed by
all ordered raw records. It is complete, not privacy-filtered.

### Filtered raw NDJSON

```http
GET /simulation/runs/{run_id}/exports/records?entity_id=agent-001&minimum_tick=10
```

The response contains matching raw records in sequence order and excludes
private research by default. It is bounded to 1000 records per export request.

### Filtered analysis bundle

```http
GET /simulation/runs/{run_id}/exports/bundle?entity_id=agent-001
```

The ZIP contains, in stable order:

1. `manifest.json` with run summary, applied filters, privacy flag, file list,
   and ordering contract;
2. `schema.json` with the data dictionary;
3. `records.ndjson`;
4. one CSV under `tables/` for every normalized and derived analysis table.

CSV files include the table fields plus linked raw-record sequence,
visibility, tick, and simulation time. JSON-valued columns remain canonical
JSON strings.

## Projection rebuild

`SQLiteDatasetStore.rebuild_run_projections(run_id)` transactionally rebuilds
the currently rebuildable analysis projections from ordered immutable raw
records. It covers interaction/perception projections and the derived
transition, opportunity, population, resource, action, decision, goal,
interaction, memory, and retrieval feature tables.

The rebuild runs under a SQLite savepoint. On failure it rolls back the
projection changes and leaves both the raw log and the previous projections
intact. Repeating a successful rebuild is deterministic. Core lifecycle tables
that are written directly during capture are not yet all reconstructed by this
method; use the raw log as the durable contract.

## Capture completeness and failure semantics

The run summary reports:

- final status, tick, and simulation time;
- counts by record type, category, visibility, schema, entity, lifecycle
  status, terminal outcome, and feature family;
- whether `run_final` was recorded;
- whether the coverage manifest was recorded;
- whether record sequences are contiguous;
- whether capture failed.

`capture_complete` is true only for a `completed` or `stopped` run with a
`run_final` record. Research sink/subscriber failures raise
`ResearchWriteError` and are retained in recorder failure diagnostics.
Persistence/projection failure while draining a research trace also marks the
run `capture_failed`; failures are never silently dropped or represented as
success. The coverage manifest fails explicitly when a new authoritative
component/resource has neither a projector nor an intentional exclusion.

The collector closes open activity, interaction, and goal episodes during
finalization. A stopped run can therefore be complete research capture while
still representing an intentionally early physical run.

## Current limitations

- Exhaustive phase snapshots, pairwise exposure, prompts, embeddings, and
  derived features can produce large SQLite files.
- There is no retention policy, sampling, compression policy, deduplication,
  Parquet/columnar export, or distributed storage backend.
- Provider text and timing are nondeterministic. Recording/replay is required
  to reproduce accepted live-provider choices.
- Private-research classification is an access boundary in the query/export
  interfaces, not a substitute for deployment authentication, authorization,
  consent, encryption, or institutional data governance.
- Projection rebuild does not yet reconstruct every normalized lifecycle table.
- Statistical prior estimation, calibration, confidence intervals, approximate
  populations, and materialisation models are deferred.
- SQLite/JSONL datasets are research records, not resumable checkpoints.
  Resume would require versioned restoration of the ECS, clock, RNG, queues,
  pending provider work, and other live resources.
