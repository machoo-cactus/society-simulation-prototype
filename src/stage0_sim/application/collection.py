from collections.abc import Mapping
from dataclasses import dataclass, field

from stage0_sim.application.data_capture import (
    DATASET_SCHEMA_VERSION,
    ActionId,
    DecisionId,
    EngagementGroupId,
    EngagementId,
    EngagementInvocationId,
    GoalId,
    InteractionId,
    MemoryId,
    ModelRequestId,
    OperatorInterventionId,
    PerceptionFactId,
    PlanId,
    RecordCategory,
    RecordJoinIds,
    RecordRelation,
    RecordSource,
    RecordVisibility,
    ResearchTrace,
    RunnerPhase,
    ToolCallId,
    TransactionRequestId,
    capture_registry_state,
    character_physical_state,
    opportunity_state,
    physical_object_states,
    physical_relation_samples,
    population_state,
    serialize_authoritative,
    state_delta,
)
from stage0_sim.application.dataset import AgentStateProjector, DatasetRecord
from stage0_sim.application.dataset_projection import DatasetRecordProjector
from stage0_sim.application.information import InformationStore
from stage0_sim.application.memory import EpisodicMemoryStore
from stage0_sim.application.ports import DatasetCaptureRepository
from stage0_sim.application.runner import SimulationRunner
from stage0_sim.domain.calendar import SimulationCalendar
from stage0_sim.domain.components import (
    ActivityComponent,
    AffordanceExecutionComponent,
    GoalComponent,
    GoalRuntime,
    PerceptionComponent,
    PositionComponent,
    SpatialLocationComponent,
    TextContentPersistenceBinding,
    TransactionExecutionComponent,
    TransactionRequestComponent,
)
from stage0_sim.domain.content import TextContentRegistry
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.environment import (
    EnvironmentAvailabilityRegistry,
    EnvironmentAvailabilityRules,
    SurfaceConditionRegistry,
    WeatherRuntime,
    wetness_band,
)
from stage0_sim.domain.events import (
    DomainEvent,
    JsonValue,
    event_payload_is_private,
)
from stage0_sim.domain.world import CityWorld


@dataclass(slots=True)
class _ActivityInterval:
    activity: str
    start_tick: int
    start_time: float


@dataclass(slots=True)
class _ActionEpisode:
    action_id: str
    agent_id: str | None
    created_tick: int
    created_at: float
    status: str
    source_event_ids: list[str]
    payload: dict[str, JsonValue]


@dataclass(slots=True)
class _DecisionEpisode:
    decision_id: str
    agent_id: str | None
    requested_tick: int
    requested_at: float
    status: str = "requested"
    selected_option_id: str | None = None
    tool_call_id: str | None = None
    action_id: str | None = None
    goal_id: str | None = None
    terminal_reason: str | None = None
    stage_times: dict[str, float] = field(default_factory=dict)
    context: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(slots=True)
class _InteractionEpisode:
    interaction_id: str
    interaction_type: str
    start_tick: int
    started_at: float
    status: str
    context: dict[str, JsonValue]
    participants: list[dict[str, JsonValue]]
    constituent_events: list[dict[str, JsonValue]]
    content_visibility: str
    record_id: str = ""
    interaction_verb: str | None = None
    actor_id: str | None = None
    target_id: str | None = None
    destination_id: str | None = None
    slot_id: str | None = None
    correlation_id: str | None = None
    initiating_goal_id: str | None = None
    initiating_decision_id: str | None = None
    initiating_action_id: str | None = None
    initiating_tool_call_id: str | None = None
    initiating_engagement_id: str | None = None
    record_visibility: RecordVisibility = RecordVisibility.PRIVATE_RESEARCH


@dataclass(slots=True)
class _EngagementInvocationProjection:
    invocation_id: str
    ordinal: int | None = None
    capability: str | None = None
    consequence_tier: int | None = None
    subject_id: str | None = None
    target_id: str | None = None
    status: str = "pending"
    private_proposal_arguments: dict[str, JsonValue] | None = None
    private_normalized_arguments: dict[str, JsonValue] | None = None
    private_result: dict[str, JsonValue] | None = None
    grounded_outcome: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(slots=True)
class _EngagementGroupProjection:
    group_id: str
    ordinal: int | None = None
    required_atomic: bool | None = None
    validation_status: str = "unknown"
    execution_status: str = "not_started"
    status: str = "pending"
    private_rejection_reason: str | None = None
    failure_reason: str | None = None
    private_issues: list[JsonValue] | None = None
    private_proposal: dict[str, JsonValue] | None = None
    grounded_outcome: dict[str, JsonValue] = field(default_factory=dict)
    invocations: dict[str, _EngagementInvocationProjection] = field(
        default_factory=dict
    )


@dataclass(slots=True)
class _EngagementProjection:
    engagement_id: str
    actor_id: str
    requested_tick: int
    requested_at: float
    action_id: str | None = None
    plan_id: str | None = None
    plan_revision: int | None = None
    decision_id: str | None = None
    tool_call_id: str | None = None
    compiler_request_id: str | None = None
    root_correlation_id: str | None = None
    referenced_ids: tuple[str, ...] | None = None
    scene_hash: str | None = None
    scene_version: str | None = None
    catalog_version: str | None = None
    prompt_version: str | None = None
    status: str = "requested"
    compiler_status: str | None = None
    private_intent: str | None = None
    private_controller_reason: str | None = None
    private_compiler_summary: str | None = None
    private_proposal: dict[str, JsonValue] | None = None
    private_result: dict[str, JsonValue] | None = None
    started_tick: int | None = None
    started_at: float | None = None
    terminal_tick: int | None = None
    terminal_at: float | None = None
    terminal_outcome: str | None = None
    groups: dict[str, _EngagementGroupProjection] = field(
        default_factory=dict
    )


@dataclass(slots=True)
class _GoalEpisode:
    goal_id: str
    subject_id: str
    activated_tick: int
    activated_at: float
    description: str
    source_event_ids: list[str] = field(default_factory=list)


class RunDataCollector:
    def __init__(
        self,
        *,
        store: DatasetCaptureRepository,
        runner: SimulationRunner,
        scenario: dict[str, JsonValue],
        projector: AgentStateProjector | None = None,
        private_provenance: dict[str, JsonValue] | None = None,
    ) -> None:
        self.store = store
        self.runner = runner
        self.projector = projector or AgentStateProjector()
        self.run_id = runner.events.run_id
        self._record_projector = DatasetRecordProjector(store, self.run_id)
        self._finalized = False
        self._activities: dict[str, _ActivityInterval] = {}
        self._previous_state_samples: dict[
            str, tuple[str, dict[str, JsonValue], int, float]
        ] = {}
        self._coverage_manifest: dict[str, JsonValue] | None = None
        self._action_episodes: dict[str, _ActionEpisode] = {}
        self._decision_episodes: dict[str, _DecisionEpisode] = {}
        self._interaction_episodes: dict[str, _InteractionEpisode] = {}
        self._engagements: dict[str, _EngagementProjection] = {}
        self._interaction_keys: dict[tuple[str, ...], str] = {}
        self._event_interactions: dict[str, tuple[str, ...]] = {}
        self._exposure_sequences: dict[tuple[str, ...], int] = {}
        self._perception_fact_records: dict[str, str] = {}
        self._goal_episodes: dict[str, _GoalEpisode] = {}
        self._memory_request_sources: dict[str, str] = {}
        self._event_lineage: dict[str, RecordJoinIds] = {}
        self._pending_action_outcomes: dict[
            str, list[dict[str, JsonValue]]
        ] = {}
        self._tool_names: dict[str, str] = {}
        self._research_pending = False
        self._capture_failed = False
        self._current_event_record_id: str | None = None
        store.begin_run(
            run_id=self.run_id,
            seed=runner.configuration.seed,
            dt=runner.configuration.dt,
            initial_speed=runner.configuration.speed,
            scenario=scenario,
        )
        if runner.registry.has_resource(EpisodicMemoryStore):
            memory_store = runner.registry.get_resource(EpisodicMemoryStore)
            memory_store.bind_persistence(store, self.run_id)
            for record in memory_store.records:
                self._append(
                    "memory_reference",
                    0,
                    record.simulation_time,
                    record.agent_id,
                    {
                        "event_type": "memory.initial",
                        "memory_id": record.id,
                        "importance": record.importance,
                        "text": record.text,
                        "embedding": list(record.embedding),
                        "memory_metadata": record.metadata,
                    },
                    None,
                    category=RecordCategory.MEMORY,
                    visibility=RecordVisibility.PRIVATE_RESEARCH,
                    joins=RecordJoinIds(memory_id=MemoryId(record.id)),
                )
        elif runner.registry.has_resource(InformationStore):
            runner.registry.get_resource(InformationStore).bind_persistence(
                store,
                self.run_id,
            )
        if runner.registry.has_resource(TextContentRegistry):
            text_content = runner.registry.get_resource(TextContentRegistry)
            store.save_text_content_snapshot(
                self.run_id,
                text_content.to_dict(),
            )
            runner.registry.set_resource(
                TextContentPersistenceBinding(
                    store.save_text_content_snapshot
                )
            )
        runner.events.subscribe(self._collect)
        runner.research.subscribe(self._research_available)
        runner.subscribe_phase(self._capture_phase)
        runner.subscribe_tick_completed(self._commit_tick)
        self._initialize_goals()
        self._capture_situation_provenance(
            private_provenance if private_provenance is not None else scenario
        )
        self._drain_research()

    def finalize(self, status: str = "completed") -> None:
        if self._finalized:
            return
        self.runner.flush_pending_memory()
        self._drain_research()
        self.runner.notify_run_final()
        self._drain_research()
        for agent_id in sorted(self._activities):
            self._close_activity(
                agent_id,
                self.runner.clock.tick,
                self.runner.clock.simulation_time,
                source_event_id=None,
            )
        self._close_open_engagements(
            self.runner.clock.tick,
            self.runner.clock.simulation_time,
        )
        self._close_open_interactions(
            self.runner.clock.tick,
            self.runner.clock.simulation_time,
        )
        self._close_open_goals(
            self.runner.clock.tick,
            self.runner.clock.simulation_time,
        )
        self.store.complete_run(
            self.run_id,
            status="capture_failed" if self._capture_failed else status,
            final_tick=self.runner.clock.tick,
            final_simulation_time=self.runner.clock.simulation_time,
        )
        self._finalized = True

    @property
    def finalized(self) -> bool:
        return self._finalized

    @property
    def _sequence(self) -> int:
        return self._record_projector.sequence

    def _collect(self, event: DomainEvent) -> None:
        if self._finalized:
            return
        joins = self._resolved_event_joins(event)
        visibility = _event_visibility(event)
        self._event_lineage[event.event_id] = joins
        event_record = self._append(
            "event",
            event.simulation_tick,
            event.simulation_time,
            event.agent_id,
            {
                "event_type": event.event_type,
                "wall_time": event.wall_time.isoformat(),
                "payload": dict(event.payload),
                "causation_id": event.causation_id,
                "correlation_id": event.correlation_id,
            },
            event.event_id,
            category=RecordCategory.EVENT,
            source=RecordSource.DOMAIN_EVENT,
            joins=joins,
            causation_id=event.causation_id,
            correlation_id=event.correlation_id,
            visibility=visibility,
        )
        self._current_event_record_id = event_record.record_id
        try:
            self._collect_specialized(event)
        finally:
            self._current_event_record_id = None
        if event.event_type == "simulation.started":
            self._initialize_activities(event)
            self._collect_tick_state(event)
        elif event.event_type == "activity.changed" and event.agent_id is not None:
            self._change_activity(event)
        elif event.event_type == "simulation.stopped":
            self.finalize("stopped")
        if event.event_type in {
            "simulation.started",
            "simulation.paused",
            "simulation.resumed",
        }:
            self.store.flush()

    def _commit_tick(self, event: DomainEvent) -> None:
        if self._finalized:
            return
        self._collect_tick_state(event)
        self._drain_research()
        self.store.flush()

    def _capture_phase(
        self,
        phase: RunnerPhase,
        runner: SimulationRunner,
        _context: object,
    ) -> None:
        if self._finalized:
            return
        self._drain_research()
        snapshot = capture_registry_state(runner.registry)
        coverage = snapshot["coverage"]
        if not isinstance(coverage, dict):
            raise TypeError("capture coverage must be an object")
        if coverage != self._coverage_manifest:
            self._append(
                "capture_coverage",
                runner.clock.tick,
                runner.clock.simulation_time,
                None,
                coverage,
                None,
                category=RecordCategory.PROVENANCE,
                source=RecordSource.DATASET_COLLECTOR,
                phase=phase,
                visibility=RecordVisibility.PRIVATE_RESEARCH,
            )
            self._coverage_manifest = coverage
        self._append(
            "phase_state",
            runner.clock.tick,
            runner.clock.simulation_time,
            None,
            {
                "phase": phase.value,
                "state": snapshot,
            },
            None,
            category=RecordCategory.STATE,
            source=RecordSource.RUNNER,
            phase=phase,
            visibility=RecordVisibility.PRIVATE_RESEARCH,
        )
        entities = snapshot["entities"]
        if not isinstance(entities, list):
            raise TypeError("captured entity state must be a list")
        for entity in entities:
            if not isinstance(entity, dict):
                raise TypeError("captured entity state must be an object")
            entity_id = entity.get("entity_id")
            if not isinstance(entity_id, str):
                raise TypeError("captured entity ID must be a string")
            self._persist_state_sample(phase, entity_id, entity)
            context, options = opportunity_state(runner.registry, entity_id)
            context["phase"] = phase.value
            context["feature_schema"] = "stage0.feature.opportunity_sample.v1"
            annotated_options: list[JsonValue] = [
                {**option, "selected": False}
                if isinstance(option, dict)
                else option
                for option in options
            ]
            opportunity = self._append(
                "opportunity_sample",
                runner.clock.tick,
                runner.clock.simulation_time,
                entity_id,
                {
                    "context": context,
                    "options": annotated_options,
                    "selected_option_id": None,
                    "choice_status": "non_choice",
                    "option_count": len(annotated_options),
                },
                None,
                category=RecordCategory.OPPORTUNITY,
                source=RecordSource.DERIVED,
                phase=phase,
                visibility=RecordVisibility.PRIVATE_RESEARCH,
                schema_id="stage0.feature.opportunity_sample",
                schema_version="1",
            )
            self.store.append_opportunity_sample(
                run_id=self.run_id,
                opportunity_sample_id=(
                    f"{opportunity.record_id}:opportunity"
                ),
                record_id=opportunity.record_id,
                subject_id=entity_id,
                simulation_tick=runner.clock.tick,
                selected_option_id=None,
                context=context,
                options=annotated_options,
            )
        self._capture_physical_observations(phase)
        resources = snapshot["resources"]
        if not isinstance(resources, dict):
            raise TypeError("captured resource state must be an object")
        self._persist_state_sample(
            phase,
            None,
            {
                "entities": entities,
                "resources": resources,
            },
        )
        population = population_state(runner.registry)
        population["feature_schema"] = "stage0.feature.population_sample.v1"
        population_record = self._append(
            "population_sample",
            runner.clock.tick,
            runner.clock.simulation_time,
            None,
            population,
            None,
            category=RecordCategory.POPULATION,
            source=RecordSource.DERIVED,
            phase=phase,
            visibility=RecordVisibility.PRIVATE_RESEARCH,
            schema_id="stage0.feature.population_sample",
            schema_version="1",
        )
        self.store.append_population_sample(
            run_id=self.run_id,
            population_sample_id=f"{population_record.record_id}:population",
            record_id=population_record.record_id,
            simulation_tick=runner.clock.tick,
            phase=phase,
            population=population,
        )
        self._capture_resource_samples(phase)
        self._capture_exposure_intervals(phase)
        self._drain_research()

    def _capture_physical_observations(self, phase: RunnerPhase) -> None:
        registry = self.runner.registry
        for state in physical_object_states(registry):
            object_id = _required_text(state, "object_id")
            pose = _required_object(state, "pose")
            anchor = _required_object(pose, "anchor")
            obstruction = _required_object(state, "obstruction")
            intrinsics = _optional_object(state, "intrinsics")
            openable = _optional_object(state, "openable")
            parent = _optional_object(state, "parent_relation")
            custody = _optional_object(state, "custody")
            spatial_index = _required_object(state, "spatial_index")
            related_ids = _physical_related_ids(state)
            record = self._append(
                "physical_object_state",
                self.runner.clock.tick,
                self.runner.clock.simulation_time,
                object_id,
                state,
                None,
                category=RecordCategory.STATE,
                source=RecordSource.DATASET_COLLECTOR,
                phase=phase,
                visibility=RecordVisibility.PRIVATE_RESEARCH,
                related_entity_ids=related_ids,
                schema_id="stage0.feature.physical_object_state",
                schema_version="2",
            )
            self.store.append_physical_object_state(
                run_id=self.run_id,
                physical_state_id=f"{record.record_id}:physical-state",
                record_id=record.record_id,
                object_id=object_id,
                definition_id=_required_text(state, "definition_id"),
                name=_required_text(state, "name"),
                room_id=_required_text(pose, "room_id"),
                anchor_x=_required_int(anchor, "x"),
                anchor_y=_required_int(anchor, "y"),
                orientation=_required_text(pose, "orientation"),
                phase=phase,
                simulation_tick=self.runner.clock.tick,
                simulation_time=self.runner.clock.simulation_time,
                movement_obstruction=_required_text(
                    obstruction, "movement"
                ),
                vision_obstruction=_required_text(
                    obstruction, "vision"
                ),
                hearing_transmission=_required_text(
                    obstruction, "hearing"
                ),
                smell_transmission=_required_text(
                    obstruction, "smell"
                ),
                blocks_movement=_required_bool(
                    obstruction, "blocks_movement"
                ),
                blocks_vision=_required_bool(
                    obstruction, "blocks_vision"
                ),
                blocks_hearing=_required_bool(
                    obstruction, "blocks_hearing"
                ),
                blocks_smell=_required_bool(
                    obstruction, "blocks_smell"
                ),
                mass_kg=(
                    _optional_float(intrinsics, "mass_kg")
                    if intrinsics is not None
                    else None
                ),
                size_class=(
                    _optional_text(intrinsics, "size_class")
                    if intrinsics is not None
                    else None
                ),
                is_open=(
                    _required_bool(openable, "is_open")
                    if openable is not None
                    else None
                ),
                is_locked=(
                    _required_bool(openable, "is_locked")
                    if openable is not None
                    else None
                ),
                parent_id=(
                    _optional_text(parent, "parent_id")
                    if parent is not None
                    else None
                ),
                relation_kind=(
                    _optional_text(parent, "relation_kind")
                    if parent is not None
                    else None
                ),
                slot_id=(
                    _optional_text(parent, "slot_id")
                    if parent is not None
                    else None
                ),
                custodian_id=(
                    _optional_text(custody, "custodian_id")
                    if custody is not None
                    else None
                ),
                held_by_id=(
                    _optional_text(custody, "held_by_id")
                    if custody is not None
                    else None
                ),
                spatial_index_revision=_optional_int(
                    spatial_index, "revision"
                ),
                topology_revision=_optional_int(
                    spatial_index, "topology_revision"
                ),
                state=state,
            )
        for relation in physical_relation_samples(registry):
            object_id = _required_text(relation, "object_id")
            spatial_index = _required_object(
                relation, "spatial_index"
            )
            parent_id = _required_text(relation, "parent_id")
            related_ids = tuple(
                dict.fromkeys(
                    value
                    for value in (
                        parent_id,
                        _optional_text(relation, "custodian_id"),
                        _optional_text(relation, "held_by_id"),
                    )
                    if value is not None and value != object_id
                )
            )
            record = self._append(
                "physical_relation_sample",
                self.runner.clock.tick,
                self.runner.clock.simulation_time,
                object_id,
                relation,
                None,
                category=RecordCategory.STATE,
                source=RecordSource.DATASET_COLLECTOR,
                phase=phase,
                visibility=RecordVisibility.PRIVATE_RESEARCH,
                related_entity_ids=related_ids,
                schema_id="stage0.feature.physical_relation_sample",
                schema_version="1",
            )
            self.store.append_physical_relation_sample(
                run_id=self.run_id,
                relation_sample_id=(
                    f"{record.record_id}:physical-relation"
                ),
                record_id=record.record_id,
                object_id=object_id,
                entity_kind=_required_text(relation, "entity_kind"),
                room_id=_optional_text(relation, "room_id"),
                parent_id=parent_id,
                parent_kind=_required_text(relation, "parent_kind"),
                relation_kind=_required_text(
                    relation, "relation_kind"
                ),
                slot_id=_optional_text(relation, "slot_id"),
                custodian_id=_optional_text(
                    relation, "custodian_id"
                ),
                held_by_id=_optional_text(relation, "held_by_id"),
                phase=phase,
                simulation_tick=self.runner.clock.tick,
                simulation_time=self.runner.clock.simulation_time,
                spatial_index_revision=_optional_int(
                    spatial_index, "revision"
                ),
                topology_revision=_optional_int(
                    spatial_index, "topology_revision"
                ),
                relation=relation,
            )
        for entity_id in registry.entities():
            character_state = character_physical_state(registry, entity_id)
            if character_state is None:
                continue
            self._append(
                "character_physical_state",
                self.runner.clock.tick,
                self.runner.clock.simulation_time,
                entity_id,
                character_state,
                None,
                category=RecordCategory.STATE,
                source=RecordSource.DATASET_COLLECTOR,
                phase=phase,
                visibility=RecordVisibility.PRIVATE_RESEARCH,
                related_entity_ids=_physical_related_ids(character_state),
                schema_id="stage0.feature.character_physical_state",
                schema_version="2",
            )

    def _capture_resource_samples(self, phase: RunnerPhase) -> None:
        from stage0_sim.domain.world import WorldMap

        if not self.runner.registry.has_resource(WorldMap):
            return
        resource_worlds: list[
            tuple[str | None, str | None, WorldMap]
        ]
        if self.runner.registry.has_resource(CityWorld):
            city = self.runner.registry.get_resource(CityWorld)
            resource_worlds = [
                (room.id, room.building_id, room.world)
                for room in city.rooms
            ]
        else:
            resource_worlds = [
                (
                    "implicit-building",
                    "implicit-building",
                    self.runner.registry.get_resource(WorldMap),
                )
            ]
        station_occupancy: dict[str, int] = {}
        for _, affordance_execution in self.runner.registry.query(
            AffordanceExecutionComponent
        ):
            station_occupancy[affordance_execution.station_id] = (
                station_occupancy.get(affordance_execution.station_id, 0) + 1
            )
        point_occupancy: dict[str, int] = {}
        for _, transaction_execution in self.runner.registry.query(
            TransactionExecutionComponent
        ):
            point_occupancy[transaction_execution.point_id] = (
                point_occupancy.get(transaction_execution.point_id, 0) + 1
            )
        point_queues: dict[str, int] = {}
        for _, request in self.runner.registry.query(
            TransactionRequestComponent
        ):
            if request.status in {
                "awaiting_staff",
                "awaiting_authorization",
                "authorized",
            }:
                point_queues[request.point_id] = (
                    point_queues.get(request.point_id, 0) + 1
                )
        samples: list[dict[str, JsonValue]] = []
        for room_id, building_id, world in resource_worlds:
            for station in world.stations:
                occupancy = station_occupancy.get(station.id, 0)
                samples.append(
                    {
                        "feature_schema": "stage0.feature.resource_sample.v1",
                        "resource_id": station.id,
                        "resource_type": "affordance_station",
                        "room_id": room_id,
                        "building_id": building_id,
                        "capacity": station.capacity,
                        "occupancy": occupancy,
                        "queue_length": 0,
                        "utilization": round(
                            occupancy / station.capacity,
                            12,
                        ),
                    }
                )
            for point in world.transaction_points:
                occupancy = point_occupancy.get(point.id, 0)
                samples.append(
                    {
                        "feature_schema": "stage0.feature.resource_sample.v1",
                        "resource_id": point.id,
                        "resource_type": "transaction_point",
                        "room_id": room_id,
                        "building_id": building_id,
                        "capacity": point.capacity,
                        "occupancy": occupancy,
                        "queue_length": point_queues.get(point.id, 0),
                        "utilization": round(
                            occupancy / point.capacity,
                            12,
                        ),
                    }
                )
        for sample in sorted(
            samples,
            key=lambda item: (
                str(item["resource_type"]),
                str(item["resource_id"]),
            ),
        ):
            record = self._append(
                "resource_sample",
                self.runner.clock.tick,
                self.runner.clock.simulation_time,
                None,
                sample,
                None,
                category=RecordCategory.ENVIRONMENT,
                source=RecordSource.DERIVED,
                phase=phase,
                visibility=RecordVisibility.PRIVATE_RESEARCH,
                schema_id="stage0.feature.resource_sample",
                schema_version="1",
            )
            capacity = sample["capacity"]
            raw_occupancy = sample["occupancy"]
            raw_queue_length = sample["queue_length"]
            utilization = sample["utilization"]
            if (
                not isinstance(raw_occupancy, int)
                or isinstance(raw_occupancy, bool)
                or not isinstance(raw_queue_length, int)
                or isinstance(raw_queue_length, bool)
            ):
                raise TypeError("resource counts must be integers")
            self.store.append_resource_sample(
                run_id=self.run_id,
                resource_sample_id=f"{record.record_id}:resource",
                record_id=record.record_id,
                resource_id=str(sample["resource_id"]),
                resource_type=str(sample["resource_type"]),
                simulation_tick=self.runner.clock.tick,
                phase=phase,
                capacity=capacity if isinstance(capacity, int) else None,
                occupancy=raw_occupancy,
                queue_length=raw_queue_length,
                utilization=(
                    float(utilization)
                    if isinstance(utilization, int | float)
                    else None
                ),
                sample=sample,
            )

    def _capture_exposure_intervals(self, phase: RunnerPhase) -> None:
        if phase is RunnerPhase.RUN_FINAL:
            self._close_exposure_interactions(
                self.runner.clock.tick,
                self.runner.clock.simulation_time,
            )
            return
        if phase is not RunnerPhase.TICK_POST_SYSTEMS:
            return
        registry = self.runner.registry
        entities = tuple(
            sorted(registry.query_entities(PositionComponent))
        )
        current: set[tuple[str, ...]] = set()
        for index, first in enumerate(entities):
            for second in entities[index + 1 :]:
                if not _share_place(registry, first, second):
                    continue
                key: tuple[str, ...] = ("co_presence", first, second)
                current.add(key)
                if key not in self._interaction_keys:
                    self._start_interval_interaction(
                        key,
                        "co_presence",
                        (
                            self._participant(first, "participant"),
                            self._participant(second, "participant"),
                        ),
                        "LOCAL_VISUAL",
                    )
        for observer_id in entities:
            if not registry.has_component(observer_id, PerceptionComponent):
                continue
            perception = registry.get_component(
                observer_id, PerceptionComponent
            )
            for subject_id in sorted(perception.visible_now):
                key = ("visibility", observer_id, subject_id)
                current.add(key)
                if key not in self._interaction_keys:
                    self._start_interval_interaction(
                        key,
                        "visibility",
                        (
                            self._participant(observer_id, "observer"),
                            self._participant(subject_id, "subject"),
                        ),
                        "LOCAL_VISUAL",
                    )
        active_exposures = {
            key
            for key in self._interaction_keys
            if key and key[0] in {"co_presence", "visibility"}
        }
        for key in sorted(active_exposures - current):
            interaction_id = self._interaction_keys.get(key)
            if interaction_id is not None:
                self._close_interaction(
                    interaction_id,
                    "ended",
                    self.runner.clock.tick,
                    self.runner.clock.simulation_time,
                    {"reason": "exposure_ended"},
                )

    def _start_interval_interaction(
        self,
        key: tuple[str, ...],
        interaction_type: str,
        participants: tuple[dict[str, JsonValue], ...],
        content_visibility: str,
    ) -> None:
        sequence = self._exposure_sequences.get(key, 0) + 1
        self._exposure_sequences[key] = sequence
        interaction_id = (
            f"interaction:{interaction_type}:"
            f"{':'.join(key[1:])}:{sequence:04d}"
        )
        self._start_interaction(
            interaction_id,
            interaction_type,
            key,
            participants,
            content_visibility,
            context={
                "exposure": True,
                "location_context": self._participant_locations(participants),
            },
        )
        self._append_interaction_boundary(
            interaction_id,
            "interval.started",
            self.runner.clock.tick,
            self.runner.clock.simulation_time,
        )

    def _close_exposure_interactions(
        self,
        terminal_tick: int,
        terminal_at: float,
    ) -> None:
        for key in sorted(tuple(self._interaction_keys)):
            if key and key[0] in {"co_presence", "visibility"}:
                interaction_id = self._interaction_keys.get(key)
                if interaction_id is not None:
                    self._close_interaction(
                        interaction_id,
                        "ended",
                        terminal_tick,
                        terminal_at,
                        {"reason": "run_final"},
                    )

    def _collect_perception_event(self, event: DomainEvent) -> None:
        fact = event.payload.get("fact")
        fact_id = event.payload.get("fact_id")
        if not isinstance(fact, dict) or not isinstance(fact_id, str):
            return
        fact_record_id = self._perception_fact_records.get(fact_id)
        event_joins = self._resolved_event_joins(event)
        if fact_record_id is None:
            raw_subject_id = fact.get("subject_id")
            subject_id = (
                raw_subject_id if isinstance(raw_subject_id, str) else None
            )
            raw_event_id = fact.get("event_id")
            source_event_id = (
                raw_event_id if isinstance(raw_event_id, str) else None
            )
            raw_object_id = fact.get("object_id")
            object_id = (
                raw_object_id if isinstance(raw_object_id, str) else None
            )
            raw_location_id = fact.get("location_id")
            location_id = (
                raw_location_id if isinstance(raw_location_id, str) else None
            )
            raw_tick = fact.get("tick")
            created_tick = (
                raw_tick
                if isinstance(raw_tick, int) and not isinstance(raw_tick, bool)
                else event.simulation_tick
            )
            fact_record = self._append(
                "perception_fact",
                event.simulation_tick,
                event.simulation_time,
                subject_id,
                {"fact": fact},
                event.causation_id,
                category=RecordCategory.PERCEPTION,
                source=RecordSource.APPLICATION,
                visibility=RecordVisibility.OPERATOR,
                joins=RecordJoinIds(
                    perception_fact_id=PerceptionFactId(fact_id),
                    engagement_id=event_joins.engagement_id,
                    engagement_group_id=event_joins.engagement_group_id,
                    engagement_invocation_id=(
                        event_joins.engagement_invocation_id
                    ),
                ),
                causation_id=event.causation_id,
                schema_id="stage0.perception.fact",
                schema_version="2",
            )
            fact_record_id = fact_record.record_id
            self._perception_fact_records[fact_id] = fact_record_id
            self.store.append_perception_fact(
                run_id=self.run_id,
                fact_id=fact_id,
                record_id=fact_record_id,
                source_event_id=source_event_id,
                fact_type=str(fact.get("fact_type", "unknown")),
                subject_id=subject_id,
                object_id=object_id,
                location_id=location_id,
                modality=str(fact.get("modality", "unknown")),
                disclosure=str(fact.get("disclosure", "unknown")),
                created_tick=created_tick,
                fact=fact,
            )
        observer_id = event.payload.get("observer_id")
        if not isinstance(observer_id, str):
            observer_id = event.agent_id
        if observer_id is None:
            return
        delivery_id = f"perception-delivery:{event.event_id}"
        status = (
            "delivered"
            if event.event_type == "perception.delivered"
            else "dropped"
        )
        payload: dict[str, JsonValue] = {
            "delivery_id": delivery_id,
            "fact_id": fact_id,
            "observer_id": observer_id,
            "status": status,
            "reason": event.payload.get("reason"),
            "perceived_tick": event.payload.get(
                "perceived_tick", event.simulation_tick
            ),
            "fact_age": event.payload.get("fact_age", 0.0),
            "salience": event.payload.get("salience"),
            "certainty": event.payload.get("certainty"),
            "modality": fact.get("modality"),
            "disclosure": fact.get("disclosure"),
            "subject_id": fact.get("subject_id"),
            "fact_type": fact.get("fact_type"),
        }
        record = self._append(
            "perception_delivery",
            event.simulation_tick,
            event.simulation_time,
            observer_id,
            payload,
            event.event_id,
            category=RecordCategory.PERCEPTION,
            source=RecordSource.APPLICATION,
            visibility=RecordVisibility.OPERATOR,
            joins=RecordJoinIds(
                perception_fact_id=PerceptionFactId(fact_id),
                engagement_id=event_joins.engagement_id,
                engagement_group_id=event_joins.engagement_group_id,
                engagement_invocation_id=(
                    event_joins.engagement_invocation_id
                ),
            ),
            causation_id=event.event_id,
            schema_id="stage0.perception.delivery",
            schema_version="1",
        )
        salience = payload["salience"]
        perceived_tick = payload["perceived_tick"]
        fact_age = payload["fact_age"]
        if (
            not isinstance(perceived_tick, int)
            or isinstance(perceived_tick, bool)
            or not isinstance(fact_age, int | float)
            or isinstance(fact_age, bool)
        ):
            raise TypeError("perception delivery timing is invalid")
        self.store.append_perception_delivery(
            run_id=self.run_id,
            delivery_id=delivery_id,
            fact_id=fact_id,
            record_id=record.record_id,
            observer_id=observer_id,
            status=status,
            reason=(
                payload["reason"] if isinstance(payload["reason"], str) else None
            ),
            perceived_tick=perceived_tick,
            fact_age=float(fact_age),
            salience=(
                float(salience)
                if isinstance(salience, int | float)
                and not isinstance(salience, bool)
                else None
            ),
            delivery=payload,
        )

    def _collect_resource_flow(self, event: DomainEvent) -> None:
        resource_id = event.payload.get("point_id")
        if not isinstance(resource_id, str):
            resource_id = event.payload.get("station_id")
        if not isinstance(resource_id, str):
            return
        flow_type = event.event_type
        amount: float | None = None
        if flow_type.endswith((".started", ".completed", ".failed", ".cancelled")):
            amount = 1.0
        payload: dict[str, JsonValue] = {
            "feature_schema": "stage0.feature.resource_flow.v1",
            "resource_id": resource_id,
            "flow_type": flow_type,
            "amount": amount,
            "event_id": event.event_id,
            "event_payload": dict(event.payload),
        }
        record = self._append(
            "resource_flow",
            event.simulation_tick,
            event.simulation_time,
            event.agent_id,
            payload,
            event.event_id,
            category=RecordCategory.ENVIRONMENT,
            source=RecordSource.DERIVED,
            visibility=RecordVisibility.PRIVATE_RESEARCH,
            joins=self._resolved_event_joins(event),
            schema_id="stage0.feature.resource_flow",
            schema_version="1",
        )
        self.store.append_resource_flow(
            run_id=self.run_id,
            resource_flow_id=f"{record.record_id}:flow",
            record_id=record.record_id,
            resource_id=resource_id,
            subject_id=event.agent_id,
            simulation_tick=event.simulation_tick,
            flow_type=flow_type,
            amount=amount,
            flow=payload,
        )

    def _collect_memory_relation_event(self, event: DomainEvent) -> None:
        if event.event_type == "memory.requested":
            if event.causation_id is not None:
                self._memory_request_sources[event.event_id] = event.causation_id
            return
        if event.event_type != "memory.recorded":
            return
        memory_id = event.payload.get("memory_id")
        if not isinstance(memory_id, str):
            return
        source_id = (
            self._memory_request_sources.get(event.causation_id, event.causation_id)
            if event.causation_id is not None
            else None
        )
        if source_id is None:
            return
        self._append_memory_relation(
            memory_id=memory_id,
            subject_id=event.agent_id,
            relation_type="source",
            source_type="event",
            source_id=source_id,
            tick=event.simulation_tick,
            simulation_time=event.simulation_time,
        )

    def _append_memory_relation(
        self,
        *,
        memory_id: str,
        subject_id: str | None,
        relation_type: str,
        source_type: str,
        source_id: str,
        tick: int,
        simulation_time: float,
    ) -> None:
        relation_id = (
            f"memory-relation:{memory_id}:{relation_type}:"
            f"{source_type}:{source_id}"
        )
        payload: dict[str, JsonValue] = {
            "relation_id": relation_id,
            "memory_id": memory_id,
            "relation_type": relation_type,
            "source_type": source_type,
            "source_id": source_id,
        }
        record = self._append(
            "memory_relation",
            tick,
            simulation_time,
            subject_id,
            payload,
            None,
            category=RecordCategory.MEMORY,
            source=RecordSource.DERIVED,
            visibility=RecordVisibility.PRIVATE_RESEARCH,
            joins=RecordJoinIds(memory_id=MemoryId(memory_id)),
            schema_id="stage0.memory.relation",
            schema_version="1",
        )
        self.store.append_memory_relation(
            run_id=self.run_id,
            relation_id=relation_id,
            record_id=record.record_id,
            memory_id=memory_id,
            subject_id=subject_id,
            relation_type=relation_type,
            source_type=source_type,
            source_id=source_id,
            relation=payload,
        )

    def _collect_engagement_event(self, event: DomainEvent) -> None:
        engagement_id = event.payload.get("engagement_id")
        actor_id = event.agent_id
        if not isinstance(engagement_id, str) or actor_id is None:
            return
        engagement = self._engagements.get(engagement_id)
        if engagement is None:
            engagement = _EngagementProjection(
                engagement_id=engagement_id,
                actor_id=actor_id,
                requested_tick=event.simulation_tick,
                requested_at=event.simulation_time,
            )
            self._engagements[engagement_id] = engagement
        self._update_engagement_lineage(engagement, event.payload)
        event_type = event.event_type
        if event_type == "engagement.requested":
            engagement.status = "requested"
            engagement.private_intent = _optional_string(
                event.payload.get("intent")
            )
            engagement.private_controller_reason = _optional_string(
                event.payload.get("reason")
            )
            engagement.referenced_ids = _optional_string_tuple(
                event.payload.get("reference_ids")
            )
        elif event_type == "engagement.compilation_requested":
            engagement.status = "compiling"
            engagement.compiler_status = "requested"
            self._update_engagement_versions(engagement, event.payload)
        elif event_type == "engagement.compilation_completed":
            engagement.status = "compiled"
            engagement.compiler_status = "completed"
            engagement.scene_hash = _optional_string(
                event.payload.get("scene_hash")
            )
            engagement.private_compiler_summary = _optional_string(
                event.payload.get("summary")
            )
            self._update_engagement_versions(engagement, event.payload)
            self._project_compiled_groups(engagement, event.payload)
        elif event_type == "engagement.compilation_failed":
            engagement.status = "compilation_failed"
            engagement.compiler_status = "failed"
            engagement.private_result = dict(event.payload)
            self._update_engagement_versions(engagement, event.payload)
        elif event_type == "engagement.compilation_cancelled":
            engagement.compiler_status = "cancelled"
            engagement.private_result = dict(event.payload)
        elif event_type == "engagement.started":
            engagement.status = "running"
            engagement.started_tick = event.simulation_tick
            engagement.started_at = event.simulation_time
        elif event_type in {
            "engagement.group_completed",
            "engagement.group_failed",
        }:
            self._project_engagement_group_event(engagement, event)
        elif event_type == "engagement.capability_committed":
            self._project_engagement_invocation_event(engagement, event)
        if event_type in {
            "engagement.completed",
            "engagement.partial",
            "engagement.failed",
            "engagement.cancelled",
        }:
            engagement.status = event_type.removeprefix("engagement.")
            engagement.terminal_outcome = engagement.status
            engagement.terminal_tick = event.simulation_tick
            engagement.terminal_at = event.simulation_time
            self._project_terminal_group_statuses(
                engagement,
                event.payload,
            )
        private_event = event_payload_is_private(event.payload)
        if private_event:
            self._persist_engagement_feature(
                engagement,
                event.simulation_tick,
                event.simulation_time,
                event.event_id,
                event.correlation_id,
                include_private=True,
                visibility=RecordVisibility.PRIVATE_RESEARCH,
            )
        else:
            self._persist_engagement_feature(
                engagement,
                event.simulation_tick,
                event.simulation_time,
                event.event_id,
                event.correlation_id,
                include_private=True,
                visibility=RecordVisibility.PRIVATE_RESEARCH,
            )
        if not private_event or event_type == "engagement.capability_committed":
            self._persist_engagement_feature(
                engagement,
                event.simulation_tick,
                event.simulation_time,
                event.event_id,
                event.correlation_id,
                include_private=False,
                visibility=RecordVisibility.OPERATOR,
            )

    @staticmethod
    def _update_engagement_lineage(
        engagement: _EngagementProjection,
        payload: Mapping[str, JsonValue],
    ) -> None:
        for attribute, name in (
            ("action_id", "action_id"),
            ("plan_id", "plan_id"),
            ("decision_id", "decision_id"),
            ("tool_call_id", "tool_call_id"),
            ("root_correlation_id", "root_correlation_id"),
        ):
            value = payload.get(name)
            if isinstance(value, str):
                setattr(engagement, attribute, value)
        plan_revision = payload.get("plan_revision")
        if isinstance(plan_revision, int) and not isinstance(
            plan_revision,
            bool,
        ):
            engagement.plan_revision = plan_revision

    @staticmethod
    def _update_engagement_versions(
        engagement: _EngagementProjection,
        payload: Mapping[str, JsonValue],
    ) -> None:
        for attribute, name in (
            ("scene_version", "scene_version"),
            ("catalog_version", "catalog_version"),
            ("prompt_version", "prompt_version"),
        ):
            value = payload.get(name)
            if isinstance(value, str):
                setattr(engagement, attribute, value)

    @staticmethod
    def _project_compiled_groups(
        engagement: _EngagementProjection,
        payload: Mapping[str, JsonValue],
    ) -> None:
        valid_groups = payload.get("valid_groups")
        if isinstance(valid_groups, list):
            for group_value in valid_groups:
                if not isinstance(group_value, dict):
                    continue
                group_id = group_value.get("group_id")
                if not isinstance(group_id, str):
                    continue
                group = engagement.groups.setdefault(
                    group_id,
                    _EngagementGroupProjection(group_id),
                )
                group.ordinal = _optional_integer_value(
                    group_value.get("ordinal")
                )
                group.required_atomic = _optional_boolean_value(
                    group_value.get("required_atomic")
                )
                group.validation_status = "valid"
                group.execution_status = "pending"
                group.status = "pending"
                group.private_proposal = dict(group_value)
                invocations = group_value.get("invocations")
                if not isinstance(invocations, list):
                    continue
                for invocation_value in invocations:
                    if not isinstance(invocation_value, dict):
                        continue
                    invocation_id = invocation_value.get("invocation_id")
                    if not isinstance(invocation_id, str):
                        continue
                    invocation = group.invocations.setdefault(
                        invocation_id,
                        _EngagementInvocationProjection(invocation_id),
                    )
                    invocation.ordinal = _optional_integer_value(
                        invocation_value.get("ordinal")
                    )
                    invocation.capability = _optional_string(
                        invocation_value.get("capability")
                    )
                    invocation.consequence_tier = _optional_integer_value(
                        invocation_value.get("consequence_tier")
                    )
                    normalized_arguments = invocation_value.get("arguments")
                    if isinstance(normalized_arguments, dict):
                        invocation.private_normalized_arguments = dict(
                            normalized_arguments
                        )
                        invocation.subject_id = _optional_string(
                            normalized_arguments.get("subject_id")
                        )
                        invocation.target_id = _optional_string(
                            normalized_arguments.get("target_id")
                        )
        rejected_groups = payload.get("rejected_groups")
        if not isinstance(rejected_groups, list):
            return
        for group_value in rejected_groups:
            if not isinstance(group_value, dict):
                continue
            group_id = group_value.get("group_id")
            if not isinstance(group_id, str):
                continue
            group = engagement.groups.setdefault(
                group_id,
                _EngagementGroupProjection(group_id),
            )
            group.ordinal = _optional_integer_value(
                group_value.get("ordinal")
            )
            group.validation_status = "rejected"
            group.execution_status = "not_run"
            group.status = "rejected"
            issues = group_value.get("issues")
            if isinstance(issues, list):
                group.private_issues = list(issues)
                group.private_rejection_reason = _first_issue_code(issues)
            group.private_proposal = dict(group_value)

    @staticmethod
    def _project_engagement_group_event(
        engagement: _EngagementProjection,
        event: DomainEvent,
    ) -> None:
        group_id = event.payload.get("group_id")
        if not isinstance(group_id, str):
            return
        group = engagement.groups.setdefault(
            group_id,
            _EngagementGroupProjection(group_id),
        )
        group.ordinal = (
            _optional_integer_value(event.payload.get("group_ordinal"))
            if group.ordinal is None
            else group.ordinal
        )
        if group.required_atomic is None:
            group.required_atomic = _optional_boolean_value(
                event.payload.get("required_atomic")
            )
        group.validation_status = "valid"
        group.execution_status = event.event_type.removeprefix(
            "engagement.group_"
        )
        group.status = group.execution_status
        group.failure_reason = _optional_string(event.payload.get("reason"))
        group.grounded_outcome = _public_engagement_event_payload(event)
        invocation_ids = _optional_string_tuple(
            event.payload.get("invocation_ids")
        )
        for invocation_id in invocation_ids or ():
            invocation = group.invocations.setdefault(
                invocation_id,
                _EngagementInvocationProjection(invocation_id),
            )
            if (
                event.event_type == "engagement.group_failed"
                and invocation.status != "committed"
            ):
                invocation.status = "failed"

    @staticmethod
    def _project_engagement_invocation_event(
        engagement: _EngagementProjection,
        event: DomainEvent,
    ) -> None:
        group_id = event.payload.get("group_id")
        invocation_id = event.payload.get("invocation_id")
        if not isinstance(group_id, str) or not isinstance(
            invocation_id,
            str,
        ):
            return
        group = engagement.groups.setdefault(
            group_id,
            _EngagementGroupProjection(group_id),
        )
        group.validation_status = "valid"
        if group.execution_status == "not_started":
            group.execution_status = "running"
            group.status = "running"
        invocation = group.invocations.setdefault(
            invocation_id,
            _EngagementInvocationProjection(invocation_id),
        )
        invocation.ordinal = _optional_integer_value(
            event.payload.get("invocation_ordinal")
        )
        invocation.capability = _optional_string(
            event.payload.get("capability")
        )
        invocation.consequence_tier = _optional_integer_value(
            event.payload.get("consequence_tier")
        )
        invocation.status = "committed"
        invocation.private_result = dict(event.payload)
        invocation.grounded_outcome = _public_engagement_event_payload(event)

    @staticmethod
    def _project_terminal_group_statuses(
        engagement: _EngagementProjection,
        payload: Mapping[str, JsonValue],
    ) -> None:
        statuses = payload.get("group_statuses")
        if not isinstance(statuses, list):
            return
        for status_value in statuses:
            if not isinstance(status_value, dict):
                continue
            group_id = status_value.get("group_id")
            status = status_value.get("status")
            if not isinstance(group_id, str) or not isinstance(status, str):
                continue
            group = engagement.groups.setdefault(
                group_id,
                _EngagementGroupProjection(group_id),
            )
            group.ordinal = (
                _optional_integer_value(
                    status_value.get("group_ordinal")
                )
                if group.ordinal is None
                else group.ordinal
            )
            if group.required_atomic is None:
                group.required_atomic = _optional_boolean_value(
                    status_value.get("required_atomic")
                )
            group.validation_status = "valid"
            group.execution_status = status
            group.status = status
            group.failure_reason = _optional_string(
                status_value.get("failure_reason")
            )
            invocation_ids = _optional_string_tuple(
                status_value.get("invocation_ids")
            )
            for invocation_id in invocation_ids or ():
                invocation = group.invocations.setdefault(
                    invocation_id,
                    _EngagementInvocationProjection(invocation_id),
                )
                if invocation.status == "committed":
                    continue
                invocation.status = status

    def _project_engagement_trace(
        self,
        trace: ResearchTrace,
    ) -> None:
        engagement_id = trace.joins.engagement_id
        if engagement_id is None:
            raw_engagement_id = trace.payload.get("engagement_id")
            if isinstance(raw_engagement_id, str):
                engagement_id = EngagementId(raw_engagement_id)
        if engagement_id is None or trace.subject_id is None:
            return
        engagement = self._engagements.get(str(engagement_id))
        if engagement is None:
            engagement = _EngagementProjection(
                engagement_id=str(engagement_id),
                actor_id=trace.subject_id,
                requested_tick=trace.simulation_tick,
                requested_at=trace.simulation_time,
            )
            self._engagements[str(engagement_id)] = engagement
        engagement.compiler_request_id = (
            str(trace.joins.model_request_id)
            if trace.joins.model_request_id is not None
            else engagement.compiler_request_id
        )
        if trace.record_type == "engagement_compilation_result":
            engagement.private_result = dict(trace.payload)
            result = trace.payload.get("result")
            if isinstance(result, dict):
                engagement.private_compiler_summary = _optional_string(
                    result.get("summary")
                )
                engagement.scene_hash = _optional_string(
                    result.get("scene_hash")
                )
                model_turn = result.get("model_turn")
                if isinstance(model_turn, dict):
                    proposal = _engagement_proposal(model_turn)
                    if proposal is not None:
                        engagement.private_proposal = proposal
                        self._apply_engagement_proposal_arguments(
                            engagement,
                            proposal,
                        )
        self._persist_engagement_feature(
            engagement,
            trace.simulation_tick,
            trace.simulation_time,
            None,
            trace.correlation_id,
            include_private=True,
            visibility=RecordVisibility.PRIVATE_RESEARCH,
        )
        if engagement.status in {
            "completed",
            "partial",
            "failed",
            "cancelled",
            "unfinished",
        }:
            self._persist_engagement_feature(
                engagement,
                trace.simulation_tick,
                trace.simulation_time,
                None,
                trace.correlation_id,
                include_private=False,
                visibility=RecordVisibility.OPERATOR,
            )

    @staticmethod
    def _apply_engagement_proposal_arguments(
        engagement: _EngagementProjection,
        proposal: dict[str, JsonValue],
    ) -> None:
        groups = proposal.get("groups")
        if not isinstance(groups, list):
            return
        for group_value in groups:
            if not isinstance(group_value, dict):
                continue
            group_id = group_value.get("group_id")
            if not isinstance(group_id, str):
                continue
            group = engagement.groups.get(group_id)
            if group is None:
                continue
            group.private_proposal = dict(group_value)
            invocations = group_value.get("invocations")
            if not isinstance(invocations, list):
                continue
            for invocation_value in invocations:
                if not isinstance(invocation_value, dict):
                    continue
                invocation_id = invocation_value.get("invocation_id")
                arguments = invocation_value.get("arguments")
                if not isinstance(invocation_id, str) or not isinstance(
                    arguments,
                    dict,
                ):
                    continue
                invocation = group.invocations.get(invocation_id)
                if invocation is not None:
                    invocation.private_proposal_arguments = dict(arguments)

    def _persist_engagement_feature(
        self,
        engagement: _EngagementProjection,
        tick: int,
        simulation_time: float,
        source_event_id: str | None,
        correlation_id: str | None,
        *,
        include_private: bool,
        visibility: RecordVisibility,
    ) -> None:
        engagement_payload: dict[str, JsonValue] = {
            "engagement_id": engagement.engagement_id,
            "actor_id": engagement.actor_id,
            "action_id": engagement.action_id,
            "plan_id": engagement.plan_id,
            "plan_revision": engagement.plan_revision,
            "decision_id": engagement.decision_id,
            "tool_call_id": engagement.tool_call_id,
            "root_correlation_id": engagement.root_correlation_id,
            "status": engagement.status,
            "compiler_status": engagement.compiler_status,
            "requested_tick": engagement.requested_tick,
            "requested_at": engagement.requested_at,
            "started_tick": engagement.started_tick,
            "started_at": engagement.started_at,
            "terminal_tick": engagement.terminal_tick,
            "terminal_at": engagement.terminal_at,
            "terminal_outcome": engagement.terminal_outcome,
        }
        if include_private:
            engagement_payload.update(
                {
                    "compiler_request_id": engagement.compiler_request_id,
                    "referenced_ids": (
                        list(engagement.referenced_ids)
                        if engagement.referenced_ids is not None
                        else None
                    ),
                    "scene_hash": engagement.scene_hash,
                    "scene_version": engagement.scene_version,
                    "catalog_version": engagement.catalog_version,
                    "prompt_version": engagement.prompt_version,
                    "private_intent": engagement.private_intent,
                    "private_controller_reason": (
                        engagement.private_controller_reason
                    ),
                    "private_compiler_summary": (
                        engagement.private_compiler_summary
                    ),
                    "private_proposal": engagement.private_proposal,
                    "private_result": engagement.private_result,
                }
            )
        group_payloads: list[JsonValue] = [
            self._engagement_group_payload(group, include_private)
            for group in sorted(
                engagement.groups.values(),
                key=lambda item: (
                    item.ordinal if item.ordinal is not None else 1_000_000,
                    item.group_id,
                ),
            )
            if include_private or _group_has_public_execution(group)
        ]
        payload: dict[str, JsonValue] = {
            "feature_schema": "stage0.feature.engagement.v1",
            "engagement": engagement_payload,
            "groups": group_payloads,
        }
        record = self._append(
            "engagement_feature",
            tick,
            simulation_time,
            engagement.actor_id,
            payload,
            source_event_id,
            category=RecordCategory.ENGAGEMENT,
            source=RecordSource.DERIVED,
            visibility=visibility,
            joins=RecordJoinIds(
                action_id=(
                    ActionId(engagement.action_id)
                    if engagement.action_id is not None
                    else None
                ),
                plan_id=(
                    PlanId(engagement.plan_id)
                    if engagement.plan_id is not None
                    else None
                ),
                decision_id=(
                    DecisionId(engagement.decision_id)
                    if engagement.decision_id is not None
                    else None
                ),
                tool_call_id=(
                    ToolCallId(engagement.tool_call_id)
                    if engagement.tool_call_id is not None
                    else None
                ),
                model_request_id=(
                    ModelRequestId(engagement.compiler_request_id)
                    if include_private
                    and engagement.compiler_request_id is not None
                    else None
                ),
                engagement_id=EngagementId(engagement.engagement_id),
            ),
            correlation_id=(
                correlation_id
                or engagement.root_correlation_id
                or engagement.engagement_id
            ),
            schema_id="stage0.feature.engagement",
            schema_version="1",
        )
        _persist_engagement_feature_payload(self.store, record, payload)

    @staticmethod
    def _engagement_group_payload(
        group: _EngagementGroupProjection,
        include_private: bool,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "engagement_group_id": group.group_id,
            "ordinal": group.ordinal,
            "required_atomic": group.required_atomic,
            "validation_status": group.validation_status,
            "execution_status": group.execution_status,
            "status": group.status,
            "failure_reason": group.failure_reason,
            "grounded_outcome": group.grounded_outcome,
        }
        if include_private:
            payload.update(
                {
                    "private_rejection_reason": (
                        group.private_rejection_reason
                    ),
                    "private_issues": group.private_issues,
                    "private_proposal": group.private_proposal,
                }
            )
        payload["invocations"] = [
            _engagement_invocation_payload(invocation, include_private)
            for invocation in sorted(
                group.invocations.values(),
                key=lambda item: (
                    item.ordinal if item.ordinal is not None else 1_000_000,
                    item.invocation_id,
                ),
            )
            if include_private or invocation.status == "committed"
        ]
        return payload

    def _close_open_engagements(
        self,
        terminal_tick: int,
        terminal_at: float,
    ) -> None:
        for engagement in sorted(
            self._engagements.values(),
            key=lambda item: item.engagement_id,
        ):
            if engagement.status in {
                "completed",
                "partial",
                "failed",
                "cancelled",
                "unfinished",
            }:
                continue
            engagement.status = "unfinished"
            engagement.terminal_outcome = "unfinished"
            engagement.terminal_tick = terminal_tick
            engagement.terminal_at = terminal_at
            for group in engagement.groups.values():
                if group.validation_status != "valid":
                    continue
                if group.execution_status not in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    group.execution_status = "unfinished"
                    group.status = "unfinished"
                for invocation in group.invocations.values():
                    if invocation.status != "committed":
                        invocation.status = "unfinished"
            self._persist_engagement_feature(
                engagement,
                terminal_tick,
                terminal_at,
                None,
                engagement.root_correlation_id,
                include_private=True,
                visibility=RecordVisibility.PRIVATE_RESEARCH,
            )
            self._persist_engagement_feature(
                engagement,
                terminal_tick,
                terminal_at,
                None,
                engagement.root_correlation_id,
                include_private=False,
                visibility=RecordVisibility.OPERATOR,
            )

    def _collect_interaction_event(self, event: DomainEvent) -> None:
        interaction_ids: list[str] = []
        event_type = event.event_type
        if event_type.startswith("speech."):
            interaction_ids.extend(self._speech_interactions(event))
        if event_type.startswith("transaction."):
            interaction_ids.extend(self._transaction_interactions(event))
        if event_type.startswith("interaction."):
            interaction_ids.extend(self._physical_interactions(event))
        if event_type.startswith("engagement."):
            interaction_ids.extend(self._engagement_interactions(event))
        if (
            event_type == "affordance.failed"
            and event.payload.get("reason") == "station_at_capacity"
        ) or (
            event_type == "transaction.failed"
            and event.payload.get("reason") == "transaction_point_at_capacity"
        ) or event_type == "npc.spawn_blocked":
            interaction_ids.append(self._contention_interaction(event))
        unique_ids = tuple(dict.fromkeys(interaction_ids))
        for interaction_id in unique_ids:
            if (
                interaction_id in self._interaction_episodes
                and not event_type.startswith("engagement.")
            ):
                self._append_interaction_constituent(interaction_id, event)
        self._event_interactions[event.event_id] = unique_ids
        terminal_status = {
            "speech.delivered": "delivered",
            "speech.failed": "failed",
            "transaction.completed": "completed",
            "transaction.failed": "failed",
            "transaction.cancelled": "cancelled",
            "transaction.timed_out": "timed_out",
            "interaction.completed": "completed",
            "interaction.failed": "failed",
            "interaction.cancelled": "cancelled",
            "engagement.completed": "completed",
            "engagement.partial": "partial",
            "engagement.failed": "failed",
            "engagement.cancelled": "cancelled",
        }.get(event_type)
        if terminal_status is not None:
            for interaction_id in unique_ids:
                self._close_interaction(
                    interaction_id,
                    terminal_status,
                    event.simulation_tick,
                    event.simulation_time,
                    {
                        "terminal_event_id": event.event_id,
                        "terminal_event_type": event.event_type,
                        "reason": event.payload.get("reason"),
                        "payload": (
                            _public_engagement_event_payload(event)
                            if event.event_type.startswith("engagement.")
                            else dict(event.payload)
                        ),
                    },
                )

    def _engagement_interactions(
        self,
        event: DomainEvent,
    ) -> tuple[str, ...]:
        engagement_id = event.payload.get("engagement_id")
        if not isinstance(engagement_id, str):
            return ()
        interaction_id = f"interaction:engagement:{engagement_id}"
        key = ("engagement", engagement_id)
        if event.event_type == "engagement.requested":
            self._start_interaction(
                interaction_id,
                "engagement",
                key,
                (self._participant(event.agent_id, "actor"),),
                "OPERATOR",
                event=event,
                context={
                    "interaction_type": "engagement",
                    "engagement_id": engagement_id,
                    "actor_id": event.agent_id,
                    "reference_count": _string_array_length(
                        event.payload.get("reference_ids")
                    ),
                },
                initial_status="requested",
                actor_id=event.agent_id,
                record_visibility=RecordVisibility.OPERATOR,
                include_event_payload=False,
            )
        existing = self._interaction_keys.get(key)
        if existing is None:
            return ()
        episode = self._interaction_episodes.get(existing)
        if episode is None:
            return ()
        status = {
            "engagement.started": "active",
            "engagement.completed": "completed",
            "engagement.partial": "partial",
            "engagement.failed": "failed",
            "engagement.cancelled": "cancelled",
        }.get(event.event_type)
        if status is not None:
            episode.status = status
        if (
            not event_payload_is_private(event.payload)
            or event.event_type == "engagement.requested"
            or event.event_type == "engagement.capability_committed"
        ):
            self._append_interaction_constituent(
                existing,
                event,
                payload_override=_public_engagement_event_payload(event),
                visibility=RecordVisibility.OPERATOR,
            )
        self.store.append_interaction(
            run_id=self.run_id,
            interaction_id=existing,
            record_id=episode.record_id,
            interaction_type=episode.interaction_type,
            start_tick=episode.start_tick,
            end_tick=None,
            status=episode.status,
            context=episode.context,
            actor_id=episode.actor_id,
            goal_id=episode.initiating_goal_id,
            action_id=episode.initiating_action_id,
            decision_id=episode.initiating_decision_id,
            tool_call_id=episode.initiating_tool_call_id,
            engagement_id=episode.initiating_engagement_id,
            correlation_id=episode.correlation_id,
        )
        return (existing,)

    def _speech_interactions(self, event: DomainEvent) -> tuple[str, ...]:
        if event.event_type == "speech.delivered" and event.causation_id:
            existing = self._event_interactions.get(event.causation_id, ())
            if existing:
                interaction_id = existing[0]
                recipients = event.payload.get("recipient_ids")
                if isinstance(recipients, list):
                    for recipient_id in recipients:
                        if isinstance(recipient_id, str):
                            self._add_participant(
                                interaction_id,
                                self._participant(recipient_id, "recipient"),
                            )
                return (interaction_id,)
        if event.event_type not in {"speech.started", "speech.failed"}:
            return ()
        interaction_id = f"interaction:speech:{event.event_id}"
        target_id = event.payload.get("target_id")
        participants = [self._participant(event.agent_id, "speaker")]
        if isinstance(target_id, str):
            participants.append(self._participant(target_id, "addressee"))
        self._start_interaction(
            interaction_id,
            "direct_speech",
            ("speech", event.event_id),
            tuple(participants),
            (
                "DIRECT_PARTICIPANTS"
                if event.payload.get("channel") == "whisper"
                else "LOCAL_AUDITORY"
            ),
            event=event,
        )
        return (interaction_id,)

    def _transaction_interactions(
        self,
        event: DomainEvent,
    ) -> tuple[str, ...]:
        request_id = event.payload.get("request_id")
        action_id = event.payload.get("action_id")
        fallback = (
            f"{event.agent_id}:{event.payload.get('point_id')}:"
            f"{event.payload.get('offer_id')}"
        )
        bases = tuple(
            dict.fromkeys(
                value
                for value in (
                    request_id if isinstance(request_id, str) else None,
                    action_id if isinstance(action_id, str) else None,
                    event.correlation_id,
                    fallback,
                )
                if value is not None
            )
        )
        keys = tuple(("transaction", str(value)) for value in bases)
        interaction_id = next(
            (
                self._interaction_keys[key]
                for key in keys
                if key in self._interaction_keys
            ),
            None,
        )
        if interaction_id is None:
            basis = bases[0]
            interaction_id = f"interaction:transaction:{basis}"
            participants = [self._participant(event.agent_id, "customer")]
            operator_id = event.payload.get("operator_id")
            if isinstance(operator_id, str):
                participants.append(
                    self._participant(operator_id, "operator")
                )
            self._start_interaction(
                interaction_id,
                "transaction",
                keys[0],
                tuple(participants),
                "DIRECT_PARTICIPANTS",
                event=event,
            )
        for key in keys:
            self._interaction_keys[key] = interaction_id
        operator_id = event.payload.get("operator_id")
        if isinstance(operator_id, str):
            self._add_participant(
                interaction_id,
                self._participant(operator_id, "operator"),
            )
        result = [interaction_id]
        service_key = ("staffed_service", interaction_id)
        existing_service_id = self._interaction_keys.get(service_key)
        if existing_service_id is not None:
            result.append(existing_service_id)
        elif event.event_type in {
            "transaction.awaiting_staff",
            "transaction.staff_assigned",
            "transaction.authorized",
            "transaction.started",
            "transaction.progressed",
            "transaction.completed",
            "transaction.failed",
            "transaction.cancelled",
            "transaction.timed_out",
        } and (
            event.event_type
            in {
                "transaction.awaiting_staff",
                "transaction.staff_assigned",
                "transaction.authorized",
            }
            or isinstance(operator_id, str)
        ):
            service_id = (
                "interaction:staffed_service:"
                f"{interaction_id.removeprefix('interaction:transaction:')}"
            )
            participants = [self._participant(event.agent_id, "customer")]
            if isinstance(operator_id, str):
                participants.append(
                    self._participant(operator_id, "service_provider")
                )
            self._start_interaction(
                service_id,
                "staffed_service",
                service_key,
                tuple(participants),
                "DIRECT_PARTICIPANTS",
                event=event,
            )
            result.append(service_id)
        if isinstance(operator_id, str):
            for service_id in result[1:]:
                self._add_participant(
                    service_id,
                    self._participant(operator_id, "service_provider"),
                )
        return tuple(result)

    def _physical_interactions(
        self,
        event: DomainEvent,
    ) -> tuple[str, ...]:
        if event.event_type not in {
            "interaction.requested",
            "interaction.started",
            "interaction.completed",
            "interaction.failed",
            "interaction.cancelled",
        }:
            return ()
        verb = event.payload.get("verb")
        target_id = event.payload.get("target_id")
        if not isinstance(verb, str) or not isinstance(target_id, str):
            return ()
        actor_id = event.agent_id
        destination_id = event.payload.get("destination_id")
        slot_id = event.payload.get("slot_id")
        action_id = event.payload.get("action_id")
        keys: list[tuple[str, ...]] = []
        if isinstance(action_id, str):
            keys.append(("physical_action", action_id))
        if event.correlation_id is not None:
            keys.append(("physical_correlation", event.correlation_id))
        if event.causation_id is not None:
            for existing_id in self._event_interactions.get(
                event.causation_id, ()
            ):
                episode = self._interaction_episodes.get(existing_id)
                if (
                    episode is not None
                    and episode.interaction_type == "physical_object"
                ):
                    keys.append(("physical_interaction", existing_id))
                    self._interaction_keys[keys[-1]] = existing_id
        fallback_key = (
            "physical_subject",
            actor_id or "",
            verb,
            target_id,
            destination_id if isinstance(destination_id, str) else "",
            slot_id if isinstance(slot_id, str) else "",
        )
        keys.append(fallback_key)
        interaction_id = next(
            (
                self._interaction_keys[key]
                for key in keys
                if key in self._interaction_keys
            ),
            None,
        )
        if interaction_id is None:
            basis = (
                action_id
                if isinstance(action_id, str)
                else event.correlation_id or event.event_id
            )
            interaction_id = f"interaction:physical:{basis}"
            participants = [self._participant(actor_id, "actor")]
            participants.append(self._participant(target_id, "target"))
            if (
                isinstance(destination_id, str)
                and destination_id != target_id
            ):
                participants.append(
                    self._participant(destination_id, "destination")
                )
            self._start_interaction(
                interaction_id,
                "physical_object",
                keys[0],
                tuple(participants),
                "PRIVATE_RESEARCH",
                event=event,
                context={
                    "interaction_type": "physical_object",
                    "verb": verb,
                    "actor_id": actor_id,
                    "target_id": target_id,
                    "destination_id": (
                        destination_id
                        if isinstance(destination_id, str)
                        else None
                    ),
                    "slot_id": slot_id if isinstance(slot_id, str) else None,
                    "source": event.payload.get("source"),
                },
                initial_status=(
                    "requested"
                    if event.event_type == "interaction.requested"
                    else "active"
                ),
                interaction_verb=verb,
                actor_id=actor_id,
                target_id=target_id,
                destination_id=(
                    destination_id
                    if isinstance(destination_id, str)
                    else None
                ),
                slot_id=slot_id if isinstance(slot_id, str) else None,
            )
        for key in keys:
            self._interaction_keys[key] = interaction_id
        self._interaction_keys[
            ("physical_interaction", interaction_id)
        ] = interaction_id
        episode = self._interaction_episodes.get(interaction_id)
        if episode is None:
            return ()
        if event.event_type == "interaction.started":
            episode.status = "active"
        elif event.event_type == "interaction.requested":
            episode.status = "requested"
        elif event.event_type.endswith(
            (".completed", ".failed", ".cancelled")
        ):
            episode.status = event.event_type.removeprefix("interaction.")
        self.store.append_interaction(
            run_id=self.run_id,
            interaction_id=interaction_id,
            record_id=episode.record_id,
            interaction_type=episode.interaction_type,
            start_tick=episode.start_tick,
            end_tick=None,
            status=episode.status,
            context=episode.context,
            interaction_verb=episode.interaction_verb,
            actor_id=episode.actor_id,
            target_id=episode.target_id,
            destination_id=episode.destination_id,
            slot_id=episode.slot_id,
            goal_id=episode.initiating_goal_id,
            action_id=episode.initiating_action_id,
            decision_id=episode.initiating_decision_id,
            tool_call_id=episode.initiating_tool_call_id,
            correlation_id=episode.correlation_id,
        )
        return (interaction_id,)

    def _contention_interaction(self, event: DomainEvent) -> str:
        interaction_id = f"interaction:contention:{event.event_id}"
        participants = [self._participant(event.agent_id, "contender")]
        blocked_by = event.payload.get("blocked_by")
        if isinstance(blocked_by, str):
            participants.append(self._participant(blocked_by, "holder"))
        resource_id = event.payload.get("station_id")
        if not isinstance(resource_id, str):
            resource_id = event.payload.get("point_id")
        if isinstance(resource_id, str):
            for holder_id in self._resource_holders(resource_id):
                participants.append(self._participant(holder_id, "holder"))
        self._start_interaction(
            interaction_id,
            "shared_resource_contention",
            ("contention", event.event_id),
            tuple(participants),
            "OPERATOR",
            event=event,
        )
        self._close_interaction(
            interaction_id,
            "failed",
            event.simulation_tick,
            event.simulation_time,
            {
                "terminal_event_id": event.event_id,
                "terminal_event_type": event.event_type,
                "reason": event.payload.get("reason") or "resource_blocked",
                "resource_id": resource_id,
            },
        )
        return interaction_id

    def _start_interaction(
        self,
        interaction_id: str,
        interaction_type: str,
        key: tuple[str, ...],
        participants: tuple[dict[str, JsonValue], ...],
        content_visibility: str,
        *,
        event: DomainEvent | None = None,
        context: dict[str, JsonValue] | None = None,
        initial_status: str = "active",
        interaction_verb: str | None = None,
        actor_id: str | None = None,
        target_id: str | None = None,
        destination_id: str | None = None,
        slot_id: str | None = None,
        record_visibility: RecordVisibility = (
            RecordVisibility.PRIVATE_RESEARCH
        ),
        include_event_payload: bool = True,
    ) -> None:
        if interaction_id in self._interaction_episodes:
            return
        joins = (
            self._resolved_event_joins(event)
            if event is not None
            else RecordJoinIds()
        )
        ordered_participants: list[dict[str, JsonValue]] = []
        for participant in participants:
            if participant["participant_id"] is None:
                continue
            if participant not in ordered_participants:
                ordered_participants.append(participant)
        payload_context: dict[str, JsonValue] = {
            "location_context": self._participant_locations(
                tuple(ordered_participants)
            ),
            "environment_context": self._environment_state(
                self.runner.clock.simulation_time
            ),
        }
        if event is not None and include_event_payload:
            payload_context["initiating_event_type"] = event.event_type
            payload_context["initiating_event_id"] = event.event_id
            payload_context["event_payload"] = dict(event.payload)
        elif event is not None:
            payload_context["initiating_event_type"] = event.event_type
            payload_context["initiating_event_id"] = event.event_id
        if context is not None:
            payload_context.update(context)
        episode = _InteractionEpisode(
            interaction_id=interaction_id,
            interaction_type=interaction_type,
            start_tick=(
                event.simulation_tick
                if event is not None
                else self.runner.clock.tick
            ),
            started_at=(
                event.simulation_time
                if event is not None
                else self.runner.clock.simulation_time
            ),
            status=initial_status,
            context=payload_context,
            participants=ordered_participants,
            constituent_events=[],
            content_visibility=content_visibility,
            interaction_verb=interaction_verb,
            actor_id=actor_id,
            target_id=target_id,
            destination_id=destination_id,
            slot_id=slot_id,
            correlation_id=(
                event.correlation_id
                if event is not None and event.correlation_id is not None
                else interaction_id
            ),
            initiating_goal_id=(
                str(joins.goal_id) if joins.goal_id is not None else None
            ),
            initiating_decision_id=(
                str(joins.decision_id)
                if joins.decision_id is not None
                else None
            ),
            initiating_action_id=(
                str(joins.action_id) if joins.action_id is not None else None
            ),
            initiating_tool_call_id=(
                str(joins.tool_call_id)
                if joins.tool_call_id is not None
                else None
            ),
            initiating_engagement_id=(
                str(joins.engagement_id)
                if joins.engagement_id is not None
                else None
            ),
            record_visibility=record_visibility,
        )
        self._interaction_episodes[interaction_id] = episode
        self._interaction_keys[key] = interaction_id
        participant_payload: list[JsonValue] = list(ordered_participants)
        record = self._append(
            "interaction_started",
            episode.start_tick,
            episode.started_at,
            (
                str(ordered_participants[0]["participant_id"])
                if ordered_participants
                else None
            ),
            {
                "interaction_id": interaction_id,
                "interaction_type": interaction_type,
                "interaction_verb": interaction_verb,
                "actor_id": actor_id,
                "target_id": target_id,
                "destination_id": destination_id,
                "slot_id": slot_id,
                "correlation_id": episode.correlation_id,
                "status": initial_status,
                "participants": participant_payload,
                "content_visibility": content_visibility,
                "context": payload_context,
            },
            event.event_id if event is not None else None,
            category=RecordCategory.INTERACTION,
            source=RecordSource.DERIVED,
            visibility=record_visibility,
            joins=RecordJoinIds(
                goal_id=joins.goal_id,
                action_id=joins.action_id,
                decision_id=joins.decision_id,
                tool_call_id=joins.tool_call_id,
                interaction_id=InteractionId(interaction_id),
                engagement_id=joins.engagement_id,
                engagement_group_id=joins.engagement_group_id,
                engagement_invocation_id=joins.engagement_invocation_id,
                transaction_request_id=joins.transaction_request_id,
            ),
            correlation_id=episode.correlation_id,
            related_entity_ids=tuple(
                str(item["participant_id"])
                for item in ordered_participants[1:]
            ),
            schema_id="stage0.interaction.started",
            schema_version="1",
        )
        episode.record_id = record.record_id
        self.store.append_interaction(
            run_id=self.run_id,
            interaction_id=interaction_id,
            record_id=record.record_id,
            interaction_type=interaction_type,
            start_tick=episode.start_tick,
            end_tick=None,
            status=initial_status,
            context=payload_context,
            interaction_verb=interaction_verb,
            actor_id=actor_id,
            target_id=target_id,
            destination_id=destination_id,
            slot_id=slot_id,
            goal_id=(
                str(joins.goal_id) if joins.goal_id is not None else None
            ),
            action_id=(
                str(joins.action_id) if joins.action_id is not None else None
            ),
            decision_id=(
                str(joins.decision_id)
                if joins.decision_id is not None
                else None
            ),
            tool_call_id=(
                str(joins.tool_call_id)
                if joins.tool_call_id is not None
                else None
            ),
            engagement_id=(
                str(joins.engagement_id)
                if joins.engagement_id is not None
                else None
            ),
            correlation_id=episode.correlation_id,
        )
        for participant in ordered_participants:
            self.store.append_interaction_participant(
                run_id=self.run_id,
                interaction_id=interaction_id,
                participant_id=str(participant["participant_id"]),
                role=str(participant["role"]),
                participant=participant,
            )

    def _append_interaction_constituent(
        self,
        interaction_id: str,
        event: DomainEvent,
        *,
        payload_override: dict[str, JsonValue] | None = None,
        visibility: RecordVisibility = RecordVisibility.PRIVATE_RESEARCH,
    ) -> None:
        episode = self._interaction_episodes[interaction_id]
        if any(
            item.get("event_id") == event.event_id
            for item in episode.constituent_events
        ):
            return
        item: dict[str, JsonValue] = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "simulation_tick": event.simulation_tick,
            "simulation_time": event.simulation_time,
            "agent_id": event.agent_id,
            "payload": (
                payload_override
                if payload_override is not None
                else dict(event.payload)
            ),
        }
        episode.constituent_events.append(item)
        joins = self._resolved_event_joins(event)
        record = self._append(
            "interaction_event",
            event.simulation_tick,
            event.simulation_time,
            event.agent_id,
            {
                "interaction_id": interaction_id,
                "event_index": len(episode.constituent_events) - 1,
                "event": item,
            },
            event.event_id,
            category=RecordCategory.INTERACTION,
            source=RecordSource.DERIVED,
            visibility=visibility,
            joins=RecordJoinIds(
                interaction_id=InteractionId(interaction_id),
                goal_id=joins.goal_id,
                action_id=joins.action_id,
                decision_id=joins.decision_id,
                tool_call_id=joins.tool_call_id,
                engagement_id=joins.engagement_id,
                engagement_group_id=joins.engagement_group_id,
                engagement_invocation_id=joins.engagement_invocation_id,
                transaction_request_id=joins.transaction_request_id,
            ),
            causation_id=event.event_id,
            correlation_id=event.correlation_id or interaction_id,
            schema_id="stage0.interaction.event",
            schema_version="1",
        )
        self.store.append_interaction_event(
            run_id=self.run_id,
            interaction_id=interaction_id,
            event_id=event.event_id,
            record_id=record.record_id,
            event_index=len(episode.constituent_events) - 1,
            event_type=event.event_type,
            simulation_tick=event.simulation_tick,
            event=item,
        )

    def _add_participant(
        self,
        interaction_id: str,
        participant: dict[str, JsonValue],
    ) -> None:
        episode = self._interaction_episodes.get(interaction_id)
        if episode is None or participant["participant_id"] is None:
            return
        if participant in episode.participants:
            return
        episode.participants.append(participant)
        self.store.append_interaction_participant(
            run_id=self.run_id,
            interaction_id=interaction_id,
            participant_id=str(participant["participant_id"]),
            role=str(participant["role"]),
            participant=participant,
        )

    def _close_interaction(
        self,
        interaction_id: str,
        terminal_status: str,
        terminal_tick: int,
        terminal_at: float,
        outcome: dict[str, JsonValue],
    ) -> None:
        active_episode = self._interaction_episodes.get(interaction_id)
        if (
            active_episode is not None
            and active_episode.interaction_type
            in {"co_presence", "visibility"}
        ):
            self._append_interaction_boundary(
                interaction_id,
                "interval.ended",
                terminal_tick,
                terminal_at,
            )
        episode = self._interaction_episodes.pop(interaction_id, None)
        if episode is None:
            return
        keys = [
            key
            for key, value in self._interaction_keys.items()
            if value == interaction_id
        ]
        for key in keys:
            del self._interaction_keys[key]
        duration = round(max(0.0, terminal_at - episode.started_at), 12)
        participants_payload: list[JsonValue] = list(episode.participants)
        events_payload: list[JsonValue] = list(episode.constituent_events)
        payload: dict[str, JsonValue] = {
            "feature_schema": "stage0.feature.interaction_episode.v2",
            "interaction_id": interaction_id,
            "interaction_type": episode.interaction_type,
            "interaction_verb": episode.interaction_verb,
            "actor_id": episode.actor_id,
            "target_id": episode.target_id,
            "destination_id": episode.destination_id,
            "slot_id": episode.slot_id,
            "correlation_id": episode.correlation_id,
            "terminal_status": terminal_status,
            "start_tick": episode.start_tick,
            "terminal_tick": terminal_tick,
            "started_at": episode.started_at,
            "terminal_at": terminal_at,
            "duration": duration,
            "initiating_goal_id": episode.initiating_goal_id,
            "initiating_decision_id": episode.initiating_decision_id,
            "initiating_action_id": episode.initiating_action_id,
            "initiating_tool_call_id": episode.initiating_tool_call_id,
            "initiating_engagement_id": episode.initiating_engagement_id,
            "content_visibility": episode.content_visibility,
            "participants": participants_payload,
            "constituent_events": events_payload,
            "context": episode.context,
            "outcome": outcome,
        }
        subject_id = (
            str(episode.participants[0]["participant_id"])
            if episode.participants
            else None
        )
        record = self._append(
            "interaction_episode",
            terminal_tick,
            terminal_at,
            subject_id,
            payload,
            (
                str(outcome["terminal_event_id"])
                if isinstance(outcome.get("terminal_event_id"), str)
                else None
            ),
            category=RecordCategory.INTERACTION,
            source=RecordSource.DERIVED,
            visibility=episode.record_visibility,
            joins=RecordJoinIds(
                goal_id=(
                    GoalId(episode.initiating_goal_id)
                    if episode.initiating_goal_id is not None
                    else None
                ),
                action_id=(
                    ActionId(episode.initiating_action_id)
                    if episode.initiating_action_id is not None
                    else None
                ),
                decision_id=(
                    DecisionId(episode.initiating_decision_id)
                    if episode.initiating_decision_id is not None
                    else None
                ),
                tool_call_id=(
                    ToolCallId(episode.initiating_tool_call_id)
                    if episode.initiating_tool_call_id is not None
                    else None
                ),
                interaction_id=InteractionId(interaction_id),
                engagement_id=(
                    EngagementId(episode.initiating_engagement_id)
                    if episode.initiating_engagement_id is not None
                    else None
                ),
            ),
            correlation_id=episode.correlation_id,
            related_entity_ids=tuple(
                str(item["participant_id"])
                for item in episode.participants[1:]
            ),
            schema_id="stage0.feature.interaction_episode",
            schema_version="2",
        )
        self.store.append_interaction(
            run_id=self.run_id,
            interaction_id=interaction_id,
            record_id=record.record_id,
            interaction_type=episode.interaction_type,
            start_tick=episode.start_tick,
            end_tick=terminal_tick,
            status=terminal_status,
            context=episode.context,
            outcome=outcome,
            interaction_verb=episode.interaction_verb,
            actor_id=episode.actor_id,
            target_id=episode.target_id,
            destination_id=episode.destination_id,
            slot_id=episode.slot_id,
            goal_id=episode.initiating_goal_id,
            action_id=episode.initiating_action_id,
            decision_id=episode.initiating_decision_id,
            tool_call_id=episode.initiating_tool_call_id,
            engagement_id=episode.initiating_engagement_id,
            correlation_id=episode.correlation_id,
        )
        self.store.append_interaction_episode(
            run_id=self.run_id,
            interaction_id=interaction_id,
            record_id=record.record_id,
            interaction_type=episode.interaction_type,
            status=terminal_status,
            start_tick=episode.start_tick,
            terminal_tick=terminal_tick,
            started_at=episode.started_at,
            terminal_at=terminal_at,
            duration=duration,
            initiating_goal_id=episode.initiating_goal_id,
            initiating_decision_id=episode.initiating_decision_id,
            initiating_action_id=episode.initiating_action_id,
            initiating_tool_call_id=episode.initiating_tool_call_id,
            initiating_engagement_id=episode.initiating_engagement_id,
            content_visibility=episode.content_visibility,
            episode=payload,
            interaction_verb=episode.interaction_verb,
            actor_id=episode.actor_id,
            target_id=episode.target_id,
            destination_id=episode.destination_id,
            slot_id=episode.slot_id,
            correlation_id=episode.correlation_id,
        )

    def _append_interaction_boundary(
        self,
        interaction_id: str,
        event_type: str,
        simulation_tick: int,
        simulation_time: float,
    ) -> None:
        episode = self._interaction_episodes.get(interaction_id)
        if episode is None:
            return
        event_id = f"{interaction_id}:{event_type}:{simulation_tick}"
        item: dict[str, JsonValue] = {
            "event_id": event_id,
            "event_type": event_type,
            "simulation_tick": simulation_tick,
            "simulation_time": simulation_time,
            "agent_id": None,
            "payload": {},
        }
        episode.constituent_events.append(item)
        record = self._append(
            "interaction_event",
            simulation_tick,
            simulation_time,
            None,
            {
                "interaction_id": interaction_id,
                "event_index": len(episode.constituent_events) - 1,
                "event": item,
            },
            None,
            category=RecordCategory.INTERACTION,
            source=RecordSource.DERIVED,
            visibility=RecordVisibility.PRIVATE_RESEARCH,
            joins=RecordJoinIds(
                interaction_id=InteractionId(interaction_id)
            ),
            correlation_id=interaction_id,
            schema_id="stage0.interaction.event",
            schema_version="1",
        )
        self.store.append_interaction_event(
            run_id=self.run_id,
            interaction_id=interaction_id,
            event_id=event_id,
            record_id=record.record_id,
            event_index=len(episode.constituent_events) - 1,
            event_type=event_type,
            simulation_tick=simulation_tick,
            event=item,
        )

    def _close_open_interactions(
        self,
        terminal_tick: int,
        terminal_at: float,
    ) -> None:
        for interaction_id in sorted(tuple(self._interaction_episodes)):
            self._close_interaction(
                interaction_id,
                "run_ended",
                terminal_tick,
                terminal_at,
                {"reason": "run_final"},
            )

    def _participant(
        self,
        participant_id: str | None,
        role: str,
    ) -> dict[str, JsonValue]:
        return {
            "participant_id": participant_id,
            "role": role,
            "actor_kind": (
                _actor_kind(self.runner.registry, participant_id)
                if participant_id is not None
                else None
            ),
        }

    def _participant_locations(
        self,
        participants: tuple[dict[str, JsonValue], ...],
    ) -> dict[str, JsonValue]:
        locations: dict[str, JsonValue] = {}
        for participant in participants:
            participant_id = participant.get("participant_id")
            if not isinstance(participant_id, str):
                continue
            locations[participant_id] = _entity_location(
                self.runner.registry, participant_id
            )
        return locations

    def _resource_holders(self, resource_id: str) -> tuple[str, ...]:
        holders: set[str] = set()
        for entity_id, affordance_execution in self.runner.registry.query(
            AffordanceExecutionComponent
        ):
            if affordance_execution.station_id == resource_id:
                holders.add(entity_id)
        for entity_id, transaction_execution in self.runner.registry.query(
            TransactionExecutionComponent
        ):
            if transaction_execution.point_id == resource_id:
                holders.add(entity_id)
        return tuple(sorted(holders))

    def _persist_state_sample(
        self,
        phase: RunnerPhase,
        subject_id: str | None,
        state: dict[str, JsonValue],
    ) -> None:
        sample_key = subject_id or "__global__"
        record = self._append(
            "state_sample",
            self.runner.clock.tick,
            self.runner.clock.simulation_time,
            subject_id,
            state,
            None,
            category=RecordCategory.STATE,
            source=RecordSource.DATASET_COLLECTOR,
            phase=phase,
            visibility=RecordVisibility.PRIVATE_RESEARCH,
        )
        sample_id = f"{record.record_id}:state"
        self.store.append_state_sample(
            run_id=self.run_id,
            state_sample_id=sample_id,
            record_id=record.record_id,
            subject_id=subject_id,
            phase=phase,
            simulation_tick=self.runner.clock.tick,
            simulation_time=self.runner.clock.simulation_time,
            state=state,
        )
        previous = self._previous_state_samples.get(sample_key)
        if previous is not None:
            (
                previous_sample_id,
                previous_state,
                previous_tick,
                previous_time,
            ) = previous
            delta = state_delta(previous_state, state)
            if delta["change_count"] != 0:
                delta_record = self._append(
                    "state_delta",
                    self.runner.clock.tick,
                    self.runner.clock.simulation_time,
                    subject_id,
                    {
                        "from_sample_id": previous_sample_id,
                        "to_sample_id": sample_id,
                        **delta,
                    },
                    None,
                    category=RecordCategory.TRANSITION,
                    source=RecordSource.DERIVED,
                    phase=phase,
                    visibility=RecordVisibility.PRIVATE_RESEARCH,
                )
                self.store.append_state_delta(
                    run_id=self.run_id,
                    state_delta_id=f"{delta_record.record_id}:delta",
                    record_id=delta_record.record_id,
                    subject_id=subject_id,
                    from_sample_id=previous_sample_id,
                    to_sample_id=sample_id,
                    simulation_tick=self.runner.clock.tick,
                    delta=delta,
                )
            action_context = _action_context(state)
            terminal_outcomes = (
                self._pending_action_outcomes.pop(subject_id, [])
                if subject_id is not None
                and phase is RunnerPhase.TICK_POST_SYSTEMS
                else []
            )
            if terminal_outcomes:
                terminal_outcome_payload: list[JsonValue] = list(
                    terminal_outcomes
                )
                action_context["terminal_outcomes"] = terminal_outcome_payload
            action_id = (
                str(terminal_outcomes[-1]["action_id"])
                if terminal_outcomes
                else _find_text(action_context, "action_id")
            )
            outcome_label = (
                str(terminal_outcomes[-1]["terminal_status"])
                if terminal_outcomes
                else "state_changed"
                if delta["change_count"] != 0
                else "no_state_change"
            )
            transition_payload: dict[str, JsonValue] = {
                "feature_schema": "stage0.feature.transition_sample.v1",
                "from_sample_id": previous_sample_id,
                "to_sample_id": sample_id,
                "start_tick": previous_tick,
                "end_tick": self.runner.clock.tick,
                "dt": round(
                    max(0.0, self.runner.clock.simulation_time - previous_time),
                    12,
                ),
                "action_id": action_id,
                "state_before": previous_state,
                "action_context": action_context,
                "exogenous_context": self._environment_state(
                    self.runner.clock.simulation_time
                ),
                "state_after": state,
                "outcome": outcome_label,
                "delta": delta,
            }
            transition_record = self._append(
                "transition_sample",
                self.runner.clock.tick,
                self.runner.clock.simulation_time,
                subject_id,
                transition_payload,
                None,
                category=RecordCategory.TRANSITION,
                source=RecordSource.DERIVED,
                phase=phase,
                visibility=RecordVisibility.PRIVATE_RESEARCH,
                joins=RecordJoinIds(
                    action_id=ActionId(action_id)
                    if action_id is not None
                    else None
                ),
                schema_id="stage0.feature.transition_sample",
                schema_version="1",
            )
            self.store.append_transition_sample(
                run_id=self.run_id,
                transition_sample_id=(
                    f"{transition_record.record_id}:transition"
                ),
                record_id=transition_record.record_id,
                subject_id=subject_id,
                action_id=action_id,
                start_tick=previous_tick,
                end_tick=self.runner.clock.tick,
                elapsed_simulation_time=round(
                    max(
                        0.0,
                        self.runner.clock.simulation_time - previous_time,
                    ),
                    12,
                ),
                outcome=str(transition_payload["outcome"]),
                state_before=previous_state,
                action=action_context,
                exogenous_context=self._environment_state(
                    self.runner.clock.simulation_time
                ),
                state_after=state,
            )
        self._previous_state_samples[sample_key] = (
            sample_id,
            state,
            self.runner.clock.tick,
            self.runner.clock.simulation_time,
        )

    def _research_available(self, _trace: ResearchTrace) -> None:
        self._research_pending = True

    def _drain_research(self) -> None:
        if self._finalized:
            return
        traces = self.runner.research.drain()
        self._research_pending = False
        for trace in traces:
            try:
                self._collect_research_trace(trace)
            except Exception as error:
                self._capture_failed = True
                note_failure = getattr(
                    self.runner.research_recorder,
                    "note_failure",
                    None,
                )
                if callable(note_failure):
                    note_failure(
                        f"research trace persistence failed for "
                        f"{trace.record_type}: {error}"
                    )
                self.store.complete_run(
                    self.run_id,
                    status="capture_failed",
                    final_tick=self.runner.clock.tick,
                    final_simulation_time=(
                        self.runner.clock.simulation_time
                    ),
                )
                raise

    def _collect_research_trace(self, trace: ResearchTrace) -> None:
        record = self._append(
            trace.record_type,
            trace.simulation_tick,
            trace.simulation_time,
            trace.subject_id,
            trace.payload,
            None,
            category=trace.category,
            source=trace.source,
            phase=trace.phase,
            visibility=trace.visibility,
            joins=trace.joins,
            causation_id=trace.causation_id,
            correlation_id=trace.correlation_id,
        )
        if trace.record_type == "decision_request":
            self._project_decision_request(record, trace)
        elif trace.record_type in {
            "decision_result",
            "cognition_evaluation",
        }:
            self._project_decision_trace(record, trace)
        elif trace.record_type in {"model_request", "model_turn", "model_error"}:
            self._project_model_trace(record, trace)
        elif trace.record_type.startswith("engagement_compilation_"):
            self._project_engagement_trace(trace)
        elif (
            trace.record_type.startswith("memory_")
            or trace.record_type.startswith("embedding_")
            and trace.category is RecordCategory.MEMORY
        ):
            self._project_memory_trace(record, trace)
        elif (
            trace.record_type.startswith("information_retrieval_")
            or trace.record_type.startswith("embedding_")
            and trace.category is RecordCategory.INFORMATION
        ):
            self._project_information_trace(record, trace)

    def _project_decision_request(
        self,
        record: DatasetRecord,
        trace: ResearchTrace,
    ) -> None:
        request = trace.payload.get("request")
        if not isinstance(request, dict):
            return
        decision_id = request.get("decision_id")
        if not isinstance(decision_id, str):
            return
        episode = self._decision_episodes.get(decision_id)
        if episode is None:
            requested_tick = request.get("requested_tick")
            episode = _DecisionEpisode(
                decision_id=decision_id,
                agent_id=trace.subject_id,
                requested_tick=(
                    requested_tick
                    if isinstance(requested_tick, int)
                    and not isinstance(requested_tick, bool)
                    else trace.simulation_tick
                ),
                requested_at=trace.simulation_time,
            )
            self._decision_episodes[decision_id] = episode
        episode.context = request
        self.store.append_decision(
            run_id=self.run_id,
            decision_id=decision_id,
            record_id=record.record_id,
            subject_id=trace.subject_id,
            simulation_tick=episode.requested_tick,
            status=episode.status,
            selected_option_id=episode.selected_option_id,
            context=request,
        )
        for index, option in enumerate(_decision_options(request)):
            option_id = option["option_id"]
            option_type = option["option_type"]
            if not isinstance(option_id, str) or not isinstance(
                option_type, str
            ):
                continue
            self.store.append_decision_option(
                run_id=self.run_id,
                decision_id=decision_id,
                option_id=option_id,
                record_id=record.record_id,
                option_index=index,
                option_type=option_type,
                selected=option_id == episode.selected_option_id,
                option=option,
            )
        for memory_id in _memory_ids(request.get("retrieved_memories")):
            self._append_memory_relation(
                memory_id=memory_id,
                subject_id=trace.subject_id,
                relation_type="used_by_decision",
                source_type="decision",
                source_id=decision_id,
                tick=trace.simulation_tick,
                simulation_time=trace.simulation_time,
            )

    def _project_decision_trace(
        self,
        record: DatasetRecord,
        trace: ResearchTrace,
    ) -> None:
        decision_id = trace.joins.decision_id
        if decision_id is None:
            return
        status = trace.payload.get("status")
        if not isinstance(status, str):
            return
        episode = self._decision_episodes.get(str(decision_id))
        effective_status = (
            episode.status
            if episode is not None
            and episode.status
            in {
                "completed",
                "failed",
                "cancelled",
                "interrupted",
                "rejected",
                "skipped",
            }
            else status
        )
        self.store.append_decision(
            run_id=self.run_id,
            decision_id=str(decision_id),
            record_id=record.record_id,
            subject_id=trace.subject_id,
            simulation_tick=(
                episode.requested_tick
                if episode is not None
                else trace.simulation_tick
            ),
            status=effective_status,
            selected_option_id=(
                episode.selected_option_id if episode is not None else None
            ),
            context=(episode.context if episode is not None else {}),
            outcome=trace.payload,
        )

    def _project_model_trace(
        self,
        record: DatasetRecord,
        trace: ResearchTrace,
    ) -> None:
        model_request_id = trace.joins.model_request_id
        if model_request_id is None:
            raw = trace.payload.get("model_request_id")
            if isinstance(raw, str):
                model_request_id = ModelRequestId(raw)
        if model_request_id is None:
            return
        decision_id = (
            str(trace.joins.decision_id)
            if trace.joins.decision_id is not None
            else None
        )
        operation = trace.payload.get("operation")
        operation_name = (
            operation if isinstance(operation, str) else "model_operation"
        )
        if trace.record_type == "model_request":
            request = trace.payload.get("request")
            if not isinstance(request, dict):
                return
            model = request.get("model")
            self.store.append_model_request(
                run_id=self.run_id,
                model_request_id=str(model_request_id),
                record_id=record.record_id,
                decision_id=decision_id,
                subject_id=trace.subject_id,
                operation=operation_name,
                provider=None,
                model=model if isinstance(model, str) else None,
                status="requested",
                request=request,
            )
            return
        status = trace.payload.get("status")
        if trace.record_type == "model_turn":
            turn = trace.payload.get("turn")
            if not isinstance(turn, dict):
                return
            provider = turn.get("provider")
            model = turn.get("model")
            status = "completed"
            self.store.append_model_request(
                run_id=self.run_id,
                model_request_id=str(model_request_id),
                record_id=record.record_id,
                decision_id=decision_id,
                subject_id=trace.subject_id,
                operation=operation_name,
                provider=provider if isinstance(provider, str) else None,
                model=model if isinstance(model, str) else None,
                status=status,
                request={},
                response=turn,
            )
            round_number = trace.payload.get("round")
            self.store.append_model_turn(
                run_id=self.run_id,
                model_request_id=str(model_request_id),
                turn_index=(
                    round_number
                    if isinstance(round_number, int)
                    and not isinstance(round_number, bool)
                    else 1
                ),
                record_id=record.record_id,
                role="assistant",
                content=turn,
                usage={
                    name: value
                    for name in (
                        "latency_ms",
                        "input_tokens",
                        "output_tokens",
                        "finish_reason",
                    )
                    if (value := turn.get(name)) is not None
                },
            )
            return
        self.store.append_model_request(
            run_id=self.run_id,
            model_request_id=str(model_request_id),
            record_id=record.record_id,
            decision_id=decision_id,
            subject_id=trace.subject_id,
            operation=operation_name,
            provider=None,
            model=None,
            status=status if isinstance(status, str) else "failed",
            request={},
            response=trace.payload,
        )
        round_number = trace.payload.get("round")
        self.store.append_model_turn(
            run_id=self.run_id,
            model_request_id=str(model_request_id),
            turn_index=(
                round_number
                if isinstance(round_number, int)
                and not isinstance(round_number, bool)
                else 1
            ),
            record_id=record.record_id,
            role="error",
            content=trace.payload,
        )

    def _project_memory_trace(
        self,
        record: DatasetRecord,
        trace: ResearchTrace,
    ) -> None:
        operation_id = trace.payload.get("operation_id")
        if not isinstance(operation_id, str):
            return
        status = _trace_status(trace.record_type, trace.payload)
        request = (
            trace.payload if trace.record_type.endswith("_request") else {}
        )
        result = (
            trace.payload if not trace.record_type.endswith("_request") else {}
        )
        self.store.append_memory_operation(
            run_id=self.run_id,
            operation_id=operation_id,
            record_id=record.record_id,
            subject_id=trace.subject_id,
            operation_type=trace.record_type,
            status=status,
            memory_id=(
                str(trace.joins.memory_id)
                if trace.joins.memory_id is not None
                else None
            ),
            request=request,
            result=result,
        )

    def _project_information_trace(
        self,
        record: DatasetRecord,
        trace: ResearchTrace,
    ) -> None:
        retrieval_id = trace.payload.get("operation_id")
        if not isinstance(retrieval_id, str):
            return
        self.store.append_information_retrieval(
            run_id=self.run_id,
            retrieval_id=retrieval_id,
            record_id=record.record_id,
            subject_id=trace.subject_id,
            status=_trace_status(trace.record_type, trace.payload),
            query=(
                trace.payload
                if trace.record_type.endswith("_request")
                else {}
            ),
            result=(
                trace.payload
                if not trace.record_type.endswith("_request")
                else {}
            ),
        )

    def _capture_situation_provenance(
        self,
        scenario: dict[str, JsonValue],
    ) -> None:
        raw = scenario.get("resolved_character_situations")
        if not isinstance(raw, dict):
            return
        for entity_id in sorted(raw):
            artifact = raw[entity_id]
            if not isinstance(artifact, dict):
                continue
            self._append(
                "character_situation_provenance",
                0,
                0.0,
                entity_id,
                artifact,
                None,
                category=RecordCategory.PROVENANCE,
                source=RecordSource.APPLICATION,
                visibility=RecordVisibility.PRIVATE_RESEARCH,
            )
            generation = artifact.get("generation")
            if not isinstance(generation, dict):
                continue
            request = generation.get("request")
            result = generation.get("result")
            if not isinstance(request, dict):
                continue
            model_request_id = request.get("request_id")
            if not isinstance(model_request_id, str):
                continue
            request_record = self._append(
                "model_request",
                0,
                0.0,
                entity_id,
                {
                    "operation": "character_situation_synthesis",
                    "round": 1,
                    "request": request,
                },
                None,
                category=RecordCategory.MODEL,
                source=RecordSource.MODEL_PROVIDER,
                visibility=RecordVisibility.PRIVATE_RESEARCH,
                joins=RecordJoinIds(
                    model_request_id=ModelRequestId(model_request_id)
                ),
                correlation_id=model_request_id,
            )
            raw_model = request.get("model")
            request_model = raw_model if isinstance(raw_model, str) else None
            self.store.append_model_request(
                run_id=self.run_id,
                model_request_id=model_request_id,
                record_id=request_record.record_id,
                decision_id=None,
                subject_id=entity_id,
                operation="character_situation_synthesis",
                provider=None,
                model=request_model,
                status="requested",
                request=request,
            )
            if not isinstance(result, dict):
                continue
            result_record = self._append(
                "model_turn",
                0,
                0.0,
                entity_id,
                {
                    "operation": "character_situation_synthesis",
                    "round": 1,
                    "model_request_id": model_request_id,
                    "turn": result,
                    "nondeterministic_fields": [
                        "turn.latency_ms",
                        "turn.provider_request_id",
                    ],
                },
                None,
                category=RecordCategory.MODEL,
                source=RecordSource.MODEL_PROVIDER,
                visibility=RecordVisibility.PRIVATE_RESEARCH,
                joins=RecordJoinIds(
                    model_request_id=ModelRequestId(model_request_id)
                ),
                correlation_id=model_request_id,
            )
            raw_provider = result.get("provider")
            result_provider = (
                raw_provider if isinstance(raw_provider, str) else None
            )
            raw_model = result.get("model")
            result_model = raw_model if isinstance(raw_model, str) else None
            self.store.append_model_request(
                run_id=self.run_id,
                model_request_id=model_request_id,
                record_id=result_record.record_id,
                decision_id=None,
                subject_id=entity_id,
                operation="character_situation_synthesis",
                provider=result_provider,
                model=result_model,
                status="completed",
                request={},
                response=result,
            )
            self.store.append_model_turn(
                run_id=self.run_id,
                model_request_id=model_request_id,
                turn_index=1,
                record_id=result_record.record_id,
                role="assistant",
                content=result,
                usage={
                    name: value
                    for name in (
                        "latency_ms",
                        "input_tokens",
                        "output_tokens",
                        "finish_reason",
                    )
                    if (value := result.get(name)) is not None
                },
            )

    def _collect_specialized(self, event: DomainEvent) -> None:
        self._track_decision_event(event)
        if event.event_type.startswith("tool."):
            self._project_tool_event(event)
        self._collect_lineage(event)
        if event.event_type.startswith("engagement."):
            self._collect_engagement_event(event)
        self._collect_interaction_event(event)
        if event.event_type.startswith("perception."):
            self._collect_perception_event(event)
        if event.event_type.startswith(
            ("transaction.", "affordance.", "npc.spawn_blocked")
        ):
            self._collect_resource_flow(event)
        if event.event_type.startswith("memory."):
            self._collect_memory_relation_event(event)
        record_type: str | None = None
        if event.event_type.startswith("goal."):
            self._collect_goal_transition(event)
            return
        if event.event_type == "threshold.breached":
            record_type = "threshold_crossing"
        elif event.event_type.startswith("plan."):
            record_type = "plan_transition"
        elif event.event_type.startswith("affordance."):
            record_type = "affordance"
        elif event.event_type.startswith("transaction."):
            record_type = "transaction"
        elif event.event_type.startswith("interaction."):
            record_type = "interaction"
        elif event.event_type.startswith("engagement."):
            record_type = "engagement"
        elif event.event_type.startswith("memory."):
            record_type = "memory_reference"
        elif event.event_type.startswith("information."):
            record_type = "information_retrieval"
        elif event.event_type.startswith("perception."):
            record_type = "perception"
        elif event.event_type.startswith("speech."):
            record_type = "speech"
        elif event.event_type.startswith("tool."):
            record_type = "tool"
        elif event.event_type.startswith("cognition."):
            record_type = "cognition"
        elif event.event_type.startswith(
            ("travel.", "building.", "vehicle.", "metro.")
        ):
            record_type = "travel"
        elif event.event_type.startswith(
            ("time.", "weather.", "surface_condition.", "availability.")
        ):
            record_type = "environment"
        if record_type is not None:
            payload = {
                "event_type": event.event_type,
                **dict(event.payload),
            }
            if event.event_type == "memory.recorded":
                payload.update(self._memory_payload(event))
            self._append(
                record_type,
                event.simulation_tick,
                event.simulation_time,
                event.agent_id,
                payload,
                event.event_id,
                category=self._event_category(event),
                source=RecordSource.DOMAIN_EVENT,
                joins=self._resolved_event_joins(event),
                causation_id=event.causation_id,
                correlation_id=event.correlation_id,
                visibility=(
                    RecordVisibility.PRIVATE_RESEARCH
                    if record_type == "memory_reference"
                    else _event_visibility(event)
                ),
            )
        request_events = {
            "cognition.completed": ("character_decision", "completed"),
            "cognition.failed": ("character_decision", "failed"),
            "cognition.cancelled": ("character_decision", "cancelled"),
        }
        if event.event_type in request_events:
            operation, status = request_events[event.event_type]
            self._append(
                "llm_request",
                event.simulation_tick,
                event.simulation_time,
                event.agent_id,
                {
                    "operation": operation,
                    "status": status,
                    **dict(event.payload),
                },
                event.event_id,
                joins=self._resolved_event_joins(event),
                causation_id=event.causation_id,
                correlation_id=event.correlation_id,
                visibility=_event_visibility(event),
            )

    def _track_decision_event(self, event: DomainEvent) -> None:
        decision_id = event.payload.get("decision_id")
        if not isinstance(decision_id, str):
            return
        episode = self._decision_episodes.get(decision_id)
        if episode is None:
            episode = _DecisionEpisode(
                decision_id=decision_id,
                agent_id=event.agent_id,
                requested_tick=event.simulation_tick,
                requested_at=event.simulation_time,
            )
            self._decision_episodes[decision_id] = episode
        status: str | None = None
        terminal = False
        terminal_reason: str | None = None
        if event.event_type == "cognition.completed":
            status = "model_completed"
            episode.stage_times["model_completed"] = event.simulation_time
        elif event.event_type == "tool.proposed":
            status = "tool_proposed"
            episode.stage_times["tool_proposed"] = event.simulation_time
            tool_name = event.payload.get("tool_name")
            if isinstance(tool_name, str):
                episode.selected_option_id = f"tool:{tool_name}"
            tool_call_id = event.payload.get("tool_call_id")
            if isinstance(tool_call_id, str):
                episode.tool_call_id = tool_call_id
        elif event.event_type == "tool.accepted":
            status = "tool_validated"
            episode.stage_times["tool_validated"] = event.simulation_time
        elif event.event_type == "tool.committed":
            status = "action_committed"
            episode.stage_times["action_committed"] = event.simulation_time
            action_id = event.payload.get("action_id")
            if isinstance(action_id, str):
                episode.action_id = action_id
        elif event.event_type == "action.queued":
            status = "action_queued"
            episode.stage_times["action_queued"] = event.simulation_time
            action_id = event.payload.get("action_id")
            if isinstance(action_id, str):
                episode.action_id = action_id
        elif event.event_type == "action.started":
            status = "action_executing"
            episode.stage_times["action_started"] = event.simulation_time
            action_id = event.payload.get("action_id")
            if isinstance(action_id, str):
                episode.action_id = action_id
        elif event.event_type == "tool.rejected":
            status = "rejected"
            terminal = True
            terminal_reason = _event_reason(event)
        elif event.event_type == "cognition.failed":
            status = "failed"
            terminal = True
            terminal_reason = _event_reason(event)
        elif event.event_type == "cognition.cancelled":
            status = "cancelled"
            terminal = True
            terminal_reason = _event_reason(event)
        elif event.event_type == "cognition.skipped":
            status = "skipped"
            terminal = True
            terminal_reason = "controller_skipped"
        elif event.event_type in {
            "action.completed",
            "action.failed",
            "action.cancelled",
        }:
            status = event.event_type.removeprefix("action.")
            terminal = True
            terminal_reason = _event_reason(event)
            action_id = event.payload.get("action_id")
            if isinstance(action_id, str):
                episode.action_id = action_id
            links = self._goal_links(event)
            if links:
                episode.goal_id = links[0][0]
        if status is None:
            return
        episode.status = status
        episode.terminal_reason = terminal_reason
        self.store.append_decision(
            run_id=self.run_id,
            decision_id=decision_id,
            record_id=(
                self._current_event_record_id
                or f"{self.run_id}:record:{self._sequence:08d}"
            ),
            subject_id=event.agent_id,
            simulation_tick=episode.requested_tick,
            status=status,
            selected_option_id=episode.selected_option_id,
            context=episode.context,
            outcome={
                "event_type": event.event_type,
                **dict(event.payload),
            },
        )
        if episode.selected_option_id is not None and episode.context:
            for index, option in enumerate(
                _decision_options(episode.context)
            ):
                if option.get("option_id") != episode.selected_option_id:
                    continue
                option_type = option.get("option_type")
                if not isinstance(option_type, str):
                    continue
                self.store.append_decision_option(
                    run_id=self.run_id,
                    decision_id=decision_id,
                    option_id=episode.selected_option_id,
                    record_id=(
                        self._current_event_record_id
                        or f"{self.run_id}:record:{self._sequence:08d}"
                    ),
                    option_index=index,
                    option_type=option_type,
                    selected=True,
                    option=option,
                )
        if terminal:
            self._close_decision_episode(episode, event)

    def _close_decision_episode(
        self,
        episode: _DecisionEpisode,
        event: DomainEvent,
    ) -> None:
        delays: dict[str, JsonValue] = {
            name: round(max(0.0, value - episode.requested_at), 12)
            for name, value in sorted(episode.stage_times.items())
        }
        delays["terminal"] = round(
            max(0.0, event.simulation_time - episode.requested_at),
            12,
        )
        action_started = episode.stage_times.get("action_started")
        action_committed = episode.stage_times.get("action_committed")
        action_queued = episode.stage_times.get("action_queued")
        if action_started is not None:
            queue_origin = (
                action_committed
                if action_committed is not None
                else action_queued
                if action_queued is not None
                else episode.requested_at
            )
            delays["action_queue"] = round(
                max(0.0, action_started - queue_origin),
                12,
            )
            delays["action_execution"] = round(
                max(0.0, event.simulation_time - action_started),
                12,
            )
        payload: dict[str, JsonValue] = {
            "feature_schema": "stage0.feature.decision_episode.v1",
            "decision_id": episode.decision_id,
            "status": episode.status,
            "selected_option_id": episode.selected_option_id,
            "tool_call_id": episode.tool_call_id,
            "action_id": episode.action_id,
            "goal_id": episode.goal_id,
            "requested_tick": episode.requested_tick,
            "terminal_tick": event.simulation_tick,
            "requested_at": episode.requested_at,
            "terminal_at": event.simulation_time,
            "terminal_reason": episode.terminal_reason,
            "delays": delays,
            "terminal_event_id": event.event_id,
            "terminal_event_type": event.event_type,
        }
        joins = RecordJoinIds(
            goal_id=(
                GoalId(episode.goal_id)
                if episode.goal_id is not None
                else None
            ),
            action_id=(
                ActionId(episode.action_id)
                if episode.action_id is not None
                else None
            ),
            decision_id=DecisionId(episode.decision_id),
            tool_call_id=(
                ToolCallId(episode.tool_call_id)
                if episode.tool_call_id is not None
                else None
            ),
        )
        record = self._append(
            "decision_episode",
            event.simulation_tick,
            event.simulation_time,
            episode.agent_id,
            payload,
            event.event_id,
            category=RecordCategory.DECISION,
            source=RecordSource.DERIVED,
            visibility=RecordVisibility.PRIVATE_RESEARCH,
            joins=joins,
            causation_id=event.event_id,
            correlation_id=episode.decision_id,
            schema_id="stage0.feature.decision_episode",
            schema_version="1",
        )
        self.store.append_decision(
            run_id=self.run_id,
            decision_id=episode.decision_id,
            record_id=record.record_id,
            subject_id=episode.agent_id,
            simulation_tick=episode.requested_tick,
            status=episode.status,
            selected_option_id=episode.selected_option_id,
            context=episode.context,
            outcome=payload,
        )
        self.store.append_decision_episode(
            run_id=self.run_id,
            decision_id=episode.decision_id,
            record_id=record.record_id,
            subject_id=episode.agent_id,
            action_id=episode.action_id,
            goal_id=episode.goal_id,
            tool_call_id=episode.tool_call_id,
            status=episode.status,
            selected_option_id=episode.selected_option_id,
            requested_tick=episode.requested_tick,
            terminal_tick=event.simulation_tick,
            requested_at=episode.requested_at,
            terminal_at=event.simulation_time,
            terminal_reason=episode.terminal_reason,
            delays=delays,
            episode=payload,
        )

    def _project_tool_event(self, event: DomainEvent) -> None:
        tool_call_id = event.payload.get("tool_call_id")
        tool_name = event.payload.get("tool_name")
        if not isinstance(tool_call_id, str):
            return
        if isinstance(tool_name, str):
            self._tool_names[tool_call_id] = tool_name
        else:
            tool_name = self._tool_names.get(tool_call_id)
        if tool_name is None:
            return
        decision_id = event.payload.get("decision_id")
        action_id = event.payload.get("action_id")
        status = event.event_type.removeprefix("tool.")
        arguments = event.payload.get("arguments")
        input_data: dict[str, JsonValue] = (
            arguments if isinstance(arguments, dict) else {}
        )
        self.store.append_tool_execution(
            run_id=self.run_id,
            tool_call_id=tool_call_id,
            record_id=(
                self._current_event_record_id
                or f"{self.run_id}:record:{self._sequence:08d}"
            ),
            decision_id=(
                decision_id if isinstance(decision_id, str) else None
            ),
            action_id=action_id if isinstance(action_id, str) else None,
            subject_id=event.agent_id,
            tool_name=tool_name,
            status=status,
            input_data=input_data,
            output_data=dict(event.payload),
        )

    def _collect_lineage(self, event: DomainEvent) -> None:
        if not event.event_type.startswith("action."):
            self._track_action_source_event(event)
        if event.event_type in {"plan.created", "plan.revised", "plan.cleared"}:
            self._collect_plan_lineage(event)
        if event.event_type.startswith("action."):
            self._collect_action_lineage(event)

    def _track_action_source_event(self, event: DomainEvent) -> None:
        action_id = event.payload.get("action_id")
        if not isinstance(action_id, str):
            return
        episode = self._action_episodes.get(action_id)
        if episode is not None and event.event_id not in episode.source_event_ids:
            episode.source_event_ids.append(event.event_id)

    def _collect_plan_lineage(self, event: DomainEvent) -> None:
        plan_id = event.payload.get("plan_id")
        revision = event.payload.get("plan_revision")
        origin = event.payload.get("origin")
        if (
            not isinstance(plan_id, str)
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or not isinstance(origin, str)
        ):
            return
        status = event.event_type.removeprefix("plan.")
        payload = {"event_type": event.event_type, **dict(event.payload)}
        record = self._append(
            "plan_lifecycle",
            event.simulation_tick,
            event.simulation_time,
            event.agent_id,
            payload,
            event.event_id,
            category=RecordCategory.ACTION,
            source=RecordSource.DOMAIN_EVENT,
            joins=RecordJoinIds(plan_id=PlanId(plan_id)),
            causation_id=event.causation_id,
            correlation_id=event.correlation_id,
            visibility=_event_visibility(event),
        )
        root = event.payload.get("root_correlation_id")
        self.store.append_plan(
            run_id=self.run_id,
            plan_id=plan_id,
            record_id=record.record_id,
            subject_id=event.agent_id,
            revision=revision,
            origin=origin,
            status=status,
            root_correlation_id=root if isinstance(root, str) else None,
            plan=payload,
        )

    def _collect_action_lineage(self, event: DomainEvent) -> None:
        action_id = event.payload.get("action_id")
        action_type = event.payload.get("action")
        origin = event.payload.get("action_origin")
        created_tick = event.payload.get("action_created_tick")
        created_at = event.payload.get("action_created_at")
        root_correlation_id = event.payload.get("root_correlation_id")
        if (
            not isinstance(action_id, str)
            or not isinstance(action_type, str)
            or not isinstance(origin, str)
            or not isinstance(created_tick, int)
            or isinstance(created_tick, bool)
            or not isinstance(created_at, int | float)
            or isinstance(created_at, bool)
            or not isinstance(root_correlation_id, str)
        ):
            return
        status = event.event_type.removeprefix("action.")
        payload = {"event_type": event.event_type, **dict(event.payload)}
        joins = self._resolved_event_joins(event)
        record = self._append(
            "action_transition",
            event.simulation_tick,
            event.simulation_time,
            event.agent_id,
            payload,
            event.event_id,
            category=RecordCategory.ACTION,
            source=RecordSource.DOMAIN_EVENT,
            joins=joins,
            causation_id=event.causation_id,
            correlation_id=event.correlation_id,
            visibility=_event_visibility(event),
        )
        episode = self._action_episodes.get(action_id)
        previous_status = episode.status if episode is not None else None
        if episode is None:
            episode = _ActionEpisode(
                action_id=action_id,
                agent_id=event.agent_id,
                created_tick=created_tick,
                created_at=float(created_at),
                status=status,
                source_event_ids=[],
                payload=payload,
            )
            self._action_episodes[action_id] = episode
        episode.status = status
        episode.source_event_ids.append(event.event_id)
        plan_id = event.payload.get("plan_id")
        plan_revision = event.payload.get("plan_revision")
        decision_id = event.payload.get("decision_id")
        tool_call_id = event.payload.get("tool_call_id")
        goal_links = self._goal_links(event)
        self.store.append_action_instance(
            run_id=self.run_id,
            action_id=action_id,
            record_id=record.record_id,
            plan_id=plan_id if isinstance(plan_id, str) else None,
            goal_id=goal_links[0][0] if goal_links else None,
            decision_id=decision_id if isinstance(decision_id, str) else None,
            tool_call_id=tool_call_id if isinstance(tool_call_id, str) else None,
            subject_id=event.agent_id,
            action_type=action_type,
            status=status,
            origin=origin,
            plan_revision=(
                plan_revision
                if isinstance(plan_revision, int)
                and not isinstance(plan_revision, bool)
                else None
            ),
            created_tick=created_tick,
            created_at=float(created_at),
            root_correlation_id=root_correlation_id,
            action=payload,
        )
        self.store.append_action_transition(
            run_id=self.run_id,
            action_transition_id=f"{record.record_id}:action-transition",
            record_id=record.record_id,
            action_id=action_id,
            simulation_tick=event.simulation_tick,
            from_status=previous_status,
            to_status=status,
            transition=payload,
        )
        for ordinal, (goal_id, link_kind) in enumerate(goal_links):
            self.store.append_goal_action_link(
                run_id=self.run_id,
                goal_id=goal_id,
                action_id=action_id,
                record_id=record.record_id,
                link_kind=link_kind,
                ordinal=ordinal,
            )
            self.store.add_record_relation(
                RecordRelation(
                    run_id=self.run_id,
                    record_id=record.record_id,
                    relation_type="goal_action_link",
                    target_type="goal",
                    target_id=goal_id,
                    ordinal=ordinal,
                    metadata={"kind": link_kind},
                )
            )
        if status not in {"completed", "failed", "cancelled", "interrupted"}:
            return
        if event.agent_id is not None:
            self._pending_action_outcomes.setdefault(
                event.agent_id, []
            ).append(
                {
                    "action_id": action_id,
                    "terminal_status": status,
                    "terminal_event_id": event.event_id,
                    "terminal_event_type": event.event_type,
                    "terminal_tick": event.simulation_tick,
                    "terminal_at": event.simulation_time,
                    "reason": event.payload.get("reason"),
                }
            )
        elapsed = round(
            max(0.0, event.simulation_time - episode.created_at),
            12,
        )
        episode_payload: dict[str, JsonValue] = {
            "feature_schema": "stage0.feature.action_episode.v1",
            "action_id": action_id,
            "terminal_status": status,
            "created_tick": episode.created_tick,
            "terminal_tick": event.simulation_tick,
            "created_at": episode.created_at,
            "terminal_at": event.simulation_time,
            "elapsed_simulation_time": elapsed,
            "source_event_ids": list(episode.source_event_ids),
            "terminal_event_id": event.event_id,
        }
        episode_record = self._append(
            "action_episode",
            event.simulation_tick,
            event.simulation_time,
            event.agent_id,
            episode_payload,
            event.event_id,
            category=RecordCategory.ACTION,
            source=RecordSource.DERIVED,
            joins=joins,
            causation_id=event.event_id,
            correlation_id=event.correlation_id,
            visibility=_event_visibility(event),
            schema_id="stage0.feature.action_episode",
            schema_version="1",
        )
        self.store.append_action_episode(
            run_id=self.run_id,
            action_id=action_id,
            record_id=episode_record.record_id,
            subject_id=event.agent_id,
            terminal_status=status,
            created_tick=episode.created_tick,
            terminal_tick=event.simulation_tick,
            created_at=episode.created_at,
            terminal_at=event.simulation_time,
            elapsed_simulation_time=elapsed,
            source_event_ids=tuple(episode.source_event_ids),
            episode=episode_payload,
        )

    @staticmethod
    def _goal_links(event: DomainEvent) -> tuple[tuple[str, str], ...]:
        value = event.payload.get("goal_links")
        if not isinstance(value, list):
            return ()
        links: list[tuple[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            goal_id = item.get("goal_id")
            kind = item.get("kind")
            if isinstance(goal_id, str) and isinstance(kind, str):
                links.append((goal_id, kind))
        return tuple(links)

    def _resolved_event_joins(self, event: DomainEvent) -> RecordJoinIds:
        direct = self._event_joins(event)
        inherited = (
            self._event_lineage.get(event.causation_id)
            if event.causation_id is not None
            else None
        )
        if inherited is None and event.correlation_id is not None:
            inherited = self._event_lineage.get(event.correlation_id)
        if inherited is None:
            return direct
        return RecordJoinIds(
            goal_id=direct.goal_id or inherited.goal_id,
            plan_id=direct.plan_id or inherited.plan_id,
            action_id=direct.action_id or inherited.action_id,
            decision_id=direct.decision_id or inherited.decision_id,
            model_request_id=(
                direct.model_request_id or inherited.model_request_id
            ),
            tool_call_id=direct.tool_call_id or inherited.tool_call_id,
            interaction_id=direct.interaction_id or inherited.interaction_id,
            engagement_id=direct.engagement_id or inherited.engagement_id,
            engagement_group_id=(
                direct.engagement_group_id
                or inherited.engagement_group_id
            ),
            engagement_invocation_id=(
                direct.engagement_invocation_id
                or inherited.engagement_invocation_id
            ),
            perception_fact_id=(
                direct.perception_fact_id or inherited.perception_fact_id
            ),
            memory_id=direct.memory_id or inherited.memory_id,
            transaction_request_id=(
                direct.transaction_request_id
                or inherited.transaction_request_id
            ),
            operator_intervention_id=(
                direct.operator_intervention_id
                or inherited.operator_intervention_id
            ),
        )

    @staticmethod
    def _event_joins(event: DomainEvent) -> RecordJoinIds:
        payload = event.payload

        def text(name: str) -> str | None:
            value = payload.get(name)
            return value if isinstance(value, str) else None

        goal_id = text("goal_id")
        if goal_id is None:
            goal_ids = payload.get("goal_ids")
            if (
                isinstance(goal_ids, list)
                and goal_ids
                and isinstance(goal_ids[0], str)
            ):
                goal_id = goal_ids[0]
        return RecordJoinIds(
            goal_id=GoalId(goal_id) if goal_id is not None else None,
            plan_id=PlanId(value) if (value := text("plan_id")) else None,
            action_id=ActionId(value) if (value := text("action_id")) else None,
            decision_id=(
                DecisionId(value) if (value := text("decision_id")) else None
            ),
            tool_call_id=(
                ToolCallId(value) if (value := text("tool_call_id")) else None
            ),
            engagement_id=(
                EngagementId(value)
                if (value := text("engagement_id"))
                else None
            ),
            engagement_group_id=(
                EngagementGroupId(value)
                if (value := text("group_id"))
                else None
            ),
            engagement_invocation_id=(
                EngagementInvocationId(value)
                if (value := text("invocation_id"))
                else None
            ),
            transaction_request_id=(
                TransactionRequestId(value)
                if (value := text("request_id"))
                else None
            ),
            operator_intervention_id=(
                OperatorInterventionId(value)
                if (value := text("operator_intervention_id"))
                else None
            ),
        )

    @staticmethod
    def _event_category(event: DomainEvent) -> RecordCategory:
        if event.event_type.startswith(("plan.", "action.")):
            return RecordCategory.ACTION
        if event.event_type.startswith("interaction."):
            return RecordCategory.INTERACTION
        if event.event_type.startswith("engagement."):
            return RecordCategory.ENGAGEMENT
        if event.event_type.startswith("tool."):
            return RecordCategory.TOOL
        if event.event_type.startswith("goal."):
            return RecordCategory.GOAL
        return RecordCategory.OTHER

    def _initialize_goals(self) -> None:
        for subject_id, component in self.runner.registry.query(GoalComponent):
            for goal in component.goals:
                serialized = serialize_authoritative(goal)
                if not isinstance(serialized, dict):
                    raise TypeError("serialized goal runtime must be an object")
                record = self._append(
                    "goal_definition",
                    self.runner.clock.tick,
                    self.runner.clock.simulation_time,
                    subject_id,
                    serialized,
                    None,
                    category=RecordCategory.GOAL,
                    source=RecordSource.APPLICATION,
                    visibility=RecordVisibility.PRIVATE_RESEARCH,
                    joins=RecordJoinIds(
                        goal_id=GoalId(goal.definition.id)
                    ),
                )
                self.store.append_goal(
                    run_id=self.run_id,
                    goal_id=goal.definition.id,
                    record_id=record.record_id,
                    subject_id=subject_id,
                    description=goal.definition.description,
                    status=goal.status.value,
                    goal=serialized,
                )
                self._goal_episodes[goal.definition.id] = _GoalEpisode(
                    goal_id=goal.definition.id,
                    subject_id=subject_id,
                    activated_tick=self.runner.clock.tick,
                    activated_at=self.runner.clock.simulation_time,
                    description=goal.definition.description,
                )

    def _collect_goal_transition(self, event: DomainEvent) -> None:
        goal_id = event.payload.get("goal_id")
        status = event.payload.get("status")
        if not isinstance(goal_id, str) or not isinstance(status, str):
            return
        payload = {
            "event_type": event.event_type,
            **dict(event.payload),
        }
        record = self._append(
            "goal_transition",
            event.simulation_tick,
            event.simulation_time,
            event.agent_id,
            payload,
            event.event_id,
            category=RecordCategory.GOAL,
            source=RecordSource.DOMAIN_EVENT,
            visibility=RecordVisibility.PRIVATE_RESEARCH,
            joins=RecordJoinIds(goal_id=GoalId(goal_id)),
        )
        previous_status = event.payload.get("previous_status")
        self.store.append_goal_transition(
            run_id=self.run_id,
            goal_transition_id=f"{record.record_id}:goal-transition",
            record_id=record.record_id,
            goal_id=goal_id,
            simulation_tick=event.simulation_tick,
            from_status=(
                previous_status if isinstance(previous_status, str) else None
            ),
            to_status=status,
            transition=payload,
        )
        episode = self._goal_episodes.get(goal_id)
        if episode is not None:
            episode.source_event_ids.append(event.event_id)
            if event.event_type == "goal.activated":
                episode.activated_tick = event.simulation_tick
                episode.activated_at = event.simulation_time
            if status in {
                "succeeded",
                "failed",
                "expired",
                "retired",
            }:
                self._close_goal_episode(episode, event, status)
        runtime = self._goal_runtime(event.agent_id, goal_id)
        if runtime is None:
            return
        serialized = serialize_authoritative(runtime)
        if not isinstance(serialized, dict):
            raise TypeError("serialized goal runtime must be an object")
        self.store.append_goal(
            run_id=self.run_id,
            goal_id=goal_id,
            record_id=record.record_id,
            subject_id=event.agent_id,
            description=runtime.definition.description,
            status=runtime.status.value,
            goal=serialized,
        )

    def _close_goal_episode(
        self,
        episode: _GoalEpisode,
        event: DomainEvent,
        terminal_status: str,
    ) -> None:
        if episode.goal_id not in self._goal_episodes:
            return
        duration = round(
            max(0.0, event.simulation_time - episode.activated_at),
            12,
        )
        payload: dict[str, JsonValue] = {
            "feature_schema": "stage0.feature.goal_episode.v1",
            "goal_id": episode.goal_id,
            "description": episode.description,
            "terminal_status": terminal_status,
            "activated_tick": episode.activated_tick,
            "terminal_tick": event.simulation_tick,
            "activated_at": episode.activated_at,
            "terminal_at": event.simulation_time,
            "duration": duration,
            "source_event_ids": list(episode.source_event_ids),
            "terminal_event_id": event.event_id,
        }
        record = self._append(
            "goal_episode",
            event.simulation_tick,
            event.simulation_time,
            episode.subject_id,
            payload,
            event.event_id,
            category=RecordCategory.GOAL,
            source=RecordSource.DERIVED,
            visibility=RecordVisibility.PRIVATE_RESEARCH,
            joins=RecordJoinIds(goal_id=GoalId(episode.goal_id)),
            causation_id=event.event_id,
            correlation_id=episode.goal_id,
            schema_id="stage0.feature.goal_episode",
            schema_version="1",
        )
        self.store.append_goal_episode(
            run_id=self.run_id,
            goal_id=episode.goal_id,
            record_id=record.record_id,
            subject_id=episode.subject_id,
            terminal_status=terminal_status,
            activated_tick=episode.activated_tick,
            terminal_tick=event.simulation_tick,
            activated_at=episode.activated_at,
            terminal_at=event.simulation_time,
            duration=duration,
            episode=payload,
        )
        del self._goal_episodes[episode.goal_id]

    def _close_open_goals(self, terminal_tick: int, terminal_at: float) -> None:
        for goal_id in sorted(tuple(self._goal_episodes)):
            episode = self._goal_episodes[goal_id]
            synthetic = DomainEvent(
                run_id=self.run_id,
                event_id=f"{self.run_id}:goal-final:{goal_id}",
                simulation_tick=terminal_tick,
                simulation_time=terminal_at,
                wall_time=self.runner.events.events[-1].wall_time,
                event_type="goal.finalized",
                agent_id=episode.subject_id,
                payload={"status": "unknown"},
                correlation_id=goal_id,
            )
            self._close_goal_episode(episode, synthetic, "unknown")

    def _goal_runtime(
        self,
        subject_id: str | None,
        goal_id: str,
    ) -> GoalRuntime | None:
        if (
            subject_id is None
            or not self.runner.registry.has_component(
                subject_id, GoalComponent
            )
        ):
            return None
        try:
            return self.runner.registry.get_component(
                subject_id, GoalComponent
            ).get(goal_id)
        except KeyError:
            return None

    def _collect_tick_state(self, event: DomainEvent) -> None:
        self._append(
            "environment_state",
            event.simulation_tick,
            event.simulation_time,
            None,
            self._environment_state(event.simulation_time),
            event.event_id,
        )
        for agent_id in self.runner.registry.entities():
            state = self.projector.project(self.runner.registry, agent_id)
            if not state:
                continue
            self._append(
                "state_vector",
                event.simulation_tick,
                event.simulation_time,
                agent_id,
                state,
                event.event_id,
                visibility=(
                    RecordVisibility.PRIVATE_RESEARCH
                    if "physical" in state
                    else RecordVisibility.OPERATOR
                ),
            )
            if self.runner.registry.has_component(agent_id, PositionComponent):
                position = self.runner.registry.get_component(
                    agent_id, PositionComponent
                )
                trajectory: dict[str, JsonValue] = {}
                if self.runner.registry.has_component(
                    agent_id, SpatialLocationComponent
                ):
                    location = self.runner.registry.get_component(
                        agent_id, SpatialLocationComponent
                    ).location
                    room_id = None
                    building_id = None
                    city_zone_id = None
                    if self.runner.registry.has_resource(CityWorld):
                        city = self.runner.registry.get_resource(CityWorld)
                        try:
                            room = city.room(location.place_id)
                        except KeyError:
                            room = None
                        if room is not None:
                            building = city.building(room.building_id)
                            room_id = room.id
                            building_id = building.id
                            city_zone_id = building.district_id
                    trajectory["spatial_location"] = {
                        "scale": location.scale.value,
                        "place_id": location.place_id,
                        "room_id": room_id,
                        "building_id": building_id,
                        "city_zone_id": city_zone_id,
                        "local_coordinate": (
                            location.local_coordinate.to_payload()
                            if location.local_coordinate is not None
                            else None
                        ),
                        "network_node_id": location.network_node_id,
                        "edge_id": location.edge_id,
                        "edge_progress": location.edge_progress,
                    }
                    if location.local_coordinate is not None:
                        trajectory["position"] = (
                            location.local_coordinate.to_payload()
                        )
                else:
                    trajectory["position"] = position.coordinate.to_payload()
                self._append(
                    "trajectory",
                    event.simulation_tick,
                    event.simulation_time,
                    agent_id,
                    trajectory,
                    event.event_id,
                )

    def _environment_state(
        self,
        simulation_time: float,
    ) -> dict[str, JsonValue]:
        registry = self.runner.registry
        payload: dict[str, JsonValue] = {
            "time": None,
            "weather": None,
            "effects": None,
            "surface_conditions": [],
            "availability": [],
        }
        if registry.has_resource(SimulationCalendar):
            payload["time"] = registry.get_resource(
                SimulationCalendar
            ).payload_at(simulation_time)
        if registry.has_resource(WeatherRuntime):
            weather = registry.get_resource(WeatherRuntime)
            payload["weather"] = weather.current.to_payload()
            payload["effects"] = {
                "walking_speed_multiplier": (
                    weather.effects.walking_speed_multiplier
                ),
                "cycling_speed_multiplier": (
                    weather.effects.cycling_speed_multiplier
                ),
                "visibility_multiplier": (
                    weather.effects.visibility_multiplier
                ),
            }
        if registry.has_resource(SurfaceConditionRegistry):
            surfaces = registry.get_resource(SurfaceConditionRegistry)
            payload["surface_conditions"] = [
                {
                    "surface_id": surface_id,
                    "wetness": value,
                    "band": wetness_band(value).value,
                }
                for surface_id, value in sorted(surfaces.wetness.items())
            ]
        if registry.has_resource(EnvironmentAvailabilityRegistry):
            availability = registry.get_resource(
                EnvironmentAvailabilityRegistry
            )
            kinds = (
                {
                    rule.resource_id: rule.resource_kind
                    for rule in registry.get_resource(
                        EnvironmentAvailabilityRules
                    ).rules
                }
                if registry.has_resource(EnvironmentAvailabilityRules)
                else {}
            )
            payload["availability"] = [
                {
                    "resource_id": resource_id,
                    "resource_kind": kinds.get(resource_id),
                    **state.to_payload(),
                }
                for resource_id, state in sorted(availability.states.items())
            ]
        return payload

    def _initialize_activities(self, event: DomainEvent) -> None:
        for agent_id, activity in self.runner.registry.query(ActivityComponent):
            self._activities[agent_id] = _ActivityInterval(
                activity=activity.current.value,
                start_tick=event.simulation_tick,
                start_time=event.simulation_time,
            )

    def _change_activity(self, event: DomainEvent) -> None:
        agent_id = event.agent_id
        if agent_id is None:
            return
        self._close_activity(
            agent_id,
            event.simulation_tick,
            event.simulation_time,
            source_event_id=event.event_id,
        )
        current = event.payload.get("current")
        if isinstance(current, str):
            self._activities[agent_id] = _ActivityInterval(
                activity=current,
                start_tick=event.simulation_tick,
                start_time=event.simulation_time,
            )

    def _close_activity(
        self,
        agent_id: str,
        end_tick: int,
        end_time: float,
        *,
        source_event_id: str | None,
    ) -> None:
        interval = self._activities.pop(agent_id, None)
        if interval is None:
            return
        self._append(
            "activity_interval",
            end_tick,
            end_time,
            agent_id,
            {
                "activity": interval.activity,
                "start_tick": interval.start_tick,
                "end_tick": end_tick,
                "start_time": interval.start_time,
                "end_time": end_time,
                "duration": round(max(0.0, end_time - interval.start_time), 12),
            },
            source_event_id,
        )

    def _memory_payload(self, event: DomainEvent) -> dict[str, JsonValue]:
        memory_id = event.payload.get("memory_id")
        if not isinstance(memory_id, str):
            return {}
        store = self.runner.registry.get_resource(EpisodicMemoryStore)
        record = next(
            (candidate for candidate in store.records if candidate.id == memory_id),
            None,
        )
        if record is None:
            return {}
        return {
            "text": record.text,
            "embedding": list(record.embedding),
            "memory_metadata": record.metadata,
        }

    def _append(
        self,
        record_type: str,
        tick: int,
        simulation_time: float,
        subject_id: str | None,
        payload: dict[str, JsonValue],
        source_event_id: str | None,
        *,
        category: RecordCategory = RecordCategory.OTHER,
        source: RecordSource = RecordSource.DATASET_COLLECTOR,
        phase: RunnerPhase = RunnerPhase.UNSPECIFIED,
        visibility: RecordVisibility = RecordVisibility.OPERATOR,
        related_entity_ids: tuple[str, ...] = (),
        joins: RecordJoinIds | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
        schema_id: str = "",
        schema_version: str = DATASET_SCHEMA_VERSION,
    ) -> DatasetRecord:
        return self._record_projector.append(
            record_type,
            tick,
            simulation_time,
            subject_id,
            payload,
            source_event_id,
            category=category,
            source=source,
            phase=phase,
            visibility=visibility,
            related_entity_ids=related_entity_ids,
            joins=joins,
            causation_id=causation_id,
            correlation_id=correlation_id,
            schema_id=schema_id,
            schema_version=schema_version,
        )


def _event_visibility(event: DomainEvent) -> RecordVisibility:
    if event_payload_is_private(event.payload):
        return RecordVisibility.PRIVATE_RESEARCH
    return RecordVisibility.OPERATOR


def _persist_engagement_feature_payload(
    store: DatasetCaptureRepository,
    record: DatasetRecord,
    payload: dict[str, JsonValue],
) -> None:
    engagement_value = payload.get("engagement")
    if not isinstance(engagement_value, dict):
        raise TypeError("engagement feature must contain an engagement object")
    engagement_id = _required_string(
        engagement_value.get("engagement_id"),
        "engagement_id",
    )
    actor_id = _required_string(
        engagement_value.get("actor_id"),
        "actor_id",
    )
    referenced_ids = _optional_string_tuple(
        engagement_value.get("referenced_ids")
    )
    private_proposal = engagement_value.get("private_proposal")
    if private_proposal is not None and not isinstance(
        private_proposal,
        dict,
    ):
        raise TypeError("private_proposal must be an object")
    private_result = engagement_value.get("private_result")
    if private_result is not None and not isinstance(private_result, dict):
        raise TypeError("private_result must be an object")
    store.append_engagement(
        run_id=record.run_id,
        engagement_id=engagement_id,
        record_id=record.record_id,
        actor_id=actor_id,
        action_id=_optional_string(engagement_value.get("action_id")),
        plan_id=_optional_string(engagement_value.get("plan_id")),
        plan_revision=_optional_integer_value(
            engagement_value.get("plan_revision")
        ),
        decision_id=_optional_string(engagement_value.get("decision_id")),
        tool_call_id=_optional_string(engagement_value.get("tool_call_id")),
        compiler_request_id=_optional_string(
            engagement_value.get("compiler_request_id")
        ),
        root_correlation_id=_optional_string(
            engagement_value.get("root_correlation_id")
        ),
        referenced_ids=referenced_ids,
        scene_hash=_optional_string(engagement_value.get("scene_hash")),
        scene_version=_optional_string(
            engagement_value.get("scene_version")
        ),
        catalog_version=_optional_string(
            engagement_value.get("catalog_version")
        ),
        prompt_version=_optional_string(
            engagement_value.get("prompt_version")
        ),
        status=_required_string(engagement_value.get("status"), "status"),
        compiler_status=_optional_string(
            engagement_value.get("compiler_status")
        ),
        private_intent=_optional_string(
            engagement_value.get("private_intent")
        ),
        private_controller_reason=_optional_string(
            engagement_value.get("private_controller_reason")
        ),
        private_compiler_summary=_optional_string(
            engagement_value.get("private_compiler_summary")
        ),
        private_proposal=private_proposal,
        private_result=private_result,
        requested_tick=_required_integer_value(
            engagement_value.get("requested_tick"),
            "requested_tick",
        ),
        requested_at=_required_number_value(
            engagement_value.get("requested_at"),
            "requested_at",
        ),
        started_tick=_optional_integer_value(
            engagement_value.get("started_tick")
        ),
        started_at=_optional_number_value(
            engagement_value.get("started_at")
        ),
        terminal_tick=_optional_integer_value(
            engagement_value.get("terminal_tick")
        ),
        terminal_at=_optional_number_value(
            engagement_value.get("terminal_at")
        ),
        terminal_outcome=_optional_string(
            engagement_value.get("terminal_outcome")
        ),
    )
    groups_value = payload.get("groups")
    if not isinstance(groups_value, list):
        raise TypeError("engagement feature groups must be an array")
    for group_value in groups_value:
        if not isinstance(group_value, dict):
            raise TypeError("engagement feature group must be an object")
        group_id = _required_string(
            group_value.get("engagement_group_id"),
            "engagement_group_id",
        )
        private_issues = group_value.get("private_issues")
        if private_issues is not None and not isinstance(
            private_issues,
            list,
        ):
            raise TypeError("private_issues must be an array")
        group_private_proposal = group_value.get("private_proposal")
        if group_private_proposal is not None and not isinstance(
            group_private_proposal,
            dict,
        ):
            raise TypeError("group private_proposal must be an object")
        grounded_outcome = group_value.get("grounded_outcome", {})
        if not isinstance(grounded_outcome, dict):
            raise TypeError("group grounded_outcome must be an object")
        store.append_engagement_group(
            run_id=record.run_id,
            engagement_id=engagement_id,
            engagement_group_id=group_id,
            record_id=record.record_id,
            ordinal=_optional_integer_value(group_value.get("ordinal")),
            required_atomic=_optional_boolean_value(
                group_value.get("required_atomic")
            ),
            validation_status=_required_string(
                group_value.get("validation_status"),
                "validation_status",
            ),
            execution_status=_required_string(
                group_value.get("execution_status"),
                "execution_status",
            ),
            status=_required_string(group_value.get("status"), "status"),
            private_rejection_reason=_optional_string(
                group_value.get("private_rejection_reason")
            ),
            failure_reason=_optional_string(
                group_value.get("failure_reason")
            ),
            private_issues=private_issues,
            private_proposal=group_private_proposal,
            grounded_outcome=grounded_outcome,
        )
        invocations_value = group_value.get("invocations")
        if not isinstance(invocations_value, list):
            raise TypeError("engagement invocations must be an array")
        for invocation_value in invocations_value:
            if not isinstance(invocation_value, dict):
                raise TypeError(
                    "engagement feature invocation must be an object"
                )
            proposal_arguments_value = invocation_value.get(
                "private_proposal_arguments"
            )
            normalized_arguments_value = invocation_value.get(
                "private_normalized_arguments"
            )
            invocation_private_result_value = invocation_value.get(
                "private_result"
            )
            for name, value in (
                ("private_proposal_arguments", proposal_arguments_value),
                (
                    "private_normalized_arguments",
                    normalized_arguments_value,
                ),
                ("private_result", invocation_private_result_value),
            ):
                if value is not None and not isinstance(value, dict):
                    raise TypeError(f"{name} must be an object")
            proposal_arguments = (
                proposal_arguments_value
                if isinstance(proposal_arguments_value, dict)
                else None
            )
            normalized_arguments = (
                normalized_arguments_value
                if isinstance(normalized_arguments_value, dict)
                else None
            )
            invocation_private_result = (
                invocation_private_result_value
                if isinstance(invocation_private_result_value, dict)
                else None
            )
            invocation_outcome = invocation_value.get(
                "grounded_outcome",
                {},
            )
            if not isinstance(invocation_outcome, dict):
                raise TypeError(
                    "invocation grounded_outcome must be an object"
                )
            store.append_engagement_invocation(
                run_id=record.run_id,
                engagement_id=engagement_id,
                engagement_group_id=group_id,
                engagement_invocation_id=_required_string(
                    invocation_value.get("engagement_invocation_id"),
                    "engagement_invocation_id",
                ),
                record_id=record.record_id,
                ordinal=_optional_integer_value(
                    invocation_value.get("ordinal")
                ),
                capability=_optional_string(
                    invocation_value.get("capability")
                ),
                consequence_tier=_optional_integer_value(
                    invocation_value.get("consequence_tier")
                ),
                subject_id=_optional_string(
                    invocation_value.get("subject_id")
                ),
                target_id=_optional_string(
                    invocation_value.get("target_id")
                ),
                status=_required_string(
                    invocation_value.get("status"),
                    "status",
                ),
                private_proposal_arguments=proposal_arguments,
                private_normalized_arguments=normalized_arguments,
                private_result=invocation_private_result,
                grounded_outcome=invocation_outcome,
            )


def _engagement_invocation_payload(
    invocation: _EngagementInvocationProjection,
    include_private: bool,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "engagement_invocation_id": invocation.invocation_id,
        "ordinal": invocation.ordinal,
        "capability": invocation.capability,
        "consequence_tier": invocation.consequence_tier,
        "subject_id": invocation.subject_id,
        "status": invocation.status,
        "grounded_outcome": invocation.grounded_outcome,
    }
    if include_private:
        payload.update(
            {
                "target_id": invocation.target_id,
                "private_proposal_arguments": (
                    invocation.private_proposal_arguments
                ),
                "private_normalized_arguments": (
                    invocation.private_normalized_arguments
                ),
                "private_result": invocation.private_result,
            }
        )
    return payload


def _public_engagement_event_payload(
    event: DomainEvent,
) -> dict[str, JsonValue]:
    safe_names = (
        "engagement_id",
        "action_id",
        "plan_id",
        "plan_revision",
        "decision_id",
        "tool_call_id",
        "root_correlation_id",
        "group_id",
        "group_ordinal",
        "required_atomic",
        "invocation_id",
        "invocation_ordinal",
        "invocation_ids",
        "capability",
        "consequence_tier",
        "modality",
        "disclosure",
        "public_text",
        "expression_band",
        "activity",
        "duration_band",
        "duration_seconds",
        "effort_band",
        "mode",
        "sound_band",
        "sound_range",
        "group_count",
        "rejected_group_count",
        "completed_group_count",
        "failed_group_count",
        "group_statuses",
    )
    payload = {
        name: event.payload[name]
        for name in safe_names
        if name in event.payload
    }
    if event.event_type not in {
        "engagement.requested",
        "engagement.compilation_requested",
        "engagement.compilation_completed",
        "engagement.compilation_failed",
        "engagement.compilation_cancelled",
    }:
        reason = event.payload.get("reason")
        if isinstance(reason, str):
            payload["reason"] = reason
    payload["event_type"] = event.event_type
    return payload


def _engagement_proposal(
    model_turn: dict[str, JsonValue],
) -> dict[str, JsonValue] | None:
    tool_calls = model_turn.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        return None
    first = tool_calls[0]
    if not isinstance(first, dict):
        return None
    arguments = first.get("arguments")
    return dict(arguments) if isinstance(arguments, dict) else None


def _group_has_public_execution(
    group: _EngagementGroupProjection,
) -> bool:
    return group.validation_status == "valid" and (
        group.execution_status
        not in {"not_started", "pending"}
        or any(
            invocation.status == "committed"
            for invocation in group.invocations.values()
        )
    )


def _first_issue_code(issues: list[JsonValue]) -> str | None:
    for issue in issues:
        if isinstance(issue, dict):
            code = issue.get("code")
            if isinstance(code, str):
                return code
    return None


def _optional_string(value: JsonValue | object) -> str | None:
    return value if isinstance(value, str) else None


def _required_string(value: JsonValue | object, name: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise TypeError(f"{name} must be a string")
    return result


def _optional_string_tuple(
    value: JsonValue | object,
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        return None
    return tuple(value)


def _string_array_length(value: JsonValue | object) -> int:
    values = _optional_string_tuple(value)
    return len(values) if values is not None else 0


def _optional_integer_value(value: JsonValue | object) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool)
        else None
    )


def _required_integer_value(value: JsonValue | object, name: str) -> int:
    result = _optional_integer_value(value)
    if result is None:
        raise TypeError(f"{name} must be an integer")
    return result


def _optional_boolean_value(value: JsonValue | object) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_number_value(value: JsonValue | object) -> float | None:
    return (
        float(value)
        if isinstance(value, int | float) and not isinstance(value, bool)
        else None
    )


def _required_number_value(value: JsonValue | object, name: str) -> float:
    result = _optional_number_value(value)
    if result is None:
        raise TypeError(f"{name} must be numeric")
    return result


def _required_object(
    payload: dict[str, JsonValue],
    name: str,
) -> dict[str, JsonValue]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    return value


def _optional_object(
    payload: dict[str, JsonValue],
    name: str,
) -> dict[str, JsonValue] | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    return value


def _required_text(payload: dict[str, JsonValue], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _optional_text(
    payload: dict[str, JsonValue],
    name: str,
) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _required_int(payload: dict[str, JsonValue], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    return value


def _optional_int(
    payload: dict[str, JsonValue],
    name: str,
) -> int | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    return value


def _optional_float(
    payload: dict[str, JsonValue],
    name: str,
) -> float | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    return float(value)


def _required_bool(payload: dict[str, JsonValue], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _physical_related_ids(
    payload: dict[str, JsonValue],
) -> tuple[str, ...]:
    primary_id = next(
        (
            value
            for name in ("object_id", "character_id")
            if isinstance((value := payload.get(name)), str)
        ),
        None,
    )
    singular_names = {
        "parent_id",
        "custodian_id",
        "held_by_id",
        "owner_id",
        "support_id",
        "target_id",
        "destination_id",
        "left_object_id",
        "right_object_id",
    }
    plural_names = {
        "occupant_ids",
        "held_object_ids",
        "physically_held_object_ids",
        "physically_custodied_object_ids",
    }
    related: set[str] = set()

    def visit(value: JsonValue) -> None:
        if isinstance(value, dict):
            for name, child in value.items():
                if name in singular_names and isinstance(child, str):
                    related.add(child)
                elif name in plural_names and isinstance(child, list):
                    related.update(
                        item for item in child if isinstance(item, str)
                    )
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    if primary_id is not None:
        related.discard(primary_id)
    return tuple(sorted(related))


def _action_context(state: dict[str, JsonValue]) -> dict[str, JsonValue]:
    components = state.get("components")
    if not isinstance(components, dict):
        return {}
    selected = {
        name: value
        for name, value in components.items()
        if any(
            marker in name
            for marker in (
                "PlanComponent",
                "NavigationComponent",
                "TravelComponent",
                "PendingSpeechComponent",
                "AffordanceExecutionComponent",
                "TransactionRequestComponent",
                "TransactionExecutionComponent",
                "InteractionRequestComponent",
                "InteractionExecutionComponent",
                "PhysicalStateComponent",
                "SpatialParentRelationComponent",
                "CharacterPostureComponent",
                "CharacterHandStateComponent",
                "MovementComponent",
            )
        )
    }
    return {"components": selected} if selected else {}


def _find_text(value: JsonValue, name: str) -> str | None:
    if isinstance(value, dict):
        candidate = value.get(name)
        if isinstance(candidate, str):
            return candidate
        for key in sorted(value):
            found = _find_text(value[key], name)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_text(item, name)
            if found is not None:
                return found
    return None


def _share_place(registry: Registry, first: str, second: str) -> bool:
    if registry.has_component(first, SpatialLocationComponent) and (
        registry.has_component(second, SpatialLocationComponent)
    ):
        first_location = registry.get_component(
            first, SpatialLocationComponent
        ).location
        second_location = registry.get_component(
            second, SpatialLocationComponent
        ).location
        if (
            first_location.local_coordinate is not None
            or second_location.local_coordinate is not None
        ):
            return (
                first_location.local_coordinate is not None
                and second_location.local_coordinate is not None
                and first_location.place_id == second_location.place_id
            )
        return (
            first_location.place_id == second_location.place_id
            and first_location.network_node_id
            == second_location.network_node_id
            and first_location.edge_id == second_location.edge_id
        )
    return True


def _actor_kind(registry: Registry, entity_id: str) -> str:
    from stage0_sim.domain.components import (
        NpcComponent,
        PhysicalObjectIdentityComponent,
    )

    if entity_id not in registry.entities():
        return "external"
    if registry.has_component(entity_id, PhysicalObjectIdentityComponent):
        return "physical_object"
    return (
        "npc"
        if registry.has_component(entity_id, NpcComponent)
        else "character"
    )


def _entity_location(
    registry: Registry,
    entity_id: str,
) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    from stage0_sim.domain.components import PhysicalStateComponent

    if entity_id not in registry.entities():
        return result
    if registry.has_component(entity_id, PhysicalStateComponent):
        physical = registry.get_component(entity_id, PhysicalStateComponent)
        result["physical_pose"] = {
            "room_id": physical.pose.room_id,
            "anchor": physical.pose.anchor.to_payload(),
            "orientation": physical.pose.orientation.value,
        }
    if registry.has_component(entity_id, PositionComponent):
        result["position"] = serialize_authoritative(
            registry.get_component(entity_id, PositionComponent)
        )
    if registry.has_component(entity_id, SpatialLocationComponent):
        result["spatial_location"] = serialize_authoritative(
            registry.get_component(entity_id, SpatialLocationComponent)
        )
    return result


def _memory_ids(value: JsonValue | None) -> tuple[str, ...]:
    found: set[str] = set()

    def visit(item: JsonValue | None) -> None:
        if isinstance(item, dict):
            memory_id = item.get("memory_id")
            if isinstance(memory_id, str):
                found.add(memory_id)
            document_id = item.get("document_id")
            if (
                isinstance(document_id, str)
                and document_id.startswith("memory-")
            ):
                found.add(document_id)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return tuple(sorted(found))


def _trace_status(
    record_type: str,
    payload: dict[str, JsonValue],
) -> str:
    status = payload.get("status")
    if isinstance(status, str):
        return status
    if record_type.endswith("_request"):
        return "requested"
    if record_type.endswith("_error"):
        return "failed"
    return "completed"


def _event_reason(event: DomainEvent) -> str | None:
    for name in ("reason", "terminal_reason", "message"):
        value = event.payload.get(name)
        if isinstance(value, str):
            return value
    return None


def _decision_options(
    request: dict[str, JsonValue],
) -> list[dict[str, JsonValue]]:
    options: list[dict[str, JsonValue]] = []
    allowed_tools = request.get("allowed_tools")
    if isinstance(allowed_tools, list):
        for name in allowed_tools:
            if isinstance(name, str):
                options.append(
                    {
                        "option_id": f"tool:{name}",
                        "option_type": "tool",
                        "name": name,
                    }
                )
    observation = request.get("observation")
    if not isinstance(observation, dict):
        return options
    targets = observation.get("targets")
    if isinstance(targets, list):
        for target in targets:
            if not isinstance(target, dict):
                continue
            target_id = target.get("id")
            if not isinstance(target_id, str):
                continue
            options.append(
                {
                    "option_id": f"target:{target_id}",
                    "option_type": "target",
                    **target,
                }
            )
            offers = target.get("offers")
            if isinstance(offers, list):
                for offer in offers:
                    if not isinstance(offer, dict):
                        continue
                    offer_id = offer.get("id")
                    if isinstance(offer_id, str):
                        options.append(
                            {
                                "option_id": (
                                    f"offer:{target_id}:{offer_id}"
                                ),
                                "option_type": "offer",
                                "target_id": target_id,
                                **offer,
                            }
                        )
    possessions = observation.get("possessions")
    if isinstance(possessions, list):
        for possession in possessions:
            if not isinstance(possession, dict):
                continue
            item_id = possession.get("item_id")
            if isinstance(item_id, str):
                options.append(
                    {
                        "option_id": f"possession:{item_id}",
                        "option_type": "possession",
                        **possession,
                    }
                )
    service_requests = observation.get("service_requests")
    if isinstance(service_requests, list):
        for service_request in service_requests:
            if not isinstance(service_request, dict):
                continue
            request_id = service_request.get("request_id")
            if isinstance(request_id, str):
                options.append(
                    {
                        "option_id": f"service_request:{request_id}",
                        "option_type": "service_request",
                        **service_request,
                    }
                )
    modes = observation.get("available_travel_modes")
    if isinstance(modes, list):
        for mode in modes:
            if isinstance(mode, str):
                options.append(
                    {
                        "option_id": f"travel_mode:{mode}",
                        "option_type": "travel_mode",
                        "mode": mode,
                    }
                )
    return options
