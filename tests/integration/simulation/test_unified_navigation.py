
import pytest

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.adapters.llm import FakeEmbeddingProvider, ScriptedModelClient
from stage0_sim.application.agents.contracts import (
    CharacterDecisionRequest,
    CharacterObservation,
    ModelToolCall,
    ModelTurn,
    ObservedTarget,
)
from stage0_sim.application.agents.tools import ToolRegistry, ToolValidationError
from stage0_sim.application.characters import prepare_scenario
from stage0_sim.application.information import InformationStore
from stage0_sim.application.navigation import (
    NavigationKnowledgeRecordingSystem,
    NavigationService,
)
from stage0_sim.application.scenario import (
    PlanActionDefinition,
    ScenarioDefinition,
    create_runner,
)
from stage0_sim.application.scenario_resolution import load_and_resolve_scenario
from stage0_sim.domain.components import (
    ActionOrigin,
    ActionType,
    InformationNamespaceComponent,
    NavigationComponent,
    NavigationPrimitiveKind,
    NavigationStatus,
    PlanAction,
    PlanComponent,
    PositionComponent,
    SpatialLocationComponent,
)
from stage0_sim.domain.information import (
    VisibilityLevel,
    character_can_access_information,
    character_information_namespace_id,
)
from stage0_sim.domain.intents import NavigationIntent
from stage0_sim.domain.lineage import queue_plan_actions
from stage0_sim.domain.world import (
    Coordinate,
    GridTopology,
    Space,
    SpaceRegistry,
    Transition,
    TravelMode,
    TraversalContext,
    WorldGrid,
    WorldMap,
)
from stage0_sim.domain.world.routing import RecursiveRoutePlanner
from tests.helpers.paths import (
    EXAMPLE_CHARACTERS,
    EXAMPLE_ELEMENTS,
    EXAMPLE_SCENARIOS,
    REPOSITORY_ROOT,
)

ROOT = REPOSITORY_ROOT
CITY_SCENARIO_PATH = EXAMPLE_SCENARIOS / "sparse-city-car-demo.json"
CHARACTER_DIRECTORY = EXAMPLE_CHARACTERS


def _load_city_scenario() -> ScenarioDefinition:
    return load_and_resolve_scenario(
        CITY_SCENARIO_PATH,
        FileSystemElementLibrary(EXAMPLE_ELEMENTS),
    ).scenario


def _grid(space_id: str, width: int = 3) -> GridTopology:
    return GridTopology(space_id, WorldMap(WorldGrid(width, 1)))


def _request(
    targets: tuple[ObservedTarget, ...],
    allowed_tools: tuple[str, ...],
) -> CharacterDecisionRequest:
    return CharacterDecisionRequest(
        decision_id="decision-1",
        run_id="run-1",
        agent_id="agent-001",
        requested_tick=1,
        state_revision=0,
        trigger="idle",
        character_description="",
        profile_id="profile-1",
        profile_template_version=1,
        profile_content_hash="hash",
        observation=CharacterObservation(
            agent_id="agent-001",
            display_name="Alex",
            simulation_time=1,
            location_id=None,
            activity="IDLE",
            satiety=80,
            energy=80,
            stress=20,
            targets=targets,
            facts=(),
            recent_outcome=None,
            available_travel_modes=("WALK", "CAR"),
        ),
        memories=(),
        allowed_tools=allowed_tools,
    )


def _city_runner(
    payload: dict,
    *,
    embedding_provider: FakeEmbeddingProvider | None = None,
):
    scenario = ScenarioDefinition.model_validate(payload)
    prepared = prepare_scenario(
        scenario,
        FileSystemCharacterLibrary(CHARACTER_DIRECTORY),
    )
    return create_runner(
        scenario,
        resolved_characters=prepared.runtime_characters(),
        embedding_provider=embedding_provider,
    )


def _city_payload_without_plan() -> dict:
    payload = _load_city_scenario().model_dump(mode="json")
    payload["entities"][0]["components"]["plan"] = {"queue": []}
    return payload


def _cross_building_navigation_payload() -> dict:
    payload = _city_payload_without_plan()
    office_room = next(
        room
        for room in payload["world"]["rooms"]
        if room["building_id"] == "building-office"
    )
    office_room["world"]["stations"] = [
        {
            "id": "office-desk",
            "name": "Office Desk",
            "position": {"x": 2, "y": 1},
            "supported_actions": ["WORK"],
        }
    ]
    payload["world"]["objects"].append(
        {
            "id": "office-desk",
            "name": "Office Desk",
            "object_kind": "affordance",
            "building_id": "building-office",
            "room_id": office_room["id"],
            "position": {"x": 2, "y": 1},
        }
    )
    components = payload["entities"][0]["components"]
    components["plan"] = {
        "queue": [
            {
                "action": "NAVIGATE",
                "target": "office-desk",
                "mode": "CAR",
            }
        ]
    }
    components["information"] = {
        "documents": [
            {
                "id": "known-office-desk",
                "kind": "knowledge.place",
                "subject_ids": ["agent-001", "office-desk"],
                "content": {
                    "destination_id": "office-desk",
                    "kind": "station",
                    "name": "Office Desk",
                    "locators": [
                        {
                            "space_id": "building-office.interior",
                            "local_reference": {
                                "kind": "coordinate",
                                "x": 2,
                                "y": 1,
                            },
                        }
                    ],
                    "transition_ids": [
                        "entrance-office",
                        "walk-home-to-parking",
                        "road-west-central",
                        "road-central-east",
                        "walk-parking-to-office",
                    ],
                },
            }
        ]
    }
    return payload


def test_recursive_planner_refines_same_space_and_cross_space_routes() -> None:
    first = _grid("space-a")
    middle = _grid("space-b")
    last = _grid("space-c")
    topology = SpaceRegistry()
    for space_id, adapter in (
        ("space-a", first),
        ("space-b", middle),
        ("space-c", last),
    ):
        topology.register_space(Space(space_id, adapter))
    topology.register_transition(
        Transition(
            id="entrance-z",
            from_locator=first.locator(Coordinate(1, 0)),
            to_locator=middle.locator(Coordinate(0, 0)),
            traversal_kind="portal",
            executor_id="portal",
            cost_model_id="constant",
        )
    )
    topology.register_transition(
        Transition(
            id="entrance-a",
            from_locator=first.locator(Coordinate(1, 0)),
            to_locator=middle.locator(Coordinate(0, 0)),
            traversal_kind="portal",
            executor_id="portal",
            cost_model_id="constant",
        )
    )
    topology.register_transition(
        Transition(
            id="bridge",
            from_locator=middle.locator(Coordinate(2, 0)),
            to_locator=last.locator(Coordinate(0, 0)),
            traversal_kind="bridge",
            executor_id="portal",
            cost_model_id="constant",
        )
    )
    planner = RecursiveRoutePlanner()

    local = planner.plan(
        topology,
        first.locator(Coordinate(0, 0)),
        (first.locator(Coordinate(2, 0)),),
        TraversalContext(),
    )
    cross_first = planner.plan(
        topology,
        first.locator(Coordinate(0, 0)),
        (last.locator(Coordinate(2, 0)),),
        TraversalContext(),
    )
    cross_second = planner.plan(
        topology,
        first.locator(Coordinate(0, 0)),
        (last.locator(Coordinate(2, 0)),),
        TraversalContext(),
    )

    assert len(local.legs) == 2
    assert all(leg.executor_id == "movement" for leg in local.legs)
    assert cross_first == cross_second
    assert [
        leg.transition_id
        for leg in cross_first.legs
        if leg.transition_id is not None
    ] == ["entrance-a", "bridge"]
    assert cross_first.destination == last.locator(Coordinate(2, 0))


def test_known_topology_hides_global_places_until_information_references_them() -> None:
    unknown_runner = _city_runner(_city_payload_without_plan())
    unknown_projection = unknown_runner.registry.get_resource(
        NavigationService
    ).known_topology
    unknown_ids = {
        destination.id
        for destination in unknown_projection.destinations("agent-001")
    }

    assert "building-home" in unknown_ids
    assert "building-office" not in unknown_ids
    assert "place-central-square" not in unknown_ids

    payload = _city_payload_without_plan()
    payload["entities"][0]["components"]["information"] = {
        "documents": [
            {
                "id": "known-office",
                "kind": "knowledge.place",
                "subject_ids": ["agent-001", "building-office"],
                "content": {
                    "destination_id": "building-office",
                    "kind": "building",
                    "name": "East Research Office",
                    "locators": [
                        {
                            "space_id": "building-office.interior",
                            "local_reference": {
                                "kind": "coordinate",
                                "x": 1,
                                "y": 1,
                            },
                        }
                    ],
                },
            },
            {
                "id": "known-office-route",
                "kind": "knowledge.route",
                "subject_ids": ["agent-001", "building-office"],
                "content": {
                    "destination_ids": ["building-office"],
                    "transition_ids": ["entrance-office"],
                },
            },
        ]
    }
    known_runner = _city_runner(payload)
    projection = known_runner.registry.get_resource(
        NavigationService
    ).known_topology
    known = {
        destination.id: destination
        for destination in projection.destinations("agent-001")
    }

    assert known["building-office"].kind == "building"
    assert known["building-office"].locators
    assert "entrance-office" in projection.transition_ids("agent-001")
    assert "place-central-square" not in known


def test_known_topology_applies_character_visibility_before_projection() -> None:
    payload = _city_payload_without_plan()
    office_locator = {
        "space_id": "building-office.interior",
        "local_reference": {"kind": "coordinate", "x": 0, "y": 1},
    }
    payload["entities"][0]["components"]["information"] = {
        "documents": [
            {
                "id": "private-other",
                "kind": "knowledge.place",
                "subject_ids": ["private-hidden"],
                "content": {
                    "destination_id": "private-hidden",
                    "locators": [office_locator],
                },
                "visibility": {
                    "level": "private",
                    "owner_ids": ["agent-002"],
                },
            },
            {
                "id": "operator-only",
                "kind": "knowledge.place",
                "subject_ids": ["operator-hidden"],
                "content": {
                    "destination_id": "operator-hidden",
                    "locators": [office_locator],
                },
                "visibility": {"level": "operator"},
            },
            {
                "id": "shared-reader",
                "kind": "knowledge.place",
                "subject_ids": ["shared-visible"],
                "content": {
                    "destination_id": "shared-visible",
                    "locators": [office_locator],
                },
                "visibility": {
                    "level": "shared",
                    "owner_ids": ["agent-002"],
                    "reader_ids": ["agent-001"],
                },
            },
            {
                "id": "public-place",
                "kind": "knowledge.place",
                "subject_ids": ["public-visible"],
                "content": {
                    "destination_id": "public-visible",
                    "locators": [office_locator],
                },
                "visibility": {"level": "public"},
            },
        ]
    }
    runner = _city_runner(payload)
    projection = runner.registry.get_resource(
        NavigationService
    ).known_topology

    known_ids = {
        destination.id
        for destination in projection.destinations("agent-001")
    }

    assert "shared-visible" in known_ids
    assert "public-visible" in known_ids
    assert "private-hidden" not in known_ids
    assert "operator-hidden" not in known_ids


def test_navigate_tool_produces_only_navigation_intents() -> None:
    targets = (
        ObservedTarget("sofa", "station", "Sofa"),
        ObservedTarget("building-office", "building", "Office"),
    )
    request = _request(
        targets,
        ("navigate_to",),
    )
    tools = ToolRegistry()

    navigate = tools.propose(
        request,
        ModelToolCall(
            "call-navigate",
            "navigate_to",
            {
                "target_id": "building-office",
                "preferred_mode": "CAR",
                "reason": "Work",
            },
        ),
    )
    assert isinstance(navigate, NavigationIntent)
    assert navigate.preferred_mode is TravelMode.CAR

    with pytest.raises(ToolValidationError) as rejected:
        tools.propose(
            request,
            ModelToolCall(
                "call-unknown",
                "navigate_to",
                {"target_id": "place-central-square"},
            ),
        )
    assert rejected.value.reason == "destination_not_known"
    for removed_tool in ("go_to", "travel_to"):
        with pytest.raises(ToolValidationError) as removed:
            tools.propose(
                request,
                ModelToolCall(
                    f"call-{removed_tool}",
                    removed_tool,
                    {"target_id": "sofa"},
                ),
            )
        assert removed.value.reason == "tool_not_allowed"


@pytest.mark.parametrize("removed_action", ["MOVE_TO", "TRAVEL_TO"])
def test_removed_navigation_actions_are_rejected(
    removed_action: str,
) -> None:
    with pytest.raises(ValueError, match=removed_action):
        PlanActionDefinition.model_validate(
            {"action": removed_action, "target": "somewhere"}
        )


def test_navigate_tool_commits_one_general_navigation_intention() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "navigate-tool",
            "cognition": {},
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
                    "id": "agent-001",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                        "controller": {
                            "enabled": True,
                            "tool_allowlist": ["navigate_to"],
                        },
                    },
                }
            ],
        }
    )
    turn = ModelTurn(
        text=None,
        tool_calls=(
            ModelToolCall(
                "call-1",
                "navigate_to",
                {"target_id": "lounge"},
            ),
        ),
        finish_reason="tool_calls",
        provider="scripted",
        model="scripted",
        latency_ms=0,
    )
    runner = create_runner(
        scenario,
        model_client=ScriptedModelClient((turn,)),
    )

    runner.run_for(3)

    navigation = runner.registry.get_component(
        "agent-001",
        NavigationComponent,
    )
    assert navigation.status is NavigationStatus.ARRIVED
    assert runner.registry.get_component(
        "agent-001",
        PositionComponent,
    ).coordinate == Coordinate(1, 0)
    started = [
        event
        for event in runner.events.events
        if event.event_type == "action.started"
    ]
    assert [event.payload["action"] for event in started] == ["NAVIGATE"]


def test_navigation_avoids_an_occupied_locator_in_a_multi_tile_zone() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "occupied-zone-destination",
            "world": {
                "width": 3,
                "height": 2,
                "zones": [
                    {
                        "id": "lounge",
                        "name": "Lounge",
                        "type": "LOUNGE",
                        "tiles": [
                            {"x": 1, "y": 0},
                            {"x": 2, "y": 0},
                        ],
                    }
                ],
            },
            "entities": [
                {
                    "id": "agent-001",
                    "components": {
                        "position": {"x": 0, "y": 1},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                        "plan": {
                            "queue": [
                                {"action": "NAVIGATE", "target": "lounge"}
                            ]
                        },
                    },
                },
                {
                    "id": "agent-002",
                    "components": {
                        "position": {"x": 1, "y": 0},
                        "movement": {"destination": {"x": 0, "y": 0}},
                    },
                },
            ],
        }
    )
    runner = create_runner(scenario)

    runner.run_for(8)

    navigation = runner.registry.get_component(
        "agent-001",
        NavigationComponent,
    )
    assert navigation.status is NavigationStatus.ARRIVED
    assert navigation.route is not None
    assert navigation.route.destination.local_reference == {
        "kind": "coordinate",
        "x": 2,
        "y": 0,
    }
    assert runner.registry.get_component(
        "agent-001",
        PositionComponent,
    ).coordinate == Coordinate(2, 0)
    assert runner.registry.get_component(
        "agent-002",
        PositionComponent,
    ).coordinate == Coordinate(0, 0)


def test_cross_building_navigate_tool_commits_navigation() -> None:
    payload = _city_payload_without_plan()
    components = payload["entities"][0]["components"]
    components["controller"] = {
        "enabled": True,
        "tool_allowlist": ["navigate_to"],
    }
    components["information"] = {
        "documents": [
            {
                "id": "known-office",
                "kind": "knowledge.place",
                "subject_ids": ["agent-001", "building-office"],
                "content": {
                    "destination_id": "building-office",
                    "kind": "building",
                    "name": "East Research Office",
                    "locators": [
                        {
                            "space_id": "building-office.interior",
                            "local_reference": {
                                "kind": "coordinate",
                                "x": 0,
                                "y": 1,
                            },
                        }
                    ],
                    "transition_ids": [
                        "entrance-office",
                        "walk-home-to-parking",
                        "road-west-central",
                        "road-central-east",
                        "walk-parking-to-office",
                    ],
                },
            }
        ]
    }
    turn = ModelTurn(
        text=None,
        tool_calls=(
            ModelToolCall(
                "call-1",
                "navigate_to",
                {
                    "target_id": "building-office",
                    "preferred_mode": "CAR",
                },
            ),
        ),
        finish_reason="tool_calls",
        provider="scripted",
        model="scripted",
        latency_ms=0,
    )
    scenario = ScenarioDefinition.model_validate(payload)
    prepared = prepare_scenario(
        scenario,
        FileSystemCharacterLibrary(CHARACTER_DIRECTORY),
    )
    runner = create_runner(
        scenario,
        resolved_characters=prepared.runtime_characters(),
        model_client=ScriptedModelClient((turn,)),
    )

    runner.run_for(1)

    plan = runner.registry.get_component("agent-001", PlanComponent)
    navigation = runner.registry.get_component(
        "agent-001",
        NavigationComponent,
    )
    assert len(plan.queue) == 1
    assert plan.queue[0].action is ActionType.NAVIGATE
    assert navigation.status is NavigationStatus.REQUESTED
    assert navigation.target_id == "building-office"
    assert navigation.preferred_mode is TravelMode.CAR


def test_cross_building_navigation_travels_then_refines_locally_without_permissions() -> None:
    embedding_provider = FakeEmbeddingProvider()
    runner = _city_runner(
        _cross_building_navigation_payload(),
        embedding_provider=embedding_provider,
    )

    runner.run_for(700)

    navigation = runner.registry.get_component(
        "agent-001",
        NavigationComponent,
    )
    location = runner.registry.get_component(
        "agent-001",
        SpatialLocationComponent,
    ).location
    assert navigation.status is NavigationStatus.ARRIVED
    assert [primitive.kind for primitive in navigation.primitives] == [
        NavigationPrimitiveKind.TRAVEL,
        NavigationPrimitiveKind.MOVE,
    ]
    assert location.place_id == "building-office.interior"
    assert runner.registry.get_component(
        "agent-001",
        PositionComponent,
    ).coordinate == Coordinate(2, 1)
    event_types = [event.event_type for event in runner.events.events]
    assert "travel.requested" in event_types
    assert "travel.arrived" in event_types
    assert event_types.index("travel.arrived") < event_types.index(
        "navigation.arrived"
    )
    started = next(
        event
        for event in runner.events.events
        if event.event_type == "action.started"
        and event.payload["action"] == "NAVIGATE"
    )
    for event_type in ("travel.requested", "travel.arrived", "navigation.arrived"):
        event = next(
            candidate
            for candidate in runner.events.events
            if candidate.event_type == event_type
        )
        assert event.payload["action_id"] == started.payload["action_id"]
    assert not any(
        event.event_type == "navigation.failed"
        for event in runner.events.events
    )
    information = runner.registry.get_resource(InformationStore)
    learned_documents = information.documents(
        namespace_id=character_information_namespace_id("agent-001"),
        kinds=("knowledge.route",),
    )
    learned = next(
        document
        for document in learned_documents
        if document.source.type == "DIRECT_EXPERIENCE"
    )
    arrived = next(
        event
        for event in runner.events.events
        if event.event_type == "navigation.arrived"
    )
    assert embedding_provider.call_count == 0
    assert learned.schema_id == "knowledge.route.v1"
    assert learned.subject_ids == ("agent-001", "office-desk")
    assert learned.content["destination_id"] == "office-desk"
    assert learned.content["locator"] == {
        "space_id": "building-office.interior",
        "local_reference": {"kind": "coordinate", "x": 2, "y": 1},
    }
    assert learned.content["transition_ids"] == [
        leg.transition_id
        for leg in navigation.route.legs
        if leg.transition_id is not None
    ]
    assert learned.content["acquisition_source"] == "DIRECT_EXPERIENCE"
    assert learned.content["simulation_time"] == arrived.simulation_time
    assert learned.source.observer_id == "agent-001"
    assert learned.source.reference_ids == (
        arrived.event_id,
        arrived.correlation_id,
    )
    assert learned.recorded_at == arrived.simulation_time
    assert learned.visibility.level is VisibilityLevel.PRIVATE
    assert learned.visibility.owner_ids == ("agent-001",)
    assert not character_can_access_information(learned, "agent-002")
    assert information.documents(
        namespace_id=character_information_namespace_id("agent-002"),
        kinds=("knowledge.route",),
    ) == ()

    recorder = next(
        system
        for system in runner.systems.systems
        if isinstance(system, NavigationKnowledgeRecordingSystem)
    )
    recorder._event_cursor = 0
    recorder.update(runner.context)
    assert information.history(learned.id) == (learned,)
    namespace = runner.registry.get_component(
        "agent-001",
        InformationNamespaceComponent,
    )
    assert namespace.document_ids.count(learned.id) == 1

    fresh_payload = _cross_building_navigation_payload()
    fresh_components = fresh_payload["entities"][0]["components"]
    fresh_components["plan"] = {"queue": []}
    fresh_components.pop("information")
    fresh_runner = _city_runner(fresh_payload)
    fresh_information = fresh_runner.registry.get_resource(InformationStore)
    fresh_information.register(learned)
    fresh_namespace = fresh_runner.registry.get_component(
        "agent-001",
        InformationNamespaceComponent,
    )
    fresh_runner.registry.set_component(
        "agent-001",
        InformationNamespaceComponent(
            namespace_id=fresh_namespace.namespace_id,
            document_ids=(*fresh_namespace.document_ids, learned.id),
        ),
    )
    fresh_plan = fresh_runner.registry.get_component(
        "agent-001",
        PlanComponent,
    )
    queue_plan_actions(
        fresh_runner.context,
        "agent-001",
        fresh_plan,
        [
            PlanAction(
            action=ActionType.NAVIGATE,
            target="office-desk",
            mode=TravelMode.CAR,
            )
        ],
        origin=ActionOrigin.SCENARIO,
    )
    fresh_runner.registry.get_component(
        "agent-001",
        NavigationComponent,
    ).request("office-desk", preferred_mode=TravelMode.CAR)

    fresh_runner.run_for(700)

    assert fresh_runner.registry.get_component(
        "agent-001",
        NavigationComponent,
    ).status is NavigationStatus.ARRIVED
    assert fresh_runner.registry.get_component(
        "agent-001",
        PositionComponent,
    ).coordinate == Coordinate(2, 1)


def test_stale_known_locator_fails_explicitly_at_authoritative_planning() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "stale-known-locator",
            "world": {"width": 2, "height": 1},
            "entities": [
                {
                    "id": "agent-001",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                        "plan": {
                            "queue": [
                                {
                                    "action": "NAVIGATE",
                                    "target": "remembered-place",
                                }
                            ]
                        },
                        "information": {
                            "documents": [
                                {
                                    "id": "stale-place",
                                    "kind": "knowledge.place",
                                    "subject_ids": [
                                        "agent-001",
                                        "remembered-place",
                                    ],
                                    "content": {
                                        "destination_id": "remembered-place",
                                        "kind": "zone",
                                        "locators": [
                                            {
                                                "space_id": "implicit-building",
                                                "local_reference": {
                                                    "kind": "coordinate",
                                                    "x": 99,
                                                    "y": 0,
                                                },
                                            }
                                        ],
                                    },
                                }
                            ]
                        },
                    },
                }
            ],
        }
    )
    runner = create_runner(scenario)

    runner.run_for(1)

    navigation = runner.registry.get_component(
        "agent-001",
        NavigationComponent,
    )
    failure = next(
        event
        for event in runner.events.events
        if event.event_type == "navigation.failed"
    )
    assert navigation.status is NavigationStatus.FAILED
    assert navigation.failure_reason == "invalid_known_destination_locator"
    assert failure.payload["reason"] == "invalid_known_destination_locator"


def test_bare_remote_destination_remains_unresolved() -> None:
    payload = _city_payload_without_plan()
    components = payload["entities"][0]["components"]
    components["plan"] = {
        "queue": [{"action": "NAVIGATE", "target": "building-office"}]
    }
    components["information"] = {
        "documents": [
            {
                "id": "incomplete-office-address",
                "kind": "knowledge.place",
                "subject_ids": ["agent-001", "building-office"],
                "content": {"destination_id": "building-office"},
            }
        ]
    }
    runner = _city_runner(payload)

    runner.run_for(1)

    navigation = runner.registry.get_component(
        "agent-001",
        NavigationComponent,
    )
    assert navigation.status is NavigationStatus.FAILED
    assert navigation.failure_reason == "known_destination_has_no_locator"
    failure = next(
        event
        for event in runner.events.events
        if event.event_type == "navigation.failed"
    )
    assert failure.payload["reason"] == "known_destination_has_no_locator"


def test_sparse_navigation_cannot_use_an_unknown_road_edge() -> None:
    payload = _city_payload_without_plan()
    components = payload["entities"][0]["components"]
    components["plan"] = {
        "queue": [
            {
                "action": "NAVIGATE",
                "target": "building-office",
                "mode": "CAR",
            }
        ]
    }
    components["information"] = {
        "documents": [
            {
                "id": "partial-office-route",
                "kind": "knowledge.route",
                "subject_ids": ["agent-001", "building-office"],
                "content": {
                    "destination_id": "building-office",
                    "locators": [
                        {
                            "space_id": "building-office.interior",
                            "local_reference": {
                                "kind": "coordinate",
                                "x": 0,
                                "y": 1,
                            },
                        }
                    ],
                    "transition_ids": [
                        "entrance-office",
                        "walk-home-to-parking",
                        "road-west-central",
                        "walk-parking-to-office",
                    ],
                },
            }
        ]
    }
    runner = _city_runner(payload)

    runner.run_for(1)

    navigation = runner.registry.get_component(
        "agent-001",
        NavigationComponent,
    )
    assert navigation.status is NavigationStatus.FAILED
    assert navigation.failure_reason == "route_not_found"


def test_sequential_queued_navigation_refreshes_each_request() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "sequential-navigation",
            "world": {
                "width": 3,
                "height": 1,
                "zones": [
                    {
                        "id": "middle",
                        "name": "Middle",
                        "type": "HALL",
                        "tiles": [{"x": 1, "y": 0}],
                    },
                    {
                        "id": "end",
                        "name": "End",
                        "type": "HALL",
                        "tiles": [{"x": 2, "y": 0}],
                    },
                ],
            },
            "entities": [
                {
                    "id": "agent-001",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                        "plan": {
                            "queue": [
                                {"action": "NAVIGATE", "target": "middle"},
                                {"action": "NAVIGATE", "target": "end"},
                            ]
                        },
                    },
                }
            ],
        }
    )
    runner = create_runner(scenario)

    runner.run_for(8)

    navigation = runner.registry.get_component(
        "agent-001",
        NavigationComponent,
    )
    assert navigation.status is NavigationStatus.ARRIVED
    assert navigation.target_id == "end"
    assert runner.registry.get_component(
        "agent-001",
        PositionComponent,
    ).coordinate == Coordinate(2, 0)
    assert [
        event.payload["target"]
        for event in runner.events.events
        if event.event_type == "action.started"
    ] == ["middle", "end"]


def test_navigation_uses_the_planned_outbound_entrance() -> None:
    payload = _city_payload_without_plan()
    payload["world"]["buildings"][0]["entrances"].append(
        {
            "id": "entrance-home-alt",
            "room_id": "building-home.interior",
            "local_coordinate": {"x": 2, "y": 1},
            "neighborhood_node_id": "node-home-alt",
        }
    )
    payload["world"]["transport"]["nodes"].append(
        {
            "id": "node-home-alt",
            "kind": "BUILDING_ENTRANCE",
            "position": {"x": 700, "y": 1400},
            "place_id": "building-home",
        }
    )
    payload["world"]["transport"]["edges"].extend(
        [
            {
                "id": "walk-home-primary-office",
                "from_node_id": "node-home-entrance",
                "to_node_id": "node-office-entrance",
                "allowed_modes": ["WALK"],
                "distance_meters": 1000,
                "geometry": [
                    {"x": 650, "y": 1400},
                    {"x": 5350, "y": 1400},
                ],
                "bidirectional": True,
            },
            {
                "id": "walk-home-alt-office",
                "from_node_id": "node-home-alt",
                "to_node_id": "node-office-entrance",
                "allowed_modes": ["WALK"],
                "distance_meters": 100,
                "geometry": [
                    {"x": 700, "y": 1400},
                    {"x": 5350, "y": 1400},
                ],
                "bidirectional": True,
            },
        ]
    )
    components = payload["entities"][0]["components"]
    components["plan"] = {
        "queue": [
            {
                "action": "NAVIGATE",
                "target": "building-office",
                "mode": "WALK",
            }
        ]
    }
    components["information"] = {
        "documents": [
            {
                "id": "known-walking-route",
                "kind": "knowledge.route",
                "subject_ids": ["agent-001", "building-office"],
                "content": {
                    "destination_id": "building-office",
                    "locators": [
                        {
                            "space_id": "building-office.interior",
                            "local_reference": {
                                "kind": "coordinate",
                                "x": 0,
                                "y": 1,
                            },
                        }
                    ],
                    "transition_ids": [
                        "entrance-office",
                        "walk-home-primary-office",
                        "walk-home-alt-office",
                    ],
                },
            }
        ]
    }
    runner = _city_runner(payload)

    runner.run_for(100)

    navigation = runner.registry.get_component(
        "agent-001",
        NavigationComponent,
    )
    assert navigation.status is NavigationStatus.ARRIVED
    travel_primitive = navigation.primitives[0]
    assert travel_primitive.outbound_transition_id == "entrance-home-alt"
    assert travel_primitive.origin_network_node_id == "node-home-alt"
    route_planned = next(
        event
        for event in runner.events.events
        if event.event_type == "travel.route_planned"
    )
    assert [leg["edge_id"] for leg in route_planned.payload["legs"]] == [
        "walk-home-alt-office"
    ]


def test_navigation_propagates_vehicle_unavailable_failure() -> None:
    payload = _cross_building_navigation_payload()
    payload["world"]["transport"]["vehicles"] = []
    runner = _city_runner(payload)

    runner.run_for(1)

    failures = {
        event.event_type: event.payload["reason"]
        for event in runner.events.events
        if event.event_type
        in {
            "travel.route_failed",
            "navigation.failed",
            "action.failed",
        }
    }
    assert failures == {
        "travel.route_failed": "vehicle_not_available",
        "navigation.failed": "vehicle_not_available",
        "action.failed": "vehicle_not_available",
    }
