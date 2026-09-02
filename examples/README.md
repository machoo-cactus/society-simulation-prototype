# Tracked examples

These catalogs are version-controlled authoring and demonstration samples.
They are never the default writable UI libraries. Runtime-created or edited
resources live under `data/characters`, `data/scenarios`, and `data/elements`.
Copy an example into a writable library before editing it.
The installed `stage0-sim run demo` command uses separate packaged resources,
so it remains runnable without this checkout.

## Scenarios

| File | Classification and purpose |
| --- | --- |
| `scenarios/minimal.json` | Runnable deterministic clock and entity baseline |
| `scenarios/navigation.json` | Runnable grid navigation and occupancy example |
| `scenarios/homeostasis.json` | Runnable physiology trajectory example |
| `scenarios/system1-preemption.json` | Runnable System 1 interruption and recovery example |
| `scenarios/environment-demo.json` | Current weather transitions, opening hours, and `check_environment` behavior |
| `scenarios/provider-character-controller.json` | Opt-in external-provider character-controller example |
| `scenarios/sparse-city-car-demo.json` | Sparse city, hierarchical location, and vehicle travel example |
| `scenarios/greyford-rivermarket-exchange.json` | Deterministic staffed transaction and cross-building navigation example |
| `scenarios/greyford-office-evening.json` | Large-city work, dining, and multimodal travel example |
| `scenarios/reference-city-restaurants.json` | Reusable element references, semantic hashes, and isolated overrides |
| `scenarios/willowbrook-saturday-morning.json` | Controller-driven suburban neighborhood with three furnished home starts, door-linked travel, portable-object interactions, and an open-ended social meetup |

The scripted controller scenario is regression data, not a user catalog
example, and therefore lives at
`tests/fixtures/scenarios/scripted-tool-cognition.json`.

## Characters

| File | Classification and purpose |
| --- | --- |
| `characters/alex-chen.json` | General reusable profile used by several scenarios |
| `characters/jordan-lee.json` | General reusable profile used by the provider-controller scenario |
| `characters/maya-thompson.json` | General reusable teacher and community-garden volunteer profile used by Willowbrook |
| `characters/character-greyford-mara-ellison.json` | Scenario-specific reusable Greyford profile |
| `characters/samira-khan.json` | Full `human-v1` authoring sample, including ordered custom sections |

`samira-khan.json` is intentionally retained as the most comprehensive
standalone character-authoring reference; it is not required by a bundled
scenario.

## Elements

`elements/` contains reusable buildings, rooms, objects, stations,
transaction points, and NPC roles. The compact
`elements/standard-restaurant.json` graph is the primary authoring reference;
the namespaced Greyford and sparse-city resources support the larger runnable
scenario examples. Element IDs match filenames and every reference carries a
semantic content hash.

The Willowbrook namespaced element family is the comprehensive physical-object
example. Its six buildings use the fixed 9-microcells-per-legacy-cell metric
and entrance links to reusable openable exterior doors, alongside reusable
windows, mirrors, chairs, sofas, beds, tables, desks, counters, bookcases,
cabinets, market displays, cafe and library furniture, lamps, books, bottles,
mugs, phones, and corrective glasses. Cedar Court, both Maple Row homes,
Corner Cup Cafe, Willowbrook Market, and the Library Annex demonstrate
semantic dimensions distinct from footprints, equipment effects, scent
sources, per-sense structural blocking, occupancy and storage slots, openable
entrances, support/container relations, and clear interaction approaches
without relying on placement names for behavior.
