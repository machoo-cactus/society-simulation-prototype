from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

type JsonValue = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
type EventHandler = Callable[["DomainEvent"], None]
type WallClock = Callable[[], datetime]


def event_payload_is_private(payload: Mapping[str, JsonValue]) -> bool:
    visibility = payload.get("visibility")
    if isinstance(visibility, str) and visibility.casefold() in {
        "private",
        "private_research",
    }:
        return True
    if isinstance(visibility, dict):
        level = visibility.get("level")
        if isinstance(level, str) and level.casefold() in {
            "private",
            "private_research",
        }:
            return True
    content_visibility = payload.get("content_visibility")
    return (
        isinstance(content_visibility, str)
        and content_visibility.casefold() in {"private", "private_research"}
    ) or payload.get("private_visibility") is True


@dataclass(frozen=True, slots=True)
class DomainEvent:
    run_id: str
    event_id: str
    simulation_tick: int
    simulation_time: float
    wall_time: datetime
    event_type: str
    payload: Mapping[str, JsonValue]
    agent_id: str | None = None
    causation_id: str | None = None
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "run_id": self.run_id,
            "event_id": self.event_id,
            "simulation_tick": self.simulation_tick,
            "simulation_time": self.simulation_time,
            "wall_time": self.wall_time.isoformat(),
            "agent_id": self.agent_id,
            "event_type": self.event_type,
            "payload": dict(self.payload),
            "causation_id": self.causation_id,
            "correlation_id": self.correlation_id,
        }

    def canonical_dict(self) -> dict[str, JsonValue]:
        """Return deterministic event content, excluding run and wall-clock identity."""
        content: dict[str, JsonValue] = {
            "simulation_tick": self.simulation_tick,
            "simulation_time": self.simulation_time,
            "event_type": self.event_type,
            "payload": self._canonical_value(dict(self.payload)),
        }
        if self.agent_id is not None:
            content["agent_id"] = self.agent_id
        if self.causation_id is not None:
            content["causation_id"] = self._canonical_reference(self.causation_id)
        if self.correlation_id is not None:
            content["correlation_id"] = self._canonical_reference(self.correlation_id)
        return content

    def _canonical_reference(self, reference: str) -> str:
        run_prefix = f"{self.run_id}:"
        if reference.startswith(run_prefix):
            return f"event-{reference.removeprefix(run_prefix)}"
        return reference

    def _canonical_value(self, value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            return self._canonical_reference(value)
        if isinstance(value, list):
            return [self._canonical_value(item) for item in value]
        if isinstance(value, dict):
            return {
                key: self._canonical_value(item)
                for key, item in value.items()
            }
        return value


class EventBus:
    def __init__(self, run_id: str, wall_clock: WallClock | None = None) -> None:
        if not run_id:
            raise ValueError("run_id must not be empty")
        self.run_id = run_id
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._handlers: dict[str | None, list[EventHandler]] = defaultdict(list)
        self._events: list[DomainEvent] = []
        self._next_event_number = 1

    @property
    def events(self) -> tuple[DomainEvent, ...]:
        return tuple(self._events)

    def subscribe(self, handler: EventHandler, event_type: str | None = None) -> None:
        self._handlers[event_type].append(handler)

    def emit(
        self,
        event_type: str,
        *,
        simulation_tick: int,
        simulation_time: float,
        payload: Mapping[str, JsonValue] | None = None,
        agent_id: str | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> DomainEvent:
        if not event_type:
            raise ValueError("event_type must not be empty")
        event = DomainEvent(
            run_id=self.run_id,
            event_id=f"{self.run_id}:{self._next_event_number:08d}",
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
            wall_time=self._wall_clock(),
            agent_id=agent_id,
            event_type=event_type,
            payload=dict(payload or {}),
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        self._next_event_number += 1
        self._events.append(event)
        for handler in (*self._handlers[event_type], *self._handlers[None]):
            handler(event)
        return event

    def restore_event_count(self, event_count: int) -> None:
        if event_count < 0:
            raise ValueError("event count must not be negative")
        if any(self._handlers.values()):
            raise RuntimeError(
                "event count must be restored before subscribers are attached"
            )
        self._events = []
        self._next_event_number = event_count + 1

    def restore_events(self, events: tuple[DomainEvent, ...]) -> None:
        if any(self._handlers.values()):
            raise RuntimeError(
                "events must be restored before subscribers are attached"
            )
        if any(event.run_id != self.run_id for event in events):
            raise ValueError("restored events must belong to this run")
        self._events = list(events)
        self._next_event_number = len(events) + 1
