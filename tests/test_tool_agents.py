import asyncio
import io
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from stage0_sim.adapters.llm import (
    OpenAICompatibleClient,
    OpenAICompatibleConfiguration,
    RecordingModelClient,
    ReplayModelClient,
    ScriptedModelClient,
)
from stage0_sim.application.agents.contracts import (
    CharacterDecisionRequest,
    CharacterObservation,
    ModelClient,
    ModelRequest,
    ModelToolCall,
    ModelTurn,
    ObservedTarget,
    ToolDefinition,
)
from stage0_sim.application.agents.tools import ToolRegistry, ToolValidationError
from stage0_sim.application.memory import EpisodicMemoryStore
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.domain.components import (
    HomeostasisComponent,
    PendingSpeechComponent,
    PerceptionComponent,
    PlanComponent,
    PositionComponent,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.world import Coordinate


def _turn(name: str, arguments: dict[str, JsonValue]) -> ModelTurn:
    return ModelTurn(
        text=None,
        tool_calls=(
            ModelToolCall(
                call_id="call-1",
                name=name,
                arguments=arguments,
            ),
        ),
        finish_reason="tool_calls",
        provider="scripted",
        model="scripted-v1",
        latency_ms=0,
    )


def _tool_scenario() -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        {
            "name": "tool-agent",
            "cognition": {"controller": "tool-agent"},
            "world": {
                "width": 3,
                "height": 1,
                "zones": [
                    {
                        "id": "office",
                        "name": "Office",
                        "type": "OFFICE",
                        "tiles": [{"x": 0, "y": 0}],
                    },
                    {
                        "id": "lounge",
                        "name": "Lounge",
                        "type": "LOUNGE",
                        "tiles": [{"x": 1, "y": 0}],
                    },
                ],
                "stations": [
                    {
                        "id": "sofa",
                        "name": "Sofa",
                        "position": {"x": 1, "y": 0},
                        "supported_actions": ["RELAX"],
                    }
                ],
            },
            "entities": [
                {
                    "id": "alex",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                        "character_profile": {
                            "display_name": "Alex",
                            "role": "researcher",
                            "goals": ["Take a break"],
                        },
                        "controller": {"enabled": True},
                    },
                }
            ],
        }
    )


def test_scripted_controller_moves_only_through_tool_commit() -> None:
    client = ScriptedModelClient(
        (_turn("go_to", {"target_id": "lounge", "reason": "Take a break"}),)
    )
    runner = create_runner(_tool_scenario(), model_client=client)

    runner.run_for(2)

    position = runner.registry.get_component("alex", PositionComponent)
    assert position.coordinate == Coordinate(1, 0)
    event_types = [event.event_type for event in runner.events.events]
    assert "tool.proposed" in event_types
    assert "tool.accepted" in event_types
    assert "tool.committed" in event_types
    assert event_types.index("tool.committed") < event_types.index("agent.moved")


def test_perception_reveals_zone_departure_not_private_destination() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "private-intention",
            "world": {
                "width": 4,
                "height": 2,
                "zones": [
                    {
                        "id": "office",
                        "name": "Office",
                        "type": "OFFICE",
                        "tiles": [{"x": 0, "y": 0}, {"x": 1, "y": 0}],
                    },
                    {
                        "id": "home",
                        "name": "Home",
                        "type": "HOME",
                        "tiles": [{"x": 3, "y": 0}],
                    },
                ],
            },
            "entities": [
                {
                    "id": "alex",
                    "components": {
                        "position": {"x": 1, "y": 0},
                        "homeostasis": {},
                        "plan": {
                            "queue": [
                                {"action": "MOVE_TO", "target": "home"}
                            ]
                        },
                    },
                },
                {
                    "id": "jordan",
                    "components": {
                        "position": {"x": 0, "y": 1},
                        "homeostasis": {},
                    },
                },
            ],
        }
    )
    runner = create_runner(scenario)

    runner.run_for(1)

    perception = runner.registry.get_component("jordan", PerceptionComponent)
    facts = [item.fact for item in perception.inbox]
    assert any(
        fact.fact_type == "entity_left_zone"
        and fact.location_id == "office"
        for fact in facts
    )
    serialized = repr(facts)
    assert "home" not in serialized
    assert "reason" not in serialized
    assert "destination" not in serialized


def test_speech_is_heard_by_nearby_observers_only() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "hearing",
            "perception": {"hearing_range": 2},
            "world": {"width": 6, "height": 1},
            "entities": [
                {
                    "id": "speaker",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {},
                        "memory": {},
                    },
                },
                {
                    "id": "target",
                    "components": {
                        "position": {"x": 1, "y": 0},
                        "homeostasis": {},
                        "memory": {},
                    },
                },
                {
                    "id": "bystander",
                    "components": {
                        "position": {"x": 2, "y": 0},
                        "homeostasis": {},
                    },
                },
                {
                    "id": "distant",
                    "components": {
                        "position": {"x": 5, "y": 0},
                        "homeostasis": {},
                    },
                },
            ],
        }
    )
    runner = create_runner(scenario)
    runner.registry.add_component(
        "speaker",
        PendingSpeechComponent(
            decision_id="decision-1",
            tool_call_id="call-1",
            target_id="target",
            text="Meet me outside.",
        ),
    )

    runner.run_for(1)

    for listener in ("target", "bystander"):
        perception = runner.registry.get_component(
            listener, PerceptionComponent
        )
        assert any(
            item.fact.fact_type == "heard_speech"
            and item.fact.properties["text"] == "Meet me outside."
            for item in perception.inbox
        )
    distant = runner.registry.get_component("distant", PerceptionComponent)
    assert not any(
        item.fact.fact_type == "heard_speech" for item in distant.inbox
    )
    delivered = next(
        event
        for event in runner.events.events
        if event.event_type == "speech.delivered"
    )
    assert delivered.payload["recipient_ids"] == ["bystander", "target"]
    memory_store = runner.registry.get_resource(EpisodicMemoryStore)
    speech_memories = [
        record
        for record in memory_store.records
        if record.metadata.get("event_type") == "speech.delivered"
    ]
    assert [record.agent_id for record in speech_memories] == [
        "speaker",
        "target",
    ]


def test_tool_registry_rejects_extra_fields_and_unknown_targets() -> None:
    observation = CharacterObservation(
        agent_id="alex",
        display_name="Alex",
        goals=(),
        simulation_time=0,
        location_id=None,
        activity="IDLE",
        satiety=100,
        energy=100,
        stress=0,
        targets=(),
        facts=(),
        recent_outcome=None,
    )
    request = CharacterDecisionRequest(
        decision_id="decision-1",
        run_id="run",
        agent_id="alex",
        requested_tick=1,
        state_revision=0,
        trigger="idle",
        character_description="# Character Profile\n\nAlex",
        profile_id="alex",
        profile_template_version=1,
        profile_content_hash="hash",
        observation=observation,
        memories=(),
        allowed_tools=("go_to", "wait"),
    )
    registry = ToolRegistry()

    with pytest.raises(ToolValidationError) as extra:
        registry.propose(
            request,
            ModelToolCall(
                "call-1",
                "wait",
                {"duration_seconds": 2, "unexpected": True},
            ),
        )
    assert extra.value.reason == "invalid_arguments"
    with pytest.raises(ToolValidationError) as target:
        registry.propose(
            request,
            ModelToolCall("call-2", "go_to", {"target_id": "hidden"}),
        )
    assert target.value.reason == "target_not_observable"


def test_travel_tool_produces_typed_cross_building_intent() -> None:
    observation = CharacterObservation(
        agent_id="alex",
        display_name="Alex",
        goals=(),
        simulation_time=0,
        location_id=None,
        activity="IDLE",
        satiety=100,
        energy=100,
        stress=0,
        targets=(
            ObservedTarget(
                id="building-office",
                kind="building",
                name="Office",
            ),
        ),
        facts=(),
        recent_outcome=None,
        available_travel_modes=("WALK", "CAR"),
    )
    request = CharacterDecisionRequest(
        decision_id="decision-1",
        run_id="run",
        agent_id="alex",
        requested_tick=1,
        state_revision=0,
        trigger="idle",
        character_description="Alex",
        profile_id="alex",
        profile_template_version=1,
        profile_content_hash="hash",
        observation=observation,
        memories=(),
        allowed_tools=("travel_to",),
    )

    intent = ToolRegistry().propose(
        request,
        ModelToolCall(
            "call-1",
            "travel_to",
            {"target_id": "building-office", "mode": "CAR"},
        ),
    )

    assert intent.kind.value == "travel"
    assert intent.target_id == "building-office"
    assert intent.mode.value == "CAR"


def test_recording_and_replay_round_trip(tmp_path: Path) -> None:
    request = ModelRequest(
        request_id="request-1",
        correlation_id="decision-1",
        messages=(),
        tools=(),
        model="scripted",
        timeout_seconds=1,
        max_output_tokens=32,
        prompt_version="v1",
    )
    expected = _turn("wait", {"duration_seconds": 3})
    path = tmp_path / "turns.jsonl"
    recording = RecordingModelClient(
        ScriptedModelClient((expected,)),
        path,
    )

    assert asyncio.run(recording.complete(request)) == expected
    replay = ReplayModelClient.from_jsonl(path)
    assert asyncio.run(replay.complete(request)) == expected


class _DelayedModelClient(ModelClient):
    async def complete(self, request: ModelRequest) -> ModelTurn:
        del request
        await asyncio.sleep(0.03)
        return _turn("wait", {"duration_seconds": 10})


def test_system1_makes_late_provider_result_stale() -> None:
    runner = create_runner(
        _tool_scenario(),
        model_client=_DelayedModelClient(),
    )

    runner.run_for(1)
    state = runner.registry.get_component("alex", HomeostasisComponent)
    state.satiety = 0
    runner.run_for(1)
    time.sleep(0.1)
    runner.run_for(1)

    plan = runner.registry.get_component("alex", PlanComponent)
    rejected = [
        event
        for event in runner.events.events
        if event.event_type == "tool.rejected"
    ]
    assert plan.current is None
    assert plan.queue == []
    assert rejected[-1].payload["reason"] == "stale_decision"
    runner.stop()


def test_coordinator_timeout_does_not_stop_simulation_ticks() -> None:
    payload = _tool_scenario().model_dump(mode="json")
    payload["cognition"]["decision_timeout_seconds"] = 0.01
    runner = create_runner(
        ScenarioDefinition.model_validate(payload),
        model_client=_DelayedModelClient(),
    )

    runner.run_for(1)
    time.sleep(0.02)
    runner.run_for(1)

    failures = [
        event
        for event in runner.events.events
        if event.event_type == "cognition.failed"
    ]
    assert runner.clock.tick == 2
    assert failures[-1].payload["reason"] == "provider_timeout"
    runner.stop()


def test_run_budget_exhaustion_is_explicit_and_stops_cognition() -> None:
    payload = _tool_scenario().model_dump(mode="json")
    payload["cognition"]["max_requests"] = 1
    runner = create_runner(
        ScenarioDefinition.model_validate(payload),
        model_client=ScriptedModelClient(
            (_turn("wait", {"duration_seconds": 30}),)
        ),
    )

    runner.run_for(35)

    exhausted = [
        event
        for event in runner.events.events
        if event.event_type == "cognition.budget_exhausted"
    ]
    assert len(exhausted) == 1
    assert exhausted[0].payload["reason"] == "maximum_requests"
    runner.stop()


def test_tool_agent_requires_an_explicit_model_client() -> None:
    with pytest.raises(ValueError, match="requires an explicit model client"):
        create_runner(_tool_scenario())


class _HTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._content = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_HTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._content


def test_openai_client_retries_llamacpp_503_and_accepts_root_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[urllib.request.Request] = []

    def urlopen(
        request: urllib.request.Request, timeout: float
    ) -> _HTTPResponse:
        del timeout
        attempts.append(request)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(
                url="http://127.0.0.1:8080/v1/chat/completions",
                code=503,
                msg="Service Unavailable",
                hdrs={},
                fp=io.BytesIO(
                    b'{"error":{"message":"model is still loading"}}'
                ),
            )
        return _HTTPResponse(
            {
                "id": "chatcmpl-1",
                "model": "local",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "wait",
                                        "arguments": (
                                            '{"duration_seconds":1}'
                                        ),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            }
        )

    monkeypatch.setattr(
        "stage0_sim.adapters.llm.tool_clients.urllib.request.urlopen",
        urlopen,
    )
    client = OpenAICompatibleClient(
        OpenAICompatibleConfiguration(
            base_url="http://127.0.0.1:8080",
            model="local",
            retry_attempts=2,
            retry_delay_seconds=0,
        )
    )
    request = ModelRequest(
        request_id="request-1",
        correlation_id="decision-1",
        messages=(),
        tools=(
            ToolDefinition(
                name="wait",
                description="Wait.",
                input_schema={"type": "object"},
            ),
        ),
        model="local",
        timeout_seconds=5,
        max_output_tokens=32,
        prompt_version="v1",
    )

    result = asyncio.run(client.complete(request))

    assert len(attempts) == 2
    sent = attempts[-1]
    assert sent.full_url == (
        "http://127.0.0.1:8080/v1/chat/completions"
    )
    assert sent.data is not None
    sent_payload = json.loads(sent.data.decode("utf-8"))
    assert sent_payload["tool_choice"] == "auto"
    assert result.tool_calls[0].name == "wait"
