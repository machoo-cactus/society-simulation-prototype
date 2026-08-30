# Real LLM Tool-Agent Architecture Plan

**Status:** Initial vertical slice implemented  
**Scope:** Real-run cognition for Stage 0  
**Primary goal:** Let an LLM control a simulated person through typed tools without
letting model output directly mutate simulation state.

**Required prerequisite:** Deterministic observer-specific vision, hearing, and
knowledge must remain enabled before real character controllers. The current
runtime now provides this boundary separately from operator telemetry.

**Implementation note (2026-08-27):** Phases A-D now have an initial integrated
implementation: deterministic visual/auditory facts and knowledge, strict
`go_to`/`perform`/`say`/`wait` tools, speech delivery, an OpenAI-compatible
client, bounded asynchronous completion handling, and record/replay adapters.
The richer Phase E interaction features remain deferred.

**Current note (2026-08-30):** The controller now uses reusable structured
character profiles, a separate general controller prompt, and a dedicated
character-description message. `travel_to` is available for hierarchical city
scenarios. The standalone fake server and real providers share the
OpenAI-compatible `/v1/chat/completions` contract.

## 1. Executive proposal

Replace the current distinction between "planner output" and "dialogue output"
with one modular **character controller** interface. On each cognition
opportunity, the controller receives a bounded observation of one character and a
catalog of currently available tools. It may select a tool such as:

- `go_to`
- `perform`
- `say`
- `wait`

The model is not asked to roleplay an unconstrained character or narrate what has
already happened. It is asked to act as the character's executive controller:
inspect the provided state, choose the character's next intentional act, and use
a tool to express that choice. `say` is the only initial tool that emits literal
character speech.

Tool calls are proposals, not privileged commands. The simulation validates each
call against authoritative state and translates accepted calls into domain
intents or existing `PlanAction` values. Movement, activity, affordances,
homeostasis, collision handling, speech delivery, and System 1 preemption remain
deterministic domain behavior.

The first version should deliberately use a **one-decision/one-action horizon**:
one model request may commit at most one state-changing tool call. When that
action completes, fails, or is interrupted, the character becomes eligible for
another decision. This is simpler, easier to observe, and safer than an open-ended
agent loop. The contracts should still allow read-only tools and multi-turn tool
results later.

Controller input must come from self state plus perceived facts, not from the
omniscient telemetry snapshot. A deterministic perception renderer is sufficient
for the first version. An LLM narrator may later rephrase already-filtered facts,
but must never decide what was perceived or supply facts used for authorization.

## 2. Design goals

1. **Character control, not direct roleplay.** The model chooses what the
   simulated person does. It does not impersonate the simulation engine, invent
   outcomes, or converse with an end user.
2. **Tool-mediated agency.** All externally visible intentional behavior enters
   the simulation through registered, typed tools.
3. **Domain authority.** The model cannot directly set coordinates, meters,
   activity state, memories, or System 1 state.
4. **Strong isolation.** Provider SDKs, prompt formats, model quirks, retries, and
   credentials remain outside the domain and application policy.
5. **Provider swapability.** OpenAI-compatible, Anthropic-style, local, scripted,
   and replay providers can implement the same model-client contract.
6. **Deterministic application.** Provider results are accepted or rejected at an
   explicit simulation boundary in stable order.
7. **System 1 supremacy.** Survival behavior cancels or supersedes pending model
   work without asking the model for permission.
8. **Bounded cost and latency.** Requests have explicit timeouts, token limits,
   maximum tool calls, and concurrency limits.
9. **Full observability.** Every request, response, tool call, validation result,
   cancellation, and cost record is correlated in events and datasets.
10. **Simple first delivery.** Real model support should not require a general
    workflow engine, arbitrary code execution, or a large agent framework.

## 3. Non-goals for the first version

- Giving the model direct access to Python, SQL, files, HTTP, or shell commands.
- Allowing the model to create new tools at runtime.
- Letting a model claim that movement, work, eating, or speech succeeded.
- Unlimited recursive model/tool/model loops within one simulation tick.
- Long-term autonomous planning over an entire day in one response.
- Provider-specific message objects inside domain or application components.
- Sharing hidden chain-of-thought. Store a short decision note, not private
  reasoning.
- Restoring in-flight model requests after process restart.
- Replacing System 1, pathfinding, physiology, or deterministic affordances.

## 4. Existing foundation

The current implementation already provides several useful boundaries:

- `MacroPlanningSystem` and `MacroDialogueSystem` enqueue work.
- `MacroWorkCoordinator` invokes providers after ordered micro-systems finish.
- `Planner`, `DialogueGenerator`, and `EmbeddingProvider` are provider-neutral
  protocols.
- `PlanExecutionSystem` owns action execution.
- `PlanAction` is a closed action vocabulary.
- System 1 clears plans and blocks cognition while survival behavior is active.
- Events and datasets already capture provider, latency, and token metadata.

The main limitation is that planning and dialogue are separate response modes.
The planner returns a preconstructed action list, while dialogue is generated
only after `SOCIALIZE` starts. A real tool-capable model should instead make one
typed decision through a unified controller, with speech represented as a tool
call alongside movement and activity.

### 4.1 Verified sensing gap

The current runtime does **not** have an agent-facing perception system:

- telemetry snapshots expose authoritative world and agent state for the human
  observer;
- planner context directly projects zones, stations, location, and vitals;
- memory recording consumes selected domain events for the event's own agent;
- conversation stores delivered text but does not model audibility;
- there is no line-of-sight, hearing range, observer-specific inbox, last-seen
  knowledge, or public/private fact classification.

Telemetry must not be reused as LLM input. It includes omniscient state intended
for debugging and visualization. Passing it to a controller would reveal private
plans, destinations, physiology, System 1 drives, and memories.

Before real character controllers are enabled, add a sensing boundary that
produces observer-specific facts. For example:

```text
private decision:
  agent-a called go_to(target_id="home")

authoritative execution:
  agent-a moved from office tile (6, 1) to corridor tile (5, 1)

agent-b's visual perception:
  "agent-a left the Office through the west side"

facts agent-b must not receive:
  agent-a is going home
  agent-a's reason for leaving
  agent-a's queued destination
```

The visible action is evidence; destination and reason remain private unless
spoken or otherwise made observable.

## 5. Target architecture

```text
Ordered micro-tick
  |
  | emits authoritative events and state transitions
  v
PerceptionProjector
  |
  | public observable facts only
  v
VisionResolver + HearingResolver + KnowledgeUpdater
  |
  | observer-specific PerceptionPacket values
  v
PerceptionInbox
  |
  | triggers cognition eligibility / supplies recent observations
  v
CognitionScheduler (application)
  |
  | creates immutable CharacterDecisionRequest
  v
CharacterContextBuilder ---- MemoryRetriever
  |
  | provider-neutral messages + allowed tool definitions
  v
CharacterController (application port)
  |
  v
ModelClient (adapter port)
  |
  +-- OpenAICompatibleClient
  +-- AnthropicClient (future)
  +-- LocalModelClient (future)
  +-- ScriptedModelClient
  +-- ReplayModelClient
  |
  | returns ModelTurn with typed ToolCall values
  v
ToolPolicy + ToolRegistry (application)
  |
  | validates identity, schema, permission, freshness, and preconditions
  v
CharacterTool handlers (application)
  |
  | produce domain intents; never mutate state through provider code
  v
IntentCommitter at deterministic post-tick boundary
  |
  +-- PlanAction / movement intent
  +-- Affordance intent
  +-- Speech intent
  +-- Wait intent
  |
  v
Existing deterministic domain systems
```

### Dependency direction

```text
domain
  <- application contracts, policies, and orchestration
      <- provider and persistence adapters
          <- API/CLI composition
```

The domain must not import model clients, prompts, JSON schemas, HTTP libraries,
or provider SDKs.

## 6. Proposed package structure

Keep the current package and add focused modules rather than importing a large
agent framework:

```text
src/stage0_sim/
  domain/
    components/
      cognition.py          # controller state, pending decision metadata
      conversation.py       # later extraction if conversation grows
    intents.py              # accepted character intents
    systems/
      plans.py              # executes movement/activity/affordance plans
      speech.py             # validates and delivers committed speech
      perception.py         # observer inbox and persistent last-seen knowledge
    perception/
      facts.py              # public, private, and self-only fact contracts
      modalities.py         # vision/hearing configuration
    systems/
      perception.py         # deterministic fact routing and knowledge updates

  application/
    perception/
      projector.py          # domain event/state -> observable fact candidates
      vision.py             # range, line-of-sight, identity, and visibility
      hearing.py            # audibility and sound propagation
      packets.py            # observer-specific bounded packets
      renderer.py           # deterministic fact-to-text rendering
      narrator.py           # optional non-authoritative translation port
      policy.py             # disclosure, deduplication, and retention rules
    agents/
      __init__.py
      contracts.py          # model-neutral request/response/tool dataclasses
      controller.py         # one bounded decision turn
      scheduler.py          # eligibility, cancellation, stable ordering
      context.py            # authoritative observation projection
      prompts.py            # controller instructions and prompt versioning
      policy.py             # tool permissions and decision limits
      registry.py           # tool definitions and dispatch
      results.py            # tool result and rejection normalization
      tools/
        movement.py
        activity.py
        speech.py
        waiting.py
    macro_work.py           # queue/commit boundary; eventually delegates here

  adapters/
    llm/
      base.py               # optional shared adapter helpers
      openai_compatible.py  # first real provider
      scripted.py           # deterministic tests
      replay.py             # replay recorded model turns
      fake.py               # current fakes during migration

  api/
    app.py
    simulation.py

tests/
  agents/
    test_controller.py
    test_context.py
    test_tool_policy.py
    test_tool_registry.py
    test_speech_tool.py
    test_provider_contract.py
    test_replay.py
```

`application/agents` contains no provider-specific classes. Provider adapters
contain no ECS mutation logic.

`application/perception` may inspect authoritative state through explicit
projections, but it may not expose arbitrary ECS snapshots. The perception
package owns disclosure; the character controller consumes only its output.

## 7. Core contracts

Use immutable dataclasses or strict Pydantic models at I/O boundaries. The exact
syntax can change during implementation, but responsibilities should remain
separate.

### 7.1 Model-neutral messages and tool calls

```python
@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ModelToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ModelTurn:
    text: str | None
    tool_calls: tuple[ModelToolCall, ...]
    finish_reason: str
    provider: str
    model: str
    latency_ms: float
    input_tokens: int | None
    output_tokens: int | None
    provider_request_id: str | None
```

`ModelTurn` contains data, not provider SDK objects. Raw provider responses may be
retained only in an adapter-level diagnostic store with explicit redaction and
size limits.

### 7.2 Provider port

```python
class ModelClient(Protocol):
    async def complete(
        self,
        request: ModelRequest,
    ) -> ModelTurn: ...
```

The real client should be asynchronous even though current fake protocols are
synchronous. Network latency must not stall the simulation runner. A synchronous
scripted adapter can be wrapped without changing application code.

`ModelRequest` should include:

- provider-neutral messages;
- available tool definitions;
- model configuration;
- request and correlation IDs;
- timeout;
- maximum output tokens;
- tool-choice policy;
- prompt version.

### 7.3 Character decision request

```python
@dataclass(frozen=True, slots=True)
class CharacterDecisionRequest:
    decision_id: str
    run_id: str
    agent_id: str
    requested_tick: int
    state_revision: int
    trigger: DecisionTrigger
    observation: CharacterObservation
    memories: tuple[MemoryExcerpt, ...]
    allowed_tools: tuple[str, ...]
```

`state_revision` prevents stale responses from being applied after a survival
interrupt, action change, target disappearance, or newer decision.

### 7.4 Controller port

```python
class CharacterController(Protocol):
    async def decide(
        self,
        request: CharacterDecisionRequest,
    ) -> CharacterDecision: ...
```

The default implementation builds the prompt, calls `ModelClient`, validates the
response envelope, and returns one normalized decision. Tests may replace the
whole controller or only the model client.

### 7.5 Tool handler port

```python
class CharacterTool(Protocol[ArgsT]):
    name: str
    args_type: type[ArgsT]

    def definition(self, context: ToolDefinitionContext) -> ToolDefinition: ...

    def authorize(self, context: ToolExecutionContext, args: ArgsT) -> None: ...

    def propose(
        self,
        context: ToolExecutionContext,
        args: ArgsT,
    ) -> CharacterIntent: ...
```

`propose` returns an intent. It does not write to the registry. The
`IntentCommitter` applies accepted intents in stable order after checking that the
decision is still current.

## 8. Initial tool catalog

Keep the first catalog small and action-oriented.

### 8.1 `go_to`

Purpose: direct the character toward a known place or character.

```json
{
  "target_id": "kitchen",
  "reason": "Get something to eat"
}
```

Rules:

- `target_id` must be present in the supplied observation.
- A zone resolves to a deterministic reachable tile.
- A station resolves to its interaction tile.
- A character resolves initially to a deterministic reachable adjacent tile
  based on the target's position when the intent is accepted.
- Acceptance means movement was queued, not that arrival succeeded.
- Pathfinding, occupancy, and failure remain domain-owned.

Initial implementation may restrict targets to zones and stations if following a
moving character would enlarge scope too much. The schema should still use a
generic `target_id` so agent targets can be enabled later.

### 8.2 `perform`

Purpose: start a supported intentional activity or affordance.

```json
{
  "action": "WORK",
  "target_id": "desk-1",
  "duration_seconds": 300,
  "reason": "Complete the current work goal"
}
```

Allowed initial actions:

- `WORK`
- `READ`
- `EAT`
- `SLEEP`
- `RELAX`

Rules:

- Action is selected from a closed enum.
- Duration bounds are tool policy, not prompt advice.
- Affordance preconditions are checked by the domain at execution time.
- The model cannot provide meter effects.
- The model cannot perform an action unsupported by the target station.

### 8.3 `say`

Purpose: speak exact words through the controlled character.

```json
{
  "target_id": "agent-002",
  "text": "Would you like to take a break together?",
  "reason": "Invite a nearby colleague to relax"
}
```

Rules:

- `text` is the literal in-world utterance.
- The target must be a dialogue-capable character visible/known to the speaker.
- Distance, audibility, availability, and System 1 state are deterministic
  preconditions.
- The tool creates a `SpeechIntent`; it does not append directly to conversation
  history.
- Delivery emits `speech.started`, `speech.delivered`, or `speech.failed`.
- Delivered speech is recorded as an episode for speaker and listener according
  to memory policy.
- A later version may support `audience_ids`, volume, channel, and nonverbal acts.

This tool replaces the need for a second LLM call that generates dialogue after a
generic `SOCIALIZE` action. `SOCIALIZE` may remain as a timed social activity, but
actual words should come from `say`.

### 8.4 `wait`

Purpose: intentionally do nothing for a bounded duration.

```json
{
  "duration_seconds": 30,
  "reason": "Wait for the other character to arrive"
}
```

Rules:

- Maps to an `IDLE` plan action.
- Has scenario-configurable minimum and maximum duration.
- Remains interruptible by System 1.

### 8.5 Deferred read-only tools

Do not include these in the first real-run milestone unless context size requires
them:

- `observe_nearby`
- `inspect_place`
- `recall_memory`
- `inspect_schedule`

The initial `CharacterObservation` can include the necessary bounded information
directly. Later, read-only tools can reduce prompt size and support a bounded
multi-turn decision loop.

## 9. Controller instruction style

The system instruction should be versioned and tested as a contract. Suggested
baseline:

> You are the executive controller for a simulated person named `{name}`. Choose
> the person's next intentional action from the supplied state and goals. You are
> not the person, the simulation engine, or a narrator. Do not claim that an
> action happened. Use exactly one available action tool to express what the
> person should attempt next. Use `say` only for the exact words the person should
> speak in-world. The simulation decides whether actions succeed and may interrupt
> them for survival needs.

Additional prompt rules:

- Refer to world objects only by supplied IDs.
- Never invent coordinates, characters, stations, memories, or tool names.
- Do not put speech in free text; use `say`.
- Do not expose hidden reasoning. The optional `reason` is a short decision note.
- Treat tool acceptance as "queued", not "completed".
- Prefer one coherent short-horizon action.
- If no useful action is available, call `wait`.

The prompt should describe the model as a controller because this produces a
clean separation:

- controller decision: "direct Alex to go to the lounge";
- in-world speech: `say(text="I will meet you in the lounge.")`;
- simulation fact: emitted only after movement or speech succeeds.

## 10. Sensing, perception, and world translation

### 10.1 Recommended solution: deterministic kernel, optional narrator

Several translation designs are possible:

| Approach | Advantages | Problems | Recommendation |
| --- | --- | --- | --- |
| Give each controller raw ECS/events | Minimal implementation | Omniscient, unstable coupling, leaks plans/vitals/memories | Reject |
| Deterministic perception plus templates | Cheap, testable, replayable, cannot invent facts | Text can be repetitive; nuanced scenes need more templates | Required foundation |
| Per-agent narrator LLM | Natural observer-specific prose | Extra calls, latency, nondeterminism, possible invented facts | Optional later |
| One omniscient broadcaster LLM | Can summarize a whole scene efficiently | Highest mind-reading/leakage risk; output partitioning is difficult | Do not use as authority |
| Hybrid fact kernel plus constrained narrator | Reliable facts with optional natural language | More contracts and validation | Recommended architecture |

A narrator LLM is **not necessary for correct sensing**. The controller can
understand compact structured facts and deterministic text. Add a narrator only
as a surface-realization adapter when richer language is worth the additional
cost.

If a broadcaster is introduced for batching efficiency, it must receive only
already-filtered `PerceptionPacket` values, never the global event stream or
private agent state. It acts as a formatter, not an observer and not a source of
facts.

### 10.2 Three representations

Keep these representations distinct:

1. **Authoritative state/event:** complete simulation truth, including private
   plans and physiological state.
2. **Perceptible fact:** a typed claim that could be sensed, with no private
   fields.
3. **Rendered observation:** deterministic or LLM-generated wording presented
   to one controller.

```python
@dataclass(frozen=True, slots=True)
class PerceptibleFact:
    fact_id: str
    event_id: str | None
    tick: int
    fact_type: str
    subject_id: str | None
    object_id: str | None
    location_id: str | None
    properties: Mapping[str, JsonValue]
    modality: Literal["visual", "auditory", "self"]


@dataclass(frozen=True, slots=True)
class PerceivedFact:
    fact: PerceptibleFact
    observer_id: str
    perceived_tick: int
    certainty: Literal["direct", "partial", "inferred"]
    salience: float


@dataclass(frozen=True, slots=True)
class PerceptionPacket:
    observer_id: str
    start_tick: int
    end_tick: int
    facts: tuple[PerceivedFact, ...]
    deterministic_text: tuple[str, ...]
```

`PerceptibleFact` should contain the minimum public semantics. Do not create a
fact and then ask a later filter to remove sensitive fields.

### 10.3 Disclosure classes

Every source field or event family needs an explicit disclosure class:

| Class | Recipients | Examples |
| --- | --- | --- |
| `SELF` | Character itself | Own vitals, current goal, accepted tool, interrupted action |
| `DIRECT_PARTICIPANTS` | Named participants | Delivered private speech, direct interaction outcome |
| `LOCAL_VISUAL` | Observers that can see the event | Movement, entering/leaving a zone, visible activity |
| `LOCAL_AUDITORY` | Observers that can hear the event | Speech, audible impact, alarm |
| `PUBLIC_WORLD` | All characters with world knowledge | Clock chime, announced closure |
| `ADMIN_ONLY` | UI, logs, researchers | Plans, tool reasons, destinations, hidden drives, raw prompts |

The default must be `ADMIN_ONLY`, not public. New event types become perceptible
only through an explicit projector.

### 10.4 Observable versus private matrix

| Authoritative information | Self | Other nearby agents |
| --- | --- | --- |
| Current position | Known | Visible if vision resolves |
| Tile-by-tile movement | Proprioceptive | Visible while in line of sight |
| Zone entry/exit | Known | Visible if transition is observed |
| Intended destination | Known after accepted tool | Never exposed automatically |
| Tool `reason` | Private decision note | Never exposed |
| Plan queue | Known in bounded form | Never exposed |
| Activity such as working/reading | Known | Visible if outwardly observable |
| Affordance use | Known | "using the fridge/bed/sofa" if visible |
| Satiety/energy/stress values | Known | Never exposed |
| System 1 drive | Known internally | Never exposed directly |
| Distress behavior | N/A | May be a separately modeled visible cue |
| Speech text | Known | Heard only by resolved recipients |
| Memory and retrieved context | Known as memory | Never exposed |
| Provider prompt/rationale | Controller infrastructure | Never exposed |

Do not infer a private cause from a visible effect. If a character abruptly
leaves work because of low energy, observers perceive the departure, not
"agent-a is exhausted" unless an explicit visible cue or utterance supports it.

### 10.5 Vision model

Use deterministic grid sensing first:

- configurable range per observer;
- deterministic supercover line-of-sight across grid cells;
- blocked/opaque tiles stop vision;
- 360-degree vision initially; facing and field-of-view can be added later;
- identity is available only when within recognition range or previously known;
- observation is evaluated at authoritative tick boundaries;
- zone transitions are derived from positions, not plan destinations;
- repeated unchanged facts are deduplicated.

Initial visual facts:

- `entity_seen`
- `entity_moved`
- `entity_entered_zone`
- `entity_left_zone`
- `visible_activity_started`
- `visible_activity_stopped`
- `visible_affordance_use`
- `object_availability_changed`

Movement rendering should describe evidence:

```text
agent-a left the Office.
agent-a moved west into the corridor.
agent-a is no longer visible.
```

It should not render:

```text
agent-a is heading home.
agent-a decided to stop working.
agent-a is looking for a bed.
```

### 10.6 Hearing model

Speech and sounds create source events with:

- source position and tick;
- literal utterance or sound type;
- volume/radius;
- optional intended addressee;
- channel (`voice`, `whisper`, `announcement`, later others).

Resolve recipients deterministically when the sound is emitted. For the Stage 0
grid, use shortest traversable-path distance as a simple sound attenuation model;
this avoids hearing directly through long blocked barriers without introducing
continuous acoustics. Later, tiles may define absorption and doors.

Rules:

- normal speech is heard within configured path distance;
- whispers have a shorter range;
- announcements can use zone/public routing;
- intended target does not guarantee audibility;
- unintended nearby characters may overhear ordinary speech;
- listeners receive exact delivered text plus speaker identity only if recognized;
- late-arriving characters do not hear past speech unless another character tells
  them;
- `say.reason` and controller rationale are never part of the sound.

`speech.delivered` should record resolved recipient IDs for audit, while each
recipient receives its own `heard_speech` fact. Admin events may contain the full
recipient list; agent context may not.

### 10.7 Perception inbox and knowledge

Add a `PerceptionComponent` with:

```text
inbox
  bounded ordered perceived facts not yet consumed by cognition

visible_now
  entity IDs and last authoritative visible state

knowledge
  last seen location/activity and when it was observed

last_processed_tick
  idempotent routing cursor
```

Knowledge is not current truth. A last-seen record must retain its age:

```text
"You last saw agent-a leave the Office 45 simulated seconds ago."
```

Never silently update a character's knowledge from global state. Knowledge
changes only through:

- self state;
- direct perception;
- explicit communication;
- scenario-initialized common knowledge.

The inbox should be bounded by count, age, and salience. Overflow emits an
observable diagnostic event and uses deterministic eviction.

### 10.8 Event-driven and snapshot sensing

Use both:

- **event-driven projection** for transitions such as movement, zone entry,
  activity changes, speech, and object changes;
- **periodic local scan** for continued visibility, newly visible entities after
  an observer moves, and disappearance.

This avoids reconstructing every transition from snapshots while ensuring an
observer can notice an already-present character after entering a room.

Perception systems run after physical/action systems and before cognition
eligibility is captured. This lets a decision at tick `t` include facts sensed at
tick `t`, while provider execution still stays outside the micro-system stack.

### 10.9 Translation/rendering layer

Define:

```python
class PerceptionRenderer(Protocol):
    def render(self, packet: PerceptionPacket) -> RenderedPerception: ...
```

The required `DeterministicPerceptionRenderer` uses versioned templates and
stable entity display names. It is the canonical renderer for tests and replay.

An optional `NarratorClient` may produce more natural prose:

```python
class NarratorClient(Protocol):
    async def render(self, packet: PerceptionPacket) -> NarrationResult: ...
```

Narrator constraints:

- input is one observer's already-filtered packet;
- it receives no plans, goals, destinations, vitals, memories, or rationales;
- output includes the `fact_id` values it used;
- it may merge or rephrase facts but may not add entities or events;
- output is advisory prose and never authorizes tools;
- structured facts remain alongside the prose in controller context;
- failure emits `narration.failed` and uses the canonical deterministic rendering;
- narration is cacheable by renderer version plus packet hash;
- narrator calls have a separate budget from character decisions.

The deterministic rendering is the source of truth. The narrator is comparable
to localization or UI presentation, not a cognitive oracle.

### 10.10 Broadcaster option

A global broadcaster LLM is not recommended for the first implementation. If
later cost analysis favors batching:

1. Build recipient-specific packets deterministically.
2. Remove private/self-only fields before batching.
3. Give each packet an opaque channel ID.
4. Require structured outputs keyed by channel and source `fact_id`.
5. Validate that each output mentions only entities present in that packet.
6. Deliver output only to the corresponding observer.
7. Retain deterministic text as the replay/failure path.

The broadcaster must never decide who perceived an event. It only phrases facts
after recipient resolution.

## 11. Character observation

Build observations from explicit projection code, not by serializing the ECS.
Suggested bounded structure:

```text
identity
  id, display name, stable traits, current goals

time
  simulation time, day/period if configured

self
  location, activity, active action, queued action count
  satiety, energy, stress
  System 1 availability (normally requests are suppressed when unavailable)

known world
  currently visible/relevant zones and stations
  supported actions and availability
  currently perceived characters and relationship summaries

perception
  new structured visual/auditory/self facts
  deterministic text and optional narrator rendering
  last-seen knowledge with timestamps

recent outcomes
  last accepted tool
  action completion/failure/interruption
  recent heard speech

memory
  top-k excerpts with simulation timestamps and relevance metadata
```

Do not include:

- private state of distant characters;
- private plans, destinations, tool reasons, or drives of any other character;
- full event history;
- raw embeddings;
- provider credentials;
- database identifiers not meaningful in-world;
- exact hidden pathfinding internals unless deliberately observable.

Tool authorization must use structured perception/knowledge, not narrator prose.
For example, `say(target_id="agent-a")` checks audibility/visibility policy and
known identity directly. A narrator mentioning a name cannot make that target
legal.

Add a `CharacterProfileComponent` or equivalent scenario definition for:

- display name;
- concise personality/decision tendencies;
- stable values;
- role/occupation;
- initial goals;
- optional relationship facts.

Avoid a single free-form "persona prompt" with no schema. Structured fields make
future model or prompt changes safer.

## 12. Decision lifecycle

### 12.1 Triggers

Request a decision only when:

- the character has no active or queued intentional action;
- a previous action completed or failed;
- a relevant external event requests reconsideration;
- a conversation turn requires a response;
- a configurable deliberation deadline expires.

Do not request decisions:

- every micro-tick;
- while System 1 is active;
- while an equivalent request is in flight;
- while a committed action is still valid;
- solely because telemetry was published.

### 12.2 Request flow

1. `CognitionScheduler` detects eligibility during the ordered pass.
2. It records a deterministic request with `decision_id`, tick, revision, and
   trigger.
3. A worker consumes the request outside the simulation tick.
4. Context and memories are projected from the captured request snapshot.
5. `CharacterController` calls the model with allowed tools.
6. The response is normalized into zero or one state-changing `ModelToolCall`.
7. The result enters a completion queue; it does not mutate the world.
8. At a later post-tick commit boundary, results are sorted by request tick,
   agent ID, and decision ID.
9. Policy and tool schemas are validated again against current state.
10. A valid tool call becomes a domain intent and then an executable plan/speech
    request.
11. Rejections become observable tool results and usually make the character
    eligible for a later decision.

### 12.3 Why not an unbounded ReAct loop initially

An immediate model -> tool -> model -> tool loop creates ambiguous simulated
time, higher cost, and opportunities for the model to act on hypothetical
success. In the first version:

- state-changing tools end the model turn;
- real domain completion/failure is observed on a future cognition opportunity;
- read-only tool loops may be added later with `max_read_tool_rounds` (for
  example, 2);
- `max_state_changing_tools_per_decision` remains 1.

## 13. Tool validation and safety

Validation should occur in layers:

1. **Envelope validation:** response shape, call count, known call ID.
2. **Schema validation:** strict typed arguments; reject extra fields.
3. **Catalog validation:** tool was offered for this exact decision.
4. **Identity validation:** caller controls only its own character.
5. **Freshness validation:** decision ID and state revision are current.
6. **System 1 validation:** character and affected social target are normal.
7. **Capability validation:** required components and scenario permissions exist.
8. **World validation:** referenced IDs exist and are known/visible as required.
9. **Precondition validation:** range, station action, target availability,
   duration, and capacity.
10. **Commit validation:** repeat volatile checks immediately before mutation.

Never rely on the prompt to enforce a rule that can be checked in code.

Tool failure categories should be stable machine-readable values:

- `unknown_tool`
- `invalid_arguments`
- `tool_not_allowed`
- `stale_decision`
- `system1_preemption`
- `unknown_target`
- `target_not_observable`
- `target_unavailable`
- `precondition_failed`
- `conflicting_action`
- `provider_timeout`
- `provider_error`

## 14. System 1 and cancellation semantics

System 1 remains above the LLM controller:

- A critical threshold prevents new decisions.
- Pending provider requests receive cancellation tokens when possible.
- Late provider results are rejected by revision/drive checks.
- Accepted model actions are cleared by existing preemption behavior.
- `say` is rejected if either speaker or target is under survival control.
- No retry call is made merely because System 1 rejected a result.
- The next normal decision includes a concise observation that the prior action
  was interrupted by a survival need.

Provider cancellation is an optimization. Correctness must rely on stale-result
rejection because network cancellation is not guaranteed.

## 15. Provider adapter design

### 15.1 First adapter: OpenAI-compatible HTTP

Implement one adapter against the common chat-completions/tool-call shape. This
supports hosted APIs and many local servers without coupling the application to
one vendor.

Configuration:

```text
STAGE0_LLM_PROVIDER=openai-compatible
STAGE0_LLM_BASE_URL=http://127.0.0.1:8080/v1
STAGE0_LLM_MODEL=<model-name>
STAGE0_LLM_API_KEY=<optional>
STAGE0_LLM_TIMEOUT_SECONDS=30
STAGE0_LLM_MAX_OUTPUT_TOKENS=512
STAGE0_LLM_MAX_CONCURRENCY=4
```

Do not put credentials in scenarios, events, datasets, or prompts. Resolve
secrets only in application composition.

Prefer a small internal HTTP adapter or an optional provider extra. Avoid making
a large vendor SDK a required dependency for fake-only simulation runs.

### 15.2 Provider registry

Composition should select providers by configuration:

```python
model_client = model_client_registry.create(settings.llm)
controller = ToolCallingCharacterController(
    model_client=model_client,
    prompt_builder=prompt_builder,
    tool_registry=tool_registry,
    policy=policy,
)
```

No `if provider == ...` branches should appear in domain systems.

### 15.3 Scripted and replay adapters

Keep deterministic testing first-class:

- `ScriptedModelClient` returns specified `ModelTurn` values.
- `ReplayModelClient` looks up a recorded response by request hash or decision ID.
- `RecordingModelClient` decorates another client and writes sanitized request
  and response envelopes.

Replay is essential for reproducing simulation behavior without paying for or
depending on a live model.

## 16. Scheduling and concurrency

The current post-tick coordinator calls synchronous fake providers directly. Real
network calls should use:

- an `asyncio.Queue` for requests;
- a bounded worker pool;
- per-run and global concurrency limits;
- per-request timeout;
- a thread-safe or event-loop-owned completion queue;
- deterministic completion application at post-tick boundaries.

Wall-clock completion order must not decide simulation order. If several results
are ready, commit them by:

```text
(requested_tick, agent_id, decision_sequence)
```

The simulation may continue while requests are pending. Each request contains a
state revision; stale results are rejected. A scenario may optionally pause a
specific character's System 2 activity while waiting, but must not pause the
global micro-clock.

## 17. Events and persistence

Add or normalize these event families:

```text
cognition.eligible
cognition.requested
cognition.completed
cognition.failed
cognition.cancelled

tool.proposed
tool.accepted
tool.rejected
tool.committed

speech.started
speech.delivered
speech.failed

perception.detected
perception.lost
perception.delivered
perception.dropped

narration.requested
narration.completed
narration.failed
```

Every cognition/tool event should carry:

- run, event, decision, and tool-call IDs;
- simulation request tick and application tick;
- agent ID and target ID when applicable;
- trigger and prompt version;
- provider and model;
- latency and token counts;
- tool name and sanitized arguments;
- validation outcome/reason;
- causation and correlation IDs.

Persist:

- the normalized request observation or a versioned reference to it;
- prompt template/version and tool schema version;
- normalized model result;
- accepted/rejected tool call;
- usage and latency;
- action outcome linkage;
- observer ID, modality, source fact IDs, and perception policy version;
- renderer/narrator version and narration source fact IDs.

Do not persist API keys, authorization headers, or unrestricted raw provider
objects. Speech text and prompts may contain sensitive scenario data, so add a
configurable redaction/retention policy before external deployments.

## 18. Scenario and runtime configuration

Add an optional cognition section:

```json
{
  "perception": {
    "vision_range": 8,
    "recognition_range": 5,
    "hearing_range": 10,
    "whisper_range": 2,
    "blocked_tiles_are_opaque": true,
    "inbox_limit": 100,
    "fact_max_age_seconds": 300,
    "renderer": "deterministic"
  },
  "cognition": {
    "controller": "tool-agent",
    "model_profile": "default",
    "decision_timeout_seconds": 30,
    "max_output_tokens": 512,
    "max_read_tool_rounds": 0,
    "max_state_changing_tools": 1,
    "tool_allowlist": ["go_to", "perform", "say", "wait"]
  }
}
```

Optional narration is deployment configuration, not a source of world rules:

```text
STAGE0_NARRATOR_ENABLED=false
STAGE0_NARRATOR_MODEL_PROFILE=default-narrator
STAGE0_NARRATOR_MAX_OUTPUT_TOKENS=256
STAGE0_NARRATOR_MAX_REQUESTS_PER_TICK=0
```

`max_requests_per_tick=0` disables narration. When enabled, narrator limits and
cost accounting are separate from character-controller budgets.

Characters may narrow the global policy:

```json
{
  "id": "agent-001",
  "components": {
    "character_profile": {
      "display_name": "Alex",
      "role": "researcher",
      "traits": ["methodical", "reserved"],
      "values": ["finish commitments", "help colleagues"],
      "goals": ["complete the report", "speak with agent-002"]
    },
    "controller": {
      "enabled": true,
      "tool_allowlist": ["go_to", "perform", "say", "wait"]
    },
    "senses": {
      "vision_range": 6,
      "hearing_multiplier": 1.0
    }
  }
}
```

Provider credentials and endpoints belong to environment/application settings,
not scenario documents. Scenarios should be portable and safe to commit.

## 19. Migration from current planner/dialogue code

Avoid a flag-day replacement.

### Step 0: Establish the perception boundary

- Add typed perceptible facts, modality resolvers, and observer inboxes.
- Classify existing events as self, local visual, local auditory, public, or
  admin-only.
- Route movement, zone transitions, activities, affordance use, and speech
  without exposing plans or destinations.
- Add deterministic rendering and last-seen knowledge.
- Stop building future controller context from omniscient world projections.

### Step 1: Introduce model/tool contracts

- Add `application/agents/contracts.py`.
- Implement `ScriptedModelClient`.
- Implement tool schemas and policy without changing current runtime.
- Map tool calls to existing `PlanAction` values.

### Step 2: Add controller behind the current planner port

- Build a compatibility `ToolAgentPlanner`.
- It invokes the new controller and converts `go_to`, `perform`, and `wait` into
  one-action `PlanResult`.
- Keep `FakePlanner` and `ScriptedPlanner` unchanged.
- This proves real provider isolation before changing dialogue.

### Step 3: Introduce `say` and `SpeechIntent`

- Add deterministic speech delivery and events.
- Let the tool controller emit `say`.
- Preserve `DialogueGenerator` temporarily for legacy `SOCIALIZE` scenarios.
- Mark generated-after-socialize dialogue as legacy once `say` is stable.

### Step 4: Replace synchronous macro invocation with workers

- Add async request and completion queues.
- Retain the same deterministic commit boundary.
- Add stale-result and cancellation handling.
- Keep fake tests able to drain synchronously for speed.

### Step 5: Make tool-agent controller the opt-in real-run mode

- Add configuration and API composition.
- Keep scripted/fake controller as default for tests and deterministic demos.
- Add a dedicated real-LLM example scenario.

### Step 6: Retire compatibility interfaces when safe

- Migrate remaining planner and dialogue callers.
- Keep thin adapters only if external users depend on them.
- Do not remove deterministic scripted/replay paths.

## 20. Implementation phases and gates

### Phase A: Deterministic perception foundation

Deliver:

- deterministic visual/hearing perception kernel;
- disclosure classification and observer-specific inboxes;
- canonical perception renderer;
- event-driven and local-scan perception;
- last-seen knowledge with timestamps;
- perception events and persistence.

Gate:

- Nearby observers see/hear only resolvable public facts and never receive
  another character's plan, destination, reason, vitals, drive, or memories.
- An observer sees that a character left the office without learning where that
  character intends to go.
- Perception and rendering are deterministic and require no model calls.

### Phase B: Contracts and deterministic tool execution

Deliver:

- provider-neutral model contracts;
- tool registry and strict schemas;
- `go_to`, `perform`, `say`, and `wait` handlers;
- character observation and profile models;
- scripted tool-calling controller;
- tool/event persistence.

Gate:

- A scripted model controls movement, work, speech, and waiting exclusively
  through tools.
- Invalid/stale tools never mutate domain state.
- Controller context is built only from self state, perception, knowledge, and
  explicitly initialized common knowledge.

### Phase C: Real provider, single decision

Deliver:

- OpenAI-compatible async adapter;
- environment configuration;
- timeout, retry classification, concurrency limits;
- sanitized request/response recording;
- opt-in real-run scenario.

Gate:

- A real model chooses and invokes one valid tool.
- Provider timeout does not stall micro-ticks.
- No credential or provider object enters events/datasets.

### Phase D: Asynchronous scheduling and replay

Deliver:

- worker queues and deterministic completion commits;
- revision-based stale rejection;
- cancellation;
- recording/replay clients;
- per-agent cost limits.

Gate:

- Different wall-clock response orders yield the same committed order for the
  same prepared completion set.
- Recorded runs can be replayed without live model access.

### Phase E: Richer interaction

Deliver only as needed:

- optional fact-constrained narrator or batched broadcaster;
- bounded read-only tool rounds;
- moving-character navigation;
- relationship-aware observations;
- multi-turn conversations;
- schedules and longer-horizon goals;
- provider fallback/routing.

Gate:

- New capabilities are plugins or policy additions, not changes to domain
  authority or provider contracts.

## 21. Testing strategy

### Contract tests

Run every model adapter against the same fixtures:

- tool call parsed correctly;
- missing/invalid arguments rejected;
- multiple state-changing calls rejected;
- text-only response handled;
- timeout and transport failures normalized;
- usage metadata preserved;
- provider-specific IDs do not leak into domain types.

### Tool tests

For each tool:

- valid call produces the expected intent;
- unknown target is rejected;
- stale decision is rejected;
- System 1 prevents commit;
- extra arguments are rejected;
- model-supplied effects cannot alter physiology;
- success means queued/committed, not completed.

### Integration tests

- an observer sees an agent leave the office but not the agent's destination or
  decision reason;
- an out-of-range or occluded observer receives no movement/speech fact;
- a nearby unintended listener can overhear normal speech;
- a character entering a room notices an already-present visible character;
- last-seen knowledge remains timestamped and does not update omnisciently;
- narrator input contains only recipient-filtered facts;
- narrator output cannot make an unauthorized target/tool valid;
- `go_to` reaches a station through existing pathfinding.
- `perform(EAT)` only succeeds at a compatible station.
- `say` delivers exact text only when social preconditions hold.
- speech is visible in telemetry and memory.
- survival activation cancels pending movement/speech decisions.
- provider completion after cancellation is ignored.
- action failure causes a later decision with the failure observation.

### Determinism and replay tests

- scripted responses produce identical canonical events.
- replay produces the same accepted tool calls as the recorded run.
- response arrival order does not change deterministic commit ordering.
- tool IDs, decision IDs, and correlations remain stable where expected.

### Live smoke tests

Keep live tests opt-in:

```text
STAGE0_RUN_LIVE_LLM_TESTS=1
```

They should use strict cost/time limits and never run in normal CI.

## 22. Operational safeguards

Recommended defaults:

| Limit | Initial default |
| --- | --- |
| State-changing tool calls per decision | 1 |
| Read-only tool rounds | 0 |
| Output tokens | 512 |
| Request timeout | 30 seconds |
| Retries | 1 for clearly transient transport failures |
| Concurrent calls per run | 4 |
| Concurrent calls per character | 1 |
| Decision requests per simulated minute | Configurable, low |
| Speech text length | 500 characters |
| Tool argument payload | 8 KiB |

Add a run-level budget:

- maximum requests;
- maximum input/output tokens;
- optional estimated cost ceiling;
- behavior when exhausted (`wait`, scripted fallback, or stop cognition).

Budget exhaustion must be an event, not a silent fallback.

## 23. Important design decisions for review

### Recommended now

1. Use one unified character controller rather than separate real planner and
   real dialogue model calls.
2. Build deterministic observer-specific sensing before enabling real controllers.
3. Keep telemetry/admin truth separate from character perception.
4. Use a deterministic renderer by default; make LLM narration optional and
   non-authoritative.
5. Allow one state-changing tool per cognition opportunity.
6. Keep domain execution behind intents and existing systems.
7. Implement `say` as literal speech selected by the controller.
8. Start with an OpenAI-compatible adapter but keep all contracts
   provider-neutral.
9. Make real providers opt-in; scripted/fake remains the default.
10. Add asynchronous workers before calling remote models during realtime runs.
11. Persist normalized requests/results and support deterministic replay.

### Decisions that may be deferred

1. Whether `go_to` may target moving characters in the first release.
2. Initial vision range, recognition range, and whether facing matters.
3. Initial speech ranges by channel and whether closed doors need explicit tile
   semantics.
4. Whether an optional narrator is useful after deterministic templates are
   evaluated. Recommendation: measure first; do not block real controllers on it.
5. Whether a character waits in place or continues a safe default activity while
   a request is pending.
6. Whether rejected tool calls trigger a second model call immediately or wait
   for the next cognition opportunity. Recommendation: wait initially.
7. Whether internal decision notes are persisted. Recommendation: store only a
   short optional rationale, never request chain-of-thought.
8. Whether scenarios select a concrete model or only a named model profile.
   Recommendation: named profile, resolved by deployment configuration.

## 24. Acceptance criteria for the first real run

A first real-run milestone is complete when:

1. A character with no active action generates one bounded model request.
2. Deterministic vision/hearing resolves an observer-specific perception packet.
3. The packet reveals public evidence but not private plans, destinations,
   reasons, vitals, drives, prompts, or memories.
4. The request describes the model as the character's controller.
5. The model receives only registered tool definitions, self state, perceived
   facts, and timestamped knowledge.
6. The model calls `go_to`, `perform`, `say`, or `wait`.
7. Strict validation converts the call into an intent without direct ECS mutation.
8. The intent is committed at a deterministic boundary.
9. Existing domain systems execute or reject it based on world state.
10. System 1 can cancel it before or after provider completion.
11. Speech is emitted only through `say`, reaches only deterministically audible
   recipients, and appears in events, telemetry, memory,
   and datasets.
12. An observer can report that another agent left a room without learning the
   hidden destination.
13. Optional narration can rephrase facts but cannot alter tool authorization or
   canonical replay.
14. Timeouts and malformed calls do not stop physical simulation.
15. Provider/model/tokens/latency/tool outcome are observable.
16. The same interaction can be replayed without the live provider.

## 25. Summary

The smallest extensible path is not to give the LLM more direct authority. It is
to make the existing cognition boundary more explicit:

- one character controller;
- a deterministic observer-specific perception kernel;
- an optional fact-constrained, non-authoritative narrator;
- a provider-neutral model client;
- a small typed tool registry;
- immutable intents;
- deterministic domain execution;
- asynchronous real-provider work;
- complete event and replay records.

This supports the desired "LLM controlling a person" style while preserving the
simulation as the source of truth. It also creates clean replacement points for
future sensing rules, renderers, narrator models, controller providers, prompt
strategies, tool catalogs, memory systems, and multi-agent cognition without
forcing those changes into the physics or homeostasis engine.
