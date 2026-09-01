from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import NewType, Self, cast

from stage0_sim.domain.events import JsonValue

DATASET_SCHEMA_ID = "stage0.dataset"
DATASET_SCHEMA_VERSION = "stage0.dataset.v3"

GoalId = NewType("GoalId", str)
PlanId = NewType("PlanId", str)
ActionId = NewType("ActionId", str)
DecisionId = NewType("DecisionId", str)
ModelRequestId = NewType("ModelRequestId", str)
ToolCallId = NewType("ToolCallId", str)
InteractionId = NewType("InteractionId", str)
PerceptionFactId = NewType("PerceptionFactId", str)
MemoryId = NewType("MemoryId", str)
TransactionRequestId = NewType("TransactionRequestId", str)
OperatorInterventionId = NewType("OperatorInterventionId", str)


class RecordVisibility(StrEnum):
    PUBLIC = "PUBLIC"
    OPERATOR = "OPERATOR"
    PRIVATE_RESEARCH = "PRIVATE_RESEARCH"


class RunnerPhase(StrEnum):
    UNSPECIFIED = "unspecified"
    RUN_INITIAL = "run_initial"
    TICK_PRE_SYSTEMS = "tick_pre_systems"
    TICK_POST_SYSTEMS = "tick_post_systems"
    TICK_POST_COGNITION = "tick_post_cognition"
    RUN_FINAL = "run_final"


class RecordCategory(StrEnum):
    RUN = "RUN"
    PROVENANCE = "PROVENANCE"
    EVENT = "EVENT"
    STATE = "STATE"
    TRANSITION = "TRANSITION"
    GOAL = "GOAL"
    DECISION = "DECISION"
    MODEL = "MODEL"
    TOOL = "TOOL"
    ACTION = "ACTION"
    INTERACTION = "INTERACTION"
    PERCEPTION = "PERCEPTION"
    MEMORY = "MEMORY"
    INFORMATION = "INFORMATION"
    ENVIRONMENT = "ENVIRONMENT"
    OPPORTUNITY = "OPPORTUNITY"
    POPULATION = "POPULATION"
    OTHER = "OTHER"


class RecordSource(StrEnum):
    DATASET_COLLECTOR = "DATASET_COLLECTOR"
    DOMAIN_EVENT = "DOMAIN_EVENT"
    RUNNER = "RUNNER"
    APPLICATION = "APPLICATION"
    MODEL_PROVIDER = "MODEL_PROVIDER"
    DERIVED = "DERIVED"
    OPERATOR = "OPERATOR"
    IMPORT = "IMPORT"


@dataclass(frozen=True, slots=True)
class RecordJoinIds:
    goal_id: GoalId | None = None
    plan_id: PlanId | None = None
    action_id: ActionId | None = None
    decision_id: DecisionId | None = None
    model_request_id: ModelRequestId | None = None
    tool_call_id: ToolCallId | None = None
    interaction_id: InteractionId | None = None
    perception_fact_id: PerceptionFactId | None = None
    memory_id: MemoryId | None = None
    transaction_request_id: TransactionRequestId | None = None
    operator_intervention_id: OperatorInterventionId | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            name: str(value)
            for name, value in (
                ("goal_id", self.goal_id),
                ("plan_id", self.plan_id),
                ("action_id", self.action_id),
                ("decision_id", self.decision_id),
                ("model_request_id", self.model_request_id),
                ("tool_call_id", self.tool_call_id),
                ("interaction_id", self.interaction_id),
                ("perception_fact_id", self.perception_fact_id),
                ("memory_id", self.memory_id),
                ("transaction_request_id", self.transaction_request_id),
                ("operator_intervention_id", self.operator_intervention_id),
            )
            if value is not None
        }

    @classmethod
    def from_dict(cls, content: dict[str, JsonValue]) -> Self:
        return cls(
            goal_id=_typed_id(content, "goal_id", GoalId),
            plan_id=_typed_id(content, "plan_id", PlanId),
            action_id=_typed_id(content, "action_id", ActionId),
            decision_id=_typed_id(content, "decision_id", DecisionId),
            model_request_id=_typed_id(content, "model_request_id", ModelRequestId),
            tool_call_id=_typed_id(content, "tool_call_id", ToolCallId),
            interaction_id=_typed_id(content, "interaction_id", InteractionId),
            perception_fact_id=_typed_id(content, "perception_fact_id", PerceptionFactId),
            memory_id=_typed_id(content, "memory_id", MemoryId),
            transaction_request_id=_typed_id(
                content,
                "transaction_request_id",
                TransactionRequestId,
            ),
            operator_intervention_id=_typed_id(
                content,
                "operator_intervention_id",
                OperatorInterventionId,
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class DatasetRecord:
    """Provider-neutral immutable raw dataset record."""

    run_id: str
    sequence: int
    record_type: str
    simulation_tick: int
    simulation_time: float
    payload: dict[str, JsonValue]
    source_event_id: str | None = None
    schema_version: str = DATASET_SCHEMA_VERSION
    record_id: str = ""
    schema_id: str = ""
    category: RecordCategory = RecordCategory.OTHER
    source: RecordSource = RecordSource.DATASET_COLLECTOR
    phase: RunnerPhase = RunnerPhase.UNSPECIFIED
    wall_time: str | None = None
    visibility: RecordVisibility = RecordVisibility.OPERATOR
    subject_id: str | None = None
    related_entity_ids: tuple[str, ...] = ()
    causation_id: str | None = None
    correlation_id: str | None = None
    joins: RecordJoinIds = field(default_factory=RecordJoinIds)
    source_metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must not be empty")
        if self.sequence < 1:
            raise ValueError("record sequence must be positive")
        if not self.record_type:
            raise ValueError("record_type must not be empty")
        if not self.record_id:
            object.__setattr__(
                self,
                "record_id",
                f"{self.run_id}:record:{self.sequence:08d}",
            )
        if not self.schema_id:
            object.__setattr__(
                self,
                "schema_id",
                f"stage0.record.{self.record_type}",
            )

    def to_dict(self) -> dict[str, JsonValue]:
        content: dict[str, JsonValue] = {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "record_id": self.record_id,
            "record_type": self.record_type,
            "category": self.category.value,
            "source": self.source.value,
            "phase": self.phase.value,
            "visibility": self.visibility.value,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "simulation_tick": self.simulation_tick,
            "simulation_time": self.simulation_time,
            "payload": self.payload,
        }
        optional: tuple[tuple[str, JsonValue], ...] = (
            ("wall_time", self.wall_time),
            ("subject_id", self.subject_id),
            ("source_event_id", self.source_event_id),
            ("causation_id", self.causation_id),
            ("correlation_id", self.correlation_id),
        )
        for name, value in optional:
            if value is not None:
                content[name] = value
        if self.related_entity_ids:
            content["related_entity_ids"] = list(self.related_entity_ids)
        content.update(self.joins.to_dict())
        if self.source_metadata:
            content["source_metadata"] = self.source_metadata
        return content

    @classmethod
    def from_dict(cls, content: dict[str, JsonValue]) -> Self:
        if "agent_id" in content:
            raise ValueError("agent_id is not supported; use subject_id")
        payload = content.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("record payload must be an object")
        related = content.get("related_entity_ids", [])
        if not isinstance(related, list) or not all(
            isinstance(value, str) for value in related
        ):
            raise ValueError("related_entity_ids must be a string array")
        source_metadata = content.get("source_metadata", {})
        if not isinstance(source_metadata, dict):
            raise ValueError("source_metadata must be an object")
        return cls(
            run_id=_required_str(content, "run_id"),
            sequence=_required_int(content, "sequence"),
            record_type=_required_str(content, "record_type"),
            simulation_tick=_required_int(content, "simulation_tick"),
            simulation_time=_required_number(content, "simulation_time"),
            payload=payload,
            source_event_id=_optional_str(content, "source_event_id"),
            schema_version=_required_str(content, "schema_version"),
            record_id=_required_str(content, "record_id"),
            schema_id=_required_str(content, "schema_id"),
            category=RecordCategory(_required_str(content, "category")),
            source=RecordSource(_required_str(content, "source")),
            phase=RunnerPhase(_required_str(content, "phase")),
            wall_time=_optional_str(content, "wall_time"),
            visibility=RecordVisibility(_required_str(content, "visibility")),
            subject_id=_optional_str(content, "subject_id"),
            related_entity_ids=tuple(cast(list[str], related)),
            causation_id=_optional_str(content, "causation_id"),
            correlation_id=_optional_str(content, "correlation_id"),
            joins=RecordJoinIds.from_dict(content),
            source_metadata=source_metadata,
        )


@dataclass(frozen=True, slots=True)
class DatasetRecordFilter:
    record_type: str | None = None
    category: RecordCategory | None = None
    schema_id: str | None = None
    schema_version: str | None = None
    subject_id: str | None = None
    related_entity_id: str | None = None
    minimum_tick: int | None = None
    maximum_tick: int | None = None
    minimum_time: float | None = None
    maximum_time: float | None = None
    visibility: RecordVisibility | None = None
    goal_id: str | None = None
    plan_id: str | None = None
    action_id: str | None = None
    decision_id: str | None = None
    model_request_id: str | None = None
    tool_call_id: str | None = None
    interaction_id: str | None = None
    perception_fact_id: str | None = None
    memory_id: str | None = None
    transaction_request_id: str | None = None
    operator_intervention_id: str | None = None
    status: str | None = None
    outcome: str | None = None
    include_private: bool = True
    after_sequence: int | None = None
    limit: int = 100

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > 1000:
            raise ValueError("record query limit must be between 1 and 1000")
        if (
            self.minimum_tick is not None
            and self.maximum_tick is not None
            and self.minimum_tick > self.maximum_tick
        ):
            raise ValueError("minimum_tick must not exceed maximum_tick")
        if (
            self.minimum_time is not None
            and self.maximum_time is not None
            and self.minimum_time > self.maximum_time
        ):
            raise ValueError("minimum_time must not exceed maximum_time")
        if self.after_sequence is not None and self.after_sequence < 0:
            raise ValueError("after_sequence must not be negative")


@dataclass(frozen=True, slots=True)
class DatasetRecordPage:
    records: tuple[DatasetRecord, ...]
    next_cursor: int | None


@dataclass(frozen=True, slots=True)
class DatasetQueryFilter:
    record_type: str | None = None
    category: RecordCategory | None = None
    schema_id: str | None = None
    schema_version: str | None = None
    primary_entity_id: str | None = None
    related_entity_id: str | None = None
    minimum_tick: int | None = None
    maximum_tick: int | None = None
    minimum_time: float | None = None
    maximum_time: float | None = None
    visibility: RecordVisibility | None = None
    goal_id: str | None = None
    plan_id: str | None = None
    action_id: str | None = None
    decision_id: str | None = None
    model_request_id: str | None = None
    tool_call_id: str | None = None
    interaction_id: str | None = None
    perception_fact_id: str | None = None
    memory_id: str | None = None
    transaction_request_id: str | None = None
    operator_intervention_id: str | None = None
    status: str | None = None
    outcome: str | None = None
    include_private: bool = False
    cursor: str | None = None
    limit: int = 100

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > 1000:
            raise ValueError("query limit must be between 1 and 1000")
        if (
            self.minimum_tick is not None
            and self.maximum_tick is not None
            and self.minimum_tick > self.maximum_tick
        ):
            raise ValueError("minimum_tick must not exceed maximum_tick")
        if (
            self.minimum_time is not None
            and self.maximum_time is not None
            and self.minimum_time > self.maximum_time
        ):
            raise ValueError("minimum_time must not exceed maximum_time")


@dataclass(frozen=True, slots=True)
class DatasetQueryPage:
    rows: tuple[dict[str, JsonValue], ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class RecordRelation:
    run_id: str
    record_id: str
    relation_type: str
    target_type: str
    target_id: str
    ordinal: int = 0
    metadata: dict[str, JsonValue] = field(default_factory=dict)


def _optional_str(content: dict[str, JsonValue], name: str) -> str | None:
    value = content.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _required_str(content: dict[str, JsonValue], name: str) -> str:
    value = _optional_str(content, name)
    if value is None:
        raise ValueError(f"{name} is required")
    return value


def _required_int(content: dict[str, JsonValue], name: str) -> int:
    value = content.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _required_number(content: dict[str, JsonValue], name: str) -> float:
    value = content.get(name)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    return float(value)


def _typed_id[T](
    content: dict[str, JsonValue],
    name: str,
    constructor: Callable[[str], T],
) -> T | None:
    value = _optional_str(content, name)
    return constructor(value) if value is not None else None
