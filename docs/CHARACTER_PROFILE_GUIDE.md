# Character Profile Guide

Character profiles are reusable JSON resources that describe who a simulated
character is. They are stored separately from scenarios and from current
physiology, perception, memory, plans, goals, priorities, and temporary role
briefings.

Each controller request has three prompt layers:

1. the shared character-controller system prompt;
2. the selected character's deterministic profile description;
3. dynamic self-state, observations, memories, targets, and tools.

## Reusable characters

Store one character per file in `characters\`:

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
  "appearance": {
    "summary": "Medium height with a slim build",
    "hair": "Short black hair",
    "clothing": "Dark sweater and practical trousers"
  },
  "personality": {
    "summary": "Quiet, methodical, and considerate",
    "traits": ["methodical", "reserved"],
    "speech_style": "Brief and precise"
  },
  "motivations": {
    "values": ["accuracy", "helping colleagues"],
    "fears": ["letting collaborators down"],
    "needs": ["time to verify important claims"]
  }
}
```

The filename must match the character ID, for example
`characters\alex-chen.json`. Scenarios declare abstract character slots:

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
    },
    "planner": {
      "daily_goals": ["Complete the report"],
      "current_priorities": ["Verify the latest evidence"]
    },
    "memory": {
      "initial_episodes": [
        {"text": "The report is due this evening."}
      ]
    }
  }
}
```

The browser simulation page loads the character catalog from the API and shows
only eligible characters for each slot. Explicit assignments override optional
defaults. Assignments are validated before Start, and the same reusable
character may be selected for multiple slots.

Age bounds are inclusive. Gender allowlists use trimmed, case-insensitive exact
matching. Template IDs use exact matching. If a constrained identity field is
missing, the character is ineligible.

The separate `/ui/characters/` page creates, imports, duplicates, downloads,
deletes, renames, and edits character files. It exposes every `human-v1` field,
plus ordered relationship records and custom sections as JSON arrays. Unknown
top-level and nested section content is preserved when known fields are edited.
Saves use content hashes so a stale browser tab cannot silently overwrite a
newer edit.

## Standard sections

The built-in `human-v1` template supports:

| Section | Fields |
|---|---|
| `identity` | `display_name`, `age`, `gender`, `pronouns`, `occupation` |
| `appearance` | `summary`, `height`, `build`, `hair`, `eyes`, `clothing`, `distinguishing_features` |
| `personality` | `summary`, `traits`, `temperament`, `social_style`, `speech_style`, `strengths`, `flaws` |
| `background` | `birthplace`, `residence`, `education`, `history` |
| `motivations` | `values`, `fears`, `needs` |
| `capabilities` | `skills`, `knowledge_areas`, `limitations` |
| `preferences` | `likes`, `dislikes`, `habits`, `routines` |
| `relationships` | ordered records containing `target_id`, `relationship`, `sentiment`, and `notes` |

Only `identity.display_name` is required.

## Experimental extensions

Explicit custom sections remain the preferred way to add labelled values to the
standard editor:

```json
{
  "custom_sections": [
    {
      "id": "decision_experiment",
      "title": "Decision Experiment",
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

Sections and fields retain their JSON order. Set `prompt_visible` or
`ui_visible` to `false` to exclude an experimental value from that surface.

Profiles may also contain arbitrary JSON-compatible fields at the top level or
inside any profile section. Those fields survive validation, library CRUD,
scenario freezing, hashing, API round trips, retrieval, and editor saves.
Unrecognized fields are descriptive information only; they do not grant action
permissions or add simulation mechanics.

## Templates and compatibility

The built-in `human-v1` template uses this section order:

```json
{
  "schema_version": 1,
  "sections": [
    "identity",
    "appearance",
    "personality",
    "background",
    "motivations",
    "capabilities",
    "preferences",
    "relationships"
  ]
}
```

Scenario-level `character_profiles`, entity `character_profile`, `profile_ref`,
inline profiles, and the previous flat profile fields are rejected by scenario
schema version 2. Use
`stage0-sim characters extract <scenario> --write` to create character files,
move legacy goals and priorities into planner data, and emit `character_slot`
components.

Characters and assignments are resolved and frozen when a composed situation
is staged. Editing a scenario or character source file does not alter an
already staged situation or active run. The effective slot assignment, complete
resolved character snapshot, and content hash are stored in the run dataset.
