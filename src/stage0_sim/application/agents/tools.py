from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from stage0_sim.application.agents.contracts import (
    CharacterDecisionRequest,
    ModelToolCall,
    ToolDefinition,
)
from stage0_sim.domain.components import ActionType
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.intents import (
    ActivityIntent,
    CharacterIntent,
    IntentKind,
    NavigationIntent,
    ServeTransactionIntent,
    SkipIntent,
    SpeechIntent,
    TransactionIntent,
    WaitIntent,
)
from stage0_sim.domain.world import TravelMode


class ToolValidationError(ValueError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class PerformArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["WORK", "READ", "EAT", "SLEEP", "RELAX"]
    target_id: str | None = None
    duration_seconds: float | None = Field(default=None, gt=0, le=3600)
    reason: str | None = Field(default=None, max_length=300)


class SayArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=500)
    reason: str | None = Field(default=None, max_length=300)


class WaitArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration_seconds: float = Field(ge=1, le=600)
    reason: str | None = Field(default=None, max_length=300)


class SkipArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reconsider_after_seconds: float = Field(default=30, ge=5, le=3600)
    reason: str | None = Field(default=None, max_length=300)


class NavigateToArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=1)
    preferred_mode: Literal["WALK", "CYCLE", "CAR", "METRO"] | None = None
    reason: str | None = Field(default=None, max_length=300)


class TransactArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    point_id: str = Field(min_length=1)
    offer_id: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=300)


class ServeTransactionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=300)


class CheckEnvironmentArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topics: list[
        Literal["time", "weather", "surface_conditions", "availability"]
    ] = Field(default=["time", "weather"])


ToolArguments = Annotated[
    PerformArguments
    | SayArguments
    | WaitArguments
    | SkipArguments
    | NavigateToArguments
    | TransactArguments
    | ServeTransactionArguments
    | CheckEnvironmentArguments,
    Field(discriminator=None),
]


class ToolRegistry:
    def __init__(self) -> None:
        self._types: dict[str, type[BaseModel]] = {
            "perform": PerformArguments,
            "say": SayArguments,
            "wait": WaitArguments,
            "skip": SkipArguments,
            "navigate_to": NavigateToArguments,
            "transact": TransactArguments,
            "serve_transaction": ServeTransactionArguments,
            "check_environment": CheckEnvironmentArguments,
        }

    def definitions(self, allowed: tuple[str, ...]) -> tuple[ToolDefinition, ...]:
        return tuple(
            ToolDefinition(
                name=name,
                description=_DESCRIPTIONS[name],
                input_schema=_schema(self._types[name]),
            )
            for name in allowed
            if name in self._types
        )

    def propose(
        self,
        request: CharacterDecisionRequest,
        call: ModelToolCall,
    ) -> CharacterIntent:
        if call.name not in request.allowed_tools:
            raise ToolValidationError("tool_not_allowed", call.name)
        args_type = self._types.get(call.name)
        if args_type is None:
            raise ToolValidationError("unknown_tool", call.name)
        try:
            args = args_type.model_validate(call.arguments)
        except ValidationError as error:
            raise ToolValidationError("invalid_arguments", str(error)) from error
        if isinstance(args, CheckEnvironmentArguments):
            raise ToolValidationError(
                "read_tool_requires_round",
                call.name,
            )
        targets = {target.id: target for target in request.observation.targets}
        if isinstance(args, PerformArguments):
            if args.target_id is not None:
                target = targets.get(args.target_id)
                if target is None:
                    raise ToolValidationError(
                        "target_not_observable", args.target_id
                    )
                if not target.available:
                    raise ToolValidationError(
                        "precondition_failed",
                        f"{target.id} is currently unavailable",
                    )
                if target.kind == "station" and args.action not in target.supported_actions:
                    raise ToolValidationError(
                        "precondition_failed",
                        f"{target.id} does not support {args.action}",
                    )
            return ActivityIntent(
                request.decision_id,
                call.call_id,
                request.agent_id,
                IntentKind.ACTIVITY,
                args.reason,
                ActionType(args.action),
                args.target_id,
                args.duration_seconds,
            )
        if isinstance(args, SayArguments):
            target = targets.get(args.target_id)
            if target is None or target.kind != "character":
                raise ToolValidationError("target_not_observable", args.target_id)
            return SpeechIntent(
                request.decision_id,
                call.call_id,
                request.agent_id,
                IntentKind.SPEECH,
                args.reason,
                args.target_id,
                args.text,
            )
        if isinstance(args, WaitArguments):
            return WaitIntent(
                request.decision_id,
                call.call_id,
                request.agent_id,
                IntentKind.WAIT,
                args.reason,
                args.duration_seconds,
            )
        if isinstance(args, SkipArguments):
            return SkipIntent(
                request.decision_id,
                call.call_id,
                request.agent_id,
                IntentKind.SKIP,
                args.reason,
                args.reconsider_after_seconds,
            )
        if isinstance(args, NavigateToArguments):
            target = targets.get(args.target_id)
            if target is None or target.kind not in {
                "zone",
                "station",
                "building",
                "outdoor",
            }:
                raise ToolValidationError(
                    "destination_not_known", args.target_id
                )
            if (
                args.preferred_mode is not None
                and args.preferred_mode
                not in request.observation.available_travel_modes
            ):
                raise ToolValidationError(
                    "mode_not_available", args.preferred_mode
                )
            return NavigationIntent(
                request.decision_id,
                call.call_id,
                request.agent_id,
                IntentKind.NAVIGATE,
                args.reason,
                args.target_id,
                (
                    TravelMode(args.preferred_mode)
                    if args.preferred_mode is not None
                    else None
                ),
            )
        if isinstance(args, TransactArguments):
            target = targets.get(args.point_id)
            if target is None or target.kind != "transaction_point":
                raise ToolValidationError(
                    "transaction_point_not_observable",
                    args.point_id,
                )
            if not target.available:
                raise ToolValidationError(
                    "precondition_failed",
                    f"{target.id} is currently unavailable",
                )
            offer = next(
                (
                    candidate
                    for candidate in target.offers
                    if candidate.id == args.offer_id
                ),
                None,
            )
            if offer is None:
                raise ToolValidationError(
                    "offer_not_observable",
                    args.offer_id,
                )
            if not offer.available:
                raise ToolValidationError(
                    "precondition_failed",
                    f"{offer.id} cannot currently be completed",
                )
            return TransactionIntent(
                request.decision_id,
                call.call_id,
                request.agent_id,
                IntentKind.TRANSACT,
                args.reason,
                args.point_id,
                args.offer_id,
            )
        if isinstance(args, ServeTransactionArguments):
            if request.actor_kind != "npc":
                raise ToolValidationError(
                    "tool_not_allowed_for_actor",
                    call.name,
                )
            service_request = next(
                (
                    candidate
                    for candidate in request.observation.service_requests
                    if candidate.request_id == args.request_id
                ),
                None,
            )
            if service_request is None:
                raise ToolValidationError(
                    "transaction_request_not_observable",
                    args.request_id,
                )
            return ServeTransactionIntent(
                request.decision_id,
                call.call_id,
                request.agent_id,
                IntentKind.SERVE_TRANSACTION,
                args.reason,
                args.request_id,
            )
        raise ToolValidationError("invalid_arguments", call.name)

    def is_read_only(self, name: str) -> bool:
        return name == "check_environment"

    def read(
        self,
        request: CharacterDecisionRequest,
        call: ModelToolCall,
    ) -> dict[str, JsonValue]:
        if call.name not in request.allowed_tools:
            raise ToolValidationError("tool_not_allowed", call.name)
        if call.name != "check_environment":
            raise ToolValidationError("not_read_tool", call.name)
        try:
            args = CheckEnvironmentArguments.model_validate(call.arguments)
        except ValidationError as error:
            raise ToolValidationError("invalid_arguments", str(error)) from error
        if request.observation.environment is None:
            return {
                "values": {},
                "unavailable_topics": list(args.topics),
            }
        requested_topics = set(args.topics)
        values = {
            topic: value
            for topic, value in request.observation.environment.values.items()
            if topic in requested_topics
        }
        unavailable = requested_topics - set(values)
        unavailable.update(
            topic
            for topic in request.observation.environment.unavailable_topics
            if topic in requested_topics
        )
        result: dict[str, JsonValue] = {
            "values": cast(dict[str, JsonValue], values),
            "unavailable_topics": cast(list[JsonValue], sorted(unavailable)),
        }
        return result


def _schema(model: type[BaseModel]) -> dict[str, JsonValue]:
    return TypeAdapter(model).json_schema()


_DESCRIPTIONS = {
    "perform": "Attempt a supported bounded activity or affordance.",
    "say": "Speak exact in-world words to a known character.",
    "wait": "Remain intentionally idle for a bounded duration.",
    "skip": "Take no intentional action now and reconsider later.",
    "navigate_to": (
        "Navigate to a known zone, station, building, or outdoor place, "
        "optionally preferring a transport mode."
    ),
    "transact": (
        "Attempt one observable configured exchange at a transaction point."
    ),
    "serve_transaction": (
        "Authorize one assigned observable transaction request at the staffed "
        "point."
    ),
    "check_environment": (
        "Read the currently available time, weather, surface, or availability "
        "information. This does not take an in-world action."
    ),
}
