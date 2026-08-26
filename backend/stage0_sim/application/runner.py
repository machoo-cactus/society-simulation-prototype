import asyncio
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from stage0_sim.domain.clock import SimulationClock
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.events import DomainEvent, EventBus
from stage0_sim.domain.systems import SystemContext, SystemExecutor


@dataclass(frozen=True, slots=True)
class RunConfiguration:
    seed: int
    dt: float = 1.0
    speed: float = 1.0
    run_id: str | None = None

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


class SimulationRunner:
    def __init__(
        self,
        configuration: RunConfiguration,
        *,
        registry: Registry | None = None,
        systems: SystemExecutor | None = None,
        events: EventBus | None = None,
    ) -> None:
        self.configuration = configuration
        self.clock = SimulationClock(dt=configuration.dt)
        self.registry = registry or Registry()
        self.systems = systems or SystemExecutor()
        self.events = events or EventBus(configuration.run_id or str(uuid4()))
        self.rng = random.Random(configuration.seed)
        self.speed = configuration.speed
        self.status = RunnerStatus.CREATED
        self._tick_completed_handlers: list[Callable[[DomainEvent], None]] = []
        self._advancing = False
        self._stop_requested = False

    @property
    def context(self) -> SystemContext:
        return SystemContext(self.clock, self.registry, self.events, self.rng)

    def start(self) -> None:
        if self.status is RunnerStatus.STOPPED:
            raise RuntimeError("a stopped simulation cannot be restarted")
        if self.status is not RunnerStatus.CREATED:
            return
        self.status = RunnerStatus.RUNNING
        self._emit(
            "simulation.started",
            {"seed": self.configuration.seed, "dt": self.configuration.dt, "speed": self.speed},
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
            return
        self._finalize_stop()

    def _finalize_stop(self) -> None:
        self._prepare_stop()
        self._stop_requested = False
        self._emit("simulation.stopped")

    def _prepare_stop(self) -> None:
        from stage0_sim.application.macro_work import MacroWorkCoordinator

        if self.registry.has_resource(MacroWorkCoordinator):
            self.registry.get_resource(MacroWorkCoordinator).cancel_non_memory(
                self.context,
                "simulation_stopped",
            )
        self.flush_pending_memory()

    def flush_pending_memory(self) -> None:
        from stage0_sim.application.macro_work import MacroWorkCoordinator
        from stage0_sim.application.memory_recording import MemoryRecordingSystem

        if not self.registry.has_resource(MacroWorkCoordinator):
            return
        for system in self.systems.systems:
            if isinstance(system, MemoryRecordingSystem):
                system.update(self.context)
        self.registry.get_resource(MacroWorkCoordinator).drain_memory(self.context)

    def subscribe_tick_completed(
        self,
        handler: Callable[[DomainEvent], None],
    ) -> None:
        self._tick_completed_handlers.append(handler)

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("speed must be greater than zero")
        previous_speed = self.speed
        self.speed = speed
        self._emit("simulation.speed_changed", {"previous": previous_speed, "speed": speed})

    def step(self) -> None:
        if self.status is not RunnerStatus.RUNNING:
            raise RuntimeError("step requires a running simulation")
        self._advance_one_tick()

    def single_step(self) -> None:
        if self.status is RunnerStatus.STOPPED:
            raise RuntimeError("a stopped simulation cannot advance")
        if self.status is RunnerStatus.CREATED:
            self.start()
            self.pause()
        self._advance_one_tick()

    def run_for(self, ticks: int) -> None:
        if ticks < 0:
            raise ValueError("ticks must not be negative")
        if self.status is RunnerStatus.CREATED:
            self.start()
        if self.status is not RunnerStatus.RUNNING:
            raise RuntimeError("run_for requires a running simulation")
        for _ in range(ticks):
            if self._advance_one_tick():
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
                self._advance_one_tick()
                completed += 1

    def _advance_one_tick(self) -> bool:
        from stage0_sim.application.macro_work import MacroWorkCoordinator

        self._advancing = True
        stopped = False
        try:
            event_start = len(self.events.events)
            self.clock.advance()
            self.systems.update(self.context)
            tick_event = self._emit("simulation.tick", {"dt": self.clock.dt})
            if (
                not self._stop_requested
                and self.registry.has_resource(MacroWorkCoordinator)
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
                self.registry.get_resource(MacroWorkCoordinator).drain(
                    self.context,
                    survival_agent_ids=survival_agent_ids,
                )
            if self._stop_requested:
                self._prepare_stop()
            for handler in tuple(self._tick_completed_handlers):
                handler(tick_event)
            if self._stop_requested:
                stopped = True
                self._finalize_stop()
        finally:
            self._advancing = False
        return stopped

    def _emit(
        self,
        event_type: str,
        payload: dict[str, bool | int | float | str | None] | None = None,
    ) -> DomainEvent:
        return self.events.emit(
            event_type,
            simulation_tick=self.clock.tick,
            simulation_time=self.clock.simulation_time,
            payload=payload,
        )
