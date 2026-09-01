# Scenario Library and Structured Editor

The operator Scenario Library is available at `/ui/scenarios/`. It stores plain
schema-version-3 `ScenarioSourceDefinition` JSON files in
`STAGE0_SCENARIO_DIRECTORY`, which defaults to `scenarios/`. Schema-version-2
materialized documents are internal runtime definitions only. Saved-library,
CLI, browser-upload, and simulation-API inputs require schema version 3 and
fail explicitly for version 2 or malformed files.

Reusable world construction records are managed separately at
`/ui/elements/` and stored in `STAGE0_ELEMENT_DIRECTORY`, which defaults to
`elements/`. Element files are strict, hash-protected resources with one of
four kinds: building, room, object, or NPC role. Buildings reference rooms,
rooms place objects, and staffed transaction objects reference NPC roles.

Reference-format city scenarios use schema version 3 and contain city zones
plus hash-pinned building instances. Validation resolves the complete element
dependency graph before staging. Missing resources, wrong kinds, changed
hashes, invalid placements, and unknown override keys fail explicitly. The
scenario remains reference-only on disk; the prepared run records the exact
resolved definitions and hashes as research provenance.

The normal repeated-building case supplies only the instance ID, shared
building reference, city position, and entrance-network bindings. Optional
overrides are closed typed records rather than arbitrary JSON merge patches.
This allows two restaurants to share layout, objects, schedules, and staffing
while changing only a name, holdings, offer, availability rule, or selected
child override.

Compatibility fields used by migrated repository sources can preserve an
existing local-map ID, entrance/object IDs, arbitrary room-local zone shapes,
and the original building, outdoor-place, and NPC-role ordering. New reusable
instances normally omit those fields and use deterministic derived IDs and
containment order.

## Resource ID and scenario name

The **scenario resource ID** is the JSON filename stem. It uses lowercase
letters, numbers, dots, underscores, and hyphens. The **Name** field belongs to
the portable scenario document and may differ from the resource ID. Renaming a
resource changes its filename; editing Name changes the document.

Creates, updates, renames, duplicates, and deletes are protected by the content
hash loaded with the editor. A stale tab cannot overwrite a file changed by
another tab or process.

## Editing

The editor provides structured controls for all typed scenario fields:

- timing, calendar, deterministic weather timelines and effects, homeostasis,
  System 1, memory, perception, and cognition;
- grid dimensions, blocked coordinates, zones, stations, actions, durations,
  capacities, weekly schedules, weather closures, base availability, and
  physiological effects;
- item catalogs, compact NPC roles, possessions, transaction offers, and
  staffed or automated point configuration;
- city bounds, city zones, hash-pinned building instances, typed building,
  room, object, and NPC-role overrides, outdoor places, transport nodes and
  edges, geometry, modes, speeds, and vehicles;
- entities and position, spatial location, movement, homeostasis, activity,
  character slot, plan, planner, information, controller, senses, memory,
  and conversation components.

Weekly opening windows use weekday names and local `HH:MM` times. Overnight
windows are supported when the closing time is earlier than the opening time.
Weather transitions use simulation-time offsets and may be configured without
a civil calendar; any weekly schedule requires a calendar.

Each `character_slot` exposes a role label, temporary briefing, optional
default character, and selection constraints. The initial constraints are
inclusive minimum/maximum age, case-insensitive exact gender allowlists, and
template ID allowlists. For version-2 characters, age is derived from
`identity.birth_date` at the scenario calendar start date; a scenario using age
constraints therefore requires a calendar. Planner goals/priorities and
initial memory episodes remain separate typed scenario components.

The planner editor keeps legacy `daily_goals` and `current_priorities` string
lists for backward compatibility. Its `goals` field accepts the strict
structured-goal JSON array: each goal has a unique stable ID, description,
priority, optional tags and activation/deadline times, `all`/`any` completion
policy, and closed typed criteria. Supported criteria are event match, allowed
state comparison, location match, possession threshold, action outcome,
interaction count, and simulation-time threshold. Extra fields and executable
expressions are rejected. Legacy strings receive deterministic IDs but remain
`unknown` unless replaced by measurable structured goals. See
[Research Data Collection](DATA_COLLECTION.md#structured-goals-and-criteria)
for an inline example and exact semantics.

Scenario-level `npc_roles` are compact reusable service templates. A
transaction point may be `AUTOMATED`, or `STAFFED` with a role reference,
adjacent staff position, and request timeout. The local-map editor outlines
staff positions beside staffed points. NPC instances are not stable scenario
entities or character-library assignments; a run creates them when the first
service request arrives.

Lists and mappings use native **Add**, **Remove**, move-up, and move-down
submissions. Optional values and grid/city branches retain their inactive draft
values. Each browser tab receives an opaque server-side draft, so incomplete or
invalid values survive redirect-after-submit without being shared between
tabs.

JSON text areas are limited to intentionally arbitrary values plus strict
nested records that are validated as a whole, including structured planner
goals. Examples of arbitrary values are information content and metadata,
metro-line payloads, entity metadata, and unknown passthrough components.

## Library operations

- **Import** validates a JSON file up to 5 MB and proposes its filename stem as
  the resource ID.
- **Duplicate** creates a hash-protected copy with a unique resource ID.
- Edit the resource ID and save to **rename**.
- **Download JSON** returns a portable scenario without a library-only ID.
- **Delete** requires confirmation and the original content hash.
- Search matches ID, name, world kind, and schema version.

## Save, stage, and run

These are separate lifecycles:

1. **Save scenario** validates and writes the library file only.
2. **Validate and stage** validates the current draft, resolves optional slot
   defaults, and replaces the Simulation-page prepared composition.
   Unsaved draft changes may be staged and are not written implicitly.
3. **Start run** is a separate Simulation-page action.

On the Simulation page, operators may replace defaults with any eligible
character. Changing an assignment creates a new staged composition without
mutating the saved scenario.

Saving does not change a staged preview or active run. Staging does not start or
advance a run. The Simulation page also provides a saved-scenario selector,
while retaining the bundled-example and uploaded-JSON staging flows.

Validation failures remain in the draft and appear in an alert summary linked
to inline field errors. Cross-field, cross-reference, malformed JSON, unsafe
resource ID, duplicate-key, and stale-write failures are explicit.

The editor and runtime projections expose the containment hierarchy as
`city -> city zone -> building instance -> room -> object`. A building
instance picker shows library IDs and hashes, creates hash-pinned references
and entrance nodes, previews inherited interiors, and resets typed overrides
without changing sibling instances. Saved JSON remains reference-only; room,
object, and NPC-role definitions are materialized only during resolution.
