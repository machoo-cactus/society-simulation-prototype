from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EngagementSpecification:
    engagement_id: str
    intent: str
    reference_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.engagement_id:
            raise ValueError("engagement ID must not be empty")
        if not self.intent.strip():
            raise ValueError("engagement intent must not be empty")
        if len(self.reference_ids) != len(set(self.reference_ids)):
            raise ValueError("engagement reference IDs must be unique")
