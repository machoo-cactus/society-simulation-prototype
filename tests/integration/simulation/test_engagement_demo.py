import asyncio
import json

import pytest

from stage0_sim.api.fake_llm import (
    ChatCompletionRequest,
    ChatMessage,
    ChatTool,
    FunctionDefinition,
    create_chat_completion,
)
from stage0_sim.application.agents.contracts import (
    ModelRequest,
    ModelToolCall,
    ModelTurn,
)
from stage0_sim.application.engagements.contracts import (
    EngagementCompilationProposal,
)
from stage0_sim.application.scenario import create_runner, load_scenario
from stage0_sim.domain.components import ControllerComponent, HomeostasisComponent
from tests.helpers.paths import SCENARIO_FIXTURES

pytestmark = pytest.mark.model_contract


class _FakeEndpointModelClient:
    synchronous = True

    async def complete(self, request: ModelRequest) -> ModelTurn:
        response = await create_chat_completion(
            ChatCompletionRequest(
                model=request.model,
                messages=[
                    ChatMessage(role=message.role, content=message.content)
                    for message in request.messages
                ],
                tools=[
                    ChatTool(
                        type="function",
                        function=FunctionDefinition(
                            name=tool.name,
                            description=tool.description,
                            parameters=tool.input_schema,
                        ),
                    )
                    for tool in request.tools
                ],
                tool_choice="required",
                max_tokens=request.max_output_tokens,
            )
        )
        choice = response["choices"][0]
        assert isinstance(choice, dict)
        message = choice["message"]
        assert isinstance(message, dict)
        calls = message.get("tool_calls", [])
        assert isinstance(calls, list)
        tool_calls = tuple(
            ModelToolCall(
                call_id=str(call["id"]),
                name=str(call["function"]["name"]),
                arguments=json.loads(str(call["function"]["arguments"])),
            )
            for call in calls
        )
        return ModelTurn(
            text=message.get("content"),
            tool_calls=tool_calls,
            finish_reason=str(choice["finish_reason"]),
            provider="stage0-fake-openai",
            model=request.model,
            latency_ms=0,
        )


@pytest.mark.parametrize(
    ("intent", "expected_capability"),
    [
        ("Wave hello.", "expressive_behavior"),
        ("Shout a warning.", "auditory_expression"),
        ("Dance solo.", "bounded_activity"),
    ],
)
def test_fake_endpoint_compiles_representative_engagements(
    intent: str,
    expected_capability: str,
) -> None:
    response = asyncio.run(
        create_chat_completion(
            ChatCompletionRequest(
                model="stage0-fake",
                messages=[
                    ChatMessage(
                        role="system",
                        content="Operation: engagement_compilation.",
                    ),
                    ChatMessage(
                        role="user",
                        content=json.dumps(
                            {
                                "actor": {"actor_id": "actor"},
                                "engagement": {
                                    "intent": intent,
                                    "reference_ids": ["target"],
                                },
                            }
                        ),
                    ),
                ],
                tools=[
                    ChatTool(
                        type="function",
                        function=FunctionDefinition(
                            name="compile_engagement",
                            parameters=(
                                EngagementCompilationProposal.model_json_schema()
                            ),
                        ),
                    )
                ],
                tool_choice="required",
            )
        )
    )

    choice = response["choices"][0]
    assert isinstance(choice, dict)
    message = choice["message"]
    assert isinstance(message, dict)
    calls = message["tool_calls"]
    assert isinstance(calls, list)
    function = calls[0]["function"]
    proposal = EngagementCompilationProposal.model_validate_json(
        str(function["arguments"])
    )
    assert proposal.groups[0].invocations[0].capability == expected_capability
    assert proposal.groups[0].invocations[0].arguments["subject_id"] == "actor"
    assert proposal.groups[0].invocations[0].arguments["target_id"] == "target"


def test_fake_endpoint_preserves_controller_wait_preference() -> None:
    response = asyncio.run(
        create_chat_completion(
            ChatCompletionRequest(
                model="stage0-fake",
                messages=[],
                tools=[
                    ChatTool(
                        type="function",
                        function=FunctionDefinition(
                            name="engage",
                            parameters={"type": "object"},
                        ),
                    ),
                    ChatTool(
                        type="function",
                        function=FunctionDefinition(
                            name="wait",
                            parameters={
                                "type": "object",
                                "properties": {
                                    "duration_seconds": {"type": "number"}
                                },
                            },
                        ),
                    ),
                ],
                tool_choice="required",
            )
        )
    )

    choice = response["choices"][0]
    assert isinstance(choice, dict)
    message = choice["message"]
    assert isinstance(message, dict)
    calls = message["tool_calls"]
    assert isinstance(calls, list)
    assert calls[0]["function"]["name"] == "wait"


def test_fake_endpoint_runs_engagement_regression_fixture() -> None:
    runner = create_runner(
        load_scenario(SCENARIO_FIXTURES / "engagement-demo.json"),
        model_client=_FakeEndpointModelClient(),
    )

    runner.run_for(1)
    for actor_id in ("dancer", "shouter"):
        runner.registry.get_component(actor_id, ControllerComponent).enabled = False
    runner.run_for(6)

    evidence = [
        event
        for event in runner.events.events
        if event.event_type == "engagement.capability_committed"
    ]
    assert {event.payload["capability"] for event in evidence} == {
        "auditory_expression",
        "bounded_activity",
    }
    warning = next(
        event
        for event in evidence
        if event.payload["capability"] == "auditory_expression"
    )
    assert warning.payload["recipient_ids"] == ["dancer", "near-listener"]
    assert [
        effect["recipient_id"]
        for effect in warning.payload["recipient_effects"]
    ] == ["dancer", "near-listener"]
    near_stress = runner.registry.get_component(
        "near-listener",
        HomeostasisComponent,
    ).stress
    blocked_stress = runner.registry.get_component(
        "blocked-listener",
        HomeostasisComponent,
    ).stress
    assert near_stress - blocked_stress == 2
    assert any(
        event.event_type == "engagement.completed"
        and event.agent_id == "dancer"
        for event in runner.events.events
    )
    runner.stop()
