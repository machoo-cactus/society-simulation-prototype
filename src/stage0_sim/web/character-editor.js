const SECTIONS = [
  {
    id: "identity",
    title: "Identity",
    fields: [
      ["display_name", "Display name", "text", true],
      ["age", "Age", "number"],
      ["gender", "Gender", "text"],
      ["pronouns", "Pronouns", "text"],
      ["occupation", "Occupation", "text"],
    ],
  },
  {
    id: "appearance",
    title: "Appearance",
    fields: [
      ["summary", "Summary", "textarea"],
      ["height", "Height", "text"],
      ["build", "Build", "text"],
      ["hair", "Hair", "text"],
      ["eyes", "Eyes", "text"],
      ["clothing", "Clothing", "textarea"],
      ["distinguishing_features", "Distinguishing features", "list"],
    ],
  },
  {
    id: "personality",
    title: "Personality",
    fields: [
      ["summary", "Summary", "textarea"],
      ["traits", "Traits", "list"],
      ["temperament", "Temperament", "text"],
      ["social_style", "Social style", "text"],
      ["speech_style", "Speech style", "textarea"],
      ["strengths", "Strengths", "list"],
      ["flaws", "Flaws", "list"],
    ],
  },
  {
    id: "background",
    title: "Background",
    fields: [
      ["birthplace", "Birthplace", "text"],
      ["residence", "Residence", "text"],
      ["education", "Education", "textarea"],
      ["history", "History", "textarea"],
    ],
  },
  {
    id: "motivations",
    title: "Motivations",
    fields: [
      ["values", "Values", "list"],
      ["goals", "Goals", "list"],
      ["fears", "Fears", "list"],
      ["needs", "Needs", "list"],
      ["current_priorities", "Current priorities", "list"],
    ],
  },
  {
    id: "capabilities",
    title: "Capabilities",
    fields: [
      ["skills", "Skills", "list"],
      ["knowledge_areas", "Knowledge areas", "list"],
      ["limitations", "Limitations", "list"],
    ],
  },
  {
    id: "preferences",
    title: "Preferences",
    fields: [
      ["likes", "Likes", "list"],
      ["dislikes", "Dislikes", "list"],
      ["habits", "Habits", "list"],
      ["routines", "Routines", "list"],
    ],
  },
];

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function deepMergePreservingUnknown(base, edited) {
  if (!isObject(base) || !isObject(edited)) return structuredClone(edited);
  const merged = structuredClone(base);
  for (const [key, value] of Object.entries(edited)) {
    merged[key] = isObject(value) && isObject(merged[key])
      ? deepMergePreservingUnknown(merged[key], value)
      : structuredClone(value);
  }
  return merged;
}

function emptyProfile(displayName = "New character") {
  return {
    template_id: "human-v1",
    identity: {
      display_name: displayName,
      age: null,
      gender: "",
      pronouns: "",
      occupation: "",
    },
    appearance: {
      summary: "",
      height: "",
      build: "",
      hair: "",
      eyes: "",
      clothing: "",
      distinguishing_features: [],
    },
    personality: {
      summary: "",
      traits: [],
      temperament: "",
      social_style: "",
      speech_style: "",
      strengths: [],
      flaws: [],
    },
    background: {
      birthplace: "",
      residence: "",
      education: "",
      history: "",
    },
    motivations: {
      values: [],
      goals: [],
      fears: [],
      needs: [],
      current_priorities: [],
    },
    capabilities: {
      skills: [],
      knowledge_areas: [],
      limitations: [],
    },
    preferences: {
      likes: [],
      dislikes: [],
      habits: [],
      routines: [],
    },
    relationships: [],
    custom_sections: [],
  };
}

function normalizedProfile(profile, fallbackName) {
  const source = isObject(profile) ? structuredClone(profile) : {};
  const normalized = emptyProfile(
    source.identity?.display_name ?? source.display_name ?? fallbackName
  );
  normalized.template_id = source.template_id ?? "human-v1";
  for (const section of SECTIONS) {
    const values = isObject(source[section.id]) ? source[section.id] : {};
    for (const [field, , type] of section.fields) {
      const legacyValue =
        section.id === "identity" && field === "occupation"
          ? source.role
          : section.id === "personality" && field === "traits"
            ? source.traits
            : section.id === "motivations" && field === "values"
              ? source.values
              : section.id === "motivations" && field === "goals"
                ? source.goals
                : undefined;
      const value = values[field] ?? legacyValue;
      if (value === undefined) continue;
      normalized[section.id][field] =
        type === "list" ? (Array.isArray(value) ? value : []) : value;
    }
  }
  normalized.relationships = Array.isArray(source.relationships)
    ? source.relationships
    : isObject(source.relationships)
      ? Object.entries(source.relationships).map(([target_id, relationship]) => ({
          target_id,
          relationship,
          sentiment: "",
          notes: "",
        }))
      : [];
  normalized.custom_sections = Array.isArray(source.custom_sections)
    ? source.custom_sections
    : [];
  return normalized;
}

function slug(value) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "character";
}

function uniqueProfileId(profiles, base) {
  let candidate = slug(base);
  let suffix = 2;
  while (Object.hasOwn(profiles, candidate)) {
    candidate = `${slug(base)}-${suffix}`;
    suffix += 1;
  }
  return candidate;
}

function listValue(value) {
  return Array.isArray(value) ? value.join("\n") : "";
}

function parseList(value) {
  return value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseArrayJson(value, label) {
  let parsed;
  try {
    parsed = JSON.parse(value || "[]");
  } catch (error) {
    throw new Error(`${label} must be valid JSON: ${error.message}`);
  }
  if (!Array.isArray(parsed)) throw new Error(`${label} must be a JSON array`);
  return parsed;
}

function updateProfileReferences(scenario, oldId, newId) {
  for (const entity of scenario.entities ?? []) {
    const profile = entity?.components?.character_profile;
    if (isObject(profile) && profile.profile_ref === oldId) {
      profile.profile_ref = newId;
    }
  }
}

export function createCharacterEditor({root, onScenarioChange}) {
  let scenario = null;
  let selectedId = null;
  let disabled = false;
  let filter = "";
  let dirty = false;
  const list = root.querySelector('[data-role="profile-list"]');
  const empty = root.querySelector('[data-role="profile-empty"]');
  const form = root.querySelector('[data-role="profile-form"]');
  const status = root.querySelector('[data-role="profile-status"]');
  const addButton = root.querySelector('[data-action="add-profile"]');
  const duplicateButton = root.querySelector('[data-action="duplicate-profile"]');
  const deleteButton = root.querySelector('[data-action="delete-profile"]');

  function profiles() {
    scenario.character_profiles ??= {};
    return scenario.character_profiles;
  }

  function setStatus(message, error = false) {
    status.textContent = message;
    status.classList.toggle("error", error);
  }

  function renderList() {
    list.replaceChildren();
    for (const [profileId, profile] of Object.entries(profiles())) {
      const name =
        profile?.identity?.display_name ?? profile?.display_name ?? profileId;
      if (
        filter
        && !`${name} ${profileId}`.toLowerCase().includes(filter)
      ) {
        continue;
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "character-studio__profile";
      button.classList.toggle("selected", profileId === selectedId);
      button.disabled = disabled;
      const strong = document.createElement("strong");
      strong.textContent = name;
      const small = document.createElement("small");
      small.textContent = profileId;
      button.append(strong, small);
      button.addEventListener("click", () => {
        if (
          dirty
          && !window.confirm("Discard unsaved character changes?")
        ) {
          return;
        }
        dirty = false;
        selectedId = profileId;
        render();
      });
      list.append(button);
    }
  }

  function fieldControl(sectionId, field, label, type, required, value) {
    const wrapper = document.createElement("label");
    wrapper.className = type === "textarea" || type === "list"
      ? "character-field character-field--wide"
      : "character-field";
    const title = document.createElement("span");
    title.textContent = label;
    let control;
    if (type === "textarea" || type === "list") {
      control = document.createElement("textarea");
      control.rows = type === "list" ? 3 : 4;
      if (type === "list") {
        control.placeholder = "One item per line";
        control.value = listValue(value);
      } else {
        control.value = value ?? "";
      }
    } else {
      control = document.createElement("input");
      control.type = type;
      control.value = value ?? "";
      if (type === "number") {
        control.min = "0";
        control.max = "150";
      }
    }
    control.name = `${sectionId}.${field}`;
    control.required = Boolean(required);
    control.disabled = disabled;
    wrapper.append(title, control);
    return wrapper;
  }

  function renderForm() {
    form.replaceChildren();
    const profile = profiles()[selectedId];
    const hasProfile = Boolean(profile);
    empty.hidden = hasProfile;
    form.hidden = !hasProfile;
    duplicateButton.disabled = disabled || !hasProfile;
    deleteButton.disabled = disabled || !hasProfile;
    if (!hasProfile) return;

    const normalized = normalizedProfile(profile, selectedId);
    const basics = document.createElement("fieldset");
    basics.className = "character-section";
    const legend = document.createElement("legend");
    legend.textContent = "Profile";
    basics.append(legend);
    basics.append(
      fieldControl("profile", "id", "Profile ID", "text", true, selectedId),
      fieldControl(
        "profile",
        "template_id",
        "Template ID",
        "text",
        true,
        normalized.template_id
      )
    );
    form.append(basics);

    for (const section of SECTIONS) {
      const fieldset = document.createElement("fieldset");
      fieldset.className = "character-section";
      const sectionLegend = document.createElement("legend");
      sectionLegend.textContent = section.title;
      fieldset.append(sectionLegend);
      for (const [field, label, type, required] of section.fields) {
        fieldset.append(
          fieldControl(
            section.id,
            field,
            label,
            type,
            required,
            normalized[section.id][field]
          )
        );
      }
      form.append(fieldset);
    }

    for (const [name, label, value, help] of [
      [
        "relationships",
        "Relationships",
        normalized.relationships,
        "Ordered JSON records with target_id, relationship, sentiment, and notes.",
      ],
      [
        "custom_sections",
        "Custom sections",
        normalized.custom_sections,
        "Ordered JSON sections and fields, including prompt_visible and ui_visible.",
      ],
    ]) {
      const fieldset = document.createElement("fieldset");
      fieldset.className = "character-section character-section--advanced";
      const sectionLegend = document.createElement("legend");
      sectionLegend.textContent = label;
      const hint = document.createElement("p");
      hint.className = "muted";
      hint.textContent = help;
      const textarea = document.createElement("textarea");
      textarea.name = name;
      textarea.rows = 10;
      textarea.value = JSON.stringify(value, null, 2);
      textarea.disabled = disabled;
      fieldset.append(sectionLegend, hint, textarea);
      form.append(fieldset);
    }

    const actions = document.createElement("div");
    actions.className = "character-studio__form-actions";
    const save = document.createElement("button");
    save.type = "submit";
    save.className = "primary";
    save.textContent = "Save character";
    save.disabled = disabled;
    actions.append(save);
    form.append(actions);
  }

  function render() {
    if (!scenario) {
      root.hidden = true;
      return;
    }
    root.hidden = false;
    const ids = Object.keys(profiles());
    if (!selectedId || !Object.hasOwn(profiles(), selectedId)) {
      selectedId = ids[0] ?? null;
    }
    addButton.disabled = disabled;
    renderList();
    renderForm();
  }

  async function commit(message) {
    dirty = false;
    setStatus("Validating character changes...");
    try {
      const accepted = await onScenarioChange(
        structuredClone(scenario),
        message
      );
      setStatus(
        accepted ? message : "Character changes were not saved.",
        !accepted
      );
    } catch (error) {
      setStatus(`Could not save character: ${error.message}`, true);
    }
  }

  addButton.addEventListener("click", async () => {
    const id = uniqueProfileId(profiles(), "new-character");
    profiles()[id] = emptyProfile("New character");
    selectedId = id;
    render();
    await commit(`Created ${id}.`);
  });

  duplicateButton.addEventListener("click", async () => {
    if (!selectedId) return;
    const id = uniqueProfileId(profiles(), `${selectedId}-copy`);
    profiles()[id] = structuredClone(profiles()[selectedId]);
    profiles()[id].identity ??= {};
    profiles()[id].identity.display_name =
      `${profiles()[id].identity.display_name ?? selectedId} Copy`;
    selectedId = id;
    render();
    await commit(`Duplicated character as ${id}.`);
  });

  deleteButton.addEventListener("click", async () => {
    if (!selectedId) return;
    if (!window.confirm(`Delete character "${selectedId}"?`)) return;
    const removedId = selectedId;
    delete profiles()[removedId];
    const replacement = Object.keys(profiles())[0] ?? null;
    updateProfileReferences(scenario, removedId, replacement);
    for (const entity of scenario.entities ?? []) {
      if (entity?.components?.character_profile?.profile_ref === null) {
        delete entity.components.character_profile.profile_ref;
      }
    }
    selectedId = replacement;
    render();
    await commit(`Deleted ${removedId}.`);
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedId) return;
    try {
      const data = new FormData(form);
      const nextId = String(data.get("profile.id") ?? "").trim();
      if (!nextId) throw new Error("Profile ID is required");
      if (nextId !== selectedId && Object.hasOwn(profiles(), nextId)) {
        throw new Error(`Profile ID "${nextId}" already exists`);
      }
      const edited = emptyProfile();
      edited.template_id = String(data.get("profile.template_id") ?? "").trim();
      for (const section of SECTIONS) {
        for (const [field, , type] of section.fields) {
          const raw = String(data.get(`${section.id}.${field}`) ?? "");
          edited[section.id][field] =
            type === "list"
              ? parseList(raw)
              : type === "number"
                ? (raw.trim() ? Number(raw) : null)
                : raw.trim();
        }
      }
      edited.relationships = parseArrayJson(
        String(data.get("relationships") ?? "[]"),
        "Relationships"
      );
      edited.custom_sections = parseArrayJson(
        String(data.get("custom_sections") ?? "[]"),
        "Custom sections"
      );
      const next = deepMergePreservingUnknown(
        profiles()[selectedId],
        edited
      );
      if (nextId !== selectedId) {
        delete profiles()[selectedId];
        updateProfileReferences(scenario, selectedId, nextId);
      }
      profiles()[nextId] = next;
      selectedId = nextId;
      render();
      await commit(`Saved ${next.identity.display_name} (${nextId}).`);
    } catch (error) {
      setStatus(error.message, true);
    }
  });
  form.addEventListener("input", () => {
    dirty = true;
    setStatus("Unsaved changes.");
  });

  return {
    setScenario(nextScenario) {
      scenario = nextScenario ? structuredClone(nextScenario) : null;
      selectedId = null;
      dirty = false;
      setStatus("");
      render();
    },
    syncScenario(nextScenario) {
      scenario = nextScenario ? structuredClone(nextScenario) : null;
      dirty = false;
      render();
    },
    setFilter(value) {
      filter = value.trim().toLowerCase();
      renderList();
    },
    getSelected() {
      if (!scenario || !selectedId) return null;
      return {
        id: selectedId,
        profile: structuredClone(profiles()[selectedId]),
      };
    },
    setDisabled(nextDisabled) {
      if (disabled === nextDisabled) return;
      disabled = nextDisabled;
      render();
    },
  };
}
