import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from stage0_sim.application.data_capture.contracts import (
    RecordCategory,
    RecordJoinIds,
    RecordSource,
    RecordVisibility,
    RunnerPhase,
)
from stage0_sim.domain.events import JsonValue


class ResearchWriteError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ResearchTrace:
    trace_id: str
    record_type: str
    simulation_tick: int
    simulation_time: float
    payload: dict[str, JsonValue]
    category: RecordCategory = RecordCategory.OTHER
    source: RecordSource = RecordSource.APPLICATION
    phase: RunnerPhase = RunnerPhase.UNSPECIFIED
    visibility: RecordVisibility = RecordVisibility.PRIVATE_RESEARCH
    subject_id: str | None = None
    related_entity_ids: tuple[str, ...] = ()
    causation_id: str | None = None
    correlation_id: str | None = None
    joins: RecordJoinIds = field(default_factory=RecordJoinIds)
    ordinal: int = 0


class ResearchSink(Protocol):
    def write(self, trace: ResearchTrace) -> None: ...


class ResearchRecorder(Protocol):
    def record(
        self,
        record_type: str,
        payload: object,
        *,
        category: RecordCategory = RecordCategory.OTHER,
        source: RecordSource = RecordSource.APPLICATION,
        phase: RunnerPhase = RunnerPhase.UNSPECIFIED,
        visibility: RecordVisibility = RecordVisibility.PRIVATE_RESEARCH,
        subject_id: str | None = None,
        related_entity_ids: tuple[str, ...] = (),
        causation_id: str | None = None,
        correlation_id: str | None = None,
        joins: RecordJoinIds | None = None,
        ordinal: int = 0,
    ) -> ResearchTrace: ...

    def subscribe(self, handler: Callable[[ResearchTrace], None]) -> None: ...

    def drain(self) -> tuple[ResearchTrace, ...]: ...


class BufferedResearchRecorder:
    """Thread-safe application-only trace buffer.

    Traces are deliberately not DomainEvents and are drained by the dataset
    collector only at application boundaries.
    """

    def __init__(self, *sinks: ResearchSink) -> None:
        self._sinks = sinks
        self._lock = threading.Lock()
        self._traces: list[tuple[int, ResearchTrace]] = []
        self._subscribers: list[Callable[[ResearchTrace], None]] = []
        self._failures: list[str] = []
        self._next_sequence = 1
        self._tick: Callable[[], int] = lambda: 0
        self._simulation_time: Callable[[], float] = lambda: 0.0

    def bind_clock(
        self,
        tick: Callable[[], int],
        simulation_time: Callable[[], float],
    ) -> None:
        self._tick = tick
        self._simulation_time = simulation_time

    @property
    def failures(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._failures)

    def subscribe(self, handler: Callable[[ResearchTrace], None]) -> None:
        with self._lock:
            self._subscribers.append(handler)

    def record(
        self,
        record_type: str,
        payload: object,
        *,
        category: RecordCategory = RecordCategory.OTHER,
        source: RecordSource = RecordSource.APPLICATION,
        phase: RunnerPhase = RunnerPhase.UNSPECIFIED,
        visibility: RecordVisibility = RecordVisibility.PRIVATE_RESEARCH,
        subject_id: str | None = None,
        related_entity_ids: tuple[str, ...] = (),
        causation_id: str | None = None,
        correlation_id: str | None = None,
        joins: RecordJoinIds | None = None,
        ordinal: int = 0,
    ) -> ResearchTrace:
        from stage0_sim.application.data_capture.state import (
            serialize_authoritative,
        )

        if not record_type:
            raise ValueError("research record_type must not be empty")
        serialized = serialize_authoritative(payload)
        if not isinstance(serialized, dict):
            raise ValueError("research trace payload must serialize to an object")
        with self._lock:
            sequence = self._next_sequence
            self._next_sequence += 1
        trace = ResearchTrace(
            trace_id=f"trace:{sequence:08d}",
            record_type=record_type,
            simulation_tick=self._tick(),
            simulation_time=self._simulation_time(),
            payload=serialized,
            category=category,
            source=source,
            phase=phase,
            visibility=visibility,
            subject_id=subject_id,
            related_entity_ids=related_entity_ids,
            causation_id=causation_id,
            correlation_id=correlation_id,
            joins=joins or RecordJoinIds(),
            ordinal=ordinal,
        )
        try:
            for sink in self._sinks:
                sink.write(trace)
        except Exception as error:
            message = (
                f"research sink {type(sink).__name__} failed for "
                f"{record_type}: {error}"
            )
            with self._lock:
                self._failures.append(message)
            raise ResearchWriteError(message) from error
        with self._lock:
            self._traces.append((sequence, trace))
            subscribers = tuple(self._subscribers)
        for handler in subscribers:
            try:
                handler(trace)
            except Exception as error:
                message = (
                    f"research subscriber {type(handler).__name__} failed for "
                    f"{record_type}: {error}"
                )
                with self._lock:
                    self._failures.append(message)
                raise ResearchWriteError(message) from error
        return trace

    def note_failure(self, message: str) -> None:
        with self._lock:
            self._failures.append(message)

    def drain(self) -> tuple[ResearchTrace, ...]:
        with self._lock:
            pending = self._traces
            self._traces = []
        pending.sort(key=lambda item: _trace_order(item[1], item[0]))
        return tuple(trace for _, trace in pending)


def _trace_order(trace: ResearchTrace, sequence: int) -> tuple[object, ...]:
    type_order = {
        "cognition_evaluation": 0,
        "decision_request": 10,
        "information_retrieval_request": 20,
        "embedding_request": 30,
        "embedding_result": 31,
        "information_retrieval_result": 40,
        "model_request": 50,
        "model_turn": 60,
        "model_error": 61,
        "decision_result": 70,
    }
    return (
        trace.simulation_tick,
        trace.subject_id or "",
        trace.correlation_id or "",
        trace.ordinal,
        type_order.get(trace.record_type, 100),
        json.dumps(
            trace.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        sequence,
    )
