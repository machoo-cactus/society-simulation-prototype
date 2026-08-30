import asyncio
from dataclasses import dataclass
from uuid import uuid4

from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.agents.contracts import ModelClient
from stage0_sim.application.characters import (
    CharacterLibrary,
    PreparedScenario,
    prepare_scenario,
)
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.runner import (
    CognitionPhase,
    RunnerStatus,
    SimulationRunner,
)
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
        character_library: CharacterLibrary | None = None,
        telemetry_hz: float = 10.0,
        model_client: ModelClient | None = None,
        model_max_output_tokens: int | None = None,
        model_max_concurrency: int | None = None,
    ) -> None:
        if telemetry_hz <= 0:
            raise ValueError("telemetry_hz must be greater than zero")
        self.telemetry_hz = telemetry_hz
        self.dataset_store = dataset_store
        self.character_library = character_library
        self.model_client = model_client
        self.model_max_output_tokens = model_max_output_tokens
        self.model_max_concurrency = model_max_concurrency
        self._scenarios: dict[str, PreparedScenario] = {}
        self._runs: dict[str, ManagedRun] = {}
        self._next_scenario_id = 1

    def add_scenario(self, scenario: ScenarioDefinition) -> str:
        if self.character_library is None:
            if any(
                isinstance(
                    entity.components.get("character_profile", {}).get(
                        "character_id"
                    ),
                    str,
                )
                for entity in scenario.entities
            ):
                raise ValueError("character library is not configured")
            prepared = PreparedScenario(scenario=scenario, characters={})
        else:
            prepared = prepare_scenario(scenario, self.character_library)
        scenario_id = f"scenario-{self._next_scenario_id:06d}"
        self._next_scenario_id += 1
        self._scenarios[scenario_id] = prepared
        return scenario_id

    def get_scenario(self, scenario_id: str) -> PreparedScenario:
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
        prepared = self.get_scenario(scenario_id)
        scenario = prepared.scenario
        run_id = f"run-{uuid4()}"
        runner = create_runner(
            scenario,
            resolved_characters={
                character_id: character.profile()
                for character_id, character in prepared.characters.items()
            },
            run_id=run_id,
            speed=speed,
            model_client=self.model_client,
            model_max_output_tokens=self.model_max_output_tokens,
            model_max_concurrency=self.model_max_concurrency,
        )
        collector = RunDataCollector(
            store=self.dataset_store,
            runner=runner,
            scenario=prepared.dataset_payload(),
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

    async def step(self, run_id: str) -> None:
        managed = self.get_run(run_id)
        if managed.runner.status is RunnerStatus.RUNNING:
            raise SimulationConflictError("pause the run before single-stepping")
        await managed.runner.single_step_async()
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
        if managed.runner.cognition_phase is not CognitionPhase.IDLE:
            raise SimulationConflictError(
                "vitals cannot be mutated while cognition is settling"
            )
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
        await self._await_realtime_boundary(managed)
        await self._cancel_tasks(managed)

    async def close(self) -> None:
        for managed in tuple(self._runs.values()):
            if managed.runner.status is not RunnerStatus.STOPPED:
                managed.runner.stop()
            await self._await_realtime_boundary(managed)
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
    async def _await_realtime_boundary(managed: ManagedRun) -> None:
        task = managed.realtime_task
        if (
            task is None
            or task is asyncio.current_task()
            or task.done()
        ):
            return
        await task

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
