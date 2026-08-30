import {api} from "./api-client.js";
import {
  TELEMETRY_SCHEMA_VERSION,
  finiteNumber,
  isObject,
  normalizeCoordinate,
  normalizeEnvelope,
  optionalString,
} from "./protocol.js";
import {
  UI_STATES,
  controlAvailability,
  uiStateForRunStatus,
} from "./ui-state.js";

const state = {
  uiState: UI_STATES.EMPTY,
  loadedScenario: null,
  loadedScenarioName: null,
  scenarioId: null,
  scenarioRevision: 0,
  characterAssignments: {},
  runId: null,
  runStatus: "created",
  bootstrap: null,
  snapshot: null,
  selectedAgentId: null,
  lastSequence: 0,
  lastDomainEventOffset: 0,
  lastSnapshotRevision: 0,
  recoveringTelemetry: false,
  socket: null,
  reconnectTimer: null,
  reconnectAttempt: 0,
  intentionalClose: false,
  events: [],
  eventIds: new Set(),
  filter: "all",
  search: "",
  eventOrder: "newest",
  autoScroll: true,
  selectedEvent: null,
  eventHistoryTotal: 0,
  eventHistoryOffset: 0,
  sensorEffects: {},
  speechBubbles: {},
  recentPerceptions: {},
  overlayOptions: {
    names: true,
    paths: true,
    speech: true,
    vision: true,
    hearing: true,
    selectedVisibility: false,
    debugDestinations: false,
  },
};

const elements = Object.fromEntries(
  [
    "world-canvas", "empty-world", "connection-dot", "connection-label",
    "sequence-label", "clock-label", "protocol-warning", "agent-select",
    "agent-location", "satiety-value", "satiety-gauge", "energy-value",
    "energy-gauge", "stress-value", "stress-gauge", "activity-value",
    "system1-value", "drive-value", "destination-value", "memory-value",
    "plan-list", "character-profile-text", "vitals-form",
    "seeing-now", "recently-heard", "recent-observations",
    "mutate-satiety", "mutate-energy",
    "mutate-stress", "scenario-file", "load-example-button", "start-button",
    "pause-button",
    "resume-button", "step-button", "stop-button", "speed-select",
    "scenario-label", "run-label", "control-status",
    "character-assignment-panel", "character-assignments",
    "event-filter", "event-search", "event-order", "auto-scroll",
    "load-older-events", "expand-log", "clear-log", "event-log", "event-count",
    "event-detail-dialog", "event-detail-title", "event-detail-meta",
    "event-detail-text", "close-event-detail", "copy-event-text",
    "copy-event-json", "conversation-transcript",
    "overlay-names", "overlay-paths", "overlay-speech", "overlay-vision",
    "overlay-hearing", "overlay-selected-visibility",
    "overlay-debug-destinations",
  ].map((id) => [id, document.getElementById(id)])
);

const canvas = elements["world-canvas"];
const context = canvas.getContext("2d");

function normalizeAgent(raw, fallbackId = "unknown-agent", staticAgent = null) {
  const agent = isObject(raw) ? raw : {};
  const homeostasis = isObject(agent.homeostasis) ? agent.homeostasis : {};
  const movement = isObject(agent.movement) ? agent.movement : {};
  const system1 = isObject(agent.system1) ? agent.system1 : {};
  const plan = isObject(agent.plan) ? agent.plan : {};
  const memory = isObject(agent.memory) ? agent.memory : {};
  const profile = isObject(agent.character_profile)
    ? agent.character_profile
    : isObject(staticAgent?.character_profile)
      ? staticAgent.character_profile
      : {};
  const perception = isObject(agent.perception) ? agent.perception : {};
  return {
    id: optionalString(agent.id) ?? fallbackId,
    displayName: optionalString(profile.display_name) ?? optionalString(agent.id) ?? fallbackId,
    characterProfile: profile,
    position: normalizeCoordinate(agent.position),
    homeostasis: {
      satiety: finiteNumber(
        homeostasis.satiety,
        100 - finiteNumber(homeostasis.hunger, 0)
      ),
      energy: finiteNumber(homeostasis.energy, 0),
      stress: finiteNumber(homeostasis.stress, 0),
    },
    activity: optionalString(agent.activity) ?? "UNKNOWN",
    movement: {
      destination: normalizeCoordinate(movement.destination),
      path: Array.isArray(movement.path)
        ? movement.path.map(normalizeCoordinate).filter(Boolean)
        : [],
    },
    system1: {
      state: optionalString(system1.state) ?? "UNKNOWN",
      activeDrive: optionalString(system1.active_drive),
      targetStationId: optionalString(system1.target_station_id),
    },
    plan: {
      current: isObject(plan.current) ? plan.current : null,
      queue: Array.isArray(plan.queue) ? plan.queue.filter(isObject) : [],
      remainingDuration: Number.isFinite(Number(plan.remaining_duration))
        ? Number(plan.remaining_duration)
        : null,
    },
    memoryCount: finiteNumber(memory.count, 0),
    perception: {
      inboxCount: finiteNumber(perception.inbox_count, 0),
      visibleNow: Array.isArray(perception.visible_now)
        ? perception.visible_now.filter((value) => typeof value === "string")
        : [],
      knownCharacterCount: finiteNumber(perception.known_character_count, 0),
    },
  };
}

function normalizeSnapshot(raw) {
  if (!isObject(raw)) throw new Error("Snapshot payload is not an object");
  const worldRaw = isObject(raw.world) ? raw.world : null;
  const bootstrapWorld = isObject(state.bootstrap?.world)
    ? state.bootstrap.world
    : null;
  const previousWorld = state.snapshot?.world ?? null;
  const agentValues = Array.isArray(raw.agents)
    ? raw.agents
    : isObject(raw.agents)
      ? Object.entries(raw.agents).map(([id, value]) => ({id, ...value}))
      : [];
  let world = worldRaw && Number.isFinite(Number(worldRaw.width))
    ? {
        width: Math.max(1, finiteNumber(worldRaw.width, 1)),
        height: Math.max(1, finiteNumber(worldRaw.height, 1)),
        blocked: Array.isArray(worldRaw.blocked)
          ? worldRaw.blocked.map(normalizeCoordinate).filter(Boolean)
          : [],
        zones: Array.isArray(worldRaw.zones)
          ? worldRaw.zones.filter(isObject).map((zone, index) => ({
              id: optionalString(zone.id) ?? `zone-${index}`,
              name: optionalString(zone.name) ?? optionalString(zone.id) ?? "Zone",
              type: optionalString(zone.type) ?? "UNKNOWN",
              tiles: Array.isArray(zone.tiles)
                ? zone.tiles.map(normalizeCoordinate).filter(Boolean)
                : [],
            }))
          : [],
        stations: Array.isArray(worldRaw.stations)
          ? worldRaw.stations.filter(isObject).map((station, index) => ({
              id: optionalString(station.id) ?? `station-${index}`,
              name: optionalString(station.name) ?? "Station",
              position: normalizeCoordinate(station.position),
              actions: Array.isArray(station.actions)
                ? station.actions.filter((item) => typeof item === "string")
                : [],
              available: station.available !== false,
            }))
          : [],
      }
    : previousWorld ?? (bootstrapWorld ? normalizeStaticWorld(bootstrapWorld) : null);
  if (world && Array.isArray(worldRaw?.station_states)) {
    const availability = new Map(
      worldRaw.station_states
        .filter(isObject)
        .map((station) => [station.id, station.available !== false])
    );
    world = {
      ...world,
      stations: world.stations.map((station) => ({
        ...station,
        available: availability.has(station.id)
          ? availability.get(station.id)
          : station.available,
      })),
    };
  }
  const staticAgents = new Map(
    (state.bootstrap?.agents ?? [])
      .filter(isObject)
      .map((agent) => [agent.id, agent])
  );
  return {
    status: optionalString(raw.status) ?? state.runStatus,
    speed: finiteNumber(raw.speed, 1),
    tick: Math.max(0, finiteNumber(raw.tick, 0)),
    simulationTime: Math.max(0, finiteNumber(raw.simulation_time, 0)),
    world,
    agents: agentValues.map((agent, index) =>
      normalizeAgent(
        agent,
        `agent-${index + 1}`,
        staticAgents.get(agent.id)
      )
    ),
  };
}

function normalizeStaticWorld(worldRaw) {
  return {
    width: Math.max(1, finiteNumber(worldRaw.width, 1)),
    height: Math.max(1, finiteNumber(worldRaw.height, 1)),
    blocked: Array.isArray(worldRaw.blocked)
      ? worldRaw.blocked.map(normalizeCoordinate).filter(Boolean)
      : [],
    zones: Array.isArray(worldRaw.zones)
      ? worldRaw.zones.filter(isObject).map((zone, index) => ({
          id: optionalString(zone.id) ?? `zone-${index}`,
          name: optionalString(zone.name) ?? optionalString(zone.id) ?? "Zone",
          type: optionalString(zone.type) ?? "UNKNOWN",
          tiles: Array.isArray(zone.tiles)
            ? zone.tiles.map(normalizeCoordinate).filter(Boolean)
            : [],
        }))
      : [],
    stations: Array.isArray(worldRaw.stations)
      ? worldRaw.stations.filter(isObject).map((station, index) => ({
          id: optionalString(station.id) ?? `station-${index}`,
          name: optionalString(station.name) ?? "Station",
          position: normalizeCoordinate(station.position),
          actions: Array.isArray(station.actions)
            ? station.actions.filter((item) => typeof item === "string")
            : [],
          available: station.available !== false,
        }))
      : [],
  };
}

async function loadScenario(scenario, sourceLabel = "JSON file") {
  if (!isObject(scenario)) throw new Error("Scenario must be a JSON object");
  state.uiState = UI_STATES.SCENARIO_LOADING;
  state.loadedScenario = structuredClone(scenario);
  const revision = ++state.scenarioRevision;
  state.loadedScenarioName = optionalString(scenario.name) ?? "Unnamed scenario";
  initializeCharacterAssignments();
  renderCharacterAssignments();
  setControlStatus(`Validating ${state.loadedScenarioName}...`);
  updateControls();
  try {
    const created = await api("/simulation/scenarios", {
      method: "POST",
      body: JSON.stringify(buildAssignedScenario()),
    });
    if (revision !== state.scenarioRevision) return;
    state.scenarioId = created.scenario_id;
    state.uiState = UI_STATES.SCENARIO_READY;
    elements["scenario-label"].textContent =
      `Ready: ${state.loadedScenarioName} (${sourceLabel})`;
    setControlStatus("Scenario loaded. Assign characters if needed, then press Start.");
    updateControls();
  } catch (error) {
    if (revision !== state.scenarioRevision) return;
    state.scenarioId = null;
    state.uiState = UI_STATES.ERROR;
    setControlStatus(`Scenario invalid: ${error.message}`, true);
    addLocalEvent("ui.scenario_invalid", {message: error.message}, "error");
    updateControls();
  }
}

async function loadExample() {
  try {
    const response = await fetch("/ui/demo.json", {cache: "no-store"});
    if (!response.ok) throw new Error(`Example HTTP ${response.status}`);
    await loadScenario(await response.json(), "bundled example");
  } catch (error) {
    setControlStatus(`Example unavailable: ${error.message}`, true);
  }
}

async function startLoadedScenario() {
  if (!state.loadedScenario || !state.scenarioId) return;
  state.uiState = UI_STATES.RUN_STARTING;
  setControlStatus("Starting simulation...");
  updateControls();
  try {
    const run = await api("/simulation/runs", {
      method: "POST",
      body: JSON.stringify({
        scenario_id: state.scenarioId,
        realtime: true,
        speed: finiteNumber(elements["speed-select"].value, 1),
      }),
    });
    closeSocket(true);
    resetRunState();
    state.runId = run.run_id;
    state.runStatus = run.status;
    state.uiState = UI_STATES.RUNNING;
    state.intentionalClose = false;
    elements["run-label"].textContent = `${run.run_id} / running`;
    setControlStatus("Simulation running.");
    updateControls();
    connectStream();
  } catch (error) {
    state.uiState = UI_STATES.SCENARIO_READY;
    setControlStatus(`Start failed: ${error.message}`, true);
    addLocalEvent("ui.start_failed", {message: error.message}, "error");
    updateControls();
  }
}

function initializeCharacterAssignments() {
  state.characterAssignments = {};
  const profiles = Object.keys(state.loadedScenario?.character_profiles ?? {});
  if (!profiles.length) return;
  const entities = Array.isArray(state.loadedScenario?.entities)
    ? state.loadedScenario.entities
    : [];
  entities.forEach((entity, index) => {
    if (!isObject(entity)) return;
    const components = isObject(entity.components) ? entity.components : {};
    const current = isObject(components.character_profile)
      ? optionalString(components.character_profile.profile_ref)
      : null;
    state.characterAssignments[entity.id] =
      current && profiles.includes(current)
        ? current
        : profiles[index % profiles.length];
  });
}

function renderCharacterAssignments() {
  const container = elements["character-assignments"];
  container.replaceChildren();
  const profiles = state.loadedScenario?.character_profiles ?? {};
  const profileEntries = Object.entries(profiles);
  const assignments = Object.entries(state.characterAssignments);
  elements["character-assignment-panel"].hidden =
    !profileEntries.length || !assignments.length;
  for (const [entityId, selectedProfile] of assignments) {
    const row = document.createElement("label");
    row.className = "character-assignments__row";
    const slot = document.createElement("span");
    slot.textContent = entityId;
    const select = document.createElement("select");
    select.dataset.entityId = entityId;
    for (const [profileId, profile] of profileEntries) {
      const name = profileDisplayName(profile, profileId);
      select.add(new Option(`${name} (${profileId})`, profileId));
    }
    select.value = selectedProfile;
    select.addEventListener("change", async (event) => {
      state.characterAssignments[entityId] = event.target.value;
      await revalidateAssignedScenario();
    });
    row.append(slot, select);
    container.append(row);
  }
}

async function revalidateAssignedScenario() {
  const revision = ++state.scenarioRevision;
  state.uiState = UI_STATES.SCENARIO_LOADING;
  state.scenarioId = null;
  setControlStatus("Validating character assignments...");
  updateControls();
  try {
    const created = await api("/simulation/scenarios", {
      method: "POST",
      body: JSON.stringify(buildAssignedScenario()),
    });
    if (revision !== state.scenarioRevision) return;
    state.scenarioId = created.scenario_id;
    state.uiState = UI_STATES.SCENARIO_READY;
    setControlStatus("Character assignments ready.");
  } catch (error) {
    if (revision !== state.scenarioRevision) return;
    state.uiState = UI_STATES.ERROR;
    setControlStatus(`Assignment invalid: ${error.message}`, true);
  }
  updateControls();
}

function buildAssignedScenario() {
  const scenario = structuredClone(state.loadedScenario);
  if (!scenario) throw new Error("No scenario loaded");
  for (const entity of scenario.entities ?? []) {
    const profileId = state.characterAssignments[entity.id];
    if (!profileId) continue;
    entity.components ??= {};
    entity.components.character_profile = {profile_ref: profileId};
  }
  return scenario;
}

function profileDisplayName(profile, fallback) {
  return optionalString(profile?.identity?.display_name)
    ?? optionalString(profile?.display_name)
    ?? fallback;
}

function connectStream() {
  if (!state.runId || state.intentionalClose) return;
  clearTimeout(state.reconnectTimer);
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const url = `${scheme}://${location.host}/simulation/runs/${encodeURIComponent(
    state.runId
  )}/stream?after_sequence=${state.lastSequence}&after_snapshot_revision=${
    state.lastSnapshotRevision
  }`;
  setConnection("warning", "Connecting telemetry...");
  const socket = new WebSocket(url);
  state.socket = socket;
  socket.addEventListener("open", () => {
    state.reconnectAttempt = 0;
    setConnection("online", "Telemetry connected");
  });
  socket.addEventListener("message", (event) => {
    try {
      handleEnvelope(normalizeEnvelope(JSON.parse(event.data)));
    } catch (error) {
      showProtocolWarning(`Ignored malformed telemetry: ${error.message}`);
      addLocalEvent("ui.protocol_error", {message: error.message}, "error");
    }
  });
  socket.addEventListener("close", () => {
    if (state.socket === socket) state.socket = null;
    if (
      !state.intentionalClose
      && !state.recoveringTelemetry
      && state.runId
    ) {
      scheduleReconnect();
    }
  });
  socket.addEventListener("error", () => {
    setConnection("offline", "Telemetry connection error");
  });
}

function scheduleReconnect() {
  const delay = Math.min(10000, 500 * 2 ** state.reconnectAttempt);
  state.reconnectAttempt += 1;
  setConnection("warning", `Reconnecting in ${(delay / 1000).toFixed(1)}s`);
  state.reconnectTimer = setTimeout(async () => {
    connectStream();
  }, delay);
}

function closeSocket(intentional) {
  state.intentionalClose = intentional;
  clearTimeout(state.reconnectTimer);
  if (state.socket) {
    state.socket.close();
    state.socket = null;
  }
}

function handleEnvelope(message) {
  if (
    message.schemaVersion
    && message.schemaVersion !== TELEMETRY_SCHEMA_VERSION
  ) {
    showProtocolWarning(
      `Unsupported telemetry schema: ${message.schemaVersion}`
    );
    return;
  }
  if (message.type === "hello") {
    state.bootstrap = isObject(message.payload.bootstrap)
      ? message.payload.bootstrap
      : state.bootstrap;
    clearProtocolWarning();
    return;
  }
  if (message.type === "resync_required") {
    void recoverTelemetry();
    return;
  }
  if (message.type === "world_snapshot") {
    if (
      message.snapshotRevision !== null
      && message.snapshotRevision <= state.lastSnapshotRevision
    ) {
      return;
    }
    try {
      state.snapshot = normalizeSnapshot(
        isObject(message.payload.snapshot) ? message.payload.snapshot : message.payload
      );
      state.runStatus = state.snapshot.status;
      state.uiState = uiStateForRunStatus(
        state.runStatus,
        Boolean(state.loadedScenario)
      );
      state.lastSnapshotRevision = Math.max(
        state.lastSnapshotRevision,
        message.snapshotRevision ?? 0
      );
      clearProtocolWarning();
      render();
    } catch (error) {
      showProtocolWarning(`Snapshot rejected: ${error.message}`);
    }
    return;
  }
  if (message.sequence <= state.lastSequence) return;
  if (state.lastSequence && message.sequence > state.lastSequence + 1) {
    showProtocolWarning(
      `Telemetry gap: expected ${state.lastSequence + 1}, received ${message.sequence}`
    );
    void recoverTelemetry();
    return;
  }
  state.lastSequence = message.sequence;
  elements["sequence-label"].textContent = `seq ${message.sequence}`;

  if (message.type === "simulation_status") {
    state.runStatus = optionalString(message.payload.status) ?? state.runStatus;
    state.uiState = uiStateForRunStatus(
      state.runStatus,
      Boolean(state.loadedScenario)
    );
    updateRunLabel();
    setControlStatus(statusMessage(state.runStatus));
    updateControls();
    return;
  }

  const event = isObject(message.payload.event)
    ? message.payload.event
    : {
        event_type: message.type,
        simulation_tick: message.tick,
        simulation_time: message.simulationTime,
        payload: message.payload,
      };
  addEvent(event, message.domainEventOffset);
}

async function refreshSnapshot() {
  if (!state.runId) return;
  try {
    const response = await api(
      `/simulation/runs/${encodeURIComponent(state.runId)}/snapshot`
    );
    state.snapshot = normalizeSnapshot(response.snapshot);
    state.lastSnapshotRevision = Math.max(
      state.lastSnapshotRevision,
      finiteNumber(response.snapshot_revision, 0)
    );
    render();
  } catch (error) {
    setConnection("offline", `Snapshot refresh failed: ${error.message}`);
  }
}

async function recoverTelemetry() {
  if (!state.runId || state.recoveringTelemetry) return;
  state.recoveringTelemetry = true;
  showProtocolWarning("Recovering missed telemetry...");
  try {
    const snapshotResponse = await api(
      `/simulation/runs/${encodeURIComponent(state.runId)}/snapshot`
    );
    state.snapshot = normalizeSnapshot(snapshotResponse.snapshot);
    const targetEventOffset = finiteNumber(
      snapshotResponse.domain_event_offset,
      state.lastDomainEventOffset
    );
    let offset = state.lastDomainEventOffset;
    while (offset < targetEventOffset) {
      const page = await api(
        `/simulation/runs/${encodeURIComponent(state.runId)}/events?offset=${
          offset
        }&limit=1000`
      );
      for (let index = 0; index < (page.events ?? []).length; index += 1) {
        addEvent(
          page.events[index],
          offset + index + 1,
          {animate: false, render: false}
        );
      }
      const nextOffset = finiteNumber(page.next_offset, offset);
      if (nextOffset <= offset) break;
      offset = nextOffset;
    }
    state.lastDomainEventOffset = Math.max(
      state.lastDomainEventOffset,
      offset
    );
    state.lastSequence = finiteNumber(
      snapshotResponse.sequence,
      state.lastSequence
    );
    state.lastSnapshotRevision = finiteNumber(
      snapshotResponse.snapshot_revision,
      state.lastSnapshotRevision
    );
    renderEventLog();
    renderTranscript();
    render();
    clearProtocolWarning();
  } catch (error) {
    showProtocolWarning(`Telemetry recovery failed: ${error.message}`);
  } finally {
    state.recoveringTelemetry = false;
    if (!state.intentionalClose && state.runId) {
      if (state.socket) state.socket.close();
      scheduleReconnect();
    }
  }
}

async function control(action, body = null) {
  if (!state.runId) return;
  state.uiState = {
    pause: UI_STATES.PAUSING,
    resume: UI_STATES.RESUMING,
    step: UI_STATES.STEPPING,
    stop: UI_STATES.STOPPING,
    speed: UI_STATES.SPEED_CHANGING,
  }[action] ?? state.uiState;
  setControlStatus(`${action[0].toUpperCase()}${action.slice(1)} in progress...`);
  updateControls();
  try {
    const result = await api(
      `/simulation/runs/${encodeURIComponent(state.runId)}/${action}`,
      {
        method: "POST",
        body: body === null ? undefined : JSON.stringify(body),
      }
    );
    if (typeof result.status === "string") state.runStatus = result.status;
    await refreshRunState();
    if (action === "stop") {
      closeSocket(true);
      setConnection("warning", "Run stopped; scenario remains loaded");
    }
  } catch (error) {
    state.uiState = uiStateForRunStatus(
      state.runStatus,
      Boolean(state.loadedScenario)
    );
    setControlStatus(`${action} failed: ${error.message}`, true);
    addLocalEvent(`ui.${action}_failed`, {message: error.message}, "error");
    updateControls();
  }
}

async function refreshRunState() {
  if (!state.runId) return;
  const run = await api(`/simulation/runs/${encodeURIComponent(state.runId)}`);
  state.runStatus = run.status;
  state.uiState = uiStateForRunStatus(
    run.status,
    Boolean(state.loadedScenario)
  );
  await refreshSnapshot();
  updateRunLabel();
  setControlStatus(statusMessage(run.status));
  updateControls();
}

async function mutateVitals(event) {
  event.preventDefault();
  if (!state.runId || !state.selectedAgentId) return;
  const values = {};
  for (const name of ["satiety", "energy", "stress"]) {
    const input = elements[`mutate-${name}`];
    if (input.value !== "") values[name] = finiteNumber(input.value);
  }
  if (!Object.keys(values).length) {
    addLocalEvent("ui.mutation_skipped", {message: "Supply at least one vital"}, "error");
    return;
  }
  try {
    await api(
      `/simulation/runs/${encodeURIComponent(state.runId)}/agents/${encodeURIComponent(
        state.selectedAgentId
      )}/vitals`,
      {method: "PATCH", body: JSON.stringify(values)}
    );
    event.target.reset();
    await refreshSnapshot();
  } catch (error) {
    addLocalEvent("ui.mutation_failed", {message: error.message}, "error");
  }
}

function render() {
  const snapshot = state.snapshot;
  elements["clock-label"].textContent = snapshot
    ? `tick ${snapshot.tick} / ${snapshot.simulationTime.toFixed(1)}s`
    : "tick 0 / 0.0s";
  elements["empty-world"].hidden = Boolean(snapshot?.world);
  syncAgentSelection();
  renderInspector();
  drawWorld();
  updateControls();
  document.querySelector(".world-panel")?.classList.toggle(
    "paused",
    state.runStatus === "paused"
  );
}

function syncAgentSelection() {
  const agents = state.snapshot?.agents ?? [];
  if (!agents.some((agent) => agent.id === state.selectedAgentId)) {
    state.selectedAgentId = agents[0]?.id ?? null;
  }
  const previous = elements["agent-select"].value;
  elements["agent-select"].replaceChildren();
  if (!agents.length) {
    elements["agent-select"].add(new Option("No agents", ""));
  } else {
    for (const agent of agents) {
      elements["agent-select"].add(
        new Option(`${agent.displayName} (${agent.id})`, agent.id)
      );
    }
  }
  elements["agent-select"].value =
    state.selectedAgentId ?? (previous && agents.some((a) => a.id === previous) ? previous : "");
}

function selectedAgent() {
  return state.snapshot?.agents.find((agent) => agent.id === state.selectedAgentId) ?? null;
}

function renderInspector() {
  const agent = selectedAgent();
  const position = agent?.position;
  elements["agent-location"].textContent = agent
    ? `${agent.displayName} [${agent.id}]${
        position ? ` at (${position.x}, ${position.y})` : " / no position"
      }`
    : "No agent selected";
  setGauge("satiety", agent?.homeostasis.satiety);
  setGauge("energy", agent?.homeostasis.energy);
  setGauge("stress", agent?.homeostasis.stress);
  elements["activity-value"].textContent = agent?.activity ?? "--";
  elements["system1-value"].textContent = agent?.system1.state ?? "--";
  elements["drive-value"].textContent = agent?.system1.activeDrive ?? "none";
  elements["destination-value"].textContent = agent?.movement.destination
    ? `(${agent.movement.destination.x}, ${agent.movement.destination.y})`
    : "none";
  elements["memory-value"].textContent = agent ? String(agent.memoryCount) : "--";
  elements["character-profile-text"].textContent =
    optionalString(agent?.characterProfile?.description)
    ?? "No character profile available";
  renderPerceptionInspector(agent);
  renderPlan(agent);
}

function renderPerceptionInspector(agent) {
  const visibleNames = (agent?.perception.visibleNow ?? []).map(
    (agentId) => agentDisplayName(agentId)
  );
  elements["seeing-now"].textContent =
    visibleNames.length ? visibleNames.join(", ") : "Nobody";
  const recent = state.recentPerceptions[agent?.id] ?? [];
  const heard = recent.find((item) => item.modality === "auditory");
  elements["recently-heard"].textContent = heard?.text ?? "Nothing";
  const list = elements["recent-observations"];
  list.replaceChildren();
  if (!recent.length) {
    const item = document.createElement("li");
    item.className = "muted";
    item.textContent = "No observations";
    list.append(item);
    return;
  }
  for (const observation of recent.slice(0, 8)) {
    const item = document.createElement("li");
    item.textContent = observation.text;
    list.append(item);
  }
}

function agentDisplayName(agentId) {
  return state.snapshot?.agents.find((agent) => agent.id === agentId)
    ?.displayName ?? agentId;
}

function setGauge(name, rawValue) {
  const value = Math.max(0, Math.min(100, finiteNumber(rawValue, 0)));
  elements[`${name}-value`].textContent = rawValue === undefined ? "--" : value.toFixed(1);
  elements[`${name}-gauge`].value = value;
}

function renderPlan(agent) {
  const list = elements["plan-list"];
  list.replaceChildren();
  const actions = [];
  if (agent?.plan.current) actions.push({...agent.plan.current, current: true});
  actions.push(...(agent?.plan.queue ?? []));
  if (!actions.length) {
    const item = document.createElement("li");
    item.className = "muted";
    item.textContent = "No plan available";
    list.append(item);
    return;
  }
  for (const action of actions) {
    const item = document.createElement("li");
    const target = optionalString(action.target) ? ` -> ${action.target}` : "";
    const duration = Number.isFinite(Number(action.duration))
      ? ` / ${Number(action.duration).toFixed(0)}s`
      : "";
    item.textContent = `${action.current ? "Active: " : ""}${
      optionalString(action.action) ?? "UNKNOWN"
    }${target}${duration}`;
    list.append(item);
  }
}

function drawWorld() {
  const rect = canvas.getBoundingClientRect();
  const ratio = Math.max(1, window.devicePixelRatio || 1);
  const width = Math.max(1, Math.round(rect.width * ratio));
  const height = Math.max(1, Math.round(rect.height * ratio));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, rect.width, rect.height);
  const world = state.snapshot?.world;
  if (!world) return;

  const padding = 30;
  const tile = Math.max(
    4,
    Math.min((rect.width - padding * 2) / world.width, (rect.height - padding * 2) / world.height)
  );
  const originX = (rect.width - tile * world.width) / 2;
  const originY = (rect.height - tile * world.height) / 2;

  context.fillStyle = "#0a141e";
  context.fillRect(originX, originY, tile * world.width, tile * world.height);
  for (const zone of world.zones) {
    context.fillStyle = zoneColor(zone.type);
    for (const coordinate of zone.tiles) {
      context.fillRect(originX + coordinate.x * tile, originY + coordinate.y * tile, tile, tile);
    }
    if (zone.tiles.length) {
      const center = zone.tiles.reduce(
        (sum, coordinate) => ({x: sum.x + coordinate.x, y: sum.y + coordinate.y}),
        {x: 0, y: 0}
      );
      context.fillStyle = "#9eb2c4";
      context.font = `${Math.max(9, tile * 0.22)}px system-ui`;
      context.textAlign = "center";
      context.fillText(
        zone.name,
        originX + (center.x / zone.tiles.length + 0.5) * tile,
        originY + (center.y / zone.tiles.length + 0.55) * tile
      );
    }
  }
  context.strokeStyle = "rgba(119, 145, 167, .22)";
  context.lineWidth = 1;
  for (let x = 0; x <= world.width; x += 1) {
    context.beginPath();
    context.moveTo(originX + x * tile, originY);
    context.lineTo(originX + x * tile, originY + world.height * tile);
    context.stroke();
  }
  for (let y = 0; y <= world.height; y += 1) {
    context.beginPath();
    context.moveTo(originX, originY + y * tile);
    context.lineTo(originX + world.width * tile, originY + y * tile);
    context.stroke();
  }
  context.fillStyle = "#354554";
  for (const coordinate of world.blocked) {
    context.fillRect(
      originX + coordinate.x * tile + 1,
      originY + coordinate.y * tile + 1,
      tile - 2,
      tile - 2
    );
  }
  for (const station of world.stations) {
    if (!station.position) continue;
    const x = originX + (station.position.x + 0.5) * tile;
    const y = originY + (station.position.y + 0.5) * tile;
    context.fillStyle = station.available ? "#d9c46c" : "#6c7780";
    context.fillRect(x - tile * 0.22, y - tile * 0.22, tile * 0.44, tile * 0.44);
    context.fillStyle = "#101820";
    context.font = `700 ${Math.max(8, tile * 0.24)}px system-ui`;
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(stationLabel(station), x, y);
  }
  for (const agent of state.snapshot.agents) {
    drawAgent(agent, originX, originY, tile);
  }
  canvas.dataset.originX = String(originX);
  canvas.dataset.originY = String(originY);
  canvas.dataset.tile = String(tile);
}

function drawAgent(agent, originX, originY, tile) {
  if (!agent.position) return;
  const points = [agent.position, ...agent.movement.path];
  if (state.overlayOptions.paths && points.length > 1) {
    context.strokeStyle = "#f4be5b";
    context.lineWidth = Math.max(2, tile * 0.08);
    context.setLineDash([tile * 0.18, tile * 0.12]);
    context.beginPath();
    points.forEach((point, index) => {
      const x = originX + (point.x + 0.5) * tile;
      const y = originY + (point.y + 0.5) * tile;
      index ? context.lineTo(x, y) : context.moveTo(x, y);
    });
    context.stroke();
    context.setLineDash([]);
  }
  if (
    state.overlayOptions.debugDestinations
    && agent.movement.destination
  ) {
    const x = originX + (agent.movement.destination.x + 0.5) * tile;
    const y = originY + (agent.movement.destination.y + 0.5) * tile;
    context.strokeStyle = "#f4be5b";
    context.lineWidth = 2;
    context.strokeRect(x - tile * 0.3, y - tile * 0.3, tile * 0.6, tile * 0.6);
  }
  const x = originX + (agent.position.x + 0.5) * tile;
  const y = originY + (agent.position.y + 0.5) * tile;
  context.beginPath();
  context.arc(x, y, tile * 0.27, 0, Math.PI * 2);
  context.fillStyle = agent.id === state.selectedAgentId ? "#67d7da" : "#8fa8bc";
  context.fill();
  context.lineWidth = 2;
  context.strokeStyle =
    agent.system1.state !== "NORMAL" ? "#ef7070" : "rgba(255,255,255,.55)";
  context.stroke();
  const selected = selectedAgent();
  if (
    state.overlayOptions.selectedVisibility
    && selected?.perception.visibleNow.includes(agent.id)
  ) {
    context.beginPath();
    context.arc(x, y, tile * 0.36, 0, Math.PI * 2);
    context.strokeStyle = "#54d78b";
    context.lineWidth = 2;
    context.stroke();
  }
  const next = agent.movement.path[0];
  if (next) {
    const dx = next.x - agent.position.x;
    const dy = next.y - agent.position.y;
    context.strokeStyle = "#071019";
    context.lineWidth = Math.max(2, tile * 0.08);
    context.beginPath();
    context.moveTo(x, y);
    context.lineTo(x + dx * tile * 0.22, y + dy * tile * 0.22);
    context.stroke();
  }
  drawAgentOverlays(agent, x, y, tile);
}

function drawAgentOverlays(agent, x, y, tile) {
  const now = Date.now();
  const effects = state.sensorEffects[agent.id] ?? {};
  const bubble = state.speechBubbles[agent.id];
  if (state.overlayOptions.names) {
    context.fillStyle = "#e7edf5";
    context.font = `700 ${Math.max(10, tile * 0.22)}px system-ui`;
    context.textAlign = "center";
    context.textBaseline = "bottom";
    context.fillText(agent.displayName, x, y - tile * 0.34);
  }
  const indicators = [];
  if (state.overlayOptions.vision && agent.perception.visibleNow.length) {
    indicators.push(`👁 ${agent.perception.visibleNow.length}`);
  }
  if (
    state.overlayOptions.hearing
    && effects.auditoryUntil
    && effects.auditoryUntil > now
  ) {
    indicators.push("👂");
    context.beginPath();
    context.arc(x, y, tile * 0.42, 0, Math.PI * 2);
    context.strokeStyle = "rgba(103, 215, 218, .75)";
    context.lineWidth = Math.max(2, tile * 0.05);
    context.stroke();
  }
  if (indicators.length) {
    context.fillStyle = "#d8e8f4";
    context.font = `${Math.max(10, tile * 0.2)}px system-ui`;
    context.textAlign = "center";
    context.textBaseline = "top";
    context.fillText(indicators.join("  "), x, y + tile * 0.34);
  }
  if (
    state.overlayOptions.speech
    && bubble
    && bubble.until > now
  ) {
    drawSpeechBubble(bubble.text, x, y - tile * 0.65, tile);
  }
}

function drawSpeechBubble(text, x, y, tile) {
  const preview = text.length > 72 ? `${text.slice(0, 69)}...` : text;
  const width = Math.min(260, Math.max(90, preview.length * 6.2));
  const height = preview.length > 35 ? 42 : 28;
  const left = x - width / 2;
  const top = y - height;
  context.fillStyle = "rgba(231, 237, 245, .96)";
  context.strokeStyle = "#26394b";
  context.lineWidth = 1;
  context.beginPath();
  context.roundRect(left, top, width, height, Math.max(4, tile * 0.08));
  context.fill();
  context.stroke();
  context.fillStyle = "#071019";
  context.font = `${Math.max(9, tile * 0.16)}px system-ui`;
  context.textAlign = "center";
  context.textBaseline = "middle";
  const lines = preview.length > 35
    ? [preview.slice(0, 35), preview.slice(35)]
    : [preview];
  lines.forEach((line, index) => {
    context.fillText(
      line,
      x,
      top + height / 2 + (index - (lines.length - 1) / 2) * 14
    );
  });
}

function zoneColor(type) {
  const colors = {
    KITCHEN: "rgba(56, 117, 101, .35)",
    BEDROOM: "rgba(86, 83, 139, .35)",
    OFFICE: "rgba(56, 101, 141, .35)",
    LOUNGE: "rgba(134, 87, 57, .35)",
  };
  return colors[String(type).toUpperCase()] ?? "rgba(78, 98, 116, .28)";
}

function stationLabel(station) {
  const labels = {EAT: "F", SLEEP: "B", RELAX: "S", WORK: "D"};
  return labels[station.actions[0]] ?? "?";
}

function addEvent(
  raw,
  domainEventOffset = null,
  {animate = true, render = true} = {}
) {
  const event = isObject(raw) ? raw : {};
  const eventId = optionalString(event.event_id)
    ?? `local:${optionalString(event.event_type) ?? "unknown"}:${
      event.simulation_tick ?? 0
    }:${state.events.length}`;
  if (Number.isInteger(domainEventOffset)) {
    state.lastDomainEventOffset = Math.max(
      state.lastDomainEventOffset,
      domainEventOffset
    );
  }
  if (state.eventIds.has(eventId)) return;
  state.eventIds.add(eventId);
  state.events.push({
    eventId,
    type: optionalString(event.event_type) ?? "unknown",
    tick: finiteNumber(event.simulation_tick, state.snapshot?.tick ?? 0),
    time: finiteNumber(event.simulation_time, state.snapshot?.simulationTime ?? 0),
    agentId: optionalString(event.agent_id),
    payload: isObject(event.payload) ? event.payload : {},
    causationId: optionalString(event.causation_id),
    correlationId: optionalString(event.correlation_id),
    wallTime: optionalString(event.wall_time),
  });
  updateReadableEventState(
    state.events[state.events.length - 1],
    animate
  );
  if (state.events.length > 5000) {
    const removed = state.events.splice(0, state.events.length - 5000);
    removed.forEach((item) => state.eventIds.delete(item.eventId));
  }
  if (render) renderEventLog();
}

function updateReadableEventState(event, animate) {
  const now = Date.now();
  if (event.type === "perception.delivered" && event.agentId) {
    const modality = optionalString(event.payload.modality) ?? "unknown";
    const subject = optionalString(event.payload.subject_id);
    const factType = optionalString(event.payload.fact_type) ?? "perception";
    if (animate) {
      state.sensorEffects[event.agentId] ??= {};
      state.sensorEffects[event.agentId][`${modality}Until`] = now + 1800;
    }
    const text = modality === "auditory"
      ? `Heard ${subject ? agentDisplayName(subject) : "something"}`
      : `${factType.replaceAll("_", " ")}${
          subject ? `: ${agentDisplayName(subject)}` : ""
        }`;
    recordRecentPerception(event.agentId, {modality, text, tick: event.tick});
  }
  if (event.type === "speech.delivered" && event.agentId) {
    const text = String(event.payload.text ?? "");
    if (animate) {
      state.speechBubbles[event.agentId] = {
        text,
        until: now + Math.min(8000, Math.max(2500, text.length * 70)),
      };
    }
    const recipients = Array.isArray(event.payload.recipient_ids)
      ? event.payload.recipient_ids
      : [];
    for (const recipientId of recipients) {
      if (typeof recipientId !== "string") continue;
      if (animate) {
        state.sensorEffects[recipientId] ??= {};
        state.sensorEffects[recipientId].auditoryUntil = now + 1800;
      }
      recordRecentPerception(recipientId, {
        modality: "auditory",
        text: `${agentDisplayName(event.agentId)}: "${text}"`,
        tick: event.tick,
      });
    }
  }
  if (animate && event.type === "dialogue.generated" && event.agentId) {
    const text = String(event.payload.text ?? "");
    state.speechBubbles[event.agentId] = {
      text,
      until: now + Math.min(8000, Math.max(2500, text.length * 70)),
    };
  }
  if (animate) {
    renderTranscript();
    renderInspector();
    drawWorld();
    scheduleOverlayExpiry();
  }
}

function recordRecentPerception(agentId, observation) {
  state.recentPerceptions[agentId] ??= [];
  state.recentPerceptions[agentId].unshift(observation);
  state.recentPerceptions[agentId] =
    state.recentPerceptions[agentId].slice(0, 20);
}

let overlayExpiryTimer = null;
function scheduleOverlayExpiry() {
  clearTimeout(overlayExpiryTimer);
  const expiries = [
    ...Object.values(state.sensorEffects).flatMap((effect) =>
      Object.values(effect).filter(Number.isFinite)
    ),
    ...Object.values(state.speechBubbles).map((bubble) => bubble.until),
  ].filter((expiry) => expiry > Date.now());
  if (!expiries.length) return;
  overlayExpiryTimer = setTimeout(() => {
    drawWorld();
    scheduleOverlayExpiry();
  }, Math.max(50, Math.min(...expiries) - Date.now() + 25));
}

function addLocalEvent(type, payload, category = null) {
  addEvent({
    event_type: type,
    simulation_tick: state.snapshot?.tick ?? 0,
    simulation_time: state.snapshot?.simulationTime ?? 0,
    payload: {...payload, category},
  });
}

function renderEventLog() {
  const visible = state.events.filter(eventMatchesFilter);
  const ordered = state.eventOrder === "oldest"
    ? visible
    : visible.slice().reverse();
  const fragment = document.createDocumentFragment();
  for (const event of ordered) {
    const row = document.createElement("div");
    row.className = `event-row ${eventClass(event.type)}`;
    row.tabIndex = 0;
    row.title = "Open full event detail";
    const tick = document.createElement("span");
    tick.className = "event-row__tick";
    tick.textContent = `t${event.tick}`;
    const type = document.createElement("span");
    type.className = "event-row__type";
    type.textContent = event.type;
    const detail = document.createElement("span");
    detail.className = "event-row__detail";
    detail.textContent = summarizePayload(event.payload, event.agentId);
    row.append(tick, type, detail);
    row.addEventListener("click", () => showEventDetail(event));
    row.addEventListener("keydown", (keyboardEvent) => {
      if (keyboardEvent.key === "Enter" || keyboardEvent.key === " ") {
        keyboardEvent.preventDefault();
        showEventDetail(event);
      }

      function renderTranscript() {
        const transcript = elements["conversation-transcript"];
        const entries = state.events.filter((event) =>
          /^(speech\.delivered|dialogue\.generated)$/.test(event.type)
        );
        transcript.replaceChildren();
        if (!entries.length) {
          const empty = document.createElement("p");
          empty.className = "muted";
          empty.textContent = "No speech yet";
          transcript.append(empty);
          return;
        }
        for (const event of entries.slice(-100).reverse()) {
          const entry = document.createElement("article");
          entry.className = "transcript-entry";
          const recipients = Array.isArray(event.payload.recipient_ids)
            ? event.payload.recipient_ids.map(agentDisplayName).join(", ")
            : optionalString(event.payload.target_id)
              ? agentDisplayName(event.payload.target_id)
              : "unspecified";
          const meta = document.createElement("div");
          meta.className = "transcript-entry__meta";
          meta.textContent = `t${event.tick} · ${
            agentDisplayName(event.agentId ?? "unknown")
          } → ${recipients}${
            event.type === "dialogue.generated" ? " · legacy dialogue" : ""
          }`;
          const quote = document.createElement("blockquote");
          quote.textContent = String(event.payload.text ?? "");
          entry.append(meta, quote);
          entry.addEventListener("click", () => showEventDetail(event));
          transcript.append(entry);
        }
      }
    });
    fragment.append(row);
  }
  elements["event-log"].replaceChildren(fragment);
  elements["event-count"].textContent = `${state.events.length} events / ${visible.length} shown`;
  if (state.autoScroll && state.eventOrder === "oldest") {
    elements["event-log"].scrollTop = elements["event-log"].scrollHeight;
  }
}

function eventMatchesFilter(event) {
  const filter = state.filter;
  let matches = true;
  if (filter === "system1") matches = event.type.startsWith("system1.") || event.type === "threshold.breached";
  if (filter === "actions") matches = /^(plan|planner|affordance|activity|agent|path)\./.test(event.type);
  if (filter === "dialogue") matches = /^(dialogue|speech)\./.test(event.type);
  if (filter === "cognition") matches = /^(cognition|tool)\./.test(event.type);
  if (filter === "perception") matches = event.type.startsWith("perception.");
  if (filter === "errors") matches = /(failed|blocked|cancelled|error|rejected)/.test(event.type);
  if (filter === "ui") matches = event.type.startsWith("ui.") || event.type.startsWith("simulation.");
  if (!matches) return false;
  if (!state.search) return true;
  return JSON.stringify(event).toLowerCase().includes(state.search);
}

function eventClass(type) {
  if (/(failed|blocked|cancelled|error)/.test(type)) return "error";
  if (type.startsWith("system1.") || type === "threshold.breached") return "system1";
  return "";
}

function summarizePayload(payload, agentId) {
  const prefix = agentId ? `${agentId}: ` : "";
  const preferred = [
    "text", "tool_name", "action", "target_id", "recipient_ids", "drive",
    "station_id", "reason", "current", "message", "provider", "latency_ms",
  ];
  const details = preferred
    .filter((key) => payload[key] !== undefined && payload[key] !== null)
    .map((key) => `${key}=${String(payload[key])}`);
  if (details.length) return prefix + details.join(", ");
  const serialized = JSON.stringify(payload);
  return prefix + (serialized.length > 240 ? `${serialized.slice(0, 237)}...` : serialized);
}

function showEventDetail(event) {
  state.selectedEvent = event;
  elements["event-detail-title"].textContent = event.type;
  elements["event-detail-meta"].textContent =
    `tick ${event.tick} / ${event.time.toFixed(1)}s / ${event.eventId}`;
  elements["event-detail-text"].textContent = JSON.stringify(event, null, 2);
  elements["event-detail-dialog"].showModal();
}

async function copySelectedEvent(mode) {
  if (!state.selectedEvent) return;
  const event = state.selectedEvent;
  const text = mode === "text"
    ? String(event.payload.text ?? summarizePayload(event.payload, event.agentId))
    : JSON.stringify(event, null, 2);
  await navigator.clipboard.writeText(text);
  setControlStatus("Copied event content.");
}

async function loadOlderEvents() {
  if (!state.runId) return;
  try {
    const page = await api(
      `/simulation/runs/${encodeURIComponent(state.runId)}/events?offset=${
        state.eventHistoryOffset
      }&limit=1000`
    );
    state.eventHistoryTotal = finiteNumber(page.total, 0);
    for (let index = 0; index < (page.events ?? []).length; index += 1) {
      addEvent(
        page.events[index],
        state.eventHistoryOffset + index + 1,
        {animate: false, render: false}
      );
    }
    state.eventHistoryOffset = finiteNumber(
      page.next_offset,
      state.eventHistoryOffset
    );
    elements["load-older-events"].disabled =
      state.eventHistoryOffset >= state.eventHistoryTotal;
    renderEventLog();
    renderTranscript();
    renderInspector();
  } catch (error) {
    setControlStatus(`Could not load event history: ${error.message}`, true);
  }
}

function updateControls() {
  const hasRun = Boolean(state.runId);
  const historyComplete =
    state.eventHistoryTotal > 0
    && state.eventHistoryOffset >= state.eventHistoryTotal;
  const availability = controlAvailability({
    uiState: state.uiState,
    runStatus: state.runStatus,
    hasRun,
    hasScenario: Boolean(state.loadedScenario),
    historyComplete,
  });
  elements["start-button"].disabled = !availability.start;
  elements["pause-button"].disabled = !availability.pause;
  elements["resume-button"].disabled = !availability.resume;
  elements["step-button"].disabled = !availability.step;
  elements["stop-button"].disabled = !availability.stop;
  elements["speed-select"].disabled = !availability.speed;
  elements["scenario-file"].disabled = !availability.loadScenario;
  elements["load-example-button"].disabled = !availability.loadScenario;
  elements["load-older-events"].disabled = !availability.loadHistory;
  elements["vitals-form"].querySelectorAll("input, button").forEach((control) => {
    control.disabled = !availability.mutate;
  });
}

function statusMessage(status) {
  if (status === "paused") return "Simulation paused. Tick will remain stable until Resume or Single step.";
  if (status === "stopped") return "Run stopped. The loaded scenario and final results remain available.";
  if (status === "running") return "Simulation running.";
  return "";
}

function updateRunLabel() {
  elements["run-label"].textContent = state.runId
    ? `${state.runId} / ${state.runStatus}`
    : "No active run";
}

function setControlStatus(message, isError = false) {
  elements["control-status"].textContent = message;
  elements["control-status"].classList.toggle("error", isError);
}

function setConnection(kind, label) {
  elements["connection-dot"].className = `connection__dot ${kind}`;
  elements["connection-label"].textContent = label;
}

function showProtocolWarning(message) {
  elements["protocol-warning"].hidden = false;
  elements["protocol-warning"].textContent = message;
}

function clearProtocolWarning() {
  elements["protocol-warning"].hidden = true;
  elements["protocol-warning"].textContent = "";
}

function resetRunState() {
  state.runId = null;
  state.runStatus = "created";
  state.snapshot = null;
  state.bootstrap = null;
  state.selectedAgentId = null;
  state.lastSequence = 0;
  state.events = [];
  state.eventIds = new Set();
  state.eventHistoryTotal = 0;
  state.eventHistoryOffset = 0;
  state.lastDomainEventOffset = 0;
  state.lastSnapshotRevision = 0;
  state.sensorEffects = {};
  state.speechBubbles = {};
  state.recentPerceptions = {};
  elements["sequence-label"].textContent = "seq --";
  renderEventLog();
  renderTranscript();
  render();
}

elements["load-example-button"].addEventListener("click", loadExample);
elements["start-button"].addEventListener("click", startLoadedScenario);
elements["pause-button"].addEventListener("click", () => control("pause"));
elements["resume-button"].addEventListener("click", () => control("resume"));
elements["step-button"].addEventListener("click", () => control("step"));
elements["stop-button"].addEventListener("click", () => control("stop"));
elements["speed-select"].addEventListener("change", () =>
  control("speed", {speed: finiteNumber(elements["speed-select"].value, 1)})
);
elements["agent-select"].addEventListener("change", (event) => {
  state.selectedAgentId = event.target.value || null;
  renderInspector();
  drawWorld();
});
elements["event-filter"].addEventListener("change", (event) => {
  state.filter = event.target.value;
  renderEventLog();
});
elements["event-search"].addEventListener("input", (event) => {
  state.search = event.target.value.trim().toLowerCase();
  renderEventLog();
});
elements["event-order"].addEventListener("change", (event) => {
  state.eventOrder = event.target.value;
  renderEventLog();
});
elements["auto-scroll"].addEventListener("change", (event) => {
  state.autoScroll = event.target.checked;
});
elements["load-older-events"].addEventListener("click", loadOlderEvents);
elements["expand-log"].addEventListener("click", () => {
  const panel = document.querySelector(".log-panel");
  const expanded = panel.classList.toggle("expanded");
  elements["expand-log"].textContent = expanded ? "Collapse" : "Expand";
});
elements["clear-log"].addEventListener("click", () => {
  state.events = [];
  state.eventIds = new Set();
  state.eventHistoryOffset = 0;
  state.eventHistoryTotal = 0;
  renderEventLog();
  updateControls();
});
elements["close-event-detail"].addEventListener("click", () =>
  elements["event-detail-dialog"].close()
);
elements["copy-event-text"].addEventListener("click", () =>
  copySelectedEvent("text")
);
elements["copy-event-json"].addEventListener("click", () =>
  copySelectedEvent("json")
);
for (const [elementId, option] of [
  ["overlay-names", "names"],
  ["overlay-paths", "paths"],
  ["overlay-speech", "speech"],
  ["overlay-vision", "vision"],
  ["overlay-hearing", "hearing"],
  ["overlay-selected-visibility", "selectedVisibility"],
  ["overlay-debug-destinations", "debugDestinations"],
]) {
  elements[elementId].addEventListener("change", (event) => {
    state.overlayOptions[option] = event.target.checked;
    drawWorld();
  });
}
elements["vitals-form"].addEventListener("submit", mutateVitals);
elements["scenario-file"].addEventListener("change", async (event) => {
  const [file] = event.target.files;
  if (!file) return;
  try {
    await loadScenario(JSON.parse(await file.text()), file.name);
  } catch (error) {
    addLocalEvent("ui.scenario_invalid", {message: error.message}, "error");
  } finally {
    event.target.value = "";
  }
});
canvas.addEventListener("click", (event) => {
  const tile = finiteNumber(canvas.dataset.tile, 0);
  if (!tile) return;
  const rect = canvas.getBoundingClientRect();
  const x = Math.floor((event.clientX - rect.left - finiteNumber(canvas.dataset.originX)) / tile);
  const y = Math.floor((event.clientY - rect.top - finiteNumber(canvas.dataset.originY)) / tile);
  const agent = state.snapshot?.agents.find(
    (candidate) => candidate.position?.x === x && candidate.position?.y === y
  );
  if (agent) {
    state.selectedAgentId = agent.id;
    elements["agent-select"].value = agent.id;
    renderInspector();
    drawWorld();
  }
});
window.addEventListener("resize", drawWorld);
window.addEventListener("beforeunload", () => closeSocket(true));

fetch("/health")
  .then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  })
  .then((health) => setConnection("warning", `API ${health.status}; no run connected`))
  .catch(() => setConnection("offline", "API unavailable"));

render();
