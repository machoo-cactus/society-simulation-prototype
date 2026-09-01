from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from stage0_sim.application.agents.contracts import (
    ModelClient,
    ModelClientError,
    ModelRequest,
    ModelToolCall,
    ModelTurn,
)
from stage0_sim.application.agents.coordinator import AgentWorkCoordinator
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.domain.components import (
    NpcComponent,
    PlanComponent,
    PossessionsComponent,
    TransactionRequestComponent,
)
from stage0_sim.domain.economy import TransactionPointRegistry
from stage0_sim.domain.npcs import NpcControlMode, NpcPoolRegistry


class _ServingModelClient(ModelClient):
    synchronous = True
    provider_name = "capturing"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        if self.fail:
            raise ModelClientError("NPC model unavailable")
        payload = request.messages[-1].content
        assert payload is not None
        request_id = __import__("json").loads(payload)["observation"][
            "service_requests"
        ][0]["request_id"]
        return ModelTurn(
            text=None,
            tool_calls=(
                ModelToolCall(
                    call_id="serve-1",
                    name="serve_transaction",
                    arguments={"request_id": request_id},
                ),
            ),
            finish_reason="tool_calls",
            provider="capturing",
            model="capturing-v1",
            latency_ms=0,
        )


def _staffed_scenario(
    *,
    request_timeout: float = 10,
    include_blocker: bool = False,
) -> ScenarioDefinition:
    entities: list[dict[str, Any]] = [
        {
            "id": "buyer",
            "components": {
                "position": {"x": 1, "y": 0},
                "homeostasis": {
                    "satiety": 80,
                    "energy": 80,
                    "stress": 20,
                },
                "possessions": {"holdings": {"cents": 5}},
                "plan": {
                    "queue": [
                        {
                            "action": "TRANSACT",
                            "target": "counter",
                            "offer_id": "buy-meal",
                        }
                    ]
                },
            },
        }
    ]
    if include_blocker:
        entities.append(
            {
                "id": "blocker",
                "components": {"position": {"x": 2, "y": 0}},
            }
        )
    return ScenarioDefinition.model_validate(
        {
            "name": "staffed-counter",
            "items": [
                {"id": "cents", "name": "Cents", "unit": "minor unit"},
                {"id": "meal", "name": "Meal", "unit": "item"},
            ],
            "npc_roles": [
                {
                    "id": "cashier",
                    "name": "Counter Cashier",
                    "briefing": "Serve valid assigned checkout requests.",
                }
            ],
            "cognition": {"npc_control_mode": "deterministic"},
            "homeostasis": {
                "activity_coefficients": {
                    "IDLE": {"satiety": 0, "energy": 0, "stress": 0}
                }
            },
            "world": {
                "width": 3,
                "height": 1,
                "transaction_points": [
                    {
                        "id": "counter",
                        "name": "Counter",
                        "position": {"x": 1, "y": 0},
                        "operation": "STAFFED",
                        "staffing": {
                            "role_id": "cashier",
                            "staff_position": {"x": 2, "y": 0},
                            "request_timeout": request_timeout,
                        },
                        "holdings": {"meal": 1},
                        "offers": [
                            {
                                "id": "buy-meal",
                                "name": "Buy meal",
                                "character_gives": [
                                    {"item_id": "cents", "quantity": 5}
                                ],
                                "character_receives": [
                                    {"item_id": "meal", "quantity": 1}
                                ],
                                "duration": 1,
                            }
                        ],
                    }
                ],
            },
            "entities": entities,
        }
    )


def test_deterministic_npc_spawns_and_serves_through_the_tool_pipeline() -> None:
    runner = create_runner(_staffed_scenario(), run_id="staffed")

    runner.run_for(1)

    pool = runner.registry.get_resource(NpcPoolRegistry)
    npc_id = pool.staffing("counter").npc_id
    assert npc_id is not None
    assert runner.registry.has_component(npc_id, NpcComponent)
    request = runner.registry.get_component(
        "buyer", TransactionRequestComponent
    )
    assert request.status == "awaiting_authorization"
    assert request.operator_id == npc_id
    assert runner.registry.get_component(
        "buyer", PossessionsComponent
    ).holdings == {"cents": 5}
    npc_plan = runner.registry.get_component(npc_id, PlanComponent)
    assert npc_plan.queue[0].action.value == "SERVE_TRANSACTION"

    runner.run_for(2)

    assert runner.registry.get_component(
        "buyer", PossessionsComponent
    ).holdings == {"meal": 1}
    assert runner.registry.get_resource(TransactionPointRegistry).state(
        "counter"
    ).holdings == {"cents": 5}
    event_types = [event.event_type for event in runner.events.events]
    assert event_types.index("npc.spawned") < event_types.index(
        "transaction.authorized"
    )
    assert event_types.index("transaction.authorized") < event_types.index(
        "transaction.completed"
    )
    assert any(
        event.event_type == "tool.committed"
        and event.agent_id == npc_id
        and event.payload["tool_name"] == "serve_transaction"
        for event in runner.events.events
    )
    assert pool.effective_mode is NpcControlMode.DETERMINISTIC


def test_staff_spawn_blocking_causes_explicit_request_timeout() -> None:
    runner = create_runner(
        _staffed_scenario(request_timeout=2, include_blocker=True)
    )

    runner.run_for(3)

    timeout = next(
        event
        for event in runner.events.events
        if event.event_type == "transaction.timed_out"
    )
    assert timeout.payload["reason"] == "transaction_service_timed_out"
    assert runner.registry.get_resource(NpcPoolRegistry).staffing(
        "counter"
    ).npc_id is None
    assert runner.registry.get_component(
        "buyer", PossessionsComponent
    ).holdings == {"cents": 5}
    assert any(
        event.event_type == "npc.spawn_blocked"
        for event in runner.events.events
    )
    assert any(
        event.event_type == "transaction.timed_out"
        for event in runner.events.events
    )


def test_model_npc_mode_requires_a_model_provider() -> None:
    scenario = _staffed_scenario().model_copy(deep=True)
    scenario.cognition.npc_control_mode = NpcControlMode.MODEL

    with pytest.raises(ValueError, match="model NPC control"):
        create_runner(scenario)


def test_model_npc_receives_restricted_context_and_serves() -> None:
    scenario = _staffed_scenario().model_copy(deep=True)
    scenario.cognition.npc_control_mode = NpcControlMode.MODEL
    client = _ServingModelClient()
    runner = create_runner(scenario, model_client=client)

    runner.run_for(3)

    assert runner.registry.get_component(
        "buyer", PossessionsComponent
    ).holdings == {"meal": 1}
    assert len(client.requests) == 1
    prompt = "\n".join(
        message.content or "" for message in client.requests[0].messages
    )
    assert "transient embodied service NPC" in prompt
    assert '"actor_kind"' not in prompt
    assert '"service_requests"' in prompt
    assert '"satiety":null' in prompt
    assert '"possessions":[]' in prompt
    assert "character.dossier" not in prompt
    assert "memory.episode" not in prompt
    assert runner.registry.get_resource(
        NpcPoolRegistry
    ).effective_mode is NpcControlMode.MODEL


def test_model_failure_does_not_fall_back_to_deterministic_service() -> None:
    scenario = _staffed_scenario(request_timeout=3).model_copy(deep=True)
    scenario.cognition.npc_control_mode = NpcControlMode.MODEL
    client = _ServingModelClient(fail=True)
    runner = create_runner(scenario, model_client=client)

    runner.run_for(4)

    assert runner.registry.get_component(
        "buyer", PossessionsComponent
    ).holdings == {"cents": 5}
    assert any(
        event.event_type == "cognition.failed"
        for event in runner.events.events
    )
    assert any(
        event.event_type == "transaction.timed_out"
        for event in runner.events.events
    )
    assert not any(
        event.event_type == "cognition.completed"
        and event.payload.get("provider") == "deterministic"
        for event in runner.events.events
    )


def test_deterministic_npc_decisions_do_not_consume_model_budget() -> None:
    scenario = _staffed_scenario().model_copy(deep=True)
    scenario.cognition.max_requests = 1
    runner = create_runner(scenario)

    runner.run_for(3)

    coordinator = runner.registry.get_resource(AgentWorkCoordinator)
    assert coordinator.request_count == 0
    assert runner.registry.get_component(
        "buyer", PossessionsComponent
    ).holdings == {"meal": 1}


def test_staffed_point_schema_rejects_invalid_role_and_geometry() -> None:
    unknown_role = _staffed_scenario().model_dump(mode="json")
    point = unknown_role["world"]["transaction_points"][0]
    point["staffing"]["role_id"] = "missing"
    with pytest.raises(ValidationError, match="unknown NPC role"):
        ScenarioDefinition.model_validate(unknown_role)

    nonadjacent = _staffed_scenario().model_dump(mode="json")
    point = nonadjacent["world"]["transaction_points"][0]
    point["staffing"]["staff_position"] = {"x": 2, "y": 1}
    with pytest.raises(ValidationError, match="must be adjacent"):
        ScenarioDefinition.model_validate(nonadjacent)

    automated = _staffed_scenario().model_dump(mode="json")
    point = automated["world"]["transaction_points"][0]
    point["operation"] = "AUTOMATED"
    with pytest.raises(ValidationError, match="must not define staffing"):
        ScenarioDefinition.model_validate(automated)
