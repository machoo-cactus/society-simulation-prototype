export const UI_STATES = Object.freeze({
  EMPTY: "EMPTY",
  SCENARIO_LOADING: "SCENARIO_LOADING",
  SCENARIO_READY: "SCENARIO_READY",
  RUN_STARTING: "RUN_STARTING",
  RUNNING: "RUNNING",
  PAUSING: "PAUSING",
  PAUSED: "PAUSED",
  RESUMING: "RESUMING",
  STEPPING: "STEPPING",
  STOPPING: "STOPPING",
  STOPPED: "STOPPED",
  SPEED_CHANGING: "SPEED_CHANGING",
  ERROR: "ERROR",
});

export const PENDING_UI_STATES = new Set([
  UI_STATES.SCENARIO_LOADING,
  UI_STATES.RUN_STARTING,
  UI_STATES.PAUSING,
  UI_STATES.RESUMING,
  UI_STATES.STEPPING,
  UI_STATES.STOPPING,
  UI_STATES.SPEED_CHANGING,
]);

export function uiStateForRunStatus(status, hasScenario) {
  if (status === "running") return UI_STATES.RUNNING;
  if (status === "paused") return UI_STATES.PAUSED;
  if (status === "stopped") return UI_STATES.STOPPED;
  return hasScenario ? UI_STATES.SCENARIO_READY : UI_STATES.EMPTY;
}

export function controlAvailability({
  uiState,
  runStatus,
  cognitionPhase = "idle",
  hasRun,
  hasScenario,
  historyComplete,
}) {
  const busy = PENDING_UI_STATES.has(uiState);
  const running = runStatus === "running";
  const paused = runStatus === "paused";
  const stopped = runStatus === "stopped";
  const cognitionSettling = ["waiting", "applying"].includes(cognitionPhase);
  const scenarioReady =
    hasScenario
    && [UI_STATES.SCENARIO_READY, UI_STATES.STOPPED].includes(uiState);
  return {
    busy,
    start: scenarioReady && (!hasRun || stopped) && !busy,
    pause: hasRun && running && !busy,
    resume: hasRun && paused && !busy,
    step: hasRun && paused && !busy && !cognitionSettling,
    stop: hasRun && !stopped && !busy,
    speed: hasRun && !stopped && !busy,
    loadScenario: (!hasRun || stopped) && !busy,
    mutate: hasRun && !stopped && !busy && !cognitionSettling,
    loadHistory: hasRun && !historyComplete,
  };
}
