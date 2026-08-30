from dataclasses import dataclass

from stage0_sim.domain.information import InformationSource, TimeRange


@dataclass(frozen=True, slots=True)
class InformationContextCapsule:
    document_id: str
    document_kind: str
    source_path: str | None
    rendered_content: str
    source: InformationSource
    valid_time: TimeRange | None
    score: float
    revision: int = 1
    recorded_at: float | None = None
