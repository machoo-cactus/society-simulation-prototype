# Character Profile Guide

Character profiles are reusable JSON resources that describe who a simulated
character is. They are stored separately from scenarios and from current
physiology, perception, memory, and plans.

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
    "goals": ["complete the report"]
  }
}
```

The filename must match the character ID, for example
`characters\alex-chen.json`. Assign it to a scenario entity slot:

```json
{
  "id": "agent-001",
  "components": {
    "character_profile": {"character_id": "alex-chen"}
  }
}
```

The browser simulation page loads the character catalog from the API and shows
a selector for each character slot. Assignments are validated before Start.
Characters may be reused in multiple slots.

The separate `/ui/characters.html` page creates, duplicates, deletes, renames,
and edits character files. It exposes every `human-v1` field, plus ordered
relationship records and custom sections as JSON arrays. Saves use content
hashes so a stale browser tab cannot silently overwrite a newer edit.

## Standard sections

The built-in `human-v1` template supports:

| Section | Fields |
|---|---|
| `identity` | `display_name`, `age`, `gender`, `pronouns`, `occupation` |
| `appearance` | `summary`, `height`, `build`, `hair`, `eyes`, `clothing`, `distinguishing_features` |
| `personality` | `summary`, `traits`, `temperament`, `social_style`, `speech_style`, `strengths`, `flaws` |
| `background` | `birthplace`, `residence`, `education`, `history` |
| `motivations` | `values`, `goals`, `fears`, `needs`, `current_priorities` |
| `capabilities` | `skills`, `knowledge_areas`, `limitations` |
| `preferences` | `likes`, `dislikes`, `habits`, `routines` |
| `relationships` | ordered records containing `target_id`, `relationship`, `sentiment`, and `notes` |

Only `identity.display_name` is required.

## Experimental extensions

Use explicit custom sections instead of adding arbitrary keys. This catches
misspellings while keeping experiments easy to author:

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

## Templates and compatibility

`character_profile_templates` can change section order:

```json
{
  "character_profile_templates": {
    "human-v1": {
      "schema_version": 1,
      "sections": [
        "identity",
        "personality",
        "motivations",
        "appearance",
        "background",
        "capabilities",
        "preferences",
        "relationships"
      ]
    }
  }
}
```

Scenario-level `character_profiles`, `profile_ref`, inline profiles, and the
previous flat fields remain accepted for compatibility. They are deprecated
and are never emitted by the character editor or migration command. Use
`stage0-sim characters extract <scenario> --write` to create character files
and a migrated scenario.

Characters are resolved and frozen when a scenario is staged. Editing a source
file does not alter an already staged scenario or active run. The complete
resolved character snapshot and content hash are stored in the run dataset.
