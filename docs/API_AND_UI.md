# API and UI Workflows

**Owner:** Canonical public routes and operator workflows.

The API is mounted in the same FastAPI process as the server-rendered UI.
OpenAPI is available at `/docs`. There are no compatibility aliases or model
provider routes in the simulation process.

## Programmatic workflow

1. Manage reusable source resources through `/characters`, `/scenarios`, and
   `/elements`.
2. `POST /simulation/scenarios` with a schema-version-8 source plus assignment
   choices as applicable. References are validated, resolved, and frozen.
3. `POST /simulation/runs` with the prepared scenario ID.
4. Inspect `/simulation/runs/{run_id}` or `/snapshot`.
5. Control with `/pause`, `/resume`, `/step`, `/speed`, and `/stop`.
6. Read event history, world views, character state, and persisted data.
7. Export research data or use `/simulation/data/*` for cross-run management.

Prepared scenarios and live runners are process-local. Persisted datasets
survive restart but do not restore those objects.

## Physical snapshot contract

Run, room, object, and agent snapshots project current ECS and `SpatialIndex`
state in stable ID order. Physical room payloads state the fixed
`microcells_per_legacy_cell: 9`, microcell dimensions, and compatibility
legacy-cell dimensions. Physical objects expose live microcell pose and
cardinal orientation, local footprint offsets, occupied cells, effective
movement/vision obstruction, open/locked state, capabilities, visible slot
occupancy, visible parent/custody/held relations, interaction anchors, and
index membership. Agents expose their body footprint, posture/support, hands,

Content-capable targets expose endpoint labels, kinds, supported operations,
access modes, and authorized artifact headers/revisions. Ordinary snapshots
and websocket telemetry never include text bodies, deleted revisions, mailbox
contents, or the authoritative actor behind reader-visible anonymous text. Agents expose their body footprint, posture/support, hands,
held objects, and current interaction request/execution; operator projections
also expose compact microcell movement/path state.

Selected-agent operator snapshots also expose privacy-safe engagement state:
one pending, compiled, or active engagement; action/plan/decision/tool lineage;
reference and participant IDs; current group; group progress/status; grounded
capability evidence; and up to five recent terminal outcomes. Raw intent,
private controller reason, compiler scene/summary/response, rejected proposal
details, and private normalized arguments are not rendered.

The ordinary API projection omits descriptive private ownership and
controller-private reasons. It excludes an object, relation, or slot occupant
hidden inside a closed opaque container. The operator UI calls the same
builders under explicit operator conventions and may inspect authoritative
hidden physical state; that does not make the state character-visible.
`CityWorld` and element hierarchy payloads remain construction/display
metadata, not mutable physical authority.

## Source-library routes

The same shape applies to characters, scenarios, and elements:

| Method | Route | Purpose |
| --- | --- | --- |
| `GET`, `POST` | `/characters`, `/scenarios`, `/elements` | List or create |
| `GET`, `PUT`, `DELETE` | `/{library}/{id}` | Read, replace, or delete with concurrency protection |
| `POST` | `/{library}/{id}/rename` | Atomic rename |

## Simulation routes

| Area | Routes |
| --- | --- |
| Prepare/start | `POST /simulation/scenarios`, `POST /simulation/runs` |
| Run state | `GET /simulation/runs/{run_id}`, `GET /simulation/runs/{run_id}/snapshot` |
| Lifecycle | `POST /simulation/runs/{run_id}/pause`, `/resume`, `/step`, `/speed`, `/stop` |
| Character inspection | `GET /simulation/runs/{run_id}/agents/{agent_id}`, `GET /simulation/runs/{run_id}/agents/{agent_id}/spatial-context`, `PATCH /simulation/runs/{run_id}/agents/{agent_id}/vitals` |
| Event history | `GET /simulation/runs/{run_id}/events` |
| World inspection | `GET /simulation/runs/{run_id}/world/city`, `/city-zones/{id}`, `/buildings/{id}`, `/rooms/{id}`, `/objects/{id}`, `/neighborhoods/{id}` |
| External telemetry | `WS /simulation/runs/{run_id}/stream` |
| Engagement research | `GET /simulation/runs/{run_id}/data/engagements`, `/engagement-groups`, `/engagement-invocations` |
| Other research/export | `/data*` and `/exports/*` as documented in [Research data](DATA_COLLECTION.md) |

Abbreviated suffixes in a table row retain the complete prefix shown by that
row.

## Persisted-data management routes

| Method | Route |
| --- | --- |
| `GET` | `/simulation/data/runs` |
| `POST` | `/simulation/data/aggregate` |
| `GET`, `POST` | `/simulation/data/aggregate.json` |
| `GET`, `POST` | `/simulation/data/aggregate.csv` |
| `POST` | `/simulation/data/deletion-preview` |
| `POST` | `/simulation/data/delete` |

## Operator UI

| Route | Workflow owner |
| --- | --- |
| `/ui/` | Stage a scenario, assign characters, run/control, inspect world and events |
| `/ui/characters/` | Character-library CRUD and import/download |
| `/ui/scenarios/` | Scenario-library CRUD, structured drafts, validation, staging |
| `/ui/elements/` | Element-library CRUD and dependency-aware delete |
| `/ui/datasets/{run_id}/` | Per-run research explorer and filtered exports |
| `/ui/data/` | Cross-run catalog, selection, aggregation, export, deletion |

The simulation page can stage the packaged example, upload JSON, or stage a
saved library scenario. Staging never starts a run. Start, pause, resume,
single-step, speed, vital mutation, stop, map/view changes, and event clearing
remain distinct labeled forms.

The operator event filter has an `Engagement` family for all `engagement.*`
events. Event rows and the selected-character engagement inspector show only
privacy-safe projected fields and committed evidence, including actual
auditory recipients. Compiler choice is not displayed as successful world
state. The inspector distinguishes pending compilation, compiled work, active
group progress, completed/failed/cancelled groups, partial terminal outcomes,
and recent history without relying on color.

The authoring pages use content hashes for optimistic concurrency. Scenario and
element drafts are server-side and tab-scoped; malformed or incomplete values
survive validation redirects without becoming global state.

The descriptor-driven scenario-version-8 and element-version-4 forms cover
physical footprints, semantic intrinsics, movement and per-sense obstruction,
wearable/scent capabilities and slots, initial
open/locked/ownership/custody state, room metric, placement
anchor/orientation/relation/slot, and entrance/portal door links. Physical
objects are presented separately from compatibility station and transaction
views. Version 7 also exposes separate engagement compiler settings and
top-level engagement validation/effect bands. Validating or staging these
settings does not start or advance a run.

## UI architecture

Python routes own orchestration and Jinja templates under
`src\stage0_sim\web\templates\` render authoritative HTML/SVG. Public assets
under `src\stage0_sim\web\static\` are mounted at `/ui/assets`; templates and
packaged example resources are not exposed by the static mount.

`enhancements.js` may submit native forms through `fetch`, replace named HTML
regions, preserve browser interaction state, use clipboard APIs, and provide
pointer map controls. It must not own lifecycle state, interpret events,
calculate outcomes, advance time, or maintain telemetry state. All workflows
retain a no-JavaScript form/link fallback.

At room scale the operator SVG renders authoritative physical footprints,
5×5 character bodies, posture/held state, door state and links, movement paths,
and selected approach/occupancy anchors. Legacy-cell guides remain the coarse
overview; the microcell pattern appears only at close zoom. Pattern fills and
compact merged footprint rectangles keep markup bounded by rendered content,
not by the number of room microcells. Labels and classes expose non-color
state, and ordinary selection forms/links remain the no-JavaScript path.

See [UI architecture and testing](UI_TESTING.md).
