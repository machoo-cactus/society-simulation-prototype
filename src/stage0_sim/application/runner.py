import asyncio
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from stage0_sim.application.data_capture import (
    BufferedResearchRecorder,
    ResearchRecorder,
    RunnerPhase,
)
from stage0_sim.domain.clock import SimulationClock
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.events import DomainEvent, EventBus, JsonValue
from stage0_sim.domain.npcs import NpcControlMode
from stage0_sim.domain.systems import SystemContext, SystemExecutor

RunnerPhaseHandler = Callable[
    [RunnerPhase, "SimulationRunner", SystemContext],
    None,
]


@dataclass(frozen=True, slots=True)
class RunConfiguration:
    seed: int
    dt: float = 1.0
    speed: float = 1.0
    run_id: str | None = None
    npc_control_mode: NpcControlMode = NpcControlMode.AUTO
    effective_npc_control_mode: NpcControlMode = NpcControlMode.DETERMINISTIC

    def __post_init__(self) -> None:
        if self.dt <= 0:
            raise ValueError("dt must be greater than zero")
        if self.speed <= 0:
            raise ValueError("speed must be greater than zero")
        if self.run_id == "":
            raise ValueError("run_id must not be empty")


class RunnerStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class CognitionPhase(StrEnum):
    IDLE = "idle"
    WAITING = "waiting"
    APPLYING = "applying"


class SimulationRunner:
    def __init__(
        self,
        configuration: RunConfiguration,
        *,
        registry: Registry | None = None,
        systems: SystemExecutor | None = None,
        events: EventBus | None = None,
        research_recorder: BufferedResearchRecorder | None = None,
    ) -> None:
        self.configuration = configuration
        self.clock = SimulationClock(dt=configuration.dt)
        self.registry = registry or Registry()
        self.systems = systems or SystemExecutor()
        self.events = events or EventBus(configuration.run_id or str(uuid4()))
        self.research_recorder = research_recorder or BufferedResearchRecorder()
        self.research_recorder.bind_clock(
            lambda: self.clock.tick,
            lambda: self.clock.simulation_time,
        )
        if not self.registry.has_resource(BufferedResearchRecorder):
            self.registry.set_resource(self.research_recorder)
        for _resource_type, resource in self.registry.resource_items():
            binder = getattr(resource, "bind_research_recorder", None)
            if callable(binder):
                binder(self.research_recorder)
        self.rng = random.Random(configuration.seed)
        self.speed = configuration.speed
        self.status = RunnerStatus.CREATED
        self.cognition_phase = CognitionPhase.IDLE
        self._cognition_wait_started_at: float | None = None
        self._tick_completed_handlers: list[Callable[[DomainEvent], None]] = []
        self._phase_handlers: list[RunnerPhaseHandler] = []
        self._run_final_notified = False
        self._advancing = False
        self._stop_requested = False

    @property
    def research(self) -> ResearchRecorder:
        return self.research_recorder

    @property
    def context(self) -> SystemContext:
        return SystemContext(self.clock, self.registry, self.events, self.rng)

    @property
    def advancing(self) -> bool:
        return self._advancing

    def require_checkpoint_boundary(self) -> None:
        if self.status is not RunnerStatus.PAUSED:
            raise RuntimeError("checkpoint creation requires a paused simulation")
        if self.cognition_phase is not CognitionPhase.IDLE or self._advancing:
            raise RuntimeError(
                "checkpoint creation requires a settled tick boundary"
            )
        if self.cognition_pending_count:
            raise RuntimeError(
                "checkpoint creation requires empty cognition work queues"
            )

    def restore_paused(
        self,
        *,
        tick: int,
        speed: float,
        rng_state: tuple[object, ...],
        event_count: int,
        event_history: tuple[DomainEvent, ...],
        research_next_sequence: int,
    ) -> None:
        if self.status is not RunnerStatus.CREATED:
            raise RuntimeError("only a newly constructed runner can be restored")
        if tick < 0:
            raise ValueError("restored tick must not be negative")
        if speed <= 0:
            raise ValueError("restored speed must be positive")
        self.clock.tick = tick
        self.speed = speed
        self.rng.setstate(rng_state)
        if event_history:
            if len(event_history) != event_count:
                raise ValueError(
                    "restored event history does not match event count"
                )
            self.events.restore_events(event_history)
        else:
            self.events.restore_event_count(event_count)
        self.research_recorder.restore_next_sequence(research_next_sequence)
        self.status = RunnerStatus.PAUSED
        self.cognition_phase = CognitionPhase.IDLE

    def announce_restore(
        self,
        *,
        checkpoint_id: str,
        source_run_id: str,
        branched: bool,
    ) -> None:
        if self.status is not RunnerStatus.PAUSED:
            raise RuntimeError("restored runner must be paused")
        if branched:
            self._notify_phase(RunnerPhase.RUN_INITIAL)
        self._emit(
            "simulation.branched" if branched else "simulation.restored",
            {
                "checkpoint_id": checkpoint_id,
                "source_run_id": source_run_id,
                "branched": branched,
            },
        )

    def start(self) -> None:
        if self.status is RunnerStatus.STOPPED:
            raise RuntimeError("a stopped simulation cannot be restarted")
        if self.status is not RunnerStatus.CREATED:
            return
        self.status = RunnerStatus.RUNNING
        self._notify_phase(RunnerPhase.RUN_INITIAL)
        self._emit(
            "simulation.started",
            {
                "seed": self.configuration.seed,
                "dt": self.configuration.dt,
                "speed": self.speed,
                "npc_control_mode": self.configuration.npc_control_mode.value,
                "effective_npc_control_mode": (
                    self.configuration.effective_npc_control_mode.value
                ),
            },
        )

    def pause(self) -> None:
        if self.status is not RunnerStatus.RUNNING:
            raise RuntimeError("only a running simulation can be paused")
        self.status = RunnerStatus.PAUSED
        self._emit("simulation.paused")

    def resume(self) -> None:
        if self.status is not RunnerStatus.PAUSED:
            raise RuntimeError("only a paused simulation can be resumed")
        self.status = RunnerStatus.RUNNING
        self._emit("simulation.resumed")

    def stop(self) -> None:
        if self.status is RunnerStatus.STOPPED:
            return
        self.status = RunnerStatus.STOPPED
        if self._advancing:
            self._stop_requested = True
            from stage0_sim.application.agents import AgentWorkCoordinator
            from stage0_sim.application.engagements import (
                EngagementWorkCoordinator,
            )

            if self.registry.has_resource(AgentWorkCoordinator):
                self.registry.get_resource(AgentWorkCoordinator).cancel_all(
                    self.context,
                    "simulation_stopped",
                )
            if self.registry.has_resource(EngagementWorkCoordinator):
                self.registry.get_resource(
                    EngagementWorkCoordinator
                ).cancel_all(
                    self.context,
                    "simulation_stopped",
                )
            return
        self._finalize_stop()

    def _finalize_stop(self) -> None:
        self._prepare_stop()
        self._stop_requested = False
        self.notify_run_final()
        self._emit("simulation.stopped")

    def _prepare_stop(self) -> None:
        from stage0_sim.application.agents import AgentWorkCoordinator
        from stage0_sim.application.engagements import EngagementWorkCoordinator
        from stage0_sim.domain.components import (
            ActionType,
            EngagementExecutionComponent,
            EngagementProgramComponent,
            PendingEngagementComponent,
            PlanComponent,
            TextActionRequestComponent,
        )
        from stage0_sim.domain.lineage import clear_plan_lineage
        from stage0_sim.domain.systems.engagements import (
            cancel_engagement_state,
        )
        from stage0_sim.domain.systems.text_actions import cancel_text_action

        if self.registry.has_resource(AgentWorkCoordinator):
            coordinator = self.registry.get_resource(AgentWorkCoordinator)
            coordinator.cancel_all(self.context, "simulation_stopped")
            coordinator.close()
        if self.registry.has_resource(EngagementWorkCoordinator):
            engagement_coordinator = self.registry.get_resource(
                EngagementWorkCoordinator
            )
            engagement_coordinator.cancel_all(
                self.context,
                "simulation_stopped",
            )
            engagement_coordinator.close()
        engagement_actor_ids = sorted(
            {
                *(
                    actor_id
                    for actor_id, _ in self.registry.query(
                        PendingEngagementComponent
                    )
                ),
                *(
                    actor_id
                    for actor_id, _ in self.registry.query(
                        EngagementProgramComponent
                    )
                ),
                *(
                    actor_id
                    for actor_id, _ in self.registry.query(
                        EngagementExecutionComponent
                    )
                ),
            }
        )
        for actor_id in engagement_actor_ids:
            if self.registry.has_component(actor_id, PlanComponent):
                plan = self.registry.get_component(actor_id, PlanComponent)
                has_engagement_action = (
                    plan.current is not None
                    and plan.current.action is ActionType.ENGAGE
                ) or any(
                    action.action is ActionType.ENGAGE
                    for action in plan.queue
                )
                if has_engagement_action:
                    clear_plan_lineage(
                        self.context,
                        actor_id,
                        plan,
                        reason="simulation_stopped",
                    )
                    continue
            if any(
                self.registry.has_component(actor_id, component_type)
                for component_type in (
                    PendingEngagementComponent,
                    EngagementProgramComponent,
                    EngagementExecutionComponent,
                )
            ):
                cancel_engagement_state(
                    self.context,
                    actor_id,
                    "simulation_stopped",
                )
        for actor_id in tuple(
            self.registry.query_entities(TextActionRequestComponent)
        ):
            cancel_text_action(self.context, actor_id, "simulation_stopped")
        self.flush_pending_memory()

    def flush_pending_memory(self) -> None:
        from stage0_sim.application.memory_recording import (
            MemoryRecordingSystem,
            MemoryWorkCoordinator,
        )

        if not self.registry.has_resource(MemoryWorkCoordinator):
            return
        for system in self.systems.systems:
            if isinstance(system, MemoryRecordingSystem):
                system.update(self.context)
        self.registry.get_resource(MemoryWorkCoordinator).drain_all(
            self.context
        )

    def subscribe_tick_completed(
        self,
        handler: Callable[[DomainEvent], None],
    ) -> None:
        self._tick_completed_handlers.append(handler)

    def subscribe_phase(self, handler: RunnerPhaseHandler) -> None:
        """Subscribe a read-only observer to runner lifecycle boundaries."""
        self._phase_handlers.append(handler)

    def notify_run_final(self) -> None:
        """Notify observers of final state once, including external finalizers."""
        if self._run_final_notified:
            return
        self._run_final_notified = True
        self._notify_phase(RunnerPhase.RUN_FINAL)

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("speed must be greater than zero")
        previous_speed = self.speed
        self.speed = speed
        self._emit("simulation.speed_changed", {"previous": previous_speed, "speed": speed})

    def step(self) -> None:
        if self.status is not RunnerStatus.RUNNING:
            raise RuntimeError("step requires a running simulation")
        asyncio.run(self.advance_one_tick())

    def single_step(self) -> None:
        if self.status is RunnerStatus.STOPPED:
            raise RuntimeError("a stopped simulation cannot advance")
        if self.status is RunnerStatus.CREATED:
            self.start()
            self.pause()
        asyncio.run(self.single_step_async())

    async def single_step_async(self) -> None:
        if self.status is RunnerStatus.STOPPED:
            raise RuntimeError("a stopped simulation cannot advance")
        if self.status is RunnerStatus.CREATED:
            self.start()
            self.pause()
        await self.advance_one_tick()

    def run_for(self, ticks: int) -> None:
        asyncio.run(self.run_for_async(ticks))

    async def run_for_async(self, ticks: int) -> None:
        if ticks < 0:
            raise ValueError("ticks must not be negative")
        if self.status is RunnerStatus.CREATED:
            self.start()
        if self.status is not RunnerStatus.RUNNING:
            raise RuntimeError("run_for requires a running simulation")
        for _ in range(ticks):
            if await self.advance_one_tick():
                break

    async def run_realtime(self, ticks: int | None = None) -> None:
        if ticks is not None and ticks < 0:
            raise ValueError("ticks must not be negative")
        if self.status is RunnerStatus.CREATED:
            self.start()
        completed = 0
        deadline = time.monotonic()
        while self.status is not RunnerStatus.STOPPED and (ticks is None or completed < ticks):
            if self.status is RunnerStatus.PAUSED:
                await asyncio.sleep(0.01)
                deadline = time.monotonic()
                continue
            interval = self.clock.dt / self.speed
            deadline += interval
            await asyncio.sleep(max(0.0, deadline - time.monotonic()))
            if self.status is RunnerStatus.RUNNING:
                await self.advance_one_tick()
                completed += 1

    @property
    def cognition_wait_elapsed_seconds(self) -> float:
        if self._cognition_wait_started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self._cognition_wait_started_at)

    @property
    def cognition_pending_decision_ids(self) -> tuple[str, ...]:
        from stage0_sim.application.agents import AgentWorkCoordinator

        if not self.registry.has_resource(AgentWorkCoordinator):
            return ()
        return self.registry.get_resource(
            AgentWorkCoordinator
        ).pending_decision_ids

    @property
    def cognition_pending_engagement_ids(self) -> tuple[str, ...]:
        from stage0_sim.application.engagements import (
            EngagementWorkCoordinator,
        )

        if not self.registry.has_resource(EngagementWorkCoordinator):
            return ()
        return self.registry.get_resource(
            EngagementWorkCoordinator
        ).pending_engagement_ids

    @property
    def cognition_pending_count(self) -> int:
        return len(self.cognition_pending_decision_ids) + len(
            self.cognition_pending_engagement_ids
        )

    async def advance_one_tick(self) -> bool:
        from stage0_sim.application.agents import AgentWorkCoordinator
        from stage0_sim.application.engagements import (
            EngagementWorkCoordinator,
        )
        from stage0_sim.application.memory_recording import MemoryWorkCoordinator

        self._advancing = True
        stopped = False
        try:
            event_start = len(self.events.events)
            self.clock.advance()
            self._notify_phase(RunnerPhase.TICK_PRE_SYSTEMS)
            self.systems.update(self.context)
            self._notify_phase(RunnerPhase.TICK_POST_SYSTEMS)
            coordinator = (
                self.registry.get_resource(AgentWorkCoordinator)
                if self.registry.has_resource(AgentWorkCoordinator)
                else None
            )
            engagement_coordinator = (
                self.registry.get_resource(EngagementWorkCoordinator)
                if self.registry.has_resource(EngagementWorkCoordinator)
                else None
            )
            has_barrier_work = (
                (
                    self.registry.has_resource(MemoryWorkCoordinator)
                    and self.registry.get_resource(
                        MemoryWorkCoordinator
                    ).pending_count
                    > 0
                )
                or (
                    coordinator is not None
                    and coordinator.pending_count > 0
                )
                or (
                    engagement_coordinator is not None
                    and engagement_coordinator.pending_count > 0
                )
            )
            if has_barrier_work:
                self.cognition_phase = CognitionPhase.WAITING
                self._cognition_wait_started_at = time.monotonic()
                batch_decision_ids = (
                    coordinator.pending_decision_ids
                    if coordinator is not None
                    else ()
                )
                batch_engagement_ids = (
                    engagement_coordinator.pending_engagement_ids
                    if engagement_coordinator is not None
                    else ()
                )
                self._emit(
                    "cognition.barrier_started",
                    {
                        "pending_count": (
                            len(batch_decision_ids)
                            + len(batch_engagement_ids)
                        ),
                        "pending_decision_count": len(batch_decision_ids),
                        "pending_engagement_count": len(
                            batch_engagement_ids
                        ),
                        "pending_decision_ids": list(batch_decision_ids),
                        "pending_engagement_ids": list(
                            batch_engagement_ids
                        ),
                    },
                )
            else:
                batch_decision_ids = ()
                batch_engagement_ids = ()
            if (
                not self._stop_requested
                and self.registry.has_resource(MemoryWorkCoordinator)
            ):
                tick_events = self.events.events[event_start:]
                survival_agent_ids = frozenset(
                    event.agent_id
                    for event in tick_events
                    if event.agent_id is not None
                    and (
                        event.event_type.startswith("system1.")
                        or event.event_type == "threshold.breached"
                    )
                )
                self.registry.get_resource(MemoryWorkCoordinator).drain(
                    self.context,
                    survival_agent_ids=survival_agent_ids,
                )
            if (
                not self._stop_requested
                and coordinator is not None
            ):
                await coordinator.drain_and_wait(
                    self.context,
                    on_applying=self._mark_cognition_applying,
                )
            if engagement_coordinator is not None:
                batch_engagement_ids = (
                    engagement_coordinator.pending_engagement_ids
                )
            if (
                not self._stop_requested
                and engagement_coordinator is not None
                and engagement_coordinator.pending_count > 0
            ):
                self.cognition_phase = CognitionPhase.WAITING
                await engagement_coordinator.drain_and_wait(
                    self.context,
                    on_applying=self._mark_cognition_applying,
                )
            self.cognition_phase = CognitionPhase.IDLE
            self._cognition_wait_started_at = None
            if has_barrier_work:
                self._emit(
                    "cognition.barrier_settled",
                    {
                        "decision_count": len(batch_decision_ids),
                        "engagement_count": len(
                            batch_engagement_ids
                        ),
                        "decision_ids": list(batch_decision_ids),
                        "engagement_ids": list(batch_engagement_ids),
                        "cancelled": self._stop_requested,
                    },
                )
            tick_event = self._emit("simulation.tick", {"dt": self.clock.dt})
            if self._stop_requested:
                self._prepare_stop()
            self._notify_phase(RunnerPhase.TICK_POST_COGNITION)
            for handler in tuple(self._tick_completed_handlers):
                handler(tick_event)
            if self._stop_requested:
                stopped = True
                self._finalize_stop()
        finally:
            self._advancing = False
            self.cognition_phase = CognitionPhase.IDLE
            self._cognition_wait_started_at = None
        return stopped

    def _mark_cognition_applying(self) -> None:
        self.cognition_phase = CognitionPhase.APPLYING

    def _notify_phase(self, phase: RunnerPhase) -> None:
        context = self.context
        for handler in tuple(self._phase_handlers):
            handler(phase, self, context)

    def _emit(
        self,
        event_type: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> DomainEvent:
        return self.events.emit(
            event_type,
            simulation_tick=self.clock.tick,
            simulation_time=self.clock.simulation_time,
            payload=payload,
        )
