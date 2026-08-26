# Project State Assessment

**Original assessment:** 2026-08-26  
**Completion update:** 2026-08-26  
**Compared against:** `IMPLEMENTATION_PLAN.md` and `starting_basic_PRD.md`

## Executive conclusion

The completion work recommended by the original assessment has been implemented.
The repository now satisfies the Stage 0 M5 baseline for deterministic
fake/scripted providers: physical systems are deterministic and provider-free,
macro cognition is isolated from the ordered system pass, dialogue and durable
episodic memory are integrated, datasets include initial/generated memories and
provider metadata, and the complete acceptance/stability paths are automated.

This assessment does **not** claim live-run checkpoint/resume or validation
against an external production LLM. API run/scenario objects remain
process-local, and stopped simulations remain non-resumable by design.

## Resolution of prior findings

| Prior finding | Resolution |
| --- | --- |
| Planner calls ran synchronously in the ordered micro-tick pass | **Resolved.** `MacroPlanningSystem` only enqueues work. `MacroWorkCoordinator` executes the provider at the runner's explicit post-tick boundary. |
| Embedding calls ran in `MemoryRecordingSystem.update()` | **Resolved.** The system emits `memory.requested` and queues an immutable work item. Embedding/indexing occurs outside `SystemExecutor.update()`. |
| Survival/physical correction could invoke cognition | **Resolved.** Survival ticks cancel queued planner/dialogue requests, defer queued memory embedding, and preserve System 1 priority. Automated stack-isolation and nine-step acceptance tests cover this boundary. |
| Dialogue and `SOCIALIZE` were not integrated | **Resolved.** Social actions require a valid agent target, queue dialogue generation, maintain conversation state, emit requested/generated/failed/cancelled events, reach telemetry/datasets, and generate episodic memories. |
| Episodic memory was process-only | **Resolved for memory durability.** A dedicated SQLite `episodic_memories` schema stores raw text, metadata, simulation time, importance, embedding, run, and agent. The active index can rehydrate from persisted rows. |
| Initial memories were missing from canonical export | **Resolved.** Initial scenario episodes are persisted and exported as `memory.initial` records; generated episodes remain linked to `memory.recorded` events. |
| Correction target selection ignored capacity | **Resolved.** Selection counts active users and deterministic reservations, then falls back by path cost and station ID. |
| Failed planner records lacked provider metadata | **Resolved and extended.** Planner/dialogue completed, failed, and cancelled records include provider plus latency/input/output token fields using null when failure metadata is unknown and zero for work cancelled before invocation. |
| Exact acceptance and long-run evidence were absent | **Resolved.** Tests cover the nine plan steps, zero provider calls during correction, two simulated hours, determinism, bounded meters, non-overlap, and valid System 1 states. |
| Browser fidelity verification was source-only | **Resolved within the no-browser-dependency constraint.** TestClient verifies authoritative ordered WebSocket snapshots, while static-asset contract tests verify that `world_snapshot` drives canvas rendering and exact homeostasis fields drive gauges. |
| Tracked generated `egg-info` and untidy `.gitignore` | **Resolved.** Generated metadata is removed, `*.egg-info/` is ignored, and `.gitignore` has a final newline. |
| Plan described TypeScript/Vite/Vitest rather than the implementation | **Resolved.** The plan and README now describe the FastAPI-served HTML/CSS/plain-JavaScript canvas UI and pytest/Ruff/mypy toolchain. |

## Implemented architecture

Each fixed tick has two explicit portions:

1. `SystemExecutor.update()` runs ordered homeostasis, plans, System 1,
   navigation, affordances, and request-enqueue systems. No provider is called.
2. After `simulation.tick`, `MacroWorkCoordinator.drain()` runs at the post-tick
   boundary when the tick is cognition-safe. Results are validated and applied
   deterministically in memory, dialogue, then planning order.

Provider protocols remain synchronous so headless fake/scripted adapters and the
existing API stay simple. Queue placement, rather than an event-loop dependency,
enforces stack isolation and deterministic application. A slow synchronous
provider can still lengthen the caller's post-tick operation; integrating a real
remote provider should add an asynchronous worker/timeout transport behind the
same queue without moving result application away from tick boundaries.

Episodic memory has two layers:

- `EpisodicMemoryStore`: active deterministic retrieval/index layer.
- `SQLiteDatasetStore.episodic_memories`: durable persistence boundary and
  source for index rehydration.

The canonical event/state dataset and memory table are durable, but neither is a
complete simulation checkpoint.

## Remaining boundaries and future work

1. **No stopped-run resume or API restart rehydration.** Implementing this would
   require versioned checkpoints for clock, ECS components, pending macro work,
   RNG state, and lifecycle semantics. Current documentation explicitly avoids
   that claim.
2. **No external real-LLM adapter is shipped.** Fake/scripted adapters verify the
   provider contract, failure metadata, isolation, and deterministic handoff.
3. **No executable browser engine test is included.** This is intentional under
   the dependency constraint. TestClient/WebSocket authority and static adapter
   wiring are tested, but pixel rendering is not.
4. **The fake embedding is deterministic, not semantically rich.** It validates
   persistence and ranking mechanics rather than production semantic quality.
5. **Post-tick synchronous adapters may consume wall time.** They cannot execute
   on the ordered micro-tick stack or during correction, but a future remote
   adapter should execute provider I/O on a worker and return results to the same
   deterministic boundary.

## Verification record

The completion change adds focused coverage for:

- exact nine-step survival acceptance;
- provider stack and correction isolation;
- capacity-aware next-nearest fallback;
- integrated dialogue, conversation, telemetry, memory, persistence, and export;
- planner/dialogue failure metadata;
- accelerated deterministic two-hour stability;
- authoritative WebSocket/browser adapter wiring.

Final completed-worktree verification:

- pytest: **81 passed** (one Starlette deprecation warning from the installed
  TestClient/httpx integration);
- Ruff: **all checks passed**;
- strict mypy: **no issues in 46 source files**.
