# UI Architecture Reliability and Readability Report

**Status:** Superseded by the server-rendered UI rewrite
**Date:** 2026-08-30
**Scope:** Browser state management, simulation controls, telemetry delivery,
event recovery, dialogue presentation, and visual sensing indicators

> Historical design record. The native ES-module architecture described below
> was removed on 2026-08-31. The implemented UI now uses Python routes,
> server-rendered accessible HTML/SVG, direct form controls, and Playwright
> browser verification. See `docs/UI_TESTING.md` and the README for current
> guidance.

## 1. Executive assessment

The current UI is adequate as a small debugging prototype, but it is not a
reliable foundation for the planned increase in displayed state.

There are two different classes of problem:

1. **Immediate UI state bugs.** Control enablement is derived from fragile
   string conventions in one large mutable JavaScript file.
2. **Delivery and scaling limits.** Current snapshots provide eventual world
   state, but event, dialogue, and perception history can be lost during
   reconnects. The server does not provide an explicit resynchronization
   protocol.

A staged rework is recommended. The first phase should fix the current control
bug without waiting for the larger architecture. The following phases should
separate state, transport, and rendering before adding richer visualizations.

## 1.1 Implementation result

The initial rework described in this document has been completed:

- explicit UI states and control selectors replaced suffix-based busy detection;
- telemetry protocol v2 separates durable message sequence, domain-event
  offset, and snapshot revision;
- periodic snapshots are held as one latest value instead of displacing event
  history;
- WebSocket bootstrap no longer mutates the shared stream;
- expired cursors produce `resync_required`;
- the client recovers a current snapshot plus missed domain events before
  reconnecting;
- immutable world/profile bootstrap data is separated from runtime snapshots;
- browser API, protocol, and state logic are native ES modules;
- current vision, hearing pulses, speech bubbles, selected-character
  perception, transcripts, and overlay controls are rendered.

The remaining evolution is incremental refinement rather than a prerequisite
architecture replacement.

## 2. Confirmed control-button defect

All controls become disabled after Start because `web/app.js::updateControls()`
contains:

```javascript
const busy = /ING$/.test(state.uiState);
```

The stable state is named `RUNNING`, which also ends in `ING`. Consequently:

```text
state.uiState = "RUNNING"
busy = true
pause disabled
stop disabled
speed disabled
vital mutation disabled
```

This is why the run starts successfully while every other control is greyed
out. The API and runner are not the cause of this specific failure.

### Immediate correction

Replace suffix inference with an explicit set:

```javascript
const pendingStates = new Set([
  "SCENARIO_LOADING",
  "RUN_STARTING",
  "PAUSING",
  "RESUMING",
  "STEPPING",
  "STOPPING",
  "SPEED_CHANGING",
]);

const busy = pendingStates.has(state.uiState);
```

Prefer an enum-like constant object and an explicit transition table. Stable and
pending states must never be distinguished by spelling patterns.

## 3. Current UI architecture

The browser is a dependency-free application composed of:

- `index.html` for the complete operator layout;
- `styles.css` for all presentation;
- one large `app.js` containing:
  - API access;
  - WebSocket lifecycle;
  - scenario staging;
  - character assignment;
  - run state;
  - control commands;
  - protocol normalization;
  - canvas drawing;
  - inspector rendering;
  - event storage and filtering;
  - detail-dialog behavior.

The backend consists of:

- REST control and history endpoints in `api/simulation.py`;
- process-local run ownership in `application/manager.py`;
- a `TelemetryBroker` with a shared monotonically increasing sequence and an
  in-memory message deque;
- periodic full snapshots at approximately 10 Hz;
- domain events forwarded as telemetry messages;
- SQLite/JSONL collection independent from the WebSocket.

This arrangement has no frontend build complexity, which is useful. The problem
is not the absence of a JavaScript framework. The problem is that transport,
state transitions, and rendering are not separated.

## 4. Reliability analysis

## 4.1 What is currently delivered reliably enough

While the WebSocket is continuously connected:

- domain events are appended to the telemetry broker;
- periodic world snapshots are appended at 10 Hz;
- the client polls the broker every 50 ms through the WebSocket handler;
- messages are ordered by one telemetry sequence;
- the client rejects duplicate or older sequences.

For the current process lifetime, a fresh REST snapshot can recover:

- current run status and speed;
- current tick and simulation time;
- world geometry and station availability;
- character positions, paths, plans, physiology, profiles, and current System 1
  state;
- limited perception summary data.

This is sufficient for eventual recovery of the latest materialized world
state.

## 4.2 Event and dialogue delivery is not reliable across reconnects

The reconnect path calls `refreshSnapshot()` before reconnecting:

```javascript
await refreshSnapshot();
connectStream();
```

`refreshSnapshot()` advances `state.lastSequence` to the broker's latest
sequence. The following WebSocket connection requests:

```text
after_sequence=<latest snapshot sequence>
```

Any events emitted between the disconnect and the snapshot refresh are skipped
permanently by the live log. The current gap handler also calls only
`refreshSnapshot()`; it does not backfill domain events.

Therefore:

- world state may recover;
- missed speech, dialogue, tool calls, failures, or perceptions do not
  automatically recover;
- advancing the telemetry cursor during snapshot recovery converts a temporary
  disconnect into permanent event loss.

The manual **Load older** action can retrieve events from offset zero, but it is
not integrated into reconnect or gap recovery.

## 4.3 The telemetry history capacity is consumed mostly by snapshots

`TelemetryBroker` stores at most 10,000 messages. At 10 snapshots per second,
snapshots alone consume the entire buffer in roughly 16 minutes and 40 seconds.
Domain events shorten that window further.

The buffer is shared by:

- state snapshots;
- run status messages;
- all domain events.

Snapshots are redundant because only the latest snapshot is useful for
recovery. Storing every snapshot displaces valuable ordered events.

## 4.4 A client connection mutates the shared stream

Each WebSocket connection calls:

```python
managed.broker.publish_status()
managed.broker.publish_snapshot()
```

These messages are appended to the shared broker and increment its global
sequence for every subscriber. Connecting a second operator changes the stream
seen by the first operator.

Subscriber bootstrap should be sent directly to that socket. Merely observing a
run should not mutate the shared telemetry sequence.

## 4.5 Gap detection cannot determine recoverability

The client detects a numeric gap but the server never communicates:

- the oldest sequence still retained;
- whether the requested cursor is recoverable;
- the domain-event offset corresponding to a telemetry message;
- whether a forced snapshot replaces one or many lost event messages.

`messages_after()` simply returns anything still present in the deque. A client
with an expired cursor receives later messages without an explicit
`resync_required` response.

## 4.6 The event-history cursor is disconnected from the live cursor

REST event history uses a zero-based domain event offset. WebSocket recovery
uses a telemetry message sequence. These are different sequences because
telemetry also contains frequent snapshots and status messages.

The UI cannot currently answer:

```text
Which domain events have I definitely processed?
```

It tracks only the telemetry sequence. Reliable recovery requires a separate
domain-event cursor.

## 4.7 Increasing snapshot size will amplify the problem

Snapshots currently duplicate relatively static and large data on every 10 Hz
publication:

- complete world geometry;
- zones and station definitions;
- full character profile descriptions;
- plans and paths;
- conversation summaries;
- perception summaries.

As character profiles, knowledge, dialogue, relationships, and sensory state
grow, sending a full omniscient snapshot ten times per second will waste CPU,
memory, bandwidth, and browser parsing time.

The current model does not distinguish:

- immutable run metadata;
- infrequently changing state;
- high-frequency position/vital deltas;
- transient visual effects;
- durable events.

## 5. State-management analysis

`app.js` uses one mutable global object and direct DOM writes from asynchronous
functions. State is modified by:

- file loading;
- character assignment validation;
- REST controls;
- snapshot refreshes;
- WebSocket status messages;
- WebSocket snapshots;
- reconnect timers;
- local UI errors.

There is no reducer or enforced transition graph. This permits invalid
combinations such as:

```text
uiState = RUNNING
runStatus = stopped
runId = null
intentionalClose = false
```

The code partially protects scenario validation with `scenarioRevision`, but
run controls, socket generations, and snapshot responses have no equivalent
stale-response protection.

The current tests serve the static files and search for strings. They cannot
detect a semantic bug such as `/ING$/` matching `RUNNING`.

## 6. Current sensing and dialogue data available to the UI

## 6.1 Vision

The perception system maintains `PerceptionComponent.visible_now`, and
telemetry includes the visible entity IDs for every character. This can support
a current **seeing** indicator without exposing private information.

The browser currently discards the `perception` object during agent
normalization, so this information is not rendered.

## 6.2 Hearing

Hearing is event-based:

- `perception.delivered` includes observer ID, modality, fact type, and subject;
- `speech.delivered` includes the speaker, text, intended target, actual
  recipients, and channel.

There is no durable `hearing_now` state because hearing is instantaneous. A UI
indicator should therefore be a short-lived pulse derived from auditory events,
not a permanent snapshot flag.

## 6.3 Speech and dialogue

Explicit speech has enough structured information for readable world bubbles:

```text
speaker ID
literal text
intended target
actual recipient IDs
channel
simulation tick/time
```

Legacy `dialogue.generated` also contains text and a target, but it represents
the transitional planner/dialogue path and should be displayed with a legacy
marker.

The current `conversation.latest_turn` snapshot lacks speaker attribution and
is not sufficient by itself for a transcript.

## 6.4 Perception details

The snapshot exposes only:

- inbox count;
- visible entity IDs;
- known-character count.

The inbox may be consumed and cleared when controller context is built. It is
not a stable operator view of what was recently perceived. Readable perception
history must come from events or from a dedicated operator projection.

## 7. Recommended target architecture

Retain dependency-free browser modules, but divide responsibilities.

```text
web/
  app.js                  composition only
  state.js                state model, actions, reducer, selectors
  api.js                  REST client
  telemetry-client.js     WebSocket and recovery protocol
  protocol.js             versioned payload validation/normalization
  scene-model.js          snapshot + deltas -> renderable world
  world-renderer.js       canvas and overlays
  controls-view.js        lifecycle controls
  inspector-view.js       selected-character details
  log-store.js            event cursor, dedupe, paging
  log-view.js             filters and detail dialog
```

This does not require React, a package manager, or a bundler. Native ES modules
are sufficient for the current project.

## 7.1 Explicit state reducer

Represent state transitions as actions:

```text
SCENARIO_LOAD_REQUESTED
SCENARIO_VALIDATED
RUN_START_REQUESTED
RUN_STARTED
RUN_STATUS_RECEIVED
CONTROL_REQUESTED
CONTROL_SUCCEEDED
CONTROL_FAILED
SNAPSHOT_RECEIVED
TELEMETRY_GAP_DETECTED
RESYNC_COMPLETED
```

Control enablement should be pure selectors over state, not scattered DOM
logic.

Each asynchronous operation should carry a generation/request ID. Responses
from a previous scenario, run, or socket generation must be ignored.

## 7.2 Separate immutable bootstrap, current state, deltas, and events

Use four data categories:

1. **Run bootstrap:** world geometry, zones, stations, resolved character
   profiles, schema versions.
2. **Current snapshot:** tick, run status, positions, vitals, active plans,
   current visibility.
3. **Transient deltas/effects:** movement animation, speech bubbles, auditory
   pulses, perception pulses.
4. **Durable domain events:** complete ordered event history for logs,
   transcripts, debugging, and recovery.

Static run bootstrap data should not be resent at 10 Hz.

## 7.3 Version the UI protocol

Every bootstrap, snapshot, delta, and event envelope should include a protocol
version:

```json
{
  "schema_version": "stage0.telemetry.v2",
  "message_type": "event",
  "telemetry_sequence": 42,
  "domain_event_offset": 17,
  "payload": {}
}
```

The UI should reject unsupported major versions and show a clear compatibility
error.

## 7.4 Introduce an explicit resynchronization protocol

On WebSocket connection, the client sends or queries:

```text
last telemetry sequence
last processed domain-event offset
run generation
```

The server responds with one of:

```text
resume
  requested telemetry sequence is still available

resync_required
  telemetry cursor expired; includes latest snapshot sequence and current
  domain-event total
```

For `resync_required`, the client:

1. fetches or receives the latest current snapshot;
2. fetches domain events after its last processed event offset;
3. deduplicates by event ID;
4. updates both cursors only after applying the recovery batch;
5. resumes live messages.

Never advance the live cursor merely because a REST snapshot was fetched.

## 7.5 Keep only the latest periodic snapshot

Change `TelemetryBroker` to maintain:

- the latest snapshot separately;
- a bounded deque of status and delta/event messages;
- `oldest_sequence` and `latest_sequence`;
- a domain-event offset for each forwarded domain event.

Do not append every 10 Hz full snapshot to the recovery deque. Prefer:

- direct latest-snapshot replacement;
- lower-rate full snapshots;
- small high-frequency deltas if smooth animation is needed.

## 7.6 Make subscriber bootstrap side-effect free

Opening a WebSocket should send a direct `hello/bootstrap` message to that
client. It must not call `publish_status()` or `publish_snapshot()` on the
shared broker.

## 7.7 Provide a UI-specific projection

Do not keep extending the raw omniscient snapshot indefinitely. Add a versioned
operator projection with stable fields specifically needed by the UI:

```text
run
world_static
characters
current_actions
current_perception
recent_utterances
diagnostics
```

The projection remains operator-only and must not be reused as character
perception.

## 8. Readability design for the world display

## 8.1 Character labels

Draw the character display name near each marker. Use the entity ID only in the
inspector or when names collide.

Provide a compact status row near the character:

```text
Alex
👁 1  👂 0  💬
```

Use accessible text equivalents in the inspector and canvas ARIA summary.

## 8.2 Vision indicator

For each character:

- show `👁` when `visible_now` is non-empty;
- optionally show the number of currently visible characters;
- on `entity_seen`, flash a small eye pulse for approximately 1–2 wall-clock
  seconds;
- on `entity_lost`, briefly fade the indicator;
- when an operator selects a character, optionally outline characters in that
  observer's `visible_now` set.

Do not draw private intended destinations or controller reasons as perceived
information.

## 8.3 Hearing indicator

On an auditory `perception.delivered` or when the character is in
`speech.delivered.recipient_ids`:

- pulse `👂` above the listener;
- retain the pulse for a short wall-clock TTL;
- optionally draw a subtle ring around the speaker with range omitted unless a
  dedicated debug overlay is enabled.

Hearing indicators represent receipt of a sound at a tick, not an ongoing
hearing mode.

## 8.4 Speech bubbles

On `speech.started` or `speech.delivered`:

- anchor a speech bubble to the speaker;
- show exact speech text, wrapped to a bounded width;
- keep it visible for a configurable wall-clock duration based on text length;
- allow clicking the bubble to open the complete event;
- display `…` while speech is pending only if the domain has a real pending
  state;
- distinguish normal speech and whisper channels visually.

For long text, show a short bubble preview and put the complete utterance in the
transcript/detail panel.

## 8.5 Selected-character perception view

Add an inspector section:

```text
Seeing now
  Jordan

Recently heard
  Jordan: "..."

Recent observations
  Jordan entered the Office.
  Jordan started working.
```

Populate this from the selected character's operator-safe perception projection
and recent `perception.delivered` events.

## 8.6 Conversation transcript

Add a separate transcript view rather than relying only on the general event
log. Group entries by conversation participants/correlation ID:

```text
t=42  Alex -> Jordan
       "Can you review the figures?"

t=43  Heard by: Jordan, Casey
```

Keep legacy `dialogue.generated` entries visible but marked `legacy dialogue`.

## 8.7 Overlay controls

Add toggles:

- Names
- Paths
- Speech bubbles
- Vision indicators
- Hearing indicators
- Selected observer's visibility
- Debug destinations

Default to readable behavior evidence. Keep omniscient debug overlays explicit.

## 9. Implementation plan

## Phase 0: Immediate control hotfix

1. Replace `/ING$/` busy detection with an explicit pending-state set.
2. Define UI state constants.
3. Add a small control-selector test covering:
   - `RUNNING` enables Pause and Stop;
   - `PAUSING` disables controls;
   - `PAUSED` enables Resume and Step;
   - `STOPPED` enables Start for the loaded scenario.

**Gate:** A newly started scenario can immediately be paused or stopped.

## Phase 1: Extract browser state and views

1. Split `app.js` into native ES modules.
2. Introduce a reducer and pure selectors.
3. Add operation and socket generation IDs.
4. Keep all DOM mutation inside view modules.
5. Preserve the current dependency-free packaging model.

**Gate:** Lifecycle state can be tested without a browser DOM, and stale REST or
WebSocket responses cannot modify a newer run.

## Phase 2: Telemetry protocol v2

1. Define typed/versioned bootstrap, snapshot, event, delta, and
   `resync_required` messages.
2. Separate the latest snapshot from the broker history deque.
3. Stop storing every periodic snapshot in the replay buffer.
4. Add `oldest_sequence`, `latest_sequence`, and domain-event offsets.
5. Make WebSocket bootstrap side-effect free.
6. Add an API to fetch domain events after an offset, or extend the current
   endpoint with an explicit cursor.

**Gate:** Disconnecting and reconnecting cannot lose speech, dialogue,
perception, tool, or failure events while the run remains available.

## Phase 3: Client recovery and log store

1. Track telemetry and domain-event cursors separately.
2. On gaps, pause live application, fetch the current snapshot and missed
   events, then resume.
3. Deduplicate by event ID.
4. Load initial history automatically for an existing run.
5. Move event paging/deduplication out of the log DOM renderer.

**Gate:** A forced disconnect followed by activity and dialogue yields the same
event log after reconnect as a continuously connected client.

## Phase 4: UI-specific operator projection

1. Split immutable world/profile bootstrap from current character state.
2. Add current perception fields:
   - visible character IDs;
   - recent auditory receipt metadata;
   - recent observation summaries.
3. Add recent utterance/transcript projection or derive it from durable speech
   events.
4. Reduce periodic payload size.

**Gate:** Adding profile, perception, and transcript detail does not require
resending the complete world and character descriptions at 10 Hz.

## Phase 5: Dialogue and sensing visualization

1. Add character names and compact status indicators.
2. Add vision and hearing pulses.
3. Add speech bubbles with TTL and click-through detail.
4. Add the selected-character perception section.
5. Add the conversation transcript.
6. Add overlay toggles and preserve explicit debug-only views.

**Gate:** An operator can see who spoke, who heard it, who currently sees whom,
and inspect the exact underlying events without confusing private intent with
perceived evidence.

## Phase 6: Performance and multi-client hardening

1. Coalesce position/vital updates when the browser falls behind.
2. Avoid scanning the full broker deque every 50 ms per client.
3. Bound transient overlay state independently from durable logs.
4. Confirm multiple clients do not modify the shared sequence.
5. Measure snapshot/delta sizes with representative larger scenarios.

**Gate:** Multiple observers and longer runs do not cause unbounded browser
memory, repeated full-state parsing, or subscriber-induced telemetry messages.

## 10. Minimal validation plan

Keep automated coverage focused:

1. Reducer/control-selector tests for lifecycle states, including the confirmed
   `RUNNING` regression.
2. Broker tests for oldest/latest sequence and side-effect-free subscriber
   bootstrap.
3. One reconnect test proving event backfill across a forced telemetry gap.
4. One UI projection test for current visibility and auditory event data.
5. One renderer test for speech bubble/transcript formatting.

Manual browser acceptance:

1. Start a scenario and immediately Pause and Stop.
2. Disconnect the browser, allow characters to move/speak, reconnect, and
   confirm no events are missing.
3. Select each character and verify current visual targets.
4. Trigger speech with intended and unintended listeners and verify hearing
   pulses and transcript recipients.
5. Run a larger scenario long enough to inspect browser responsiveness and log
   memory behavior.

No live LLM is required. Use scripted/replay control or the standalone fake
OpenAI-compatible API for dialogue and tool events.

## 11. Recommended decision

Apply Phase 0 immediately. Complete Phases 1–4 before adding many new panels or
high-volume visual data. Implement dialogue and sensing overlays only after the
recovery protocol and UI-specific projection exist; otherwise the new display
will make the current delivery and state-management weaknesses harder to fix.
