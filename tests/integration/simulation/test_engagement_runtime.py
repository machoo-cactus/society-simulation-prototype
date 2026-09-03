import asyncio
import json
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from stage0_sim.adapters.llm import (
    RecordingModelClient,
    ReplayModelClient,
    ScriptedModelClient,
)
from stage0_sim.application.agents.contracts import (
    ModelClient,
    ModelRequest,
    ModelToolCall,
    ModelTurn,
)
from stage0_sim.application.engagements import (
    COMPILE_ENGAGEMENT_TOOL,
    EngagementWorkCoordinator,
)
from stage0_sim.application.memory import EpisodicMemoryStore
from stage0_sim.application.runner import SimulationRunner
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.application.telemetry import build_runtime_snapshot
from stage0_sim.domain.components import (
    ActivityComponent,
    ActivityType,
    ControllerComponent,
    ConversationComponent,
    DriveComponent,
    EngagementExecutionComponent,
    EngagementProgramComponent,
    HomeostasisComponent,
    PendingEngagementComponent,
    PerceptionComponent,
    PhysicalPose,
    PhysicalStateComponent,
    PlanComponent,
    SenseTransmission,
    SpatialIndex,
    SpatialIndexEntry,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.systems.system1 import System1ArbitrationSystem
from stage0_sim.domain.world import Coordinate, Footprint


def _turn(name: str, arguments: dict[str, JsonValue]) -> ModelTurn:
    return ModelTurn(
        text=None,
        tool_calls=(ModelToolCall(f"{name}-call", name, arguments),),
        finish_reason="tool_calls",
        provider="scripted",
        model="engagement-test",
        latency_ms=0.0,
        input_tokens=10,
        output_tokens=5,
    )


def _engage_turn() -> ModelTurn:
    return _turn(
        "engage",
        {
            "intent": "Wave and perform a short calming stretch.",
            "reference_ids": ["office"],
            "reason": "Acknowledge the room.",
        },
    )


def _expressive_group(
    *,
    group_id: str = "gesture",
    invocation_id: str = "gesture-1",
) -> dict[str, JsonValue]:
    return {
        "group_id": group_id,
        "required_atomic": True,
        "public_text": "Alex waves.",
        "invocations": [
            {
                "invocation_id": invocation_id,
                "capability": "expressive_behavior",
                "arguments": {
                    "subject_id": "alex",
                    "target_id": "office",
                    "public_text": "Alex waves.",
                    "expression_band": "moderate",
                },
            }
        ],
    }


def _bounded_group() -> dict[str, JsonValue]:
    return {
        "group_id": "stretch",
        "required_atomic": True,
        "public_text": "Alex stretches briefly.",
        "invocations": [
            {
                "invocation_id": "stretch-1",
                "capability": "bounded_activity",
                "arguments": {
                    "subject_id": "alex",
                    "target_id": "office",
                    "activity": "a short stretch",
                    "duration_band": "short",
                    "effort_band": "medium",
                    "stress_effect": "calming",
                },
            }
        ],
    }


def _auditory_group(
    *,
    sound_band: str = "normal",
    mode: str = "speech",
    listener_effect: str = "alarming",
    public_text: str = "Please look this way.",
) -> dict[str, JsonValue]:
    return {
        "group_id": "warning",
        "required_atomic": True,
        "public_text": "Alex calls out.",
        "invocations": [
            {
                "invocation_id": "warning-1",
                "capability": "auditory_expression",
                "arguments": {
                    "subject_id": "alex",
                    "target_id": "office",
                    "public_text": public_text,
                    "mode": mode,
                    "sound_band": sound_band,
                    "effort_band": "medium",
                    "listener_effect": listener_effect,
                },
            }
        ],
    }


def _compiled_turn(
    *groups: dict[str, JsonValue],
) -> ModelTurn:
    return _turn(
        COMPILE_ENGAGEMENT_TOOL,
        {
            "disposition": "compiled",
            "summary": "Alex performs a bounded engagement.",
            "groups": list(groups or (_expressive_group(),)),
        },
    )


def _scenario(
    *,
    two_controllers: bool = False,
    blair_x: int = 4,
    additional_characters: tuple[tuple[str, int, bool], ...] = (),
    with_memory: bool = False,
) -> ScenarioDefinition:
    entities: list[dict[str, object]] = []
    character_specs = (
        ("alex", 0, True),
        ("blair", blair_x, two_controllers),
        *additional_characters,
    )
    for actor_id, x, enabled in character_specs:
        components: dict[str, object] = {
            "position": {"x": x, "y": 0},
            "homeostasis": {
                "satiety": 80,
                "energy": 80,
                "stress": 20,
            },
            "character_slot": {
                "label": actor_id.title(),
                "briefing": "Use bounded engagements.",
            },
            "metadata": {"display_name": actor_id.title()},
            "controller": {
                "enabled": enabled,
                "tool_allowlist": ["engage", "say", "skip"],
            },
        }
        if with_memory:
            components["memory"] = {}
        entities.append(
            {
                "id": actor_id,
                "components": components,
            }
        )
    width = max(5, *(x + 1 for _, x, _ in character_specs))
    return ScenarioDefinition.model_validate(
        {
            "name": "engagement-runtime",
            "run_id": "engagement-runtime-run",
            "cognition": {
                "max_concurrency": 2,
                "engagement_compiler": {
                    "max_concurrency": 2,
                },
            },
            "engagement": {
                "short_activity_seconds": 1,
                "medium_activity_seconds": 2,
                "long_activity_seconds": 3,
            },
            "homeostasis": {
                "activity_coefficients": {
                    "IDLE": {"satiety": 0, "energy": 0, "stress": 0},
                    "ENGAGING": {"satiety": 0, "energy": 0, "stress": 0},
                }
            },
            "world": {
                "width": width,
                "height": 1,
                "zones": [
                    {
                        "id": "office",
                        "name": "Office",
                        "type": "OFFICE",
                        "tiles": [{"x": x, "y": 0} for x in range(width)],
                    }
                ],
            },
            "entities": entities,
        }
    )


def _disable_controller(
    runner: SimulationRunner,
    actor_id: str = "alex",
) -> None:
    registry = runner.registry
    registry.get_component(actor_id, ControllerComponent).enabled = False


def _add_hearing_blocker(
    runner: SimulationRunner,
    coordinate: Coordinate,
) -> None:
    registry = runner.registry
    blocker_id = "hearing-blocker"
    registry.create_entity(blocker_id)
    state = PhysicalStateComponent(
        PhysicalPose("implicit-building", coordinate),
        Footprint(
            frozenset(Coordinate(0, y) for y in range(-4, 5))
        ),
        hearing_transmission=SenseTransmission.BLOCK,
    )
    registry.add_component(blocker_id, state)
    registry.get_resource(SpatialIndex).add(
        SpatialIndexEntry(blocker_id, state)
    )


def test_controller_and_compiler_share_one_frozen_ordered_barrier() -> None:
    runner = create_runner(
        _scenario(),
        model_client=ScriptedModelClient(
            (_engage_turn(), _compiled_turn())
        ),
    )

    runner.run_for(1)

    event_types = [event.event_type for event in runner.events.events]
    assert runner.clock.tick == 1
    assert "engagement.compilation_completed" in event_types
    assert "engagement.started" not in event_types
    assert event_types.index("tool.committed") < event_types.index(
        "engagement.compilation_requested"
    )
    assert event_types.index("engagement.compilation_completed") < event_types.index(
        "cognition.barrier_settled"
    )
    settled = next(
        event
        for event in runner.events.events
        if event.event_type == "cognition.barrier_settled"
    )
    assert settled.payload["decision_count"] == 1
    assert settled.payload["engagement_count"] == 1
    assert runner.registry.has_component("alex", EngagementProgramComponent)
    assert not runner.registry.has_component("alex", PendingEngagementComponent)

    _disable_controller(runner)
    runner.run_for(1)

    assert any(
        event.event_type == "engagement.started"
        and event.simulation_tick == 2
        for event in runner.events.events
    )
    runner.stop()


def test_runtime_snapshot_projects_pending_active_and_recent_engagement_safely() -> None:
    async def exercise() -> None:
        client = _DelayedCompilerClient(
            delay=1,
            compiler_turn=_compiled_turn(_bounded_group()),
        )
        runner = create_runner(_scenario(), model_client=client)
        task = asyncio.create_task(runner.run_for_async(1))
        while not client.compiler_started.is_set():
            await asyncio.sleep(0.001)

        pending = build_runtime_snapshot(runner)["agents"][0]["engagement"]
        assert pending["pending"]["status"] == "pending"
        assert pending["pending"]["reference_ids"] == ["office"]
        assert pending["pending"]["participant_ids"] == ["alex", "office"]

        client.release.set()
        await task
        compiled = build_runtime_snapshot(runner)["agents"][0]["engagement"]
        assert compiled["compiled"]["status"] == "compiled"
        assert compiled["compiled"]["compiler_status"] == "succeeded"
        assert compiled["compiled"]["groups"][0]["group_id"] == "stretch"

        _disable_controller(runner)
        await runner.run_for_async(1)
        active = build_runtime_snapshot(runner)["agents"][0]["engagement"]
        assert active["active"]["status"] == "running"
        assert active["active"]["groups"][0]["status"] == "running"
        assert active["active"]["current_group_id"] == "stretch"
        await runner.run_for_async(3)
        recent = build_runtime_snapshot(runner)["agents"][0]["engagement"]
        assert recent["recent"][0]["status"] == "completed"
        assert recent["recent"][0]["groups"][0]["status"] == "completed"
        assert recent["recent"][0]["evidence"][0]["activity"] == (
            "a short stretch"
        )

        serialized = json.dumps(build_runtime_snapshot(runner))
        assert "Wave and perform a short calming stretch." not in serialized
        assert "Acknowledge the room." not in serialized
        assert "Alex performs a bounded engagement." not in serialized
        assert "scene_hash" not in serialized
        assert '"reason"' not in serialized
        runner.stop()

    asyncio.run(exercise())


class _ConcurrentEngagementClient(ModelClient):
    def __init__(self) -> None:
        self.controller_barrier = threading.Barrier(2)
        self.compiler_barrier = threading.Barrier(2)
        self.compiler_completed_at: dict[str, float] = {}

    async def complete(self, request: ModelRequest) -> ModelTurn:
        actor_id = (
            request.request_id.split(":")[1]
            if request.prompt_version != "engagement_compilation.v1"
            else str(
                json.loads(request.messages[-1].content or "{}")["actor"][
                    "actor_id"
                ]
            )
        )
        if request.prompt_version == "engagement_compilation.v1":
            self.compiler_barrier.wait(timeout=2)
            if actor_id == "alex":
                time.sleep(0.02)
            self.compiler_completed_at[actor_id] = time.monotonic()
            group = _expressive_group(
                group_id=f"gesture-{actor_id}",
                invocation_id=f"gesture-{actor_id}-1",
            )
            invocation = group["invocations"]
            assert isinstance(invocation, list)
            arguments = invocation[0]
            assert isinstance(arguments, dict)
            capability_arguments = arguments["arguments"]
            assert isinstance(capability_arguments, dict)
            capability_arguments["subject_id"] = actor_id
            return _compiled_turn(group)
        self.controller_barrier.wait(timeout=2)
        return _engage_turn()


def test_compiler_calls_run_concurrently_but_apply_stably() -> None:
    client = _ConcurrentEngagementClient()
    runner = create_runner(
        _scenario(two_controllers=True),
        model_client=client,
    )
    applied_at: list[tuple[str | None, float]] = []
    runner.events.subscribe(
        lambda event: applied_at.append(
            (event.agent_id, time.monotonic())
        )
        if event.event_type == "engagement.compilation_completed"
        else None
    )

    runner.run_for(1)

    completed = [
        event
        for event in runner.events.events
        if event.event_type == "engagement.compilation_completed"
    ]
    assert [event.agent_id for event in completed] == ["alex", "blair"]
    latest_provider_completion = max(client.compiler_completed_at.values())
    assert [actor_id for actor_id, _ in applied_at] == ["alex", "blair"]
    assert all(
        timestamp >= latest_provider_completion
        for _, timestamp in applied_at
    )
    runner.stop()


@pytest.mark.parametrize(
    "compiler_turn, reason",
    [
        (
            ModelTurn(
                text="not a tool",
                tool_calls=(),
                finish_reason="stop",
                provider="scripted",
                model="engagement-test",
                latency_ms=0,
            ),
            "unsupported_response_shape",
        ),
        (
            _turn(
                COMPILE_ENGAGEMENT_TOOL,
                {
                    "disposition": "unsupported",
                    "summary": "No capability can represent this.",
                    "reason": "Unsupported material effect.",
                },
            ),
            "unsupported",
        ),
        (
            _turn(
                COMPILE_ENGAGEMENT_TOOL,
                {
                    "disposition": "specialized_tool_required",
                    "summary": "Use speech.",
                    "specialized_tool": "say",
                    "reason": "The say tool is authoritative.",
                },
            ),
            "specialized_tool_required",
        ),
    ],
)
@pytest.mark.model_contract
def test_invalid_or_noncompiled_results_fail_without_cosmetic_success(
    compiler_turn: ModelTurn,
    reason: str,
) -> None:
    runner = create_runner(
        _scenario(),
        model_client=ScriptedModelClient((_engage_turn(), compiler_turn)),
    )

    runner.run_for(1)

    assert not runner.registry.has_component("alex", EngagementProgramComponent)
    assert not runner.registry.has_component("alex", PendingEngagementComponent)
    assert runner.registry.get_component("alex", PlanComponent).current is None
    assert any(
        event.event_type == "engagement.compilation_failed"
        and event.payload["reason"] == reason
        for event in runner.events.events
    )
    assert any(
        event.event_type == "action.failed"
        and event.payload["reason"] == reason
        for event in runner.events.events
    )
    assert not any(
        event.event_type == "engagement.capability_committed"
        for event in runner.events.events
    )
    runner.stop()


class _DelayedCompilerClient(ModelClient):
    def __init__(
        self,
        delay: float = 0.05,
        compiler_turn: ModelTurn | None = None,
    ) -> None:
        self.delay = delay
        self.compiler_turn = compiler_turn or _compiled_turn()
        self.compiler_started = threading.Event()
        self.release = threading.Event()

    async def complete(self, request: ModelRequest) -> ModelTurn:
        if request.prompt_version != "engagement_compilation.v1":
            return _engage_turn()
        self.compiler_started.set()
        if self.release.is_set():
            return self.compiler_turn
        self.release.wait(timeout=self.delay)
        return self.compiler_turn


@pytest.mark.model_contract
def test_compiler_timeout_never_installs_a_late_program() -> None:
    payload = _scenario().model_dump(mode="json")
    payload["cognition"]["engagement_compiler"]["timeout_seconds"] = 0.01
    client = _DelayedCompilerClient(delay=0.05)
    runner = create_runner(
        ScenarioDefinition.model_validate(payload),
        model_client=client,
    )

    runner.run_for(1)
    client.release.set()
    time.sleep(0.06)

    assert not runner.registry.has_component("alex", EngagementProgramComponent)
    assert any(
        event.event_type == "engagement.compilation_failed"
        and event.payload["reason"] == "provider_timeout"
        for event in runner.events.events
    )
    runner.stop()


def test_stale_actor_state_rejects_compiler_result() -> None:
    async def exercise() -> None:
        client = _DelayedCompilerClient(delay=1)
        runner = create_runner(_scenario(), model_client=client)
        task = asyncio.create_task(runner.run_for_async(1))
        while not client.compiler_started.is_set():
            await asyncio.sleep(0.001)
        runner.registry.get_component(
            "alex",
            ControllerComponent,
        ).state_revision += 1
        client.release.set()
        await task

        assert not runner.registry.has_component(
            "alex",
            EngagementProgramComponent,
        )
        assert any(
            event.event_type == "engagement.compilation_failed"
            and event.payload["reason"] == "stale_actor_state"
            for event in runner.events.events
        )
        runner.stop()

    asyncio.run(exercise())


def test_stop_cancels_compiler_work_and_prevents_late_commit() -> None:
    async def exercise() -> None:
        client = _DelayedCompilerClient(delay=1)
        runner = create_runner(_scenario(), model_client=client)
        task = asyncio.create_task(runner.run_for_async(1))
        while not client.compiler_started.is_set():
            await asyncio.sleep(0.001)
        runner.stop()
        client.release.set()
        await task
        await asyncio.sleep(0.01)

        assert not runner.registry.has_component(
            "alex",
            EngagementProgramComponent,
        )
        event_types = [event.event_type for event in runner.events.events]
        assert "engagement.compilation_cancelled" in event_types
        assert "engagement.cancelled" in event_types
        assert "action.cancelled" in event_types
        assert "engagement.compilation_completed" not in event_types
        assert event_types[-1] == "simulation.stopped"

    asyncio.run(exercise())


def test_pause_during_compiler_barrier_settles_before_pausing() -> None:
    async def exercise() -> None:
        client = _DelayedCompilerClient(delay=1)
        runner = create_runner(_scenario(), model_client=client)
        task = asyncio.create_task(runner.run_for_async(1))
        while not client.compiler_started.is_set():
            await asyncio.sleep(0.001)

        assert runner.cognition_pending_engagement_ids
        runner.pause()
        client.release.set()
        await task

        assert runner.status.value == "paused"
        assert runner.cognition_pending_count == 0
        assert runner.registry.has_component(
            "alex",
            EngagementProgramComponent,
        )
        runner.stop()

    asyncio.run(exercise())


def test_stop_cancels_active_engagement_and_restores_activity() -> None:
    runner = create_runner(
        _scenario(),
        model_client=ScriptedModelClient(
            (_engage_turn(), _compiled_turn(_bounded_group()))
        ),
    )
    runner.run_for(1)
    _disable_controller(runner)
    runner.run_for(1)

    runner.stop()

    assert runner.registry.get_component(
        "alex",
        ActivityComponent,
    ).current is ActivityType.IDLE
    assert not runner.registry.has_component(
        "alex",
        EngagementExecutionComponent,
    )
    assert any(
        event.event_type == "engagement.cancelled"
        and event.payload["reason"] == "simulation_stopped"
        for event in runner.events.events
    )
    assert any(
        event.event_type == "action.cancelled"
        and event.payload["reason"] == "simulation_stopped"
        for event in runner.events.events
    )


def test_expressive_and_bounded_groups_complete_with_bounded_effects() -> None:
    runner = create_runner(
        _scenario(),
        model_client=ScriptedModelClient(
            (
                _engage_turn(),
                _compiled_turn(_expressive_group(), _bounded_group()),
            )
        ),
    )
    runner.run_for(1)
    _disable_controller(runner)

    runner.run_for(3)

    homeostasis = runner.registry.get_component(
        "alex",
        HomeostasisComponent,
    )
    assert homeostasis.energy == 77
    assert homeostasis.stress == 18
    assert runner.registry.get_component(
        "alex",
        ActivityComponent,
    ).current is ActivityType.IDLE
    event_types = [event.event_type for event in runner.events.events]
    assert event_types.count("engagement.group_completed") == 2
    assert "engagement.completed" in event_types
    assert "action.completed" in event_types
    visual_delivery = next(
        event
        for event in runner.events.events
        if event.event_type == "perception.delivered"
        and event.agent_id == "blair"
        and event.payload["fact_type"] == "engagement_evidence_observed"
    )
    fact = visual_delivery.payload["fact"]
    assert isinstance(fact, dict)
    properties = fact["properties"]
    assert isinstance(properties, dict)
    assert "decision_id" not in properties
    runner.stop()


def test_auditory_expression_uses_grounded_reach_and_public_metadata() -> None:
    runner = create_runner(
        _scenario(),
        model_client=ScriptedModelClient(
            (_engage_turn(), _compiled_turn(_auditory_group()))
        ),
    )
    runner.run_for(1)
    _disable_controller(runner)

    runner.run_for(1)

    assert runner.registry.get_component(
        "alex",
        HomeostasisComponent,
    ).energy == 77
    assert runner.registry.get_component(
        "blair",
        HomeostasisComponent,
    ).stress == 20
    evidence = next(
        event
        for event in runner.events.events
        if event.event_type == "engagement.capability_committed"
        and event.payload["capability"] == "auditory_expression"
    )
    assert evidence.payload["sound_range"] == 10
    assert evidence.payload["listener_effect"] == "alarming"
    assert evidence.payload["recipient_ids"] == []
    assert evidence.payload["recipient_effects_applied"] is False
    runner.stop()


@pytest.mark.parametrize(
    ("sound_band", "expected_recipients"),
    [
        ("quiet", []),
        ("normal", ["blair"]),
        ("loud", ["blair", "casey"]),
    ],
)
def test_auditory_sound_bands_resolve_deterministic_actual_recipients(
    sound_band: str,
    expected_recipients: list[str],
) -> None:
    runner = create_runner(
        _scenario(
            blair_x=1,
            additional_characters=(
                ("casey", 2, False),
                ("drew", 3, False),
            ),
        ),
        model_client=ScriptedModelClient(
            (
                _engage_turn(),
                _compiled_turn(_auditory_group(sound_band=sound_band)),
            )
        ),
    )
    runner.run_for(1)
    _disable_controller(runner)

    runner.run_for(1)

    evidence = next(
        event
        for event in runner.events.events
        if event.event_type == "engagement.capability_committed"
        and event.payload["capability"] == "auditory_expression"
    )
    assert evidence.payload["recipient_ids"] == expected_recipients
    for character_id in ("blair", "casey", "drew"):
        expected_stress = 22 if character_id in expected_recipients else 20
        assert runner.registry.get_component(
            character_id,
            HomeostasisComponent,
        ).stress == expected_stress
    runner.stop()


def test_structural_hearing_blocker_prevents_engagement_facts_and_effects() -> None:
    runner = create_runner(
        _scenario(blair_x=1, with_memory=True),
        model_client=ScriptedModelClient(
            (_engage_turn(), _compiled_turn(_auditory_group()))
        ),
    )
    runner.run_for(1)
    _disable_controller(runner)
    _add_hearing_blocker(runner, Coordinate(9, 4))

    runner.run_for(1)

    evidence = next(
        event
        for event in runner.events.events
        if event.event_type == "engagement.capability_committed"
        and event.payload["capability"] == "auditory_expression"
    )
    assert evidence.payload["recipient_ids"] == []
    assert evidence.payload["recipient_effects"] == []
    assert runner.registry.get_component(
        "blair",
        HomeostasisComponent,
    ).stress == 20
    assert not any(
        item.fact.fact_type == "engagement_evidence_heard"
        for item in runner.registry.get_component(
            "blair",
            PerceptionComponent,
        ).inbox
    )
    engagement_memories = [
        record
        for record in runner.registry.get_resource(
            EpisodicMemoryStore
        ).records
        if record.metadata.get("event_type")
        == "engagement.capability_committed"
    ]
    assert [record.agent_id for record in engagement_memories] == ["alex"]
    runner.stop()


def test_alarming_listener_stress_clamps_with_explicit_effect_evidence() -> None:
    runner = create_runner(
        _scenario(blair_x=1),
        model_client=ScriptedModelClient(
            (_engage_turn(), _compiled_turn(_auditory_group()))
        ),
    )
    runner.run_for(1)
    _disable_controller(runner)
    listener_homeostasis = runner.registry.get_component(
        "blair",
        HomeostasisComponent,
    )
    listener_homeostasis.stress = 99.5
    runner.registry.remove_component("blair", DriveComponent)
    before_revision = runner.registry.get_component(
        "blair",
        ControllerComponent,
    ).state_revision

    runner.run_for(1)

    evidence = next(
        event
        for event in runner.events.events
        if event.event_type == "engagement.capability_committed"
        and event.payload["capability"] == "auditory_expression"
    )
    assert listener_homeostasis.stress == 100
    assert runner.registry.get_component(
        "alex",
        HomeostasisComponent,
    ).stress == 20
    assert evidence.payload["recipient_effects_applied"] is True
    assert evidence.payload["recipient_effects"] == [
        {
            "recipient_id": "blair",
            "stress_before": 99.5,
            "stress_after": 100.0,
            "stress_delta": 0.5,
        }
    ]
    assert runner.registry.get_component(
        "blair",
        ControllerComponent,
    ).state_revision == before_revision + 1
    heard = next(
        item.fact
        for item in runner.registry.get_component(
            "blair",
            PerceptionComponent,
        ).inbox
        if item.fact.fact_type == "engagement_evidence_heard"
    )
    assert "recipient_effects_applied" not in heard.properties
    runner.stop()


def test_rejected_engage_arguments_do_not_expose_private_input() -> None:
    private_intent = "PRIVATE ENGAGEMENT INTENT"
    private_reason = "PRIVATE CONTROLLER REASON"
    runner = create_runner(
        _scenario(),
        model_client=ScriptedModelClient(
            (
                _turn(
                    "engage",
                    {
                        "intent": private_intent,
                        "reference_ids": ["office", "office"],
                        "reason": private_reason,
                    },
                ),
            )
        ),
    )

    runner.run_for(1)

    rejected = next(
        event
        for event in runner.events.events
        if event.event_type == "tool.rejected"
    )
    serialized = json.dumps(rejected.payload, sort_keys=True)
    assert rejected.payload["visibility"] == "private"
    assert rejected.payload["message"] == "engage arguments were rejected"
    assert private_intent not in serialized
    assert private_reason not in serialized
    runner.stop()


def test_speech_engagement_updates_only_grounded_recipient_context() -> None:
    public_text = "Please look this way."
    runner = create_runner(
        _scenario(
            blair_x=1,
            additional_characters=(("casey", 3, False),),
            with_memory=True,
        ),
        model_client=ScriptedModelClient(
            (
                _engage_turn(),
                _compiled_turn(
                    _auditory_group(public_text=public_text)
                ),
            )
        ),
    )
    runner.run_for(1)
    _disable_controller(runner)

    runner.run_for(2)

    heard = [
        item.fact
        for item in runner.registry.get_component(
            "blair",
            PerceptionComponent,
        ).inbox
        if item.fact.fact_type == "engagement_evidence_heard"
    ]
    assert len(heard) == 1
    assert heard[0].properties["public_text"] == public_text
    assert not any(
        item.fact.fact_type == "engagement_evidence_heard"
        for item in runner.registry.get_component(
            "casey",
            PerceptionComponent,
        ).inbox
    )
    assert runner.registry.get_component(
        "alex",
        ConversationComponent,
    ).turns == [public_text]
    assert runner.registry.get_component(
        "blair",
        ConversationComponent,
    ).turns == [public_text]
    assert runner.registry.get_component(
        "casey",
        ConversationComponent,
    ).turns == []
    engagement_memories = [
        record
        for record in runner.registry.get_resource(
            EpisodicMemoryStore
        ).records
        if record.metadata.get("event_type")
        == "engagement.capability_committed"
    ]
    assert [record.agent_id for record in engagement_memories] == [
        "alex",
        "blair",
    ]
    private_values = (
        "Wave and perform a short calming stretch.",
        "Acknowledge the room.",
        "Alex performs a bounded engagement.",
    )
    grounded_output = repr((heard, engagement_memories))
    assert all(value not in grounded_output for value in private_values)
    runner.stop()


def test_visual_engagement_memory_is_limited_to_actual_observers() -> None:
    runner = create_runner(
        _scenario(
            blair_x=1,
            additional_characters=(("casey", 10, False),),
            with_memory=True,
        ),
        model_client=ScriptedModelClient(
            (_engage_turn(), _compiled_turn(_expressive_group()))
        ),
    )
    runner.run_for(1)
    _disable_controller(runner)

    runner.run_for(1)

    assert any(
        item.fact.fact_type == "engagement_evidence_observed"
        for item in runner.registry.get_component(
            "blair",
            PerceptionComponent,
        ).inbox
    )
    assert not any(
        item.fact.fact_type == "engagement_evidence_observed"
        for item in runner.registry.get_component(
            "casey",
            PerceptionComponent,
        ).inbox
    )
    engagement_memories = [
        record
        for record in runner.registry.get_resource(
            EpisodicMemoryStore
        ).records
        if record.metadata.get("event_type")
        == "engagement.capability_committed"
    ]
    assert [record.agent_id for record in engagement_memories] == [
        "alex",
        "blair",
    ]
    runner.stop()


@pytest.mark.parametrize(
    ("group", "fact_type"),
    [
        (_expressive_group(), "engagement_evidence_observed"),
        (_auditory_group(), "engagement_evidence_heard"),
    ],
)
def test_grounded_engagement_evidence_triggers_one_interaction_update(
    group: dict[str, JsonValue],
    fact_type: str,
) -> None:
    runner = create_runner(
        _scenario(blair_x=1),
        model_client=ScriptedModelClient(
            (
                _engage_turn(),
                _compiled_turn(group),
                _turn("skip", {"reconsider_after_seconds": 30}),
            )
        ),
    )
    runner.run_for(1)
    runner.registry.get_component(
        "blair",
        ControllerComponent,
    ).enabled = True

    runner.run_for(1)

    interaction_requests = [
        event
        for event in runner.events.events
        if event.event_type == "cognition.requested"
        and event.payload["trigger"] == "interaction_update"
    ]
    assert [event.agent_id for event in interaction_requests] == ["blair"]
    assert any(
        item.fact.fact_type == fact_type
        for item in runner.registry.get_component(
            "blair",
            PerceptionComponent,
        ).inbox
    ) is False
    assert not any(
        event.event_type == "cognition.requested"
        and event.agent_id == "alex"
        and event.simulation_tick == 2
        for event in runner.events.events
    )
    _disable_controller(runner)
    modality = "auditory" if fact_type.endswith("heard") else "visual"
    follow_up_payload: dict[str, JsonValue] = {
        "capability": (
            "auditory_expression"
            if modality == "auditory"
            else "expressive_behavior"
        ),
        "modality": modality,
        "disclosure": f"local_{modality}",
        "public_text": "Alex provides new public evidence.",
        "target_id": "office",
    }
    if modality == "auditory":
        follow_up_payload.update(
            {
                "mode": "speech",
                "sound_band": "normal",
                "sound_range": 10,
                "recipient_ids": ["blair"],
                "recipient_effects_applied": False,
            }
        )
    runner.events.emit(
        "engagement.capability_committed",
        simulation_tick=runner.clock.tick,
        simulation_time=runner.clock.simulation_time,
        agent_id="alex",
        payload=follow_up_payload,
    )

    runner.run_for(3)

    interaction_requests = [
        event
        for event in runner.events.events
        if event.event_type == "cognition.requested"
        and event.payload["trigger"] == "interaction_update"
    ]
    assert len(interaction_requests) == 1
    runner.stop()


def test_unrelated_group_failure_produces_partial_action_success() -> None:
    runner = create_runner(
        _scenario(),
        model_client=ScriptedModelClient(
            (
                _engage_turn(),
                _compiled_turn(_expressive_group(), _bounded_group()),
            )
        ),
    )
    runner.run_for(1)
    _disable_controller(runner)
    component = runner.registry.get_component(
        "alex",
        EngagementProgramComponent,
    )
    invalid_invocation = replace(
        component.program.groups[1].invocations[0],
        capability="unregistered_capability",
    )
    invalid_group = replace(
        component.program.groups[1],
        invocations=(invalid_invocation,),
    )
    runner.registry.set_component(
        "alex",
        EngagementProgramComponent(
            replace(
                component.program,
                groups=(component.program.groups[0], invalid_group),
            )
        ),
    )

    runner.run_for(2)

    event_types = [event.event_type for event in runner.events.events]
    assert "engagement.group_completed" in event_types
    assert "engagement.group_failed" in event_types
    assert "engagement.partial" in event_types
    assert "action.completed" in event_types
    assert "action.failed" not in event_types
    runner.stop()


def test_no_runtime_group_commit_fails_the_engagement_and_action() -> None:
    runner = create_runner(
        _scenario(),
        model_client=ScriptedModelClient(
            (_engage_turn(), _compiled_turn())
        ),
    )
    runner.run_for(1)
    _disable_controller(runner)
    component = runner.registry.get_component(
        "alex",
        EngagementProgramComponent,
    )
    invalid_invocation = replace(
        component.program.groups[0].invocations[0],
        capability="unregistered_capability",
    )
    runner.registry.set_component(
        "alex",
        EngagementProgramComponent(
            replace(
                component.program,
                groups=(
                    replace(
                        component.program.groups[0],
                        invocations=(invalid_invocation,),
                    ),
                ),
            )
        ),
    )

    runner.run_for(2)

    event_types = [event.event_type for event in runner.events.events]
    assert "engagement.failed" in event_types
    assert "action.failed" in event_types
    assert "action.completed" not in event_types
    runner.stop()


@pytest.mark.parametrize(
    "settings, reason",
    [
        ({"max_requests": 1}, "maximum_requests"),
        ({"max_input_tokens": 1}, "maximum_input_tokens"),
        ({"max_total_output_tokens": 1}, "maximum_output_tokens"),
    ],
)
def test_compiler_budgets_are_separate_and_fail_explicitly(
    settings: dict[str, int],
    reason: str,
) -> None:
    payload = _scenario().model_dump(mode="json")
    payload["cognition"]["engagement_compiler"].update(settings)
    runner = create_runner(
        ScenarioDefinition.model_validate(payload),
        model_client=ScriptedModelClient(
            (_engage_turn(), _compiled_turn(), _engage_turn())
        ),
    )

    runner.run_for(3)

    coordinator = runner.registry.get_resource(EngagementWorkCoordinator)
    assert coordinator.request_count == 1
    assert any(
        event.event_type == "engagement.compilation_failed"
        and event.payload["reason"] == reason
        for event in runner.events.events
    )
    runner.stop()


def test_system1_plan_cancellation_restores_activity_and_cleans_state() -> None:
    runner = create_runner(
        _scenario(),
        model_client=ScriptedModelClient(
            (_engage_turn(), _compiled_turn(_bounded_group()))
        ),
    )
    runner.run_for(1)
    _disable_controller(runner)
    runner.run_for(1)
    assert runner.registry.has_component(
        "alex",
        EngagementExecutionComponent,
    )
    assert runner.registry.get_component(
        "alex",
        ActivityComponent,
    ).current is ActivityType.ENGAGING

    runner.registry.get_component("alex", HomeostasisComponent).energy = 0
    System1ArbitrationSystem().update(runner.context)

    assert not runner.registry.has_component(
        "alex",
        EngagementExecutionComponent,
    )
    assert not runner.registry.has_component(
        "alex",
        EngagementProgramComponent,
    )
    assert runner.registry.get_component(
        "alex",
        ActivityComponent,
    ).current is ActivityType.IDLE
    assert any(
        event.event_type == "engagement.cancelled"
        and event.payload["reason"] == "system1_preemption"
        for event in runner.events.events
    )
    runner.stop()


@pytest.mark.model_contract
def test_recording_and_replay_cover_compiler_calls(tmp_path: Path) -> None:
    path = tmp_path / "engagement-turns.jsonl"
    recording = RecordingModelClient(
        ScriptedModelClient((_engage_turn(), _compiled_turn())),
        path,
    )
    recorded_runner = create_runner(_scenario(), model_client=recording)
    recorded_runner.run_for(1)
    recorded_runner.stop()

    records = path.read_text(encoding="utf-8").splitlines()
    assert len(records) == 2
    replay_runner = create_runner(
        _scenario(),
        model_client=ReplayModelClient.from_jsonl(path),
    )
    replay_runner.run_for(1)

    assert replay_runner.registry.has_component(
        "alex",
        EngagementProgramComponent,
    )
    coordinator = replay_runner.registry.get_resource(
        EngagementWorkCoordinator
    )
    assert coordinator.request_count == 1
    replay_runner.stop()
