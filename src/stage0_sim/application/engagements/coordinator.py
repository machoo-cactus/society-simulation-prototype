import asyncio
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from stage0_sim.application.agents.contracts import (
    CharacterDecisionRequest,
    ModelClientError,
)
from stage0_sim.application.data_capture import (
    ActionId,
    DecisionId,
    EngagementId,
    ModelRequestId,
    PlanId,
    RecordCategory,
    RecordJoinIds,
    RecordSource,
    ResearchRecorder,
    ToolCallId,
)
from stage0_sim.application.engagements.compiler import (
    ENGAGEMENT_COMPILATION_OPERATION,
    ENGAGEMENT_COMPILATION_PROMPT_VERSION,
    EngagementCompiler,
    EngagementCompilerError,
)
from stage0_sim.application.engagements.context import (
    ENGAGEMENT_COMPILER_SCENE_VERSION,
)
from stage0_sim.application.engagements.contracts import (
    CompilationDisposition,
    EngagementCompilationResult,
)
from stage0_sim.domain.components import (
    ActionInstance,
    ControllerComponent,
    DriveComponent,
    EngagementArgument,
    EngagementCapabilityInvocation,
    EngagementInvocationGroup,
    EngagementProgram,
    EngagementProgramComponent,
    EngagementValidationIssue,
    PendingEngagementComponent,
    PlanComponent,
    RejectedEngagementGroup,
    System1State,
)
from stage0_sim.domain.engagements import EngagementSpecification
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.engagements import (
    cancel_engagement_state,
    remove_engagement_state,
)
from stage0_sim.domain.systems.plans import (
    fail_plan_action,
    interrupt_plan_action,
)


@dataclass(frozen=True, slots=True)
class _QueuedCompilation:
    request: CharacterDecisionRequest
    action: ActionInstance
    engagement: EngagementSpecification


@dataclass(frozen=True, slots=True)
class _PendingCompilation:
    work: _QueuedCompilation
    submitted_at: float


@dataclass(frozen=True, slots=True)
class _CompilationExecution:
    work: _QueuedCompilation
    result: EngagementCompilationResult | None
    error_reason: str | None = None
    error_message: str | None = None


class EngagementWorkCoordinator:
    def __init__(
        self,
        compiler: EngagementCompiler,
        *,
        max_concurrency: int = 2,
        request_timeout_seconds: float = 30.0,
        max_requests: int | None = None,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        research_recorder: ResearchRecorder | None = None,
    ) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be greater than zero")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be greater than zero")
        self.compiler = compiler
        self.request_timeout_seconds = request_timeout_seconds
        self.max_requests = max_requests
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.research_recorder = research_recorder
        self.request_count = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self._executor = ThreadPoolExecutor(
            max_workers=(
                1
                if bool(
                    getattr(self.compiler.model_client, "synchronous", False)
                )
                else max_concurrency
            ),
            thread_name_prefix="stage0-engagement-compiler",
        )
        self._queued: list[_QueuedCompilation] = []
        self._pending: dict[
            Future[_CompilationExecution],
            _PendingCompilation,
        ] = {}
        self._cancelled_engagement_ids: set[str] = set()
        self._stopped = False
        self._closed = False

    def bind_research_recorder(self, recorder: ResearchRecorder) -> None:
        self.research_recorder = recorder

    def budget_failure(self) -> str | None:
        if self.max_requests is not None and self.request_count >= self.max_requests:
            return "maximum_requests"
        if (
            self.max_input_tokens is not None
            and self.input_tokens >= self.max_input_tokens
        ):
            return "maximum_input_tokens"
        if (
            self.max_output_tokens is not None
            and self.output_tokens >= self.max_output_tokens
        ):
            return "maximum_output_tokens"
        return None

    def submit(
        self,
        context: SystemContext,
        request: CharacterDecisionRequest,
        action: ActionInstance,
    ) -> bool:
        engagement = action.engagement
        if engagement is None:
            raise ValueError("engagement compiler work requires an ENGAGE action")
        failure = (
            "coordinator_closed"
            if self._closed or self._stopped
            else self.budget_failure()
        )
        if failure is not None:
            fail_uncompiled_engagement(
                context,
                request.agent_id,
                action,
                failure,
                f"engagement compilation unavailable: {failure}",
            )
            return False
        if any(
            context.registry.has_component(request.agent_id, component_type)
            for component_type in (
                PendingEngagementComponent,
                EngagementProgramComponent,
            )
        ):
            fail_uncompiled_engagement(
                context,
                request.agent_id,
                action,
                "conflicting_engagement",
                "actor already has engagement work",
            )
            return False
        work = _QueuedCompilation(request, action, engagement)
        context.registry.add_component(
            request.agent_id,
            PendingEngagementComponent(
                engagement_id=engagement.engagement_id,
                action_id=action.action_id,
                plan_id=action.plan_id,
                plan_revision=action.plan_revision,
                decision_id=request.decision_id,
                tool_call_id=action.tool_call_id or "",
                root_correlation_id=action.root_correlation_id,
                requested_tick=request.requested_tick,
                expected_state_revision=request.state_revision + 1,
            ),
        )
        self.request_count += 1
        self._queued.append(work)
        _emit_compilation_event(
            context,
            "engagement.compilation_requested",
            request.agent_id,
            action,
            {
                "catalog_version": self.compiler.catalog.version,
                "prompt_version": ENGAGEMENT_COMPILATION_PROMPT_VERSION,
                "visibility": "private",
            },
        )
        self._record(
            "engagement_compilation_request",
            work,
            {
                "operation": ENGAGEMENT_COMPILATION_OPERATION,
                "request": request,
                "engagement": engagement,
            },
            category=RecordCategory.MODEL,
        )
        return True

    def _start(self, work: _QueuedCompilation) -> None:
        future = self._executor.submit(
            asyncio.run,
            self._compile(work),
        )
        self._pending[future] = _PendingCompilation(
            work,
            time.monotonic(),
        )

    async def _compile(
        self,
        work: _QueuedCompilation,
    ) -> _CompilationExecution:
        try:
            result = await self.compiler.compile_engagement(
                work.request,
                work.engagement,
            )
        except EngagementCompilerError as error:
            return _CompilationExecution(
                work,
                None,
                error.reason,
                str(error),
            )
        except ModelClientError as error:
            return _CompilationExecution(
                work,
                None,
                error.reason,
                str(error),
            )
        return _CompilationExecution(work, result)

    async def drain_and_wait(
        self,
        context: SystemContext,
        *,
        on_applying: Callable[[], None] | None = None,
    ) -> None:
        completed = self._collect_completed(start_queued=True)
        while self._pending:
            await asyncio.sleep(0 if completed else 0.01)
            completed.extend(self._collect_completed(start_queued=False))
        completed.sort(key=_stable_work_key)
        if on_applying is not None and completed and not self._stopped:
            on_applying()
        for execution in completed:
            if self._stopped:
                break
            self._apply(context, execution)

    def _collect_completed(
        self,
        *,
        start_queued: bool,
    ) -> list[_CompilationExecution]:
        if start_queued:
            queued = tuple(self._queued)
            self._queued.clear()
            for work in queued:
                self._start(work)
        completed: list[_CompilationExecution] = []
        for future, pending in tuple(self._pending.items()):
            work = pending.work
            if not future.done():
                if (
                    time.monotonic() - pending.submitted_at
                    < self.request_timeout_seconds
                ):
                    continue
                del self._pending[future]
                future.cancel()
                completed.append(
                    _CompilationExecution(
                        work,
                        None,
                        "provider_timeout",
                        "engagement compiler request timed out",
                    )
                )
                continue
            del self._pending[future]
            execution = future.result()
            completed.append(execution)
        completed.sort(key=_stable_work_key)
        return completed

    def _apply(
        self,
        context: SystemContext,
        execution: _CompilationExecution,
    ) -> None:
        work = execution.work
        result = execution.result
        if work.engagement.engagement_id in self._cancelled_engagement_ids:
            return
        if result is not None:
            turn = result.model_turn
            self.input_tokens += turn.input_tokens or 0
            self.output_tokens += turn.output_tokens or 0
        freshness = self._freshness_failure(context, work)
        if freshness is not None:
            self._fail(
                context,
                work,
                freshness,
                "engagement compilation result is stale",
                result,
            )
            return
        if execution.error_reason is not None:
            self._fail(
                context,
                work,
                execution.error_reason,
                execution.error_message or execution.error_reason,
                None,
            )
            return
        if result is None:
            raise RuntimeError("engagement compilation has neither result nor error")
        if result.disposition is not CompilationDisposition.COMPILED:
            reason = (
                "specialized_tool_required"
                if result.disposition
                is CompilationDisposition.SPECIALIZED_TOOL_REQUIRED
                else "unsupported"
            )
            self._fail(
                context,
                work,
                reason,
                result.reason or result.summary,
                result,
            )
            return
        program = _program_from_result(work, result)
        context.registry.remove_component(
            work.request.agent_id,
            PendingEngagementComponent,
        )
        context.registry.add_component(
            work.request.agent_id,
            EngagementProgramComponent(program),
        )
        _emit_compilation_event(
            context,
            "engagement.compilation_completed",
            work.request.agent_id,
            work.action,
            {
                "scene_hash": result.scene_hash,
                "scene_version": ENGAGEMENT_COMPILER_SCENE_VERSION,
                "catalog_version": self.compiler.catalog.version,
                "prompt_version": ENGAGEMENT_COMPILATION_PROMPT_VERSION,
                "summary": result.summary,
                "valid_group_count": len(result.valid_groups),
                "rejected_group_count": len(result.rejected_groups),
                "valid_groups": [
                    {
                        "group_id": group.group_id,
                        "ordinal": group.ordinal,
                        "required_atomic": group.required_atomic,
                        "public_text": group.public_text,
                        "invocations": [
                            {
                                "invocation_id": invocation.invocation_id,
                                "ordinal": invocation_ordinal,
                                "capability": invocation.capability,
                                "consequence_tier": invocation.consequence_tier,
                                "arguments": {
                                    argument.name: argument.value
                                    for argument in invocation.arguments
                                },
                            }
                            for invocation_ordinal, invocation in enumerate(
                                group.invocations
                            )
                        ],
                    }
                    for group in result.valid_groups
                ],
                "rejected_groups": [
                    {
                        "group_id": group.group_id,
                        "ordinal": group.ordinal,
                        "issues": [
                            {
                                "code": issue.code,
                                "message": issue.message,
                                "invocation_id": issue.invocation_id,
                            }
                            for issue in group.issues
                        ],
                    }
                    for group in result.rejected_groups
                ],
                "provider": result.model_turn.provider,
                "model": result.model_turn.model,
                "input_tokens": result.model_turn.input_tokens,
                "output_tokens": result.model_turn.output_tokens,
                "visibility": "private",
            },
        )
        self._record_result(work, result, "completed")
        if context.registry.has_component(
            work.request.agent_id,
            ControllerComponent,
        ):
            context.registry.get_component(
                work.request.agent_id,
                ControllerComponent,
            ).last_outcome = "engagement compiled"

    def _fail(
        self,
        context: SystemContext,
        work: _QueuedCompilation,
        reason: str,
        message: str,
        result: EngagementCompilationResult | None,
    ) -> None:
        remove_engagement_state(context, work.request.agent_id)
        _emit_compilation_event(
            context,
            "engagement.compilation_failed",
            work.request.agent_id,
            work.action,
            {
                "reason": reason,
                "message": message,
                "disposition": (
                    result.disposition.value if result is not None else None
                ),
                "specialized_tool": (
                    result.specialized_tool if result is not None else None
                ),
                "visibility": "private",
            },
        )
        _emit_engagement_failed(
            context,
            work.request.agent_id,
            work.action,
            reason,
        )
        _fail_action(context, work.request.agent_id, work.action, reason)
        self._record_result(
            work,
            result,
            "failed",
            reason=reason,
            message=message,
        )
        if context.registry.has_component(
            work.request.agent_id,
            ControllerComponent,
        ):
            context.registry.get_component(
                work.request.agent_id,
                ControllerComponent,
            ).last_outcome = f"engagement compilation failed: {reason}"

    @staticmethod
    def _freshness_failure(
        context: SystemContext,
        work: _QueuedCompilation,
    ) -> str | None:
        actor_id = work.request.agent_id
        if actor_id not in context.registry.entities():
            return "stale_actor_state"
        if not context.registry.has_component(actor_id, ControllerComponent):
            return "stale_actor_state"
        controller = context.registry.get_component(
            actor_id,
            ControllerComponent,
        )
        if controller.state_revision != work.request.state_revision + 1:
            return "stale_actor_state"
        if (
            controller.request_pending
            or controller.current_decision_id is not None
        ):
            return "stale_actor_state"
        if (
            context.registry.has_component(actor_id, DriveComponent)
            and context.registry.get_component(
                actor_id,
                DriveComponent,
            ).state
            is not System1State.NORMAL
        ):
            return "system1_preemption"
        if not context.registry.has_component(actor_id, PlanComponent):
            return "stale_actor_state"
        plan = context.registry.get_component(actor_id, PlanComponent)
        actions = (
            *((plan.current,) if plan.current is not None else ()),
            *plan.queue,
        )
        if not any(
            action.action_id == work.action.action_id
            and action.engagement is not None
            and action.engagement.engagement_id
            == work.engagement.engagement_id
            for action in actions
        ):
            return "stale_actor_state"
        if not context.registry.has_component(
            actor_id,
            PendingEngagementComponent,
        ):
            return "stale_actor_state"
        pending = context.registry.get_component(
            actor_id,
            PendingEngagementComponent,
        )
        if (
            pending.engagement_id != work.engagement.engagement_id
            or pending.action_id != work.action.action_id
            or pending.expected_state_revision != controller.state_revision
        ):
            return "stale_actor_state"
        return None

    def cancel_all(self, context: SystemContext, reason: str) -> None:
        self._stopped = True
        work_items = [
            *self._queued,
            *(pending.work for pending in self._pending.values()),
        ]
        self._queued.clear()
        for future in tuple(self._pending):
            future.cancel()
        self._pending.clear()
        for work in sorted(work_items, key=_queued_work_key):
            self._cancelled_engagement_ids.add(
                work.engagement.engagement_id
            )
            cancel_engagement_state(
                context,
                work.request.agent_id,
                reason,
            )
            _cancel_action(
                context,
                work.request.agent_id,
                work.action,
                reason,
            )
            self._record_result(
                work,
                None,
                "cancelled",
                reason=reason,
                message=reason,
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    @property
    def pending_count(self) -> int:
        return len(self._queued) + len(self._pending)

    @property
    def pending_engagement_ids(self) -> tuple[str, ...]:
        work_items = [
            *self._queued,
            *(pending.work for pending in self._pending.values()),
        ]
        return tuple(
            work.engagement.engagement_id
            for work in sorted(work_items, key=_queued_work_key)
        )

    def _record_result(
        self,
        work: _QueuedCompilation,
        result: EngagementCompilationResult | None,
        status: str,
        *,
        reason: str | None = None,
        message: str | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "operation": ENGAGEMENT_COMPILATION_OPERATION,
            "status": status,
            "engagement_id": work.engagement.engagement_id,
            "result": result,
            "reason": reason,
            "message": message,
        }
        model_request_id = (
            f"{ENGAGEMENT_COMPILATION_OPERATION}:"
            f"{work.engagement.engagement_id}:{result.scene_hash}"
            if result is not None
            else None
        )
        self._record(
            "engagement_compilation_result",
            work,
            payload,
            category=RecordCategory.MODEL,
            model_request_id=model_request_id,
        )

    def _record(
        self,
        record_type: str,
        work: _QueuedCompilation,
        payload: object,
        *,
        category: RecordCategory,
        model_request_id: str | None = None,
    ) -> None:
        if self.research_recorder is None:
            return
        self.research_recorder.record(
            record_type,
            payload,
            category=category,
            source=RecordSource.APPLICATION,
            subject_id=work.request.agent_id,
            correlation_id=work.action.root_correlation_id,
            joins=RecordJoinIds(
                plan_id=(
                    PlanId(work.action.plan_id)
                    if work.action.plan_id is not None
                    else None
                ),
                action_id=ActionId(work.action.action_id),
                decision_id=DecisionId(work.request.decision_id),
                model_request_id=(
                    ModelRequestId(model_request_id)
                    if model_request_id is not None
                    else None
                ),
                tool_call_id=(
                    ToolCallId(work.action.tool_call_id)
                    if work.action.tool_call_id is not None
                    else None
                ),
                engagement_id=EngagementId(
                    work.engagement.engagement_id
                ),
            ),
        )


def fail_uncompiled_engagement(
    context: SystemContext,
    actor_id: str,
    action: ActionInstance,
    reason: str,
    message: str,
) -> None:
    remove_engagement_state(context, actor_id)
    _emit_compilation_event(
        context,
        "engagement.compilation_failed",
        actor_id,
        action,
        {
            "reason": reason,
            "message": message,
            "visibility": "private",
        },
    )
    _emit_engagement_failed(context, actor_id, action, reason)
    _fail_action(context, actor_id, action, reason)


def _program_from_result(
    work: _QueuedCompilation,
    result: EngagementCompilationResult,
) -> EngagementProgram:
    return EngagementProgram(
        engagement_id=work.engagement.engagement_id,
        actor_id=work.request.agent_id,
        action_id=work.action.action_id,
        plan_id=work.action.plan_id,
        plan_revision=work.action.plan_revision,
        decision_id=work.request.decision_id,
        tool_call_id=work.action.tool_call_id or "",
        root_correlation_id=work.action.root_correlation_id,
        requested_tick=work.request.requested_tick,
        scene_hash=result.scene_hash,
        groups=tuple(
            EngagementInvocationGroup(
                group_id=group.group_id,
                ordinal=group.ordinal,
                required_atomic=group.required_atomic,
                public_text=group.public_text,
                invocations=tuple(
                    EngagementCapabilityInvocation(
                        invocation_id=invocation.invocation_id,
                        capability=invocation.capability,
                        consequence_tier=invocation.consequence_tier,
                        arguments=tuple(
                            EngagementArgument(
                                argument.name,
                                argument.value,
                            )
                            for argument in invocation.arguments
                        ),
                    )
                    for invocation in group.invocations
                ),
            )
            for group in result.valid_groups
        ),
        rejected_groups=tuple(
            RejectedEngagementGroup(
                group_id=group.group_id,
                ordinal=group.ordinal,
                issues=tuple(
                    EngagementValidationIssue(
                        code=issue.code,
                        message=issue.message,
                        invocation_id=issue.invocation_id,
                    )
                    for issue in group.issues
                ),
            )
            for group in result.rejected_groups
        ),
    )


def _fail_action(
    context: SystemContext,
    actor_id: str,
    action: ActionInstance,
    reason: str,
) -> None:
    plan = _activate_matching_action(context, actor_id, action)
    if plan is not None:
        fail_plan_action(context, actor_id, plan, reason)


def _cancel_action(
    context: SystemContext,
    actor_id: str,
    action: ActionInstance,
    reason: str,
) -> None:
    plan = _activate_matching_action(context, actor_id, action)
    if plan is not None:
        interrupt_plan_action(context, actor_id, plan, reason)


def _activate_matching_action(
    context: SystemContext,
    actor_id: str,
    action: ActionInstance,
) -> PlanComponent | None:
    if (
        actor_id not in context.registry.entities()
        or not context.registry.has_component(actor_id, PlanComponent)
    ):
        return None
    plan = context.registry.get_component(actor_id, PlanComponent)
    if plan.current is not None:
        return plan if plan.current.action_id == action.action_id else None
    for index, queued in enumerate(plan.queue):
        if queued.action_id == action.action_id:
            plan.current = plan.queue.pop(index)
            plan.current_started = False
            return plan
    return None


def _emit_engagement_failed(
    context: SystemContext,
    actor_id: str,
    action: ActionInstance,
    reason: str,
) -> None:
    _emit_compilation_event(
        context,
        "engagement.failed",
        actor_id,
        action,
        {"reason": reason},
    )


def _emit_compilation_event(
    context: SystemContext,
    event_type: str,
    actor_id: str,
    action: ActionInstance,
    extra: dict[str, JsonValue],
) -> None:
    engagement = action.engagement
    context.events.emit(
        event_type,
        simulation_tick=context.clock.tick,
        simulation_time=context.clock.simulation_time,
        agent_id=actor_id,
        payload={
            "engagement_id": (
                engagement.engagement_id if engagement is not None else None
            ),
            "action_id": action.action_id,
            "plan_id": action.plan_id,
            "plan_revision": action.plan_revision,
            "decision_id": action.decision_id,
            "tool_call_id": action.tool_call_id,
            "root_correlation_id": action.root_correlation_id,
            **extra,
        },
        correlation_id=action.root_correlation_id,
    )


def _queued_work_key(work: _QueuedCompilation) -> tuple[int, str, str, str]:
    return (
        work.request.requested_tick,
        work.request.agent_id,
        work.request.decision_id,
        work.engagement.engagement_id,
    )


def _stable_work_key(
    execution: _CompilationExecution,
) -> tuple[int, str, str, str]:
    return _queued_work_key(execution.work)
