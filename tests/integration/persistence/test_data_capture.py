import asyncio
import json
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pytest

from stage0_sim.adapters.llm import ScriptedModelClient
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.agents.contracts import (
    ModelClient,
    ModelRequest,
    ModelToolCall,
    ModelTurn,
)
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.data_capture import (
    BufferedResearchRecorder,
    CaptureCoverageError,
    DatasetRecord,
    RecordCategory,
    RecordSource,
    RecordVisibility,
    ResearchTrace,
    ResearchWriteError,
    RunnerPhase,
    UnsupportedAuthoritativeValue,
    capture_coverage_manifest,
    capture_registry_state,
    serialize_authoritative,
)
from stage0_sim.application.runner import RunConfiguration, SimulationRunner
from stage0_sim.application.scenario import (
    ScenarioDefinition,
    create_runner,
    load_scenario,
)
from stage0_sim.application.telemetry import TelemetryBroker
from stage0_sim.domain.components import (
    PerceptionComponent,
    PositionComponent,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.systems import SystemContext, SystemExecutor
from stage0_sim.domain.world import Coordinate
from tests.helpers.paths import EXAMPLE_SCENARIOS


class ExampleKind(StrEnum):
    FIRST = "first"
    SECOND = "second"


@dataclass(frozen=True, slots=True)
class ExampleComponent:
    name: str
    kind: ExampleKind
    values: dict[str, tuple[int, ...]]
    labels: frozenset[str]


@dataclass(frozen=True, slots=True)
class UnsupportedComponent:
    value: object


@dataclass(slots=True)
class TraceSystem:
    trace: list[str]
    name: str = "trace"
    order: int = 10

    def update(self, _context: SystemContext) -> None:
        self.trace.append("system")


class _FailingResearchSink:
    def write(self, _trace: ResearchTrace) -> None:
        raise OSError("capture unavailable")


class _SlowModelClient(ModelClient):
    synchronous = False

    async def complete(self, _request: ModelRequest) -> ModelTurn:
        await asyncio.sleep(0.05)
        return _model_turn("skip", {"reconsider_after_seconds": 30})


def scenario_path(name: str) -> Path:
    return EXAMPLE_SCENARIOS / name


def _model_turn(name: str, arguments: dict[str, JsonValue]) -> ModelTurn:
    return ModelTurn(
        text=None,
        tool_calls=(
            ModelToolCall(
                call_id=f"call-{name}",
                name=name,
                arguments=arguments,
            ),
        ),
        finish_reason="tool_calls",
        provider="scripted",
        model="scripted-v1",
        latency_ms=2,
        input_tokens=11,
        output_tokens=3,
        provider_request_id=f"provider-{name}",
    )


def _capture_tool_scenario(
    *,
    timeout_seconds: float = 30,
) -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        {
            "name": "private-capture",
            "cognition": {
                "max_requests": 1,
                "decision_timeout_seconds": timeout_seconds,
                "max_read_tool_rounds": 1,
            },
            "world": {
                "width": 2,
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
            },
            "entities": [
                {
                    "id": "alex",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {},
                        "character_slot": {
                            "label": "Researcher",
                            "briefing": "Private briefing.",
                        },
                        "goals": {
                            "goals": [
                                {
                                    "id": "visit-lounge",
                                    "description": "Visit the lounge",
                                    "priority": 50,
                                },
                                {
                                    "id": "choose-route",
                                    "description": "Choose a route",
                                    "priority": 100,
                                },
                            ],
                        },
                        "controller": {"enabled": True},
                    },
                }
            ],
        }
    )


def test_runner_phase_hooks_wrap_systems_and_preserve_tick_completed() -> None:
    trace: list[str] = []
    systems = SystemExecutor()
    systems.add(TraceSystem(trace))
    runner = SimulationRunner(
        RunConfiguration(seed=7, run_id="phase-hooks"),
        systems=systems,
    )
    phases: list[tuple[RunnerPhase, int]] = []
    runner.subscribe_phase(
        lambda phase, observed_runner, _context: (
            phases.append((phase, observed_runner.clock.tick)),
            trace.append(phase.value),
        )
    )
    runner.subscribe_tick_completed(lambda _event: trace.append("tick_completed"))

    runner.run_for(1)
    runner.stop()

    assert phases == [
        (RunnerPhase.RUN_INITIAL, 0),
        (RunnerPhase.TICK_PRE_SYSTEMS, 1),
        (RunnerPhase.TICK_POST_SYSTEMS, 1),
        (RunnerPhase.TICK_POST_COGNITION, 1),
        (RunnerPhase.RUN_FINAL, 1),
    ]
    assert trace == [
        "run_initial",
        "tick_pre_systems",
        "system",
        "tick_post_systems",
        "tick_post_cognition",
        "tick_completed",
        "run_final",
    ]


def test_full_state_serialization_is_deterministic() -> None:
    registry = Registry()
    registry.create_entity("character")
    registry.add_component(
        "character",
        ExampleComponent(
            name="example",
            kind=ExampleKind.SECOND,
            values={"z": (3, 2), "a": (1,)},
            labels=frozenset({"beta", "alpha"}),
        ),
    )

    first = capture_registry_state(registry)
    second = capture_registry_state(registry)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    component = first["entities"][0]["components"][
        f"{__name__}.ExampleComponent"
    ]
    assert component == {
        "name": "example",
        "kind": "second",
        "values": {"a": [1], "z": [3, 2]},
        "labels": ["alpha", "beta"],
    }
    assert serialize_authoritative({ExampleKind.FIRST: {3, 1}}) == {
        "first": [1, 3]
    }


def test_unsupported_state_and_unclassified_resources_fail_explicitly() -> None:
    registry = Registry()
    registry.create_entity("character")
    registry.add_component(
        "character",
        UnsupportedComponent(object()),
    )

    with pytest.raises(
        UnsupportedAuthoritativeValue,
        match="UnsupportedComponent.value",
    ):
        capture_registry_state(registry)

    class UnknownResource:
        pass

    registry = Registry()
    registry.set_resource(UnknownResource())
    with pytest.raises(CaptureCoverageError, match="UnknownResource"):
        capture_coverage_manifest(registry)


def test_coverage_manifest_classifies_operational_resources() -> None:
    scenario = load_scenario(scenario_path("minimal.json"))
    runner = create_runner(scenario, run_id="coverage")

    snapshot = capture_registry_state(runner.registry)
    resources = snapshot["coverage"]["resources"]
    exclusions = {
        entry["type"]
        for entry in resources
        if entry["classification"] == "operational_exclusion"
    }

    assert (
        "stage0_sim.application.memory_recording.MemoryWorkCoordinator"
        in exclusions
    )
    assert (
        "stage0_sim.application.memory_recording.MemoryWorkCoordinator"
        not in snapshot["resources"]
    )
    runner.stop()


def test_collector_persists_phase_deltas_opportunities_and_population(
    tmp_path: Path,
) -> None:
    database = tmp_path / "capture.sqlite3"
    scenario = load_scenario(scenario_path("system1-preemption.json"))
    runner = create_runner(scenario, run_id="capture")
    store = SQLiteDatasetStore(database)
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario=scenario.model_dump(mode="json"),
    )

    runner.run_for(2)
    collector.finalize()
    store.close()

    connection = sqlite3.connect(database)
    phase_rows = connection.execute(
        """
        SELECT phase FROM records
        WHERE run_id = ? AND record_type = 'phase_state'
        ORDER BY sequence
        """,
        ("capture",),
    ).fetchall()
    phases = [row[0] for row in phase_rows]
    assert phases == [
        "run_initial",
        "tick_pre_systems",
        "tick_post_systems",
        "tick_post_cognition",
        "tick_pre_systems",
        "tick_post_systems",
        "tick_post_cognition",
        "run_final",
    ]
    assert connection.execute(
        "SELECT COUNT(*) FROM state_samples WHERE run_id = ?",
        ("capture",),
    ).fetchone()[0] > 0
    delta_json = connection.execute(
        """
        SELECT delta_json FROM state_deltas
        WHERE run_id = ? ORDER BY rowid LIMIT 1
        """,
        ("capture",),
    ).fetchone()[0]
    assert json.loads(delta_json)["change_count"] > 0
    assert connection.execute(
        "SELECT COUNT(*) FROM opportunity_samples WHERE run_id = ?",
        ("capture",),
    ).fetchone()[0] > 0
    opportunity_payload = json.loads(
        connection.execute(
            """
            SELECT payload_json FROM records
            WHERE run_id = ? AND record_type = 'opportunity_sample'
            ORDER BY sequence LIMIT 1
            """,
            ("capture",),
        ).fetchone()[0]
    )
    assert opportunity_payload["choice_status"] == "non_choice"
    assert all(
        option["selected"] is False
        for option in opportunity_payload["options"]
    )
    population_json = connection.execute(
        """
        SELECT population_json FROM population_samples
        WHERE run_id = ? AND phase = 'run_initial'
        """,
        ("capture",),
    ).fetchone()[0]
    assert json.loads(population_json)["entity_count"] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM resource_samples WHERE run_id = ?",
        ("capture",),
    ).fetchone()[0] > 0
    transition = connection.execute(
        """
        SELECT state_before_json, state_after_json, elapsed_simulation_time
        FROM transition_samples WHERE run_id = ?
        ORDER BY start_tick, end_tick LIMIT 1
        """,
        ("capture",),
    ).fetchone()
    assert isinstance(json.loads(transition[0]), dict)
    assert isinstance(json.loads(transition[1]), dict)
    assert transition[2] >= 0
    connection.close()


def test_interaction_perception_and_rebuild_projections(
    tmp_path: Path,
) -> None:
    database = tmp_path / "interaction-projections.sqlite3"
    registry = Registry()
    for entity_id, coordinate in (
        ("speaker", Coordinate(0, 0)),
        ("listener", Coordinate(1, 0)),
    ):
        registry.create_entity(entity_id)
        registry.add_component(entity_id, PositionComponent(coordinate))
    registry.add_component(
        "speaker",
        PerceptionComponent(visible_now={"listener"}),
    )
    runner = SimulationRunner(
        RunConfiguration(seed=3, run_id="interaction-projections"),
        registry=registry,
    )
    store = SQLiteDatasetStore(database)
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario={"name": "interaction-projections"},
    )

    runner.run_for(1)
    speech = runner.events.emit(
        "speech.started",
        simulation_tick=runner.clock.tick,
        simulation_time=runner.clock.simulation_time,
        agent_id="speaker",
        payload={
            "target_id": "listener",
            "text": "Hello",
            "channel": "voice",
            "decision_id": "decision-speech",
            "action_id": "action-speech",
            "tool_call_id": "tool-speech",
        },
        correlation_id="decision-speech",
    )
    runner.events.emit(
        "speech.delivered",
        simulation_tick=runner.clock.tick,
        simulation_time=runner.clock.simulation_time,
        agent_id="speaker",
        payload={
            "target_id": "listener",
            "recipient_ids": ["listener"],
            "text": "Hello",
            "channel": "voice",
        },
        causation_id=speech.event_id,
        correlation_id="decision-speech",
    )
    runner.events.emit(
        "transaction.requested",
        simulation_tick=runner.clock.tick,
        simulation_time=runner.clock.simulation_time,
        agent_id="listener",
        payload={
            "request_id": "request-1",
            "point_id": "counter",
            "offer_id": "coffee",
        },
    )
    runner.events.emit(
        "transaction.completed",
        simulation_tick=runner.clock.tick,
        simulation_time=runner.clock.simulation_time,
        agent_id="listener",
        payload={
            "request_id": "request-1",
            "point_id": "counter",
            "offer_id": "coffee",
            "operator_id": "speaker",
        },
    )
    runner.events.emit(
        "transaction.failed",
        simulation_tick=runner.clock.tick,
        simulation_time=runner.clock.simulation_time,
        agent_id="listener",
        payload={
            "point_id": "counter",
            "offer_id": "coffee",
            "reason": "transaction_point_at_capacity",
        },
    )
    fact = {
        "fact_id": "fact-manual",
        "event_id": speech.event_id,
        "tick": runner.clock.tick,
        "fact_type": "heard_speech",
        "subject_id": "speaker",
        "object_id": "listener",
        "location_id": None,
        "properties": {"text": "Hello"},
        "modality": "auditory",
        "disclosure": "LOCAL_AUDITORY",
    }
    runner.events.emit(
        "perception.delivered",
        simulation_tick=runner.clock.tick,
        simulation_time=runner.clock.simulation_time,
        agent_id="listener",
        payload={
            "fact_id": "fact-manual",
            "observer_id": "listener",
            "perceived_tick": runner.clock.tick,
            "fact_age": 0.0,
            "salience": 0.9,
            "fact": fact,
        },
        causation_id=speech.event_id,
    )
    runner.events.emit(
        "perception.dropped",
        simulation_tick=runner.clock.tick,
        simulation_time=runner.clock.simulation_time,
        agent_id="listener",
        payload={
            "fact_id": "fact-manual",
            "observer_id": "listener",
            "perceived_tick": runner.clock.tick,
            "fact_age": 0.0,
            "salience": 0.9,
            "reason": "inbox_limit",
            "fact": fact,
        },
        causation_id=speech.event_id,
    )
    registry.get_component(
        "speaker", PerceptionComponent
    ).visible_now.clear()
    registry.get_component(
        "listener", PositionComponent
    ).coordinate = Coordinate(2, 0)
    runner.run_for(1)
    collector.finalize()

    connection = sqlite3.connect(database)
    episodes = {
        row[0]: (row[1], json.loads(row[2]))
        for row in connection.execute(
            """
            SELECT interaction_type, status, episode_json
            FROM interaction_episodes WHERE run_id = ?
            """,
            ("interaction-projections",),
        )
    }
    assert episodes["direct_speech"][0] == "delivered"
    speech_participants = episodes["direct_speech"][1]["participants"]
    assert {
        (participant["participant_id"], participant["role"])
        for participant in speech_participants
    } >= {
        ("speaker", "speaker"),
        ("listener", "addressee"),
        ("listener", "recipient"),
    }
    assert episodes["transaction"][0] in {"completed", "failed"}
    assert episodes["staffed_service"][0] == "completed"
    assert {
        (participant["participant_id"], participant["role"])
        for participant in episodes["staffed_service"][1]["participants"]
    } >= {
        ("listener", "customer"),
        ("speaker", "service_provider"),
    }
    assert episodes["shared_resource_contention"][0] == "failed"
    assert episodes["visibility"][0] == "ended"
    assert episodes["co_presence"][0] == "ended"
    assert connection.execute(
        "SELECT COUNT(*) FROM perception_facts WHERE run_id = ?",
        ("interaction-projections",),
    ).fetchone()[0] == 1
    deliveries = connection.execute(
        """
        SELECT observer_id, status, reason, salience, delivery_json
        FROM perception_deliveries WHERE run_id = ? ORDER BY status
        """,
        ("interaction-projections",),
    ).fetchall()
    assert [(row[0], row[1], row[2]) for row in deliveries] == [
        ("listener", "delivered", None),
        ("listener", "dropped", "inbox_limit"),
    ]
    assert all(row[3] == 0.9 for row in deliveries)
    assert all(
        json.loads(row[4])["disclosure"] == "LOCAL_AUDITORY"
        for row in deliveries
    )
    raw_count = connection.execute(
        "SELECT COUNT(*) FROM records WHERE run_id = ?",
        ("interaction-projections",),
    ).fetchone()[0]
    before = connection.execute(
        """
        SELECT interaction_id, episode_json FROM interaction_episodes
        WHERE run_id = ? ORDER BY interaction_id
        """,
        ("interaction-projections",),
    ).fetchall()
    connection.close()

    first = store.rebuild_run_projections("interaction-projections")
    second = store.rebuild_run_projections("interaction-projections")
    assert first == second
    connection = sqlite3.connect(database)
    after = connection.execute(
        """
        SELECT interaction_id, episode_json FROM interaction_episodes
        WHERE run_id = ? ORDER BY interaction_id
        """,
        ("interaction-projections",),
    ).fetchall()
    assert after == before
    assert connection.execute(
        "SELECT COUNT(*) FROM records WHERE run_id = ?",
        ("interaction-projections",),
    ).fetchone()[0] == raw_count
    connection.close()
    summary = store.summary("interaction-projections")
    assert summary["capture_complete"] is True
    assert summary["derived_feature_counts"]["interaction_episodes"] >= 5
    store.close()


def test_projection_rebuild_preserves_unfinished_interactions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unfinished-interaction.sqlite3"
    store = SQLiteDatasetStore(database)
    store.begin_run(
        run_id="unfinished-interaction",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "unfinished-interaction"},
    )
    participants = [
        {"participant_id": "speaker", "role": "speaker"},
        {"participant_id": "listener", "role": "listener"},
    ]
    started = DatasetRecord(
        run_id="unfinished-interaction",
        sequence=1,
        record_type="interaction_started",
        simulation_tick=1,
        simulation_time=1,
        subject_id="speaker",
        payload={
            "interaction_id": "interaction-1",
            "interaction_type": "direct_speech",
            "status": "active",
            "participants": participants,
            "content_visibility": "PRIVATE_RESEARCH",
            "context": {"location_id": "room"},
        },
        category=RecordCategory.INTERACTION,
        source=RecordSource.DERIVED,
        visibility=RecordVisibility.PRIVATE_RESEARCH,
    )
    constituent = {
        "event_id": "event-1",
        "event_type": "speech.started",
        "simulation_tick": 1,
        "simulation_time": 1.0,
        "agent_id": "speaker",
        "payload": {"target_id": "listener"},
    }
    event = DatasetRecord(
        run_id="unfinished-interaction",
        sequence=2,
        record_type="interaction_event",
        simulation_tick=1,
        simulation_time=1,
        subject_id="speaker",
        payload={
            "interaction_id": "interaction-1",
            "event_index": 0,
            "event": constituent,
        },
        category=RecordCategory.INTERACTION,
        source=RecordSource.DERIVED,
        visibility=RecordVisibility.PRIVATE_RESEARCH,
    )
    store.append(started)
    store.append_interaction(
        run_id="unfinished-interaction",
        interaction_id="interaction-1",
        record_id=started.record_id,
        interaction_type="direct_speech",
        start_tick=1,
        end_tick=None,
        status="active",
        context={"location_id": "room"},
    )
    for participant in participants:
        store.append_interaction_participant(
            run_id="unfinished-interaction",
            interaction_id="interaction-1",
            participant_id=str(participant["participant_id"]),
            role=str(participant["role"]),
            participant=participant,
        )
    store.append(event)
    store.append_interaction_event(
        run_id="unfinished-interaction",
        interaction_id="interaction-1",
        event_id="event-1",
        record_id=event.record_id,
        event_index=0,
        event_type="speech.started",
        simulation_tick=1,
        event=constituent,
    )
    store.flush()
    store.complete_run(
        "unfinished-interaction",
        status="interrupted",
        final_tick=1,
        final_simulation_time=1,
    )

    store.rebuild_run_projections("unfinished-interaction")
    connection = sqlite3.connect(database)
    interaction = connection.execute(
        """
        SELECT status FROM interactions
        WHERE run_id = ? AND interaction_id = ?
        """,
        ("unfinished-interaction", "interaction-1"),
    ).fetchone()
    participant_count = connection.execute(
        """
        SELECT COUNT(*) FROM interaction_participants
        WHERE run_id = ? AND interaction_id = ?
        """,
        ("unfinished-interaction", "interaction-1"),
    ).fetchone()[0]
    event_count = connection.execute(
        """
        SELECT COUNT(*) FROM interaction_events
        WHERE run_id = ? AND interaction_id = ?
        """,
        ("unfinished-interaction", "interaction-1"),
    ).fetchone()[0]
    connection.close()
    store.close()

    assert interaction == ("active",)
    assert participant_count == 2
    assert event_count == 1


def test_collection_does_not_change_canonical_simulation_behavior(
    tmp_path: Path,
) -> None:
    scenario = load_scenario(scenario_path("system1-preemption.json"))
    observed = create_runner(scenario, run_id="observed")
    baseline = create_runner(scenario, run_id="baseline")
    store = SQLiteDatasetStore(tmp_path / "canonical.sqlite3")
    collector = RunDataCollector(
        store=store,
        runner=observed,
        scenario=scenario.model_dump(mode="json"),
    )

    observed.run_for(3)
    baseline.run_for(3)

    assert [event.canonical_dict() for event in observed.events.events] == [
        event.canonical_dict() for event in baseline.events.events
    ]
    collector.finalize()
    baseline.stop()
    store.close()


def test_derived_feature_output_is_deterministic(tmp_path: Path) -> None:
    def collect(database: Path) -> list[tuple[str, str, str, str]]:
        scenario = load_scenario(scenario_path("system1-preemption.json"))
        runner = create_runner(scenario, run_id="canonical-features")
        store = SQLiteDatasetStore(database)
        collector = RunDataCollector(
            store=store,
            runner=runner,
            scenario=scenario.model_dump(mode="json"),
        )
        runner.run_for(3)
        collector.finalize()
        connection = sqlite3.connect(database)
        rows = connection.execute(
            """
            SELECT schema_id, schema_version, record_type, payload_json
            FROM records
            WHERE run_id = ? AND source = 'DERIVED'
            ORDER BY sequence
            """,
            ("canonical-features",),
        ).fetchall()
        connection.close()
        store.close()
        return rows

    assert collect(tmp_path / "features-a.sqlite3") == collect(
        tmp_path / "features-b.sqlite3"
    )


def test_projection_rebuild_failure_rolls_back_without_deleting_raw(
    tmp_path: Path,
) -> None:
    store = SQLiteDatasetStore(tmp_path / "rebuild-rollback.sqlite3")
    store.begin_run(
        run_id="rebuild-rollback",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "rebuild-rollback"},
    )
    population = DatasetRecord(
        run_id="rebuild-rollback",
        sequence=1,
        record_type="population_sample",
        simulation_tick=0,
        simulation_time=0,
        payload={"entity_count": 1},
        category=RecordCategory.POPULATION,
        source=RecordSource.DERIVED,
        phase=RunnerPhase.RUN_INITIAL,
    )
    store.append(population)
    store.append_population_sample(
        run_id="rebuild-rollback",
        population_sample_id=f"{population.record_id}:population",
        record_id=population.record_id,
        simulation_tick=0,
        phase=RunnerPhase.RUN_INITIAL,
        population=population.payload,
    )
    store.append(
        DatasetRecord(
            run_id="rebuild-rollback",
            sequence=2,
            record_type="transition_sample",
            simulation_tick=1,
            simulation_time=1,
            subject_id="character",
            payload={"malformed": True},
            category=RecordCategory.TRANSITION,
            source=RecordSource.DERIVED,
        )
    )
    store.flush()
    store.complete_run(
        "rebuild-rollback",
        status="failed",
        final_tick=1,
        final_simulation_time=1,
    )

    with pytest.raises(RuntimeError, match="projection rebuild failed"):
        store.rebuild_run_projections("rebuild-rollback")

    connection = sqlite3.connect(store.path)
    assert connection.execute(
        "SELECT COUNT(*) FROM population_samples WHERE run_id = ?",
        ("rebuild-rollback",),
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM records WHERE run_id = ?",
        ("rebuild-rollback",),
    ).fetchone()[0] == 2
    connection.close()
    store.close()


def test_private_recorder_is_not_an_event_or_telemetry_transport() -> None:
    runner = SimulationRunner(
        RunConfiguration(seed=1, run_id="private-recorder")
    )
    broker = TelemetryBroker(runner)

    runner.research.record(
        "decision_request",
        {"secret": "private prompt"},
        category=RecordCategory.DECISION,
    )

    assert runner.events.events == ()
    assert broker.messages_after(0) == ()
    trace = runner.research.drain()[0]
    assert trace.visibility is RecordVisibility.PRIVATE_RESEARCH
    assert trace.payload["secret"] == "private prompt"

    failing = BufferedResearchRecorder(_FailingResearchSink())
    with pytest.raises(ResearchWriteError, match="capture unavailable"):
        failing.record("model_request", {"request": "private"})
    assert failing.failures


def test_tool_decision_capture_persists_rounds_choices_and_action_outcome(
    tmp_path: Path,
) -> None:
    database = tmp_path / "private-tool-capture.sqlite3"
    scenario = _capture_tool_scenario()
    client = ScriptedModelClient(
        (
            _model_turn(
                "check_environment",
                {"topics": ["time", "weather"]},
            ),
            _model_turn("navigate_to", {"target_id": "lounge"}),
        )
    )
    runner = create_runner(
        scenario,
        model_client=client,
        run_id="private-tool-capture",
    )
    broker = TelemetryBroker(runner)
    store = SQLiteDatasetStore(database)
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario=scenario.model_dump(mode="json"),
    )

    runner.run_for(3)
    telemetry_json = json.dumps(
        [message.to_dict() for message in broker.messages_after(0)]
    )
    assert "Private briefing." not in telemetry_json
    assert "decision_request" not in telemetry_json
    collector.finalize()
    store.close()

    connection = sqlite3.connect(database)
    request_json, visibility = connection.execute(
        """
        SELECT payload_json, visibility FROM records
        WHERE run_id = ? AND record_type = 'decision_request'
        ORDER BY sequence LIMIT 1
        """,
        ("private-tool-capture",),
    ).fetchone()
    request = json.loads(request_json)["request"]
    assert visibility == "PRIVATE_RESEARCH"
    assert request["character_description"]
    assert request["situation_description"] == "Private briefing."
    assert [
        goal["id"] for goal in request["observation"]["structured_goals"]
    ] == ["visit-lounge", "choose-route"]
    assert request["allowed_tools"]
    assert request["information_retrieval_performed"] is True
    assert isinstance(request["retrieved_information"], list)
    evaluations = [
        json.loads(row[0])
        for row in connection.execute(
            """
            SELECT payload_json FROM records
            WHERE run_id = ? AND record_type = 'cognition_evaluation'
            ORDER BY sequence
            """,
            ("private-tool-capture",),
        )
    ]
    assert evaluations[0]["eligible"] is True
    assert any(
        evaluation["eligible"] is False
        and evaluation["reasons"]
        and isinstance(evaluation["gates"], dict)
        for evaluation in evaluations[1:]
    )

    requests = connection.execute(
        """
        SELECT model_request_id, request_json, status
        FROM model_requests WHERE run_id = ?
        ORDER BY model_request_id
        """,
        ("private-tool-capture",),
    ).fetchall()
    assert len(requests) == 2
    second_messages = json.loads(requests[1][1])["messages"]
    assert any(message["role"] == "tool" for message in second_messages)
    assert all(row[2] == "completed" for row in requests)
    assert connection.execute(
        "SELECT COUNT(*) FROM model_turns WHERE run_id = ?",
        ("private-tool-capture",),
    ).fetchone()[0] == 2
    option_types = {
        row[0]
        for row in connection.execute(
            """
            SELECT option_type FROM decision_options
            WHERE run_id = ?
            """,
            ("private-tool-capture",),
        )
    }
    assert {"tool", "target", "travel_mode"} <= option_types
    assert connection.execute(
        """
        SELECT status FROM tool_executions
        WHERE run_id = ? AND tool_name = 'check_environment'
        """,
        ("private-tool-capture",),
    ).fetchone()[0] == "read_completed"
    decision = connection.execute(
        """
        SELECT status, selected_option_id, outcome_json FROM decisions
        WHERE run_id = ? ORDER BY simulation_tick LIMIT 1
        """,
        ("private-tool-capture",),
    ).fetchone()
    assert decision[0:2] == ("completed", "tool:navigate_to")
    assert json.loads(decision[2])["terminal_event_type"] == (
        "action.completed"
    )
    episode = connection.execute(
        """
        SELECT status, action_id, tool_call_id, delays_json
        FROM decision_episodes WHERE run_id = ?
        ORDER BY requested_tick LIMIT 1
        """,
        ("private-tool-capture",),
    ).fetchone()
    assert episode[0] == "completed"
    assert episode[1] is not None
    assert episode[2] == "call-navigate_to"
    assert "terminal" in json.loads(episode[3])
    connection.close()


def test_timeout_capture_closes_decision_without_guessing_action_success(
    tmp_path: Path,
) -> None:
    database = tmp_path / "private-timeout.sqlite3"
    scenario = _capture_tool_scenario(timeout_seconds=0.01)
    runner = create_runner(
        scenario,
        model_client=_SlowModelClient(),
        run_id="private-timeout",
    )
    store = SQLiteDatasetStore(database)
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario=scenario.model_dump(mode="json"),
    )

    runner.run_for(1)
    collector.finalize()
    store.close()

    connection = sqlite3.connect(database)
    assert connection.execute(
        """
        SELECT status FROM model_requests
        WHERE run_id = ? ORDER BY model_request_id LIMIT 1
        """,
        ("private-timeout",),
    ).fetchone()[0] == "timeout"
    assert connection.execute(
        """
        SELECT role FROM model_turns
        WHERE run_id = ? ORDER BY model_request_id LIMIT 1
        """,
        ("private-timeout",),
    ).fetchone()[0] == "error"
    status, action_id, reason = connection.execute(
        """
        SELECT status, action_id, terminal_reason
        FROM decision_episodes WHERE run_id = ?
        """,
        ("private-timeout",),
    ).fetchone()
    assert status == "failed"
    assert action_id is None
    assert reason == "provider_timeout"
    connection.close()
