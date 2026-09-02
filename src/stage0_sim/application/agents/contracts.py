from dataclasses import dataclass
from typing import Literal, Protocol

from stage0_sim.application.information_context import InformationContextCapsule
from stage0_sim.domain.events import JsonValue


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None
    tool_call_id: str | None = None
    tool_calls: tuple["ModelToolCall", ...] = ()


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ModelToolCall:
    call_id: str
    name: str
    arguments: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ModelRequest:
    request_id: str
    correlation_id: str
    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolDefinition, ...]
    model: str
    timeout_seconds: float
    max_output_tokens: int
    prompt_version: str


@dataclass(frozen=True, slots=True)
class ModelTurn:
    text: str | None
    tool_calls: tuple[ModelToolCall, ...]
    finish_reason: str
    provider: str
    model: str
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider_request_id: str | None = None


class ModelClient(Protocol):
    async def complete(self, request: ModelRequest) -> ModelTurn: ...


class ModelClientError(RuntimeError):
    def __init__(self, message: str, *, reason: str = "provider_error") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ObservedTarget:
    id: str
    kind: Literal[
        "zone",
        "station",
        "transaction_point",
        "character",
        "building",
        "outdoor",
        "room",
        "physical_object",
    ]
    name: str
    supported_actions: tuple[str, ...] = ()
    offers: tuple["ObservedOffer", ...] = ()
    available: bool = True
    last_observed_tick: int | None = None
    available_interactions: tuple[str, ...] = ()
    public_state: dict[str, JsonValue] | None = None


@dataclass(frozen=True, slots=True)
class ObservedItemAmount:
    item_id: str
    item_name: str
    unit: str
    quantity: int


@dataclass(frozen=True, slots=True)
class ObservedOffer:
    id: str
    name: str
    character_gives: tuple[ObservedItemAmount, ...]
    character_receives: tuple[ObservedItemAmount, ...]
    duration: float
    available: bool


@dataclass(frozen=True, slots=True)
class ObservedPossession:
    item_id: str
    item_name: str
    unit: str
    quantity: int


@dataclass(frozen=True, slots=True)
class ObservedServiceRequest:
    request_id: str
    customer_id: str
    customer_name: str
    point_id: str
    offer_id: str
    offer_name: str
    requested_at: float


@dataclass(frozen=True, slots=True)
class ObservationFact:
    fact_id: str
    fact_type: str
    text: str
    tick: int
    subject_id: str | None


@dataclass(frozen=True, slots=True)
class CalendarTimeObservation:
    datetime: str
    date: str
    time: str
    weekday: str
    period: str


@dataclass(frozen=True, slots=True)
class EnvironmentObservation:
    values: dict[str, JsonValue]
    unavailable_topics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ObservedGoal:
    id: str
    description: str
    status: str
    priority: int
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CharacterObservation:
    agent_id: str
    display_name: str
    simulation_time: float
    location_id: str | None
    activity: str
    satiety: float | None
    energy: float | None
    stress: float | None
    targets: tuple[ObservedTarget, ...]
    facts: tuple[ObservationFact, ...]
    recent_outcome: str | None
    spatial_location: dict[str, JsonValue] | None = None
    available_travel_modes: tuple[str, ...] = ()
    calendar_time: CalendarTimeObservation | None = None
    environment: EnvironmentObservation | None = None
    senses: dict[str, JsonValue] | None = None
    equipment: dict[str, JsonValue] | None = None
    carried_load: dict[str, JsonValue] | None = None
    possessions: tuple[ObservedPossession, ...] = ()
    service_requests: tuple[ObservedServiceRequest, ...] = ()
    structured_goals: tuple[ObservedGoal, ...] = ()


@dataclass(frozen=True, slots=True)
class CharacterDecisionRequest:
    decision_id: str
    run_id: str
    agent_id: str
    requested_tick: int
    state_revision: int
    trigger: str
    character_description: str
    profile_id: str
    profile_template_version: int
    profile_content_hash: str
    observation: CharacterObservation
    memories: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    actor_kind: Literal["character", "npc"] = "character"
    situation_description: str = ""
    situation_content_hash: str = ""
    situation_input_hash: str = ""
    retrieved_information: tuple[InformationContextCapsule, ...] = ()
    information_retrieval_performed: bool = False
    information_query: str = ""


@dataclass(frozen=True, slots=True)
class CharacterDecision:
    decision_id: str
    tool_call: ModelToolCall | None
    model_turn: ModelTurn
    error: str | None = None
    read_tools: tuple["ReadToolExecution", ...] = ()


@dataclass(frozen=True, slots=True)
class ReadToolExecution:
    call: ModelToolCall
    result: dict[str, JsonValue]


class CharacterController(Protocol):
    async def decide(
        self, request: CharacterDecisionRequest
    ) -> CharacterDecision: ...
