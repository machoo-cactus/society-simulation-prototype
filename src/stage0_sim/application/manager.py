import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import uuid4

from stage0_sim.application.agents.contracts import ModelClient
from stage0_sim.application.character_synthesis import (
    CharacterSituationSynthesizer,
    ModelCharacterSituationSynthesizer,
    compose_character_situations,
)
from stage0_sim.application.characters import (
    CharacterLibrary,
    PreparedScenario,
    prepare_scenario,
)
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.data_management import (
    AggregateDatasetSummary,
    DatasetManagementService,
    LiveRunOverlay,
    PersistedRunFilter,
    PersistedRunPage,
    RunDeletionPreview,
    RunDeletionResult,
    RunSelection,
)
from stage0_sim.application.data_query import DatasetQueryService
from stage0_sim.application.ports import DatasetStore
from stage0_sim.application.runner import (
    CognitionPhase,
    RunnerStatus,
    SimulationRunner,
)
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.application.telemetry import TelemetryBroker
from stage0_sim.domain.components import ActionOrigin, HomeostasisComponent
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.lineage import (
    action_lineage_payload,
    emit_action_lifecycle,
    new_action_instance,
    new_operator_intervention_id,
)
from stage0_sim.domain.npcs import NpcControlMode


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
        dataset_store: DatasetStore,
        character_library: CharacterLibrary | None = None,
        telemetry_hz: float = 10.0,
        model_client: ModelClient | None = None,
        model_max_output_tokens: int | None = None,
        model_max_concurrency: int | None = None,
        situation_synthesizer: CharacterSituationSynthesizer | None = None,
    ) -> None:
        if telemetry_hz <= 0:
            raise ValueError("telemetry_hz must be greater than zero")
        self.telemetry_hz = telemetry_hz
        self.dataset_store = dataset_store
        self._runs: dict[str, ManagedRun] = {}
        self.data_query = DatasetQueryService(dataset_store)
        self.data_management = DatasetManagementService(
            dataset_store,
            self._live_dataset_statuses,
        )
        self.data_management.reconcile_prior_runs()
        self.character_library = character_library
        self.model_client = model_client
        self.model_max_output_tokens = model_max_output_tokens
        self.model_max_concurrency = model_max_concurrency
        self.situation_synthesizer = situation_synthesizer or (
            ModelCharacterSituationSynthesizer(model_client)
            if model_client is not None
            else None
        )
        self._scenarios: dict[str, PreparedScenario] = {}
        self._next_scenario_id = 1

    def _live_dataset_statuses(self) -> dict[str, LiveRunOverlay]:
        return {
            run_id: LiveRunOverlay(
                status=managed.runner.status.value,
                cognition_phase=managed.runner.cognition_phase.value,
                deletion_ready=self._managed_run_deletion_ready(managed),
            )
            for run_id, managed in self._runs.items()
        }

    @staticmethod
    def _managed_run_deletion_ready(managed: ManagedRun) -> bool:
        return (
            managed.runner.status is RunnerStatus.STOPPED
            and managed.runner.cognition_phase is CognitionPhase.IDLE
            and managed.collector.finalized
            and all(
                task is None or task.done()
                for task in (managed.realtime_task, managed.telemetry_task)
            )
        )

    def persisted_runs(
        self,
        filters: PersistedRunFilter | None = None,
    ) -> PersistedRunPage:
        return self.data_management.catalog(filters)

    def aggregate_persisted_runs(
        self,
        selection: RunSelection,
        *,
        include_private_derived: bool = False,
    ) -> AggregateDatasetSummary:
        return self.data_management.aggregate(
            selection,
            include_private_derived=include_private_derived,
        )

    def preview_persisted_run_deletion(
        self,
        selection: RunSelection,
    ) -> RunDeletionPreview:
        return self.data_management.preview_deletion(selection)

    def delete_persisted_runs(
        self,
        selection: RunSelection,
        confirmation_token: str,
    ) -> RunDeletionResult:
        for run_id in selection.run_ids:
            managed = self._runs.get(run_id)
            if managed is not None and not self._managed_run_deletion_ready(managed):
                raise SimulationConflictError(
                    f"run is not fully finalized and cannot be deleted: {run_id}"
                )
        result = self.data_management.delete(selection, confirmation_token)
        self._forget_deleted_runs(result.run_ids)
        return result

    def _forget_deleted_runs(self, run_ids: tuple[str, ...]) -> None:
        for run_id in run_ids:
            managed = self._runs.get(run_id)
            if managed is not None and not self._managed_run_deletion_ready(managed):
                raise SimulationConflictError(
                    f"run is not fully finalized and cannot be forgotten: {run_id}"
                )
        for run_id in run_ids:
            self._runs.pop(run_id, None)

    async def add_scenario(
        self,
        scenario: ScenarioDefinition,
        character_assignments: Mapping[str, str] | None = None,
        *,
        scenario_source: Mapping[str, JsonValue] | None = None,
        resolved_elements: Mapping[str, JsonValue] | None = None,
    ) -> str:
        if self.character_library is None:
            has_character_references = bool(character_assignments) or any(
                entity.components.get("character_slot", {}).get(
                    "default_character_id"
                )
                is not None
                for entity in scenario.entities
            )
            if has_character_references:
                raise ValueError("character library is not configured")
            prepared = PreparedScenario(
                scenario=scenario,
                assignments={},
                characters={},
                situations={},
                scenario_source=scenario_source,
                resolved_elements=resolved_elements or {},
            )
        else:
            prepared = prepare_scenario(
                scenario,
                self.character_library,
                character_assignments,
            )
        situations = await compose_character_situations(
            scenario=prepared.scenario,
            assignments=prepared.assignments,
            characters=prepared.characters,
            synthesizer=self.situation_synthesizer,
        )
        prepared = PreparedScenario(
            scenario=prepared.scenario,
            assignments=prepared.assignments,
            characters=prepared.characters,
            situations=situations,
            scenario_source=scenario_source,
            resolved_elements=resolved_elements or {},
        )
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
        npc_control_mode: NpcControlMode | str | None = None,
    ) -> str:
        prepared = self.get_scenario(scenario_id)
        scenario = prepared.scenario
        run_id = f"run-{uuid4()}"
        runner = create_runner(
            scenario,
            resolved_characters=prepared.runtime_characters(),
            resolved_situations=prepared.runtime_situations(),
            run_id=run_id,
            speed=speed,
            model_client=self.model_client,
            model_max_output_tokens=self.model_max_output_tokens,
            model_max_concurrency=self.model_max_concurrency,
            npc_control_mode=npc_control_mode,
        )
        dataset_scenario = prepared.dataset_payload()
        dataset_scenario["runtime_configuration"] = {
            "npc_control_mode": runner.configuration.npc_control_mode.value,
            "effective_npc_control_mode": (
                runner.configuration.effective_npc_control_mode.value
            ),
        }
        collector = RunDataCollector(
            store=self.dataset_store,
            runner=runner,
            scenario=dataset_scenario,
            private_provenance=prepared.private_research_provenance(),
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
        context = managed.runner.context
        intervention_id = new_operator_intervention_id(context)
        action = new_action_instance(
            context,
            agent_id,
            origin=ActionOrigin.OPERATOR,
            action_name="MUTATE_VITALS",
            target_id=agent_id,
            root_correlation_id=intervention_id,
        )
        emit_action_lifecycle(context, "action.queued", agent_id, action)
        emit_action_lifecycle(context, "action.started", agent_id, action)
        for name, value in values.items():
            setattr(state, name, value)
        mutated = managed.runner.events.emit(
            "homeostasis.mutated",
            simulation_tick=managed.runner.clock.tick,
            simulation_time=managed.runner.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "before": before,
                "after": state.snapshot(),
                "source": "api",
                "operator_intervention_id": intervention_id,
                **action_lineage_payload(action),
            },
            correlation_id=intervention_id,
        )
        emit_action_lifecycle(
            context,
            "action.completed",
            agent_id,
            action,
            causation_id=mutated.event_id,
        )
        managed.broker.publish_snapshot()

    async def stop_run(self, run_id: str) -> None:
        managed = self.get_run(run_id)
        managed.runner.stop()
        managed.broker.publish_status()
        await self._await_realtime_boundary(managed)
        await self._cancel_tasks(managed)

    async def close(self) -> None:
        failures: list[BaseException] = []
        for managed in tuple(self._runs.values()):
            try:
                task = managed.realtime_task
                if (
                    task is not None
                    and task is not asyncio.current_task()
                    and task.done()
                ):
                    await task
                if managed.runner.status is not RunnerStatus.STOPPED:
                    managed.runner.stop()
                await self._await_realtime_boundary(managed)
            except asyncio.CancelledError as error:
                failures.append(error)
                try:
                    self._finalize_failed_managed_run(managed)
                except Exception as cleanup_error:
                    failures.append(cleanup_error)
            except Exception as error:
                failures.append(error)
                try:
                    self._finalize_failed_managed_run(managed)
                except Exception as cleanup_error:
                    failures.append(cleanup_error)
            finally:
                try:
                    await self._cancel_tasks(managed)
                except asyncio.CancelledError as error:
                    failures.append(error)
                except Exception as error:
                    failures.append(error)
        try:
            self.dataset_store.close()
        except Exception as error:
            failures.append(error)
        if failures:
            raise failures[0]

    @staticmethod
    def _finalize_failed_managed_run(managed: ManagedRun) -> None:
        if not managed.collector.finalized:
            managed.collector.finalize("failed")
        if managed.runner.status is not RunnerStatus.STOPPED:
            managed.runner.stop()

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
