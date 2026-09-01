# Scenario and Element Authoring

**Owner:** Portable scenario source schema version 4, reusable element schema,
and structured authoring/staging workflow.

Writable libraries default to `data\scenarios\` and `data\elements\`. Tracked
references live under `examples\scenarios\` and `examples\elements\`.

## Scenario source version 4

Every accepted source has this strict root:

```json
{
  "schema_version": 4,
  "name": "example",
  "seed": 42,
  "dt": 1.0,
  "speed": 1.0,
  "world": {
    "width": 8,
    "height": 5,
    "blocked": [],
    "zones": [],
    "stations": []
  },
  "entities": []
}
```

Root fields are `schema_version`, `name`, `seed`, `dt`, `speed`, optional
`run_id`, `items`, `calendar`, `weather`, `world`, `homeostasis`, `system1`,
`memory`, `perception`, `cognition`, `character_situation_synthesis`, and
`entities`. Extra fields are rejected.

Grid worlds define an inline local map. City worlds define `type: "city"`,
city bounds, city zones, hash-pinned building instances, outdoor places,
transport nodes/edges, geometry, modes, speeds, and vehicles. Runtime
materialization records NPC roles and resolved element graphs separately.

Use the Pydantic model and tracked examples as the exhaustive field reference:

- `examples\scenarios\minimal.json` — smallest grid source;
- `examples\scenarios\environment-demo.json` — calendar/weather/availability;
- `examples\scenarios\sparse-city-car-demo.json` — hierarchical city travel;
- `examples\scenarios\reference-city-restaurants.json` — element references;
- `examples\scenarios\greyford-rivermarket-exchange.json` — staffed exchange;
- `examples\scenarios\provider-character-controller.json` — character slots and controller.

## Entity components

Common components include position or spatial location, movement,
homeostasis, activity, character slot, plan, structured goals, information,
controller, senses, memory, possessions, and conversation. Components are
strict typed records.

Plans use only the action names in [Actions, tools, and events](ACTIONS_AND_EVENTS.md).
`NAVIGATE` requires a target and is the only navigation action. Scenario goals
use stable IDs and one or more closed criteria:

- `event_match`
- `state_comparison`
- `location_match`
- `possession_threshold`
- `action_outcome`
- `interaction_count`
- `simulation_time`

Criteria may provide success or failure evidence. Executable expressions and
textual goal/priority lists are not accepted.

Character slots refer to external schema-version-2 profiles. See
[Character authoring](CHARACTER_PROFILE_GUIDE.md).

## Reusable elements

Element files currently use `schema_version: 1`; scenario sources that reference
them remain version 4. Each element is one strict, hash-protected resource:

| `kind` | Purpose |
| --- | --- |
| `building` | Entrances, portals, and room placements |
| `room` | Local grid and object placements |
| `object` | Affordance or transaction point |
| `npc_role` | Run-scoped service-character briefing, senses, and restricted tools |

Element IDs and filenames must match. IDs use lowercase letters, numbers, dots,
underscores, and hyphens. Building references contain the expected SHA-256
semantic content hash. Resolution validates the entire dependency graph before
staging and rejects missing resources, wrong kinds, changed hashes, duplicate
IDs, invalid placement, and unknown override keys.

Repeated building instances share one source graph. Closed typed overrides may
change selected instance, room, object, or NPC-role values without mutating the
shared element. The source stays reference-only; the prepared run records exact
resolved definitions and hashes.

`examples\elements\standard-restaurant.json` and its referenced room, object,
and NPC-role records are the compact element-authoring example.

## Environment and services

Weekly schedules use weekday names and local `HH:MM` values. Overnight windows
are supported; weekly schedules require a civil calendar. Weather transitions
use simulation-time offsets and can exist without a calendar.

Transaction points have finite holdings, capacity, offers, and either
`AUTOMATED` or `STAFFED` operation. Staffed points reference an NPC role, staff
position, and request timeout. NPCs are run-scoped characters created when
service is first needed, not durable character-library entries.

## Save, stage, and run

These operations are deliberately separate:

1. **Save** validates and writes a hash-protected library file.
2. **Validate and stage** validates the draft, resolves elements and character
   assignments, optionally synthesizes situations, and replaces the prepared
   composition.
3. **Start** creates a runner from the prepared composition.

Saving does not change a staged preview or active run. Staging does not save,
start, or advance time. Unsaved editor drafts may be staged explicitly.

The scenario editor at `/ui/scenarios/` and element editor at `/ui/elements/`
support create/import, edit, duplicate, rename, download, and guarded delete.
Each browser tab has independent server-side draft state. Stale content hashes,
cross-reference failures, duplicate keys, malformed JSON, and invalid IDs are
reported explicitly.
