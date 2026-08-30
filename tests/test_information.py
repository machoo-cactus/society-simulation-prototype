import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.information import (
    InformationQuery,
    InformationRetriever,
    InformationStore,
)
from stage0_sim.application.memory import EpisodicMemoryStore, MemoryRecord
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.domain.components import (
    CharacterProfileComponent,
    InformationNamespaceComponent,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.information import (
    InformationDocument,
    InformationSource,
    VisibilityLevel,
    VisibilityPolicy,
    canonical_json,
    canonical_json_hash,
    character_dossier_document_id,
    character_information_namespace_id,
    information_document_from_dict,
)


@dataclass
class CountingEmbeddingProvider:
    call_count: int = 0

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.call_count += 1
        return tuple((1.0, 0.0) for _ in texts)


def _document(
    *,
    document_id: str = "document-1",
    content: JsonValue,
    revision: int = 1,
) -> InformationDocument:
    return InformationDocument.create(
        id=document_id,
        namespace_id=character_information_namespace_id("agent-001"),
        kind="character.dossier",
        schema_id="generic.v1",
        subject_ids=("agent-001",),
        content=content,
        source=InformationSource(type="TEST"),
        visibility=VisibilityPolicy(
            level=VisibilityLevel.PRIVATE,
            owner_ids=("agent-001",),
        ),
        revision=revision,
    )


def test_canonical_documents_preserve_nested_json_and_hash_stably() -> None:
    first_content = {
        "experimental": {
            "nested": [
                {"enabled": True, "score": 1.25, "notes": None},
                ["上海", 7],
            ]
        },
        "identity": {"display_name": "Alex"},
    }
    reordered_content = {
        "identity": {"display_name": "Alex"},
        "experimental": {
            "nested": [
                {"notes": None, "score": 1.25, "enabled": True},
                ["上海", 7],
            ]
        },
    }

    first_json = canonical_json(first_content)
    first_hash = canonical_json_hash(first_content)
    document = _document(content=first_content)
    first_content["experimental"]["nested"][0]["enabled"] = False

    assert document.content == reordered_content
    assert document.to_dict()["content"] == reordered_content
    assert first_json == canonical_json(reordered_content)
    assert first_hash == canonical_json_hash(reordered_content)
    assert document.content_hash == first_hash


def test_document_content_and_source_metadata_return_detached_json_values() -> None:
    source = InformationSource(
        type="TEST",
        metadata={
            "nested": {
                "values": [1, {"enabled": True}],
            }
        },
    )
    document = InformationDocument.create(
        id="immutable-document",
        namespace_id=character_information_namespace_id("agent-001"),
        kind="knowledge.note",
        schema_id="generic.v1",
        subject_ids=("agent-001",),
        content={
            "nested": {
                "values": ["original", {"enabled": True}],
            }
        },
        source=source,
    )
    original_hash = hash(document)
    original_source_hash = hash(source)

    content = document.content
    assert isinstance(content, dict)
    nested_content = content["nested"]
    assert isinstance(nested_content, dict)
    values = nested_content["values"]
    assert isinstance(values, list)
    values[0] = "changed"
    nested_metadata = source.metadata["nested"]
    assert isinstance(nested_metadata, dict)
    metadata_values = nested_metadata["values"]
    assert isinstance(metadata_values, list)
    metadata_values.append("changed")
    dumped = document.to_dict()
    dumped_content = dumped["content"]
    assert isinstance(dumped_content, dict)
    dumped_content["extra"] = "changed"
    dumped_source = dumped["source"]
    assert isinstance(dumped_source, dict)
    dumped_metadata = dumped_source["metadata"]
    assert isinstance(dumped_metadata, dict)
    dumped_metadata["extra"] = "changed"

    expected_content = {
        "nested": {
            "values": ["original", {"enabled": True}],
        }
    }
    expected_metadata = {
        "nested": {
            "values": [1, {"enabled": True}],
        }
    }
    assert document.content == expected_content
    assert source.metadata == expected_metadata
    assert document.content_hash == canonical_json_hash(expected_content)
    assert hash(document) == original_hash
    assert hash(source) == original_source_hash
    round_trip = information_document_from_dict(document.to_dict())
    assert round_trip == document
    assert hash(round_trip) == original_hash


def test_information_store_retains_revision_history() -> None:
    store = InformationStore()
    original = store.register(_document(content={"status": "draft"}))
    revised = store.revise(
        original.id,
        {"status": "published", "details": {"version": 2}},
    )

    assert store.get(original.id) == revised
    assert store.get(original.id, revision=1) == original
    assert [item.revision for item in store.history(original.id)] == [1, 2]
    assert revised.content_hash != original.content_hash


def test_runner_creates_dossier_without_embedding_or_profile_regression() -> None:
    provider = CountingEmbeddingProvider()
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "information-dossier",
            "character_profiles": {
                "alex": {
                    "identity": {
                        "display_name": "Alex Chen",
                        "occupation": "Research engineer",
                    },
                    "motivations": {"goals": ["Finish the report"]},
                    "custom_sections": [
                        {
                            "id": "experiment",
                            "title": "Experiment",
                            "fields": [
                                {
                                    "key": "nested_data",
                                    "label": "Nested data",
                                    "value": {
                                        "strategy": "landmarks",
                                        "weights": [1, 2, {"future": True}],
                                    },
                                }
                            ],
                        }
                    ],
                }
            },
            "world": {"width": 1, "height": 1},
            "entities": [
                {
                    "id": "agent-001",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "character_profile": {"profile_ref": "alex"},
                    },
                }
            ],
        }
    )

    runner = create_runner(scenario, embedding_provider=provider)
    profile = runner.registry.get_component(
        "agent-001", CharacterProfileComponent
    )
    namespace = runner.registry.get_component(
        "agent-001", InformationNamespaceComponent
    )
    information = runner.registry.get_resource(InformationStore)
    dossier = information.get(character_dossier_document_id("agent-001"))

    assert provider.call_count == 0
    assert profile.profile_id == "alex"
    assert profile.display_name == "Alex Chen"
    assert profile.goals == ("Finish the report",)
    assert profile.content_hash
    assert namespace.namespace_id == character_information_namespace_id(
        "agent-001"
    )
    assert namespace.document_ids == (dossier.id,)
    assert dossier.kind == "character.dossier"
    assert dossier.content["custom_sections"][0]["fields"][0]["value"] == {
        "strategy": "landmarks",
        "weights": [1, 2, {"future": True}],
    }


def test_memory_records_have_episode_documents_and_legacy_rehydration() -> None:
    provider = CountingEmbeddingProvider()
    information = InformationStore()
    memory = EpisodicMemoryStore(provider, information_store=information)
    recorded = memory.record(
        agent_id="agent-001",
        text="Jordan warned that the west entrance was closed.",
        simulation_time=42,
        importance=0.8,
        metadata={
            "event_id": "event-42",
            "payload": {"recipient_ids": ["agent-001"], "closed": True},
        },
    )
    episode = memory.document(recorded.id)

    assert recorded.id == "memory-00000001"
    assert episode.kind == "memory.episode"
    assert episode.namespace_id == character_information_namespace_id("agent-001")
    assert episode.content["summary"] == recorded.text
    assert episode.content["metadata"] == recorded.metadata
    assert episode.source.type == "DIRECT_PERCEPTION"
    assert episode.source.reference_ids == ("event-42",)

    rehydration_provider = CountingEmbeddingProvider()
    rehydrated = EpisodicMemoryStore(rehydration_provider)
    rehydrated.rehydrate((recorded,))
    synthesized = rehydrated.document(recorded.id)

    assert rehydration_provider.call_count == 0
    assert rehydrated.records == (recorded,)
    assert synthesized == episode


def test_retrieval_expands_relevant_anchor_to_coherent_parent() -> None:
    information = InformationStore()
    information.register(
        _document(
            document_id=character_dossier_document_id("agent-001"),
            content={
                "capabilities": {
                    "driving": {
                        "experience": "moderate",
                        "licences": ["car"],
                        "traffic_preference": "avoids dense traffic",
                    }
                },
                "identity": {"display_name": "Alex Chen"},
            },
        )
    )
    memory = EpisodicMemoryStore(
        CountingEmbeddingProvider(),
        information_store=information,
    )
    memory.record(
        agent_id="agent-001",
        text="Jordan warned that the west entrance was closed.",
        simulation_time=10,
        importance=0.8,
    )
    retriever = InformationRetriever(information)
    query = InformationQuery(
        character_id="agent-001",
        text="driving car",
        simulation_time=20,
        token_budget=100,
    )

    first = retriever.retrieve(query)
    second = retriever.retrieve(query)

    assert first == second
    assert first[0].document_kind == "character.dossier"
    assert first[0].source_path == "$.capabilities.driving"
    assert '"experience":"moderate"' in first[0].rendered_content
    assert '"licences":["car"]' in first[0].rendered_content
    assert '"traffic_preference":"avoids dense traffic"' in first[0].rendered_content


def test_retrieval_supports_references_and_opt_in_embeddings() -> None:
    information = InformationStore()
    information.register(
        _document(
            content={
                "relationships": [
                    {
                        "target_id": "agent-002",
                        "relationship": "colleague",
                        "notes": "Works on the west entrance project.",
                    }
                ]
            },
        )
    )
    reference_results = InformationRetriever(information).retrieve(
        InformationQuery(
            character_id="agent-001",
            text="",
            referenced_entity_ids=("agent-002",),
        )
    )
    provider = CountingEmbeddingProvider()
    retriever = InformationRetriever(information, provider)
    semantic_query = InformationQuery(
        character_id="agent-001",
        text="unmatched semantic query",
    )
    semantic_results = retriever.retrieve(semantic_query)
    calls_after_first_query = provider.call_count
    repeated_results = retriever.retrieve(semantic_query)

    assert reference_results[0].source_path == "$.relationships[0]"
    assert '"relationship":"colleague"' in reference_results[0].rendered_content
    assert semantic_results
    assert repeated_results == semantic_results
    assert calls_after_first_query == 2
    assert provider.call_count == calls_after_first_query + 1


def test_retrieval_capsules_respect_character_budget() -> None:
    information = InformationStore()
    information.register(
        _document(
            content={
                "background": {
                    "relevant": "commute " + ("very long detail " * 100),
                }
            },
        )
    )
    results = InformationRetriever(information).retrieve(
        InformationQuery(
            character_id="agent-001",
            text="commute",
            token_budget=16,
        )
    )

    assert len(results) == 1
    assert len(results[0].rendered_content) <= 64
    assert results[0].rendered_content.endswith("…[truncated]")


def test_character_retrieval_enforces_visibility_policy_before_anchoring() -> None:
    information = InformationStore()
    policies = {
        "private-own": VisibilityPolicy(
            level=VisibilityLevel.PRIVATE,
            owner_ids=("agent-001",),
        ),
        "private-other": VisibilityPolicy(
            level=VisibilityLevel.PRIVATE,
            owner_ids=("agent-002",),
        ),
        "shared-reader": VisibilityPolicy(
            level=VisibilityLevel.SHARED,
            owner_ids=("agent-002",),
            reader_ids=("agent-001",),
        ),
        "shared-other": VisibilityPolicy(
            level=VisibilityLevel.SHARED,
            owner_ids=("agent-002",),
            reader_ids=("agent-003",),
        ),
        "public": VisibilityPolicy(level=VisibilityLevel.PUBLIC),
        "operator": VisibilityPolicy(level=VisibilityLevel.OPERATOR),
    }
    for document_id, visibility in policies.items():
        information.register(
            InformationDocument.create(
                id=document_id,
                namespace_id=character_information_namespace_id("agent-001"),
                kind="knowledge.note",
                schema_id="generic.v1",
                subject_ids=("agent-001",),
                content={"note": f"visibility marker {document_id}"},
                source=InformationSource(type="TEST"),
                visibility=visibility,
            )
        )

    results = InformationRetriever(information).retrieve(
        InformationQuery(
            character_id="agent-001",
            text="visibility marker",
            token_budget=512,
        )
    )

    assert {result.document_id for result in results} == {
        "private-own",
        "shared-reader",
        "public",
    }


class FailingInformationPersistence:
    def save_information_document(
        self,
        run_id: str,
        document: InformationDocument,
    ) -> None:
        del run_id, document
        raise OSError("injected persistence failure")

    def load_information_documents(
        self,
        run_id: str,
    ) -> tuple[InformationDocument, ...]:
        del run_id
        return ()


def test_information_store_does_not_mutate_before_persistence_success() -> None:
    information = InformationStore()
    information.bind_persistence(FailingInformationPersistence(), "run")
    document = _document(content={"status": "must persist first"})

    with pytest.raises(OSError, match="injected persistence failure"):
        information.register(document)

    assert not information.has(document.id)


def test_sqlite_memory_and_episode_write_rolls_back_atomically(
    tmp_path: Path,
) -> None:
    database = tmp_path / "atomic-memory.sqlite3"
    persistence = SQLiteDatasetStore(database)
    persistence.begin_run(
        run_id="atomic-memory",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "atomic-memory"},
    )
    memory = EpisodicMemoryStore(CountingEmbeddingProvider())
    memory.bind_persistence(persistence, "atomic-memory")
    trigger_connection = sqlite3.connect(database)
    trigger_connection.execute(
        """
        CREATE TRIGGER fail_episode_document
        BEFORE INSERT ON information_documents
        BEGIN
            SELECT RAISE(ABORT, 'injected episode failure');
        END
        """
    )
    trigger_connection.commit()
    trigger_connection.close()

    with pytest.raises(sqlite3.IntegrityError, match="injected episode failure"):
        memory.record(
            agent_id="agent-001",
            text="This write must be atomic.",
            simulation_time=1,
            importance=0.5,
        )

    verification = sqlite3.connect(database)
    memory_count = int(
        verification.execute(
            "SELECT COUNT(*) FROM episodic_memories"
        ).fetchone()[0]
    )
    document_count = int(
        verification.execute(
            "SELECT COUNT(*) FROM information_documents"
        ).fetchone()[0]
    )
    verification.execute("DROP TRIGGER fail_episode_document")
    verification.commit()
    verification.close()

    assert memory.records == ()
    assert memory_count == 0
    assert document_count == 0

    recorded = memory.record(
        agent_id="agent-001",
        text="The retry succeeds.",
        simulation_time=2,
        importance=0.6,
    )
    persistence.close()

    assert recorded.id == "memory-00000001"


def test_preexisting_memory_binding_rolls_back_store_and_sqlite_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / "atomic-memory-binding.sqlite3"
    persistence = SQLiteDatasetStore(database)
    persistence.begin_run(
        run_id="atomic-memory-binding",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "atomic-memory-binding"},
    )
    information = InformationStore()
    information.register(
        _document(
            document_id="standalone-document",
            content={"status": "must roll back with memory"},
        )
    )
    memory = EpisodicMemoryStore(
        CountingEmbeddingProvider(),
        information_store=information,
    )
    recorded = memory.record(
        agent_id="agent-001",
        text="This memory exists before persistence is bound.",
        simulation_time=1,
        importance=0.5,
    )
    trigger_connection = sqlite3.connect(database)
    trigger_connection.execute(
        """
        CREATE TRIGGER fail_memory_binding
        BEFORE INSERT ON information_documents
        WHEN NEW.kind = 'memory.episode'
        BEGIN
            SELECT RAISE(ABORT, 'injected binding failure');
        END
        """
    )
    trigger_connection.commit()
    trigger_connection.close()

    with pytest.raises(sqlite3.IntegrityError, match="injected binding failure"):
        memory.bind_persistence(persistence, "atomic-memory-binding")

    verification = sqlite3.connect(database)
    memory_count = int(
        verification.execute(
            "SELECT COUNT(*) FROM episodic_memories"
        ).fetchone()[0]
    )
    document_count = int(
        verification.execute(
            "SELECT COUNT(*) FROM information_documents"
        ).fetchone()[0]
    )
    verification.execute("DROP TRIGGER fail_memory_binding")
    verification.commit()
    verification.close()

    assert not information.persistence_bound
    assert memory.records == (recorded,)
    assert information.get(recorded.id).kind == "memory.episode"
    assert information.has("standalone-document")
    assert memory_count == 0
    assert document_count == 0

    memory.bind_persistence(persistence, "atomic-memory-binding")
    assert information.persistence_bound
    assert persistence.load_memories("atomic-memory-binding") == (recorded,)
    assert {
        document.id
        for document in persistence.load_information_documents(
            "atomic-memory-binding"
        )
    } == {"standalone-document", recorded.id}
    persistence.close()


def test_preexisting_memory_persists_and_rehydrates_with_its_document(
    tmp_path: Path,
) -> None:
    database = tmp_path / "preexisting-memory.sqlite3"
    persistence = SQLiteDatasetStore(database)
    persistence.begin_run(
        run_id="preexisting-memory",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "preexisting-memory"},
    )
    original = EpisodicMemoryStore(CountingEmbeddingProvider())
    recorded = original.record(
        agent_id="agent-001",
        text="Recorded before the persistence binding.",
        simulation_time=4,
        importance=0.7,
        metadata={"event_id": "event-4"},
    )

    original.bind_persistence(persistence, "preexisting-memory")
    persisted_document = original.document(recorded.id)
    persistence.close()

    reopened = SQLiteDatasetStore(database)
    rehydrated = EpisodicMemoryStore(CountingEmbeddingProvider())
    rehydrated.bind_persistence(
        reopened,
        "preexisting-memory",
        rehydrate=True,
    )

    assert rehydrated.records == (recorded,)
    assert rehydrated.document(recorded.id) == persisted_document
    assert reopened.load_memories("preexisting-memory") == (recorded,)
    assert reopened.load_information_documents("preexisting-memory") == (
        persisted_document,
    )
    reopened.close()


def test_memory_rehydration_reserves_orphan_document_ids_and_repairs_legacy_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory-orphans.sqlite3"
    persistence = SQLiteDatasetStore(database)
    persistence.begin_run(
        run_id="memory-orphans",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "memory-orphans"},
    )
    orphan_document = InformationDocument.create(
        id="memory-00000007",
        namespace_id=character_information_namespace_id("agent-001"),
        kind="memory.episode",
        schema_id="memory.episode.v1",
        subject_ids=("agent-001",),
        content={
            "summary": "Document without compatibility row",
            "importance": 0.4,
            "metadata": {},
        },
        source=InformationSource(type="MEMORY_RECORD"),
        visibility=VisibilityPolicy(
            level=VisibilityLevel.PRIVATE,
            owner_ids=("agent-001",),
        ),
    )
    legacy_record = MemoryRecord(
        id="memory-00000003",
        agent_id="agent-001",
        text="Legacy row without document",
        simulation_time=3,
        importance=0.5,
        embedding=(1.0, 0.0),
        metadata={},
    )
    persistence.save_information_document("memory-orphans", orphan_document)
    persistence.save_memory("memory-orphans", legacy_record)
    memory = EpisodicMemoryStore(CountingEmbeddingProvider())

    memory.bind_persistence(
        persistence,
        "memory-orphans",
        rehydrate=True,
    )
    recorded = memory.record(
        agent_id="agent-001",
        text="New memory after orphan recovery",
        simulation_time=8,
        importance=0.7,
    )
    documents = persistence.load_information_documents("memory-orphans")
    persistence.close()

    assert recorded.id == "memory-00000008"
    assert memory.records[0] == legacy_record
    assert memory.document(legacy_record.id).content["summary"] == legacy_record.text
    assert {document.id for document in documents} == {
        "memory-00000003",
        "memory-00000007",
        "memory-00000008",
    }


def test_sqlite_v3_migrates_v2_and_persists_information_documents(
    tmp_path: Path,
) -> None:
    database = tmp_path / "information-v3.sqlite3"
    store = SQLiteDatasetStore(database)
    store.begin_run(
        run_id="migration",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "migration"},
    )
    legacy_memory = MemoryRecord(
        id="memory-00000001",
        agent_id="agent-001",
        text="Legacy memory",
        simulation_time=1,
        importance=0.5,
        embedding=(1.0,),
        metadata={"source": "legacy"},
    )
    store.save_memory("migration", legacy_memory)
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("DROP TABLE information_documents")
    connection.execute("PRAGMA user_version = 2")
    connection.commit()
    connection.close()

    migrated = SQLiteDatasetStore(database)
    document = _document(content={"future": {"nested": [1, {"ok": True}]}})
    migrated.save_information_document("migration", document)
    loaded_documents = migrated.load_information_documents("migration")
    loaded_memories = migrated.load_memories("migration")
    migrated.close()

    version_connection = sqlite3.connect(database)
    version = int(version_connection.execute("PRAGMA user_version").fetchone()[0])
    version_connection.close()

    assert version == 3
    assert loaded_documents == (document,)
    assert loaded_memories == (legacy_memory,)
    document_connection = sqlite3.connect(database)
    persisted_payload = json.loads(
        document_connection.execute(
            "SELECT document_json FROM information_documents "
            "WHERE document_id = ?",
            (document.id,),
        )
        .fetchone()[0]
    )
    document_connection.close()
    assert persisted_payload["content"] == document.content
