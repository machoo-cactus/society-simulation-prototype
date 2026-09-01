from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.api.app import app
from stage0_sim.application.characters import prepare_scenario
from stage0_sim.application.information import InformationStore
from stage0_sim.application.scenario import (
    ScenarioDefinition,
    create_runner,
)
from stage0_sim.application.scenario_resolution import load_and_resolve_scenario
from stage0_sim.application.telemetry import build_ui_bootstrap
from stage0_sim.domain.components import (
    HomeostasisComponent,
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

SCENARIO_PATH = (
    Path(__file__).parents[1] / "scenarios" / "sparse-city-car-demo.json"
)
CHARACTER_DIRECTORY = Path(__file__).parents[1] / "characters"
ELEMENT_DIRECTORY = Path(__file__).parents[1] / "elements"


def load_city_scenario() -> ScenarioDefinition:
    return load_and_resolve_scenario(
        SCENARIO_PATH,
        FileSystemElementLibrary(ELEMENT_DIRECTORY),
    ).scenario


def create_city_runner(
    scenario: ScenarioDefinition | None = None,
    *,
    run_id: str | None = None,
):
    scenario = scenario or load_city_scenario()
    prepared = prepare_scenario(
        scenario,
        FileSystemCharacterLibrary(CHARACTER_DIRECTORY),
    )
    return create_runner(
        scenario,
        run_id=run_id,
        resolved_characters=prepared.runtime_characters(),
    )


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
            "entrance entrance-home references unknown node",
        ),
        (
            [],
            "entrance_coordinate",
            {"x": 99, "y": 0},
            "entrance entrance-home is not on a walkable room tile",
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


def test_sparse_route_is_deterministic_and_uses_access_legs() -> None:
    runner = create_city_runner()
    city = runner.registry.get_resource(CityWorld)

    first = find_transport_route(
        city,
        "node-home-entrance",
        "node-office-entrance",
        TravelMode.CAR,
    )
    second = find_transport_route(
        city,
        "node-home-entrance",
        "node-office-entrance",
        TravelMode.CAR,
    )

    assert first == second
    assert first is not None
    assert [leg.mode for leg in first] == [
        TravelMode.WALK,
        TravelMode.CAR,
        TravelMode.CAR,
        TravelMode.WALK,
    ]


def test_scripted_car_trip_arrives_without_teleporting() -> None:
    runner = create_city_runner(run_id="city-trip")

    runner.run_for(575)
    before_component = runner.registry.get_component(
        "agent-001", SpatialLocationComponent
    )
    before = before_component.location
    assert before.scale is SpatialScale.CITY
    assert before.edge_id is not None
    assert before_component.locator is not None
    assert before_component.locator.space_id == "demo-city"
    assert before_component.locator.local_reference == {
        "kind": "edge",
        "edge_id": before.edge_id,
        "progress": before.edge_progress,
    }

    runner.run_for(2)
    after_component = runner.registry.get_component(
        "agent-001", SpatialLocationComponent
    )
    after = after_component.location
    travel = runner.registry.get_component("agent-001", TravelComponent)

    assert after.scale is SpatialScale.BUILDING
    assert after.place_id == "building-office.interior"
    assert after_component.locator is not None
    assert after_component.locator.space_id == "building-office.interior"
    assert travel.status is TravelStatus.ARRIVED
    event_types = [event.event_type for event in runner.events.events]
    assert "building.exited" in event_types
    assert "vehicle.boarded" in event_types
    assert "vehicle.moved" in event_types
    assert "vehicle.exited" in event_types
    assert "travel.arrived" in event_types
    learned = next(
        document
        for document in runner.registry.get_resource(
            InformationStore
        ).documents(
            namespace_id=character_information_namespace_id("agent-001"),
            kinds=("knowledge.route",),
        )
        if document.source.type == "DIRECT_EXPERIENCE"
    )
    arrived = next(
        event
        for event in runner.events.events
        if event.event_type == "travel.arrived"
    )
    assert learned.content["destination_id"] == "building-office"
    assert learned.content["locator"] == {
        "space_id": "building-office.interior",
        "local_reference": {"kind": "coordinate", "x": 0, "y": 1},
    }
    assert learned.content["transition_ids"] == [
        *(leg.edge_id for leg in travel.route),
        travel.destination_entrance_id,
    ]
    assert learned.source.reference_ids == (
        arrived.event_id,
        arrived.correlation_id,
    )


def test_city_bootstrap_omits_unrequested_local_map_grids() -> None:
    runner = create_city_runner()

    bootstrap = build_ui_bootstrap(runner)

    assert bootstrap["city"] is not None
    assert "local_maps" not in bootstrap["city"]
    assert [zone["id"] for zone in bootstrap["city"]["city_zones"]] == [
        "district-west",
        "district-central",
        "district-east",
    ]
    assert {
        room["id"] for room in bootstrap["city"]["rooms"]
    } == {"building-home.interior", "building-office.interior"}
    assert bootstrap["world"]["width"] == 3


def test_city_and_building_read_apis() -> None:
    scenario = load_and_resolve_scenario(
        SCENARIO_PATH,
        FileSystemElementLibrary(ELEMENT_DIRECTORY),
    ).source.model_dump(mode="json")
    with TestClient(app) as client:
        scenario_id = client.post(
            "/simulation/scenarios",
            json={"scenario": scenario, "character_assignments": {}},
        ).json()["scenario_id"]
        run_id = client.post(
            "/simulation/runs",
            json={"scenario_id": scenario_id, "realtime": False},
        ).json()["run_id"]
        city = client.get(f"/simulation/runs/{run_id}/world/city")
        building = client.get(
            f"/simulation/runs/{run_id}/world/buildings/building-office"
        )
        city_zone = client.get(
            f"/simulation/runs/{run_id}/world/city-zones/district-east"
        )
        room = client.get(
            f"/simulation/runs/{run_id}/world/rooms/building-office.interior"
        )
        neighborhood = client.get(
            f"/simulation/runs/{run_id}/world/neighborhoods/building-office"
        )
        spatial = client.get(
            f"/simulation/runs/{run_id}/agents/agent-001/spatial-context"
        )

    assert city.status_code == 200
    assert city.json()["city"]["id"] == "demo-city"
    assert building.status_code == 200
    assert building.json()["building"]["rooms"][0]["map"]["width"] == 3
    assert building.json()["building"]["rooms"][0]["id"] == (
        "building-office.interior"
    )
    assert city_zone.status_code == 200
    assert city_zone.json()["city_zone"]["buildings"][0]["id"] == (
        "building-office"
    )
    assert room.status_code == 200
    assert room.json()["room"]["building_id"] == "building-office"
    assert neighborhood.status_code == 200
    assert neighborhood.json()["edges"]
    assert spatial.json()["spatial_location"]["place_id"] == (
        "building-home.interior"
    )
    assert spatial.json()["spatial_location"]["building_id"] == "building-home"


def test_system1_interrupts_city_travel_at_next_safe_node() -> None:
    runner = create_city_runner(run_id="interrupt")
    state = runner.registry.get_component(
        "agent-001", HomeostasisComponent
    )

    runner.run_for(1)
    state.satiety = 0
    runner.run_for(130)

    location = runner.registry.get_component(
        "agent-001", SpatialLocationComponent
    ).location
    travel = runner.registry.get_component("agent-001", TravelComponent)

    assert travel.status is TravelStatus.CANCELLED
    assert location.scale is SpatialScale.CITY
    assert location.network_node_id == "node-home-parking"
    interrupted = next(
        event
        for event in runner.events.events
        if event.event_type == "travel.interrupted"
    )
    assert interrupted.payload["safe_node_id"] == "node-home-parking"


@pytest.mark.parametrize(
    ("mode", "vehicle_type", "expected_event"),
    [
        ("CYCLE", "CYCLE", "vehicle.boarded"),
        ("METRO", None, "metro.boarded"),
    ],
)
def test_cycle_and_direct_metro_routes(
    mode: str,
    vehicle_type: str | None,
    expected_event: str,
) -> None:
    payload = load_city_scenario().model_dump(mode="json")
    for edge in payload["world"]["transport"]["edges"]:
        if edge["id"].startswith("road-"):
            edge["allowed_modes"] = [mode]
    payload["entities"][0]["components"]["plan"]["queue"][0]["mode"] = mode
    if vehicle_type is None:
        payload["world"]["transport"]["vehicles"] = []
    else:
        vehicle = payload["world"]["transport"]["vehicles"][0]
        vehicle["type"] = vehicle_type
        vehicle["name"] = "Demo Bicycle"
    runner = create_city_runner(ScenarioDefinition.model_validate(payload))

    runner.run_for(1400)

    location = runner.registry.get_component(
        "agent-001", SpatialLocationComponent
    ).location
    assert location.place_id == "building-office.interior"
    assert any(
        event.event_type == expected_event
        for event in runner.events.events
    )
