# Global Cognition Barrier and Skip Tool Plan

> Legacy implementation record. The global barrier and skip tool are part of
> the current runtime described in `docs/CONCEPT_GUIDE.md`.

**Status:** Implemented  
**Date:** 2026-08-30  
**Scope:** Tool-controller scheduling, runner tick boundaries, provider waiting,
timeouts and cancellation, no-action decisions, telemetry, browser controls,
events, datasets, scenarios, and tests  
**Estimated implementation:** 3-5 focused engineering days  
**External services required:** None; scripted, replay, and fake providers are
sufficient for implementation and automated verification

**Implementation record (2026-08-30):** The default scenario mode is now
`global_barrier`, with explicit `background` compatibility. The runner has an
awaitable tick boundary, controller requests are dispatched concurrently and
committed only after the batch settles, Pause and Stop remain responsive,
telemetry exposes cognition phase and wait duration, and vital mutation is
rejected during settlement. The strict `skip` tool creates no physical plan and
uses a simulated-time reconsideration delay. OpenAI-compatible tool choice now
defaults to `required`, while invalid call counts include structured
diagnostics.

## 1. Requested behavior

Change tool-controller execution so that once one or more characters request an
LLM decision, the entire simulation stops advancing until every decision in
that cognition batch has:

- completed successfully;
- failed explicitly;
- been cancelled by Stop; or
- reached its configured provider timeout.

No simulation clock, homeostasis, movement, plan, travel, affordance, speech, or
System 1 system may advance while the batch is pending.

Also add a `skip` tool for a character that has no useful intentional action to
take. `skip` must not create a physical action, but it must defer the next idle
decision so the character does not immediately call the LLM again on the next
tick.

## 2. Current-state analysis

## 2.1 The requested global freeze reverses a core current invariant

The current architecture deliberately separates wall-clock model latency from
the micro-clock:

- `CognitionScheduler` enqueues a request during ordered system execution.
- `AgentWorkCoordinator` starts real controller work in a thread pool.
- `SimulationRunner` continues advancing ticks.
- A later post-tick `drain()` applies completed decisions in stable order.
- decision IDs and state revisions reject results that became stale while the
  world continued.

This is documented in `docs\CONCEPT_GUIDE.md` as:

- model latency must not become part of physical rules;
- real providers may complete while the simulation continues;
- the global micro-clock must not pause for a character request.

The requested behavior is therefore not a small scheduling option. It is an
intentional replacement of the current latency-independent execution model.
The concept guide, real-LLM plan, tests, telemetry language, and dataset
interpretation must all be updated together.

The authority boundary should remain unchanged: the model still proposes one
typed tool and deterministic systems remain the only authority over physical
outcomes.

## 2.2 Exact source of the tool-call-count error

`ToolCallingCharacterController.decide()` currently requires:

```python
len(turn.tool_calls) == 1
```

Zero or multiple calls produce:

```text
exactly_one_tool_required
```

The OpenAI-compatible adapter currently defaults to:

```text
STAGE0_LLM_TOOL_CHOICE=auto
```

With `auto`, a provider may return ordinary text and zero tool calls even
though the prompt asks for exactly one tool. A provider may also return
multiple calls. The controller correctly rejects both because choosing one
silently would be nondeterministic and could conceal an invalid response.

There is already a `wait` tool:

```json
{
  "name": "wait",
  "arguments": {
    "duration_seconds": 30
  }
}
```

Therefore the observed error is not strictly caused by the absence of a
no-action tool. Likely contributing factors are:

1. `tool_choice="auto"` permits a text-only response.
2. Some models distinguish "perform an idle action for a duration" from
   "nothing needs deciding now."
3. The prompt says to call `wait`, but weaker tool-calling models may still
   answer in prose.
4. Current rejection telemetry records the generic decision error but does not
   prominently expose actual tool-call count, text presence, and finish reason.

Adding `skip` will improve the model's semantic choices, but the plan must also
require tool use and improve diagnostics.

## 2.3 `skip` cannot be an immediate zero-duration no-op

The scheduler requests cognition whenever a controlled character:

- is enabled;
- has no pending request;
- has no current or queued plan;
- is not under System 1 control;
- is not executing an affordance; and
- has no pending speech.

If `skip` merely clears `request_pending`, the same character becomes eligible
again on the next tick. Under the requested global barrier this would freeze
the simulation for another LLM call every simulated second.

`skip` therefore needs a deterministic simulated-time cooldown or a later
event trigger. The initial implementation should use a cooldown because the
current scheduler does not yet implement a complete external-event
reconsideration system.

## 2.4 Current runner APIs are not ready to await cognition

`SimulationRunner._advance_one_tick()` is synchronous. It:

1. advances the clock;
2. runs ordered systems;
3. emits `simulation.tick`;
4. drains legacy macro work;
5. polls tool-controller work;
6. invokes tick-completed handlers.

`run_realtime()` is asynchronous, but it calls the synchronous tick method.
Blocking that method on a network future would also block FastAPI's event loop,
preventing responsive Stop/Pause requests and telemetry delivery.

The tick boundary must become awaitable rather than calling
`Future.result()` directly on the API event-loop thread.

## 2.5 Existing tests explicitly encode the opposite behavior

Current tests verify that:

- delayed provider responses can become stale after System 1 changes state;
- provider timeout does not stop simulation ticks;
- the clock reaches tick 2 while a delayed request times out;
- the simulation can continue while requests are pending.

These tests must be replaced with global-barrier assertions. Stale-result
validation should remain as defense in depth for Stop, operator mutation,
future modes, and providers whose underlying HTTP operation cannot be
cancelled.

## 2.6 Legacy macro provider work already blocks, but is not reported uniformly

`MacroWorkCoordinator` executes planner, dialogue, and memory provider work
synchronously at the post-tick boundary. That already stops simulation
advancement, although it can block the server event loop and does not expose a
common "waiting for cognition" phase.

The initial change should make tool-controller calls use the global barrier and
instrument all provider work with the same runner phase. A later refactor may
move legacy planner/dialogue providers onto the same awaitable provider
interface, but the new semantics must be consistent: no next tick begins while
any post-tick provider work remains.

## 3. Proposed semantics

## 3.1 Make the global cognition barrier the default

Add an explicit scenario setting:

```json
{
  "cognition": {
    "execution_mode": "global_barrier"
  }
}
```

Supported values:

| Value | Behavior |
|---|---|
| `global_barrier` | Default. Freeze all simulation advancement until the batch settles. |
| `background` | Compatibility/research mode retaining the current asynchronous behavior. |

The default changes to `global_barrier`, satisfying the requested system
behavior. Keeping `background` behind an explicit setting preserves the
ability to compare both execution models and replay older experiments.

The selected mode must be recorded in the run manifest and cognition events.

## 3.2 Define one cognition batch per tick boundary

At tick `N`:

1. Advance the clock to `N`.
2. Run all deterministic ordered systems through `CognitionScheduler`.
3. Collect every controller request generated by that system pass.
4. Emit a global waiting event if at least one request exists.
5. Start requests in stable `(requested_tick, agent_id, decision_id)` order,
   respecting `max_concurrency`.
6. Freeze the simulation state.
7. Wait until all requests complete, fail, cancel, or time out.
8. Do not commit early results while any request in the batch remains pending.
9. Sort all settled results by
   `(requested_tick, agent_id, decision_id)`.
10. Validate and commit each result.
11. Drain other post-tick provider work.
12. Emit `simulation.tick`.
13. Invoke tick-completed handlers and persist the completed tick.
14. Schedule the next wall-clock-paced tick.

The batch is simultaneous from the simulation's perspective. A fast character
must not act while another character's request is still pending.

## 3.3 Freeze authoritative state, not operator observability

While waiting:

- simulation time and tick remain constant;
- ordered systems do not run;
- no physical component is mutated;
- telemetry snapshots may continue at the existing wall-clock frequency;
- event history and run inspection remain available;
- the UI shows that the run is waiting for cognition;
- Pause and Stop remain available;
- speed changes affect subsequent pacing only;
- controlled vital mutation is rejected with `409` until the barrier settles.

The FastAPI event loop must remain responsive. "Freeze the simulation" must not
mean "freeze the server."

## 3.4 Keep bounded timeouts

The simulation waits for completion up to
`cognition.decision_timeout_seconds`. A timeout:

1. emits `cognition.failed` with `provider_timeout`;
2. clears the character's pending request;
3. settles that member of the batch;
4. releases the global barrier after all other batch members settle;
5. allows later cognition according to retry/cooldown policy.

Removing timeouts would allow a failed provider to deadlock a run
indefinitely. The requested wait behavior should therefore mean "wait until a
terminal result," where timeout is a terminal result.

## 3.5 Pause and Stop behavior

**Pause during a cognition barrier**

- the in-progress tick remains frozen;
- completed model results are validated and committed;
- the tick boundary finishes;
- no next tick starts until Resume.

**Stop during a cognition barrier**

- mark the runner as stopping;
- cancel queued and awaitable controller tasks;
- discard any result that arrives after cancellation;
- emit `cognition.cancelled`;
- do not commit pending tools;
- finish dataset finalization and publish `simulation.stopped`;
- do not wait for an uncancellable provider thread beyond a short shutdown
  grace period.

Underlying synchronous HTTP work may continue in a worker thread after logical
cancellation. Correctness must still rely on decision IDs and cancellation
state, not successful network interruption.

## 4. `skip` tool design

## 4.1 Tool contract

Add:

```json
{
  "name": "skip",
  "description": "Take no intentional action now and reconsider later.",
  "arguments": {
    "reconsider_after_seconds": 30,
    "reason": "No useful action is currently needed"
  }
}
```

Pydantic schema:

```text
reconsider_after_seconds: float = 30
minimum: 5
maximum: 3600
reason: optional string, maximum 300 characters
extra fields: forbidden
```

`skip` differs from `wait`:

| Tool | Meaning | Runtime effect |
|---|---|---|
| `wait` | Intentionally remain idle for a bounded in-world duration | Creates an `IDLE` plan action |
| `skip` | No useful decision is needed now | Creates no plan; defers cognition eligibility |

Do not automatically convert a zero-tool response into `skip`. Invalid model
output must remain an explicit rejection.

## 4.2 Runtime state

Add to `ControllerComponent`:

```text
next_decision_time: float = 0.0
```

The scheduler must require:

```text
context.clock.simulation_time >= next_decision_time
```

Committing `skip`:

1. creates a typed `SkipIntent`;
2. performs no world or plan mutation;
3. clears `request_pending` and `current_decision_id`;
4. increments `state_revision`;
5. sets `next_decision_time` to current simulation time plus the requested
   delay;
6. records `last_outcome`;
7. emits `tool.accepted`, `tool.committed`, and `cognition.skipped`.

External reconsideration triggers may later clear or shorten the cooldown, but
that should be a separate feature with explicit deterministic rules.

## 4.3 Closed-vocabulary updates

Add `skip` consistently to:

- `IntentKind`;
- `SkipIntent`;
- `ToolRegistry`;
- tool descriptions and JSON schema;
- scenario cognition allowlist validation;
- per-character controller allowlist validation;
- `ControllerComponent` defaults;
- representative tool-agent scenarios;
- fake LLM tool selection and argument generation;
- prompts and documentation;
- telemetry/event summaries;
- datasets and tests.

No surface may advertise `skip` before validation and commit support exist.

## 5. Tool-call cardinality improvements

## 5.1 Require a tool for tool-controller requests

Change the OpenAI-compatible tool-controller default from:

```text
tool_choice = "auto"
```

to:

```text
tool_choice = "required"
```

Validation rules:

- tool-agent mode rejects `tool_choice="none"`;
- `required` remains configurable because some local providers may not support
  it correctly;
- providers using `auto` remain subject to strict cardinality rejection;
- multiple calls remain invalid even when all calls are known tools.

The adapter should continue normalizing provider responses rather than
silently manufacturing a call.

## 5.2 Improve rejection diagnostics

When call count is not one, record:

```json
{
  "reason": "exactly_one_tool_required",
  "expected_tool_call_count": 1,
  "actual_tool_call_count": 0,
  "finish_reason": "stop",
  "response_text_present": true,
  "offered_tools": ["go_to", "perform", "say", "wait", "skip"]
}
```

Keep response text out of concise telemetry if it may contain private model
content, but retain it in the existing sanitized recording adapter when
recording is explicitly enabled.

This distinguishes:

- no call because the provider answered in text;
- multiple calls;
- malformed call parsing;
- an unknown or disallowed tool;
- invalid tool arguments.

## 5.3 Prompt update

Increment the prompt version and state:

- exactly one tool is mandatory;
- choose `skip` when no decision is useful;
- choose `wait` only when intentional in-world idleness for a duration is the
  desired action;
- never answer only in prose.

The code remains authoritative; prompt wording is not treated as validation.

## 6. Architecture changes

## 6.1 Awaitable runner boundary

Introduce canonical async methods:

```python
async def advance_one_tick()
async def run_for_async(ticks)
async def single_step_async()
```

Keep synchronous CLI/test wrappers:

```python
def run_for(ticks)
def single_step()
```

The synchronous wrappers call the async implementation only when no event loop
is already running. Async application code must use the async methods directly.

Update:

- `SimulationRunner.run_realtime()` to await the tick boundary;
- `SimulationManager.step()` to become async;
- the step API endpoint to await the manager;
- CLI execution to use one event loop for the complete run;
- tests that currently call synchronous runner methods from async contexts.

Do not block FastAPI's event-loop thread on `Future.result()`.

## 6.2 Coordinator batch API

Add a batch-oriented coordinator method:

```python
async def drain_and_wait(context) -> CognitionBatchResult
```

Responsibilities:

- snapshot and clear the queued requests;
- start all requests with bounded concurrency;
- await terminal outcomes with per-request deadlines;
- handle Stop cancellation;
- collect usage once per completed response;
- apply nothing until the entire batch settles;
- sort and apply outcomes deterministically;
- return counts, IDs, elapsed wall time, and terminal states.

Retain the current polling `drain()` only for explicit
`execution_mode="background"`.

Avoid creating a separate event loop per request. The coordinator should own
awaitable tasks on the runner's event loop; blocking provider SDKs remain
isolated inside adapters with `asyncio.to_thread`.

## 6.3 Runner cognition phase

Add runner state separate from lifecycle status:

```text
cognition_phase:
  idle
  waiting
  applying

pending_cognition_count
pending_decision_ids
cognition_wait_started_at
```

Do not add `waiting` as a replacement for `RunnerStatus.RUNNING`. Lifecycle and
execution phase are different:

- lifecycle answers whether a run is running, paused, or stopped;
- cognition phase explains why a running tick is not advancing.

Guard phase transitions with `try/finally` so provider errors cannot leave a
run permanently marked as waiting.

## 6.4 Tick event and dataset boundary

Move `simulation.tick` emission and tick-completed handlers after the cognition
barrier. This makes a tick record mean that:

- deterministic systems ran;
- all provider work scheduled by that pass settled;
- accepted tool decisions were committed;
- the tick is fully complete.

All events generated while waiting retain the same simulation tick and
simulation time. Wall-clock latency remains metadata, not simulated duration.

This changes event ordering and must increment any affected dataset or
telemetry contract version if consumers assume `simulation.tick` precedes
cognition completion.

## 7. Telemetry and browser behavior

Extend run status and runtime snapshots with:

```json
{
  "cognition": {
    "phase": "waiting",
    "pending_count": 2,
    "pending_decision_ids": [
      "decision:agent-001:00000001",
      "decision:agent-002:00000001"
    ],
    "elapsed_wall_seconds": 4.2
  }
}
```

Add events:

```text
simulation.cognition_wait_started
simulation.cognition_wait_finished
cognition.skipped
```

The start event includes batch ID, decision IDs, count, and execution mode.
The finish event includes completed, failed, cancelled, and timed-out counts
plus wall-clock duration.

Browser changes:

- show `Waiting for 2 character decisions...`;
- keep the simulation tick visibly stable;
- distinguish operator Pause from cognition waiting;
- keep Stop enabled;
- keep Pause enabled;
- disable vital mutation while waiting;
- allow log inspection and event-history loading;
- show timeout/failure/call-count diagnostics in event details;
- do not label telemetry disconnection as simulation waiting.

Telemetry snapshots continue at approximately 10 Hz so the UI remains alive,
but repeated snapshots must not create canonical dataset records.

## 8. Determinism and correctness consequences

## 8.1 What remains deterministic

For scripted or replayed model responses:

- simulation tick order;
- request creation order;
- batch membership;
- completion commit order;
- tool validation;
- physical outcomes;
- events excluding wall-clock identity;
- datasets excluding latency metadata.

## 8.2 What changes

- A slow model now increases real run duration.
- A hung model freezes progression until timeout.
- System 1 cannot newly activate while waiting because homeostasis does not
  advance.
- A character may remain physically safe longer in simulation time than in wall
  time; this is intentional because physiology follows simulation time.
- Late-result staleness becomes uncommon in global-barrier mode.
- Multi-character throughput is bounded by the slowest request in each batch.
- Realtime speed no longer guarantees wall-clock pacing when cognition is slow.

The UI and documentation must explain that configured speed applies only while
the micro-clock is advancing.

## 8.3 Why requests should remain concurrent within a batch

Dispatch eligible requests concurrently up to `max_concurrency`, then wait for
the whole batch. Sequential calls would multiply wall time by character count
without improving simulation determinism because commit order is already
stable.

Recording/replay tests should not depend on live provider completion order.

## 9. Failure handling

| Failure | Required behavior |
|---|---|
| Provider timeout | Mark request failed, settle batch member, release after all settle |
| Provider transport error | Emit explicit failure; no tool commit |
| Zero tool calls | Reject with actual call count and finish reason |
| Multiple tool calls | Reject all; never choose the first |
| Unknown/disallowed tool | Reject explicitly |
| Invalid `skip` delay | Reject arguments |
| Stop during wait | Cancel logically, discard late results, finalize run |
| Pause during wait | Complete current boundary, remain paused afterward |
| Vital mutation during wait | Return conflict; do not mutate captured state |
| Coordinator exception | Clear waiting phase in `finally`, emit run-visible failure |
| Budget exhausted | Do not open a barrier; disable cognition as currently |

Do not retry a malformed model decision automatically in the initial
implementation. Automatic repair calls would create another cost and latency
policy that requires separate limits and telemetry.

## 10. Implementation phases

## Phase 1: Lock semantics and configuration

1. Add `cognition.execution_mode`.
2. Make `global_barrier` the default.
3. Record the mode in run configuration and datasets.
4. Update concept documentation to describe the intentional semantic change.
5. Add runner cognition-phase state.

**Exit gate:** A run can report lifecycle and cognition phase independently.

## Phase 2: Add `skip` completely

1. Add `SkipArguments`, `SkipIntent`, and `IntentKind.SKIP`.
2. Add `next_decision_time` to `ControllerComponent`.
3. Update scheduler eligibility.
4. Add deterministic commit behavior and events.
5. Update all closed allowlists, prompts, fake providers, examples, and tests.
6. Distinguish `skip` from `wait` in documentation.

**Exit gate:** A skipped character creates no plan and is not queried again
until its simulated cooldown expires.

## Phase 3: Fix tool cardinality behavior and diagnostics

1. Default tool-agent provider requests to `tool_choice="required"`.
2. Reject incompatible `none` configuration.
3. Increment prompt version.
4. Include actual tool-call count and finish metadata in rejection events.
5. Add zero-call and multiple-call adapter/controller tests.

**Exit gate:** A compliant provider always has an explicit no-action option;
noncompliant responses remain visible and machine-classifiable.

## Phase 4: Add the awaitable global barrier

1. Add async runner tick methods.
2. Add `AgentWorkCoordinator.drain_and_wait()`.
3. Dispatch each batch concurrently with bounded concurrency.
4. Await all terminal outcomes.
5. Apply settled results only after the batch completes.
6. Move tick completion emission after the barrier.
7. Keep background mode isolated behind the explicit setting.

**Exit gate:** A delayed provider leaves tick and simulation time unchanged
until completion or timeout.

## Phase 5: Lifecycle, cancellation, and API integration

1. Make manager Step await the full boundary.
2. Keep Pause and Stop responsive during a wait.
3. Add logical task cancellation and late-result discard on Stop.
4. Reject vital mutation while waiting.
5. Ensure shutdown does not hang on an uncancellable provider thread.

**Exit gate:** Stop terminates a waiting run promptly and no cancelled tool can
commit later.

## Phase 6: Telemetry, browser, and datasets

1. Project cognition phase and pending batch details.
2. Add wait-start/wait-finish events.
3. Update browser labels and control availability.
4. Record execution mode and batch outcomes in datasets.
5. Update event ordering/version documentation where required.

**Exit gate:** Operators can distinguish running, paused, stopped, waiting,
timed out, and failed states without reading raw logs.

## Phase 7: Compatibility and full verification

1. Update current scenarios and defaults.
2. Rewrite tests that assert background progression.
3. Retain explicit background-mode tests.
4. Exercise scripted, replay, fake HTTP, delayed, timeout, and cancellation
   providers.
5. Run standard test, Ruff, mypy, JavaScript syntax, and wheel checks.

**Exit gate:** Both modes are explicit, global barrier is the default, and
installed CLI/API/browser behavior agrees.

## 11. Test plan

## 11.1 `skip` tests

- tool appears in global and per-character allowlists;
- strict empty/default arguments are accepted;
- invalid delay and extra fields are rejected;
- commit creates no plan or speech component;
- controller pending state is cleared;
- state revision increments;
- `next_decision_time` is deterministic;
- scheduler does not request before the cooldown;
- scheduler requests at or after the cooldown;
- `cognition.skipped` and `tool.committed` contain structured data;
- System 1 still prevents `skip` from being accepted.

## 11.2 Cardinality tests

- exactly one `skip` call succeeds;
- text plus zero calls is rejected;
- two valid calls are both rejected;
- rejection records actual call count and finish reason;
- default OpenAI-compatible payload uses `required`;
- explicit compatible `auto` remains supported;
- `none` is rejected for tool-agent scenarios.

## 11.3 Barrier tests

- one delayed request freezes the tick;
- multiple requests start concurrently;
- no result commits before the slowest batch member settles;
- results commit in stable agent/decision order;
- timeout releases the barrier without advancing during the wait;
- a provider error releases the barrier;
- no ordered system runs while waiting;
- tick-completed handlers run only after commit;
- `simulation.tick` follows cognition terminal events;
- synchronous scripted/replay clients use the same semantics.

## 11.4 Lifecycle tests

- Pause during wait leaves the run paused after boundary completion;
- Stop during wait cancels all pending decisions;
- late uncancellable results cannot commit after Stop;
- Step waits for cognition before returning;
- speed change affects only subsequent pacing;
- vital mutation returns conflict during a barrier;
- telemetry and event endpoints remain responsive.

## 11.5 Simulation behavior tests

- homeostasis does not decay during wall-clock LLM latency;
- movement and travel do not progress;
- System 1 state does not change until the next tick;
- accepted action begins through normal deterministic systems on the following
  system pass;
- skip leaves the character physically idle;
- replayed decisions produce canonical deterministic records.

## 11.6 Browser tests

- waiting phase is rendered separately from Pause;
- pending count is visible;
- tick display remains stable;
- Stop and Pause remain available;
- vital mutation is disabled;
- timeout and cardinality details are inspectable;
- reconnect reconstructs waiting phase from snapshot state.

## 12. Documentation changes

Update:

- `docs\CONCEPT_GUIDE.md`;
- `docs/legacy/plans/REAL_LLM_TOOL_AGENT_PLAN.md`;
- README execution and speed descriptions;
- scenario/profile guidance where tool allowlists appear;
- fake LLM instructions;
- API and telemetry schema descriptions.

Replace claims that wall-clock model latency never stalls the simulation with:

> In `global_barrier` mode, provider latency stalls wall-clock execution but
> does not advance simulation time or change deterministic physical rules.

Keep the distinction between simulated duration and wall-clock duration.

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Slow provider makes runs unusably slow | Concurrent batches, visible wait state, bounded timeout |
| Hung provider deadlocks the run | Preserve per-request timeout |
| Blocking call freezes FastAPI | Await futures; never call blocking `result()` on event-loop thread |
| Stop cannot cancel synchronous HTTP | Logical cancellation, late-result discard, bounded shutdown |
| Skip causes request storm | Deterministic simulated-time cooldown |
| Skip becomes a hidden success fallback | Never synthesize it from invalid output |
| Multiple calls are applied accidentally | Reject the complete response |
| Pause and waiting are conflated | Separate lifecycle status from cognition phase |
| Tick records appear before decisions settle | Move tick completion event after barrier |
| Multi-character decisions become order-dependent | Collect all results, then commit in stable order |
| Existing benchmarks change meaning | Record execution mode; retain explicit background mode |
| Model ignores tools under `auto` | Default to `required` and expose call-count diagnostics |
| System 1 cannot preempt during wall wait | Document that no simulation time passes; retain validation at commit |

## 14. Acceptance criteria

The work is complete when:

1. `global_barrier` is the default tool-controller execution mode.
2. Once cognition is requested, no next tick begins until the entire batch
   settles.
3. Simulation time does not advance during wall-clock provider latency.
4. FastAPI telemetry, Pause, and Stop remain responsive.
5. Stop prevents all pending or late tools from committing.
6. `skip` is a validated closed-vocabulary tool with no physical plan effect.
7. A skipped character is not immediately queried again.
8. Tool-agent requests default to required tool use.
9. Zero and multiple calls remain explicit failures with useful diagnostics.
10. `wait` remains available and semantically distinct from `skip`.
11. Tick events and dataset boundaries occur after cognition settlement.
12. Browser telemetry clearly shows the cognition waiting phase.
13. Background execution remains available only through explicit
    compatibility configuration.
14. System 1, perception privacy, tool validation, and simulation authority
    remain unchanged.
15. Standard automated and packaging checks pass.

## 15. Recommended review decisions

Confirm these choices before implementation:

1. Keep a bounded timeout rather than wait forever.
2. Make `global_barrier` the default while retaining explicit `background`
   mode.
3. Dispatch requests concurrently within a batch and commit only after all
   settle.
4. Use a default 30 simulated-second `skip` cooldown, configurable per call
   from 5 to 3600 seconds.
5. Keep `wait` as an embodied idle action and `skip` as cognition deferral.
6. Change the default OpenAI-compatible `tool_choice` to `required`.
7. Keep lifecycle status and cognition waiting phase as separate state.
8. Reject operator vital mutation during an active cognition barrier.
