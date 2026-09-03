from fastapi.testclient import TestClient

from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.api.app import app
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.application.scenario_resolution import load_and_resolve_scenario
from stage0_sim.application.telemetry import (
    build_agent_snapshot,
    build_runtime_snapshot,
    build_ui_bootstrap,
)
from stage0_sim.domain.components import (
    NpcComponent,
    OpenableComponent,
    PhysicalInteractionRegistry,
    PhysicalObjectIdentityComponent,
    PlanComponent,
    PossessionsComponent,
    SpatialLocationComponent,
)
from stage0_sim.domain.economy import TransactionPointRegistry
from stage0_sim.domain.world import CityWorld
from tests.helpers.paths import CATALOG_ELEMENTS, CATALOG_SCENARIOS

SCENARIO_PATH = CATALOG_SCENARIOS / "neighborhood-errand.json"
CHARACTER_ID = "resident-shopper"
MARKET_ID = "building-riverbend-market"
MARKET_POINT_ID = f"{MARKET_ID}.interior.checkout"
CAFE_ID = "building-riverbend-cafe"
CAFE_POINT_ID = f"{CAFE_ID}.interior.counter"


def _load_scenario() -> ScenarioDefinition:
    return load_and_resolve_scenario(
        SCENARIO_PATH,
        FileSystemElementLibrary(CATALOG_ELEMENTS),
    ).scenario


def _completed_runner(*, run_id: str):
    runner = create_runner(_load_scenario(), run_id=run_id)
    runner.run_for(340)
    return runner


def test_neighborhood_errand_materializes_reusable_physical_places() -> None:
    scenario = _load_scenario()
    assert scenario.schema_version == 8
    assert scenario.world is not None
    assert [building.id for building in scenario.world.buildings] == [
        "building-riverbend-home",
        MARKET_ID,
        CAFE_ID,
    ]
    assert {
        world_object.definition_id for world_object in scenario.world.objects
    } >= {
        "common.exterior-door",
        "common.interior-door",
        "common.phone",
        "hospitality.cafe-counter",
        "residential.sofa",
        "retail.market-checkout",
        "retail.shelf",
    }

    runner = create_runner(scenario)
    city = runner.registry.get_resource(CityWorld)
    interactions = runner.registry.get_resource(PhysicalInteractionRegistry)
    for building in city.buildings:
        assert len(building.entrances) == 1
        door_id = interactions.door_for_transition(building.entrances[0].id)
        assert door_id is not None
        assert runner.registry.get_component(
            door_id, PhysicalObjectIdentityComponent
        ).definition_id == "common.exterior-door"
        door = runner.registry.get_component(door_id, OpenableComponent)
        assert not door.is_open
        assert not door.is_locked


def test_neighborhood_errand_completes_transactions_and_is_reproducible() -> None:
    first = _completed_runner(run_id="neighborhood-errand-first")
    second = _completed_runner(run_id="neighborhood-errand-second")

    possessions = first.registry.get_component(
        CHARACTER_ID, PossessionsComponent
    )
    location = first.registry.get_component(
        CHARACTER_ID, SpatialLocationComponent
    ).location
    plan = first.registry.get_component(CHARACTER_ID, PlanComponent)
    point_registry = first.registry.get_resource(TransactionPointRegistry)

    assert possessions.holdings == {
        "filter-coffee": 1,
        "packed-lunch": 1,
    }
    assert location.place_id == f"{CAFE_ID}.interior"
    assert plan.current is None
    assert plan.queue == []
    assert point_registry.state(MARKET_POINT_ID).holdings == {
        "bread-loaf": 16,
        "city-credit": 5475,
        "health-kit": 8,
        "local-apples": 20,
        "packed-lunch": 11,
        "returnable-bottle": 1,
    }
    assert point_registry.state(CAFE_POINT_ID).holdings == {
        "city-credit": 4,
        "filter-coffee": 39,
        "hot-tea": 30,
        "morning-bun": 24,
        "prepared-meal": 20,
    }

    routes = [
        event
        for event in first.events.events
        if event.event_type == "travel.route_planned"
    ]
    assert [event.payload["destination_id"] for event in routes] == [
        MARKET_ID,
        CAFE_ID,
    ]
    assert {
        leg["mode"]
        for event in routes
        for leg in event.payload["legs"]
    } == {"WALK"}

    transactions = [
        event
        for event in first.events.events
        if event.event_type
        in {"transaction.started", "transaction.completed"}
    ]
    assert [
        (event.event_type, event.payload["offer_id"])
        for event in transactions
    ] == [
        ("transaction.started", "redeem-returnable-bottle"),
        ("transaction.completed", "redeem-returnable-bottle"),
        ("transaction.started", "buy-packed-lunch"),
        ("transaction.completed", "buy-packed-lunch"),
        ("transaction.started", "buy-filter-coffee"),
        ("transaction.completed", "buy-filter-coffee"),
    ]
    npc_ids = tuple(first.registry.query_entities(NpcComponent))
    assert len(npc_ids) == 1
    assert first.registry.get_component(
        npc_ids[0], NpcComponent
    ).role_id == "hospitality.barista"
    cafe_transactions = [
        event
        for event in transactions
        if event.payload["point_id"] == CAFE_POINT_ID
    ]
    assert all(
        event.payload["operator_id"] == npc_ids[0]
        for event in cafe_transactions
    )
    assert not any(
        event.event_type
        in {
            "navigation.failed",
            "action.failed",
            "transaction.failed",
            "transaction.cancelled",
        }
        for event in first.events.events
    )
    assert [event.canonical_dict() for event in first.events.events] == [
        event.canonical_dict() for event in second.events.events
    ]


def test_neighborhood_errand_projects_transactions_to_telemetry_and_api() -> None:
    resolved = load_and_resolve_scenario(
        SCENARIO_PATH,
        FileSystemElementLibrary(CATALOG_ELEMENTS),
    )
    runner = create_runner(resolved.scenario)
    bootstrap = build_ui_bootstrap(runner)
    runtime = build_runtime_snapshot(runner)
    agent = build_agent_snapshot(runner, CHARACTER_ID)

    assert {item["id"] for item in bootstrap["item_catalog"]} >= {
        "city-credit",
        "filter-coffee",
        "packed-lunch",
        "returnable-bottle",
    }
    point_states = {
        state["id"]: state
        for state in runtime["world"]["transaction_point_states"]
    }
    assert point_states[MARKET_POINT_ID]["operation"] == "AUTOMATED"
    assert point_states[MARKET_POINT_ID]["staffing"] is None
    assert point_states[CAFE_POINT_ID]["operation"] == "STAFFED"
    assert point_states[CAFE_POINT_ID]["staffing"] == {
        "npc_id": None,
        "role_id": "hospitality.barista",
    }
    assert agent["possessions"] == [
        {
            "item_id": "city-credit",
            "name": "City credit",
            "unit": "credit",
            "quantity": 479,
        },
        {
            "item_id": "returnable-bottle",
            "name": "Empty returnable bottle",
            "unit": "bottle",
            "quantity": 1,
        },
    ]

    with TestClient(app) as client:
        composed = client.post(
            "/simulation/scenarios",
            json={
                "scenario": resolved.source.model_dump(mode="json"),
                "character_assignments": {},
            },
        )
        assert composed.status_code == 201
        run = client.post(
            "/simulation/runs",
            json={
                "scenario_id": composed.json()["scenario_id"],
                "realtime": False,
            },
        )
        assert run.status_code == 201
        assert run.json()["effective_npc_control_mode"] == "deterministic"

        market = client.get(
            f"/simulation/runs/{run.json()['run_id']}/world/buildings/{MARKET_ID}"
        )
        cafe = client.get(
            f"/simulation/runs/{run.json()['run_id']}/world/buildings/{CAFE_ID}"
        )
        assert market.status_code == 200
        assert cafe.status_code == 200
        market_point = market.json()["building"]["rooms"][0]["map"][
            "transaction_points"
        ][0]
        cafe_point = cafe.json()["building"]["rooms"][0]["map"][
            "transaction_points"
        ][0]
        assert market_point["id"] == MARKET_POINT_ID
        assert market_point["runtime"]["holdings"]["packed-lunch"] == 12
        assert cafe_point["id"] == CAFE_POINT_ID
        assert cafe_point["staffing"]["role_id"] == "hospitality.barista"
