# Scenario and Element Authoring

**Owner:** Portable scenario source schema version 8, reusable element schema,
and structured authoring/staging workflow.

The writable, version-controlled libraries are `data\scenarios\` and
`data\elements\`.

## Scenario source version 8

Every accepted source has this strict root:

```json
{
  "schema_version": 8,
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
`memory`, `perception`, `cognition`, `engagement`, `text_content`,
`character_situation_synthesis`, and `entities`. Extra fields are rejected.

Grid worlds define an inline local map. City worlds define `type: "city"`,
city bounds, city zones, hash-pinned building instances, outdoor places,
transport nodes/edges, geometry, modes, speeds, and vehicles. Runtime
materialization records NPC roles and resolved element graphs separately.

Use the Pydantic model and tracked catalog as the exhaustive field reference:

- `data\scenarios\baseline.json` — smallest lifecycle source;
- `data\scenarios\grid-navigation.json` — deterministic grid navigation;
- `data\scenarios\needs-and-preemption.json` — physiology and System 1;
- `data\scenarios\weather-and-hours.json` — calendar/weather/availability;
- `data\scenarios\neighborhood-errand.json` — references and services;
- `data\scenarios\community-meetup.json` — physical and social interaction;
- `data\scenarios\open-city-day.json` — multimodal free-form city.

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

Element files use `schema_version: 4`; scenario sources that reference them use
version 8. Each element is one strict, hash-protected resource:

| `kind` | Purpose |
| --- | --- |
| `building` | Entrances, portals, and room placements |
| `room` | Local grid and object placements |
| `object` | Affordance or transaction point |
| `npc_role` | Run-scoped service-character briefing, senses, and restricted tools |

Version-4 objects require an explicit physical footprint, obstruction, and
composable capability record. Version-4 rooms use the fixed metric of 9
microcells per legacy cell. Source `width`, `height`, blocked cells, zones,
ordinary object `position`, portal coordinates, entrance coordinates, and
staff positions remain coarse legacy-cell authoring fields. Materialization
scales the room to microcell dimensions. A physical placement `anchor` and
footprint offsets are room-local microcells; `orientation` is one of
`NORTH`, `EAST`, `SOUTH`, or `WEST`.

Physical object fields are:

| Field | Contract |
| --- | --- |
| `physical.footprint.cells` | Non-empty unique local microcell offsets, rotated around the placement anchor |
| `physical.intrinsics` | Optional positive `mass_kg`, SI `dimensions_cm`, and `TINY|SMALL|MEDIUM|LARGE|BULKY` semantic size independent from footprint |
| `physical.obstruction` | Independent movement, vision, hearing, and smell closed-state behavior |
| `physical.capabilities.slots` | Stable slot IDs, accepted live relation kinds, and positive capacities |
| capability records | Optional support, container, portable/two-handed, readable, content endpoints, consumable, usable, openable, wearable typed effects, and scent-source behavior |
| `physical.initial_open` | Initial open state; requires `openable` and cannot combine with initial locking |
| `physical.owner_id`, `custodian_id` | Descriptive owner and initial physical custodian; they do not create abstract possessions |
| `placement` | Microcell anchor, cardinal orientation, and initial parent relation/slot |

Parent relations are `ON_FLOOR`, `ON_SUPPORT`, `IN_CONTAINER`, `HELD_BY`,
`ATTACHED_TO`, and `OCCUPIES_SLOT`. Slotted relations require a compatible
`slot_id`; parent graphs must be acyclic and placements must remain within the
materialized room. Runtime uses the ECS components and `SpatialIndex`, not the
element hierarchy, as live truth.

Wearables declare compatible closed equipment slots and ordered typed
`ADD`/`MULTIPLY` effects targeting vision, recognition, hearing, or smell
range. `EQUIP`/`UNEQUIP` create and remove the live slotted `ATTACHED_TO`
relation. Effects are deterministic domain state and cannot be inferred from
names or prose. Additive effect values and scent-source emission ranges are
authored in legacy-cell range units and scaled exactly to runtime microcells;
multipliers are dimensionless.

Blocked room cells act as walls for vision, hearing, and smell. Structural
objects independently declare whether each sense passes or is blocked.
Footprint-aware supercover sweeps allow partial visibility when any stable
observer-to-target path is clear. Windows can pass vision while blocking sound
and scent; mirrors block through-vision but do not reflect entities.

Building entrances and portals may set `door_object_id` to a materialized
openable object. The link makes navigation use that object's live open/locked
and effective obstruction state; it does not duplicate door state in the
building definition.

Element IDs and filenames must match. IDs use lowercase letters, numbers, dots,
underscores, and hyphens. Building references contain the expected SHA-256
semantic content hash. Resolution validates the entire dependency graph before
staging and rejects missing resources, wrong kinds, changed hashes, duplicate
IDs, invalid placement, and unknown override keys.

Repeated building instances share one source graph. Closed typed overrides may
change selected instance, room, object, or NPC-role values without mutating the
shared element. The source stays reference-only; the prepared run records exact
resolved definitions and hashes.

The compact hospitality building graph and its referenced room, object, and
NPC-role records in `data\elements\` are the primary element-authoring
reference.

Use [Content migration](CONTENT_MIGRATION.md) for old catalogs. Runtime
libraries deliberately reject legacy versions.

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

The structured forms are generated from reviewed schema descriptors for every
scenario-version-8 and element-version-4 field, including room metric,
footprints, obstruction, capabilities/slots, initial state, physical anchor,
orientation, relation/slot, and entrance/portal door links. Submitted values
remain in the server-side draft when strict validation fails, so malformed or
incomplete work can be corrected without weakening the runtime models.

Authoring previews label physical objects separately from legacy station and
transaction-point views. A physical-only object can remain selectable and
inspectable even when a coarse legacy map cannot place it; placement names do
not grant capabilities or determine behavior.
