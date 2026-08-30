from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InformationNamespaceComponent:
    namespace_id: str
    document_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.namespace_id.strip():
            raise ValueError("information namespace ID must not be empty")
        if any(not document_id.strip() for document_id in self.document_ids):
            raise ValueError("information document IDs must not be empty")
        if len(self.document_ids) != len(set(self.document_ids)):
            raise ValueError("information document IDs must be unique")
