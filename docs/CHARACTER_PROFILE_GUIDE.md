# Character Authoring

**Owner:** Reusable character schema version 2, assignment, and situation
synthesis.

Character files describe stable people, not current simulation state. Writable
files live in `data\characters\`; tracked references live in
`examples\characters\`. The filename stem must equal `id`.

## Minimal schema

```json
{
  "schema_version": 2,
  "id": "alex-chen",
  "template_id": "human-v1",
  "identity": {
    "display_name": "Alex Chen",
    "birth_date": "1990-02-14"
  }
}
```

Only `identity.display_name` is required. Schema version 2 accepts exactly
`schema_version: 2`, requires `template_id: "human-v1"`, uses
`identity.birth_date` rather than integer age, and uses
`body_measurements.height_cm` rather than prose `appearance.height`.

IDs use lowercase letters, numbers, dots, underscores, and hyphens; Windows
reserved filenames are rejected.

## Stable dossier sections

`human-v1` defines these ordered standard sections:

| Section | Content |
| --- | --- |
| `identity` | Name, birth date, gender, pronouns, occupation |
| `body_measurements` | Dated metric measurements and shoe sizing |
| `appearance` | Intrinsic appearance and distinguishing features |
| `health` | Dated conditions, allergies, medication, disability and sensory facts |
| `personality`, `motivations` | Stable traits, style, values, fears, and needs |
| `background` | Birthplace, residence, education, history |
| `financial_situation` | Dated currency-denominated financial snapshot |
| `capabilities`, `preferences` | Skills, knowledge, limitations, likes, habits, routines |
| `presentation` | Stable aesthetic, clothing preferences, grooming, accessories |
| `dispositions`, `communication`, `decision_coping` | Usual tendencies and conditional responses |
| `life_structure` | Household, obligations, possessions, culture, interests, social patterns |
| `family`, `relationships` | Ordered structured relationship records |
| `custom_sections` | Ordered labelled experimental values |

Standard and custom models preserve additional JSON-compatible fields. Unknown
fields remain descriptive information: they do not create inventory,
permissions, capabilities, world objects, or deterministic behavior.

Use stable defaults and tendencies—“usually”, “prefers”, “when travelling”.
Current clothing, affect, carried items, tasks, plans, goals, location,
physiology, memories, and completed outcomes belong to the scenario,
synthesized situation, or live ECS state.

Runtime embodiment is also not a dossier field. Materialized characters use a
fixed 5×5-microcell body footprint, a cardinal pose, live occupied cells,
`STANDING`/`SITTING`/`LYING` posture, left/right hands, and optional physical
parent/custody relations. These are ECS state and can change only through
domain execution. Descriptive ownership or possessions in a profile do not
spawn objects, fill hands, grant custody, or alter the independent abstract
scenario `possessions` component.

Use [Content migration](CONTENT_MIGRATION.md) to upgrade version-1 characters.
Lossy legacy fields are preserved in stable migration custom sections rather
than silently discarded.

Financial, measurement, and health values are dated snapshots. They do not
create spendable funds, live physiology, or other entities.

## Scenario slots and assignments

Scenarios declare abstract slots:

```json
{
  "id": "agent-001",
  "components": {
    "character_slot": {
      "label": "Lead analyst",
      "briefing": "Complete the report before leaving.",
      "default_character_id": "alex-chen",
      "constraints": {
        "minimum_age": 30,
        "allowed_genders": ["Man"],
        "allowed_template_ids": ["human-v1"]
      }
    }
  }
}
```

Age is derived from `birth_date` at the scenario calendar start. An age
constraint therefore requires a calendar. Age bounds are inclusive; gender
matching is trimmed and case-insensitive; template IDs match exactly. Missing
constrained data makes the character ineligible.

The UI can replace defaults before staging. One reusable character may fill
multiple slots. Resolution freezes the complete profile, source hash, and
assignment into the prepared scenario and dataset; later library edits do not
alter a staged composition or active run.

Scenario source version 6 rejects embedded character catalogs, inline profiles,
`profile_ref`, and reusable profile fields. Goals and initial memories are
scenario-owned components, not dossier fields.

## Custom sections

```json
{
  "custom_sections": [
    {
      "id": "decision_experiment",
      "title": "Decision experiment",
      "prompt_visible": true,
      "ui_visible": true,
      "fields": [
        {
          "key": "risk_tolerance",
          "label": "Risk tolerance",
          "value": "low",
          "prompt_visible": true,
          "ui_visible": true
        }
      ]
    }
  ]
}
```

Section IDs and field keys must be unique in their containing lists. JSON order
is preserved. Visibility flags affect prompt/UI projection only.

## Character-situation synthesis

Enable optional one-time synthesis in the scenario:

```json
{"character_situation_synthesis": {"enabled": true}}
```

After all assignments validate, the application freezes the assignment map and
invokes one model request per assigned character in stable entity order. The
batch is transactional: any failure rejects the candidate composition.

The model receives the stable dossier plus a bounded scenario projection. It
may describe current outfit, grooming, affect, recent context, role
interpretation, and manifestations of existing tendencies. It cannot create or
modify identity, goals, memories, vitals, locations, inventory, permissions,
relationships, affordances, capabilities, or outcomes.

The frozen private situation, hashes, prompt/schema versions, provider/model,
and usage metadata are recorded. Start never performs synthesis. Enabling it
without a configured provider fails explicitly; offline scenarios should leave
it disabled.

## Library workflow

Use `/ui/characters/` to create, import, duplicate, rename, edit, download, and
delete profiles. Saves and renames use content hashes so stale tabs cannot
overwrite newer revisions. The API equivalents are listed in
[API and UI workflows](API_AND_UI.md).

The most complete tracked authoring sample is
`examples\characters\samira-khan.json`.
