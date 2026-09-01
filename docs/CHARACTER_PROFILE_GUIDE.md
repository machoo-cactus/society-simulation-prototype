# Character Profile Guide

Character profiles are reusable JSON resources that describe who a simulated
character is. They are stored separately from scenarios and from current
physiology, perception, memory, plans, goals, priorities, and temporary role
briefings.

Each controller request has three prompt layers:

1. the shared character-controller system prompt;
2. a frozen short-term character situation plus bounded retrieved stable dossier
   capsules;
3. dynamic self-state, observations, memories, targets, and tools.

Character information has four separate layers:

1. the stable reusable dossier;
2. authoritative scenario facts such as role, goals, priorities, location, and
   initial environment;
3. the frozen non-authoritative character situation synthesized after all
   assignments are valid;
4. live physiology, perception, memory, plans, and outcomes.

Do not put exact current clothing, today's carried items, current affect,
completed tasks, immediate itinerary, or other short-term state in a reusable
character file.

## Reusable characters

Store one character per file in `characters\`:

```json
{
  "schema_version": 2,
  "id": "alex-chen",
  "template_id": "human-v1",
  "identity": {
    "display_name": "Alex Chen",
    "birth_date": "1990-02-14",
    "gender": "Man",
    "pronouns": "he/him",
    "occupation": "Software research engineer"
  },
  "body_measurements": {
    "measured_on": "2026-08-12",
    "height_cm": 178.0,
    "weight_kg": 69.4,
    "waist_cm": 78.0
  },
  "appearance": {
    "summary": "Medium height with a slim build",
    "build": "lean and slim",
    "hair": "Short black hair"
  },
  "health": {
    "as_of_date": "2026-08-12",
    "conditions": [],
    "allergies": [],
    "medications": [],
    "vision": "Myopia corrected with prescription glasses"
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
  },
  "financial_situation": {
    "as_of_date": "2026-08-31",
    "currency": "CAD",
    "annual_gross_income": 128000,
    "total_debt": 18000,
    "housing_tenure": "Rents apartment"
  },
  "presentation": {
    "aesthetic_identity": "Quietly practical and unobtrusive",
    "wardrobe_palette": ["charcoal", "navy", "forest green"],
    "comfort_priorities": ["comfortable walking shoes", "adaptable layers"],
    "context_variations": [
      "uses lighter layers and relaxed clothing while travelling",
      "adds a structured layer for external presentations"
    ]
  },
  "dispositions": {
    "emotional_baseline": "Calm and mildly serious",
    "adaptability": "Adapts after forming a clear model of a new setting",
    "novelty_response": "Orients carefully, then explores methodically"
  },
  "family": {
    "members": [
      {
        "member_id": "mei-chen",
        "linked_character_id": "mei-chen",
        "display_name": "Mei Chen",
        "relationship": "Older sister",
        "living_status": "alive",
        "household_member": false,
        "financial_dependent": false
      }
    ]
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
      "daily_goals": ["Maintain a sustainable work routine"],
      "current_priorities": ["Verify the latest evidence"],
      "goals": [
        {
          "id": "complete-report",
          "description": "Complete the report",
          "priority": 9,
          "deadline_time": 300,
          "criteria": [
            {
              "type": "action_outcome",
              "action": "WORK",
              "outcome": "completed",
              "target": "report-desk"
            }
          ]
        }
      ]
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

Structured `planner.goals` belong to the scenario, not the reusable character
dossier. They use stable authored IDs and closed measurable criteria, so their
progress and terminal result can be captured. Legacy `daily_goals` and
`current_priorities` remain backward compatible and receive deterministic
generated IDs, but their prose is not interpreted as success; unresolved
legacy goals are recorded with an `unknown` result. See
[Research Data Collection](DATA_COLLECTION.md#structured-goals-and-criteria).

Age bounds are inclusive and are evaluated from `identity.birth_date` against
the scenario calendar's start date. A version-2 character cannot satisfy an age
constraint when the scenario has no calendar. Gender allowlists use trimmed,
case-insensitive exact matching. Template IDs use exact matching. If a
constrained identity field is missing, the character is ineligible.

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
| `identity` | `display_name`, canonical `birth_date`, `gender`, `pronouns`, `occupation`; version-1 `age` is legacy compatibility only |
| `body_measurements` | dated metric `height_cm`, `weight_kg`, chest, waist, hips, inseam, and explicit shoe-size system/value |
| `appearance` | intrinsic `summary`, concise `build`, `hair`, `eyes`, legacy `height`/`clothing`, and `distinguishing_features` |
| `health` | dated stable health facts, conditions, allergies, medications, disabilities, sensory/mobility notes, procedures, and dietary restrictions |
| `personality` | `summary`, `traits`, `temperament`, `social_style`, `speech_style`, `strengths`, `flaws` |
| `background` | `birthplace`, `residence`, `education`, `history` |
| `financial_situation` | dated currency-denominated income, assets, debt, expenses, housing tenure, and dependent count |
| `motivations` | `values`, `fears`, `needs` |
| `capabilities` | `skills`, `knowledge_areas`, `limitations` |
| `preferences` | `likes`, `dislikes`, `habits`, `routines` |
| `presentation` | stable aesthetic, wardrobe palette, silhouettes, fabrics, formality range, comfort, grooming, accessories, constraints, purchase habits, and context variations |
| `dispositions` | usual emotional baseline, sociability, assertiveness, patience, conscientiousness, openness, adaptability, risk and ambiguity tolerance, impulse control, social styles, and responses to pressure, fatigue, novelty, authority, and crowds |
| `communication` | cadence, vocabulary, directness, politeness, humor, nonverbal manner, listening, disagreement, apology, and audience-specific styles |
| `decision_coping` | information seeking, planning horizon, heuristics, error sensitivity, persistence, recovery, self-soothing, stress signals, and conditional shifts |
| `life_structure` | household, recurring obligations, material habits, typical possessions, cultural practices, interests, and social patterns |
| `family` | ordered hard-fact member records with optional library links, birth dates, living/residence/household status, and dependency status |
| `relationships` | ordered records containing `target_id`, `relationship`, `sentiment`, and `notes` |

Only `identity.display_name` is required.

Stable fields describe defaults, ranges, and conditional tendencies. Prefer
phrases such as "usually", "tends to", "prefers", and "when travelling". Exact
claims such as "wearing now", "today", "completed", or "remaining" belong in
the scenario or synthesized situation.

Attribute fields should state the attribute, not explain its cause. Write
`"build": "lean and athletic"`, not `"build": "athletic from cycling"`.
Cycling belongs under capabilities, routines, or interests. Keep occupation in
identity, diagnoses in health, household membership in family/life structure,
and causal history in background.

Financial, measurement, and health values are dated snapshots. Updating them
creates a new character revision and content hash. Financial values do not
create spendable simulation resources, health records do not replace live
homeostasis or acute state, and family records do not instantiate another
character.

These fields are descriptive context. They do not create inventory,
affordances, skills, vehicles, permissions, or deterministic personality rules.

## Character-situation synthesis

Enable one-time composition in a scenario:

```json
{
  "character_situation_synthesis": {
    "enabled": true
  }
}
```

Synthesis starts only after every required character slot has a valid effective
assignment. The application freezes the complete assignment map and character
snapshots first, then invokes one model request per assigned character in stable
entity order. If any request or output fails, the whole candidate composition
is rejected and the previous staged artifact remains unchanged.

The model receives the complete stable dossier and a bounded per-character
scenario projection containing:

- slot label, briefing, and optional `synthesis_guidance`;
- authored planner goals and priorities;
- initial homeostasis, activity, position, or spatial location;
- initial calendar time and weather;
- relationships whose target characters are also assigned.

It does not receive the entire scenario JSON. The strict output contains a
summary, role interpretation, current outfit and grooming, descriptive carried
personal items, recent context, current affect, manifestations of existing
dispositions, assigned relationship context, and explicit assumptions.

The output cannot modify stable identity or generate authoritative goals,
priorities, memories, vitals, locations, resources, permissions, capabilities,
relationships, affordances, outcomes, or world facts. It is private descriptive
controller context only.

Synthesis uses the configured `STAGE0_LLM_*` provider. Enabling synthesis
without a provider, receiving invalid structured output, or encountering a
provider failure makes staging fail explicitly. Offline scenarios should leave
synthesis disabled.

The browser previews every frozen situation with input/content hashes, prompt
version, provider, and model. Regeneration is an explicit pre-run operation and
creates a new staged artifact; starting a run never calls the synthesis model.
Recording and replay use deterministic request IDs derived from canonical
composition input hashes.

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
  "schema_version": 2,
  "sections": [
    "identity",
    "body_measurements",
    "appearance",
    "health",
    "personality",
    "background",
    "financial_situation",
    "motivations",
    "capabilities",
    "preferences",
    "presentation",
    "dispositions",
    "communication",
    "decision_coping",
    "life_structure",
    "family",
    "relationships"
  ]
}
```

New and bundled character files use character schema version 2. Version-2
profiles author `birth_date` and `body_measurements.height_cm`; they reject
legacy `identity.age` and `appearance.height`. Version-1 files remain readable
for compatibility and may continue to use their static age for slot matching
until deliberately migrated. Because an exact birthday cannot be inferred from
an integer age, migration requires an authored birth date rather than an
automatic guess.

Scenario-level `character_profiles`, entity `character_profile`, `profile_ref`,
inline profiles, and the previous flat profile fields are rejected by scenario
source schema version 3. Use
`stage0-sim characters extract <scenario> --write` to create character files,
move legacy goals and priorities into planner data, and emit `character_slot`
components.

Characters and assignments are resolved and frozen when a composed situation
is staged. Editing a scenario or character source file does not alter an
already staged situation or active run. The effective slot assignment, complete
resolved character snapshot, synthesized situation, hashes, prompt version, and
provider metadata are stored in the run dataset.
