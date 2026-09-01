import json
from collections import deque
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stage0_sim.application.cognition import EmbeddingError
from stage0_sim.application.data_capture import (
    MemoryId,
    RecordCategory,
    RecordJoinIds,
    RecordSource,
    ResearchRecorder,
)
from stage0_sim.application.memory import EpisodicMemoryStore
from stage0_sim.domain.components import DriveComponent, MemoryComponent, System1State
from stage0_sim.domain.events import DomainEvent, JsonValue
from stage0_sim.domain.systems import SystemContext

_EVENT_IMPORTANCE = {
    "action.completed": 0.55,
    "action.failed": 0.75,
    "speech.delivered": 0.7,
    "system1.activated": 0.9,
    "system1.resolved": 0.85,
    "system1.blocked": 1.0,
    "threshold.breached": 0.85,
    "transaction.completed": 0.65,
    "transaction.failed": 0.75,
}


@runtime_checkable
class NamedProvider(Protocol):
    provider_name: str


@dataclass(frozen=True, slots=True)
class MemoryWork:
    agent_id: str
    text: str
    simulation_time: float
    importance: float
    metadata: dict[str, JsonValue]
    requested_event_id: str
    correlation_id: str | None


class MemoryWorkCoordinator:
    def __init__(
        self,
        memory_store: EpisodicMemoryStore,
        *,
        research_recorder: ResearchRecorder | None = None,
    ) -> None:
        self.memory_store = memory_store
        self.research_recorder = research_recorder
        self._memory: deque[MemoryWork] = deque()

    def bind_research_recorder(self, recorder: ResearchRecorder) -> None:
        self.research_recorder = recorder

    @property
    def pending_count(self) -> int:
        return len(self._memory)

    def enqueue(self, work: MemoryWork) -> None:
        self._memory.append(work)

    def drain(
        self,
        context: SystemContext,
        *,
        survival_agent_ids: frozenset[str] = frozenset(),
    ) -> None:
        blocked = set(survival_agent_ids)
        blocked.update(
            agent_id
            for agent_id, drive in context.registry.query(DriveComponent)
            if drive.state is not System1State.NORMAL
        )
        pending = tuple(self._memory)
        self._memory.clear()
        for work in pending:
            if work.agent_id in blocked:
                self._memory.append(work)
            else:
                self._record(context, work)

    def drain_all(self, context: SystemContext) -> None:
        pending = tuple(self._memory)
        self._memory.clear()
        for work in pending:
            if (
                context.registry.has_component(work.agent_id, DriveComponent)
                and context.registry.get_component(
                    work.agent_id, DriveComponent
                ).state
                is not System1State.NORMAL
            ):
                self._cancel(context, work)
            else:
                self._record(context, work)

    def _record(self, context: SystemContext, work: MemoryWork) -> None:
        self._record_private(
            "memory_generation_request",
            {
                "operation_id": work.requested_event_id,
                "request": work,
                "provider": _provider_name(
                    self.memory_store.embedding_provider
                ),
            },
            subject_id=work.agent_id,
            correlation_id=work.correlation_id,
        )
        try:
            record = self.memory_store.record(
                agent_id=work.agent_id,
                text=work.text,
                simulation_time=work.simulation_time,
                importance=work.importance,
                metadata=work.metadata,
            )
        except EmbeddingError as error:
            self._record_private(
                "memory_generation_result",
                {
                    "operation_id": work.requested_event_id,
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
                subject_id=work.agent_id,
                correlation_id=work.correlation_id,
            )
            context.events.emit(
                "memory.failed",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=work.agent_id,
                payload={
                    "message": str(error),
                    "provider": _provider_name(
                        self.memory_store.embedding_provider
                    ),
                },
                causation_id=work.requested_event_id,
                correlation_id=work.correlation_id,
            )
            return
        self._record_private(
            "memory_generation_result",
            {
                "operation_id": work.requested_event_id,
                "status": "completed",
                "memory": record,
            },
            subject_id=work.agent_id,
            correlation_id=work.correlation_id,
            joins=RecordJoinIds(memory_id=MemoryId(record.id)),
        )
        context.events.emit(
            "memory.recorded",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=work.agent_id,
            payload={
                "memory_id": record.id,
                "source_event_type": record.metadata.get("event_type"),
                "importance": record.importance,
                "provider": _provider_name(
                    self.memory_store.embedding_provider
                ),
            },
            causation_id=work.requested_event_id,
            correlation_id=work.correlation_id,
        )

    def _cancel(self, context: SystemContext, work: MemoryWork) -> None:
        self._record_private(
            "memory_generation_result",
            {
                "operation_id": work.requested_event_id,
                "status": "cancelled",
                "reason": "system1_active_at_finalization",
                "request": work,
            },
            subject_id=work.agent_id,
            correlation_id=work.correlation_id,
        )
        context.events.emit(
            "memory.cancelled",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=work.agent_id,
            payload={
                "reason": "system1_active_at_finalization",
                "source_event_type": work.metadata.get("event_type"),
                "provider": _provider_name(
                    self.memory_store.embedding_provider
                ),
            },
            causation_id=work.requested_event_id,
            correlation_id=work.correlation_id,
        )

    def _record_private(
        self,
        record_type: str,
        payload: object,
        *,
        subject_id: str,
        correlation_id: str | None,
        joins: RecordJoinIds | None = None,
    ) -> None:
        if self.research_recorder is None:
            return
        self.research_recorder.record(
            record_type,
            payload,
            category=RecordCategory.MEMORY,
            source=RecordSource.APPLICATION,
            subject_id=subject_id,
            correlation_id=correlation_id,
            joins=joins,
        )


@dataclass(slots=True)
class MemoryRecordingSystem:
    name: str = "memory_recording"
    order: int = 290
    _event_cursor: int = 0

    def update(self, context: SystemContext) -> None:
        coordinator = context.registry.get_resource(MemoryWorkCoordinator)
        events = context.events.events
        pending = events[self._event_cursor :]
        self._event_cursor = len(events)
        for event in pending:
            if event.event_type not in _EVENT_IMPORTANCE:
                continue
            for recipient_id in _memory_recipients(event):
                if not context.registry.has_component(
                    recipient_id, MemoryComponent
                ):
                    continue
                text = _event_text(event)
                requested = context.events.emit(
                    "memory.requested",
                    simulation_tick=context.clock.tick,
                    simulation_time=context.clock.simulation_time,
                    agent_id=recipient_id,
                    payload={
                        "source_event_type": event.event_type,
                        "importance": _EVENT_IMPORTANCE[event.event_type],
                    },
                    causation_id=event.event_id,
                    correlation_id=event.correlation_id,
                )
                coordinator.enqueue(
                    MemoryWork(
                        agent_id=recipient_id,
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
    if event.event_type == "speech.delivered":
        return (
            f"{event.agent_id or 'Someone'} said: "
            f"{payload.get('text', '')}"
        )
    if event.event_type.startswith("transaction."):
        return (
            f"Transaction event {event.event_type}: "
            f"{json.dumps(payload, sort_keys=True, separators=(',', ':'))}"
        )
    if event.event_type.startswith("action."):
        return (
            f"Action event {event.event_type}: "
            f"{json.dumps(payload, sort_keys=True, separators=(',', ':'))}"
        )
    return (
        f"{event.event_type}: "
        f"{json.dumps(payload, sort_keys=True, separators=(',', ':'))}"
    )


def observation_metadata(source: str) -> dict[str, JsonValue]:
    return {"source": source, "event_type": "observation"}


def _memory_recipients(event: DomainEvent) -> tuple[str, ...]:
    recipients: set[str] = set()
    if event.agent_id is not None:
        recipients.add(event.agent_id)
    if event.event_type == "speech.delivered":
        raw_recipients = event.payload.get("recipient_ids")
        if isinstance(raw_recipients, list):
            recipients.update(
                recipient
                for recipient in raw_recipients
                if isinstance(recipient, str)
            )
    return tuple(sorted(recipients))


def _provider_name(provider: object) -> str:
    return (
        provider.provider_name
        if isinstance(provider, NamedProvider)
        else "unknown"
    )
