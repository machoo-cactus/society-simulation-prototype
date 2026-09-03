# Tracked content catalog

`characters`, `elements`, and `scenarios` are the writable,
version-controlled source libraries used by the CLI, API, and operator UI.
Generated run data belongs under `runs`. Pre-overhaul and migration backups
belong under `backups` or `migration-backups`; all three locations are ignored
by Git.

The installed `stage0-sim run demo` command uses separate packaged resources,
so it remains runnable without this checkout.

## Scenarios

| File | Scope and purpose |
| --- | --- |
| `scenarios/baseline.json` | Small lifecycle, assignment, staging, and deterministic tick baseline |
| `scenarios/grid-navigation.json` | Small deterministic grid pathing, occupancy, and microcell example |
| `scenarios/needs-and-preemption.json` | Small homeostasis, System 1 interruption, correction, and recovery example |
| `scenarios/weather-and-hours.json` | Small civil-time, weather, schedule, availability, and environment-query example |
| `scenarios/neighborhood-errand.json` | Medium deterministic cross-building errand with reusable elements and transactions |
| `scenarios/community-meetup.json` | Medium controller-driven physical, social, engagement, and text-content example |
| `scenarios/open-city-day.json` | Large free-form multimodal city assembled from reusable building archetypes |

Narrow protocol and failure cases are regression fixtures under
`tests/fixtures/scenarios`; they are not user catalog scenarios.

## Characters

Character files are reusable stable dossiers. Briefings, goals, current
possessions, memories, locations, clothing, and other situation state remain
scenario or runtime data.

| File | Purpose |
| --- | --- |
| `characters/alex-chen.json` | Reusable analytical and technical profile |
| `characters/jordan-lee.json` | Reusable operations and coordination profile |
| `characters/maya-thompson.json` | Reusable education and community profile |
| `characters/samira-khan.json` | Comprehensive `human-v1` authoring reference |

## Elements

Element IDs describe reusable domain roles rather than scenarios. Families
cover common physical objects plus residential, hospitality, retail, civic,
and mobility buildings and rooms. Scenario instances supply place names,
availability, stock, and other closed overrides.

Element IDs match filenames. Every dependency reference carries the current
semantic SHA-256 content hash, and the complete graph must resolve before a
scenario can be staged.
