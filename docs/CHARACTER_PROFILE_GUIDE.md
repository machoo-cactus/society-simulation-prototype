# Character Profile Guide

Character profiles are reusable scenario data that describe who a simulated
character is. They are separate from current physiology, perception, memory, and
plans.

Each controller request has three prompt layers:

1. the shared character-controller system prompt;
2. the selected character's deterministic profile description;
3. dynamic self-state, observations, memories, targets, and tools.

## Reusable profiles

Define a catalog at the scenario root:

```json
{
  "character_profiles": {
    "alex-chen": {
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
        "speech_style": "Brief and precise",
        "strengths": ["careful analysis"],
        "flaws": ["hesitates when information is incomplete"]
      },
      "motivations": {
        "values": ["accuracy", "helping colleagues"],
        "goals": ["complete the report"]
      }
    }
  }
}
```

Assign a profile to an entity slot:

```json
{
  "id": "agent-001",
  "components": {
    "character_profile": {"profile_ref": "alex-chen"}
  }
}
```

The browser shows one profile selector for every entity when the loaded scenario
has a profile catalog. The selected assignments are validated before Start and
are used to create the run. Profiles may be reused in multiple slots.

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

## Templates and overrides

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

An entity may override part of a referenced profile:

```json
{
  "character_profile": {
    "profile_ref": "alex-chen",
    "personality": {
      "speech_style": "Warm but concise"
    }
  }
}
```

Object sections merge recursively. Lists replace the base list. The resolved
profile receives a deterministic content hash recorded with cognition events.

The previous flat fields (`display_name`, `role`, `traits`, `values`, `goals`,
and relationship maps) remain accepted for compatibility and are normalized
into `human-v1`.
