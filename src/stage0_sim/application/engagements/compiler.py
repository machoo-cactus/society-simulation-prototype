from dataclasses import dataclass

from pydantic import ValidationError

from stage0_sim.application.agents.contracts import (
    CharacterDecisionRequest,
    ModelClient,
    ModelMessage,
    ModelRequest,
    ModelToolCall,
    ModelTurn,
    ToolDefinition,
)
from stage0_sim.application.engagements.catalog import (
    EngagementCapabilityCatalog,
    build_v1_capability_catalog,
)
from stage0_sim.application.engagements.context import (
    CompilerSceneError,
    EngagementCompilerScene,
    build_engagement_compiler_scene,
)
from stage0_sim.application.engagements.contracts import (
    CapabilityInvocationProposal,
    CompilationDisposition,
    EngagementCompilationProposal,
    EngagementCompilationResult,
    GroupValidationIssue,
    InvocationGroupProposal,
    NormalizedCapabilityInvocation,
    NormalizedInvocationGroup,
    RejectedInvocationGroup,
)
from stage0_sim.application.scenario import (
    EngagementCompilerSettingsDefinition,
    EngagementSettingsDefinition,
)
from stage0_sim.domain.engagements import EngagementSpecification

ENGAGEMENT_COMPILATION_OPERATION = "engagement_compilation"
ENGAGEMENT_COMPILATION_PROMPT_VERSION = "engagement_compilation.v1"
COMPILE_ENGAGEMENT_TOOL = "compile_engagement"

_SYSTEM_PROMPT = (
    "Operation: engagement_compilation. Compile the supplied attempted engagement "
    "against only the frozen sanitized scene and its versioned capability catalog. "
    "You are not simulation authority and must not invent references, capabilities, "
    "numeric state writes, private target state, success, or future cooperation. "
    "Return exactly one compile_engagement tool call and no prose. Select "
    "specialized_tool_required when an offered specialized controller tool fully "
    "covers the attempt, unsupported when the catalog cannot represent it, otherwise "
    "propose one or more independently valid capability groups."
)


class EngagementCompilerError(RuntimeError):
    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class EngagementCompilerResponseError(EngagementCompilerError):
    pass


class EngagementCompilerValidationError(EngagementCompilerError):
    pass


@dataclass(slots=True, init=False)
class EngagementCompiler:
    model_client: ModelClient
    catalog: EngagementCapabilityCatalog
    compiler_settings: EngagementCompilerSettingsDefinition
    engagement_settings: EngagementSettingsDefinition

    def __init__(
        self,
        model_client: ModelClient,
        *,
        catalog: EngagementCapabilityCatalog | None = None,
        compiler_settings: EngagementCompilerSettingsDefinition | None = None,
        engagement_settings: EngagementSettingsDefinition | None = None,
    ) -> None:
        self.model_client = model_client
        self.catalog = catalog or build_v1_capability_catalog()
        self.compiler_settings = (
            compiler_settings or EngagementCompilerSettingsDefinition()
        ).model_copy(deep=True)
        self.engagement_settings = (
            engagement_settings or EngagementSettingsDefinition()
        ).model_copy(deep=True)

    async def compile_engagement(
        self,
        request: CharacterDecisionRequest,
        engagement: EngagementSpecification,
    ) -> EngagementCompilationResult:
        try:
            scene = build_engagement_compiler_scene(request, engagement, self.catalog)
        except CompilerSceneError as error:
            raise EngagementCompilerValidationError(
                str(error),
                reason="invalid_scene",
            ) from error
        model_request = self._model_request(request, scene)
        turn = await self.model_client.complete(model_request)
        call = self._required_tool_call(turn)
        try:
            proposal = EngagementCompilationProposal.model_validate(call.arguments)
        except ValidationError as error:
            raise EngagementCompilerResponseError(
                f"invalid compile_engagement output: {error}",
                reason="invalid_tool_arguments",
            ) from error
        return self._normalize(request, engagement, scene, proposal, turn)

    async def compile(
        self,
        request: CharacterDecisionRequest,
        engagement: EngagementSpecification,
    ) -> EngagementCompilationResult:
        return await self.compile_engagement(request, engagement)

    def _model_request(
        self,
        request: CharacterDecisionRequest,
        scene: EngagementCompilerScene,
    ) -> ModelRequest:
        request_id = (
            f"{ENGAGEMENT_COMPILATION_OPERATION}:{scene.engagement_id}:"
            f"{scene.content_hash}"
        )
        return ModelRequest(
            request_id=request_id,
            correlation_id=request.decision_id,
            messages=(
                ModelMessage(role="system", content=_SYSTEM_PROMPT),
                ModelMessage(role="user", content=scene.canonical_json()),
            ),
            tools=(
                ToolDefinition(
                    name=COMPILE_ENGAGEMENT_TOOL,
                    description=(
                        "Return one strict engagement compilation proposal using "
                        "only supplied capability and reference names."
                    ),
                    input_schema=EngagementCompilationProposal.model_json_schema(),
                ),
            ),
            model=self.compiler_settings.model_profile,
            timeout_seconds=self.compiler_settings.timeout_seconds,
            max_output_tokens=self.compiler_settings.max_output_tokens,
            prompt_version=ENGAGEMENT_COMPILATION_PROMPT_VERSION,
        )

    def _required_tool_call(self, turn: ModelTurn) -> ModelToolCall:
        if turn.text is not None and turn.text.strip():
            raise EngagementCompilerResponseError(
                "engagement compilation does not accept prose responses",
                reason="unsupported_response_shape",
            )
        if len(turn.tool_calls) != 1:
            raise EngagementCompilerResponseError(
                "engagement compilation requires exactly one tool call",
                reason="exactly_one_tool_required",
            )
        call = turn.tool_calls[0]
        if call.name != COMPILE_ENGAGEMENT_TOOL:
            raise EngagementCompilerResponseError(
                f"unexpected engagement compilation tool: {call.name}",
                reason="unexpected_tool",
            )
        return call

    def _normalize(
        self,
        request: CharacterDecisionRequest,
        engagement: EngagementSpecification,
        scene: EngagementCompilerScene,
        proposal: EngagementCompilationProposal,
        turn: ModelTurn,
    ) -> EngagementCompilationResult:
        if len(proposal.summary) > self.engagement_settings.max_public_text_chars:
            raise EngagementCompilerValidationError(
                "engagement compilation summary exceeds configured public text limit",
                reason="public_text_limit",
            )
        if proposal.disposition is CompilationDisposition.SPECIALIZED_TOOL_REQUIRED:
            tool_name = proposal.specialized_tool
            if (
                tool_name is None
                or tool_name == "engage"
                or tool_name not in request.allowed_tools
            ):
                raise EngagementCompilerValidationError(
                    "specialized-tool disposition did not select an offered "
                    "specialized tool",
                    reason="invalid_specialized_tool",
                )
            return EngagementCompilationResult(
                disposition=proposal.disposition,
                summary=proposal.summary,
                scene_hash=scene.content_hash,
                specialized_tool=tool_name,
                reason=proposal.reason,
                model_turn=turn,
            )
        if proposal.disposition is CompilationDisposition.UNSUPPORTED:
            return EngagementCompilationResult(
                disposition=proposal.disposition,
                summary=proposal.summary,
                scene_hash=scene.content_hash,
                reason=proposal.reason,
                model_turn=turn,
            )

        valid_groups: list[NormalizedInvocationGroup] = []
        rejected_groups: list[RejectedInvocationGroup] = []
        seen_group_ids: set[str] = set()
        seen_invocation_ids: set[str] = set()
        for index, group in enumerate(proposal.groups):
            normalized, rejection = self._validate_group(
                group,
                index=index,
                actor_id=request.agent_id,
                reference_ids=frozenset(engagement.reference_ids),
                seen_group_ids=seen_group_ids,
                seen_invocation_ids=seen_invocation_ids,
            )
            if normalized is not None:
                valid_groups.append(normalized)
            if rejection is not None:
                rejected_groups.append(rejection)
        if not valid_groups:
            raise EngagementCompilerValidationError(
                "compiled engagement contains no valid capability groups",
                reason="no_valid_groups",
            )
        return EngagementCompilationResult(
            disposition=CompilationDisposition.COMPILED,
            summary=proposal.summary,
            scene_hash=scene.content_hash,
            valid_groups=tuple(valid_groups),
            rejected_groups=tuple(rejected_groups),
            reason=proposal.reason,
            model_turn=turn,
        )

    def _validate_group(
        self,
        group: InvocationGroupProposal,
        *,
        index: int,
        actor_id: str,
        reference_ids: frozenset[str],
        seen_group_ids: set[str],
        seen_invocation_ids: set[str],
    ) -> tuple[NormalizedInvocationGroup | None, RejectedInvocationGroup | None]:
        issues: list[GroupValidationIssue] = []
        if index >= self.engagement_settings.max_groups:
            issues.append(
                GroupValidationIssue(
                    code="group_limit",
                    message="group exceeds configured maximum group count",
                )
            )
        if group.group_id in seen_group_ids:
            issues.append(
                GroupValidationIssue(
                    code="duplicate_group_id",
                    message=f"duplicate group ID: {group.group_id}",
                )
            )
        seen_group_ids.add(group.group_id)
        if len(group.invocations) > self.engagement_settings.max_invocations_per_group:
            issues.append(
                GroupValidationIssue(
                    code="invocation_limit",
                    message="group exceeds configured invocation count",
                )
            )
        if (
            group.public_text is not None
            and len(group.public_text) > self.engagement_settings.max_public_text_chars
        ):
            issues.append(
                GroupValidationIssue(
                    code="public_text_limit",
                    message="group public text exceeds configured character limit",
                )
            )

        normalized_invocations: list[NormalizedCapabilityInvocation] = []
        group_invocation_ids: set[str] = set()
        for invocation in group.invocations:
            invocation_issues, normalized = self._validate_invocation(
                invocation,
                actor_id=actor_id,
                reference_ids=reference_ids,
            )
            if (
                invocation.invocation_id in seen_invocation_ids
                or invocation.invocation_id in group_invocation_ids
            ):
                invocation_issues.append(
                    GroupValidationIssue(
                        code="duplicate_invocation_id",
                        message=(
                            f"duplicate invocation ID: {invocation.invocation_id}"
                        ),
                        invocation_id=invocation.invocation_id,
                    )
                )
            group_invocation_ids.add(invocation.invocation_id)
            seen_invocation_ids.add(invocation.invocation_id)
            issues.extend(invocation_issues)
            if normalized is not None:
                normalized_invocations.append(normalized)

        if issues:
            return None, RejectedInvocationGroup(
                group_id=group.group_id,
                ordinal=index,
                issues=tuple(issues),
            )
        return (
            NormalizedInvocationGroup(
                group_id=group.group_id,
                ordinal=index,
                required_atomic=group.required_atomic,
                public_text=group.public_text,
                invocations=tuple(normalized_invocations),
            ),
            None,
        )

    def _validate_invocation(
        self,
        invocation: CapabilityInvocationProposal,
        *,
        actor_id: str,
        reference_ids: frozenset[str],
    ) -> tuple[
        list[GroupValidationIssue],
        NormalizedCapabilityInvocation | None,
    ]:
        capability = invocation.capability
        invocation_id = invocation.invocation_id
        registration = self.catalog.registration(capability)
        if registration is None:
            return [
                GroupValidationIssue(
                    code="unknown_capability",
                    message=f"capability is not registered: {capability}",
                    invocation_id=invocation_id,
                )
            ], None
        try:
            arguments = registration.arguments_model.model_validate(invocation.arguments)
        except ValidationError as error:
            return [
                GroupValidationIssue(
                    code="invalid_capability_arguments",
                    message=str(error),
                    invocation_id=invocation_id,
                )
            ], None

        issues: list[GroupValidationIssue] = []
        subject_id = getattr(arguments, "subject_id", None)
        target_id = getattr(arguments, "target_id", None)
        if subject_id != actor_id:
            issues.append(
                GroupValidationIssue(
                    code="invalid_subject_reference",
                    message="V1 capabilities may act only through the initiating actor",
                    invocation_id=invocation_id,
                )
            )
        if target_id is not None and target_id not in reference_ids:
            issues.append(
                GroupValidationIssue(
                    code="invalid_target_reference",
                    message=f"target is not an engagement reference: {target_id}",
                    invocation_id=invocation_id,
                )
            )
        for name in ("public_text", "activity"):
            value = getattr(arguments, name, None)
            if (
                isinstance(value, str)
                and len(value) > self.engagement_settings.max_public_text_chars
            ):
                issues.append(
                    GroupValidationIssue(
                        code="public_text_limit",
                        message=(
                            f"{name} exceeds configured public text character limit"
                        ),
                        invocation_id=invocation_id,
                    )
                )
        if issues:
            return issues, None
        return [], NormalizedCapabilityInvocation(
            invocation_id=invocation_id,
            capability=capability,
            consequence_tier=registration.consequence_tier,
            arguments=registration.normalizer(
                arguments,
                self.engagement_settings,
            ),
        )
