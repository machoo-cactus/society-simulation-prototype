import inspect
import json
from pathlib import Path, PurePosixPath

from stage0_sim.adapters.llm import (
    FakeDialogueGenerator,
    FakeEmbeddingProvider,
    FakePlanner,
    ScriptedPlanner,
)
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.cognition import (
    DialogueContext,
    DialogueError,
    DialogueResult,
    PlannerContext,
    PlannerError,
)
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.macro_work import (
    DialogueWork,
    MacroWorkCoordinator,
)
from stage0_sim.application.memory import EpisodicMemoryStore
from stage0_sim.application.planning import MacroPlanningSystem
from stage0_sim.application.runner import RunnerStatus
from stage0_sim.application.scenario import ScenarioDefinition, create_runner, load_scenario
from stage0_sim.application.telemetry import TelemetryBroker
from stage0_sim.domain.components import (
    ActionType,
    ConversationComponent,
    DriveComponent,
    HomeostasisComponent,
    PlanAction,
    PlanComponent,
    PlannerComponent,
    PositionComponent,
    System1State,
)


def _scenario_path(name: str) -> Path:
    return Path(__file__).parents[1] / "scenarios" / name


def _social_scenario(*, with_memory: bool = False) -> ScenarioDefinition:
    speaker_components: dict[str, object] = {
        "position": {"x": 0, "y": 0},
        "homeostasis": {
            "satiety": 80,
            "energy": 80,
            "stress": 20,
        },
        "planner": {"daily_goals": ["Talk"]},
    }
    if with_memory:
        speaker_components["memory"] = {
            "initial_episodes": [
                {
                    "text": "The listener appreciates concise greetings.",
                    "importance": 0.7,
                }
            ]
        }
    return ScenarioDefinition.model_validate(
        {
            "name": "social-isolation",
            "homeostasis": {
                "activity_coefficients": {
                    "IDLE": {"satiety": 0, "energy": 0, "stress": 0}
                }
            },
            "world": {"width": 2, "height": 1, "stations": []},
            "entities": [
                {
                    "id": "speaker",
                    "components": speaker_components,
                },
                {
                    "id": "listener",
                    "components": {
                        "position": {"x": 1, "y": 0},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                    },
                },
            ],
        }
    )


def _invalid_social_target_scenario(
    *,
    queued_action: bool = False,
) -> ScenarioDefinition:
    speaker_components: dict[str, object] = {
        "position": {"x": 0, "y": 0},
        "homeostasis": {
            "satiety": 80,
            "energy": 80,
            "stress": 20,
        },
    }
    if queued_action:
        speaker_components["plan"] = {
            "queue": [
                {
                    "action": "SOCIALIZE",
                    "target": "prop",
                    "duration": 2,
                }
            ]
        }
    else:
        speaker_components["planner"] = {}
    return ScenarioDefinition.model_validate(
        {
            "name": "invalid-social-target",
            "world": {"width": 2, "height": 1, "stations": []},
            "entities": [
                {"id": "speaker", "components": speaker_components},
                {
                    "id": "prop",
                    "components": {"position": {"x": 1, "y": 0}},
                },
            ],
        }
    )


def test_exact_nine_step_survival_acceptance_flow_is_provider_isolated() -> None:
    scenario = load_scenario(_scenario_path("fake-llm-planning.json"))
    planner = FakePlanner()
    embeddings = FakeEmbeddingProvider()
    runner = create_runner(
        scenario,
        planner=planner,
        embedding_provider=embeddings,
    )
    runner.run_for(7)
    plan = runner.registry.get_component("agent-001", PlanComponent)
    assert plan.current is not None
    assert plan.current.action is ActionType.WORK

    state = runner.registry.get_component("agent-001", HomeostasisComponent)
    state.satiety = 10
    provider_counts = (planner.call_count, embeddings.call_count)
    runner.run_for(1)

    drive = runner.registry.get_component("agent-001", DriveComponent)
    assert plan.current is None
    assert plan.queue == []
    assert drive.target_station_id == "fridge-1"

    while drive.state is not System1State.NORMAL:
        runner.run_for(1)
    assert state.satiety >= 30
    assert (planner.call_count, embeddings.call_count) == provider_counts
    planner_state = runner.registry.get_component(
        "agent-001", PlannerComponent
    )
    assert planner_state.needs_plan
    assert not planner_state.request_pending

    runner.run_for(1)
    assert planner.call_count == provider_counts[0] + 1
    assert embeddings.call_count > provider_counts[1]


def test_survival_work_filtering_does_not_starve_healthy_agents() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "per-agent-survival-filtering",
            "homeostasis": {
                "activity_coefficients": {
                    "IDLE": {"satiety": 0, "energy": 0, "stress": 0}
                }
            },
            "world": {
                "width": 3,
                "height": 1,
                "stations": [
                    {
                        "id": "fridge",
                        "name": "Fridge",
                        "position": {"x": 0, "y": 0},
                        "supported_actions": ["EAT"],
                    },
                    {
                        "id": "desk",
                        "name": "Desk",
                        "position": {"x": 1, "y": 0},
                        "supported_actions": ["WORK"],
                    },
                ],
            },
            "entities": [
                {
                    "id": "survival-agent",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {
                            "satiety": 10,
                            "energy": 80,
                            "stress": 20,
                        },
                        "memory": {},
                    },
                },
                {
                    "id": "healthy-agent",
                    "components": {
                        "position": {"x": 2, "y": 0},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                        "planner": {},
                        "memory": {},
                    },
                },
            ],
        }
    )
    planner = FakePlanner()
    runner = create_runner(scenario, planner=planner)
    for agent_id in ("survival-agent", "healthy-agent"):
        runner.events.emit(
            "plan.action_failed",
            simulation_tick=0,
            simulation_time=0,
            agent_id=agent_id,
            payload={"reason": "test"},
        )

    runner.run_for(1)

    records = runner.registry.get_resource(EpisodicMemoryStore).records
    assert planner.call_count == 1
    assert any(
        event.event_type == "planner.completed"
        and event.agent_id == "healthy-agent"
        for event in runner.events.events
    )
    assert any(record.agent_id == "healthy-agent" for record in records)
    assert not any(record.agent_id == "survival-agent" for record in records)


def test_capacity_reservations_choose_deterministic_next_nearest_station() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "capacity-fallback",
            "homeostasis": {
                "activity_coefficients": {
                    "IDLE": {"satiety": 0, "energy": 0, "stress": 0}
                }
            },
            "world": {
                "width": 5,
                "height": 2,
                "stations": [
                    {
                        "id": "fridge-near",
                        "name": "Near",
                        "position": {"x": 2, "y": 0},
                        "supported_actions": ["EAT"],
                        "capacity": 1,
                    },
                    {
                        "id": "fridge-fallback",
                        "name": "Fallback",
                        "position": {"x": 4, "y": 1},
                        "supported_actions": ["EAT"],
                        "capacity": 1,
                    },
                ],
            },
            "entities": [
                {
                    "id": "agent-a",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {
                            "satiety": 10,
                            "energy": 80,
                            "stress": 20,
                        },
                    },
                },
                {
                    "id": "agent-b",
                    "components": {
                        "position": {"x": 0, "y": 1},
                        "homeostasis": {
                            "satiety": 10,
                            "energy": 80,
                            "stress": 20,
                        },
                    },
                },
            ],
        }
    )
    runner = create_runner(scenario)

    runner.run_for(1)

    assert runner.registry.get_component(
        "agent-a", DriveComponent
    ).target_station_id == "fridge-near"
    assert runner.registry.get_component(
        "agent-b", DriveComponent
    ).target_station_id == "fridge-fallback"


def test_dialogue_is_generated_telemetried_persisted_and_exported(
    tmp_path: Path,
) -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "integrated-dialogue",
            "homeostasis": {
                "activity_coefficients": {
                    "IDLE": {"satiety": 0, "energy": 0, "stress": 0}
                }
            },
            "world": {"width": 2, "height": 1, "stations": []},
            "entities": [
                {
                    "id": "speaker",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                        "planner": {"daily_goals": ["Talk"]},
                        "memory": {
                            "initial_episodes": [
                                {
                                    "text": "A prior friendly conversation.",
                                    "importance": 0.7,
                                }
                            ]
                        },
                    },
                },
                {
                    "id": "listener",
                    "components": {
                        "position": {"x": 1, "y": 0},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                        "memory": {},
                    },
                },
            ],
        }
    )
    dialogue = FakeDialogueGenerator()
    runner = create_runner(
        scenario,
        run_id="dialogue-run",
        planner=ScriptedPlanner(
            actions=(
                PlanAction(
                    ActionType.SOCIALIZE,
                    target="listener",
                    duration=4,
                ),
            )
        ),
        dialogue_generator=dialogue,
    )
    store = SQLiteDatasetStore(tmp_path / "dialogue.sqlite3")
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario=scenario.model_dump(mode="json"),
    )
    broker = TelemetryBroker(runner)

    runner.run_for(3)
    collector.finalize()

    events = runner.events.events
    assert dialogue.call_count == 1
    assert any(event.event_type == "dialogue.requested" for event in events)
    generated = next(
        event for event in events if event.event_type == "dialogue.generated"
    )
    text = generated.payload["text"]
    assert isinstance(text, str)
    assert text in runner.registry.get_component(
        "speaker", ConversationComponent
    ).turns
    assert text in runner.registry.get_component(
        "listener", ConversationComponent
    ).turns
    memories = runner.registry.get_resource(EpisodicMemoryStore).records
    assert any(record.text == text for record in memories)
    assert any(
        message.message_type == "dialogue_event"
        for message in broker.messages_after(0)
    )

    persisted = store.load_memories("dialogue-run")
    exported = "\n".join(store.iter_jsonl("dialogue-run"))
    store.close()
    rehydrated = EpisodicMemoryStore(FakeEmbeddingProvider())
    rehydrated.rehydrate(persisted)
    assert any(record.text == "A prior friendly conversation." for record in persisted)
    assert any(record.text == text for record in persisted)
    assert rehydrated.records == persisted
    assert '"event_type":"memory.initial"' in exported
    assert '"operation":"dialogue"' in exported
    assert text in exported


class StackCheckingPlanner(FakePlanner):
    called_from_system_pass: bool = False

    def plan(self, context: PlannerContext):
        self.called_from_system_pass = _inside_system_executor()
        return super().plan(context)


class StackCheckingEmbedding(FakeEmbeddingProvider):
    called_from_system_pass: bool = False

    def embed(self, texts: tuple[str, ...]):
        self.called_from_system_pass = (
            self.called_from_system_pass or _inside_system_executor()
        )
        return super().embed(texts)


def _inside_system_executor() -> bool:
    return any(
        _is_system_executor_frame(frame.function, frame.filename)
        for frame in inspect.stack()
    )


def _is_system_executor_frame(function: str, filename: str) -> bool:
    path = PurePosixPath(filename.replace("\\", "/"))
    return (
        function == "update"
        and path.name == "__init__.py"
        and path.parent.name == "systems"
        and path.parent.parent.name == "domain"
    )


def test_system_executor_stack_detection_accepts_native_path_styles() -> None:
    assert _is_system_executor_frame(
        "update",
        "/repo/src/stage0_sim/domain/systems/__init__.py",
    )
    assert _is_system_executor_frame(
        "update",
        r"C:\repo\src\stage0_sim\domain\systems\__init__.py",
    )
    assert not _is_system_executor_frame(
        "update",
        "/repo/src/stage0_sim/application/runner.py",
    )


def test_provider_calls_never_execute_from_ordered_system_pass() -> None:
    planner = StackCheckingPlanner()
    embeddings = StackCheckingEmbedding()
    runner = create_runner(
        load_scenario(_scenario_path("fake-llm-planning.json")),
        planner=planner,
        embedding_provider=embeddings,
    )

    runner.run_for(2)

    assert planner.call_count == 1
    assert embeddings.call_count > 0
    assert not planner.called_from_system_pass
    assert not embeddings.called_from_system_pass


class FailingPlanner(FakePlanner):
    def plan(self, context: PlannerContext):
        del context
        self.call_count += 1
        raise PlannerError(
            "planner timed out",
            provider="remote-planner",
            latency_ms=125.0,
            input_tokens=42,
            output_tokens=0,
        )


class FailingDialogue(FakeDialogueGenerator):
    def generate(self, context: DialogueContext) -> DialogueResult:
        del context
        self.call_count += 1
        raise DialogueError(
            "dialogue timed out",
            provider="remote-dialogue",
            latency_ms=80.0,
            input_tokens=12,
            output_tokens=0,
        )


def test_failed_planner_and_dialogue_events_include_known_provider_metadata() -> None:
    planner_runner = create_runner(
        load_scenario(_scenario_path("fake-llm-planning.json")),
        planner=FailingPlanner(),
    )
    planner_runner.run_for(1)
    planner_failure = next(
        event
        for event in planner_runner.events.events
        if event.event_type == "planner.failed"
    )
    assert planner_failure.payload["provider"] == "remote-planner"
    assert planner_failure.payload["latency_ms"] == 125.0
    assert planner_failure.payload["input_tokens"] == 42
    assert planner_failure.payload["output_tokens"] == 0

    dialogue_scenario = ScenarioDefinition.model_validate(
        {
            "name": "dialogue-failure",
            "world": {"width": 2, "height": 1, "stations": []},
            "entities": [
                {
                    "id": "speaker",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                        "planner": {},
                    },
                },
                {
                    "id": "listener",
                    "components": {
                        "position": {"x": 1, "y": 0},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                    },
                },
            ],
        }
    )
    dialogue_runner = create_runner(
        dialogue_scenario,
        planner=ScriptedPlanner(
            actions=(
                PlanAction(
                    ActionType.SOCIALIZE,
                    target="listener",
                    duration=2,
                ),
            )
        ),
        dialogue_generator=FailingDialogue(),
    )
    dialogue_runner.run_for(2)
    dialogue_failure = next(
        event
        for event in dialogue_runner.events.events
        if event.event_type == "dialogue.failed"
    )
    assert dialogue_failure.payload["provider"] == "remote-dialogue"
    assert dialogue_failure.payload["latency_ms"] == 80.0
    assert dialogue_failure.payload["input_tokens"] == 12
    assert dialogue_failure.payload["output_tokens"] == 0


def test_cancelled_cognitive_requests_report_zero_use_and_known_provider() -> None:
    planner = FakePlanner()
    runner = create_runner(
        load_scenario(_scenario_path("fake-llm-planning.json")),
        planner=planner,
    )
    coordinator = runner.registry.get_resource(MacroWorkCoordinator)
    MacroPlanningSystem().update(runner.context)
    coordinator.drain(
        runner.context,
        survival_agent_ids=frozenset({"agent-001"}),
    )

    conversation = runner.registry.get_component(
        "agent-001", ConversationComponent
    )
    requested = runner.events.emit(
        "dialogue.requested",
        simulation_tick=0,
        simulation_time=0,
        agent_id="agent-001",
        payload={"target_id": "agent-001"},
    )
    conversation.request_pending = True
    coordinator.enqueue_dialogue(
        DialogueWork(
            agent_id="agent-001",
            target_id="agent-001",
            prompt="cancel me",
            top_k=1,
            requested_event_id=requested.event_id,
        )
    )
    coordinator.drain(
        runner.context,
        survival_agent_ids=frozenset({"agent-001"}),
    )

    cancellations = [
        event
        for event in runner.events.events
        if event.event_type in {"planner.cancelled", "dialogue.cancelled"}
    ]
    assert planner.call_count == 0
    assert [event.payload["provider"] for event in cancellations] == [
        "fake",
        "fake",
    ]
    assert all(event.payload["latency_ms"] == 0.0 for event in cancellations)
    assert all(event.payload["input_tokens"] == 0 for event in cancellations)
    assert all(event.payload["output_tokens"] == 0 for event in cancellations)


def test_dialogue_is_rejected_at_enqueue_when_target_is_in_system1() -> None:
    dialogue = FakeDialogueGenerator()
    embeddings = FakeEmbeddingProvider()
    runner = create_runner(
        _social_scenario(),
        planner=ScriptedPlanner(
            actions=(
                PlanAction(
                    ActionType.SOCIALIZE,
                    target="listener",
                    duration=4,
                ),
            )
        ),
        dialogue_generator=dialogue,
        embedding_provider=embeddings,
    )
    runner.run_for(1)
    runner.registry.get_component(
        "listener", DriveComponent
    ).state = System1State.BLOCKED_SURVIVAL

    runner.run_for(1)

    speaker = runner.registry.get_component(
        "speaker", ConversationComponent
    )
    listener = runner.registry.get_component(
        "listener", ConversationComponent
    )
    cancellation = next(
        event
        for event in runner.events.events
        if event.event_type == "dialogue.cancelled"
    )
    assert cancellation.payload["reason"] == "system1_preemption"
    assert dialogue.call_count == 0
    assert embeddings.call_count == 0
    assert speaker.turns == []
    assert listener.turns == []
    assert not speaker.request_pending
    assert runner.registry.get_component("speaker", PlanComponent).current is None
    social_failure = next(
        event
        for event in runner.events.events
        if event.event_type == "plan.action_failed"
    )
    assert social_failure.payload["reason"] == "system1_preemption"


def test_dialogue_is_cancelled_at_drain_when_target_enters_system1() -> None:
    dialogue = FakeDialogueGenerator()
    embeddings = FakeEmbeddingProvider()
    runner = create_runner(
        _social_scenario(),
        dialogue_generator=dialogue,
        embedding_provider=embeddings,
    )
    coordinator = runner.registry.get_resource(MacroWorkCoordinator)
    speaker = runner.registry.get_component(
        "speaker", ConversationComponent
    )
    listener = runner.registry.get_component(
        "listener", ConversationComponent
    )
    requested = runner.events.emit(
        "dialogue.requested",
        simulation_tick=0,
        simulation_time=0,
        agent_id="speaker",
        payload={"target_id": "listener"},
    )
    speaker.request_pending = True
    coordinator.enqueue_dialogue(
        DialogueWork(
            agent_id="speaker",
            target_id="listener",
            prompt="Do not generate this.",
            top_k=1,
            requested_event_id=requested.event_id,
        )
    )
    runner.registry.get_component(
        "listener", DriveComponent
    ).state = System1State.BLOCKED_SURVIVAL

    coordinator.drain(runner.context, survival_agent_ids=frozenset())

    assert dialogue.call_count == 0
    assert embeddings.call_count == 0
    assert speaker.turns == []
    assert listener.turns == []
    assert not speaker.request_pending
    assert runner.events.events[-1].event_type == "dialogue.cancelled"
    assert runner.events.events[-1].payload["reason"] == "system1_preemption"


def test_social_plan_validation_rejects_existing_non_dialogue_entity() -> None:
    dialogue = FakeDialogueGenerator()
    runner = create_runner(
        _invalid_social_target_scenario(),
        planner=ScriptedPlanner(
            actions=(
                PlanAction(
                    ActionType.SOCIALIZE,
                    target="prop",
                    duration=2,
                ),
            )
        ),
        dialogue_generator=dialogue,
    )

    runner.run_for(1)

    failure = next(
        event
        for event in runner.events.events
        if event.event_type == "planner.failed"
    )
    assert failure.payload["message"] == "invalid_social_target"
    assert runner.registry.get_component("speaker", PlanComponent).queue == []
    assert dialogue.call_count == 0


def test_social_action_start_rejects_existing_non_dialogue_entity() -> None:
    dialogue = FakeDialogueGenerator()
    runner = create_runner(
        _invalid_social_target_scenario(queued_action=True),
        dialogue_generator=dialogue,
    )

    runner.run_for(1)

    social_events = [
        event
        for event in runner.events.events
        if event.event_type.startswith("plan.action_")
    ]
    assert [event.event_type for event in social_events] == ["plan.action_failed"]
    assert social_events[0].payload["reason"] == "invalid_social_target"
    assert runner.registry.get_component("speaker", PlanComponent).current is None
    assert dialogue.call_count == 0


def test_social_action_fails_if_target_loses_dialogue_capability_at_enqueue() -> None:
    dialogue = FakeDialogueGenerator()
    runner = create_runner(
        _social_scenario(),
        planner=ScriptedPlanner(
            actions=(
                PlanAction(
                    ActionType.SOCIALIZE,
                    target="listener",
                    duration=2,
                ),
            )
        ),
        dialogue_generator=dialogue,
    )

    def remove_target_drive(event) -> None:
        if (
            event.event_type == "plan.action_started"
            and event.payload.get("action") == ActionType.SOCIALIZE.value
        ):
            runner.registry.remove_component("listener", DriveComponent)

    runner.events.subscribe(remove_target_drive)
    runner.run_for(2)

    failure = next(
        event
        for event in runner.events.events
        if event.event_type == "plan.action_failed"
    )
    assert failure.payload["reason"] == "invalid_social_target"
    assert runner.registry.get_component("speaker", PlanComponent).current is None
    assert dialogue.call_count == 0


def test_tick_subscriber_stop_sees_settled_macro_work_before_finalization(
    tmp_path: Path,
) -> None:
    planner = FakePlanner()
    dialogue = FakeDialogueGenerator()
    runner = create_runner(
        _social_scenario(),
        run_id="deferred-stop",
        planner=planner,
        dialogue_generator=dialogue,
    )
    store = SQLiteDatasetStore(tmp_path / "deferred-stop.sqlite3")
    RunDataCollector(
        store=store,
        runner=runner,
        scenario={"name": "deferred-stop"},
    )
    MacroPlanningSystem().update(runner.context)
    conversation = runner.registry.get_component(
        "speaker", ConversationComponent
    )
    requested = runner.events.emit(
        "dialogue.requested",
        simulation_tick=0,
        simulation_time=0,
        agent_id="speaker",
        payload={"target_id": "listener"},
    )
    conversation.request_pending = True
    runner.registry.get_resource(MacroWorkCoordinator).enqueue_dialogue(
        DialogueWork(
            agent_id="speaker",
            target_id="listener",
            prompt="Generate before the completed tick is published.",
            top_k=1,
            requested_event_id=requested.event_id,
        )
    )
    finalized_snapshot: dict[str, object] = {}

    def stop_on_tick(event) -> None:
        if event.event_type == "simulation.tick":
            runner.stop()

    def capture_finalized_state(event) -> None:
        if event.event_type == "simulation.stopped":
            finalized_snapshot.update(
                planner_calls=planner.call_count,
                dialogue_calls=dialogue.call_count,
                planner_pending=runner.registry.get_component(
                    "speaker", PlannerComponent
                ).request_pending,
                dialogue_pending=conversation.request_pending,
                event_count=len(runner.events.events),
            )

    runner.events.subscribe(stop_on_tick)
    runner.events.subscribe(capture_finalized_state)

    runner.run_for(3)

    assert runner.status is RunnerStatus.STOPPED
    assert runner.clock.tick == 1
    assert planner.call_count == 1
    assert dialogue.call_count == 1
    assert not runner.registry.get_component(
        "speaker", PlannerComponent
    ).request_pending
    assert not conversation.request_pending
    assert finalized_snapshot == {
        "planner_calls": 1,
        "dialogue_calls": 1,
        "planner_pending": False,
        "dialogue_pending": False,
        "event_count": len(runner.events.events),
    }
    event_types = [event.event_type for event in runner.events.events]
    assert event_types.index("planner.completed") < event_types.index(
        "simulation.tick"
    )
    assert event_types.index("dialogue.generated") < event_types.index(
        "simulation.tick"
    )
    assert runner.events.events[-1].event_type == "simulation.stopped"
    store.close()


def test_stop_flushes_pending_memory_without_running_pending_planning(
    tmp_path: Path,
) -> None:
    planner = FakePlanner()
    runner = create_runner(
        load_scenario(_scenario_path("fake-llm-planning.json")),
        run_id="stop-flush",
        planner=planner,
    )
    store = SQLiteDatasetStore(tmp_path / "stop-flush.sqlite3")
    RunDataCollector(
        store=store,
        runner=runner,
        scenario={"name": "stop-flush"},
    )
    MacroPlanningSystem().update(runner.context)
    runner.events.emit(
        "planner.failed",
        simulation_tick=0,
        simulation_time=0,
        agent_id="agent-001",
        payload={"message": "pending event"},
    )

    runner.stop()

    event_types = [event.event_type for event in runner.events.events]
    assert planner.call_count == 0
    assert event_types.index("memory.recorded") < event_types.index(
        "simulation.stopped"
    )
    store.close()
    reopened = SQLiteDatasetStore(tmp_path / "stop-flush.sqlite3")
    persisted = reopened.load_memories("stop-flush")
    reopened.close()
    assert any(
        record.metadata.get("event_type") == "planner.failed"
        for record in persisted
    )


def test_collector_finalization_flushes_only_pending_memory(
    tmp_path: Path,
) -> None:
    planner = FakePlanner()
    runner = create_runner(
        load_scenario(_scenario_path("fake-llm-planning.json")),
        run_id="finalize-flush",
        planner=planner,
    )
    store = SQLiteDatasetStore(tmp_path / "finalize-flush.sqlite3")
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario={"name": "finalize-flush"},
    )
    MacroPlanningSystem().update(runner.context)
    runner.events.emit(
        "plan.action_failed",
        simulation_tick=0,
        simulation_time=0,
        agent_id="agent-001",
        payload={"reason": "pending event"},
    )

    collector.finalize()

    assert planner.call_count == 0
    assert any(
        record.metadata.get("event_type") == "plan.action_failed"
        for record in runner.registry.get_resource(EpisodicMemoryStore).records
    )
    store.close()


def test_stop_cancels_critical_pending_memory_without_embedding(
    tmp_path: Path,
) -> None:
    embeddings = FakeEmbeddingProvider()
    runner = create_runner(
        load_scenario(_scenario_path("system1-preemption.json")),
        run_id="critical-stop",
        embedding_provider=embeddings,
    )
    database = tmp_path / "critical-stop.sqlite3"
    store = SQLiteDatasetStore(database)
    RunDataCollector(
        store=store,
        runner=runner,
        scenario={"name": "critical-stop"},
    )
    runner.run_for(1)

    runner.stop()

    assert embeddings.call_count == 0
    assert runner.registry.get_resource(EpisodicMemoryStore).records == ()
    cancellation = next(
        event
        for event in runner.events.events
        if event.event_type == "memory.cancelled"
    )
    assert cancellation.payload["reason"] == "system1_active_at_finalization"
    store.close()
    reopened = SQLiteDatasetStore(database)
    exported = "\n".join(reopened.iter_jsonl("critical-stop"))
    reopened.close()
    assert '"event_type":"memory.cancelled"' in exported


def test_finalization_cancels_critical_pending_memory_without_embedding(
    tmp_path: Path,
) -> None:
    embeddings = FakeEmbeddingProvider()
    runner = create_runner(
        load_scenario(_scenario_path("system1-preemption.json")),
        run_id="critical-finalize",
        embedding_provider=embeddings,
    )
    store = SQLiteDatasetStore(tmp_path / "critical-finalize.sqlite3")
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario={"name": "critical-finalize"},
    )
    runner.run_for(1)

    collector.finalize()

    assert embeddings.call_count == 0
    assert runner.registry.get_resource(EpisodicMemoryStore).records == ()
    assert any(
        event.event_type == "memory.cancelled"
        and event.payload["reason"] == "system1_active_at_finalization"
        for event in runner.events.events
    )
    store.close()


def test_post_cognition_tick_is_durable_without_finalization(
    tmp_path: Path,
) -> None:
    database = tmp_path / "post-cognition.sqlite3"
    runner = create_runner(
        _social_scenario(with_memory=True),
        run_id="post-cognition",
        planner=ScriptedPlanner(
            actions=(
                PlanAction(
                    ActionType.SOCIALIZE,
                    target="listener",
                    duration=4,
                ),
            )
        ),
    )
    store = SQLiteDatasetStore(database)
    RunDataCollector(
        store=store,
        runner=runner,
        scenario={"name": "post-cognition"},
    )

    runner.run_for(2)
    store.close()

    reopened = SQLiteDatasetStore(database)
    records = [
        json.loads(line) for line in reopened.iter_jsonl("post-cognition")
    ]
    reopened.close()
    event_types = [
        record["payload"]["event_type"]
        for record in records
        if record["record_type"] == "event"
    ]
    tick_two_states = [
        record
        for record in records
        if record["record_type"] == "state_vector"
        and record["simulation_tick"] == 2
        and record.get("agent_id") == "speaker"
    ]
    assert "planner.completed" in event_types
    assert "dialogue.generated" in event_types
    assert "memory.retrieved" in event_types
    assert len(tick_two_states) == 1
    state = tick_two_states[0]["payload"]
    assert state["planner"]["request_pending"] is False
    assert state["conversation"]["turn_count"] == 1
    assert state["conversation"]["latest_turn"] is not None


def test_accelerated_two_hour_run_is_deterministic_bounded_and_valid() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "long-run",
            "seed": 42,
            "world": {
                "width": 8,
                "height": 2,
                "stations": [
                    {
                        "id": "fridge-a",
                        "name": "Fridge A",
                        "position": {"x": 0, "y": 0},
                        "supported_actions": ["EAT"],
                    },
                    {
                        "id": "fridge-b",
                        "name": "Fridge B",
                        "position": {"x": 7, "y": 1},
                        "supported_actions": ["EAT"],
                    },
                    {
                        "id": "bed-a",
                        "name": "Bed A",
                        "position": {"x": 0, "y": 1},
                        "supported_actions": ["SLEEP"],
                    },
                    {
                        "id": "bed-b",
                        "name": "Bed B",
                        "position": {"x": 7, "y": 0},
                        "supported_actions": ["SLEEP"],
                    },
                    {
                        "id": "sofa-a",
                        "name": "Sofa A",
                        "position": {"x": 3, "y": 0},
                        "supported_actions": ["RELAX"],
                    },
                    {
                        "id": "sofa-b",
                        "name": "Sofa B",
                        "position": {"x": 4, "y": 1},
                        "supported_actions": ["RELAX"],
                    },
                ],
            },
            "entities": [
                {
                    "id": "agent-a",
                    "components": {
                        "position": {"x": 2, "y": 0},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                    },
                },
                {
                    "id": "agent-b",
                    "components": {
                        "position": {"x": 5, "y": 1},
                        "homeostasis": {
                            "satiety": 60,
                            "energy": 70,
                            "stress": 30,
                        },
                    },
                },
            ],
        }
    )

    def run() -> tuple[object, ...]:
        runner = create_runner(scenario)

        def assert_invariants(event) -> None:
            if event.event_type != "simulation.tick":
                return
            positions = [
                position.coordinate
                for _, position in runner.registry.query(PositionComponent)
            ]
            assert len(positions) == len(set(positions))
            for _, state in runner.registry.query(HomeostasisComponent):
                assert 0 <= state.satiety <= 100
                assert 0 <= state.energy <= 100
                assert 0 <= state.stress <= 100
            for _, drive in runner.registry.query(DriveComponent):
                assert isinstance(drive.state, System1State)

        runner.events.subscribe(assert_invariants)
        runner.run_for(7_200)
        return tuple(
            event.canonical_dict()
            for event in runner.events.events
            if event.event_type != "homeostasis.changed"
        )

    assert run() == run()
