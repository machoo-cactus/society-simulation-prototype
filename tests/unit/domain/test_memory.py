from dataclasses import dataclass
from pathlib import Path

from stage0_sim.adapters.llm import FakeEmbeddingProvider
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.cognition import EmbeddingError
from stage0_sim.application.memory import EpisodicMemoryStore, MemoryConfiguration
from stage0_sim.application.scenario import create_runner, load_scenario
from tests.helpers.paths import EXAMPLE_SCENARIOS


@dataclass
class MappingEmbeddingProvider:
    vectors: dict[str, tuple[float, ...]]
    call_count: int = 0

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.call_count += 1
        return tuple(self.vectors[text] for text in texts)


class FailingEmbeddingProvider:
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        del texts
        raise EmbeddingError("embedding server unavailable")


def test_retrieval_combines_semantics_recency_and_importance() -> None:
    provider = MappingEmbeddingProvider(
        {
            "work query": (1.0, 0.0),
            "relevant old work": (1.0, 0.0),
            "irrelevant recent lunch": (0.0, 1.0),
            "relevant recent work": (1.0, 0.0),
        }
    )
    store = EpisodicMemoryStore(
        provider,
        MemoryConfiguration(recency_half_life=100),
    )
    store.record(
        agent_id="agent",
        text="relevant old work",
        simulation_time=0,
        importance=0.5,
    )
    store.record(
        agent_id="agent",
        text="irrelevant recent lunch",
        simulation_time=100,
        importance=0.5,
    )
    store.record(
        agent_id="agent",
        text="relevant recent work",
        simulation_time=100,
        importance=0.9,
    )

    results = store.retrieve(
        agent_id="agent",
        query="work query",
        simulation_time=100,
        top_k=3,
    )

    assert [result.record.text for result in results] == [
        "relevant recent work",
        "relevant old work",
        "irrelevant recent lunch",
    ]
    assert results[0].score > results[1].score > results[2].score


def test_retrieval_ties_are_deterministic() -> None:
    provider = MappingEmbeddingProvider(
        {"query": (1.0,), "first": (1.0,), "second": (1.0,)}
    )
    store = EpisodicMemoryStore(provider)
    first = store.record(
        agent_id="agent",
        text="first",
        simulation_time=10,
        importance=0.5,
    )
    second = store.record(
        agent_id="agent",
        text="second",
        simulation_time=10,
        importance=0.5,
    )

    results = store.retrieve(
        agent_id="agent",
        query="query",
        simulation_time=10,
        top_k=2,
    )

    assert [result.record.id for result in results] == [first.id, second.id]


def test_saved_memory_survives_close_without_run_finalization(
    tmp_path: Path,
) -> None:
    database = tmp_path / "durable-memory.sqlite3"
    persistence = SQLiteDatasetStore(database)
    persistence.begin_run(
        run_id="durable-memory",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "durability"},
    )
    memory = EpisodicMemoryStore(FakeEmbeddingProvider())
    memory.bind_persistence(persistence, "durable-memory")
    recorded = memory.record(
        agent_id="agent",
        text="Persist this before the run finalizes.",
        simulation_time=0,
        importance=0.8,
    )
    persistence.close()

    reopened = SQLiteDatasetStore(database)
    persisted = reopened.load_memories("durable-memory")
    persisted_documents = reopened.load_information_documents("durable-memory")
    reopened.close()

    assert persisted == (recorded,)
    assert persisted_documents == (memory.document(recorded.id),)


def test_meaningful_events_are_recorded_but_routine_ticks_are_not() -> None:
    scenario_path = EXAMPLE_SCENARIOS / "system1-preemption.json"
    embedding_provider = FakeEmbeddingProvider()
    runner = create_runner(
        load_scenario(scenario_path),
        embedding_provider=embedding_provider,
    )
    store = runner.registry.get_resource(EpisodicMemoryStore)

    runner.run_for(11)

    event_types = [record.metadata["event_type"] for record in store.records]
    assert "threshold.breached" in event_types
    assert "system1.activated" in event_types
    assert "system1.resolved" in event_types
    assert "simulation.tick" not in event_types
    assert sum(
        event.event_type == "memory.recorded" for event in runner.events.events
    ) == len(store.records)


def test_physical_only_ticks_make_zero_embedding_calls() -> None:
    scenario_path = EXAMPLE_SCENARIOS / "navigation.json"
    provider = FakeEmbeddingProvider()
    runner = create_runner(
        load_scenario(scenario_path),
        embedding_provider=provider,
    )

    runner.run_for(20)

    assert provider.call_count == 0


def test_embedding_failure_is_observable_and_does_not_stop_ticks() -> None:
    scenario_path = EXAMPLE_SCENARIOS / "system1-preemption.json"
    runner = create_runner(
        load_scenario(scenario_path),
        embedding_provider=FailingEmbeddingProvider(),
    )

    runner.run_for(11)

    assert runner.clock.tick == 11
    failures = [
        event for event in runner.events.events if event.event_type == "memory.failed"
    ]
    assert failures
    assert failures[0].payload["message"] == "embedding server unavailable"
