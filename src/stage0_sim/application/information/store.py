from collections.abc import Callable
from typing import Protocol

from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.information import InformationDocument, InformationSource


class InformationPersistence(Protocol):
    def save_information_document(
        self,
        run_id: str,
        document: InformationDocument,
    ) -> None: ...

    def load_information_documents(
        self,
        run_id: str,
    ) -> tuple[InformationDocument, ...]: ...


class InformationStore:
    def __init__(self) -> None:
        self._history: dict[str, list[InformationDocument]] = {}
        self._persistence: InformationPersistence | None = None
        self._run_id: str | None = None

    def register(self, document: InformationDocument) -> InformationDocument:
        return self.register_with_persistence(document)

    def register_with_persistence(
        self,
        document: InformationDocument,
        persist: Callable[[], None] | None = None,
    ) -> InformationDocument:
        current = self._validate_registration(document)
        if current is not None:
            return current
        if persist is not None:
            persist()
        elif self._persistence is not None and self._run_id is not None:
            self._persistence.save_information_document(self._run_id, document)
        self._commit_registration(document)
        return document

    def _validate_registration(
        self,
        document: InformationDocument,
    ) -> InformationDocument | None:
        history = self._history.get(document.id)
        if history is not None:
            current = history[-1]
            if document == current:
                return current
            if document.revision != current.revision + 1:
                raise ValueError(
                    f"information document {document.id} revision must be "
                    f"{current.revision + 1}"
                )
            if (
                document.namespace_id,
                document.kind,
                document.schema_id,
            ) != (
                current.namespace_id,
                current.kind,
                current.schema_id,
            ):
                raise ValueError(
                    "information document namespace, kind, and schema "
                    "cannot change across revisions"
                )
        elif document.revision != 1:
            raise ValueError("new information documents must start at revision 1")
        return None

    def _commit_registration(self, document: InformationDocument) -> None:
        history = self._history.get(document.id)
        if history is not None:
            history.append(document)
        else:
            self._history[document.id] = [document]

    def revise(
        self,
        document_id: str,
        content: JsonValue,
        *,
        source: InformationSource | None = None,
        recorded_at: float | None = None,
    ) -> InformationDocument:
        current = self.get(document_id)
        revised = InformationDocument.create(
            id=current.id,
            namespace_id=current.namespace_id,
            kind=current.kind,
            schema_id=current.schema_id,
            subject_ids=current.subject_ids,
            content=content,
            source=source or current.source,
            valid_time=current.valid_time,
            recorded_at=(
                current.recorded_at if recorded_at is None else recorded_at
            ),
            visibility=current.visibility,
            revision=current.revision + 1,
        )
        return self.register(revised)

    def get(
        self,
        document_id: str,
        revision: int | None = None,
    ) -> InformationDocument:
        try:
            history = self._history[document_id]
        except KeyError as error:
            raise KeyError(f"unknown information document: {document_id}") from error
        if revision is None:
            return history[-1]
        if revision <= 0:
            raise ValueError("information revision must be greater than zero")
        for document in history:
            if document.revision == revision:
                return document
        raise KeyError(
            f"unknown information document revision: {document_id}@{revision}"
        )

    def has(self, document_id: str) -> bool:
        return document_id in self._history

    def history(self, document_id: str) -> tuple[InformationDocument, ...]:
        self.get(document_id)
        return tuple(self._history[document_id])

    def documents(
        self,
        *,
        namespace_id: str | None = None,
        kinds: tuple[str, ...] | None = None,
    ) -> tuple[InformationDocument, ...]:
        documents = (history[-1] for history in self._history.values())
        return tuple(
            sorted(
                (
                    document
                    for document in documents
                    if (
                        namespace_id is None
                        or document.namespace_id == namespace_id
                    )
                    and (kinds is None or document.kind in kinds)
                ),
                key=lambda document: (document.namespace_id, document.kind, document.id),
            )
        )

    def restore_documents(
        self,
        documents: tuple[InformationDocument, ...],
    ) -> None:
        if self._persistence is not None:
            raise RuntimeError(
                "information documents must be restored before persistence binding"
            )
        candidate = InformationStore()
        for document in sorted(
            documents,
            key=lambda item: (item.id, item.revision),
        ):
            candidate.register(document)
        self._history = candidate._history

    @property
    def persistence_bound(self) -> bool:
        return self._persistence is not None

    @property
    def bound_run_id(self) -> str | None:
        return self._run_id

    @property
    def bound_persistence(self) -> InformationPersistence | None:
        return self._persistence

    def bind_persistence(
        self,
        persistence: InformationPersistence,
        run_id: str,
        *,
        rehydrate: bool = False,
    ) -> None:
        candidate, documents_to_flush = self._prepare_persistence_binding(
            persistence,
            run_id,
            rehydrate=rehydrate,
        )
        for document in documents_to_flush:
            persistence.save_information_document(run_id, document)
        self._commit_persistence_binding(candidate, persistence, run_id)

    def _prepare_persistence_binding(
        self,
        persistence: InformationPersistence,
        run_id: str,
        *,
        rehydrate: bool,
    ) -> tuple["InformationStore", tuple[InformationDocument, ...]]:
        if self._persistence is not None:
            raise RuntimeError("information persistence is already bound")
        candidate = self._clone_unbound()
        if rehydrate:
            for document in persistence.load_information_documents(run_id):
                current = candidate._validate_registration(document)
                if current is None:
                    candidate._commit_registration(document)
        documents_to_flush = tuple(
            document
            for document_id in sorted(self._history)
            for document in self._history[document_id]
        )
        return candidate, documents_to_flush

    def _clone_unbound(self) -> "InformationStore":
        candidate = InformationStore()
        candidate._history = {
            document_id: list(history)
            for document_id, history in self._history.items()
        }
        return candidate

    def _commit_candidate_history(self, candidate: "InformationStore") -> None:
        self._history = {
            document_id: list(history)
            for document_id, history in candidate._history.items()
        }

    def _commit_persistence_binding(
        self,
        candidate: "InformationStore",
        persistence: InformationPersistence,
        run_id: str,
    ) -> None:
        self._commit_candidate_history(candidate)
        self._persistence = persistence
        self._run_id = run_id
