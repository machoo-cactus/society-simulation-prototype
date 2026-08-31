# Scenario Library and Structured Editor

The operator Scenario Library is available at `/ui/scenarios/`. It stores plain
`ScenarioDefinition` JSON files in `STAGE0_SCENARIO_DIRECTORY`, which defaults
to `scenarios/`.

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
- city bounds, districts, buildings, entrances, outdoor places, local maps,
  transport nodes and edges, geometry, modes, speeds, and vehicles;
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
template ID allowlists. Planner goals/priorities and initial memory episodes
remain separate typed scenario components.

Lists and mappings use native **Add**, **Remove**, move-up, and move-down
submissions. Optional values and grid/city branches retain their inactive draft
values. Each browser tab receives an opaque server-side draft, so incomplete or
invalid values survive redirect-after-submit without being shared between
tabs.

JSON text areas are limited to intentionally arbitrary values: information
content and metadata, metro-line payloads, entity metadata and unknown
passthrough components.

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
