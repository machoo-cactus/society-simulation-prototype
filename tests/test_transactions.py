from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from stage0_sim.application.runner import RunConfiguration, SimulationRunner
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.domain.components import (
    HomeostasisComponent,
    PositionComponent,
    PossessionsComponent,
    TransactionExecutionComponent,
    TransactionRequestComponent,
)
from stage0_sim.domain.economy import (
    ItemAmount,
    TransactionOffer,
    TransactionPoint,
    TransactionPointRegistry,
    TransactionPointState,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.systems import SystemExecutor
from stage0_sim.domain.systems.transactions import TransactionExecutionSystem
from stage0_sim.domain.world import Coordinate, WorldGrid, WorldMap


def _scenario_payload() -> dict[str, Any]:
    return {
        "name": "transaction-test",
        "items": [
            {"id": "cents", "name": "Greyford cents", "unit": "minor unit"},
            {"id": "bottle", "name": "Returnable bottle", "unit": "item"},
            {"id": "meal", "name": "Packaged meal", "unit": "item"},
        ],
        "homeostasis": {
            "activity_coefficients": {
                "IDLE": {"satiety": 0, "energy": 0, "stress": 0}
            }
        },
        "world": {
            "width": 2,
            "height": 1,
            "transaction_points": [
                {
                    "id": "counter",
                    "name": "Service counter",
                    "position": {"x": 0, "y": 0},
                    "holdings": {"cents": 1000, "meal": 2},
                    "offers": [
                        {
                            "id": "redeem-bottle",
                            "name": "Redeem bottle",
                            "character_gives": [
                                {"item_id": "bottle", "quantity": 1}
                            ],
                            "character_receives": [
                                {"item_id": "cents", "quantity": 25}
                            ],
                            "duration": 1,
                        },
                        {
                            "id": "buy-meal",
                            "name": "Buy meal",
                            "character_gives": [
                                {"item_id": "cents", "quantity": 500}
                            ],
                            "character_receives": [
                                {"item_id": "meal", "quantity": 1}
                            ],
                            "duration": 1,
                        },
                    ],
                }
            ],
        },
        "entities": [
            {
                "id": "buyer",
                "components": {
                    "position": {"x": 0, "y": 0},
                    "homeostasis": {
                        "satiety": 80,
                        "energy": 80,
                        "stress": 20,
                    },
                    "possessions": {
                        "holdings": {"cents": 475, "bottle": 1}
                    },
                    "plan": {
                        "queue": [
                            {
                                "action": "TRANSACT",
                                "target": "counter",
                                "offer_id": "redeem-bottle",
                            },
                            {
                                "action": "TRANSACT",
                                "target": "counter",
                                "offer_id": "buy-meal",
                            },
                        ]
                    },
                },
            }
        ],
    }


def _direct_runner(
    *,
    agents: dict[str, tuple[Coordinate, dict[str, int], str, str]],
    point_holdings: dict[str, int] | None = None,
    available: bool = True,
    capacity: int = 1,
    duration: float = 1,
) -> SimulationRunner:
    offer = TransactionOffer(
        id="buy",
        name="Buy meal",
        character_gives=(ItemAmount("cents", 5),),
        character_receives=(ItemAmount("meal", 1),),
        duration=duration,
    )
    point = TransactionPoint(
        id="counter",
        name="Counter",
        position=Coordinate(0, 0),
        offers=(offer,),
        available=available,
        capacity=capacity,
    )
    registry = Registry()
    registry.set_resource(WorldMap(WorldGrid(2, 1), transaction_points=(point,)))
    registry.set_resource(
        TransactionPointRegistry(
            {
                "counter": TransactionPointState(
                    {"meal": 1}
                    if point_holdings is None
                    else point_holdings
                )
            }
        )
    )
    for agent_id, (position, holdings, point_id, offer_id) in agents.items():
        registry.create_entity(agent_id)
        registry.add_component(agent_id, PositionComponent(position))
        registry.add_component(agent_id, PossessionsComponent(holdings))
        registry.add_component(
            agent_id,
            TransactionRequestComponent(
                point_id=point_id,
                offer_id=offer_id,
                source="test",
            ),
        )
    systems = SystemExecutor()
    systems.add(TransactionExecutionSystem())
    return SimulationRunner(
        RunConfiguration(seed=0),
        registry=registry,
        systems=systems,
    )


def test_buy_and_redeem_plan_transfers_finite_holdings_atomically() -> None:
    runner = create_runner(
        ScenarioDefinition.model_validate(_scenario_payload()),
        run_id="exchange",
    )

    runner.run_for(3)

    possessions = runner.registry.get_component("buyer", PossessionsComponent)
    point_state = runner.registry.get_resource(TransactionPointRegistry).state(
        "counter"
    )
    assert possessions.holdings == {"meal": 1}
    assert point_state.holdings == {
        "bottle": 1,
        "cents": 1475,
        "meal": 1,
    }
    completed = [
        event
        for event in runner.events.events
        if event.event_type == "transaction.completed"
    ]
    assert [event.payload["offer_id"] for event in completed] == [
        "redeem-bottle",
        "buy-meal",
    ]
    assert completed[-1].payload["character_holdings_after"] == {"meal": 1}


@pytest.mark.parametrize(
    ("position", "holdings", "point_id", "offer_id", "point_holdings", "available", "reason"),
    [
        (
            Coordinate(1, 0),
            {"cents": 5},
            "counter",
            "buy",
            {"meal": 1},
            True,
            "character_not_at_transaction_point",
        ),
        (
            Coordinate(0, 0),
            {"cents": 4},
            "counter",
            "buy",
            {"meal": 1},
            True,
            "insufficient_character_holdings",
        ),
        (
            Coordinate(0, 0),
            {"cents": 5},
            "counter",
            "buy",
            {},
            True,
            "insufficient_transaction_point_holdings",
        ),
        (
            Coordinate(0, 0),
            {"cents": 5},
            "missing",
            "buy",
            {"meal": 1},
            True,
            "transaction_point_not_found",
        ),
        (
            Coordinate(0, 0),
            {"cents": 5},
            "counter",
            "missing",
            {"meal": 1},
            True,
            "offer_not_found",
        ),
        (
            Coordinate(0, 0),
            {"cents": 5},
            "counter",
            "buy",
            {"meal": 1},
            False,
            "transaction_point_unavailable",
        ),
    ],
)
def test_transaction_precondition_failures_do_not_transfer(
    position: Coordinate,
    holdings: dict[str, int],
    point_id: str,
    offer_id: str,
    point_holdings: dict[str, int],
    available: bool,
    reason: str,
) -> None:
    runner = _direct_runner(
        agents={"buyer": (position, holdings, point_id, offer_id)},
        point_holdings=point_holdings,
        available=available,
    )
    character_before = dict(holdings)
    point_before = dict(point_holdings)

    runner.run_for(1)

    assert runner.registry.get_component(
        "buyer", PossessionsComponent
    ).holdings == character_before
    assert runner.registry.get_resource(TransactionPointRegistry).state(
        "counter"
    ).holdings == point_before
    failure = next(
        event
        for event in runner.events.events
        if event.event_type == "transaction.failed"
    )
    assert failure.payload["reason"] == reason


def test_transaction_capacity_is_resolved_in_stable_character_order() -> None:
    runner = _direct_runner(
        agents={
            "z-buyer": (Coordinate(0, 0), {"cents": 5}, "counter", "buy"),
            "a-buyer": (Coordinate(0, 0), {"cents": 5}, "counter", "buy"),
        },
        point_holdings={"meal": 2},
        duration=2,
    )

    runner.run_for(1)

    assert runner.registry.has_component(
        "a-buyer", TransactionExecutionComponent
    )
    assert not runner.registry.has_component(
        "z-buyer", TransactionExecutionComponent
    )
    failure = next(
        event
        for event in runner.events.events
        if event.event_type == "transaction.failed"
    )
    assert failure.agent_id == "z-buyer"
    assert failure.payload["reason"] == "transaction_point_at_capacity"


def test_system1_preemption_cancels_without_partial_transfer() -> None:
    payload = _scenario_payload()
    world = payload["world"]
    assert isinstance(world, dict)
    points = world["transaction_points"]
    assert isinstance(points, list)
    points[0]["offers"][0]["duration"] = 3
    entity = payload["entities"][0]
    entity["components"]["plan"]["queue"] = [
        {
            "action": "TRANSACT",
            "target": "counter",
            "offer_id": "redeem-bottle",
        }
    ]
    runner = create_runner(ScenarioDefinition.model_validate(payload))
    possessions = runner.registry.get_component("buyer", PossessionsComponent)
    point_state = runner.registry.get_resource(TransactionPointRegistry).state(
        "counter"
    )

    runner.run_for(1)
    runner.registry.get_component("buyer", HomeostasisComponent).satiety = 0
    runner.run_for(1)

    assert possessions.holdings == {"bottle": 1, "cents": 475}
    assert point_state.holdings == {"cents": 1000, "meal": 2}
    cancellation = next(
        event
        for event in runner.events.events
        if event.event_type == "transaction.cancelled"
    )
    assert cancellation.payload["reason"] == "system1_preemption"


def test_scenario_rejects_unknown_item_references_and_duplicate_offer_items() -> None:
    unknown = deepcopy(_scenario_payload())
    unknown["world"]["transaction_points"][0]["holdings"]["unknown"] = 1
    with pytest.raises(ValidationError, match="references unknown items"):
        ScenarioDefinition.model_validate(unknown)

    duplicate = deepcopy(_scenario_payload())
    duplicate["world"]["transaction_points"][0]["offers"][0][
        "character_gives"
    ].append({"item_id": "bottle", "quantity": 1})
    with pytest.raises(ValidationError, match="duplicate character_gives"):
        ScenarioDefinition.model_validate(duplicate)
