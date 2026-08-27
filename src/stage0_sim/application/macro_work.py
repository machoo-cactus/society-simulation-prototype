from collections import deque
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from stage0_sim.application.cognition import (
    DialogueContext,
    DialogueError,
    DialogueGenerator,
    EmbeddingError,
    Planner,
    PlannerContext,
    PlannerError,
    PlanValidationError,
)
from stage0_sim.application.memory import EpisodicMemoryStore
from stage0_sim.application.planning import action_payload, validate_plan
from stage0_sim.domain.components import (
    AffordanceExecutionComponent,
    ConversationComponent,
    DriveComponent,
    PlanComponent,
    PlannerComponent,
    System1State,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.plans import fail_social_action, is_dialogue_capable
from stage0_sim.domain.world import WorldMap


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


@dataclass(frozen=True, slots=True)
class PlanningWork:
    agent_id: str
    context: PlannerContext
    memory_query: str | None
    top_k: int


@dataclass(frozen=True, slots=True)
class DialogueWork:
    agent_id: str
    target_id: str
    prompt: str
    top_k: int
    requested_event_id: str


class MacroWorkCoordinator:
    """Executes provider work only at the runner's post-tick boundary."""

    def __init__(
        self,
        *,
        planner: Planner,
        dialogue_generator: DialogueGenerator,
        memory_store: EpisodicMemoryStore,
    ) -> None:
        self.planner = planner
        self.dialogue_generator = dialogue_generator
        self.memory_store = memory_store
        self._memory: deque[MemoryWork] = deque()
        self._planning: deque[PlanningWork] = deque()
        self._dialogue: deque[DialogueWork] = deque()

    def enqueue_memory(self, work: MemoryWork) -> None:
        self._memory.append(work)

    def enqueue_planning(self, work: PlanningWork) -> None:
        self._planning.append(work)

    def enqueue_dialogue(self, work: DialogueWork) -> None:
        self._dialogue.append(work)

    def drain(
        self,
        context: SystemContext,
        *,
        survival_agent_ids: frozenset[str],
    ) -> None:
        blocked_agent_ids = set(survival_agent_ids)
        blocked_agent_ids.update(
            agent_id
            for agent_id, drive in context.registry.query(DriveComponent)
            if drive.state is not System1State.NORMAL
        )
        pending_memory = tuple(self._memory)
        self._memory.clear()
        for memory_work in pending_memory:
            if memory_work.agent_id in blocked_agent_ids:
                self._memory.append(memory_work)
            else:
                self._record_memory(context, memory_work)
        pending_dialogue = tuple(self._dialogue)
        self._dialogue.clear()
        for dialogue_work in pending_dialogue:
            if (
                dialogue_work.agent_id in blocked_agent_ids
                or dialogue_work.target_id in blocked_agent_ids
            ):
                self.cancel_dialogue(
                    context,
                    dialogue_work,
                    "system1_preemption",
                )
            else:
                self._generate_dialogue(context, dialogue_work)
        pending_planning = tuple(self._planning)
        self._planning.clear()
        for planning_work in pending_planning:
            if planning_work.agent_id in blocked_agent_ids:
                self._cancel_planning(
                    context,
                    planning_work,
                    "system1_preemption",
                )
            else:
                self._generate_plan(context, planning_work)

    def drain_memory(self, context: SystemContext) -> None:
        pending_memory = tuple(self._memory)
        self._memory.clear()
        for memory_work in pending_memory:
            if self._is_system1_active(context, memory_work.agent_id):
                self._cancel_memory(
                    context,
                    memory_work,
                    "system1_active_at_finalization",
                )
            else:
                self._record_memory(context, memory_work)

    def cancel_non_memory(self, context: SystemContext, reason: str) -> None:
        pending_dialogue = tuple(self._dialogue)
        self._dialogue.clear()
        for dialogue_work in pending_dialogue:
            self.cancel_dialogue(context, dialogue_work, reason)
        pending_planning = tuple(self._planning)
        self._planning.clear()
        for planning_work in pending_planning:
            self._cancel_planning(context, planning_work, reason)

    def cancel_dialogue(
        self,
        context: SystemContext,
        work: DialogueWork,
        reason: str,
    ) -> None:
        conversation = context.registry.get_component(
            work.agent_id, ConversationComponent
        )
        conversation.request_pending = False
        fail_social_action(
            context,
            work.agent_id,
            work.target_id,
            reason,
        )
        context.events.emit(
            "dialogue.cancelled",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=work.agent_id,
            payload={
                "target_id": work.target_id,
                "reason": reason,
                "provider": _provider_name(self.dialogue_generator),
                "latency_ms": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
            },
            causation_id=work.requested_event_id,
            correlation_id=work.requested_event_id,
        )

    def _cancel_memory(
        self,
        context: SystemContext,
        work: MemoryWork,
        reason: str,
    ) -> None:
        context.events.emit(
            "memory.cancelled",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=work.agent_id,
            payload={
                "reason": reason,
                "source_event_type": work.metadata.get("event_type"),
                "provider": _provider_name(
                    self.memory_store.embedding_provider
                ),
            },
            causation_id=work.requested_event_id,
            correlation_id=work.correlation_id,
        )

    def _cancel_planning(
        self,
        context: SystemContext,
        work: PlanningWork,
        reason: str,
    ) -> None:
        planner = context.registry.get_component(
            work.agent_id, PlannerComponent
        )
        planner.request_pending = False
        context.events.emit(
            "planner.cancelled",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=work.agent_id,
            payload={
                "reason": reason,
                "provider": _provider_name(self.planner),
                "latency_ms": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
            },
        )

    def _record_memory(self, context: SystemContext, work: MemoryWork) -> None:
        try:
            record = self.memory_store.record(
                agent_id=work.agent_id,
                text=work.text,
                simulation_time=work.simulation_time,
                importance=work.importance,
                metadata=work.metadata,
            )
        except EmbeddingError as error:
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

    def _generate_plan(self, context: SystemContext, work: PlanningWork) -> None:
        planner_state = context.registry.get_component(
            work.agent_id, PlannerComponent
        )
        plan = context.registry.get_component(work.agent_id, PlanComponent)
        drive = context.registry.get_component(work.agent_id, DriveComponent)
        provider = _provider_name(self.planner)
        if (
            drive.state is not System1State.NORMAL
            or plan.current is not None
            or plan.queue
            or context.registry.has_component(
                work.agent_id, AffordanceExecutionComponent
            )
        ):
            planner_state.request_pending = False
            context.events.emit(
                "planner.cancelled",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=work.agent_id,
                payload={
                    "reason": "agent_no_longer_eligible",
                    "provider": provider,
                    "latency_ms": 0.0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                },
            )
            return

        memories = self._retrieve(
            context,
            agent_id=work.agent_id,
            query=work.memory_query,
            top_k=work.top_k,
        )
        planner_context = replace(work.context, memories=memories)
        requested = context.events.emit(
            "planner.requested",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=work.agent_id,
            payload={
                "daily_goals": list(planner_context.daily_goals),
                "zone_count": len(planner_context.zones),
                "station_count": len(planner_context.stations),
                "memory_count": len(planner_context.memories),
                "provider": provider,
            },
        )
        planner_state.request_count += 1
        result = None
        try:
            result = self.planner.plan(planner_context)
            world = context.registry.get_resource(WorldMap)
            validate_plan(result, world, context.registry, work.agent_id)
        except (PlannerError, PlanValidationError) as error:
            planner_state.failure_count += 1
            planner_state.request_pending = False
            metadata: dict[str, JsonValue]
            if isinstance(error, PlanValidationError) and result is not None:
                metadata = {
                    "provider": result.provider,
                    "latency_ms": result.latency_ms,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                }
            else:
                metadata = _failure_metadata(error, provider)
            context.events.emit(
                "planner.failed",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=work.agent_id,
                payload={
                    "error_type": type(error).__name__,
                    "message": str(error),
                    **metadata,
                },
                causation_id=requested.event_id,
                correlation_id=requested.event_id,
            )
            return

        plan.queue.extend(result.actions)
        planner_state.needs_plan = False
        planner_state.request_pending = False
        planner_state.last_planned_at = context.clock.simulation_time
        context.events.emit(
            "planner.completed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=work.agent_id,
            payload={
                "rationale": result.rationale,
                "actions": [action_payload(action) for action in result.actions],
                "provider": result.provider,
                "latency_ms": result.latency_ms,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
            causation_id=requested.event_id,
            correlation_id=requested.event_id,
        )

    def _generate_dialogue(
        self,
        context: SystemContext,
        work: DialogueWork,
    ) -> None:
        if not is_dialogue_capable(context.registry, work.target_id):
            self.cancel_dialogue(context, work, "invalid_social_target")
            return
        if (
            not self._is_system1_normal(context, work.agent_id)
            or not self._is_system1_normal(context, work.target_id)
        ):
            self.cancel_dialogue(context, work, "system1_preemption")
            return
        conversation = context.registry.get_component(
            work.agent_id, ConversationComponent
        )
        provider = _provider_name(self.dialogue_generator)
        memories = self._retrieve(
            context,
            agent_id=work.agent_id,
            query=work.prompt,
            top_k=work.top_k,
        )
        dialogue_context = DialogueContext(
            agent_id=work.agent_id,
            simulation_time=context.clock.simulation_time,
            prompt=work.prompt,
            memories=memories,
        )
        try:
            result = self.dialogue_generator.generate(dialogue_context)
        except DialogueError as error:
            conversation.request_pending = False
            context.events.emit(
                "dialogue.failed",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=work.agent_id,
                payload={
                    "target_id": work.target_id,
                    "error_type": type(error).__name__,
                    "message": str(error),
                    **_failure_metadata(error, provider),
                },
                causation_id=work.requested_event_id,
                correlation_id=work.requested_event_id,
            )
            return

        conversation.request_pending = False
        conversation.turns.append(result.text)
        target = context.registry.get_component(
            work.target_id, ConversationComponent
        )
        target.turns.append(result.text)
        context.events.emit(
            "dialogue.generated",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=work.agent_id,
            payload={
                "target_id": work.target_id,
                "text": result.text,
                "provider": result.provider,
                "latency_ms": result.latency_ms,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "memory_count": len(memories),
            },
            causation_id=work.requested_event_id,
            correlation_id=work.requested_event_id,
        )

    @staticmethod
    def _is_system1_active(context: SystemContext, agent_id: str) -> bool:
        return (
            context.registry.has_component(agent_id, DriveComponent)
            and context.registry.get_component(
                agent_id, DriveComponent
            ).state
            is not System1State.NORMAL
        )

    @staticmethod
    def _is_system1_normal(context: SystemContext, agent_id: str) -> bool:
        return (
            context.registry.has_component(agent_id, DriveComponent)
            and context.registry.get_component(
                agent_id, DriveComponent
            ).state
            is System1State.NORMAL
        )

    def _retrieve(
        self,
        context: SystemContext,
        *,
        agent_id: str,
        query: str | None,
        top_k: int,
    ) -> tuple[str, ...]:
        if query is None:
            return ()
        try:
            retrieved = self.memory_store.retrieve(
                agent_id=agent_id,
                query=query,
                simulation_time=context.clock.simulation_time,
                top_k=top_k,
            )
        except EmbeddingError as error:
            context.events.emit(
                "memory.retrieval_failed",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "message": str(error),
                    "query": query,
                    "provider": _provider_name(
                        self.memory_store.embedding_provider
                    ),
                },
            )
            return ()
        if retrieved:
            context.events.emit(
                "memory.retrieved",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "query": query,
                    "memory_ids": [item.record.id for item in retrieved],
                    "scores": [item.score for item in retrieved],
                },
            )
        return tuple(item.record.text for item in retrieved)


def _provider_name(provider: object) -> str:
    return provider.provider_name if isinstance(provider, NamedProvider) else "unknown"


def _failure_metadata(
    error: PlannerError | PlanValidationError | DialogueError,
    default_provider: str,
) -> dict[str, JsonValue]:
    if isinstance(error, (PlannerError, DialogueError)):
        return {
            "provider": error.provider or default_provider,
            "latency_ms": error.latency_ms,
            "input_tokens": error.input_tokens,
            "output_tokens": error.output_tokens,
        }
    return {
        "provider": default_provider,
        "latency_ms": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
