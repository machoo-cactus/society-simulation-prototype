import math
from dataclasses import dataclass

from stage0_sim.application.cognition import EmbeddingProvider
from stage0_sim.domain.events import JsonValue


@dataclass(frozen=True, slots=True)
class MemoryConfiguration:
    semantic_weight: float = 0.6
    recency_weight: float = 0.25
    importance_weight: float = 0.15
    recency_half_life: float = 3600.0

    def __post_init__(self) -> None:
        weights = (
            self.semantic_weight,
            self.recency_weight,
            self.importance_weight,
        )
        if any(weight < 0 for weight in weights):
            raise ValueError("memory ranking weights must not be negative")
        if not math.isclose(sum(weights), 1.0):
            raise ValueError("memory ranking weights must sum to 1")
        if self.recency_half_life <= 0:
            raise ValueError("memory recency half-life must be greater than zero")


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: str
    agent_id: str
    text: str
    simulation_time: float
    importance: float
    embedding: tuple[float, ...]
    metadata: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class RetrievedMemory:
    record: MemoryRecord
    score: float
    semantic_score: float
    recency_score: float


class EpisodicMemoryStore:
    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        configuration: MemoryConfiguration | None = None,
    ) -> None:
        self.embedding_provider = embedding_provider
        self.configuration = configuration or MemoryConfiguration()
        self._records: list[MemoryRecord] = []
        self._next_id = 1

    @property
    def records(self) -> tuple[MemoryRecord, ...]:
        return tuple(self._records)

    def record(
        self,
        *,
        agent_id: str,
        text: str,
        simulation_time: float,
        importance: float,
        metadata: dict[str, JsonValue] | None = None,
    ) -> MemoryRecord:
        if not text.strip():
            raise ValueError("memory text must not be empty")
        if not 0 <= importance <= 1:
            raise ValueError("memory importance must be between 0 and 1")
        embedding = self.embedding_provider.embed((text,))[0]
        record = MemoryRecord(
            id=f"memory-{self._next_id:08d}",
            agent_id=agent_id,
            text=text,
            simulation_time=simulation_time,
            importance=importance,
            embedding=embedding,
            metadata=dict(metadata or {}),
        )
        self._next_id += 1
        self._records.append(record)
        return record

    def retrieve(
        self,
        *,
        agent_id: str,
        query: str,
        simulation_time: float,
        top_k: int,
    ) -> tuple[RetrievedMemory, ...]:
        if top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        candidates = [record for record in self._records if record.agent_id == agent_id]
        if not candidates:
            return ()
        query_embedding = self.embedding_provider.embed((query,))[0]
        ranked: list[RetrievedMemory] = []
        for record in candidates:
            semantic = _cosine_similarity(query_embedding, record.embedding)
            age = max(0.0, simulation_time - record.simulation_time)
            recency = 0.5 ** (age / self.configuration.recency_half_life)
            score = (
                self.configuration.semantic_weight * semantic
                + self.configuration.recency_weight * recency
                + self.configuration.importance_weight * record.importance
            )
            ranked.append(
                RetrievedMemory(
                    record=record,
                    score=round(score, 12),
                    semantic_score=round(semantic, 12),
                    recency_score=round(recency, 12),
                )
            )
        ranked.sort(
            key=lambda item: (
                -item.score,
                -item.record.simulation_time,
                item.record.id,
            )
        )
        return tuple(ranked[:top_k])


def _cosine_similarity(
    left: tuple[float, ...],
    right: tuple[float, ...],
) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions do not match")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )
