from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from stage0_sim.application.agents.contracts import (
    CharacterDecisionRequest,
    ModelToolCall,
    ObservedContentEndpoint,
    ToolDefinition,
)
from stage0_sim.domain.components import ActionType
from stage0_sim.domain.content import (
    TextAttributionDisplay,
    TextBlockDraft,
    TextBlockKind,
    TextOperation,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.intents import (
    ActivityIntent,
    CharacterIntent,
    EngageIntent,
    IntentKind,
    InteractionIntent,
    NavigationIntent,
    ServeTransactionIntent,
    SkipIntent,
    SpeechIntent,
    TextReadIntent,
    TextWriteIntent,
    TransactionIntent,
    WaitIntent,
)
from stage0_sim.domain.interactions import (
    InteractionSpecification,
    InteractionVerb,
)
from stage0_sim.domain.text_actions import (
    TextAttributionRequest,
    TextReadSpecification,
    TextWriteSpecification,
)
from stage0_sim.domain.world import TravelMode


class ToolValidationError(ValueError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class PerformArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["WORK", "READ", "DRINK", "EAT", "SLEEP", "RELAX"]
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


class InteractWithArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verb: InteractionVerb
    target_id: str = Field(min_length=1)
    destination_id: str | None = Field(default=None, min_length=1)
    slot_id: str | None = Field(default=None, min_length=1)
    reason: str | None = Field(default=None, max_length=300)


class EngageArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(min_length=1, max_length=1000)
    reference_ids: list[str] = Field(default_factory=list, max_length=12)
    reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def references_are_unique(self) -> "EngageArguments":
        self.intent = self.intent.strip()
        if not self.intent:
            raise ValueError("engagement intent must not be blank")
        if any(not reference_id.strip() for reference_id in self.reference_ids):
            raise ValueError("engagement reference IDs must not be empty")
        if len(self.reference_ids) != len(set(self.reference_ids)):
            raise ValueError("engagement reference IDs must be unique")
        return self


class CheckEnvironmentArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topics: list[
        Literal["time", "weather", "surface_conditions", "availability"]
    ] = Field(default=["time", "weather"])


class ReadTextArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    block_ids: list[str] = Field(default_factory=list, max_length=64)
    reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def block_ids_are_unique(self) -> "ReadTextArguments":
        if len(self.block_ids) != len(set(self.block_ids)):
            raise ValueError("block_ids must be unique")
        return self


class TextAttributionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display: TextAttributionDisplay = TextAttributionDisplay.VERIFIED
    sender_address_id: str | None = Field(default=None, min_length=1)
    display_label: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def shape_is_valid(self) -> "TextAttributionArguments":
        if self.display is TextAttributionDisplay.ANONYMOUS and (
            self.sender_address_id is not None
            or self.display_label is not None
        ):
            raise ValueError("anonymous attribution cannot expose identity")
        if self.display in {
            TextAttributionDisplay.PSEUDONYMOUS,
            TextAttributionDisplay.UNVERIFIED,
        } and (
            self.display_label is None
            or self.sender_address_id is not None
        ):
            raise ValueError(
                f"{self.display.value} attribution requires only display_label"
            )
        return self


class TextBlockArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=65_536)
    kind: TextBlockKind = TextBlockKind.PARAGRAPH


class WriteTextBaseArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    attribution: TextAttributionArguments = Field(
        default_factory=TextAttributionArguments
    )
    reason: str | None = Field(default=None, max_length=300)


class CreateTextArguments(WriteTextBaseArguments):
    operation: Literal["create"]
    expected_collection_revision: int = Field(gt=0)
    expected_sent_collection_revision: int | None = Field(default=None, gt=0)
    recipient_address_id: str | None = Field(default=None, min_length=1)
    artifact_id_hint: str | None = Field(default=None, min_length=1)
    blocks: list[TextBlockArguments] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def message_fields_are_paired(self) -> "CreateTextArguments":
        if (
            self.recipient_address_id is None
        ) != (self.expected_sent_collection_revision is None):
            raise ValueError(
                "recipient_address_id and expected_sent_collection_revision "
                "must be provided together"
            )
        return self


class AppendTextArguments(WriteTextBaseArguments):
    operation: Literal["append"]
    artifact_id: str = Field(min_length=1)
    expected_artifact_revision: int = Field(gt=0)
    blocks: list[TextBlockArguments] = Field(min_length=1, max_length=128)


class ReplaceTextArguments(WriteTextBaseArguments):
    operation: Literal["replace"]
    artifact_id: str = Field(min_length=1)
    expected_artifact_revision: int = Field(gt=0)
    block_id: str = Field(min_length=1)
    expected_block_revision: int = Field(gt=0)
    text: str = Field(max_length=65_536)


class EditTextArguments(WriteTextBaseArguments):
    operation: Literal["edit"]
    artifact_id: str = Field(min_length=1)
    expected_artifact_revision: int = Field(gt=0)
    block_id: str = Field(min_length=1)
    expected_block_revision: int = Field(gt=0)
    text: str = Field(max_length=65_536)
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def range_is_ordered(self) -> "EditTextArguments":
        if self.end < self.start:
            raise ValueError("edit end must not precede start")
        return self


class DeleteTextArguments(WriteTextBaseArguments):
    operation: Literal["delete"]
    artifact_id: str = Field(min_length=1)
    expected_artifact_revision: int = Field(gt=0)
    block_id: str | None = Field(default=None, min_length=1)
    expected_block_revision: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def block_fields_are_paired(self) -> "DeleteTextArguments":
        if (self.block_id is None) != (self.expected_block_revision is None):
            raise ValueError(
                "block_id and expected_block_revision must be provided together"
            )
        return self


WriteTextOperationArguments = Annotated[
    CreateTextArguments
    | AppendTextArguments
    | ReplaceTextArguments
    | EditTextArguments
    | DeleteTextArguments,
    Field(discriminator="operation"),
]


class WriteTextArguments(RootModel[WriteTextOperationArguments]):
    pass


ToolArguments = Annotated[
    PerformArguments
    | SayArguments
    | WaitArguments
    | SkipArguments
    | NavigateToArguments
    | TransactArguments
    | ServeTransactionArguments
    | InteractWithArguments
    | EngageArguments
    | ReadTextArguments
    | WriteTextArguments
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
            "interact_with": InteractWithArguments,
            "engage": EngageArguments,
            "check_environment": CheckEnvironmentArguments,
            "read_text": ReadTextArguments,
            "write_text": WriteTextArguments,
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
                "room",
                "physical_object",
                "transaction_point",
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
        if isinstance(args, InteractWithArguments):
            target = targets.get(args.target_id)
            if target is None:
                raise ToolValidationError(
                    "target_not_observable",
                    args.target_id,
                )
            if args.verb.value not in target.available_interactions:
                raise ToolValidationError(
                    "interaction_not_available",
                    f"{args.target_id} does not advertise {args.verb.value}",
                )
            if args.destination_id is not None:
                destination = targets.get(args.destination_id)
                if destination is None:
                    raise ToolValidationError(
                        "target_not_observable",
                        args.destination_id,
                    )
            try:
                specification = InteractionSpecification(
                    args.verb,
                    args.target_id,
                    args.destination_id,
                    args.slot_id,
                )
            except ValueError as error:
                raise ToolValidationError(
                    "invalid_arguments",
                    str(error),
                ) from error
            return InteractionIntent(
                request.decision_id,
                call.call_id,
                request.agent_id,
                IntentKind.INTERACT,
                args.reason,
                specification,
            )
        if isinstance(args, EngageArguments):
            for reference_id in args.reference_ids:
                if reference_id not in targets:
                    raise ToolValidationError(
                        "reference_not_observable",
                        reference_id,
                    )
            return EngageIntent(
                request.decision_id,
                call.call_id,
                request.agent_id,
                IntentKind.ENGAGE,
                args.reason,
                args.intent.strip(),
                tuple(args.reference_ids),
            )
        if isinstance(args, ReadTextArguments):
            target = targets.get(args.target_id)
            endpoint = _observed_endpoint(target, args.endpoint_id)
            if endpoint is None or TextOperation.READ.value not in endpoint.operations:
                raise ToolValidationError(
                    "content_endpoint_not_available",
                    args.endpoint_id,
                )
            artifact = next(
                (
                    item
                    for item in endpoint.artifacts
                    if item.id == args.artifact_id
                ),
                None,
            )
            if artifact is None:
                raise ToolValidationError(
                    "text_artifact_not_observable",
                    args.artifact_id,
                )
            known_blocks = {block.id for block in artifact.blocks}
            if any(block_id not in known_blocks for block_id in args.block_ids):
                raise ToolValidationError(
                    "text_block_not_observable",
                    args.artifact_id,
                )
            return TextReadIntent(
                request.decision_id,
                call.call_id,
                request.agent_id,
                IntentKind.READ_TEXT,
                args.reason,
                TextReadSpecification(
                    args.target_id,
                    args.endpoint_id,
                    args.artifact_id,
                    tuple(args.block_ids),
                ),
            )
        if isinstance(args, WriteTextArguments):
            value = args.root
            target = targets.get(value.target_id)
            endpoint = _observed_endpoint(target, value.endpoint_id)
            if endpoint is None or value.operation not in endpoint.operations:
                raise ToolValidationError(
                    "content_endpoint_not_available",
                    value.endpoint_id,
                )
            text_specification = _text_write_specification(value)
            if isinstance(value, CreateTextArguments):
                if value.recipient_address_id is None:
                    if (
                        endpoint.collection_revision
                        != value.expected_collection_revision
                    ):
                        raise ToolValidationError(
                            "revision_conflict",
                            endpoint.resource_id,
                        )
                else:
                    destination_address = next(
                        (
                            address
                            for address in request.observation.text_addresses
                            if address.id == value.recipient_address_id
                        ),
                        None,
                    )
                    if destination_address is None:
                        raise ToolValidationError(
                            "text_address_not_known",
                            value.recipient_address_id,
                        )
                    if (
                        destination_address.mailbox_revision
                        != value.expected_collection_revision
                        or endpoint.collection_revision
                        != value.expected_sent_collection_revision
                    ):
                        raise ToolValidationError(
                            "revision_conflict",
                            value.recipient_address_id,
                        )
                    sender_address_id = value.attribution.sender_address_id
                    if not any(
                        address.id == sender_address_id
                        and address.controlled
                        for address in request.observation.text_addresses
                    ):
                        raise ToolValidationError(
                            "sender_not_authorized",
                            sender_address_id or "",
                        )
            if text_specification.artifact_id is not None:
                artifact = next(
                    (
                        item
                        for item in endpoint.artifacts
                        if item.id == text_specification.artifact_id
                    ),
                    None,
                )
                if artifact is None:
                    raise ToolValidationError(
                        "text_artifact_not_observable",
                        text_specification.artifact_id,
                    )
                if (
                    artifact.revision
                    != text_specification.expected_artifact_revision
                ):
                    raise ToolValidationError(
                        "revision_conflict",
                        text_specification.artifact_id,
                    )
                if text_specification.block_id is not None:
                    block = next(
                        (
                            item
                            for item in artifact.blocks
                            if item.id == text_specification.block_id
                        ),
                        None,
                    )
                    if (
                        block is None
                        or block.revision
                        != text_specification.expected_block_revision
                    ):
                        raise ToolValidationError(
                            "revision_conflict",
                            text_specification.block_id,
                        )
            return TextWriteIntent(
                request.decision_id,
                call.call_id,
                request.agent_id,
                IntentKind.WRITE_TEXT,
                value.reason,
                text_specification,
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
    "interact_with": (
        "Attempt one advertised physical interaction with an observable target."
    ),
    "engage": (
        "Attempt a free-form in-world behavior when no other available action "
        "tool accurately expresses the intention. References must use supplied "
        "observable IDs. The simulation determines all actual effects."
    ),
    "check_environment": (
        "Read the currently available time, weather, surface, or availability "
        "information. This does not take an in-world action."
    ),
    "read_text": (
        "Read an advertised in-world text artifact through an accessible "
        "content endpoint. The embodied action completes before text is supplied."
    ),
    "write_text": (
        "Create, append, replace, edit, or tombstone text through an advertised "
        "content endpoint using the supplied expected revisions."
    ),
}


def _observed_endpoint(
    target: object,
    endpoint_id: str,
) -> ObservedContentEndpoint | None:
    from stage0_sim.application.agents.contracts import (
        ObservedTarget,
    )

    if not isinstance(target, ObservedTarget):
        return None
    return next(
        (
            endpoint
            for endpoint in target.content_endpoints
            if endpoint.id == endpoint_id
        ),
        None,
    )


def _text_write_specification(
    args: WriteTextOperationArguments,
) -> TextWriteSpecification:
    attribution = TextAttributionRequest(
        args.attribution.display,
        args.attribution.sender_address_id,
        args.attribution.display_label,
    )
    if isinstance(args, CreateTextArguments):
        return TextWriteSpecification(
            TextOperation.CREATE,
            args.target_id,
            args.endpoint_id,
            attribution,
            expected_collection_revision=args.expected_collection_revision,
            expected_sent_collection_revision=(
                args.expected_sent_collection_revision
            ),
            blocks=tuple(
                TextBlockDraft(block.text, block.kind)
                for block in args.blocks
            ),
            recipient_address_id=args.recipient_address_id,
            artifact_id_hint=args.artifact_id_hint,
        )
    if isinstance(args, AppendTextArguments):
        return TextWriteSpecification(
            TextOperation.APPEND,
            args.target_id,
            args.endpoint_id,
            attribution,
            artifact_id=args.artifact_id,
            expected_artifact_revision=args.expected_artifact_revision,
            blocks=tuple(
                TextBlockDraft(block.text, block.kind)
                for block in args.blocks
            ),
        )
    if isinstance(args, ReplaceTextArguments) and not isinstance(
        args, EditTextArguments
    ):
        return TextWriteSpecification(
            TextOperation.REPLACE,
            args.target_id,
            args.endpoint_id,
            attribution,
            artifact_id=args.artifact_id,
            expected_artifact_revision=args.expected_artifact_revision,
            block_id=args.block_id,
            expected_block_revision=args.expected_block_revision,
            text=args.text,
        )
    if isinstance(args, EditTextArguments):
        return TextWriteSpecification(
            TextOperation.EDIT,
            args.target_id,
            args.endpoint_id,
            attribution,
            artifact_id=args.artifact_id,
            expected_artifact_revision=args.expected_artifact_revision,
            block_id=args.block_id,
            expected_block_revision=args.expected_block_revision,
            text=args.text,
            start=args.start,
            end=args.end,
        )
    if isinstance(args, DeleteTextArguments):
        return TextWriteSpecification(
            TextOperation.DELETE,
            args.target_id,
            args.endpoint_id,
            attribution,
            artifact_id=args.artifact_id,
            expected_artifact_revision=args.expected_artifact_revision,
            block_id=args.block_id,
            expected_block_revision=args.expected_block_revision,
        )
    raise AssertionError("unhandled text write arguments")
