import hashlib
import math
from dataclasses import dataclass
from functools import partial
from typing import Protocol, runtime_checkable

from stage0_sim.application.cognition import EmbeddingProvider
from stage0_sim.application.data_capture import (
    MemoryId,
    RecordCategory,
    RecordJoinIds,
    RecordSource,
    ResearchRecorder,
)
from stage0_sim.application.information import InformationPersistence, InformationStore
from stage0_sim.application.information_context import InformationContextCapsule
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.information import (
    InformationDocument,
    InformationSource,
    TimeRange,
    VisibilityLevel,
    VisibilityPolicy,
    character_information_namespace_id,
)


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


class MemoryPersistence(InformationPersistence, Protocol):
    def save_memory(self, run_id: str, record: MemoryRecord) -> None: ...

    def load_memories(self, run_id: str) -> tuple[MemoryRecord, ...]: ...


@runtime_checkable
class AtomicMemoryPersistence(Protocol):
    def save_memory_episode(
        self,
        run_id: str,
        record: MemoryRecord,
        document: InformationDocument,
    ) -> None: ...


@runtime_checkable
class AtomicMemoryBindingPersistence(Protocol):
    def save_memory_binding(
        self,
        run_id: str,
        documents: tuple[InformationDocument, ...],
        episodes: tuple[tuple[MemoryRecord, InformationDocument], ...],
    ) -> None: ...


class EpisodicMemoryStore:
    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        configuration: MemoryConfiguration | None = None,
        information_store: InformationStore | None = None,
        research_recorder: ResearchRecorder | None = None,
    ) -> None:
        self.embedding_provider = embedding_provider
        self.configuration = configuration or MemoryConfiguration()
        self.information_store = information_store or InformationStore()
        self._records: list[MemoryRecord] = []
        self._next_id = 1
        self._persistence: MemoryPersistence | None = None
        self._run_id: str | None = None
        self.research_recorder = research_recorder

    @property
    def records(self) -> tuple[MemoryRecord, ...]:
        return tuple(self._records)

    def bind_research_recorder(self, recorder: ResearchRecorder) -> None:
        self.research_recorder = recorder

    def document(self, memory_id: str) -> InformationDocument:
        document = self.information_store.get(memory_id)
        if document.kind != "memory.episode":
            raise KeyError(f"information document is not a memory episode: {memory_id}")
        return document

    def bind_persistence(
        self,
        persistence: MemoryPersistence,
        run_id: str,
        *,
        rehydrate: bool = False,
    ) -> None:
        if self._persistence is not None:
            raise RuntimeError("memory persistence is already bound")
        information_was_bound = self.information_store.persistence_bound
        if information_was_bound:
            if (
                self.information_store.bound_run_id != run_id
                or self.information_store.bound_persistence is not persistence
            ):
                raise RuntimeError(
                    "memory and information persistence must use the same "
                    "persistence and run ID"
                )
            candidate_information = self.information_store._clone_unbound()
            documents_to_flush: tuple[InformationDocument, ...] = ()
        else:
            (
                candidate_information,
                documents_to_flush,
            ) = self.information_store._prepare_persistence_binding(
                persistence,
                run_id,
                rehydrate=rehydrate,
            )
        candidate_records = list(self._records)
        if rehydrate:
            self._stage_rehydration(
                candidate_information,
                candidate_records,
                persistence.load_memories(run_id),
            )
        for record in candidate_records:
            self._ensure_episode_document(candidate_information, record)
        episodes = tuple(
            (record, candidate_information.get(record.id))
            for record in sorted(candidate_records, key=lambda item: item.id)
        )
        paired_ids = frozenset(record.id for record, _ in episodes)
        standalone_documents = tuple(
            document
            for document in documents_to_flush
            if document.id not in paired_ids
        )
        atomic_binding = self._atomic_binding_persistence(persistence)
        if atomic_binding is not None:
            atomic_binding.save_memory_binding(
                run_id,
                standalone_documents,
                episodes,
            )
        else:
            for document in standalone_documents:
                persistence.save_information_document(run_id, document)
            atomic_persistence = (
                persistence
                if isinstance(persistence, AtomicMemoryPersistence)
                else None
            )
            for record, document in episodes:
                if atomic_persistence is not None:
                    atomic_persistence.save_memory_episode(
                        run_id,
                        record,
                        document,
                    )
                else:
                    persistence.save_information_document(run_id, document)
                    persistence.save_memory(run_id, record)
        if information_was_bound:
            self.information_store._commit_candidate_history(
                candidate_information
            )
        else:
            self.information_store._commit_persistence_binding(
                candidate_information,
                persistence,
                run_id,
            )
        self._records = candidate_records
        self._refresh_next_id()
        self._persistence = persistence
        self._run_id = run_id

    @staticmethod
    def _stage_rehydration(
        information_store: InformationStore,
        candidate_records: list[MemoryRecord],
        records: tuple[MemoryRecord, ...],
    ) -> None:
        existing_ids = {record.id for record in candidate_records}
        for record in sorted(records, key=lambda item: item.id):
            EpisodicMemoryStore._ensure_episode_document(
                information_store,
                record,
            )
            if record.id not in existing_ids:
                candidate_records.append(record)
                existing_ids.add(record.id)

    @staticmethod
    def _ensure_episode_document(
        information_store: InformationStore,
        record: MemoryRecord,
    ) -> None:
        document = _episode_document(record)
        if not information_store.has(record.id):
            information_store.register(document)
            return
        persisted_document = information_store.get(record.id)
        if persisted_document.kind != "memory.episode":
            raise ValueError(
                "memory ID collides with non-episode document: "
                f"{record.id}"
            )
        if (
            persisted_document.namespace_id
            != character_information_namespace_id(record.agent_id)
        ):
            raise ValueError(
                "memory episode namespace does not match legacy "
                f"memory owner: {record.id}"
            )

    @staticmethod
    def _atomic_binding_persistence(
        persistence: MemoryPersistence,
    ) -> AtomicMemoryBindingPersistence | None:
        if isinstance(persistence, AtomicMemoryBindingPersistence):
            return persistence
        return None

    def rehydrate(self, records: tuple[MemoryRecord, ...]) -> None:
        self._rehydrate(
            records,
            persistence=self._persistence,
            run_id=self._run_id,
        )

    def _rehydrate(
        self,
        records: tuple[MemoryRecord, ...],
        *,
        persistence: MemoryPersistence | None,
        run_id: str | None,
    ) -> None:
        existing_ids = {record.id for record in self._records}
        for record in sorted(records, key=lambda item: item.id):
            document = _episode_document(record)
            if not self.information_store.has(record.id):
                atomic_persistence = (
                    self._atomic_persistence(persistence)
                    if persistence is not None
                    else None
                )
                if (
                    atomic_persistence is not None
                    and run_id is not None
                ):
                    self.information_store.register_with_persistence(
                        document,
                        partial(
                            atomic_persistence.save_memory_episode,
                            run_id,
                            record,
                            document,
                        ),
                    )
                else:
                    self.information_store.register(document)
            else:
                self._ensure_episode_document(
                    self.information_store,
                    record,
                )
            if record.id not in existing_ids:
                self._records.append(record)
                existing_ids.add(record.id)
        self._refresh_next_id()

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
        self._refresh_next_id()
        operation_id = f"memory-embedding:{agent_id}:{self._next_id:08d}"
        self._record_embedding_request(
            operation_id,
            agent_id,
            (text,),
            "memory_generation",
        )
        try:
            embedding = self.embedding_provider.embed((text,))[0]
        except Exception as error:
            self._record_embedding_error(
                operation_id,
                agent_id,
                "memory_generation",
                error,
            )
            raise
        self._record_embedding_result(
            operation_id,
            agent_id,
            (embedding,),
            "memory_generation",
        )
        record = MemoryRecord(
            id=f"memory-{self._next_id:08d}",
            agent_id=agent_id,
            text=text,
            simulation_time=simulation_time,
            importance=importance,
            embedding=embedding,
            metadata=dict(metadata or {}),
        )
        document = _episode_document(record)
        try:
            persistence = self._persistence
            run_id = self._run_id
            atomic_persistence = (
                self._atomic_persistence(persistence)
                if persistence is not None
                else None
            )
            if (
                atomic_persistence is not None
                and run_id is not None
            ):
                self.information_store.register_with_persistence(
                    document,
                    partial(
                        atomic_persistence.save_memory_episode,
                        run_id,
                        record,
                        document,
                    ),
                )
            else:
                self.information_store.register(document)
                if persistence is not None and run_id is not None:
                    persistence.save_memory(run_id, record)
        except Exception:
            self._refresh_next_id()
            raise
        self._records.append(record)
        self._next_id += 1
        return record

    def _atomic_persistence(
        self,
        persistence: MemoryPersistence,
    ) -> AtomicMemoryPersistence | None:
        if (
            isinstance(persistence, AtomicMemoryPersistence)
            and self.information_store.bound_persistence is persistence
        ):
            return persistence
        return None

    def _refresh_next_id(self) -> None:
        candidate_ids = {
            record.id for record in self._records
        }
        candidate_ids.update(
            document.id for document in self.information_store.documents()
        )
        numeric_ids = [
            int(candidate_id.removeprefix("memory-"))
            for candidate_id in candidate_ids
            if candidate_id.startswith("memory-")
            and candidate_id.removeprefix("memory-").isdigit()
        ]
        self._next_id = max(numeric_ids, default=0) + 1

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
        operation_id = (
            f"memory-query:{agent_id}:{simulation_time:.12g}:"
            f"{len(candidates)}:{top_k}:"
            f"{hashlib.sha256(query.encode('utf-8')).hexdigest()[:16]}"
        )
        if self.research_recorder is not None:
            self.research_recorder.record(
                "memory_retrieval_request",
                {
                    "operation_id": operation_id,
                    "query": query,
                    "simulation_time": simulation_time,
                    "top_k": top_k,
                    "candidate_memory_ids": [
                        record.id for record in candidates
                    ],
                },
                category=RecordCategory.MEMORY,
                source=RecordSource.APPLICATION,
                subject_id=agent_id,
                correlation_id=operation_id,
            )
        self._record_embedding_request(
            operation_id,
            agent_id,
            (query,),
            "memory_query",
        )
        try:
            query_embedding = self.embedding_provider.embed((query,))[0]
        except Exception as error:
            self._record_embedding_error(
                operation_id,
                agent_id,
                "memory_query",
                error,
            )
            raise
        self._record_embedding_result(
            operation_id,
            agent_id,
            (query_embedding,),
            "memory_query",
        )
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
        selected = tuple(ranked[:top_k])
        if self.research_recorder is not None:
            self.research_recorder.record(
                "memory_retrieval_result",
                {
                    "operation_id": operation_id,
                    "query": query,
                    "selected": [
                        {
                            "memory_id": item.record.id,
                            "score": item.score,
                            "semantic_score": item.semantic_score,
                            "recency_score": item.recency_score,
                            "text": item.record.text,
                        }
                        for item in selected
                    ],
                },
                category=RecordCategory.MEMORY,
                source=RecordSource.APPLICATION,
                subject_id=agent_id,
                correlation_id=operation_id,
                joins=RecordJoinIds(
                    memory_id=(
                        MemoryId(selected[0].record.id)
                        if selected
                        else None
                    )
                ),
            )
        return selected

    def _record_embedding_request(
        self,
        operation_id: str,
        agent_id: str,
        texts: tuple[str, ...],
        operation: str,
    ) -> None:
        if self.research_recorder is None:
            return
        self.research_recorder.record(
            "embedding_request",
            {
                "operation_id": operation_id,
                "operation": operation,
                "provider": _provider_name(self.embedding_provider),
                "texts": list(texts),
            },
            category=RecordCategory.MEMORY,
            source=RecordSource.MODEL_PROVIDER,
            subject_id=agent_id,
            correlation_id=operation_id,
        )

    def _record_embedding_result(
        self,
        operation_id: str,
        agent_id: str,
        embeddings: tuple[tuple[float, ...], ...],
        operation: str,
    ) -> None:
        if self.research_recorder is None:
            return
        self.research_recorder.record(
            "embedding_result",
            {
                "operation_id": operation_id,
                "operation": operation,
                "provider": _provider_name(self.embedding_provider),
                "embeddings": [list(value) for value in embeddings],
            },
            category=RecordCategory.MEMORY,
            source=RecordSource.MODEL_PROVIDER,
            subject_id=agent_id,
            correlation_id=operation_id,
        )

    def _record_embedding_error(
        self,
        operation_id: str,
        agent_id: str,
        operation: str,
        error: Exception,
    ) -> None:
        if self.research_recorder is None:
            return
        self.research_recorder.record(
            "embedding_error",
            {
                "operation_id": operation_id,
                "operation": operation,
                "provider": _provider_name(self.embedding_provider),
                "error_type": type(error).__name__,
                "message": str(error),
            },
            category=RecordCategory.MEMORY,
            source=RecordSource.MODEL_PROVIDER,
            subject_id=agent_id,
            correlation_id=operation_id,
        )


def memory_context_capsules(
    store: EpisodicMemoryStore,
    retrieved: tuple[RetrievedMemory, ...],
) -> tuple[InformationContextCapsule, ...]:
    capsules: list[InformationContextCapsule] = []
    for item in retrieved:
        document = store.document(item.record.id)
        capsules.append(
            InformationContextCapsule(
                document_id=document.id,
                document_kind=document.kind,
                source_path="$",
                rendered_content=item.record.text,
                source=document.source,
                valid_time=document.valid_time,
                score=item.score,
                revision=document.revision,
                recorded_at=document.recorded_at,
            )
        )
    return tuple(capsules)


def _provider_name(provider: object) -> str:
    value = getattr(provider, "provider_name", None)
    return value if isinstance(value, str) else type(provider).__name__


def _episode_document(record: MemoryRecord) -> InformationDocument:
    source_type = "MEMORY_RECORD"
    raw_source = record.metadata.get("source")
    if isinstance(raw_source, str):
        source_type = f"{raw_source.upper()}_MEMORY"
    elif isinstance(record.metadata.get("event_id"), str):
        source_type = "DIRECT_PERCEPTION"
    reference_ids = tuple(
        value
        for key in ("event_id", "source_document_id")
        if isinstance((value := record.metadata.get(key)), str)
    )
    return InformationDocument.create(
        id=record.id,
        namespace_id=character_information_namespace_id(record.agent_id),
        kind="memory.episode",
        schema_id="memory.episode.v1",
        subject_ids=(record.agent_id,),
        content={
            "summary": record.text,
            "importance": record.importance,
            "metadata": dict(record.metadata),
        },
        source=InformationSource(
            type=source_type,
            observer_id=record.agent_id,
            reference_ids=reference_ids,
        ),
        valid_time=TimeRange(
            start=record.simulation_time,
            end=record.simulation_time,
        ),
        recorded_at=record.simulation_time,
        visibility=VisibilityPolicy(
            level=VisibilityLevel.PRIVATE,
            owner_ids=(record.agent_id,),
        ),
    )


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
