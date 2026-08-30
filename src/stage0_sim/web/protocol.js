export const TELEMETRY_SCHEMA_VERSION = "stage0.telemetry.v2";

export function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

export function optionalString(value) {
  return typeof value === "string" && value.length ? value : null;
}

export function normalizeCoordinate(value) {
  if (Array.isArray(value) && value.length >= 2) {
    return {x: finiteNumber(value[0]), y: finiteNumber(value[1])};
  }
  if (!isObject(value)) return null;
  const x = Number(value.x);
  const y = Number(value.y);
  return Number.isFinite(x) && Number.isFinite(y) ? {x, y} : null;
}

export function normalizeEnvelope(raw) {
  if (!isObject(raw)) throw new Error("WebSocket message is not an object");
  const sequence = Number(raw.sequence);
  if (!Number.isInteger(sequence) || sequence < 1) {
    throw new Error("WebSocket message has no valid sequence");
  }
  return {
    schemaVersion: optionalString(raw.schema_version),
    sequence,
    type: optionalString(raw.type) ?? "unknown",
    tick: finiteNumber(raw.simulation_tick, 0),
    simulationTime: finiteNumber(raw.simulation_time, 0),
    payload: isObject(raw.payload) ? raw.payload : {},
    domainEventOffset: Number.isInteger(Number(raw.domain_event_offset))
      ? Number(raw.domain_event_offset)
      : null,
    snapshotRevision: Number.isInteger(Number(raw.snapshot_revision))
      ? Number(raw.snapshot_revision)
      : null,
  };
}
