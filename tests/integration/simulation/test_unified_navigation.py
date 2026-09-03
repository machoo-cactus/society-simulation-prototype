
import pytest

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.adapters.llm import ScriptedModelClient
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
    OpenableComponent,
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
    CATALOG_CHARACTERS,
    CATALOG_ELEMENTS,
    CATALOG_SCENARIOS,
    REPOSITORY_ROOT,
)

ROOT = REPOSITORY_ROOT
CITY_SCENARIO_PATH = CATALOG_SCENARIOS / "open-city-day.json"
CHARACTER_DIRECTORY = CATALOG_CHARACTERS
CITY_ACTOR_ID = "city-alex"
ORIGIN_BUILDING_ID = "building-city-north-apartments"
ORIGIN_ROOM_ID = f"{ORIGIN_BUILDING_ID}.interior"
ORIGIN_NODE_ID = "node-city-north-apartments"
DESTINATION_BUILDING_ID = "building-city-north-cafe"
DESTINATION_ROOM_ID = f"{DESTINATION_BUILDING_ID}.interior"
DESTINATION_NODE_ID = "node-city-north-cafe"
DESTINATION_ENTRANCE_ID = f"{DESTINATION_BUILDING_ID}.front"
DESTINATION_STATION_ID = f"{DESTINATION_ROOM_ID}.window-seat"
CITY_ROUTE_EDGE_IDS = (
    "edge-node-city-north-apartments-node-district-city-north-hub",
    "edge-node-city-north-cafe-node-district-city-north-hub",
)


def _load_city_scenario() -> ScenarioDefinition:
    return load_and_resolve_scenario(
        CITY_SCENARIO_PATH,
        FileSystemElementLibrary(CATALOG_ELEMENTS),
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
):
    scenario = ScenarioDefinition.model_validate(payload)
    prepared = prepare_scenario(
        scenario,
        FileSystemCharacterLibrary(CHARACTER_DIRECTORY),
    )
    return create_runner(
        scenario,
        resolved_characters=prepared.runtime_characters(),
    )


def _city_payload_without_plan() -> dict:
    payload = _load_city_scenario().model_dump(mode="json")
    payload["dt"] = 10.0
    for entity in payload["entities"]:
        entity["components"]["controller"]["enabled"] = False
        entity["components"]["plan"] = {"queue": []}
    return payload


def _cross_building_navigation_payload() -> dict:
    payload = _city_payload_without_plan()
    components = next(
        entity["components"]
        for entity in payload["entities"]
        if entity["id"] == CITY_ACTOR_ID
    )
    components["plan"] = {
        "queue": [
            {
                "action": "NAVIGATE",
                "target": DESTINATION_STATION_ID,
                "mode": "CYCLE",
            }
        ]
    }
    components["information"] = {
        "documents": [
            {
                "id": "known-cafe-seat",
                "kind": "knowledge.place",
                "subject_ids": [CITY_ACTOR_ID, DESTINATION_STATION_ID],
                "content": {
                    "destination_id": DESTINATION_STATION_ID,
                    "kind": "station",
                    "name": "Cafe Seat",
                    "locators": [
                        {
                            "space_id": DESTINATION_ROOM_ID,
                            "local_reference": {
                                "kind": "coordinate",
                                "x": 7,
                                "y": 2,
                            },
                        }
                    ],
                    "transition_ids": [
                        DESTINATION_ENTRANCE_ID,
                        *CITY_ROUTE_EDGE_IDS,
                    ],
                },
            }
        ]
    }
    return payload


def _door_navigation_payload(*, locked: bool) -> dict:
    payload = _city_payload_without_plan()
    door = next(
        world_object
        for world_object in payload["world"]["objects"]
        if world_object["id"] == f"{ORIGIN_ROOM_ID}.front-door"
    )
    door["physical"]["initial_open"] = False
    door["physical"]["capabilities"]["openable"]["initially_locked"] = (
        locked
    )
    components = next(
        entity["components"]
        for entity in payload["entities"]
        if entity["id"] == CITY_ACTOR_ID
    )
    components["plan"] = {
        "queue": [
            {
                "action": "NAVIGATE",
                "target": DESTINATION_BUILDING_ID,
                "mode": "WALK",
            }
        ]
    }
    destination_document = next(
        document
        for document in components["information"]["documents"]
        if document["content"]["destination_id"] == DESTINATION_BUILDING_ID
    )
    destination_document["content"]["transition_ids"] = [
        DESTINATION_ENTRANCE_ID,
        *CITY_ROUTE_EDGE_IDS,
    ]
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


def test_navigation_opens_an_unlocked_door_through_interaction_primitive() -> None:
    runner = _city_runner(_door_navigation_payload(locked=False))

    runner.run_for(40)

    door_id = f"{ORIGIN_ROOM_ID}.front-door"
    door = runner.registry.get_component(door_id, OpenableComponent)
    navigation = runner.registry.get_component(
        CITY_ACTOR_ID,
        NavigationComponent,
    )
    assert door.is_open
    assert navigation.status is NavigationStatus.ARRIVED
    assert any(
        primitive.kind is NavigationPrimitiveKind.INTERACT
        for primitive in navigation.primitives
    )
    assert any(
        event.event_type == "interaction.completed"
        and event.payload.get("target_id") == door_id
        and event.payload.get("source") == "navigation"
        for event in runner.events.events
    )


def test_navigation_fails_explicitly_at_a_locked_door() -> None:
    runner = _city_runner(_door_navigation_payload(locked=True))

    runner.run_for(20)

    navigation = runner.registry.get_component(
        CITY_ACTOR_ID,
        NavigationComponent,
    )
    assert navigation.status is NavigationStatus.FAILED
    assert navigation.failure_reason == "object_locked"
    assert any(
        event.event_type == "interaction.failed"
        and event.payload.get("reason") == "object_locked"
        for event in runner.events.events
    )


def test_known_topology_hides_global_places_until_information_references_them() -> None:
    unknown_payload = _city_payload_without_plan()
    actor = next(
        entity
        for entity in unknown_payload["entities"]
        if entity["id"] == CITY_ACTOR_ID
    )
    actor["components"]["information"] = {"documents": []}
    unknown_runner = _city_runner(unknown_payload)
    unknown_projection = unknown_runner.registry.get_resource(
        NavigationService
    ).known_topology
    unknown_ids = {
        destination.id
        for destination in unknown_projection.destinations(CITY_ACTOR_ID)
    }

    assert ORIGIN_BUILDING_ID in unknown_ids
    assert DESTINATION_BUILDING_ID not in unknown_ids
    assert "place-city-central-square" not in unknown_ids

    payload = _city_payload_without_plan()
    actor = next(
        entity
        for entity in payload["entities"]
        if entity["id"] == CITY_ACTOR_ID
    )
    actor["components"]["information"] = {
        "documents": [
            {
                "id": "known-cafe",
                "kind": "knowledge.place",
                "subject_ids": [CITY_ACTOR_ID, DESTINATION_BUILDING_ID],
                "content": {
                    "destination_id": DESTINATION_BUILDING_ID,
                    "kind": "building",
                    "name": "North Park Cafe",
                    "locators": [
                        {
                            "space_id": DESTINATION_ROOM_ID,
                            "local_reference": {
                                "kind": "coordinate",
                                "x": 0,
                                "y": 4,
                            },
                        }
                    ],
                },
            },
            {
                "id": "known-cafe-route",
                "kind": "knowledge.route",
                "subject_ids": [CITY_ACTOR_ID, DESTINATION_BUILDING_ID],
                "content": {
                    "destination_ids": [DESTINATION_BUILDING_ID],
                    "transition_ids": [DESTINATION_ENTRANCE_ID],
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
        for destination in projection.destinations(CITY_ACTOR_ID)
    }

    assert known[DESTINATION_BUILDING_ID].kind == "building"
    assert known[DESTINATION_BUILDING_ID].locators
    assert DESTINATION_ENTRANCE_ID in projection.transition_ids(
        CITY_ACTOR_ID
    )
    assert "place-city-central-square" not in known


def test_known_topology_applies_character_visibility_before_projection() -> None:
    payload = _city_payload_without_plan()
    office_locator = {
        "space_id": DESTINATION_ROOM_ID,
        "local_reference": {"kind": "coordinate", "x": 0, "y": 1},
    }
    actor = next(
        entity
        for entity in payload["entities"]
        if entity["id"] == CITY_ACTOR_ID
    )
    actor["components"]["information"] = {
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
                    "owner_ids": ["city-jordan"],
                    "reader_ids": [CITY_ACTOR_ID],
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
        for destination in projection.destinations(CITY_ACTOR_ID)
    }

    assert "shared-visible" in known_ids
    assert "public-visible" in known_ids
    assert "private-hidden" not in known_ids
    assert "operator-hidden" not in known_ids


def test_navigate_tool_produces_only_navigation_intents() -> None:
    targets = (
        ObservedTarget("sofa", "station", "Sofa"),
        ObservedTarget("known-building", "building", "Known building"),
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
                "target_id": "known-building",
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
    ).coordinate == Coordinate(13, 4)
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
        "x": 22,
        "y": 4,
    }
    assert runner.registry.get_component(
        "agent-001",
        PositionComponent,
    ).coordinate == Coordinate(22, 4)
    assert runner.registry.get_component(
        "agent-002",
        PositionComponent,
    ).coordinate == Coordinate(4, 4)


def test_cross_building_navigate_tool_commits_navigation() -> None:
    payload = _city_payload_without_plan()
    components = next(
        entity["components"]
        for entity in payload["entities"]
        if entity["id"] == CITY_ACTOR_ID
    )
    components["controller"] = {
        "enabled": True,
        "tool_allowlist": ["navigate_to"],
    }
    components["information"] = {
        "documents": [
            {
                "id": "known-cafe",
                "kind": "knowledge.place",
                "subject_ids": [CITY_ACTOR_ID, DESTINATION_BUILDING_ID],
                "content": {
                    "destination_id": DESTINATION_BUILDING_ID,
                    "kind": "building",
                    "name": "North Park Cafe",
                    "locators": [
                        {
                            "space_id": DESTINATION_ROOM_ID,
                            "local_reference": {
                                "kind": "coordinate",
                                "x": 0,
                                "y": 4,
                            },
                        }
                    ],
                    "transition_ids": [
                        DESTINATION_ENTRANCE_ID,
                        *CITY_ROUTE_EDGE_IDS,
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
                    "target_id": DESTINATION_BUILDING_ID,
                    "preferred_mode": "CYCLE",
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

    plan = runner.registry.get_component(CITY_ACTOR_ID, PlanComponent)
    navigation = runner.registry.get_component(
        CITY_ACTOR_ID,
        NavigationComponent,
    )
    assert len(plan.queue) == 1
    assert plan.queue[0].action is ActionType.NAVIGATE
    assert navigation.status is NavigationStatus.REQUESTED
    assert navigation.target_id == DESTINATION_BUILDING_ID
    assert navigation.preferred_mode is TravelMode.CYCLE


def test_cross_building_navigation_travels_then_refines_locally_without_permissions() -> None:
    runner = _city_runner(_cross_building_navigation_payload())

    runner.run_for(50)

    navigation = runner.registry.get_component(
        CITY_ACTOR_ID,
        NavigationComponent,
    )
    location = runner.registry.get_component(
        CITY_ACTOR_ID,
        SpatialLocationComponent,
    ).location
    assert navigation.status is NavigationStatus.ARRIVED
    primitive_kinds = [
        primitive.kind for primitive in navigation.primitives
    ]
    assert NavigationPrimitiveKind.TRAVEL in primitive_kinds
    assert NavigationPrimitiveKind.MOVE in primitive_kinds
    assert NavigationPrimitiveKind.INTERACT in primitive_kinds
    assert primitive_kinds.index(
        NavigationPrimitiveKind.TRAVEL
    ) < len(primitive_kinds) - 1
    assert location.place_id == DESTINATION_ROOM_ID
    assert navigation.route is not None
    position = runner.registry.get_component(
        CITY_ACTOR_ID,
        PositionComponent,
    ).coordinate
    assert navigation.route.destination.local_reference == {
        "kind": "coordinate",
        "x": position.x,
        "y": position.y,
    }
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
        namespace_id=character_information_namespace_id(CITY_ACTOR_ID),
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
    assert learned.schema_id == "knowledge.route.v1"
    assert learned.subject_ids == (CITY_ACTOR_ID, DESTINATION_STATION_ID)
    assert learned.content["destination_id"] == DESTINATION_STATION_ID
    assert learned.content["locator"] == {
        "space_id": DESTINATION_ROOM_ID,
        "local_reference": navigation.route.destination.local_reference,
    }
    assert learned.content["transition_ids"] == [
        leg.transition_id
        for leg in navigation.route.legs
        if leg.transition_id is not None
    ]
    assert learned.content["acquisition_source"] == "DIRECT_EXPERIENCE"
    assert learned.content["simulation_time"] == arrived.simulation_time
    assert learned.source.observer_id == CITY_ACTOR_ID
    assert learned.source.reference_ids == (
        arrived.event_id,
        arrived.correlation_id,
    )
    assert learned.recorded_at == arrived.simulation_time
    assert learned.visibility.level is VisibilityLevel.PRIVATE
    assert learned.visibility.owner_ids == (CITY_ACTOR_ID,)
    assert not character_can_access_information(learned, "city-jordan")
    assert information.documents(
        namespace_id=character_information_namespace_id("city-jordan"),
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
        CITY_ACTOR_ID,
        InformationNamespaceComponent,
    )
    assert namespace.document_ids.count(learned.id) == 1

    fresh_payload = _cross_building_navigation_payload()
    fresh_components = next(
        entity["components"]
        for entity in fresh_payload["entities"]
        if entity["id"] == CITY_ACTOR_ID
    )
    fresh_components["plan"] = {"queue": []}
    fresh_components.pop("information")
    fresh_runner = _city_runner(fresh_payload)
    fresh_information = fresh_runner.registry.get_resource(InformationStore)
    fresh_information.register(learned)
    fresh_namespace = fresh_runner.registry.get_component(
        CITY_ACTOR_ID,
        InformationNamespaceComponent,
    )
    fresh_runner.registry.set_component(
        CITY_ACTOR_ID,
        InformationNamespaceComponent(
            namespace_id=fresh_namespace.namespace_id,
            document_ids=(*fresh_namespace.document_ids, learned.id),
        ),
    )
    fresh_plan = fresh_runner.registry.get_component(
        CITY_ACTOR_ID,
        PlanComponent,
    )
    queue_plan_actions(
        fresh_runner.context,
        CITY_ACTOR_ID,
        fresh_plan,
        [
            PlanAction(
                action=ActionType.NAVIGATE,
                target=DESTINATION_STATION_ID,
                mode=TravelMode.CYCLE,
            )
        ],
        origin=ActionOrigin.SCENARIO,
    )
    fresh_runner.registry.get_component(
        CITY_ACTOR_ID,
        NavigationComponent,
    ).request(DESTINATION_STATION_ID, preferred_mode=TravelMode.CYCLE)

    fresh_runner.run_for(50)

    assert fresh_runner.registry.get_component(
        CITY_ACTOR_ID,
        NavigationComponent,
    ).status is NavigationStatus.ARRIVED
    assert fresh_runner.registry.get_component(
        CITY_ACTOR_ID,
        SpatialLocationComponent,
    ).location.place_id == DESTINATION_ROOM_ID


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
    components = next(
        entity["components"]
        for entity in payload["entities"]
        if entity["id"] == CITY_ACTOR_ID
    )
    components["plan"] = {
        "queue": [
            {
                "action": "NAVIGATE",
                "target": DESTINATION_BUILDING_ID,
            }
        ]
    }
    components["information"] = {
        "documents": [
            {
                "id": "incomplete-cafe-address",
                "kind": "knowledge.place",
                "subject_ids": [CITY_ACTOR_ID, DESTINATION_BUILDING_ID],
                "content": {"destination_id": DESTINATION_BUILDING_ID},
            }
        ]
    }
    runner = _city_runner(payload)

    runner.run_for(1)

    navigation = runner.registry.get_component(
        CITY_ACTOR_ID,
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


def test_navigation_cannot_use_an_unknown_city_edge() -> None:
    payload = _city_payload_without_plan()
    components = next(
        entity["components"]
        for entity in payload["entities"]
        if entity["id"] == CITY_ACTOR_ID
    )
    components["plan"] = {
        "queue": [
            {
                "action": "NAVIGATE",
                "target": DESTINATION_BUILDING_ID,
                "mode": "CYCLE",
            }
        ]
    }
    components["information"] = {
        "documents": [
            {
                "id": "partial-cafe-route",
                "kind": "knowledge.route",
                "subject_ids": [CITY_ACTOR_ID, DESTINATION_BUILDING_ID],
                "content": {
                    "destination_id": DESTINATION_BUILDING_ID,
                    "locators": [
                        {
                            "space_id": DESTINATION_ROOM_ID,
                            "local_reference": {
                                "kind": "coordinate",
                                "x": 0,
                                "y": 4,
                            },
                        }
                    ],
                    "transition_ids": [
                        DESTINATION_ENTRANCE_ID,
                        CITY_ROUTE_EDGE_IDS[0],
                    ],
                },
            }
        ]
    }
    runner = _city_runner(payload)

    runner.run_for(1)

    navigation = runner.registry.get_component(
        CITY_ACTOR_ID,
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
    ).coordinate == Coordinate(22, 4)
    assert [
        event.payload["target"]
        for event in runner.events.events
        if event.event_type == "action.started"
    ] == ["middle", "end"]


def test_navigation_uses_the_planned_outbound_entrance() -> None:
    payload = _city_payload_without_plan()
    origin_building = next(
        building
        for building in payload["world"]["buildings"]
        if building["id"] == ORIGIN_BUILDING_ID
    )
    origin_building["entrances"].append(
        {
            "id": "building-city-north-apartments.side",
            "room_id": ORIGIN_ROOM_ID,
            "local_coordinate": {"x": 0, "y": 3},
            "neighborhood_node_id": "node-city-north-apartments-side",
        }
    )
    destination_node = next(
        node
        for node in payload["world"]["transport"]["nodes"]
        if node["id"] == DESTINATION_NODE_ID
    )
    origin_node = next(
        node
        for node in payload["world"]["transport"]["nodes"]
        if node["id"] == ORIGIN_NODE_ID
    )
    payload["world"]["transport"]["nodes"].append(
        {
            "id": "node-city-north-apartments-side",
            "kind": "BUILDING_ENTRANCE",
            "position": {"x": 110.0, "y": 900.0},
            "place_id": ORIGIN_BUILDING_ID,
        }
    )
    payload["world"]["transport"]["edges"].extend(
        [
            {
                "id": "walk-apartments-primary-cafe",
                "from_node_id": ORIGIN_NODE_ID,
                "to_node_id": DESTINATION_NODE_ID,
                "allowed_modes": ["WALK"],
                "distance_meters": 1000,
                "geometry": [
                    origin_node["position"],
                    destination_node["position"],
                ],
                "bidirectional": True,
            },
            {
                "id": "walk-apartments-side-cafe",
                "from_node_id": "node-city-north-apartments-side",
                "to_node_id": DESTINATION_NODE_ID,
                "allowed_modes": ["WALK"],
                "distance_meters": 100,
                "geometry": [
                    {"x": 110.0, "y": 900.0},
                    destination_node["position"],
                ],
                "bidirectional": True,
            },
        ]
    )
    components = next(
        entity["components"]
        for entity in payload["entities"]
        if entity["id"] == CITY_ACTOR_ID
    )
    components["plan"] = {
        "queue": [
            {
                "action": "NAVIGATE",
                "target": DESTINATION_BUILDING_ID,
                "mode": "WALK",
            }
        ]
    }
    components["information"] = {
        "documents": [
            {
                "id": "known-walking-route",
                "kind": "knowledge.route",
                "subject_ids": [CITY_ACTOR_ID, DESTINATION_BUILDING_ID],
                "content": {
                    "destination_id": DESTINATION_BUILDING_ID,
                    "locators": [
                        {
                            "space_id": DESTINATION_ROOM_ID,
                            "local_reference": {
                                "kind": "coordinate",
                                "x": 0,
                                "y": 4,
                            },
                        }
                    ],
                    "transition_ids": [
                        DESTINATION_ENTRANCE_ID,
                        "walk-apartments-primary-cafe",
                        "walk-apartments-side-cafe",
                    ],
                },
            }
        ]
    }
    runner = _city_runner(payload)

    runner.run_for(20)

    navigation = runner.registry.get_component(
        CITY_ACTOR_ID,
        NavigationComponent,
    )
    assert navigation.status is NavigationStatus.ARRIVED
    travel_primitive = next(
        primitive
        for primitive in navigation.primitives
        if primitive.kind is NavigationPrimitiveKind.TRAVEL
    )
    assert travel_primitive.outbound_transition_id == (
        "building-city-north-apartments.side"
    )
    assert travel_primitive.origin_network_node_id == (
        "node-city-north-apartments-side"
    )
    route_planned = next(
        event
        for event in runner.events.events
        if event.event_type == "travel.route_planned"
    )
    assert [leg["edge_id"] for leg in route_planned.payload["legs"]] == [
        "walk-apartments-side-cafe"
    ]


def test_navigation_propagates_vehicle_unavailable_failure() -> None:
    payload = _cross_building_navigation_payload()
    payload["world"]["transport"]["vehicles"] = []
    runner = _city_runner(payload)

    runner.run_for(10)

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
