# Current Contracts

**Owner:** Volatile release, schema, dataset, telemetry, vocabulary, and route
identifiers. Code constants remain authoritative; documentation tests keep this
page synchronized with them.

## Version inventory

| Contract | Current value |
| --- | --- |
| Package release | `0.3.0` |
| Scenario source | `9` |
| Character source | `2` with template `human-v1` |
| Reusable element source | `5` |
| Dataset | `stage0.dataset.v6` |
| SQLite | schema `13`, fresh-only; default `stage0-v13.sqlite3` |
| Telemetry | `stage0.telemetry.v5` |
| Checkpoint | `stage0.checkpoint.v1` |
| Runtime checkpoint compatibility | `stage0.runtime-checkpoint.v1` |

Runtime libraries accept only current character, element, and scenario sources.
The offline migration service retains tested adjacent transforms for authored
content. Existing SQLite databases are not migrated.

The default SQLite filename includes the schema version. After a schema bump,
the application creates the new default beside earlier databases rather than
overwriting, deleting, or attempting to migrate research records. An explicitly
configured `STAGE0_DATASET_DATABASE` remains exact and fails if its schema is
not current.

## Runtime boundaries

- Character controllers use typed tools behind one global cognition barrier.
- `engage` is the specialized-first fallback and uses a separately budgeted
  `engagement_compilation` model operation.
- Navigation uses `navigate_to` and the domain action `NAVIGATE`.
- Canonical action lifecycle events are `action.queued`, `action.started`,
  `action.completed`, `action.failed`, and `action.cancelled`.
- Physical interactions and engagement events use the closed vocabularies in
  [Actions, tools, and events](ACTIONS_AND_EVENTS.md).

## Canonical route families

- Persisted-data management: `/simulation/data/*`
- Per-run data: `/simulation/runs/{run_id}/data/*`
- Per-run exports: `/simulation/runs/{run_id}/exports/*`

See [API and UI workflows](API_AND_UI.md) for complete route and browser
behavior. Do not add aliases for removed route families during breaking
changes.

## Update rule

When a value changes:

1. update its code constant and strict models;
2. add the required adjacent authored-content migrator when applicable;
3. update this page;
4. update the owning specialist document only if behavior changed;
5. run `python -m pytest tests\integration\catalogs\test_documentation.py`.

The root README, status, Copilot instructions, and specialist documents should
link here rather than duplicate this inventory.
