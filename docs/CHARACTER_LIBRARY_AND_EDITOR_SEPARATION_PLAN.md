# Character Library and Editor Separation Plan

**Status:** Implemented  
**Date:** 2026-08-30  
**Scope:** Character storage, scenario references, scenario preparation, API and
CLI loading, browser character management, examples, datasets, and compatibility  
**Estimated implementation:** 4-6 focused engineering days  
**Expected storage impact:** Negligible; one small JSON file per character plus
resolved character snapshots in run manifests  
**External services or AI credits:** None

## 1. Goal

Separate reusable character definitions from scenarios.

The target design has:

- one `characters\` directory containing one JSON file per character;
- scenarios containing only stable character references, not character
  definitions;
- a dedicated browser page for creating and editing the character library;
- the simulation page selecting library characters for scenario entity slots;
- API and CLI paths resolving and validating characters consistently;
- runs freezing the resolved character data so later file edits cannot change an
  existing staged scenario or active run;
- a compatibility path for current scenarios containing
  `character_profiles`.

This is a configuration and application-boundary change. It must not change
domain simulation order, cognition scheduling, System 1 priority, perception
privacy, or provider behavior.

## 2. Current state and problems

Character definitions currently live in the scenario root:

```json
{
  "character_profiles": {
    "alex-chen": {
      "identity": {"display_name": "Alex Chen"},
      "personality": {"traits": ["methodical"]}
    }
  },
  "entities": [
    {
      "id": "agent-001",
      "components": {
        "character_profile": {"profile_ref": "alex-chen"}
      }
    }
  ]
}
```

This creates several problems:

1. A character must be copied between scenarios.
2. Editing one scenario does not update the same character elsewhere.
3. The browser Character Studio mutates the staged scenario rather than a
   durable character resource.
4. Scenario validation, profile resolution, runner construction, and dataset
   recording all assume an inline profile catalog.
5. The API has no character CRUD surface.
6. The CLI cannot resolve a shared character independently from scenario JSON.
7. The current editor is embedded in the simulation console even though
   character management has a separate lifecycle.
8. Entity assignment currently risks conflating reusable character identity
   with scenario-specific state such as position, physiology, activity, and
   plans.

The earlier design intentionally kept profiles inside browser-uploaded
scenarios to avoid server-local path references. This proposal changes that
decision by making the character directory an explicit server-managed resource
exposed through an API. Browser clients will never submit or access filesystem
paths.

## 3. Design decisions

## 3.1 Character files are authoritative reusable resources

Create a repository-level `characters\` directory:

```text
characters\
  alex-chen.json
  jordan-lee.json
```

Each file contains one complete character definition:

```json
{
  "schema_version": 1,
  "id": "alex-chen",
  "template_id": "human-v1",
  "identity": {
    "display_name": "Alex Chen",
    "age": 34,
    "gender": "Man",
    "pronouns": "he/him",
    "occupation": "Software research engineer"
  },
  "appearance": {},
  "personality": {},
  "background": {},
  "motivations": {},
  "capabilities": {},
  "preferences": {},
  "relationships": [],
  "custom_sections": []
}
```

Rules:

- the filename stem and `id` must match exactly;
- IDs use the existing non-empty string rules, with path separators, `..`, and
  reserved filenames rejected;
- files are UTF-8 JSON and contain exactly one object;
- standard sections remain strict Pydantic models;
- custom data remains limited to ordered `custom_sections`;
- only `identity.display_name` is required;
- files never contain physiology, position, memory, plans, controller state,
  provider credentials, or scenario-specific world state;
- directory listing and loading are sorted by character ID for deterministic
  behavior;
- relationships use stable character IDs, not scenario entity IDs, in newly
  written files.

`human-v1` remains the built-in template owned by the application. Existing
scenario-level `character_profile_templates` are accepted only by the legacy
loader during migration. A future custom-template library can be designed
separately rather than keeping character behavior dependent on a scenario.

## 3.2 Scenarios contain references and scenario-specific state only

The canonical scenario shape becomes:

```json
{
  "name": "office-demo",
  "world": {},
  "entities": [
    {
      "id": "agent-001",
      "components": {
        "character_profile": {
          "character_id": "alex-chen"
        },
        "position": {"x": 1, "y": 1},
        "homeostasis": {
          "satiety": 80,
          "energy": 70,
          "stress": 20
        }
      }
    }
  ]
}
```

The scenario must not contain reusable character fields or per-entity profile
overrides in the canonical format. This keeps the ownership boundary clear:

| Character file | Scenario |
|---|---|
| Identity and display name | Entity slot ID |
| Appearance and personality | Initial location and position |
| Background and motivations | Initial physiology |
| Capabilities and preferences | Initial activity and plan |
| Stable relationships | Controller enablement and tool allowlist |
| Custom character sections | World, timing, memory, and cognition settings |

The ECS component can remain named `CharacterProfileComponent`; it represents
the immutable, resolved profile snapshot attached to a runtime character. The
scenario input reference changes to `character_id`.

## 3.3 Resolve characters before constructing a runner

Do not let ordered domain systems or runner construction perform filesystem
I/O.

Add an application preparation boundary:

```text
Scenario JSON
    +
CharacterLibrary
    |
    v
ScenarioPreparer
    |
    v
PreparedScenario
    |
    v
create_runner()
```

`PreparedScenario` should contain:

- the validated scenario definition;
- an immutable mapping from entity ID to resolved character definition;
- each character's source ID, schema version, template version, and content
  hash;
- a serializable resolved-character snapshot for dataset manifests.

Preparation rules:

1. Validate the scenario's structure without requiring an inline catalog.
2. Collect all referenced character IDs.
3. Load each unique character once through the application interface.
4. Validate every file and reference.
5. Resolve relationship display names from the same library snapshot.
6. Render and hash profiles deterministically.
7. Freeze the prepared result.
8. Only then store the staged scenario or create a runner.

Changing a character file after scenario preparation must not change:

- an already staged scenario ID;
- an active run;
- telemetry for that run;
- recorded prompt/profile hashes;
- the run dataset.

The operator must reload or revalidate the scenario to use updated character
files.

## 3.4 Use an application interface and filesystem adapter

Add a provider-neutral application interface:

```python
class CharacterLibrary(Protocol):
    def list(self) -> tuple[CharacterSummary, ...]: ...
    def get(self, character_id: str) -> CharacterDefinition: ...
    def create(self, character: CharacterDefinition) -> CharacterDefinition: ...
    def update(
        self,
        character_id: str,
        character: CharacterDefinition,
        expected_hash: str,
    ) -> CharacterDefinition: ...
    def rename(
        self,
        character_id: str,
        new_id: str,
        expected_hash: str,
    ) -> CharacterDefinition: ...
    def delete(self, character_id: str, expected_hash: str) -> None: ...
```

Implement it with a filesystem adapter outside the application layer:

```text
application\characters\
  models.py
  library.py
  preparation.py

adapters\characters\
  filesystem.py
```

The filesystem adapter must:

- resolve every file under one configured root;
- prevent path traversal and symlink escape;
- reject non-JSON files as library entries;
- use write-to-temporary-file plus atomic replace;
- fail explicitly on malformed files and ID collisions;
- use content hashes for optimistic concurrency;
- never silently overwrite a newer browser edit;
- avoid process-global mutable caches unless invalidation is deterministic.

Add:

```text
STAGE0_CHARACTER_DIRECTORY=characters
```

The default is `characters\` relative to the process working directory. Tests
must inject a temporary directory. The browser and API deal only in character
IDs and never receive local paths.

## 4. API changes

Add a top-level character router rather than placing library operations under
`/simulation`:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/characters` | List ID, display name, template ID, and content hash |
| `POST` | `/characters` | Create a new character; reject duplicate IDs |
| `GET` | `/characters/{character_id}` | Return the complete character and hash |
| `PUT` | `/characters/{character_id}` | Replace one character using an expected hash |
| `POST` | `/characters/{character_id}/rename` | Atomically rename the file and internal ID |
| `DELETE` | `/characters/{character_id}` | Delete using an expected hash |

Error behavior:

- `400`: invalid or unsafe character ID;
- `404`: missing character;
- `409`: duplicate ID or stale content hash;
- `422`: strict character-schema validation error;
- `500`: explicit filesystem failure without a success-shaped response.

`POST /simulation/scenarios` continues to validate and stage a scenario, but it
must now:

1. validate scenario JSON;
2. resolve all character references through `CharacterLibrary`;
3. store a `PreparedScenario` in `SimulationManager`;
4. return character summaries and preparation warnings with the scenario ID.

Example response:

```json
{
  "scenario_id": "scenario-000001",
  "name": "office-demo",
  "characters": [
    {
      "entity_id": "agent-001",
      "character_id": "alex-chen",
      "display_name": "Alex Chen",
      "content_hash": "..."
    }
  ],
  "warnings": []
}
```

The manager should store `PreparedScenario`, not a live library handle or a raw
scenario that will resolve again at run start.

## 5. Separate character editor page

## 5.1 Page structure

Move character management out of the simulation console.

Add:

```text
src\stage0_sim\web\
  characters.html
  characters-page.js
  character-form.js
```

Responsibilities:

- `characters.html`: dedicated page shell and navigation;
- `characters-page.js`: list, load, create, duplicate, rename, save, delete,
  conflict handling, and API state;
- `character-form.js`: schema-driven form rendering and conversion between form
  state and character JSON;
- `api-client.js`: shared HTTP helper;
- `styles.css`: shared visual system plus page-specific layout.

The current `character-editor.js` mixes form logic with scenario mutation. Split
it rather than adapting it in place:

- retain reusable field metadata, normalization, list parsing, relationship
  JSON, and custom-section JSON in `character-form.js`;
- remove scenario knowledge, entity reference rewrites, and scenario
  revalidation from the editor;
- put character persistence and content-hash conflict handling in
  `characters-page.js`.

The page should support:

- list and search characters;
- create from a blank `human-v1` definition;
- duplicate;
- edit every standard field;
- edit ordered relationships and custom sections;
- rename with conflict detection;
- save with optimistic concurrency;
- delete with explicit confirmation;
- show strict validation errors next to the editor;
- export/download one character JSON;
- import one character JSON through the API;
- clearly distinguish unsaved local edits from persisted library state.

## 5.2 Navigation

Add navigation links to both browser pages:

```text
Simulation | Characters
```

Routes remain static and dependency-free:

- `/ui/` or `/ui/index.html`: simulation console;
- `/ui/characters.html`: character library editor.

The root redirect remains `/ui/`.

## 5.3 Simulation page changes

Remove the embedded Character Studio panel from `index.html` and all character
CRUD behavior from `app.js`.

The simulation page should:

1. load a scenario JSON;
2. fetch `GET /characters`;
3. display one character selector per scenario entity slot;
4. initialize selectors from each slot's `character_id`;
5. preserve valid explicit assignments;
6. show missing references as errors rather than silently selecting another
   character;
7. stage the assigned scenario through `POST /simulation/scenarios`;
8. link to the Characters page for library changes;
9. provide a `Refresh characters` action;
10. refresh summaries when the page regains focus without discarding current
    valid selections.

Starting, pausing, resuming, stepping, stopping, telemetry, logs, overlays, and
vital mutation remain unchanged.

## 6. CLI changes

Update scenario loading so the CLI and API use the same preparation service.

Add:

```powershell
stage0-sim run scenarios\minimal.json --characters-dir characters --ticks 10
```

Resolution precedence:

1. explicit `--characters-dir`;
2. `STAGE0_CHARACTER_DIRECTORY`;
3. default `characters\` under the current working directory.

Do not resolve paths from arbitrary values inside scenario JSON.

Add a migration command:

```powershell
stage0-sim characters extract scenarios\real-llm-tool-agent.json `
  --directory characters
```

Default migration behavior is a dry run. `--write`:

- writes one character file per inline profile;
- refuses conflicting existing files unless content hashes match;
- replaces the scenario's inline catalog with `character_id` references;
- creates a backup or writes to an explicit output path rather than
  destructively overwriting without confirmation;
- reports unresolved legacy relationships that use entity IDs.

## 7. Compatibility and migration

## 7.1 Legacy input support

During migration, continue accepting:

- root `character_profiles`;
- entity `character_profile.profile_ref`;
- inline entity character profiles;
- legacy flat profile fields;
- scenario-level `character_profile_templates`.

Legacy handling must be isolated in a compatibility loader. New application,
API, editor, and example output must write only the canonical external-library
format.

Precedence must not be silent:

1. if a scenario has no inline catalog, resolve from the character library;
2. if a legacy inline profile exists and no external file exists, use the
   inline profile and emit a deprecation warning;
3. if both exist and their canonical content hashes match, use the external
   file and emit a warning;
4. if both exist and differ, reject the scenario and require an explicit
   migration decision.

Do not silently let inline data override the shared library or vice versa.

## 7.2 Repository migration

Migrate current reusable profiles from:

- `scenarios\real-llm-tool-agent.json`;
- `scenarios\sparse-city-car-demo.json`;
- `src\stage0_sim\web\demo.json`;
- tests that construct inline catalogs.

Create at least:

```text
characters\alex-chen.json
characters\jordan-lee.json
```

Update all representative scenarios to contain references only.

Relationships in current examples target entity IDs such as `agent-001`.
Convert them to stable character IDs such as `alex-chen` and `jordan-lee`.
Runtime rendering may map those IDs to assigned entity slots for operator
display, but the character file remains scenario-independent.

## 7.3 Removal sequence

Do not remove inline parsing first. Use this order:

1. introduce models, library, and preparation;
2. add character API and tests;
3. add the separate editor page;
4. update the simulation page to use the API catalog;
5. update CLI resolution;
6. migrate examples and tests;
7. verify datasets contain resolved snapshots;
8. mark inline fields deprecated in documentation;
9. remove embedded Character Studio code;
10. consider removing legacy parsing only in a later schema-breaking release.

## 8. Dataset and reproducibility updates

Datasets are research records and must remain self-contained.

The run manifest should store:

```json
{
  "scenario": {
    "name": "office-demo",
    "entities": [
      {
        "id": "agent-001",
        "components": {
          "character_profile": {"character_id": "alex-chen"}
        }
      }
    ]
  },
  "resolved_characters": {
    "alex-chen": {
      "schema_version": 1,
      "template_id": "human-v1",
      "content_hash": "...",
      "data": {}
    }
  }
}
```

This ensures:

- old runs remain analyzable after character files change;
- prompt/profile hashes can be reproduced;
- datasets do not depend on a local filesystem;
- replay and comparison tooling can identify character-version changes.

Do not record local character-directory paths in datasets.

## 9. Detailed implementation phases

## Phase 1: Extract character models

1. Move character Pydantic definitions from `application\scenario.py` into
   `application\characters\models.py`.
2. Add `schema_version` and stable `id`.
3. Preserve the existing strict section schemas and legacy normalization.
4. Update the profile renderer and imports without changing rendered content.
5. Add focused model tests.

**Exit gate:** Existing profile rendering and prompt tests pass with no
filesystem access.

## Phase 2: Character library and filesystem adapter

1. Define the `CharacterLibrary` protocol and errors.
2. Implement deterministic list/get/create/update/rename/delete.
3. Add content hashing and optimistic concurrency.
4. Add atomic writes and path-safety checks.
5. Add `STAGE0_CHARACTER_DIRECTORY`.
6. Inject the library through FastAPI lifespan and tests.

**Exit gate:** Temporary-directory tests cover CRUD, malformed JSON, duplicate
IDs, stale hashes, rename, traversal attempts, and atomic update failure.

## Phase 3: Scenario preparation

1. Replace inline-catalog validation with syntactic character-reference
   validation.
2. Add `PreparedScenario` and `ScenarioPreparer`.
3. Resolve and freeze characters at staging time.
4. Change `SimulationManager` to store prepared scenarios.
5. Change `create_runner()` to consume resolved character data only.
6. Keep legacy resolution in a compatibility adapter.

**Exit gate:** Modifying or deleting a character file after staging does not
alter a run created from the staged scenario.

## Phase 4: Character API

1. Add the character router and response models.
2. Implement CRUD and rename endpoints.
3. Return strict, useful error responses.
4. Extend scenario staging responses with resolved character summaries and
   warnings.

**Exit gate:** API tests cover the complete lifecycle and stale-edit conflicts.

## Phase 5: Dedicated Characters page

1. Extract the current form into `character-form.js`.
2. Add `characters.html` and `characters-page.js`.
3. Wire list, search, CRUD, import/export, unsaved state, and conflict handling.
4. Add shared navigation.
5. Add responsive styles and accessibility labels.

**Exit gate:** The page can create a character file, reload it, edit every
field, rename it, duplicate it, and delete it through the API.

## Phase 6: Simulation page integration

1. Remove the embedded studio.
2. Fetch the library catalog independently from scenario JSON.
3. Render slot assignment selectors.
4. Preserve explicit references and reject missing assignments.
5. Add refresh/focus behavior.
6. Keep lifecycle controls independent from character editing.

**Exit gate:** Loading a scenario never starts a run; character selection can
be changed and revalidated; all existing run controls and telemetry still work.

## Phase 7: CLI, examples, and migration

1. Add CLI directory resolution.
2. Add the dry-run-first extraction command.
3. Create the repository `characters\` directory and migrate examples.
4. Update README and profile documentation.
5. Update tests and packaged browser demo behavior.

**Exit gate:** All checked-in scenarios contain references only and run through
both CLI and API using the shared character directory.

## Phase 8: Dataset and final compatibility verification

1. Store resolved character snapshots and hashes in run manifests.
2. Verify replay and export do not need the source directory.
3. Exercise legacy inline scenarios and confirm warnings.
4. Run the standard test, lint, type, and wheel checks.

**Exit gate:** New and legacy scenarios are supported, datasets are
self-contained, and the installed wheel serves both browser pages and modules.

## 10. Test plan

## 10.1 Character model tests

- minimal valid character;
- complete `human-v1` character;
- all standard fields round-trip;
- custom-section order and visibility;
- duplicate custom section or field IDs rejected;
- filename/ID mismatch rejected;
- legacy flat fields normalized;
- relationship target uses a character ID.

## 10.2 Filesystem adapter tests

- deterministic sorted listing;
- create, read, update, rename, duplicate, and delete;
- duplicate create returns conflict;
- stale hash returns conflict;
- malformed JSON is surfaced;
- temporary file is not exposed as a character;
- path traversal and symlink escape are rejected;
- failed writes preserve the previous valid file.

## 10.3 Scenario and runner tests

- referenced characters resolve;
- missing references fail staging;
- one library character may be assigned to multiple slots if current reusable
  profile behavior is retained;
- staged scenarios freeze character content;
- runner receives the same description and content hash as before migration;
- System 1, cognition, memory, and perception behavior remain unchanged;
- legacy inline catalog behavior follows explicit precedence rules.

## 10.4 API tests

- character CRUD lifecycle;
- strict validation errors;
- optimistic concurrency conflict;
- scenario staging returns resolved character summaries;
- deleting a source file does not invalidate an already staged scenario;
- a newly staged scenario fails if its reference is now missing.

## 10.5 Browser tests

- both pages and all modules are served from the installed package;
- navigation links are present;
- Characters page loads an empty and populated library;
- every standard field survives save/reload;
- relationships and custom sections survive save/reload;
- unsaved edits are not silently discarded;
- stale edits show a conflict;
- simulation page refreshes character summaries;
- missing assignments block Start;
- startup/reset paths do not rely on accidentally nested helpers;
- every changed JavaScript module passes `node --check`.

## 10.6 CLI and dataset tests

- explicit `--characters-dir`;
- environment directory;
- default directory;
- extraction dry run;
- conflicting extraction;
- migrated scenario execution;
- run manifest contains resolved character data and hashes;
- JSONL export remains readable without the character directory.

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Character files change during a run | Resolve and freeze at scenario staging |
| Two editor tabs overwrite each other | Content-hash optimistic concurrency |
| Browser gains filesystem access | Expose IDs and JSON only through the API |
| Path traversal through character IDs | Strict ID validation and resolved-root checks |
| Old scenarios become unusable | Isolated legacy loader with explicit warnings |
| Inline and external definitions disagree | Reject hash mismatches; no silent precedence |
| Datasets depend on local files | Record complete resolved snapshots |
| Relationships remain scenario-specific | Migrate relationship targets to stable character IDs |
| Character deletion breaks a loaded UI | Existing staged scenario remains frozen; new staging reports missing IDs |
| Custom templates remain scenario-coupled | Keep `human-v1` application-owned; design a separate template library later |
| Main UI silently changes assignments | Preserve explicit IDs and block invalid assignments |

## 12. Acceptance criteria

The separation is complete when:

1. Reusable character definitions exist only as `characters\*.json` in the
   canonical repository examples.
2. Canonical scenarios contain character IDs but no character catalogs or
   reusable character fields.
3. API and CLI use the same character library and preparation logic.
4. Character files are never loaded from ordered domain systems.
5. The Characters page performs durable CRUD independently of scenario
   loading.
6. The simulation page only assigns existing library characters to entity
   slots.
7. Missing, malformed, conflicting, and stale character data fail explicitly.
8. Staged scenarios and active runs are unaffected by later library edits.
9. Run datasets contain complete resolved character snapshots and hashes.
10. Existing lifecycle, telemetry, cognition, perception, memory, dataset, and
    CLI functionality remains compatible.
11. Legacy inline scenarios still load through the compatibility path with
    visible deprecation warnings.
12. Standard pytest, Ruff, mypy, JavaScript syntax, and wheel packaging checks
    pass.

## 13. Recommended review decisions

Before implementation, confirm these proposed decisions:

1. Canonical scenario reference field: `character_id`.
2. Default library location: `characters\` relative to the process working
   directory.
3. New relationships target stable character IDs rather than entity IDs.
4. Character resolution is frozen at scenario staging, not run start.
5. Legacy inline profiles remain read-compatible but are never emitted by new
   tools.
6. Custom profile templates are not included in this migration; `human-v1`
   remains the canonical application-owned template.
