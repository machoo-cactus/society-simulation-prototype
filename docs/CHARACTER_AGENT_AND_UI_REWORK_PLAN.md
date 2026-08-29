# Character Agent Definition and UI Rework Plan

**Status:** Implemented  
**Date:** 2026-08-29  
**Scope:** Character-controller prompts and profiles; browser scenario/run
controls; event, speech, and dialogue log usability  
**Testing approach:** Small targeted automated checks plus operator-led browser
testing. Live LLM testing is explicitly not required for this work.

## 1. Goals

This work should solve two separate usability problems without weakening the
simulation's authority boundaries:

1. Give every LLM-controlled character a clear, reusable general controller
   prompt plus a rich, structured, character-specific description.
2. Make the browser behave like an understandable simulation console:
   loading configuration, starting execution, pausing, stopping, and reading
   logs must be distinct and predictable operations.

The work must preserve these existing rules:

- the LLM selects intentions but does not declare outcomes;
- System 1 remains authoritative and non-bypassable;
- character profiles do not expose private state belonging to other characters;
- loading or editing scenario data does not advance simulation time;
- operator logs may be omniscient, but character observations remain filtered.

## 2. Current-state diagnosis

## 2.1 Character definition is too shallow and tightly coupled

The current character profile is fixed in
`domain/components/cognition.py::CharacterProfileComponent`:

```text
display_name
role
traits
values
goals
relationships
```

The matching Pydantic model in `application/scenario.py` rejects every unknown
field with `extra="forbid"`. Adding age, gender, appearance, speech style,
history, preferences, fears, skills, or an experimental field currently
requires coordinated source changes in:

- the scenario model;
- the ECS component;
- the controller observation contract;
- the context builder;
- prompt construction;
- tests and examples.

This is safe but not experimentation-friendly.

The prompt has a second structural problem. `application/agents/prompts.py`
builds one system message that mixes:

- the universal controller contract;
- the character's display name;
- action-selection instructions.

The remaining character information is buried inside the dynamic observation
JSON. There is no explicit character description message, profile template,
profile renderer, section ordering, profile version, or prompt-visible field
policy. As a result:

- character identity is weak relative to dynamic state;
- character descriptions cannot be reviewed independently from the controller
  prompt;
- prompt changes and character-data changes cannot be versioned separately;
- profiles are difficult for non-code contributors to extend;
- arbitrary experimental attributes have no supported path into the prompt.

The existing two-character scenario demonstrates only a role, two traits, two
values, and goals. It does not define a sufficiently concrete person.

## 2.2 The UI conflates loading a scenario with starting a run

`web/app.js` handles file selection by calling `startScenario()`.
`startScenario()` immediately:

1. closes the existing socket;
2. clears current run state and logs;
3. posts the scenario to `/simulation/scenarios`;
4. posts immediately to `/simulation/runs`;
5. starts realtime execution;
6. connects telemetry.

Therefore **Load JSON means Load and Start**, even though the label says only
Load JSON. The UI has no state for "valid scenario loaded but not running."

The `Start survival demo` button introduces another hidden behavior: it fetches
the bundled `web/demo.json` and starts it immediately. This makes Start mean
"discard the current context and use an implicit default scenario," rather than
"start the scenario the operator selected."

The current state object also mixes scenario and run concepts. It stores
`runId`, `runStatus`, and snapshots, but stores neither:

- the loaded scenario document;
- its validation/upload status;
- the loaded scenario ID;
- whether the operator is editing/staging a scenario for the next run.

## 2.3 Pause works in the runner but has poor UI feedback

The backend pause path is implemented correctly at a basic level:

- `POST /pause` changes `RunnerStatus` to `paused`;
- the realtime loop stops advancing ticks while paused;
- telemetry continues publishing snapshots, which should show a stable tick.

The browser makes this appear ineffective because:

- the run label never displays the authoritative status;
- the connection label remains `Telemetry connected`, which looks like the
  simulation is still running;
- the pause action does not refresh the run or snapshot immediately;
- there is no pending/busy state while a control request is in flight;
- snapshots continue arriving at the telemetry rate while paused;
- there is no clear paused visual treatment on the world or clock;
- control failures are added only to the event log and are easy to miss.

The plan should first improve status synchronization and feedback rather than
change deterministic runner semantics.

## 2.4 Stop and restart semantics are unclear

After Stop:

- the socket closes;
- `runId` remains set;
- the stopped snapshot and logs remain;
- there is no explicit distinction between the loaded scenario and the stopped
  run;
- starting again requires loading JSON again or clicking the implicit demo.

The intended behavior should instead be:

- Stop ends only the active run;
- the loaded scenario remains staged;
- Start creates a fresh run from the staged scenario;
- final logs and snapshot remain inspectable until the next run starts or the
  operator explicitly clears them.

## 2.5 Logs lose important information and are difficult to inspect

The current log has several confirmed limitations:

1. `summarizePayload()` intentionally truncates generic JSON at 180 characters.
   The complete payload is not available anywhere in the UI.
2. Preferred summary fields omit `text`, `target_id`, `decision_id`,
   `tool_name`, recipient IDs, provider, and latency.
3. The Dialogue filter includes only `dialogue.*`. New explicit speech is
   emitted as `speech.*`, so normal character speech does not appear under
   Dialogue.
4. Cognition and tool events have no dedicated filters.
5. Rows are not expandable and there is no detail drawer, modal, copy action,
   or pretty-printed JSON view.
6. The log panel is fixed to `18rem` high.
7. The client silently retains only the newest 500 events.
8. Event IDs and correlation IDs are discarded during browser normalization,
   preventing lifecycle tracing.
9. There is no search, auto-scroll control, oldest/newest ordering option, or
   "load older events" action.
10. The entire event log uses `aria-live="polite"`, which is unsuitable for a
    high-frequency stream and can produce poor accessibility behavior.

The backend already exposes paginated events at
`GET /simulation/runs/{run_id}/events`, but the browser does not use it for
initial loading, reconnect backfill, or older-history browsing.

## 2.6 Existing UI tests cannot detect these problems

`tests/test_ui.py` mainly checks that static assets contain expected strings and
that the bundled demo can be run through the API. It does not execute the
browser state machine or verify:

- loading without starting;
- button enablement across states;
- immediate paused status feedback;
- restart after stop;
- speech/dialogue filtering;
- full log expansion.

This plan keeps automation deliberately small, but the current string checks
should not be treated as UI behavioral coverage.

## 3. Target character-agent architecture

## 3.1 Separate the prompt into three layers

Build model messages in this order:

1. **General character-controller system prompt**
2. **Character-specific description**
3. **Dynamic decision context**

The first message is shared by every character and contains only stable
controller rules:

```text
You are the executive controller for one embodied character in a deterministic
simulation. Choose the character's next intentional action through exactly one
available tool. You do not control physical outcomes, change private simulation
state, narrate success, or override survival behavior. Treat the supplied
character description as identity and behavioral guidance, not as permission to
ignore tool or simulation rules.
```

The second message contains the rendered character profile. The third contains
current self-state, perceived facts, knowledge, memories, recent outcomes, and
available target IDs.

This separation allows independent versions:

```text
controller_prompt_version
profile_template_version
profile_content_hash
observation_schema_version
tool_schema_version
```

## 3.2 Use a structured, template-driven character profile

Add a human-oriented `human-v1` profile template. Only `display_name` should be
mandatory. Other fields should be optional so a minimal experimental character
remains easy to define.

Recommended standard sections:

| Section | Initial fields |
|---|---|
| Identity | display name, age, gender, pronouns, occupation/role |
| Appearance | summary, height, build, hair, eyes, clothing/style, distinguishing features |
| Personality | summary, traits, temperament, social style, speech style, strengths, flaws |
| Background | birthplace, residence, education, history |
| Motivations | values, goals, fears, needs, current priorities |
| Capabilities | skills, knowledge areas, limitations |
| Preferences | likes, dislikes, habits, routines |
| Relationships | target character ID, relationship label, sentiment, notes |

The profile model should combine:

- typed standard sections for discoverability and validation;
- ordered custom sections for experiments;
- explicit prompt/UI visibility on custom fields;
- schema and template versions.

Do not use unrestricted Pydantic `extra="allow"` on every object. That would
make typos silently become profile fields. Instead, keep standard sections
strict and provide an explicit extension mechanism:

```json
{
  "custom_sections": [
    {
      "id": "research_experiment",
      "title": "Experiment Variables",
      "prompt_visible": true,
      "ui_visible": true,
      "fields": [
        {"key": "risk_tolerance", "label": "Risk tolerance", "value": "low"},
        {"key": "decision_bias", "label": "Decision bias", "value": "evidence-first"}
      ]
    }
  ]
}
```

Using lists for custom sections and fields preserves human-authored order in
both prompts and the UI.

## 3.3 Make profiles reusable without requiring filesystem access

Browser-uploaded scenarios cannot safely rely on server-local file paths.
Therefore reusable templates and profiles should be scenario data, not implicit
filesystem references.

Recommended scenario shape:

```json
{
  "character_profile_templates": {
    "human-v1": {
      "schema_version": 1,
      "sections": ["identity", "appearance", "personality", "background",
                   "motivations", "capabilities", "preferences", "relationships"]
    }
  },
  "character_profiles": {
    "alex": {
      "template_id": "human-v1",
      "identity": {},
      "appearance": {},
      "personality": {}
    }
  },
  "entities": [
    {
      "id": "agent-001",
      "components": {
        "character_profile": {"profile_ref": "alex"}
      }
    }
  ]
}
```

Support an inline form for small scenarios:

```json
"character_profile": {
  "template_id": "human-v1",
  "identity": {"display_name": "Alex"}
}
```

Resolution rules:

1. load the referenced profile;
2. apply explicit entity-level overrides;
3. validate the resolved profile;
4. freeze it into the ECS component;
5. calculate a stable content hash;
6. render it deterministically.

Reject unknown profile references and duplicate custom section/field IDs.

## 3.4 Render character descriptions as readable tables

Add a `CharacterDescriptionRenderer` application interface and a canonical
deterministic Markdown renderer. Each non-empty section becomes a table:

```markdown
## Identity
| Field | Value |
|---|---|
| Name | Alex Chen |
| Age | 34 |
| Pronouns | he/him |

## Personality
| Field | Value |
|---|---|
| Summary | Quiet, methodical, and considerate |
| Speech style | Concise and precise |
```

Rendering rules:

- preserve template field order;
- omit empty optional fields;
- render lists consistently;
- render relationships using known character display names while retaining
  stable IDs;
- escape table-breaking characters;
- impose section and total-size limits with explicit validation errors;
- never include controller state, private observations, or current physiology in
  the static character description.

The same renderer output can be shown in the operator UI, making it easy to
inspect exactly what identity information the controller receives.

## 3.5 Two initial example characters

These are intentionally simple examples for the first revised scenario.

### Alex Chen

| Field | Value |
|---|---|
| Age | 34 |
| Gender | Man |
| Pronouns | he/him |
| Role | Software research engineer |
| Looks | Medium height, slim build, short black hair, rectangular glasses |
| Clothing | Dark sweater, practical trousers, worn trainers |
| Personality | Methodical, reserved, considerate |
| Speech style | Brief, precise, avoids exaggeration |
| Values | Accuracy, finishing commitments, helping colleagues |
| Strengths | Careful analysis, sustained concentration |
| Flaws | Hesitates when information is incomplete |
| Current goals | Complete the report; check whether Jordan needs help |

### Jordan Lee

| Field | Value |
|---|---|
| Age | 29 |
| Gender | Woman |
| Pronouns | she/her |
| Role | Operations analyst |
| Looks | Average height, athletic build, shoulder-length brown hair |
| Clothing | Blue overshirt, plain T-shirt, dark jeans |
| Personality | Curious, warm, direct |
| Speech style | Friendly, asks concrete follow-up questions |
| Values | Clarity, teamwork, practical progress |
| Strengths | Notices coordination problems, communicates early |
| Flaws | Can become impatient with prolonged indecision |
| Current goals | Coordinate the day's work with Alex; take a break after the analysis |

Their relationship should be defined explicitly by ID in both profiles:

```text
agent-001 <-> agent-002
relationship: trusted colleagues
```

## 3.6 Planned code changes for character profiles

| Area | Change |
|---|---|
| `domain/components/cognition.py` | Replace the flat profile component with an immutable resolved profile and version/hash metadata |
| `application/scenario.py` | Add template/profile definitions, reference resolution, overrides, validation, and legacy migration |
| `application/agents/contracts.py` | Separate static `CharacterDescription` from dynamic `CharacterObservation` |
| `application/agents/prompts.py` | Emit general system prompt, character description message, then dynamic context |
| `application/agents/profile_renderer.py` | Add deterministic section/table rendering |
| `application/agents/context.py` | Stop flattening the five legacy profile fields into dynamic observation |
| telemetry/datasets | Expose profile ID/version/hash and operator-visible resolved profile without mixing it into perception |
| scenarios | Update the real tool-agent scenario with Alex and Jordan examples |

Maintain a compatibility loader for the existing flat profile shape for one
schema version. The compatibility path should normalize old profiles into
`human-v1` and emit a clear deprecation diagnostic.

## 4. Target UI behavior

## 4.1 Introduce an explicit UI state machine

Replace loosely related booleans with these states:

```text
EMPTY
  -> SCENARIO_LOADING
  -> SCENARIO_READY
  -> RUN_STARTING
  -> RUNNING
  -> PAUSING
  -> PAUSED
  -> RESUMING
  -> STOPPING
  -> STOPPED
  -> ERROR
```

Store scenario state separately from run state:

```text
loadedScenario
loadedScenarioName
scenarioId

runId
runStatus
snapshot
lastSequence
controlPending
```

Button enablement must derive from this state machine in one function.

## 4.2 Correct scenario loading and start behavior

**Load JSON**

1. Read and parse the file.
2. Submit it to `POST /simulation/scenarios` for authoritative validation.
3. Store the returned scenario ID and original document.
4. Show `Scenario ready: <name>`.
5. Optionally render a static preview from the scenario.
6. Do not create a run, connect a WebSocket, clear existing run logs, or advance
   simulation time.

**Start**

1. Require a validated loaded scenario.
2. If a prior run is active, require Stop first.
3. Clear run-specific snapshot/log state only after the new run is accepted.
4. Post to `/simulation/runs`.
5. Connect telemetry.

Replace `Start survival demo` with a plain **Start** button. Remove implicit
loading of `demo.json`. If examples remain useful, expose a separate clearly
named **Load example** action that only stages the example and never starts it.

## 4.3 Correct pause, resume, step, and stop feedback

For every control action:

1. enter a visible pending state (`Pausing...`, `Stopping...`);
2. disable conflicting controls;
3. call the API;
4. fetch authoritative run status and a fresh snapshot;
5. update the status badge and controls;
6. surface failures next to the controls as well as in logs.

When paused:

- display a prominent `PAUSED` badge;
- freeze or visually dim the world view;
- keep telemetry connected;
- show that the current tick is stable;
- enable Resume and Single step only;
- after Single step, refresh the snapshot and remain paused.

When stopped:

- retain the final snapshot and logs;
- disconnect telemetry;
- retain the loaded scenario;
- enable Start to create a new run from that scenario;
- disable Pause, Resume, Step, speed, and vital mutation.

The backend runner does not need a semantic rewrite unless manual browser
testing shows ticks still advance after the authoritative status becomes
paused.

## 4.4 Redesign log storage and presentation

Normalize and retain:

```text
event_id
event_type
tick
simulation_time
agent_id
payload
causation_id
correlation_id
source (server or UI)
```

Do not truncate the stored event. Truncation may be used only for the collapsed
row preview.

Add event-aware summaries:

| Family | Collapsed summary |
|---|---|
| `speech.*` | speaker -> intended target / actual recipients: full utterance preview |
| `dialogue.*` | participants and dialogue text |
| `cognition.*` | character, provider/model, status, latency |
| `tool.*` | tool name, target, accepted/rejected reason |
| `plan.*` | action, target, duration, outcome |
| failure events | reason and message |

Change filters to:

- All
- Speech and dialogue
- Cognition and tools
- Plans and actions
- System 1
- Perception
- Failures
- UI/control events

`Speech and dialogue` must include both `speech.*` and legacy `dialogue.*`.

## 4.5 Make long logs inspectable

Add:

- expandable rows;
- a resizable detail drawer or modal;
- pretty-printed full JSON;
- a dedicated selectable text block for speech/dialogue;
- Copy text and Copy event JSON actions;
- text search across event type, agent ID, and payload;
- newest-first/oldest-first ordering;
- auto-scroll toggle;
- adjustable log panel height or full-screen log mode;
- Load older events using the existing paginated events endpoint.

Use `event_id` to deduplicate REST history and WebSocket events. Keep a larger
bounded browser buffer, but do not imply that the buffer is the complete run
history.

Remove `aria-live` from the rapidly changing full log. Use a small separate
status region for important control errors.

## 4.6 Planned code changes for UI behavior

| Area | Change |
|---|---|
| `web/index.html` | Rename Start, add loaded-scenario/status areas, add control error region and log detail UI |
| `web/app.js` | Introduce scenario/run state machine, separate load/start handlers, authoritative control refresh, event deduplication and detail rendering |
| `web/styles.css` | Add state badges, pending/paused treatments, expandable/full-screen log styles, long-text presentation |
| `web/demo.json` | Remove as an implicit start source; retain only as an explicitly loadable example or remove from packaged UI |
| `application/telemetry.py` | Review message categories so speech/dialogue/cognition map cleanly to UI filters |
| `api/simulation.py` | Reuse existing scenario creation and event pagination; add a validation/preview endpoint only if client-side preview proves inadequate |

## 5. Implementation phases

## Phase 1: Character profile foundation

1. Define `human-v1`, reusable profile records, explicit custom sections, and
   reference/override rules.
2. Add deterministic resolution and validation.
3. Add the general controller system prompt.
4. Add the character-description table renderer.
5. Split static profile content from dynamic observation content.
6. Persist prompt/profile version and profile hash in cognition records.
7. Convert the real tool-agent scenario to the two example characters.

**Gate:** Inspecting a model request clearly shows a general controller prompt,
a separate rich description for the correct character, and a dynamic context
that contains no other character's private profile.

## Phase 2: UI scenario/run state separation

1. Add explicit UI states and centralized control enablement.
2. Make Load JSON validate and stage only.
3. Replace Start survival demo with Start.
4. Preserve the loaded scenario after Stop.
5. Add authoritative status display and pending control states.
6. Refresh run state after Pause, Resume, Step, and Stop.

**Gate:** Loading a file never creates a run; Start is impossible without a
valid loaded scenario; Pause visibly freezes the tick; Stop leaves the final run
inspectable and allows a clean restart.

## Phase 3: Log correctness and long-form inspection

1. Preserve full event envelopes and IDs.
2. Correct speech/dialogue/cognition filters.
3. Add event-specific summaries.
4. Add expandable rows and a full detail view.
5. Add search, ordering, auto-scroll, copy, and older-event loading.
6. Remove high-frequency `aria-live` behavior.

**Gate:** A long `speech.delivered` or `dialogue.generated` event can be read and
copied in full, and related cognition/tool/action events can be followed by
correlation ID.

## Phase 4: Documentation and cleanup

1. Document the profile template format and extension rules.
2. Document the exact UI lifecycle.
3. Update the concept guide's character profile and prompt model.
4. Mark the flat profile shape and implicit demo-start behavior as deprecated.
5. Remove compatibility code only after example scenarios are migrated.

## 6. Minimal validation plan

Automated validation should remain deliberately small:

1. One profile-resolution test covering template reference, override, and a
   custom section.
2. One prompt test asserting three-layer separation and deterministic table
   rendering for Alex and Jordan.
3. One API lifecycle test proving scenario creation does not create or advance a
   run, and paused runs do not advance.
4. One lightweight UI state test for Load -> Ready -> Start -> Pause -> Stop
   button transitions.
5. One log formatter test proving full speech text is retained and the combined
   speech/dialogue filter includes `speech.delivered`.

Primary UI acceptance should be manual:

1. Load a JSON file and confirm no run starts.
2. Start, pause for several seconds, single-step, resume, and stop.
3. Start a second run without reloading the scenario.
4. Inspect and copy a long speech/dialogue event.
5. Search and filter cognition, tool, speech, and failure events.

No live-model test is required. Use the standalone counting fake
OpenAI-compatible API only when a controller response is needed during manual
testing.

## 7. Recommended implementation order

Implement character profiles first because the revised example scenario becomes
the primary fixture for manual UI work. Then implement the UI state machine
before changing log presentation. The log redesign should come last because it
depends on stable run lifecycle and event normalization.

Do not combine this work with changes to pathfinding, homeostasis, System 1,
perception disclosure, or provider scheduling.
