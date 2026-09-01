from __future__ import annotations

from dataclasses import replace

import pytest

from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.adapters.llm import ScriptedModelClient
from stage0_sim.application.agents.contracts import (
    CharacterDecisionRequest,
    CharacterObservation,
    ModelToolCall,
    ModelTurn,
    ObservedItemAmount,
    ObservedOffer,
    ObservedPossession,
    ObservedServiceRequest,
    ObservedTarget,
)
from stage0_sim.application.agents.tools import ToolRegistry, ToolValidationError
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.application.scenario_resolution import load_and_resolve_scenario
from stage0_sim.domain.components import PossessionsComponent
from stage0_sim.domain.intents import ServeTransactionIntent, TransactionIntent
from tests.helpers.paths import EXAMPLE_ELEMENTS, EXAMPLE_SCENARIOS, REPOSITORY_ROOT

ROOT = REPOSITORY_ROOT


def _request(*, offer_available: bool = True) -> CharacterDecisionRequest:
    cents = ObservedItemAmount(
        item_id="cents",
        item_name="Cents",
        unit="minor unit",
        quantity=5,
    )
    meal = ObservedItemAmount(
        item_id="meal",
        item_name="Meal",
        unit="item",
        quantity=1,
    )
    observation = CharacterObservation(
        agent_id="buyer",
        display_name="Buyer",
        simulation_time=0,
        location_id=None,
        activity="IDLE",
        satiety=80,
        energy=80,
        stress=20,
        targets=(
            ObservedTarget(
                id="counter",
                kind="transaction_point",
                name="Counter",
                offers=(
                    ObservedOffer(
                        id="buy",
                        name="Buy meal",
                        character_gives=(cents,),
                        character_receives=(meal,),
                        duration=1,
                        available=offer_available,
                    ),
                ),
            ),
        ),
        facts=(),
        recent_outcome=None,
        possessions=(
            ObservedPossession(
                item_id="cents",
                item_name="Cents",
                unit="minor unit",
                quantity=5,
            ),
        ),
    )
    return CharacterDecisionRequest(
        decision_id="decision-1",
        run_id="run",
        agent_id="buyer",
        requested_tick=1,
        state_revision=0,
        trigger="idle",
        character_description="Buyer",
        profile_id="buyer",
        profile_template_version=1,
        profile_content_hash="hash",
        observation=observation,
        memories=(),
        allowed_tools=("transact",),
    )


def test_transact_tool_returns_typed_intent_and_rejects_hidden_terms() -> None:
    registry = ToolRegistry()
    intent = registry.propose(
        _request(),
        ModelToolCall(
            call_id="call-1",
            name="transact",
            arguments={"point_id": "counter", "offer_id": "buy"},
        ),
    )

    assert isinstance(intent, TransactionIntent)
    assert intent.point_id == "counter"
    assert intent.offer_id == "buy"

    with pytest.raises(ToolValidationError) as hidden_offer:
        registry.propose(
            _request(),
            ModelToolCall(
                call_id="call-2",
                name="transact",
                arguments={"point_id": "counter", "offer_id": "hidden"},
            ),
        )
    assert hidden_offer.value.reason == "offer_not_observable"

    with pytest.raises(ToolValidationError) as unavailable:
        registry.propose(
            _request(offer_available=False),
            ModelToolCall(
                call_id="call-3",
                name="transact",
                arguments={"point_id": "counter", "offer_id": "buy"},
            ),
        )
    assert unavailable.value.reason == "precondition_failed"


def test_serve_transaction_is_restricted_to_observed_npc_requests() -> None:
    base = _request()
    observation = replace(
        base.observation,
        service_requests=(
            ObservedServiceRequest(
                request_id="request-1",
                customer_id="buyer",
                customer_name="Buyer",
                point_id="counter",
                offer_id="buy",
                offer_name="Buy meal",
                requested_at=1,
            ),
        ),
    )
    npc_request = replace(
        base,
        actor_kind="npc",
        observation=observation,
        allowed_tools=("serve_transaction",),
    )
    intent = ToolRegistry().propose(
        npc_request,
        ModelToolCall(
            "call-serve",
            "serve_transaction",
            {"request_id": "request-1"},
        ),
    )
    assert isinstance(intent, ServeTransactionIntent)
    assert intent.request_id == "request-1"

    with pytest.raises(ToolValidationError) as normal_character:
        ToolRegistry().propose(
            replace(npc_request, actor_kind="character"),
            ModelToolCall(
                "call-invalid",
                "serve_transaction",
                {"request_id": "request-1"},
            ),
        )
    assert normal_character.value.reason == "tool_not_allowed_for_actor"


def test_scripted_tool_agent_can_complete_observed_transaction() -> None:
    source = load_and_resolve_scenario(
        EXAMPLE_SCENARIOS / "greyford-rivermarket-exchange.json",
        FileSystemElementLibrary(EXAMPLE_ELEMENTS),
    ).scenario.model_dump(mode="json")
    source["cognition"] = {
        "tool_allowlist": ["transact"],
        "npc_control_mode": "deterministic",
    }
    components = source["entities"][0]["components"]
    components.pop("plan")
    components["spatial_location"] = {
        "scale": "BUILDING",
        "place_id": "building-greyford-rivermarket-grocer-demo.interior",
        "local_coordinate": {"x": 8, "y": 3},
    }
    components["controller"] = {"enabled": True}
    client = ScriptedModelClient(
        (
            ModelTurn(
                text=None,
                tool_calls=(
                    ModelToolCall(
                        call_id="call-1",
                        name="transact",
                        arguments={
                            "point_id": (
                                "transaction-point-greyford-rivermarket-checkout"
                            ),
                            "offer_id": "redeem-returnable-bottle",
                        },
                    ),
                ),
                finish_reason="tool_calls",
                provider="scripted",
                model="scripted-v1",
                latency_ms=0,
            ),
        )
    )
    runner = create_runner(
        ScenarioDefinition.model_validate(source),
        model_client=client,
    )

    runner.run_for(4)

    possessions = runner.registry.get_component(
        "character-greyford-rivermarket-shopper",
        PossessionsComponent,
    )
    assert possessions.holdings == {"greyford-cent": 500}
    committed = next(
        event
        for event in runner.events.events
        if event.event_type == "tool.committed"
        and event.payload["tool_name"] == "transact"
    )
    assert any(
        event.event_type == "tool.committed"
        and event.payload["tool_name"] == "transact"
        for event in runner.events.events
    )
    completed = next(
        event
        for event in runner.events.events
        if event.event_type == "transaction.completed"
    )
    assert completed.payload["action_id"] == committed.payload["action_id"]
    assert completed.payload["tool_call_id"] == committed.payload["tool_call_id"]
    assert any(
        event.event_type == "transaction.completed"
        for event in runner.events.events
    )
    runner.stop()
