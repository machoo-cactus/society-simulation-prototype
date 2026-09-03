# Documentation

Active documentation describes only the current runtime. Release and schema
identifiers have one owner: [Current contracts](CURRENT_CONTRACTS.md).

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
| Author current scenarios and reusable elements | [Scenario and element authoring](SCENARIO_EDITOR_GUIDE.md) |
| Author current `human-v1` characters | [Character authoring](CHARACTER_PROFILE_GUIDE.md) |
| Check or upgrade content catalogs | [Content migration](CONTENT_MIGRATION.md) |
| Select valid tools, actions, criteria, and event names | [Actions, tools, and events](ACTIONS_AND_EVENTS.md) |
| Understand character-agent actions, requirements, information retrieval, and outcomes | [Character agent actions and decision flow](CHARACTER_AGENT_ACTIONS.md) |
| Author text artifacts, endpoints, access policies, and in-world mailboxes | [Text content and character read/write actions](TEXT_CONTENT.md) |
| Browse and edit tracked content | [Content catalog](../data/README.md) |

## Researchers

| Task | Owner |
| --- | --- |
| Understand current dataset and SQLite contracts | [Research data](DATA_COLLECTION.md), [Current contracts](CURRENT_CONTRACTS.md) |
| Analyze physical object, relation, and character-body observations | [Research data](DATA_COLLECTION.md) |
| Query, export, aggregate, retain, or delete datasets | [Research data](DATA_COLLECTION.md) |
| Review privacy and reproducibility boundaries | [Research data](DATA_COLLECTION.md) |
| Trace why an architecture changed | [Development history](legacy/DEVELOPMENT_HISTORY.md) |

## Developers

| Task | Owner |
| --- | --- |
| Plan, implement, validate, and hand off agent work | [Development workflow](DEVELOPMENT_WORKFLOW.md) |
| Select quick, startup, source, package, browser, and full-CI test tiers | [Development workflow](DEVELOPMENT_WORKFLOW.md) |
| Check current release, schema, telemetry, vocabulary, and route identifiers | [Current contracts](CURRENT_CONTRACTS.md) |
| Locate authority and dependency boundaries | [Architecture](ARCHITECTURE.md) |
| Change tick, cognition, perception, or action behavior | [Runtime semantics](RUNTIME.md) |
| Maintain canonical public vocabulary | [Actions, tools, and events](ACTIONS_AND_EVENTS.md) |
| Change controller observations, retrieval, tools, intents, or action flow | [Character agent actions and decision flow](CHARACTER_AGENT_ACTIONS.md) |
| Change text artifacts, endpoints, read/write actions, attribution, or messaging | [Text content and character read/write actions](TEXT_CONTENT.md) |
| Change the server-rendered UI or browser tests | [UI architecture and testing](UI_TESTING.md) |
| Change a content schema or migrator | [Content migration](CONTENT_MIGRATION.md) |
| Configure or smoke-test local/rented model services | [LLM operations](LLM_OPERATIONS.md) |
| Check supported platforms, limitations, and unfinished work | [Status and roadmap](STATUS_AND_ROADMAP.md) |
| Follow repository automation rules | [Copilot instructions](../.github/copilot-instructions.md) |

## Historical record

[Development history](legacy/README.md) compresses superseded architectural
eras and links to their implementation commits. Detailed old plans and
requirements remain available through Git history, not the active working tree.
