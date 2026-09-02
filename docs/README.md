# Documentation

Active documentation describes version **0.2.0** only. Historical plans and
requirements are provenance, not operating instructions.

## Operators

| Task | Owner |
| --- | --- |
| Install, launch, and run examples | [Root README](../README.md) |
| Configure catalogs, datasets, CORS, and model providers | [Configuration](CONFIGURATION.md) |
| Use canonical API and browser workflows | [API and UI workflows](API_AND_UI.md) |
| Understand simulation lifecycle and System 1 | [Runtime semantics](RUNTIME.md) |
| Inspect physical authority, interactions, and snapshots | [Architecture](ARCHITECTURE.md), [Runtime semantics](RUNTIME.md), and [API and UI workflows](API_AND_UI.md) |

## Scenario, element, and character authors

| Task | Owner |
| --- | --- |
| Author schema-version-6 scenarios and version-3 reusable elements | [Scenario and element authoring](SCENARIO_EDITOR_GUIDE.md) |
| Author schema-version-2 `human-v1` characters | [Character authoring](CHARACTER_PROFILE_GUIDE.md) |
| Check or upgrade content catalogs | [Content migration](CONTENT_MIGRATION.md) |
| Select valid tools, actions, criteria, and event names | [Actions, tools, and events](ACTIONS_AND_EVENTS.md) |
| Understand character-agent actions, requirements, information retrieval, and outcomes | [Character agent actions and decision flow](CHARACTER_AGENT_ACTIONS.md) |
| Choose and copy a tracked sample | [Example catalog](../examples/README.md) |

## Researchers

| Task | Owner |
| --- | --- |
| Understand dataset v4 and SQLite schema 10 | [Research data](DATA_COLLECTION.md) |
| Analyze physical object, relation, and character-body observations | [Research data](DATA_COLLECTION.md) |
| Query, export, aggregate, retain, or delete datasets | [Research data](DATA_COLLECTION.md) |
| Review privacy and reproducibility boundaries | [Research data](DATA_COLLECTION.md) |
| Trace why an architecture changed | [Development history](legacy/DEVELOPMENT_HISTORY.md) |

## Developers

| Task | Owner |
| --- | --- |
| Locate authority and dependency boundaries | [Architecture](ARCHITECTURE.md) |
| Change tick, cognition, perception, or action behavior | [Runtime semantics](RUNTIME.md) |
| Maintain canonical public vocabulary | [Actions, tools, and events](ACTIONS_AND_EVENTS.md) |
| Change controller observations, retrieval, tools, intents, or action flow | [Character agent actions and decision flow](CHARACTER_AGENT_ACTIONS.md) |
| Change the server-rendered UI or browser tests | [UI architecture and testing](UI_TESTING.md) |
| Change a content schema or migrator | [Content migration](CONTENT_MIGRATION.md) |
| Check supported platforms, limitations, and unfinished work | [Status and roadmap](STATUS_AND_ROADMAP.md) |
| Follow repository automation rules | [Copilot instructions](../.github/copilot-instructions.md) |

## Historical records

[The legacy archive](legacy/README.md) preserves the original requirements,
completed and superseded plans, dated assessments, and non-authoritative prompt
material. Do not copy old route names, schema versions, tools, or compatibility
behavior into current work.
