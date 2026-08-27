import asyncio
from dataclasses import dataclass
from uuid import uuid4

from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.runner import RunnerStatus, SimulationRunner
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.application.telemetry import TelemetryBroker
from stage0_sim.domain.components import HomeostasisComponent


class SimulationNotFoundError(KeyError):
    pass


class SimulationConflictError(RuntimeError):
    pass


@dataclass(slots=True)
class ManagedRun:
    runner: SimulationRunner
    broker: TelemetryBroker
    collector: RunDataCollector
    realtime_task: asyncio.Task[None] | None = None
    telemetry_task: asyncio.Task[None] | None = None


class SimulationManager:
    def __init__(
        self,
        dataset_store: SQLiteDatasetStore,
        telemetry_hz: float = 10.0,
    ) -> None:
        if telemetry_hz <= 0:
            raise ValueError("telemetry_hz must be greater than zero")
        self.telemetry_hz = telemetry_hz
        self.dataset_store = dataset_store
        self._scenarios: dict[str, ScenarioDefinition] = {}
        self._runs: dict[str, ManagedRun] = {}
        self._next_scenario_id = 1

    def add_scenario(self, scenario: ScenarioDefinition) -> str:
        scenario_id = f"scenario-{self._next_scenario_id:06d}"
        self._next_scenario_id += 1
        self._scenarios[scenario_id] = scenario
        return scenario_id

    def get_scenario(self, scenario_id: str) -> ScenarioDefinition:
        try:
            return self._scenarios[scenario_id]
        except KeyError as error:
            raise SimulationNotFoundError(
                f"unknown scenario: {scenario_id}"
            ) from error

    async def start_run(
        self,
        scenario_id: str,
        *,
        realtime: bool,
        speed: float | None = None,
    ) -> str:
        scenario = self.get_scenario(scenario_id)
        run_id = f"run-{uuid4()}"
        runner = create_runner(scenario, run_id=run_id, speed=speed)
        collector = RunDataCollector(
            store=self.dataset_store,
            runner=runner,
            scenario=scenario.model_dump(mode="json"),
        )
        broker = TelemetryBroker(runner)
        managed = ManagedRun(
            runner=runner,
            broker=broker,
            collector=collector,
        )
        self._runs[run_id] = managed
        runner.start()
        broker.publish_status()
        broker.publish_snapshot()
        managed.telemetry_task = asyncio.create_task(
            self._telemetry_loop(managed),
            name=f"{run_id}-telemetry",
        )
        if realtime:
            managed.realtime_task = asyncio.create_task(
                runner.run_realtime(),
                name=f"{run_id}-simulation",
            )
        return run_id

    def get_run(self, run_id: str) -> ManagedRun:
        try:
            return self._runs[run_id]
        except KeyError as error:
            raise SimulationNotFoundError(f"unknown run: {run_id}") from error

    def pause(self, run_id: str) -> None:
        managed = self.get_run(run_id)
        managed.runner.pause()
        managed.broker.publish_status()

    def resume(self, run_id: str) -> None:
        managed = self.get_run(run_id)
        managed.runner.resume()
        if managed.realtime_task is None or managed.realtime_task.done():
            managed.realtime_task = asyncio.create_task(
                managed.runner.run_realtime(),
                name=f"{run_id}-simulation",
            )
        managed.broker.publish_status()

    def step(self, run_id: str) -> None:
        managed = self.get_run(run_id)
        if managed.runner.status is RunnerStatus.RUNNING:
            raise SimulationConflictError("pause the run before single-stepping")
        managed.runner.single_step()
        managed.broker.publish_snapshot()

    def set_speed(self, run_id: str, speed: float) -> None:
        managed = self.get_run(run_id)
        managed.runner.set_speed(speed)
        managed.broker.publish_status()

    def mutate_vitals(
        self,
        run_id: str,
        agent_id: str,
        values: dict[str, float],
    ) -> None:
        managed = self.get_run(run_id)
        registry = managed.runner.registry
        if agent_id not in registry.entities():
            raise SimulationNotFoundError(f"unknown agent: {agent_id}")
        if not registry.has_component(agent_id, HomeostasisComponent):
            raise SimulationConflictError(
                f"agent has no homeostasis component: {agent_id}"
            )
        state = registry.get_component(agent_id, HomeostasisComponent)
        before = state.snapshot()
        for name, value in values.items():
            setattr(state, name, value)
        managed.runner.events.emit(
            "homeostasis.mutated",
            simulation_tick=managed.runner.clock.tick,
            simulation_time=managed.runner.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "before": before,
                "after": state.snapshot(),
                "source": "api",
            },
        )
        managed.broker.publish_snapshot()

    async def stop_run(self, run_id: str) -> None:
        managed = self.get_run(run_id)
        managed.runner.stop()
        managed.broker.publish_status()
        await self._cancel_tasks(managed)

    async def close(self) -> None:
        for managed in tuple(self._runs.values()):
            if managed.runner.status is not RunnerStatus.STOPPED:
                managed.runner.stop()
            await self._cancel_tasks(managed)
        self.dataset_store.close()

    async def _telemetry_loop(self, managed: ManagedRun) -> None:
        interval = 1.0 / self.telemetry_hz
        try:
            while managed.runner.status is not RunnerStatus.STOPPED:
                await asyncio.sleep(interval)
                managed.broker.publish_snapshot()
        except asyncio.CancelledError:
            raise

    @staticmethod
    async def _cancel_tasks(managed: ManagedRun) -> None:
        current = asyncio.current_task()
        tasks = [
            task
            for task in (managed.realtime_task, managed.telemetry_task)
            if task is not None and task is not current and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
