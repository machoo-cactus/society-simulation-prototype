from dataclasses import dataclass
from enum import StrEnum

from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.information.documents import canonical_json


class Cardinality(StrEnum):
    OPTIONAL = "optional"
    ONE = "one"
    MANY = "many"


class TemporalMode(StrEnum):
    STATIC = "static"
    VALID_TIME = "valid_time"
    EPISODIC = "episodic"


@dataclass(frozen=True, slots=True)
class InformationFieldDescriptor:
    path: str
    value_schema: JsonValue
    cardinality: Cardinality = Cardinality.OPTIONAL
    temporal_mode: TemporalMode = TemporalMode.STATIC
    reference_kind: str | None = None
    display_label: str | None = None
    indexing_hint: str | None = None

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValueError("information field path must not be empty")
        canonical_json(self.value_schema)
        for value, label in (
            (self.reference_kind, "reference_kind"),
            (self.display_label, "display_label"),
            (self.indexing_hint, "indexing_hint"),
        ):
            if value == "":
                raise ValueError(f"information field {label} must not be empty")
