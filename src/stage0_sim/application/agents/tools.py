from typing import Annotated, Literal

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
    MoveIntent,
    NavigationIntent,
    SkipIntent,
    SpeechIntent,
    TravelIntent,
    WaitIntent,
)
from stage0_sim.domain.world import TravelMode


class ToolValidationError(ValueError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class GoToArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=300)


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


class TravelToArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=1)
    mode: Literal["WALK", "CYCLE", "CAR", "METRO"]
    reason: str | None = Field(default=None, max_length=300)


class NavigateToArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=1)
    preferred_mode: Literal["WALK", "CYCLE", "CAR", "METRO"] | None = None
    reason: str | None = Field(default=None, max_length=300)


ToolArguments = Annotated[
    GoToArguments
    | PerformArguments
    | SayArguments
    | WaitArguments
    | SkipArguments
    | TravelToArguments
    | NavigateToArguments,
    Field(discriminator=None),
]


class ToolRegistry:
    def __init__(self) -> None:
        self._types: dict[str, type[BaseModel]] = {
            "go_to": GoToArguments,
            "perform": PerformArguments,
            "say": SayArguments,
            "wait": WaitArguments,
            "skip": SkipArguments,
            "travel_to": TravelToArguments,
            "navigate_to": NavigateToArguments,
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
        targets = {target.id: target for target in request.observation.targets}
        if isinstance(args, GoToArguments):
            target = targets.get(args.target_id)
            if target is None or target.kind not in {"zone", "station"}:
                raise ToolValidationError("target_not_observable", args.target_id)
            return MoveIntent(
                request.decision_id,
                call.call_id,
                request.agent_id,
                IntentKind.MOVE,
                args.reason,
                args.target_id,
            )
        if isinstance(args, PerformArguments):
            if args.target_id is not None:
                target = targets.get(args.target_id)
                if target is None:
                    raise ToolValidationError(
                        "target_not_observable", args.target_id
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
        if isinstance(args, TravelToArguments):
            target = targets.get(args.target_id)
            if target is None or target.kind not in {"building", "outdoor"}:
                raise ToolValidationError(
                    "destination_not_known", args.target_id
                )
            if args.mode not in request.observation.available_travel_modes:
                raise ToolValidationError("mode_not_available", args.mode)
            return TravelIntent(
                request.decision_id,
                call.call_id,
                request.agent_id,
                IntentKind.TRAVEL,
                args.reason,
                args.target_id,
                TravelMode(args.mode),
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
        raise ToolValidationError("invalid_arguments", call.name)


def _schema(model: type[BaseModel]) -> dict[str, JsonValue]:
    return TypeAdapter(model).json_schema()


_DESCRIPTIONS = {
    "go_to": "Queue movement toward a known zone or station.",
    "perform": "Attempt a supported bounded activity or affordance.",
    "say": "Speak exact in-world words to a known character.",
    "wait": "Remain intentionally idle for a bounded duration.",
    "skip": "Take no intentional action now and reconsider later.",
    "travel_to": "Travel to a known building using a selected transport mode.",
    "navigate_to": (
        "Navigate to a known zone, station, building, or outdoor place, "
        "optionally preferring a transport mode."
    ),
}
