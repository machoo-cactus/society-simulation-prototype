from collections import deque
from pathlib import Path

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.application.characters import prepare_scenario
from stage0_sim.application.information import InformationStore
from stage0_sim.application.navigation import NavigationService
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.application.scenario_resolution import load_and_resolve_scenario
from stage0_sim.domain.components import (
    HomeostasisComponent,
    NpcComponent,
    PositionComponent,
    SpatialLocationComponent,
)
from stage0_sim.domain.information import character_information_namespace_id
from stage0_sim.domain.world import CityWorld, TravelMode, find_transport_route

ROOT = Path(__file__).parents[1]
SCENARIO_PATH = ROOT / "scenarios" / "greyford-office-evening.json"
CHARACTER_ID = "character-greyford-mara-ellison"
DINNER_STATION_ID = "station-greyford-juniper-window-table"
HOME_STATION_ID = "station-greyford-rowan-home-sofa"

FOCUS_BUILDINGS = {
    "building-greyford-civic-analytics-office",
    "building-greyford-juniper-kitchen",
    "building-greyford-quayline-cafe",
    "building-greyford-rivermarket-grocer",
    "building-greyford-northbank-pharmacy",
    "building-greyford-meridian-clinic",
    "building-greyford-foundry-fitness",
    "building-greyford-alder-hotel",
    "building-greyford-harcourt-residences",
    "building-greyford-mobility-centre",
    "building-greyford-quayside-metro-station",
    "building-greyford-river-library",
    "building-greyford-spruce-childcare",
    "building-greyford-lantern-coworking",
}
FOCUS_TRANSACTION_POINTS = {
    "transaction-point-greyford-quayside-ticket-machine",
    "transaction-point-greyford-quayline-counter",
    "transaction-point-greyford-rivermarket-checkout",
    "transaction-point-greyford-northbank-dispensary",
    "transaction-point-greyford-meridian-check-in",
    "transaction-point-greyford-alder-front-desk",
    "transaction-point-greyford-mobility-service",
}


def _load_scenario() -> ScenarioDefinition:
    return load_and_resolve_scenario(
        SCENARIO_PATH,
        FileSystemElementLibrary(ROOT / "elements"),
    ).scenario


def _runner(*, run_id: str):
    scenario = _load_scenario()
    prepared = prepare_scenario(
        scenario,
        FileSystemCharacterLibrary(ROOT / "characters"),
    )
    return create_runner(
        scenario,
        resolved_characters=prepared.runtime_characters(),
        run_id=run_id,
    )

REPRESENTATIVE_INFRASTRUCTURE = {
    "building-greyford-provincial-assembly",
    "building-greyford-city-hall",
    "building-greyford-general-hospital",
    "building-greyford-state-university",
    "building-greyford-central-interchange",
    "building-greyford-intermodal-logistics",
    "building-greyford-airport-terminal",
    "building-greyford-capital-stadium",
    "building-greyford-performing-arts",
    "building-greyford-water-treatment",
    "building-greyford-rowan-home",
    "building-greyford-northgate-apartments",
    "building-greyford-lakeside-townhouses",
}


def test_greyford_materializes_city_neighborhood_and_character_knowledge() -> None:
    scenario = _load_scenario()
    world = scenario.world
    assert world is not None
    assert world.type == "city"
    assert world.city.bounds_meters.max_x - world.city.bounds_meters.min_x == 30000
    assert world.city.bounds_meters.max_y - world.city.bounds_meters.min_y == 28000
    assert len(world.districts) == 9
    assert len(world.buildings) == 35
    assert len(world.outdoor_places) == 13
    assert len(world.rooms) == 35
    assert len(world.transport.nodes) == 79
    assert len(world.transport.edges) == 91

    building_ids = {building.id for building in world.buildings}
    assert building_ids >= FOCUS_BUILDINGS
    assert building_ids >= REPRESENTATIVE_INFRASTRUCTURE
    assert {
        room.building_id for room in world.rooms
    } == building_ids
    assert {item.id for item in scenario.items} >= {
        "greyford-cent",
        "returnable-glass-bottle",
        "rivermarket-grocery-bag",
        "greyford-transit-ticket",
    }
    assert {role.id for role in scenario.npc_roles} == {
        "alder-receptionist",
        "meridian-receptionist",
        "northbank-pharmacy-clerk",
        "quayside-cashier",
        "riverfront-service-clerk",
    }
    focus_maps = [
        room.world
        for room in world.rooms
        if room.building_id in FOCUS_BUILDINGS
    ]
    assert {
        point.id
        for local_map in focus_maps
        for point in local_map.transaction_points
    } == FOCUS_TRANSACTION_POINTS
    operations = {
        point.id: point.operation.value
        for local_map in focus_maps
        for point in local_map.transaction_points
    }
    assert operations[
        "transaction-point-greyford-quayside-ticket-machine"
    ] == "AUTOMATED"
    assert all(
        operation == "STAFFED"
        for point_id, operation in operations.items()
        if point_id
        != "transaction-point-greyford-quayside-ticket-machine"
    )
    assert all(
        quantity >= 0
        for local_map in focus_maps
        for point in local_map.transaction_points
        for quantity in point.holdings.values()
    )
    assert not any(
        zone.name.endswith(("Entry and Reception", "Main Interior"))
        for local_map in focus_maps
        for zone in local_map.zones
    )

    node_by_id = {node.id: node for node in world.transport.nodes}
    for building in world.buildings:
        assert building.entrances
        for entrance in building.entrances:
            assert entrance.neighborhood_node_id in node_by_id
            room = next(
                room
                for room in world.rooms
                if room.id == entrance.room_id
            )
            assert 0 <= entrance.local_coordinate.x < room.world.width
            assert 0 <= entrance.local_coordinate.y < room.world.height

    room_worlds = {
        room.building_id: room.world for room in world.rooms
    }
    office = room_worlds["building-greyford-civic-analytics-office"]
    restaurant = room_worlds["building-greyford-juniper-kitchen"]
    transit = room_worlds["building-greyford-quayside-metro-station"]
    home = room_worlds["building-greyford-rowan-home"]
    assert (office.width, office.height, len(office.zones), len(office.stations)) == (
        18,
        12,
        4,
        3,
    )
    assert (
        restaurant.width,
        restaurant.height,
        len(restaurant.zones),
        len(restaurant.stations),
    ) == (14, 9, 3, 2)
    assert (transit.width, transit.height, len(transit.zones)) == (16, 9, 3)
    assert (home.width, home.height, len(home.zones), len(home.stations)) == (
        16,
        11,
        5,
        4,
    )

    all_modes = {
        mode
        for edge in world.transport.edges
        for mode in edge.allowed_modes
    }
    assert all_modes == {
        TravelMode.WALK,
        TravelMode.CYCLE,
        TravelMode.CAR,
        TravelMode.METRO,
    }
    for edge in world.transport.edges:
        assert edge.geometry[0] == node_by_id[edge.from_node_id].position
        assert edge.geometry[-1] == node_by_id[edge.to_node_id].position

    neighbors: dict[str, set[str]] = {
        node_id: set() for node_id in node_by_id
    }
    for edge in world.transport.edges:
        neighbors[edge.from_node_id].add(edge.to_node_id)
        if edge.bidirectional:
            neighbors[edge.to_node_id].add(edge.from_node_id)
    reached = {"node-greyford-office-entrance"}
    pending = deque(reached)
    while pending:
        node_id = pending.popleft()
        for neighbor in neighbors[node_id] - reached:
            reached.add(neighbor)
            pending.append(neighbor)
    assert reached == set(node_by_id)

    profile = FileSystemCharacterLibrary(ROOT / "characters").get(CHARACTER_ID)
    profile_payload = profile.model_dump(mode="json")
    assert profile_payload["identity"]["display_name"] == "Mara Ellison"
    assert profile_payload["personal_dossier"]["research_annotations"][
        "nested_extension_demo"
    ]["observations"][0]["domain"] == "mobility"
    assert profile_payload["presentation"]["context_variations"][0] == (
        "uses relaxed technical layers while travelling or hiking"
    )
    entity = scenario.entities[0]
    assert entity.components["planner"]["current_priorities"][2] == (
        "take the metro home"
    )

    runner = _runner(run_id="greyford-content")
    assert not tuple(runner.registry.query_entities(NpcComponent))
    city = runner.registry.get_resource(CityWorld)
    route = find_transport_route(
        city,
        "node-greyford-office-entrance",
        "node-greyford-rowan-home-entrance",
        TravelMode.METRO,
    )
    assert route is not None
    assert TravelMode.WALK in {leg.mode for leg in route}
    assert TravelMode.METRO in {leg.mode for leg in route}

    known_ids = {
        destination.id
        for destination in runner.registry.get_resource(
            NavigationService
        ).known_topology.destinations(CHARACTER_ID)
    }
    assert {
        DINNER_STATION_ID,
        HOME_STATION_ID,
        "building-greyford-juniper-kitchen",
        "building-greyford-rowan-home",
    } <= known_ids
    assert "building-greyford-provincial-assembly" not in known_ids
    assert "building-greyford-airport-terminal" not in known_ids

    documents = runner.registry.get_resource(InformationStore).documents(
        namespace_id=character_information_namespace_id(CHARACTER_ID)
    )
    documents_by_id = {document.id: document for document in documents}
    assert {
        "info-greyford-city-public-overview",
        "info-greyford-quayside-neighborhood-guide",
        "info-greyford-office-to-juniper-route",
        "info-greyford-evening-home-commute",
        "info-greyford-personal-evening-constraints",
    } <= set(documents_by_id)
    assert documents_by_id[
        "info-greyford-city-public-overview"
    ].content["demographics"]["metro_population"] == 1180000
    assert documents_by_id[
        "info-greyford-evening-home-commute"
    ].content["transition_ids"][-1] == "entrance-greyford-rowan-home-main"


def test_greyford_evening_finishes_dinner_before_arriving_home() -> None:
    runner = _runner(run_id="greyford-evening-itinerary")

    runner.run_for(3000)

    dinner_arrival = next(
        event
        for event in runner.events.events
        if event.event_type == "navigation.arrived"
        and event.payload["target_id"] == DINNER_STATION_ID
    )
    dinner_completed = next(
        event
        for event in runner.events.events
        if event.event_type == "affordance.completed"
        and event.payload["station_id"] == DINNER_STATION_ID
        and event.payload["action"] == "EAT"
    )
    home_arrival = next(
        event
        for event in runner.events.events
        if event.event_type == "navigation.arrived"
        and event.payload["target_id"] == HOME_STATION_ID
    )
    assert dinner_arrival.simulation_tick < dinner_completed.simulation_tick
    assert dinner_completed.simulation_tick < home_arrival.simulation_tick
    assert dinner_completed.payload["duration"] == 600
    assert home_arrival.simulation_tick == 2830

    home_route = next(
        event
        for event in runner.events.events
        if event.event_type == "travel.route_planned"
        and event.payload["destination_id"] == "building-greyford-rowan-home"
    )
    assert [leg["edge_id"] for leg in home_route.payload["legs"]] == [
        "edge-greyford-juniper-spur",
        "edge-greyford-quay-middle-2",
        "edge-greyford-quay-centre-north-link",
        "edge-greyford-metro-quayside-civic",
        "edge-greyford-metro-civic-westborough",
        "edge-greyford-west-metro-market",
        "edge-greyford-west-market-rowan",
        "edge-greyford-rowan-home-spur",
    ]
    assert [leg["mode"] for leg in home_route.payload["legs"]] == [
        "WALK",
        "WALK",
        "WALK",
        "METRO",
        "METRO",
        "WALK",
        "WALK",
        "WALK",
    ]

    location = runner.registry.get_component(
        CHARACTER_ID, SpatialLocationComponent
    ).location
    position = runner.registry.get_component(
        CHARACTER_ID, PositionComponent
    ).coordinate
    homeostasis = runner.registry.get_component(
        CHARACTER_ID, HomeostasisComponent
    )
    assert location.place_id == "building-greyford-rowan-home.interior"
    assert (position.x, position.y) == (12, 7)
    assert homeostasis.satiety > 90
    assert homeostasis.energy > 70
    assert homeostasis.stress < 30

    failure_types = {
        "navigation.failed",
        "plan.action_failed",
        "travel.route_failed",
        "travel.interrupted",
        "affordance.failed",
        "affordance.cancelled",
        "system1.blocked",
    }
    assert not any(
        event.event_type in failure_types
        and event.simulation_tick <= home_arrival.simulation_tick
        for event in runner.events.events
    )
