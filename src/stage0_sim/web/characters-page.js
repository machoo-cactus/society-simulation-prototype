import {api} from "./api-client.js";
import {createCharacterEditor} from "./character-editor.js";

const root = document.getElementById("character-studio-panel");
const search = document.getElementById("character-search");
const importInput = document.getElementById("import-character");
const exportButton = document.getElementById("export-character");
let hashes = {};
let persistedProfiles = {};
let schemaVersions = {};

function profilePayload(id, profile) {
  return {
    schema_version: schemaVersions[id] ?? 1,
    id,
    ...structuredClone(profile),
  };
}

function comparable(value) {
  return JSON.stringify(value);
}

function profileFromCharacter(character) {
  const profile = structuredClone(character);
  delete profile.id;
  delete profile.schema_version;
  return profile;
}

async function loadLibrary() {
  const listing = await api("/characters");
  const entries = await Promise.all(
    listing.characters.map(async (summary) => {
      const response = await api(
        `/characters/${encodeURIComponent(summary.id)}`
      );
      const character = profileFromCharacter(response.character);
      return [
        summary.id,
        character,
        response.content_hash,
        response.character.schema_version,
      ];
    })
  );
  persistedProfiles = Object.fromEntries(
    entries.map(([id, profile]) => [id, profile])
  );
  hashes = Object.fromEntries(
    entries.map(([id, , contentHash]) => [id, contentHash])
  );
  schemaVersions = Object.fromEntries(
    entries.map(([id, , , schemaVersion]) => [id, schemaVersion])
  );
  editor.setScenario({character_profiles: persistedProfiles, entities: []});
}

async function persistScenario(nextScenario) {
  const nextProfiles = nextScenario.character_profiles ?? {};
  const previousIds = new Set(Object.keys(persistedProfiles));
  const nextIds = new Set(Object.keys(nextProfiles));
  const deleted = [...previousIds].filter((id) => !nextIds.has(id));
  const added = [...nextIds].filter((id) => !previousIds.has(id));

  if (deleted.length === 1 && added.length === 1) {
    const oldId = deleted[0];
    const newId = added[0];
    const renamed = await api(
      `/characters/${encodeURIComponent(oldId)}/rename`,
      {
        method: "POST",
        body: JSON.stringify({
          expected_hash: hashes[oldId],
          new_id: newId,
        }),
      }
    );
    hashes[newId] = renamed.content_hash;
    schemaVersions[newId] = schemaVersions[oldId] ?? 1;
    delete hashes[oldId];
    delete schemaVersions[oldId];
    previousIds.delete(oldId);
    previousIds.add(newId);
    if (
      comparable(nextProfiles[newId])
      !== comparable(profileFromCharacter(renamed.character))
    ) {
      const updated = await api(`/characters/${encodeURIComponent(newId)}`, {
        method: "PUT",
        body: JSON.stringify({
          expected_hash: hashes[newId],
          character: profilePayload(newId, nextProfiles[newId]),
        }),
      });
      hashes[newId] = updated.content_hash;
    }
  } else {
    for (const id of added) {
      const created = await api("/characters", {
        method: "POST",
        body: JSON.stringify(profilePayload(id, nextProfiles[id])),
      });
      hashes[id] = created.content_hash;
      schemaVersions[id] = created.character.schema_version;
    }
    for (const id of deleted) {
      await api(
        `/characters/${encodeURIComponent(id)}?expected_hash=${encodeURIComponent(
          hashes[id]
        )}`,
        {method: "DELETE"}
      );
      delete hashes[id];
      delete schemaVersions[id];
    }
  }

  for (const id of Object.keys(nextProfiles)) {
    if (!previousIds.has(id) || !Object.hasOwn(persistedProfiles, id)) continue;
    if (comparable(nextProfiles[id]) === comparable(persistedProfiles[id])) {
      continue;
    }
    const updated = await api(`/characters/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({
        expected_hash: hashes[id],
        character: profilePayload(id, nextProfiles[id]),
      }),
    });
    hashes[id] = updated.content_hash;
  }
  await loadLibrary();
  return true;
}

const editor = createCharacterEditor({
  root,
  onScenarioChange: async (scenario) => {
    try {
      return await persistScenario(scenario);
    } catch (error) {
      await loadLibrary();
      throw error;
    }
  },
});

search.addEventListener("input", (event) => {
  editor.setFilter(event.target.value);
});

importInput.addEventListener("change", async (event) => {
  const [file] = event.target.files;
  if (!file) return;
  try {
    const character = JSON.parse(await file.text());
    await api("/characters", {
      method: "POST",
      body: JSON.stringify(character),
    });
    await loadLibrary();
  } catch (error) {
    root.querySelector('[data-role="profile-status"]').textContent =
      `Could not import character: ${error.message}`;
  } finally {
    event.target.value = "";
  }
});

exportButton.addEventListener("click", () => {
  const selected = editor.getSelected();
  if (!selected) return;
  const character = profilePayload(selected.id, selected.profile);
  const blob = new Blob(
    [`${JSON.stringify(character, null, 2)}\n`],
    {type: "application/json"}
  );
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${selected.id}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
});

loadLibrary().catch((error) => {
  root.querySelector('[data-role="profile-status"]').textContent =
    `Could not load character library: ${error.message}`;
});
