import pytest
from fastapi.testclient import TestClient

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.adapters.llm import ScriptedModelClient
from stage0_sim.api.app import app
from stage0_sim.application.characters import prepare_scenario
from stage0_sim.application.information import InformationStore
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.application.scenario_resolution import load_and_resolve_scenario
from stage0_sim.application.telemetry import build_ui_bootstrap
from stage0_sim.domain.components import (
    HomeostasisComponent,
    NavigationComponent,
    SpatialLocationComponent,
    TravelComponent,
)
from stage0_sim.domain.information import character_information_namespace_id
from stage0_sim.domain.world import (
    CityWorld,
    SpatialScale,
    TravelMode,
    TravelStatus,
    find_transport_route,
)
from tests.helpers.paths import (
    CATALOG_CHARACTERS,
    CATALOG_ELEMENTS,
    CATALOG_SCENARIOS,
)

SCENARIO_PATH = CATALOG_SCENARIOS / "open-city-day.json"
CHARACTER_ID = "city-alex"
HOME_BUILDING_ID = "building-city-north-apartments"
HOME_ROOM_ID = f"{HOME_BUILDING_ID}.interior"
HOME_NODE_ID = "node-city-north-apartments"
DESTINATION_BUILDING_ID = "building-city-north-cafe"
DESTINATION_ROOM_ID = f"{DESTINATION_BUILDING_ID}.interior"
DESTINATION_NODE_ID = "node-city-north-cafe"
DESTINATION_ENTRANCE_ID = f"{DESTINATION_BUILDING_ID}.front"
WALKING_EDGE_IDS = (
    "edge-node-city-north-apartments-node-district-city-north-hub",
    "edge-node-city-north-cafe-node-district-city-north-hub",
)


def load_city_scenario() -> ScenarioDefinition:
    return load_and_resolve_scenario(
        SCENARIO_PATH,
        FileSystemElementLibrary(CATALOG_ELEMENTS),
    ).scenario


def create_city_runner(scenario: ScenarioDefinition | None = None):
    scenario = scenario or load_city_scenario()
    prepared = prepare_scenario(
        scenario,
        FileSystemCharacterLibrary(CATALOG_CHARACTERS),
    )
    return create_runner(
        scenario,
        resolved_characters=prepared.runtime_characters(),
        model_client=ScriptedModelClient(()),
    )


def walking_trip_scenario() -> ScenarioDefinition:
    payload = load_city_scenario().model_dump(mode="json")
    payload["dt"] = 10.0
    for entity in payload["entities"]:
        entity["components"]["controller"]["enabled"] = False
        entity["components"]["plan"] = {"queue": []}
    actor = next(
        entity
        for entity in payload["entities"]
        if entity["id"] == CHARACTER_ID
    )
    actor["components"]["plan"] = {
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
        for document in actor["components"]["information"]["documents"]
        if document["content"]["destination_id"] == DESTINATION_BUILDING_ID
    )
    destination_document["content"]["transition_ids"] = [
        DESTINATION_ENTRANCE_ID,
        *WALKING_EDGE_IDS,
    ]
    return ScenarioDefinition.model_validate(payload)


def test_city_schema_and_references_are_validated() -> None:
    payload = load_city_scenario().model_dump(mode="json")
    payload["world"]["transport"]["edges"][0]["to_node_id"] = "missing"

    with pytest.raises(ValueError, match="references unknown node"):
        ScenarioDefinition.model_validate(payload)


@pytest.mark.parametrize(
    ("outdoor_places", "field", "value", "message"),
    [
        ([], "room_ids", ["missing-room"], "references an invalid room"),
        (
            None,
            "entrance_node",
            "missing-node",
            "references unknown node",
        ),
        (
            [],
            "entrance_coordinate",
            {"x": 99, "y": 0},
            "is not on a walkable room tile",
        ),
    ],
)
def test_every_building_reference_is_validated_independently(
    outdoor_places: list[object] | None,
    field: str,
    value: object,
    message: str,
) -> None:
    payload = load_city_scenario().model_dump(mode="json")
    if outdoor_places is not None:
        payload["world"]["outdoor_places"] = outdoor_places
    if field == "entrance_node":
        payload["world"]["buildings"][0]["entrances"][0][
            "neighborhood_node_id"
        ] = value
    elif field == "entrance_coordinate":
        payload["world"]["buildings"][0]["entrances"][0][
            "local_coordinate"
        ] = value
    else:
        payload["world"]["buildings"][0][field] = value

    with pytest.raises(ValueError, match=message):
        ScenarioDefinition.model_validate(payload)


def test_open_city_route_is_deterministic_and_uses_metro_access_legs() -> None:
    runner = create_city_runner()
    city = runner.registry.get_resource(CityWorld)

    first = find_transport_route(
        city,
        HOME_NODE_ID,
        "node-city-east-civic",
        TravelMode.METRO,
    )
    second = find_transport_route(
        city,
        HOME_NODE_ID,
        "node-city-east-civic",
        TravelMode.METRO,
    )

    assert first == second
    assert first is not None
    assert [leg.mode for leg in first] == [
        TravelMode.WALK,
        TravelMode.METRO,
        TravelMode.METRO,
        TravelMode.WALK,
    ]


def test_scripted_walking_trip_arrives_without_teleporting() -> None:
    runner = create_city_runner(walking_trip_scenario())

    runner.run_for(3)
    before_component = runner.registry.get_component(
        CHARACTER_ID, SpatialLocationComponent
    )
    before = before_component.location
    assert before.scale is SpatialScale.CITY
    assert before.edge_id == WALKING_EDGE_IDS[0]
    assert before_component.locator is not None
    assert before_component.locator.space_id == "city-open-day"
    assert before_component.locator.local_reference == {
        "kind": "edge",
        "edge_id": WALKING_EDGE_IDS[0],
        "progress": before.edge_progress,
    }

    runner.run_for(28)
    after_component = runner.registry.get_component(
        CHARACTER_ID, SpatialLocationComponent
    )
    travel = runner.registry.get_component(CHARACTER_ID, TravelComponent)
    navigation = runner.registry.get_component(
        CHARACTER_ID, NavigationComponent
    )

    assert after_component.location.scale is SpatialScale.BUILDING
    assert after_component.location.place_id == DESTINATION_ROOM_ID
    assert after_component.locator is not None
    assert after_component.locator.space_id == DESTINATION_ROOM_ID
    assert travel.status is TravelStatus.IDLE
    event_types = [event.event_type for event in runner.events.events]
    assert "building.exited" in event_types
    assert "travel.progressed" in event_types
    assert "building.entered" in event_types
    assert "travel.arrived" in event_types

    learned = next(
        document
        for document in runner.registry.get_resource(
            InformationStore
        ).documents(
            namespace_id=character_information_namespace_id(CHARACTER_ID),
            kinds=("knowledge.route",),
        )
        if document.source.type == "DIRECT_EXPERIENCE"
    )
    arrived = next(
        event
        for event in runner.events.events
        if event.event_type == "navigation.arrived"
        and event.agent_id == CHARACTER_ID
    )
    assert navigation.route is not None
    assert learned.content["destination_id"] == DESTINATION_BUILDING_ID
    assert learned.content["locator"] == {
        "space_id": DESTINATION_ROOM_ID,
        "local_reference": {"kind": "coordinate", "x": 4, "y": 40},
    }
    assert learned.content["transition_ids"] == [
        leg.transition_id
        for leg in navigation.route.legs
        if leg.transition_id is not None
    ]
    assert learned.source.reference_ids == (
        arrived.event_id,
        arrived.correlation_id,
    )


def test_city_bootstrap_omits_unrequested_local_map_grids() -> None:
    bootstrap = build_ui_bootstrap(create_city_runner())

    assert bootstrap["city"] is not None
    assert bootstrap["city"]["id"] == "city-open-day"
    assert "local_maps" not in bootstrap["city"]
    assert [zone["id"] for zone in bootstrap["city"]["city_zones"]] == [
        "district-city-north",
        "district-city-central",
        "district-city-east",
    ]
    assert len(bootstrap["city"]["buildings"]) == 10
    assert len(bootstrap["city"]["rooms"]) == 10


def test_city_and_building_read_apis() -> None:
    resolved = load_and_resolve_scenario(
        SCENARIO_PATH,
        FileSystemElementLibrary(CATALOG_ELEMENTS),
    )
    source = resolved.source.model_dump(mode="json")
    for entity in source["entities"]:
        entity["components"]["controller"]["enabled"] = False

    with TestClient(app) as client:
        scenario_response = client.post(
            "/simulation/scenarios",
            json={"scenario": source, "character_assignments": {}},
        )
        assert scenario_response.status_code == 201
        run_response = client.post(
            "/simulation/runs",
            json={
                "scenario_id": scenario_response.json()["scenario_id"],
                "realtime": False,
            },
        )
        assert run_response.status_code == 201
        run_id = run_response.json()["run_id"]
        city = client.get(f"/simulation/runs/{run_id}/world/city")
        building = client.get(
            f"/simulation/runs/{run_id}/world/buildings/"
            f"{DESTINATION_BUILDING_ID}"
        )
        city_zone = client.get(
            f"/simulation/runs/{run_id}/world/city-zones/"
            "district-city-north"
        )
        room = client.get(
            f"/simulation/runs/{run_id}/world/rooms/{DESTINATION_ROOM_ID}"
        )
        neighborhood = client.get(
            f"/simulation/runs/{run_id}/world/neighborhoods/"
            f"{DESTINATION_BUILDING_ID}"
        )
        spatial = client.get(
            f"/simulation/runs/{run_id}/agents/{CHARACTER_ID}/spatial-context"
        )

    assert city.status_code == 200
    assert city.json()["city"]["id"] == "city-open-day"
    assert building.status_code == 200
    assert building.json()["building"]["rooms"][0]["map"]["width"] == 11
    assert building.json()["building"]["rooms"][0]["id"] == (
        DESTINATION_ROOM_ID
    )
    assert city_zone.status_code == 200
    assert {
        item["id"] for item in city_zone.json()["city_zone"]["buildings"]
    } >= {
        HOME_BUILDING_ID,
        DESTINATION_BUILDING_ID,
        "building-city-north-library",
    }
    assert room.status_code == 200
    assert room.json()["room"]["building_id"] == DESTINATION_BUILDING_ID
    assert neighborhood.status_code == 200
    assert neighborhood.json()["edges"]
    assert spatial.status_code == 200
    assert spatial.json()["spatial_location"]["place_id"] == HOME_ROOM_ID
    assert spatial.json()["spatial_location"]["building_id"] == (
        HOME_BUILDING_ID
    )


def test_system1_interrupts_city_travel_at_next_safe_node() -> None:
    runner = create_city_runner(walking_trip_scenario())
    runner.run_for(3)
    runner.registry.get_component(
        CHARACTER_ID, HomeostasisComponent
    ).satiety = 0

    runner.run_for(30)

    location = runner.registry.get_component(
        CHARACTER_ID, SpatialLocationComponent
    ).location
    travel = runner.registry.get_component(CHARACTER_ID, TravelComponent)
    assert travel.status is TravelStatus.CANCELLED
    assert location.scale is SpatialScale.CITY
    assert location.network_node_id == "node-district-city-north-hub"
    interrupted = next(
        event
        for event in runner.events.events
        if event.event_type == "travel.interrupted"
        and event.agent_id == CHARACTER_ID
    )
    assert interrupted.payload["safe_node_id"] == (
        "node-district-city-north-hub"
    )


@pytest.mark.parametrize(
    ("origin", "destination", "mode"),
    [
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
    ],
)
def test_current_catalog_supports_each_travel_mode(
    origin: str,
    destination: str,
    mode: TravelMode,
) -> None:
    city = create_city_runner().registry.get_resource(CityWorld)
    route = find_transport_route(city, origin, destination, mode)
    assert route is not None
    assert mode in {leg.mode for leg in route}
