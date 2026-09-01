from pathlib import Path

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
    PlanComponent,
    PossessionsComponent,
    SpatialLocationComponent,
)
from stage0_sim.domain.economy import TransactionPointRegistry

ROOT = Path(__file__).parents[1]
SCENARIO_PATH = ROOT / "scenarios" / "greyford-rivermarket-exchange.json"
CHARACTER_ID = "character-greyford-rivermarket-shopper"
POINT_ID = "transaction-point-greyford-rivermarket-checkout"


def _load_scenario() -> ScenarioDefinition:
    return load_and_resolve_scenario(
        SCENARIO_PATH,
        FileSystemElementLibrary(ROOT / "elements"),
    ).scenario


def test_rivermarket_exchange_is_complete_and_reproducible() -> None:
    scenario = _load_scenario()
    first = create_runner(scenario, run_id="rivermarket-first")
    second = create_runner(scenario, run_id="rivermarket-second")

    first.run_for(500)
    second.run_for(500)

    possessions = first.registry.get_component(
        CHARACTER_ID, PossessionsComponent
    )
    point_state = first.registry.get_resource(TransactionPointRegistry).state(
        POINT_ID
    )
    location = first.registry.get_component(
        CHARACTER_ID, SpatialLocationComponent
    ).location
    plan = first.registry.get_component(CHARACTER_ID, PlanComponent)
    assert possessions.holdings == {"rivermarket-packed-lunch": 1}
    assert point_state.holdings == {
        "greyford-cent": 5475,
        "returnable-glass-bottle": 1,
        "rivermarket-packed-lunch": 5,
    }
    assert location.place_id == (
        "building-greyford-rivermarket-grocer-demo.interior"
    )
    assert plan.current is None
    assert plan.queue == []

    transaction_events = [
        event
        for event in first.events.events
        if event.event_type.startswith("transaction.")
    ]
    npc_ids = tuple(first.registry.query_entities(NpcComponent))
    assert len(npc_ids) == 1
    assert first.registry.get_component(
        npc_ids[0], NpcComponent
    ).role_id == "rivermarket-cashier"
    assert all(
        event.payload.get("operator_id") == npc_ids[0]
        for event in transaction_events
        if event.event_type
        in {
            "transaction.started",
            "transaction.progressed",
            "transaction.completed",
        }
    )
    assert [
        (event.event_type, event.payload.get("offer_id"))
        for event in transaction_events
        if event.event_type in {"transaction.started", "transaction.completed"}
    ] == [
        ("transaction.started", "redeem-returnable-bottle"),
        ("transaction.completed", "redeem-returnable-bottle"),
        ("transaction.started", "buy-packed-lunch"),
        ("transaction.completed", "buy-packed-lunch"),
    ]
    assert not any(
        event.event_type in {
            "navigation.failed",
            "plan.action_failed",
            "transaction.failed",
            "transaction.cancelled",
        }
        for event in first.events.events
    )
    assert [event.canonical_dict() for event in first.events.events] == [
        event.canonical_dict() for event in second.events.events
    ]


def test_rivermarket_state_is_projected_to_telemetry_and_building_api() -> None:
    scenario = _load_scenario()
    runner = create_runner(scenario)
    bootstrap = build_ui_bootstrap(runner)
    runtime = build_runtime_snapshot(runner)
    agent = build_agent_snapshot(runner, CHARACTER_ID)

    assert {item["id"] for item in bootstrap["item_catalog"]} == {
        "greyford-cent",
        "returnable-glass-bottle",
        "rivermarket-packed-lunch",
    }
    point_state = next(
        state
        for state in runtime["world"]["transaction_point_states"]
        if state["id"] == POINT_ID
    )
    assert point_state["id"] == POINT_ID
    assert point_state["holdings"] == {
        "greyford-cent": 5000,
        "rivermarket-packed-lunch": 6,
    }
    assert point_state["available"] is True
    assert point_state["operation"] == "STAFFED"
    assert point_state["staffing"] == {
        "npc_id": None,
        "role_id": "rivermarket-cashier",
    }
    assert point_state["queued_request_count"] == 0
    assert agent["possessions"] == [
        {
            "item_id": "greyford-cent",
            "name": "Greyford cent",
            "unit": "minor currency unit",
            "quantity": 475,
        },
        {
            "item_id": "returnable-glass-bottle",
            "name": "Empty returnable glass bottle",
            "unit": "bottle",
            "quantity": 1,
        },
    ]

    with TestClient(app) as client:
        source = load_and_resolve_scenario(
            SCENARIO_PATH,
            FileSystemElementLibrary(ROOT / "elements"),
        ).source
        composed = client.post(
            "/simulation/scenarios",
            json={
                "scenario": source.model_dump(mode="json"),
                "character_assignments": {},
            },
        )
        assert composed.status_code == 201
        unavailable_model = client.post(
            "/simulation/runs",
            json={
                "scenario_id": composed.json()["scenario_id"],
                "realtime": False,
                "npc_control_mode": "model",
            },
        )
        assert unavailable_model.status_code == 422
        assert "model NPC control" in unavailable_model.json()["detail"]
        run = client.post(
            "/simulation/runs",
            json={
                "scenario_id": composed.json()["scenario_id"],
                "realtime": False,
            },
        )
        assert run.status_code == 201
        assert run.json()["npc_control_mode"] == "deterministic"
        assert run.json()["effective_npc_control_mode"] == "deterministic"
        building = client.get(
            "/simulation/runs/"
            f"{run.json()['run_id']}/world/buildings/"
            "building-greyford-rivermarket-grocer-demo"
        )
        assert building.status_code == 200
        point = building.json()["building"]["rooms"][0]["map"][
            "transaction_points"
        ][0]
        assert point["id"] == POINT_ID
        assert point["runtime"] == {
            "holdings": {
                "greyford-cent": 5000,
                "rivermarket-packed-lunch": 6,
            },
            "available": True,
            "queued_request_count": 0,
            "npc_id": None,
        }
