import inspect
from pathlib import Path, PurePosixPath

from stage0_sim.adapters.llm import FakeEmbeddingProvider
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.memory import EpisodicMemoryStore
from stage0_sim.application.scenario import ScenarioDefinition, create_runner, load_scenario
from stage0_sim.domain.components import (
    DriveComponent,
    HomeostasisComponent,
    PositionComponent,
    System1State,
)
from tests.helpers.paths import CATALOG_SCENARIOS


def _scenario_path(name: str) -> Path:
    return CATALOG_SCENARIOS / name


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


def test_stop_cancels_critical_pending_memory_without_embedding(
    tmp_path: Path,
) -> None:
    embeddings = FakeEmbeddingProvider()
    runner = create_runner(
        load_scenario(_scenario_path("needs-and-preemption.json")),
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
        load_scenario(_scenario_path("needs-and-preemption.json")),
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
