from collections import Counter, deque

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.adapters.llm import ScriptedModelClient
from stage0_sim.application.agents.contracts import ModelToolCall, ModelTurn
from stage0_sim.application.characters import prepare_scenario
from stage0_sim.application.navigation import NavigationService
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.application.scenario_resolution import load_and_resolve_scenario
from stage0_sim.application.telemetry import (
    build_agent_snapshot,
    build_runtime_snapshot,
    build_ui_bootstrap,
)
from stage0_sim.domain.world import CityWorld, TravelMode, find_transport_route
from tests.helpers.paths import (
    CATALOG_CHARACTERS,
    CATALOG_ELEMENTS,
    CATALOG_SCENARIOS,
)

SCENARIO_PATH = CATALOG_SCENARIOS / "open-city-day.json"
CHARACTER_ASSIGNMENTS = {
    "city-alex": "alex-chen",
    "city-jordan": "jordan-lee",
    "city-maya": "maya-thompson",
    "city-samira": "samira-khan",
}
BUILDING_ARCHETYPES = {
    "civic.library": 2,
    "hospitality.cafe": 2,
    "mobility.station": 1,
    "residential.apartment": 1,
    "residential.townhouse": 2,
    "retail.market": 2,
}


def _load_scenario() -> ScenarioDefinition:
    return load_and_resolve_scenario(
        SCENARIO_PATH,
        FileSystemElementLibrary(CATALOG_ELEMENTS),
    ).scenario


def _prepared_runner(*, model_client: ScriptedModelClient | None = None):
    scenario = _load_scenario()
    prepared = prepare_scenario(
        scenario,
        FileSystemCharacterLibrary(CATALOG_CHARACTERS),
    )
    return (
        scenario,
        prepared,
        create_runner(
            scenario,
            resolved_characters=prepared.runtime_characters(),
            model_client=model_client or ScriptedModelClient(()),
        ),
    )


def _wait_turn(index: int) -> ModelTurn:
    return ModelTurn(
        text=None,
        tool_calls=(
            ModelToolCall(
                call_id=f"wait-{index}",
                name="wait",
                arguments={"duration_seconds": 1},
            ),
        ),
        finish_reason="tool_calls",
        provider="scripted",
        model="open-city-smoke",
        latency_ms=0,
    )


def test_open_city_catalog_materializes_current_city_contract() -> None:
    resolved = load_and_resolve_scenario(
        SCENARIO_PATH,
        FileSystemElementLibrary(CATALOG_ELEMENTS),
    )
    scenario = resolved.scenario
    world = scenario.world
    assert world is not None
    assert scenario.schema_version == 9
    assert world.type == "city"
    assert world.city.id == "city-open-day"
    assert len(world.districts) == 3
    assert len(world.buildings) == 10
    assert len(world.outdoor_places) == 3
    assert len(world.rooms) == 10
    assert len(world.transport.nodes) == 16
    assert len(world.transport.edges) == 15

    source = resolved.source.model_dump(mode="json")
    archetypes = Counter(
        building["element"]["id"]
        for zone in source["world"]["city_zones"]
        for building in zone["buildings"]
    )
    assert archetypes == BUILDING_ARCHETYPES
    assert all(
        building.entrances and len(building.room_ids) == 1
        for building in world.buildings
    )
    assert len(world.objects) >= 200
    assert {
        world_object.definition_id for world_object in world.objects
    } >= {
        "common.exterior-door",
        "common.phone",
        "civic.service-desk",
        "hospitality.cafe-counter",
        "mobility.ticket-machine",
        "residential.sofa",
        "retail.market-checkout",
    }


def test_open_city_has_characters_goals_services_and_four_travel_modes() -> None:
    scenario, prepared, runner = _prepared_runner()
    assert prepared.assignments == CHARACTER_ASSIGNMENTS
    assert {
        entity.id
        for entity in scenario.entities
        if entity.components["controller"]["enabled"]
    } == set(CHARACTER_ASSIGNMENTS)
    assert {
        entity.components["goals"]["goals"][0]["id"]
        for entity in scenario.entities
    } == {
        "city-alex-open-day",
        "city-jordan-open-day",
        "city-maya-open-day",
        "city-samira-open-day",
    }
    assert all(
        entity.components["goals"]["goals"][0]["completion_policy"] == "all"
        and entity.components["goals"]["goals"][0]["criteria"]
        == [{"type": "simulation_time", "simulation_time": 14400.0}]
        for entity in scenario.entities
    )
    assert all(
        "navigate_to" in entity.components["controller"]["tool_allowlist"]
        and "engage" in entity.components["controller"]["tool_allowlist"]
        and "transact" in entity.components["controller"]["tool_allowlist"]
        for entity in scenario.entities
    )

    transaction_points = [
        point
        for room in scenario.world.rooms
        for point in room.world.transaction_points
    ]
    assert len(transaction_points) == 5
    assert {
        point.id for point in transaction_points if point.staffing is not None
    } == {
        "building-city-central-cafe.interior.counter",
        "building-city-north-cafe.interior.counter",
    }
    assert {role.id for role in scenario.npc_roles} == {
        "hospitality.barista"
    }

    city = runner.registry.get_resource(CityWorld)
    assert {
        mode for edge in city.edges for mode in edge.allowed_modes
    } == {
        TravelMode.WALK,
        TravelMode.CYCLE,
        TravelMode.CAR,
        TravelMode.METRO,
    }
    route_cases = (
        (
            "node-city-east-civic",
            "node-city-central-market",
            TravelMode.WALK,
        ),
        (
            "node-city-east-townhouse",
            "node-city-central-cafe",
            TravelMode.CYCLE,
        ),
        (
            "node-city-central-townhouse",
            "node-city-north-library",
            TravelMode.CAR,
        ),
        (
            "node-city-north-apartments",
            "node-city-east-civic",
            TravelMode.METRO,
        ),
    )
    for origin, destination, mode in route_cases:
        route = find_transport_route(city, origin, destination, mode)
        assert route is not None
        assert mode in {leg.mode for leg in route}


def test_open_city_transport_topology_is_connected_and_geometrically_valid() -> None:
    world = _load_scenario().world
    assert world is not None
    node_by_id = {node.id: node for node in world.transport.nodes}
    neighbors = {node_id: set() for node_id in node_by_id}
    for edge in world.transport.edges:
        assert edge.geometry[0] == node_by_id[edge.from_node_id].position
        assert edge.geometry[-1] == node_by_id[edge.to_node_id].position
        neighbors[edge.from_node_id].add(edge.to_node_id)
        if edge.bidirectional:
            neighbors[edge.to_node_id].add(edge.from_node_id)

    reached = {"node-city-north-apartments"}
    pending = deque(reached)
    while pending:
        node_id = pending.popleft()
        for neighbor in neighbors[node_id] - reached:
            reached.add(neighbor)
            pending.append(neighbor)
    assert reached == set(node_by_id)


def test_open_city_runs_one_scripted_controller_tick_and_projects_state() -> None:
    turns = tuple(_wait_turn(index) for index in range(4))
    _, _, runner = _prepared_runner(
        model_client=ScriptedModelClient(turns)
    )

    runner.run_for(1)

    committed = [
        event
        for event in runner.events.events
        if event.event_type == "tool.committed"
    ]
    assert {
        (event.agent_id, event.payload["tool_name"])
        for event in committed
    } == {(character_id, "wait") for character_id in CHARACTER_ASSIGNMENTS}

    bootstrap = build_ui_bootstrap(runner)
    runtime = build_runtime_snapshot(runner)
    alex = build_agent_snapshot(runner, "city-alex")
    assert bootstrap["city"]["id"] == "city-open-day"
    assert len(bootstrap["city"]["city_zones"]) == 3
    assert len(bootstrap["city"]["buildings"]) == 10
    assert len(bootstrap["city"]["rooms"]) == 10
    assert "local_maps" not in bootstrap["city"]
    assert {
        state["id"] for state in runtime["world"]["vehicle_states"]
    } == {"vehicle-city-car", "vehicle-city-share-cycle"}
    assert len(runtime["world"]["transaction_point_states"]) == 5
    assert alex["possessions"] == [
        {
            "item_id": "city-credit",
            "name": "City credit",
            "unit": "credit",
            "quantity": 40,
        }
    ]

    known_ids = {
        destination.id
        for destination in runner.registry.get_resource(
            NavigationService
        ).known_topology.destinations("city-alex")
    }
    assert {
        "building-city-north-apartments",
        "building-city-north-cafe",
        "building-city-central-market",
        "building-city-central-transit",
        "building-city-east-civic",
    } <= known_ids
    assert "building-city-east-market" not in known_ids
