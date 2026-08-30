import asyncio
import io
import json
import threading
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
from stage0_sim.adapters.persistence import SQLiteDatasetStore
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
from stage0_sim.application.agents.coordinator import AgentWorkCoordinator
from stage0_sim.application.agents.tools import ToolRegistry, ToolValidationError
from stage0_sim.application.cognition import EmbeddingError
from stage0_sim.application.manager import (
    SimulationConflictError,
    SimulationManager,
)
from stage0_sim.application.memory import EpisodicMemoryStore
from stage0_sim.application.runner import CognitionPhase, RunnerStatus
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.application.telemetry import build_runtime_snapshot
from stage0_sim.config import Settings, create_model_client
from stage0_sim.domain.components import (
    ControllerComponent,
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


class _CapturingModelClient(ModelClient):
    synchronous = True
    provider_name = "capturing"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        return _turn("skip", {"reconsider_after_seconds": 30})


class _ZeroEmbeddingProvider:
    provider_name = "zero"

    def __init__(self) -> None:
        self.call_count = 0

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.call_count += 1
        return tuple((0.0,) for _ in texts)


class _FailingRetrievalEmbeddingProvider:
    provider_name = "failing"

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        del texts
        raise EmbeddingError("retrieval embeddings unavailable")


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


def test_controller_retrieves_dossier_and_episode_capsules() -> None:
    payload = _tool_scenario().model_dump(mode="json")
    profile = payload["entities"][0]["components"]["character_profile"]
    profile["private_archive"] = {
        "unrelated": "FULL DOSSIER SECRET " * 200
    }
    payload["entities"][0]["components"]["memory"] = {
        "initial_episodes": [
            {
                "text": "Taking a break near the sofa helped yesterday.",
                "simulation_time": 0,
                "importance": 0.8,
            }
        ]
    }
    client = _CapturingModelClient()
    provider = _ZeroEmbeddingProvider()
    runner = create_runner(
        ScenarioDefinition.model_validate(payload),
        model_client=client,
        embedding_provider=provider,
    )

    runner.run_for(1)

    assert len(client.requests) == 1
    model_request = client.requests[0]
    prompt = "\n".join(message.content for message in model_request.messages)
    dynamic = json.loads(model_request.messages[2].content)
    retrieved = next(
        event
        for event in runner.events.events
        if event.event_type == "information.retrieved"
    )
    capsules = retrieved.payload["capsules"]

    assert "Retrieved information context" in prompt
    assert "FULL DOSSIER SECRET" not in prompt
    assert "cognition trigger: idle" in retrieved.payload["query"]
    assert "current goal: Take a break" in retrieved.payload["query"]
    assert "present target: Sofa (station sofa)" in retrieved.payload["query"]
    assert "allowed tool: wait" in retrieved.payload["query"]
    assert dynamic["memories"] == [
        "Taking a break near the sofa helped yesterday."
    ]
    assert isinstance(capsules, list)
    assert [capsule["document_id"] for capsule in capsules] == [
        capsule["document_id"]
        for capsule in sorted(
            capsules,
            key=lambda item: (
                -item["score"],
                item["document_kind"],
                item["document_id"],
                item["source_path"] or "",
            ),
        )
    ]
    assert all(
        {
            "document_id",
            "source_path",
            "score",
            "capsule_text",
            "source",
            "valid_time",
        }.issubset(capsule)
        for capsule in capsules
    )
    runner.stop()


def test_controller_information_retrieval_failure_is_explicit() -> None:
    client = _CapturingModelClient()
    runner = create_runner(
        _tool_scenario(),
        model_client=client,
        embedding_provider=_FailingRetrievalEmbeddingProvider(),
    )

    runner.run_for(1)

    information_failure = next(
        event
        for event in runner.events.events
        if event.event_type == "information.retrieval_failed"
    )
    cognition_failure = next(
        event
        for event in runner.events.events
        if event.event_type == "cognition.failed"
    )
    assert client.requests == []
    assert information_failure.payload["provider"] == "failing"
    assert information_failure.payload["message"] == (
        "retrieval embeddings unavailable"
    )
    assert cognition_failure.payload["reason"] == (
        "information_retrieval_failed"
    )
    assert runner.clock.tick == 1
    runner.stop()


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


def test_skip_tool_accepts_defaults_and_rejects_invalid_delay() -> None:
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
        character_description="Alex",
        profile_id="alex",
        profile_template_version=1,
        profile_content_hash="hash",
        observation=observation,
        memories=(),
        allowed_tools=("skip",),
    )

    intent = ToolRegistry().propose(
        request,
        ModelToolCall("call-1", "skip", {}),
    )
    assert intent.kind.value == "skip"
    assert intent.reconsider_after_seconds == 30

    with pytest.raises(ToolValidationError) as invalid:
        ToolRegistry().propose(
            request,
            ModelToolCall(
                "call-2",
                "skip",
                {"reconsider_after_seconds": 1},
            ),
        )
    assert invalid.value.reason == "invalid_arguments"


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


def test_skip_defers_cognition_without_creating_a_plan() -> None:
    runner = create_runner(
        _tool_scenario(),
        model_client=ScriptedModelClient(
            (
                _turn("skip", {"reconsider_after_seconds": 30}),
                _turn("skip", {"reconsider_after_seconds": 30}),
            )
        ),
    )

    runner.run_for(1)

    plan = runner.registry.get_component("alex", PlanComponent)
    controller = runner.registry.get_component("alex", ControllerComponent)
    assert plan.current is None
    assert plan.queue == []
    assert controller.next_decision_time == 31
    assert any(
        event.event_type == "cognition.skipped"
        for event in runner.events.events
    )

    runner.run_for(29)
    coordinator = runner.registry.get_resource(AgentWorkCoordinator)
    assert coordinator.request_count == 1

    runner.run_for(1)
    assert coordinator.request_count == 2
    runner.stop()


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


class _ConcurrentModelClient(ModelClient):
    def __init__(self) -> None:
        self.barrier = threading.Barrier(2)
        self.completed_at: dict[str, float] = {}

    async def complete(self, request: ModelRequest) -> ModelTurn:
        self.barrier.wait(timeout=1)
        self.completed_at[request.request_id] = time.monotonic()
        return _turn("skip", {"reconsider_after_seconds": 30})


def _two_character_tool_scenario() -> ScenarioDefinition:
    payload = _tool_scenario().model_dump(mode="json")
    second = json.loads(json.dumps(payload["entities"][0]))
    second["id"] = "blair"
    second["components"]["position"] = {"x": 2, "y": 0}
    second["components"]["character_profile"]["display_name"] = "Blair"
    payload["entities"].append(second)
    return ScenarioDefinition.model_validate(payload)


def test_system1_makes_late_provider_result_stale() -> None:
    payload = _tool_scenario().model_dump(mode="json")
    payload["cognition"]["execution_mode"] = "background"
    runner = create_runner(
        ScenarioDefinition.model_validate(payload),
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


def test_global_barrier_waits_for_timeout_before_completing_tick() -> None:
    payload = _tool_scenario().model_dump(mode="json")
    payload["cognition"]["decision_timeout_seconds"] = 0.01
    runner = create_runner(
        ScenarioDefinition.model_validate(payload),
        model_client=_DelayedModelClient(),
    )

    started_at = time.monotonic()
    runner.run_for(1)
    elapsed = time.monotonic() - started_at

    failures = [
        event
        for event in runner.events.events
        if event.event_type == "cognition.failed"
    ]
    assert runner.clock.tick == 1
    assert elapsed >= 0.01
    assert failures[-1].payload["reason"] == "provider_timeout"
    runner.stop()


def test_global_barrier_freezes_until_decision_is_committed() -> None:
    runner = create_runner(
        _tool_scenario(),
        model_client=_DelayedModelClient(),
    )

    started_at = time.monotonic()
    runner.run_for(1)
    elapsed = time.monotonic() - started_at

    assert elapsed >= 0.03
    event_types = [event.event_type for event in runner.events.events]
    assert event_types.index("tool.committed") < event_types.index(
        "simulation.tick"
    )
    runner.stop()


def test_global_barrier_dispatches_concurrently_and_commits_stably() -> None:
    client = _ConcurrentModelClient()
    runner = create_runner(
        _two_character_tool_scenario(),
        model_client=client,
    )
    committed_at: list[tuple[str | None, float]] = []
    runner.events.subscribe(
        lambda event: committed_at.append(
            (event.agent_id, time.monotonic())
        )
        if event.event_type == "tool.committed"
        else None
    )

    runner.run_for(1)

    assert [agent_id for agent_id, _ in committed_at] == ["alex", "blair"]
    last_completion = max(client.completed_at.values())
    assert all(timestamp >= last_completion for _, timestamp in committed_at)
    runner.stop()


def test_pause_during_barrier_finishes_current_boundary() -> None:
    async def exercise() -> None:
        runner = create_runner(
            _tool_scenario(),
            model_client=_DelayedModelClient(),
        )
        task = asyncio.create_task(runner.run_for_async(1))
        await asyncio.sleep(0.005)

        assert runner.cognition_phase is CognitionPhase.WAITING
        snapshot = build_runtime_snapshot(runner)
        assert snapshot["cognition_pending_count"] == 1
        assert snapshot["cognition_wait_elapsed_seconds"] >= 0
        runner.pause()
        await task

        assert runner.status is RunnerStatus.PAUSED
        assert runner.cognition_phase is CognitionPhase.IDLE
        assert any(
            event.event_type == "tool.committed"
            for event in runner.events.events
        )
        runner.stop()

    asyncio.run(exercise())


def test_stop_during_barrier_cancels_without_late_commit() -> None:
    async def exercise() -> None:
        runner = create_runner(
            _tool_scenario(),
            model_client=_DelayedModelClient(),
        )
        task = asyncio.create_task(runner.run_for_async(1))
        await asyncio.sleep(0.005)

        runner.stop()
        await task

        event_types = [event.event_type for event in runner.events.events]
        assert runner.status is RunnerStatus.STOPPED
        assert "cognition.cancelled" in event_types
        assert "tool.committed" not in event_types
        assert event_types[-1] == "simulation.stopped"

    asyncio.run(exercise())


def test_manager_rejects_vital_mutation_while_step_is_waiting(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        manager = SimulationManager(
            SQLiteDatasetStore(tmp_path / "barrier.sqlite3"),
            model_client=_DelayedModelClient(),
        )
        scenario_id = manager.add_scenario(_tool_scenario())
        run_id = await manager.start_run(scenario_id, realtime=False)
        manager.pause(run_id)
        step = asyncio.create_task(manager.step(run_id))
        await asyncio.sleep(0.005)

        with pytest.raises(SimulationConflictError, match="settling"):
            manager.mutate_vitals(
                run_id,
                "alex",
                {"satiety": 50},
            )

        await step
        await manager.close()

    asyncio.run(exercise())


def test_zero_tool_calls_include_cardinality_diagnostics() -> None:
    empty_turn = ModelTurn(
        text="I will do nothing.",
        tool_calls=(),
        finish_reason="stop",
        provider="scripted",
        model="scripted-v1",
        latency_ms=0,
    )
    runner = create_runner(
        _tool_scenario(),
        model_client=ScriptedModelClient((empty_turn,)),
    )

    runner.run_for(1)

    rejected = next(
        event
        for event in runner.events.events
        if event.event_type == "tool.rejected"
    )
    assert rejected.payload["expected_tool_call_count"] == 1
    assert rejected.payload["actual_tool_call_count"] == 0
    assert rejected.payload["finish_reason"] == "stop"
    assert rejected.payload["has_text"] is True
    assert "skip" in rejected.payload["offered_tools"]
    runner.stop()


def test_multiple_tool_calls_are_rejected_as_one_invalid_decision() -> None:
    turn = ModelTurn(
        text=None,
        tool_calls=(
            ModelToolCall("call-1", "wait", {"duration_seconds": 1}),
            ModelToolCall(
                "call-2",
                "skip",
                {"reconsider_after_seconds": 30},
            ),
        ),
        finish_reason="tool_calls",
        provider="scripted",
        model="scripted-v1",
        latency_ms=0,
    )
    runner = create_runner(
        _tool_scenario(),
        model_client=ScriptedModelClient((turn,)),
    )

    runner.run_for(1)

    rejected = [
        event
        for event in runner.events.events
        if event.event_type == "tool.rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0].payload["actual_tool_call_count"] == 2
    assert not any(
        event.event_type == "tool.committed"
        for event in runner.events.events
    )
    runner.stop()


def test_none_tool_choice_is_rejected_for_tool_agent_client() -> None:
    with pytest.raises(ValueError, match="incompatible"):
        create_model_client(
            Settings(
                llm_provider="openai-compatible",
                llm_base_url="http://127.0.0.1:8080/v1",
                llm_model="local",
                llm_tool_choice="none",
            )
        )


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
    assert sent_payload["tool_choice"] == "required"
    assert result.tool_calls[0].name == "wait"
