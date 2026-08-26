import json
from dataclasses import dataclass

from stage0_sim.domain.components import MemoryComponent
from stage0_sim.domain.events import DomainEvent, JsonValue
from stage0_sim.domain.systems import SystemContext

_EVENT_IMPORTANCE = {
    "dialogue.generated": 0.65,
    "plan.action_completed": 0.55,
    "plan.action_failed": 0.75,
    "planner.failed": 0.75,
    "system1.activated": 0.9,
    "system1.resolved": 0.85,
    "system1.blocked": 1.0,
    "threshold.breached": 0.85,
}


@dataclass(slots=True)
class MemoryRecordingSystem:
    name: str = "memory_recording"
    order: int = 290
    _event_cursor: int = 0

    def update(self, context: SystemContext) -> None:
        from stage0_sim.application.macro_work import (
            MacroWorkCoordinator,
            MemoryWork,
        )

        coordinator = context.registry.get_resource(MacroWorkCoordinator)
        events = context.events.events
        pending = events[self._event_cursor :]
        self._event_cursor = len(events)
        for event in pending:
            if (
                event.event_type not in _EVENT_IMPORTANCE
                or event.agent_id is None
                or not context.registry.has_component(
                    event.agent_id, MemoryComponent
                )
            ):
                continue
            text = _event_text(event)
            requested = context.events.emit(
                "memory.requested",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=event.agent_id,
                payload={
                    "source_event_type": event.event_type,
                    "importance": _EVENT_IMPORTANCE[event.event_type],
                },
                causation_id=event.event_id,
                correlation_id=event.correlation_id,
            )
            coordinator.enqueue_memory(
                MemoryWork(
                    agent_id=event.agent_id,
                    text=text,
                    simulation_time=event.simulation_time,
                    importance=_EVENT_IMPORTANCE[event.event_type],
                    metadata={
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "payload": dict(event.payload),
                    },
                    requested_event_id=requested.event_id,
                    correlation_id=event.correlation_id,
                )
            )


def _event_text(event: DomainEvent) -> str:
    payload = dict(event.payload)
    if event.event_type == "threshold.breached":
        return (
            f"{payload.get('drive')} became critical at "
            f"{payload.get('value')}."
        )
    if event.event_type == "system1.activated":
        return f"Survival behavior activated for {payload.get('drive')}."
    if event.event_type == "system1.resolved":
        return f"Recovered from the {payload.get('drive')} survival drive."
    if event.event_type == "system1.blocked":
        return (
            f"Survival behavior for {payload.get('drive')} was blocked: "
            f"{payload.get('reason')}."
        )
    if event.event_type == "dialogue.generated":
        return str(payload.get("text", "Dialogue occurred."))
    if event.event_type.startswith("plan."):
        return (
            f"Plan event {event.event_type}: "
            f"{json.dumps(payload, sort_keys=True, separators=(',', ':'))}"
        )
    return (
        f"{event.event_type}: "
        f"{json.dumps(payload, sort_keys=True, separators=(',', ':'))}"
    )


def observation_metadata(source: str) -> dict[str, JsonValue]:
    return {"source": source, "event_type": "observation"}
