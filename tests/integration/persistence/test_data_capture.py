import asyncio
import json
import sqlite3
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

import pytest

from stage0_sim.adapters.llm import ScriptedModelClient
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.agents.contracts import (
    ModelClient,
    ModelRequest,
    ModelToolCall,
    ModelTurn,
)
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.data_capture import (
    BufferedResearchRecorder,
    CaptureCoverageError,
    DatasetQueryFilter,
    DatasetRecord,
    DatasetRecordFilter,
    RecordCategory,
    RecordSource,
    RecordVisibility,
    ResearchTrace,
    ResearchWriteError,
    RunnerPhase,
    UnsupportedAuthoritativeValue,
    capture_coverage_manifest,
    capture_registry_state,
    character_physical_state,
    physical_object_states,
    physical_relation_samples,
    serialize_authoritative,
)
from stage0_sim.application.data_management import DatasetManagementService
from stage0_sim.application.runner import RunConfiguration, SimulationRunner
from stage0_sim.application.scenario import (
    ScenarioDefinition,
    create_runner,
    load_scenario,
)
from stage0_sim.application.telemetry import TelemetryBroker
from stage0_sim.domain.components import (
    ActionInstance,
    ActionOrigin,
    CardinalOrientation,
    CharacterHandStateComponent,
    CharacterPosture,
    CharacterPostureComponent,
    ContainerComponent,
    CustodyComponent,
    Footprint,
    InteractionExecutionComponent,
    InteractionRequestComponent,
    MovementComponent,
    NavigationComponent,
    NavigationPrimitive,
    NavigationPrimitiveKind,
    NavigationStatus,
    OccupancySlot,
    OccupancySlotsComponent,
    OpenableComponent,
    OwnershipComponent,
    PerceptionComponent,
    PhysicalInteractionRegistry,
    PhysicalInteractionTarget,
    PhysicalObjectIdentityComponent,
    PhysicalRelationKind,
    PhysicalStateComponent,
    PortableComponent,
    PositionComponent,
    PossessionsComponent,
    SpatialIndex,
    SpatialIndexEntry,
    SpatialParentRelationComponent,
    SupportComponent,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.interactions import (
    InteractionSpecification,
    InteractionVerb,
)
from stage0_sim.domain.lineage import action_lineage_payload
from stage0_sim.domain.systems import SystemContext, SystemExecutor
from stage0_sim.domain.world import (
    Coordinate,
    Locator,
    MovementObstruction,
    PhysicalPose,
    VisionObstruction,
)
from tests.helpers.paths import CATALOG_SCENARIOS


class ExampleKind(StrEnum):
    FIRST = "first"
    SECOND = "second"


@dataclass(frozen=True, slots=True)
class ExampleComponent:
    name: str
    kind: ExampleKind
    values: dict[str, tuple[int, ...]]
    labels: frozenset[str]


@dataclass(frozen=True, slots=True)
class UnsupportedComponent:
    value: object


@dataclass(slots=True)
class TraceSystem:
    trace: list[str]
    name: str = "trace"
    order: int = 10

    def update(self, _context: SystemContext) -> None:
        self.trace.append("system")


class _FailingResearchSink:
    def write(self, _trace: ResearchTrace) -> None:
        raise OSError("capture unavailable")


class _SlowModelClient(ModelClient):
    synchronous = False

    async def complete(self, _request: ModelRequest) -> ModelTurn:
        await asyncio.sleep(0.05)
        return _model_turn("skip", {"reconsider_after_seconds": 30})


def scenario_path(name: str) -> Path:
    return CATALOG_SCENARIOS / name


def _model_turn(name: str, arguments: dict[str, JsonValue]) -> ModelTurn:
    return ModelTurn(
        text=None,
        tool_calls=(
            ModelToolCall(
                call_id=f"call-{name}",
                name=name,
                arguments=arguments,
            ),
        ),
        finish_reason="tool_calls",
        provider="scripted",
        model="scripted-v1",
        latency_ms=2,
        input_tokens=11,
        output_tokens=3,
        provider_request_id=f"provider-{name}",
    )


def _capture_tool_scenario(
    *,
    timeout_seconds: float = 30,
) -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        {
            "name": "private-capture",
            "cognition": {
                "max_requests": 1,
                "decision_timeout_seconds": timeout_seconds,
                "max_read_tool_rounds": 1,
            },
            "world": {
                "width": 2,
                "height": 1,
                "zones": [
                    {
                        "id": "office",
                        "name": "Office",
                        "type": "OFFICE",
                        "tiles": [{"x": 0, "y": 0}],
                    },
                    {
                        "id": "lounge",
                        "name": "Lounge",
                        "type": "LOUNGE",
                        "tiles": [{"x": 1, "y": 0}],
                    },
                ],
            },
            "entities": [
                {
                    "id": "alex",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {},
                        "character_slot": {
                            "label": "Researcher",
                            "briefing": "Private briefing.",
                        },
                        "goals": {
                            "goals": [
                                {
                                    "id": "visit-lounge",
                                    "description": "Visit the lounge",
                                    "priority": 50,
                                },
                                {
                                    "id": "choose-route",
                                    "description": "Choose a route",
                                    "priority": 100,
                                },
                            ],
                        },
                        "controller": {"enabled": True},
                    },
                }
            ],
        }
    )


def _physical_capture_runner(run_id: str) -> SimulationRunner:
    registry = Registry()
    actor_id = "character-alex"
    cabinet_id = "cabinet-private"
    chair_id = "chair-one"
    held_id = "mug-held"
    hidden_id = "note-hidden"
    for entity_id in (
        actor_id,
        cabinet_id,
        chair_id,
        held_id,
        hidden_id,
    ):
        registry.create_entity(entity_id)

    unit = Footprint(frozenset({Coordinate(0, 0)}))
    actor_state = PhysicalStateComponent(
        PhysicalPose("room-a", Coordinate(2, 2)),
        unit,
    )
    cabinet_state = PhysicalStateComponent(
        PhysicalPose(
            "room-a",
            Coordinate(10, 10),
            CardinalOrientation.EAST,
        ),
        unit,
        MovementObstruction.HARD,
        VisionObstruction.OPAQUE,
    )
    chair_state = PhysicalStateComponent(
        PhysicalPose("room-a", Coordinate(15, 15)),
        unit,
        MovementObstruction.HARD,
    )
    held_state = PhysicalStateComponent(
        PhysicalPose("room-a", Coordinate(2, 2)),
        unit,
    )
    hidden_state = PhysicalStateComponent(
        PhysicalPose("room-a", Coordinate(10, 10)),
        unit,
    )
    action = ActionInstance(
        action_id="action-interact-1",
        origin=ActionOrigin.CONTROLLER,
        created_tick=0,
        created_at=0,
        root_correlation_id="decision-physical-1",
        action_name="INTERACT",
        target_id=cabinet_id,
        plan_id="plan-physical-1",
        plan_revision=1,
        decision_id="decision-physical-1",
        tool_call_id="tool-physical-1",
    )
    specification = InteractionSpecification(
        InteractionVerb.OPEN,
        cabinet_id,
    )
    registry.add_component(actor_id, PositionComponent(Coordinate(2, 2)))
    registry.add_component(actor_id, actor_state)
    registry.add_component(
        actor_id,
        SpatialParentRelationComponent(
            chair_id,
            PhysicalRelationKind.OCCUPIES_SLOT,
            "seat",
        ),
    )
    registry.add_component(
        actor_id,
        CharacterPostureComponent(CharacterPosture.SITTING, chair_id),
    )
    registry.add_component(
        actor_id,
        CharacterHandStateComponent(left_hand_object_id=held_id),
    )
    registry.add_component(
        actor_id,
        PossessionsComponent({"coffee": 2, "credits": 5}),
    )
    registry.add_component(
        actor_id,
        MovementComponent(
            destination=Coordinate(3, 4),
            path=(
                Coordinate(2, 2),
                Coordinate(3, 2),
                Coordinate(4, 2),
                Coordinate(4, 3),
                Coordinate(4, 4),
            ),
            retry_after_tick=4,
            path_correlation_id="path-physical-1",
            action_instance=action,
            planned_spatial_revision=3,
        ),
    )
    registry.add_component(
        actor_id,
        NavigationComponent(
            target_id=cabinet_id,
            primitives=(
                NavigationPrimitive(
                    NavigationPrimitiveKind.MOVE,
                    Locator("room-a", {"x": 2, "y": 2}),
                    Locator("room-a", {"x": 4, "y": 4}),
                    0,
                    1,
                ),
            ),
            current_primitive_index=0,
            status=NavigationStatus.NAVIGATING,
            action_instance=action,
        ),
    )
    registry.add_component(
        actor_id,
        InteractionRequestComponent(
            specification,
            "tool",
            status="running",
            action_instance=action,
        ),
    )
    registry.add_component(
        actor_id,
        InteractionExecutionComponent(
            specification,
            "tool",
            elapsed=0.25,
            duration=1,
            correlation_id="decision-physical-1",
            action_instance=action,
        ),
    )

    registry.add_component(
        cabinet_id,
        PhysicalObjectIdentityComponent(
            "definition-cabinet",
            "Private cabinet",
        ),
    )
    registry.add_component(cabinet_id, cabinet_state)
    registry.add_component(
        cabinet_id,
        SpatialParentRelationComponent(
            "room-a",
            PhysicalRelationKind.ON_FLOOR,
        ),
    )
    registry.add_component(
        cabinet_id,
        OccupancySlotsComponent(
            (
                OccupancySlot(
                    "inside",
                    frozenset({PhysicalRelationKind.IN_CONTAINER}),
                    2,
                ),
                OccupancySlot(
                    "top",
                    frozenset({PhysicalRelationKind.ON_SUPPORT}),
                    2,
                ),
            )
        ),
    )
    registry.add_component(cabinet_id, ContainerComponent(("inside",)))
    registry.add_component(cabinet_id, SupportComponent(("top",)))
    registry.add_component(
        cabinet_id,
        OpenableComponent(is_open=False, is_locked=True),
    )

    registry.add_component(
        chair_id,
        PhysicalObjectIdentityComponent("definition-chair", "Chair"),
    )
    registry.add_component(chair_id, chair_state)
    registry.add_component(
        chair_id,
        SpatialParentRelationComponent(
            "room-a",
            PhysicalRelationKind.ON_FLOOR,
        ),
    )
    registry.add_component(
        chair_id,
        OccupancySlotsComponent(
            (
                OccupancySlot(
                    "seat",
                    frozenset({PhysicalRelationKind.OCCUPIES_SLOT}),
                ),
            )
        ),
    )

    registry.add_component(
        held_id,
        PhysicalObjectIdentityComponent("definition-mug", "Held mug"),
    )
    registry.add_component(held_id, held_state)
    registry.add_component(held_id, PortableComponent())
    registry.add_component(
        held_id,
        SpatialParentRelationComponent(
            actor_id,
            PhysicalRelationKind.HELD_BY,
            "left",
        ),
    )
    registry.add_component(held_id, CustodyComponent(actor_id))

    registry.add_component(
        hidden_id,
        PhysicalObjectIdentityComponent(
            "definition-note",
            "TOP SECRET HIDDEN NOTE",
        ),
    )
    registry.add_component(hidden_id, hidden_state)
    registry.add_component(hidden_id, PortableComponent())
    registry.add_component(
        hidden_id,
        SpatialParentRelationComponent(
            cabinet_id,
            PhysicalRelationKind.IN_CONTAINER,
            "inside",
        ),
    )
    registry.add_component(hidden_id, OwnershipComponent(actor_id))

    spatial_index = SpatialIndex()
    spatial_index.add(SpatialIndexEntry(actor_id, actor_state, dynamic=True))
    spatial_index.add(SpatialIndexEntry(cabinet_id, cabinet_state))
    spatial_index.add(SpatialIndexEntry(chair_id, chair_state))
    registry.set_resource(spatial_index)
    registry.set_resource(
        PhysicalInteractionRegistry(
            targets={
                cabinet_id: PhysicalInteractionTarget(
                    cabinet_id,
                    "room-a",
                    (Coordinate(9, 10),),
                )
            },
            transition_doors={},
        )
    )
    return SimulationRunner(
        RunConfiguration(seed=7, run_id=run_id),
        registry=registry,
    )


def test_runner_phase_hooks_wrap_systems_and_preserve_tick_completed() -> None:
    trace: list[str] = []
    systems = SystemExecutor()
    systems.add(TraceSystem(trace))
    runner = SimulationRunner(
        RunConfiguration(seed=7, run_id="phase-hooks"),
        systems=systems,
    )
    phases: list[tuple[RunnerPhase, int]] = []
    runner.subscribe_phase(
        lambda phase, observed_runner, _context: (
            phases.append((phase, observed_runner.clock.tick)),
            trace.append(phase.value),
        )
    )
    runner.subscribe_tick_completed(lambda _event: trace.append("tick_completed"))

    runner.run_for(1)
    runner.stop()

    assert phases == [
        (RunnerPhase.RUN_INITIAL, 0),
        (RunnerPhase.TICK_PRE_SYSTEMS, 1),
        (RunnerPhase.TICK_POST_SYSTEMS, 1),
        (RunnerPhase.TICK_POST_COGNITION, 1),
        (RunnerPhase.RUN_FINAL, 1),
    ]
    assert trace == [
        "run_initial",
        "tick_pre_systems",
        "system",
        "tick_post_systems",
        "tick_post_cognition",
        "tick_completed",
        "run_final",
    ]


def test_full_state_serialization_is_deterministic() -> None:
    registry = Registry()
    registry.create_entity("character")
    registry.add_component(
        "character",
        ExampleComponent(
            name="example",
            kind=ExampleKind.SECOND,
            values={"z": (3, 2), "a": (1,)},
            labels=frozenset({"beta", "alpha"}),
        ),
    )

    first = capture_registry_state(registry)
    second = capture_registry_state(registry)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    component = first["entities"][0]["components"][
        f"{__name__}.ExampleComponent"
    ]
    assert component == {
        "name": "example",
        "kind": "second",
        "values": {"a": [1], "z": [3, 2]},
        "labels": ["alpha", "beta"],
    }
    assert serialize_authoritative({ExampleKind.FIRST: {3, 1}}) == {
        "first": [1, 3]
    }


def test_physical_state_helpers_are_deterministic_compact_and_hybrid() -> None:
    runner = _physical_capture_runner("physical-helper")
    registry = runner.registry

    first_objects = physical_object_states(registry)
    second_objects = physical_object_states(registry)
    relations = physical_relation_samples(registry)
    character = character_physical_state(registry, "character-alex")
    authoritative = capture_registry_state(registry)

    assert first_objects == second_objects
    assert [state["object_id"] for state in first_objects] == sorted(
        state["object_id"] for state in first_objects
    )
    cabinet = next(
        state
        for state in first_objects
        if state["object_id"] == "cabinet-private"
    )
    assert cabinet["pose"] == {
        "room_id": "room-a",
        "anchor": {"x": 10, "y": 10},
        "orientation": "EAST",
    }
    assert cabinet["openable"] == {
        "is_open": False,
        "is_locked": True,
        "closed_movement_obstruction": "HARD",
        "closed_vision_obstruction": "OPAQUE",
        "closed_hearing_transmission": "PASS",
        "closed_smell_transmission": "PASS",
    }
    assert cabinet["slots"][0]["occupant_ids"] == ["note-hidden"]
    assert cabinet["spatial_index"]["revision"] == 2
    assert cabinet["spatial_index"]["topology_revision"] == 2
    hidden = next(
        state
        for state in first_objects
        if state["object_id"] == "note-hidden"
    )
    assert hidden["ownership"] == {"owner_id": "character-alex"}
    assert hidden["parent_relation"] == {
        "parent_id": "cabinet-private",
        "relation_kind": "IN_CONTAINER",
        "slot_id": "inside",
    }
    assert {
        (
            relation["object_id"],
            relation["parent_id"],
            relation["relation_kind"],
        )
        for relation in relations
    } >= {
        ("character-alex", "chair-one", "OCCUPIES_SLOT"),
        ("mug-held", "character-alex", "HELD_BY"),
        ("note-hidden", "cabinet-private", "IN_CONTAINER"),
    }
    assert character is not None
    assert character["posture"] == {
        "value": "SITTING",
        "support_id": "chair-one",
    }
    assert character["hands"]["held_object_ids"] == ["mug-held"]
    assert character["interaction"]["request"]["verb"] == "OPEN"
    assert character["interaction"]["execution"]["action_lineage"][
        "tool_call_id"
    ] == "tool-physical-1"
    assert character["movement"]["remaining_path"] == {
        "encoding": "delta_segments.v1",
        "point_count": 5,
        "start": {"x": 2, "y": 2},
        "segments": [
            {
                "start": {"x": 2, "y": 2},
                "end": {"x": 4, "y": 2},
                "delta": {"x": 1, "y": 0},
                "steps": 2,
            },
            {
                "start": {"x": 4, "y": 2},
                "end": {"x": 4, "y": 4},
                "delta": {"x": 0, "y": 1},
                "steps": 2,
            },
        ],
    }
    assert len(character["navigation"]["primitives"]) == 1
    assert character["hybrid_possession"] == {
        "abstract_holdings": {"coffee": 2, "credits": 5},
        "physically_held_object_ids": ["mug-held"],
        "physically_custodied_object_ids": ["mug-held"],
        "representations_are_independent": True,
    }
    actor_components = next(
        entity["components"]
        for entity in authoritative["entities"]
        if entity["entity_id"] == "character-alex"
    )
    movement = actor_components[
        "stage0_sim.domain.components.spatial.MovementComponent"
    ]
    assert "path" not in movement
    assert movement["remaining_path"]["segments"] == character["movement"][
        "remaining_path"
    ]["segments"]
    spatial_resource = authoritative["resources"][
        "stage0_sim.domain.world.physical.SpatialIndex"
    ]
    assert spatial_resource["revision"] == 2
    assert spatial_resource["topology_revision"] == 2


def test_physical_capture_interactions_privacy_and_rebuild(
    tmp_path: Path,
) -> None:
    database = tmp_path / "physical-capture.sqlite3"
    runner = _physical_capture_runner("physical-capture")
    store = SQLiteDatasetStore(database)
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario={"name": "physical-capture", "schema_version": 8},
    )
    runner.start()
    registry = runner.registry
    cabinet = registry.get_component(
        "cabinet-private", PhysicalStateComponent
    )
    openable = registry.get_component(
        "cabinet-private", OpenableComponent
    )
    openable.is_locked = False
    openable.is_open = True
    opened = replace(
        cabinet,
        movement_obstruction=MovementObstruction.NONE,
        vision_obstruction=VisionObstruction.TRANSPARENT,
    )
    registry.get_resource(SpatialIndex).update(
        SpatialIndexEntry("cabinet-private", opened)
    )
    registry.set_component("cabinet-private", opened)
    registry.set_component(
        "note-hidden",
        SpatialParentRelationComponent(
            "cabinet-private",
            PhysicalRelationKind.ON_SUPPORT,
            "top",
        ),
    )

    action = registry.get_component(
        "character-alex", InteractionRequestComponent
    ).action_instance
    assert action is not None
    lineage = action_lineage_payload(action)
    action_payload: dict[str, JsonValue] = {
        "action": "INTERACT",
        "interaction": {
            "verb": "OPEN",
            "target_id": "cabinet-private",
            "destination_id": None,
            "slot_id": None,
        },
        **lineage,
    }
    queued = runner.events.emit(
        "action.queued",
        simulation_tick=0,
        simulation_time=0,
        agent_id="character-alex",
        payload=action_payload,
        correlation_id=action.root_correlation_id,
    )
    runner.events.emit(
        "tool.committed",
        simulation_tick=0,
        simulation_time=0,
        agent_id="character-alex",
        payload={
            "decision_id": "decision-physical-1",
            "tool_call_id": "tool-physical-1",
            "tool_name": "interact_with",
            "action_id": "action-interact-1",
            "arguments": {
                "verb": "OPEN",
                "target_id": "cabinet-private",
            },
        },
        correlation_id=action.root_correlation_id,
    )
    started_action = runner.events.emit(
        "action.started",
        simulation_tick=0,
        simulation_time=0,
        agent_id="character-alex",
        payload=action_payload,
        causation_id=queued.event_id,
        correlation_id=action.root_correlation_id,
    )
    requested = runner.events.emit(
        "interaction.requested",
        simulation_tick=0,
        simulation_time=0,
        agent_id="character-alex",
        payload={
            "verb": "OPEN",
            "target_id": "cabinet-private",
            "destination_id": None,
            "slot_id": None,
            "source": "tool",
            **lineage,
        },
        causation_id=started_action.event_id,
        correlation_id=action.root_correlation_id,
    )
    started = runner.events.emit(
        "interaction.started",
        simulation_tick=0,
        simulation_time=0,
        agent_id="character-alex",
        payload={
            "verb": "OPEN",
            "target_id": "cabinet-private",
            "destination_id": None,
            "slot_id": None,
            "source": "tool",
            "duration": 1.0,
            **lineage,
        },
        causation_id=requested.event_id,
        correlation_id=action.root_correlation_id,
    )
    runner.events.emit(
        "interaction.completed",
        simulation_tick=1,
        simulation_time=1,
        agent_id="character-alex",
        payload={
            "verb": "OPEN",
            "target_id": "cabinet-private",
            "destination_id": None,
            "slot_id": None,
            "source": "tool",
            "is_open": True,
            **lineage,
        },
        causation_id=started.event_id,
        correlation_id=action.root_correlation_id,
    )
    runner.events.emit(
        "action.completed",
        simulation_tick=1,
        simulation_time=1,
        agent_id="character-alex",
        payload=action_payload,
        causation_id=started_action.event_id,
        correlation_id=action.root_correlation_id,
    )
    runner.events.emit(
        "information.retrieved",
        simulation_tick=1,
        simulation_time=1,
        agent_id="character-alex",
        payload={
            "visibility": "private",
            "capsules": [{"capsule_text": "PRIVATE RETRIEVAL MARKER"}],
        },
    )
    private_action_payload: dict[str, JsonValue] = {
        "action": "WAIT",
        "duration": 1.0,
        "action_id": "private-action",
        "action_origin": "operator",
        "plan_id": None,
        "plan_revision": None,
        "goal_ids": [],
        "goal_links": [],
        "decision_id": None,
        "tool_call_id": None,
        "action_created_tick": 1,
        "action_created_at": 1.0,
        "root_correlation_id": "private-action",
        "visibility": "private",
    }
    private_queued = runner.events.emit(
        "action.queued",
        simulation_tick=1,
        simulation_time=1,
        agent_id="character-alex",
        payload=private_action_payload,
        correlation_id="private-action",
    )
    runner.events.emit(
        "action.completed",
        simulation_tick=1,
        simulation_time=1,
        agent_id="character-alex",
        payload=private_action_payload,
        causation_id=private_queued.event_id,
        correlation_id="private-action",
    )
    runner.run_for(1)
    collector.finalize()

    public_records = "\n".join(
        store.iter_records_ndjson("physical-capture")
    )
    private_records = "\n".join(
        store.iter_records_ndjson(
            "physical-capture",
            DatasetRecordFilter(include_private=True),
        )
    )
    public_physical = store.query_table(
        "physical-capture",
        "physical_object_states",
    )
    private_physical = store.query_table(
        "physical-capture",
        "physical_object_states",
        DatasetQueryFilter(
            object_id="cabinet-private",
            room_id="room-a",
            is_open=True,
            is_locked=False,
            include_private=True,
        ),
    )
    private_relations = store.query_table(
        "physical-capture",
        "physical_relation_samples",
        DatasetQueryFilter(
            parent_id="cabinet-private",
            relation_kind="ON_SUPPORT",
            include_private=True,
        ),
    )
    physical_interactions = store.query_table(
        "physical-capture",
        "interactions",
        DatasetQueryFilter(
            interaction_type="physical_object",
            interaction_verb="OPEN",
            primary_entity_id="character-alex",
            status="completed",
            include_private=True,
        ),
    )
    public_actions = store.query_table(
        "physical-capture",
        "action_instances",
    )
    private_actions = store.query_table(
        "physical-capture",
        "action_instances",
        DatasetQueryFilter(include_private=True),
    )
    public_events, _ = store.persisted_events("physical-capture")
    private_events, _ = store.persisted_events(
        "physical-capture",
        include_private=True,
    )
    summary = store.summary("physical-capture", include_private=True)
    aggregate_service = DatasetManagementService(store)
    private_aggregate = aggregate_service.aggregate(
        aggregate_service.selection(["physical-capture"]),
        include_private_derived=True,
    )

    assert public_physical.rows == ()
    assert "TOP SECRET HIDDEN NOTE" not in public_records
    assert "PRIVATE RETRIEVAL MARKER" not in public_records
    assert "TOP SECRET HIDDEN NOTE" in private_records
    assert "PRIVATE RETRIEVAL MARKER" in private_records
    assert private_physical.rows
    assert private_physical.rows[0]["spatial_index_revision"] == 3
    assert private_physical.rows[0]["topology_revision"] == 3
    assert private_relations.rows
    assert len(physical_interactions.rows) == 1
    assert [row["action_id"] for row in public_actions.rows] == [
        "action-interact-1"
    ]
    assert [row["action_id"] for row in private_actions.rows] == [
        "action-interact-1",
        "private-action",
    ]
    assert all(
        event["event_type"] != "information.retrieved"
        for event in public_events
    )
    assert any(
        event["event_type"] == "information.retrieved"
        for event in private_events
    )
    assert summary["physical"]["distinct_object_count"] == 4
    assert summary["physical"]["state_sample_count"] == 20
    assert summary["physical"]["relation_sample_count"] == 25
    assert private_aggregate.distributions["physical_interactions.verb"] == {
        "OPEN": 1
    }
    assert private_aggregate.distributions[
        "physical_interactions.terminal_status"
    ] == {"completed": 1}

    connection = sqlite3.connect(database)
    interaction = connection.execute(
        """
        SELECT interaction_type, interaction_verb, actor_id, target_id,
               destination_id, slot_id, status, action_id, decision_id,
               tool_call_id, correlation_id
        FROM interactions WHERE run_id = ?
        """,
        ("physical-capture",),
    ).fetchone()
    episode = connection.execute(
        """
        SELECT interaction_verb, status, initiating_action_id,
               initiating_decision_id, initiating_tool_call_id,
               correlation_id
        FROM interaction_episodes WHERE run_id = ?
        """,
        ("physical-capture",),
    ).fetchone()
    action_row = connection.execute(
        """
        SELECT action_type, status, tool_call_id
        FROM action_instances
        WHERE run_id = ? AND action_id = 'action-interact-1'
        """,
        ("physical-capture",),
    ).fetchone()
    tool_row = connection.execute(
        """
        SELECT tool_name, status FROM tool_executions
        WHERE run_id = ? AND tool_call_id = 'tool-physical-1'
        """,
        ("physical-capture",),
    ).fetchone()
    state_deltas = [
        json.loads(row[0])
        for row in connection.execute(
            """
            SELECT delta_json FROM state_deltas
            WHERE run_id = ? ORDER BY state_delta_id
            """,
            ("physical-capture",),
        )
    ]
    assert connection.execute(
        """
        SELECT COUNT(*) FROM records
        WHERE run_id = ?
          AND visibility != 'PRIVATE_RESEARCH'
          AND payload_json LIKE '%PRIVATE RETRIEVAL MARKER%'
        """,
        ("physical-capture",),
    ).fetchone()[0] == 0
    before_states = connection.execute(
        """
        SELECT physical_state_id, state_json
        FROM physical_object_states
        WHERE run_id = ? ORDER BY physical_state_id
        """,
        ("physical-capture",),
    ).fetchall()
    before_relations = connection.execute(
        """
        SELECT relation_sample_id, relation_json
        FROM physical_relation_samples
        WHERE run_id = ? ORDER BY relation_sample_id
        """,
        ("physical-capture",),
    ).fetchall()
    connection.close()

    assert interaction == (
        "physical_object",
        "OPEN",
        "character-alex",
        "cabinet-private",
        None,
        None,
        "completed",
        "action-interact-1",
        "decision-physical-1",
        "tool-physical-1",
        "decision-physical-1",
    )
    assert episode == (
        "OPEN",
        "completed",
        "action-interact-1",
        "decision-physical-1",
        "tool-physical-1",
        "decision-physical-1",
    )
    assert action_row == ("INTERACT", "completed", "tool-physical-1")
    assert tool_row == ("interact_with", "committed")
    changed_paths = {
        str(change["path"])
        for delta in state_deltas
        for change in delta["changed_fields"]
    }
    assert any("PhysicalStateComponent" in path for path in changed_paths)
    assert any(
        "SpatialParentRelationComponent" in path
        for path in changed_paths
    )

    first_rebuild = store.rebuild_run_projections("physical-capture")
    second_rebuild = store.rebuild_run_projections("physical-capture")
    assert first_rebuild == second_rebuild
    assert first_rebuild["derived_feature_counts"][
        "physical_object_states"
    ] == 20
    assert first_rebuild["derived_feature_counts"][
        "physical_relation_samples"
    ] == 25
    connection = sqlite3.connect(database)
    assert connection.execute(
        """
        SELECT physical_state_id, state_json
        FROM physical_object_states
        WHERE run_id = ? ORDER BY physical_state_id
        """,
        ("physical-capture",),
    ).fetchall() == before_states
    assert connection.execute(
        """
        SELECT relation_sample_id, relation_json
        FROM physical_relation_samples
        WHERE run_id = ? ORDER BY relation_sample_id
        """,
        ("physical-capture",),
    ).fetchall() == before_relations
    assert connection.execute(
        """
        SELECT interaction_type, interaction_verb, actor_id, target_id,
               status, action_id, decision_id, tool_call_id, correlation_id
        FROM interactions WHERE run_id = ?
        """,
        ("physical-capture",),
    ).fetchone() == (
        "physical_object",
        "OPEN",
        "character-alex",
        "cabinet-private",
        "completed",
        "action-interact-1",
        "decision-physical-1",
        "tool-physical-1",
        "decision-physical-1",
    )
    connection.execute(
        """
        CREATE TRIGGER reject_physical_projection
        BEFORE INSERT ON physical_object_states
        BEGIN
            SELECT RAISE(ABORT, 'injected physical rebuild failure');
        END
        """
    )
    connection.commit()
    connection.close()
    with pytest.raises(
        RuntimeError,
        match="injected physical rebuild failure",
    ):
        store.rebuild_run_projections("physical-capture")
    connection = sqlite3.connect(database)
    assert connection.execute(
        """
        SELECT physical_state_id, state_json
        FROM physical_object_states
        WHERE run_id = ? ORDER BY physical_state_id
        """,
        ("physical-capture",),
    ).fetchall() == before_states
    connection.close()
    store.close()


def test_unsupported_state_and_unclassified_resources_fail_explicitly() -> None:
    registry = Registry()
    registry.create_entity("character")
    registry.add_component(
        "character",
        UnsupportedComponent(object()),
    )

    with pytest.raises(
        UnsupportedAuthoritativeValue,
        match="UnsupportedComponent.value",
    ):
        capture_registry_state(registry)

    class UnknownResource:
        pass

    registry = Registry()
    registry.set_resource(UnknownResource())
    with pytest.raises(CaptureCoverageError, match="UnknownResource"):
        capture_coverage_manifest(registry)


def test_coverage_manifest_classifies_operational_resources() -> None:
    scenario = load_scenario(scenario_path("baseline.json"))
    runner = create_runner(scenario, run_id="coverage")

    snapshot = capture_registry_state(runner.registry)
    resources = snapshot["coverage"]["resources"]
    exclusions = {
        entry["type"]
        for entry in resources
        if entry["classification"] == "operational_exclusion"
    }

    assert (
        "stage0_sim.application.memory_recording.MemoryWorkCoordinator"
        in exclusions
    )
    assert (
        "stage0_sim.application.memory_recording.MemoryWorkCoordinator"
        not in snapshot["resources"]
    )
    runner.stop()


def test_collector_persists_phase_deltas_opportunities_and_population(
    tmp_path: Path,
) -> None:
    database = tmp_path / "capture.sqlite3"
    scenario = load_scenario(scenario_path("needs-and-preemption.json"))
    runner = create_runner(scenario, run_id="capture")
    store = SQLiteDatasetStore(database)
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario=scenario.model_dump(mode="json"),
    )

    runner.run_for(2)
    collector.finalize()
    store.close()

    connection = sqlite3.connect(database)
    phase_rows = connection.execute(
        """
        SELECT phase FROM records
        WHERE run_id = ? AND record_type = 'phase_state'
        ORDER BY sequence
        """,
        ("capture",),
    ).fetchall()
    phases = [row[0] for row in phase_rows]
    assert phases == [
        "run_initial",
        "tick_pre_systems",
        "tick_post_systems",
        "tick_post_cognition",
        "tick_pre_systems",
        "tick_post_systems",
        "tick_post_cognition",
        "run_final",
    ]
    assert connection.execute(
        "SELECT COUNT(*) FROM state_samples WHERE run_id = ?",
        ("capture",),
    ).fetchone()[0] > 0
    delta_json = connection.execute(
        """
        SELECT delta_json FROM state_deltas
        WHERE run_id = ? ORDER BY rowid LIMIT 1
        """,
        ("capture",),
    ).fetchone()[0]
    assert json.loads(delta_json)["change_count"] > 0
    assert connection.execute(
        "SELECT COUNT(*) FROM opportunity_samples WHERE run_id = ?",
        ("capture",),
    ).fetchone()[0] > 0
    opportunity_payload = json.loads(
        connection.execute(
            """
            SELECT payload_json FROM records
            WHERE run_id = ? AND record_type = 'opportunity_sample'
            ORDER BY sequence LIMIT 1
            """,
            ("capture",),
        ).fetchone()[0]
    )
    assert opportunity_payload["choice_status"] == "non_choice"
    assert all(
        option["selected"] is False
        for option in opportunity_payload["options"]
    )
    population_json = connection.execute(
        """
        SELECT population_json FROM population_samples
        WHERE run_id = ? AND phase = 'run_initial'
        """,
        ("capture",),
    ).fetchone()[0]
    assert json.loads(population_json)["entity_count"] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM resource_samples WHERE run_id = ?",
        ("capture",),
    ).fetchone()[0] > 0
    transition = connection.execute(
        """
        SELECT state_before_json, state_after_json, elapsed_simulation_time
        FROM transition_samples WHERE run_id = ?
        ORDER BY start_tick, end_tick LIMIT 1
        """,
        ("capture",),
    ).fetchone()
    assert isinstance(json.loads(transition[0]), dict)
    assert isinstance(json.loads(transition[1]), dict)
    assert transition[2] >= 0
    connection.close()


def test_interaction_perception_and_rebuild_projections(
    tmp_path: Path,
) -> None:
    database = tmp_path / "interaction-projections.sqlite3"
    registry = Registry()
    for entity_id, coordinate in (
        ("speaker", Coordinate(0, 0)),
        ("listener", Coordinate(1, 0)),
    ):
        registry.create_entity(entity_id)
        registry.add_component(entity_id, PositionComponent(coordinate))
    registry.add_component(
        "speaker",
        PerceptionComponent(visible_now={"listener"}),
    )
    runner = SimulationRunner(
        RunConfiguration(seed=3, run_id="interaction-projections"),
        registry=registry,
    )
    store = SQLiteDatasetStore(database)
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario={"name": "interaction-projections"},
    )

    runner.run_for(1)
    speech = runner.events.emit(
        "speech.started",
        simulation_tick=runner.clock.tick,
        simulation_time=runner.clock.simulation_time,
        agent_id="speaker",
        payload={
            "target_id": "listener",
            "text": "Hello",
            "channel": "voice",
            "decision_id": "decision-speech",
            "action_id": "action-speech",
            "tool_call_id": "tool-speech",
        },
        correlation_id="decision-speech",
    )
    runner.events.emit(
        "speech.delivered",
        simulation_tick=runner.clock.tick,
        simulation_time=runner.clock.simulation_time,
        agent_id="speaker",
        payload={
            "target_id": "listener",
            "recipient_ids": ["listener"],
            "text": "Hello",
            "channel": "voice",
        },
        causation_id=speech.event_id,
        correlation_id="decision-speech",
    )
    runner.events.emit(
        "transaction.requested",
        simulation_tick=runner.clock.tick,
        simulation_time=runner.clock.simulation_time,
        agent_id="listener",
        payload={
            "request_id": "request-1",
            "point_id": "counter",
            "offer_id": "coffee",
        },
    )
    runner.events.emit(
        "transaction.completed",
        simulation_tick=runner.clock.tick,
        simulation_time=runner.clock.simulation_time,
        agent_id="listener",
        payload={
            "request_id": "request-1",
            "point_id": "counter",
            "offer_id": "coffee",
            "operator_id": "speaker",
        },
    )
    runner.events.emit(
        "transaction.failed",
        simulation_tick=runner.clock.tick,
        simulation_time=runner.clock.simulation_time,
        agent_id="listener",
        payload={
            "point_id": "counter",
            "offer_id": "coffee",
            "reason": "transaction_point_at_capacity",
        },
    )
    fact = {
        "fact_id": "fact-manual",
        "event_id": speech.event_id,
        "tick": runner.clock.tick,
        "fact_type": "heard_speech",
        "subject_id": "speaker",
        "object_id": "listener",
        "location_id": None,
        "properties": {"text": "Hello"},
        "modality": "auditory",
        "disclosure": "LOCAL_AUDITORY",
    }
    runner.events.emit(
        "perception.delivered",
        simulation_tick=runner.clock.tick,
        simulation_time=runner.clock.simulation_time,
        agent_id="listener",
        payload={
            "fact_id": "fact-manual",
            "observer_id": "listener",
            "perceived_tick": runner.clock.tick,
            "fact_age": 0.0,
            "salience": 0.9,
            "fact": fact,
        },
        causation_id=speech.event_id,
    )
    runner.events.emit(
        "perception.dropped",
        simulation_tick=runner.clock.tick,
        simulation_time=runner.clock.simulation_time,
        agent_id="listener",
        payload={
            "fact_id": "fact-manual",
            "observer_id": "listener",
            "perceived_tick": runner.clock.tick,
            "fact_age": 0.0,
            "salience": 0.9,
            "reason": "inbox_limit",
            "fact": fact,
        },
        causation_id=speech.event_id,
    )
    registry.get_component(
        "speaker", PerceptionComponent
    ).visible_now.clear()
    registry.get_component(
        "listener", PositionComponent
    ).coordinate = Coordinate(2, 0)
    runner.run_for(1)
    collector.finalize()

    connection = sqlite3.connect(database)
    episodes = {
        row[0]: (row[1], json.loads(row[2]))
        for row in connection.execute(
            """
            SELECT interaction_type, status, episode_json
            FROM interaction_episodes WHERE run_id = ?
            """,
            ("interaction-projections",),
        )
    }
    assert episodes["direct_speech"][0] == "delivered"
    speech_participants = episodes["direct_speech"][1]["participants"]
    assert {
        (participant["participant_id"], participant["role"])
        for participant in speech_participants
    } >= {
        ("speaker", "speaker"),
        ("listener", "addressee"),
        ("listener", "recipient"),
    }
    assert episodes["transaction"][0] in {"completed", "failed"}
    assert episodes["staffed_service"][0] == "completed"
    assert {
        (participant["participant_id"], participant["role"])
        for participant in episodes["staffed_service"][1]["participants"]
    } >= {
        ("listener", "customer"),
        ("speaker", "service_provider"),
    }
    assert episodes["shared_resource_contention"][0] == "failed"
    assert episodes["visibility"][0] == "ended"
    assert episodes["co_presence"][0] == "ended"
    assert connection.execute(
        "SELECT COUNT(*) FROM perception_facts WHERE run_id = ?",
        ("interaction-projections",),
    ).fetchone()[0] == 1
    deliveries = connection.execute(
        """
        SELECT observer_id, status, reason, salience, delivery_json
        FROM perception_deliveries WHERE run_id = ? ORDER BY status
        """,
        ("interaction-projections",),
    ).fetchall()
    assert [(row[0], row[1], row[2]) for row in deliveries] == [
        ("listener", "delivered", None),
        ("listener", "dropped", "inbox_limit"),
    ]
    assert all(row[3] == 0.9 for row in deliveries)
    assert all(
        json.loads(row[4])["disclosure"] == "LOCAL_AUDITORY"
        for row in deliveries
    )
    raw_count = connection.execute(
        "SELECT COUNT(*) FROM records WHERE run_id = ?",
        ("interaction-projections",),
    ).fetchone()[0]
    before = connection.execute(
        """
        SELECT interaction_id, episode_json FROM interaction_episodes
        WHERE run_id = ? ORDER BY interaction_id
        """,
        ("interaction-projections",),
    ).fetchall()
    before_events = connection.execute(
        """
        SELECT interaction_id, event_id, record_id, event_index
        FROM interaction_events
        WHERE run_id = ?
        ORDER BY interaction_id, event_index, event_id
        """,
        ("interaction-projections",),
    ).fetchall()
    connection.close()

    first = store.rebuild_run_projections("interaction-projections")
    second = store.rebuild_run_projections("interaction-projections")
    assert first == second
    connection = sqlite3.connect(database)
    after = connection.execute(
        """
        SELECT interaction_id, episode_json FROM interaction_episodes
        WHERE run_id = ? ORDER BY interaction_id
        """,
        ("interaction-projections",),
    ).fetchall()
    assert after == before
    after_events = connection.execute(
        """
        SELECT interaction_id, event_id, record_id, event_index
        FROM interaction_events
        WHERE run_id = ?
        ORDER BY interaction_id, event_index, event_id
        """,
        ("interaction-projections",),
    ).fetchall()
    assert after_events == before_events
    assert connection.execute(
        "SELECT COUNT(*) FROM records WHERE run_id = ?",
        ("interaction-projections",),
    ).fetchone()[0] == raw_count
    connection.close()
    summary = store.summary(
        "interaction-projections",
        include_private=True,
    )
    assert summary["capture_complete"] is True
    assert summary["derived_feature_counts"]["interaction_episodes"] >= 5
    store.close()


def test_projection_rebuild_preserves_unfinished_interactions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unfinished-interaction.sqlite3"
    store = SQLiteDatasetStore(database)
    store.begin_run(
        run_id="unfinished-interaction",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "unfinished-interaction"},
    )
    participants = [
        {"participant_id": "speaker", "role": "speaker"},
        {"participant_id": "listener", "role": "listener"},
    ]
    started = DatasetRecord(
        run_id="unfinished-interaction",
        sequence=1,
        record_type="interaction_started",
        simulation_tick=1,
        simulation_time=1,
        subject_id="speaker",
        payload={
            "interaction_id": "interaction-1",
            "interaction_type": "direct_speech",
            "status": "active",
            "participants": participants,
            "content_visibility": "PRIVATE_RESEARCH",
            "context": {"location_id": "room"},
        },
        category=RecordCategory.INTERACTION,
        source=RecordSource.DERIVED,
        visibility=RecordVisibility.PRIVATE_RESEARCH,
    )
    constituent = {
        "event_id": "event-1",
        "event_type": "speech.started",
        "simulation_tick": 1,
        "simulation_time": 1.0,
        "agent_id": "speaker",
        "payload": {"target_id": "listener"},
    }
    event = DatasetRecord(
        run_id="unfinished-interaction",
        sequence=2,
        record_type="interaction_event",
        simulation_tick=1,
        simulation_time=1,
        subject_id="speaker",
        payload={
            "interaction_id": "interaction-1",
            "event_index": 0,
            "event": constituent,
        },
        category=RecordCategory.INTERACTION,
        source=RecordSource.DERIVED,
        visibility=RecordVisibility.PRIVATE_RESEARCH,
    )
    store.append(started)
    store.append_interaction(
        run_id="unfinished-interaction",
        interaction_id="interaction-1",
        record_id=started.record_id,
        interaction_type="direct_speech",
        start_tick=1,
        end_tick=None,
        status="active",
        context={"location_id": "room"},
    )
    for participant in participants:
        store.append_interaction_participant(
            run_id="unfinished-interaction",
            interaction_id="interaction-1",
            participant_id=str(participant["participant_id"]),
            role=str(participant["role"]),
            participant=participant,
        )
    store.append(event)
    store.append_interaction_event(
        run_id="unfinished-interaction",
        interaction_id="interaction-1",
        event_id="event-1",
        record_id=event.record_id,
        event_index=0,
        event_type="speech.started",
        simulation_tick=1,
        event=constituent,
    )
    store.flush()
    store.complete_run(
        "unfinished-interaction",
        status="interrupted",
        final_tick=1,
        final_simulation_time=1,
    )

    store.rebuild_run_projections("unfinished-interaction")
    connection = sqlite3.connect(database)
    interaction = connection.execute(
        """
        SELECT status FROM interactions
        WHERE run_id = ? AND interaction_id = ?
        """,
        ("unfinished-interaction", "interaction-1"),
    ).fetchone()
    participant_count = connection.execute(
        """
        SELECT COUNT(*) FROM interaction_participants
        WHERE run_id = ? AND interaction_id = ?
        """,
        ("unfinished-interaction", "interaction-1"),
    ).fetchone()[0]
    event_count = connection.execute(
        """
        SELECT COUNT(*) FROM interaction_events
        WHERE run_id = ? AND interaction_id = ?
        """,
        ("unfinished-interaction", "interaction-1"),
    ).fetchone()[0]
    connection.close()
    store.close()

    assert interaction == ("active",)
    assert participant_count == 2
    assert event_count == 1


def test_collection_does_not_change_canonical_simulation_behavior(
    tmp_path: Path,
) -> None:
    scenario = load_scenario(scenario_path("needs-and-preemption.json"))
    observed = create_runner(scenario, run_id="observed")
    baseline = create_runner(scenario, run_id="baseline")
    store = SQLiteDatasetStore(tmp_path / "canonical.sqlite3")
    collector = RunDataCollector(
        store=store,
        runner=observed,
        scenario=scenario.model_dump(mode="json"),
    )

    observed.run_for(3)
    baseline.run_for(3)

    assert [event.canonical_dict() for event in observed.events.events] == [
        event.canonical_dict() for event in baseline.events.events
    ]
    collector.finalize()
    baseline.stop()
    store.close()


def test_derived_feature_output_is_deterministic(tmp_path: Path) -> None:
    def collect(database: Path) -> list[tuple[str, str, str, str]]:
        scenario = load_scenario(scenario_path("needs-and-preemption.json"))
        runner = create_runner(scenario, run_id="canonical-features")
        store = SQLiteDatasetStore(database)
        collector = RunDataCollector(
            store=store,
            runner=runner,
            scenario=scenario.model_dump(mode="json"),
        )
        runner.run_for(3)
        collector.finalize()
        connection = sqlite3.connect(database)
        rows = connection.execute(
            """
            SELECT schema_id, schema_version, record_type, payload_json
            FROM records
            WHERE run_id = ? AND source = 'DERIVED'
            ORDER BY sequence
            """,
            ("canonical-features",),
        ).fetchall()
        connection.close()
        store.close()
        return rows

    assert collect(tmp_path / "features-a.sqlite3") == collect(
        tmp_path / "features-b.sqlite3"
    )


def test_projection_rebuild_failure_rolls_back_without_deleting_raw(
    tmp_path: Path,
) -> None:
    store = SQLiteDatasetStore(tmp_path / "rebuild-rollback.sqlite3")
    store.begin_run(
        run_id="rebuild-rollback",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "rebuild-rollback"},
    )
    population = DatasetRecord(
        run_id="rebuild-rollback",
        sequence=1,
        record_type="population_sample",
        simulation_tick=0,
        simulation_time=0,
        payload={"entity_count": 1},
        category=RecordCategory.POPULATION,
        source=RecordSource.DERIVED,
        phase=RunnerPhase.RUN_INITIAL,
    )
    store.append(population)
    store.append_population_sample(
        run_id="rebuild-rollback",
        population_sample_id=f"{population.record_id}:population",
        record_id=population.record_id,
        simulation_tick=0,
        phase=RunnerPhase.RUN_INITIAL,
        population=population.payload,
    )
    store.append(
        DatasetRecord(
            run_id="rebuild-rollback",
            sequence=2,
            record_type="transition_sample",
            simulation_tick=1,
            simulation_time=1,
            subject_id="character",
            payload={"malformed": True},
            category=RecordCategory.TRANSITION,
            source=RecordSource.DERIVED,
        )
    )
    store.flush()
    store.complete_run(
        "rebuild-rollback",
        status="failed",
        final_tick=1,
        final_simulation_time=1,
    )

    with pytest.raises(RuntimeError, match="projection rebuild failed"):
        store.rebuild_run_projections("rebuild-rollback")

    connection = sqlite3.connect(store.path)
    assert connection.execute(
        "SELECT COUNT(*) FROM population_samples WHERE run_id = ?",
        ("rebuild-rollback",),
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM records WHERE run_id = ?",
        ("rebuild-rollback",),
    ).fetchone()[0] == 2
    connection.close()
    store.close()


def test_private_recorder_is_not_an_event_or_telemetry_transport() -> None:
    runner = SimulationRunner(
        RunConfiguration(seed=1, run_id="private-recorder")
    )
    broker = TelemetryBroker(runner)

    runner.research.record(
        "decision_request",
        {"secret": "private prompt"},
        category=RecordCategory.DECISION,
    )

    assert runner.events.events == ()
    assert broker.messages_after(0) == ()
    trace = runner.research.drain()[0]
    assert trace.visibility is RecordVisibility.PRIVATE_RESEARCH
    assert trace.payload["secret"] == "private prompt"

    failing = BufferedResearchRecorder(_FailingResearchSink())
    with pytest.raises(ResearchWriteError, match="capture unavailable"):
        failing.record("model_request", {"request": "private"})
    assert failing.failures


def test_tool_decision_capture_persists_rounds_choices_and_action_outcome(
    tmp_path: Path,
) -> None:
    database = tmp_path / "private-tool-capture.sqlite3"
    scenario = _capture_tool_scenario()
    client = ScriptedModelClient(
        (
            _model_turn(
                "check_environment",
                {"topics": ["time", "weather"]},
            ),
            _model_turn("navigate_to", {"target_id": "lounge"}),
        )
    )
    runner = create_runner(
        scenario,
        model_client=client,
        run_id="private-tool-capture",
    )
    broker = TelemetryBroker(runner)
    store = SQLiteDatasetStore(database)
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario=scenario.model_dump(mode="json"),
    )

    runner.run_for(3)
    telemetry_json = json.dumps(
        [message.to_dict() for message in broker.messages_after(0)]
    )
    assert "Private briefing." not in telemetry_json
    assert "decision_request" not in telemetry_json
    collector.finalize()
    store.close()

    connection = sqlite3.connect(database)
    request_json, visibility = connection.execute(
        """
        SELECT payload_json, visibility FROM records
        WHERE run_id = ? AND record_type = 'decision_request'
        ORDER BY sequence LIMIT 1
        """,
        ("private-tool-capture",),
    ).fetchone()
    request = json.loads(request_json)["request"]
    assert visibility == "PRIVATE_RESEARCH"
    assert request["character_description"]
    assert request["situation_description"] == "Private briefing."
    assert [
        goal["id"] for goal in request["observation"]["structured_goals"]
    ] == ["visit-lounge", "choose-route"]
    assert request["allowed_tools"]
    assert request["information_retrieval_performed"] is True
    assert isinstance(request["retrieved_information"], list)
    evaluations = [
        json.loads(row[0])
        for row in connection.execute(
            """
            SELECT payload_json FROM records
            WHERE run_id = ? AND record_type = 'cognition_evaluation'
            ORDER BY sequence
            """,
            ("private-tool-capture",),
        )
    ]
    assert evaluations[0]["eligible"] is True
    assert any(
        evaluation["eligible"] is False
        and evaluation["reasons"]
        and isinstance(evaluation["gates"], dict)
        for evaluation in evaluations[1:]
    )

    requests = connection.execute(
        """
        SELECT model_request_id, request_json, status
        FROM model_requests WHERE run_id = ?
        ORDER BY model_request_id
        """,
        ("private-tool-capture",),
    ).fetchall()
    assert len(requests) == 2
    second_messages = json.loads(requests[1][1])["messages"]
    assert any(message["role"] == "tool" for message in second_messages)
    assert all(row[2] == "completed" for row in requests)
    assert connection.execute(
        "SELECT COUNT(*) FROM model_turns WHERE run_id = ?",
        ("private-tool-capture",),
    ).fetchone()[0] == 2
    option_types = {
        row[0]
        for row in connection.execute(
            """
            SELECT option_type FROM decision_options
            WHERE run_id = ?
            """,
            ("private-tool-capture",),
        )
    }
    assert {"tool", "target", "travel_mode"} <= option_types
    assert connection.execute(
        """
        SELECT status FROM tool_executions
        WHERE run_id = ? AND tool_name = 'check_environment'
        """,
        ("private-tool-capture",),
    ).fetchone()[0] == "read_completed"
    decision = connection.execute(
        """
        SELECT status, selected_option_id, outcome_json FROM decisions
        WHERE run_id = ? ORDER BY simulation_tick LIMIT 1
        """,
        ("private-tool-capture",),
    ).fetchone()
    assert decision[0:2] == ("completed", "tool:navigate_to")
    assert json.loads(decision[2])["terminal_event_type"] == (
        "action.completed"
    )
    episode = connection.execute(
        """
        SELECT status, action_id, tool_call_id, delays_json
        FROM decision_episodes WHERE run_id = ?
        ORDER BY requested_tick LIMIT 1
        """,
        ("private-tool-capture",),
    ).fetchone()
    assert episode[0] == "completed"
    assert episode[1] is not None
    assert episode[2] == "call-navigate_to"
    assert "terminal" in json.loads(episode[3])
    connection.close()


def test_timeout_capture_closes_decision_without_guessing_action_success(
    tmp_path: Path,
) -> None:
    database = tmp_path / "private-timeout.sqlite3"
    scenario = _capture_tool_scenario(timeout_seconds=0.01)
    runner = create_runner(
        scenario,
        model_client=_SlowModelClient(),
        run_id="private-timeout",
    )
    store = SQLiteDatasetStore(database)
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario=scenario.model_dump(mode="json"),
    )

    runner.run_for(1)
    collector.finalize()
    store.close()

    connection = sqlite3.connect(database)
    assert connection.execute(
        """
        SELECT status FROM model_requests
        WHERE run_id = ? ORDER BY model_request_id LIMIT 1
        """,
        ("private-timeout",),
    ).fetchone()[0] == "timeout"
    assert connection.execute(
        """
        SELECT role FROM model_turns
        WHERE run_id = ? ORDER BY model_request_id LIMIT 1
        """,
        ("private-timeout",),
    ).fetchone()[0] == "error"
    status, action_id, reason = connection.execute(
        """
        SELECT status, action_id, terminal_reason
        FROM decision_episodes WHERE run_id = ?
        """,
        ("private-timeout",),
    ).fetchone()
    assert status == "failed"
    assert action_id is None
    assert reason == "provider_timeout"
    connection.close()
