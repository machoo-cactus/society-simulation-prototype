import asyncio
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace

from stage0_sim.application.agents.contracts import (
    CharacterController,
    CharacterDecision,
    CharacterDecisionRequest,
    ModelClientError,
)
from stage0_sim.application.agents.tools import ToolRegistry, ToolValidationError
from stage0_sim.application.cognition import EmbeddingError
from stage0_sim.application.memory import EpisodicMemoryStore
from stage0_sim.domain.components import (
    ActionType,
    ControllerComponent,
    DriveComponent,
    PendingSpeechComponent,
    PlanAction,
    PlanComponent,
    System1State,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.intents import (
    ActivityIntent,
    CharacterIntent,
    MoveIntent,
    SkipIntent,
    SpeechIntent,
    TravelIntent,
    WaitIntent,
)
from stage0_sim.domain.systems import SystemContext


@dataclass(frozen=True, slots=True)
class _CompletedDecision:
    request: CharacterDecisionRequest
    decision: CharacterDecision | None
    error: ModelClientError | None


@dataclass(frozen=True, slots=True)
class _PendingDecision:
    request: CharacterDecisionRequest
    submitted_at: float


class AgentWorkCoordinator:
    def __init__(
        self,
        controller: CharacterController,
        tool_registry: ToolRegistry,
        *,
        max_concurrency: int = 4,
        request_timeout_seconds: float = 30.0,
        max_requests: int | None = None,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        memory_store: EpisodicMemoryStore | None = None,
        execution_mode: str = "global_barrier",
    ) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be greater than zero")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be greater than zero")
        if execution_mode not in {"global_barrier", "background"}:
            raise ValueError(
                "execution_mode must be global_barrier or background"
            )
        self.controller = controller
        self.tool_registry = tool_registry
        self.request_timeout_seconds = request_timeout_seconds
        self.max_requests = max_requests
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.memory_store = memory_store
        self.execution_mode = execution_mode
        self.request_count = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self._executor = ThreadPoolExecutor(
            max_workers=(
                1
                if bool(getattr(self.controller, "synchronous", False))
                else max_concurrency
            ),
            thread_name_prefix="stage0-controller",
        )
        self._pending: dict[Future[CharacterDecision], _PendingDecision] = {}
        self._queued: list[CharacterDecisionRequest] = []

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

    def submit(self, request: CharacterDecisionRequest) -> None:
        if self.budget_failure() is not None:
            raise RuntimeError("cannot submit after cognition budget exhaustion")
        self.request_count += 1
        self._queued.append(request)

    def _start(self, request: CharacterDecisionRequest) -> None:
        future = self._executor.submit(asyncio.run, self._decide(request))
        self._pending[future] = _PendingDecision(request, time.monotonic())

    def drain(self, context: SystemContext) -> None:
        completed = self._collect_completed(start_queued=True)
        for item in completed:
            self._apply(context, item)

    async def drain_and_wait(
        self,
        context: SystemContext,
        *,
        on_applying: Callable[[], None] | None = None,
    ) -> None:
        completed = self._collect_completed(start_queued=True)
        while self._pending:
            if completed:
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(0.01)
            completed.extend(self._collect_completed(start_queued=False))
        if on_applying is not None and completed:
            on_applying()
        for item in completed:
            self._apply(context, item)

    @property
    def pending_count(self) -> int:
        return len(self._queued) + len(self._pending)

    @property
    def pending_decision_ids(self) -> tuple[str, ...]:
        requests = [
            *self._queued,
            *(pending.request for pending in self._pending.values()),
        ]
        return tuple(
            request.decision_id
            for request in sorted(
                requests,
                key=lambda item: (
                    item.requested_tick,
                    item.agent_id,
                    item.decision_id,
                ),
            )
        )

    def _collect_completed(
        self,
        *,
        start_queued: bool,
    ) -> list[_CompletedDecision]:
        if start_queued:
            queued = tuple(self._queued)
            self._queued.clear()
            for request in queued:
                self._start(request)
        completed: list[_CompletedDecision] = []
        for future, pending in tuple(self._pending.items()):
            request = pending.request
            if not future.done():
                if (
                    time.monotonic() - pending.submitted_at
                    < self.request_timeout_seconds
                ):
                    continue
                del self._pending[future]
                future.cancel()
                completed.append(
                    _CompletedDecision(
                        request,
                        None,
                        ModelClientError(
                            "model request timed out",
                            reason="provider_timeout",
                        ),
                    )
                )
                continue
            del self._pending[future]
            try:
                decision = future.result()
            except ModelClientError as error:
                completed.append(_CompletedDecision(request, None, error))
            else:
                completed.append(_CompletedDecision(request, decision, None))
        completed.sort(
            key=lambda item: (
                item.request.requested_tick,
                item.request.agent_id,
                item.request.decision_id,
            )
        )
        return completed

    def cancel_all(self, context: SystemContext, reason: str) -> None:
        for request in self._queued:
            self._clear_pending(context, request)
            context.events.emit(
                "cognition.cancelled",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=request.agent_id,
                payload={"decision_id": request.decision_id, "reason": reason},
                correlation_id=request.decision_id,
            )
        self._queued.clear()
        for future, pending in tuple(self._pending.items()):
            request = pending.request
            future.cancel()
            self._clear_pending(context, request)
            context.events.emit(
                "cognition.cancelled",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=request.agent_id,
                payload={"decision_id": request.decision_id, "reason": reason},
                correlation_id=request.decision_id,
            )
        self._pending.clear()

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _decide(
        self, request: CharacterDecisionRequest
    ) -> CharacterDecision:
        if self.memory_store is None:
            return await self.controller.decide(request)
        query_parts = [
            *request.observation.goals,
            *(fact.text for fact in request.observation.facts),
        ]
        if not query_parts:
            return await self.controller.decide(request)
        try:
            retrieved = self.memory_store.retrieve(
                agent_id=request.agent_id,
                query=" ".join(query_parts),
                simulation_time=request.observation.simulation_time,
                top_k=5,
            )
        except EmbeddingError as error:
            raise ModelClientError(
                f"memory retrieval failed: {error}",
                reason="provider_error",
            ) from error
        enriched = replace(
            request,
            memories=tuple(item.record.text for item in retrieved),
        )
        return await self.controller.decide(enriched)

    def _apply(
        self, context: SystemContext, completed: _CompletedDecision
    ) -> None:
        request = completed.request
        if completed.error is not None:
            self._clear_pending(context, request)
            context.events.emit(
                "cognition.failed",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=request.agent_id,
                payload={
                    "decision_id": request.decision_id,
                    "reason": completed.error.reason,
                    "message": str(completed.error),
                },
                correlation_id=request.decision_id,
            )
            return
        decision = completed.decision
        if decision is None:
            raise RuntimeError("completed decision has neither result nor error")
        turn = decision.model_turn
        self.input_tokens += turn.input_tokens or 0
        self.output_tokens += turn.output_tokens or 0
        context.events.emit(
            "cognition.completed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=request.agent_id,
            payload={
                "decision_id": request.decision_id,
                "provider": turn.provider,
                "model": turn.model,
                "latency_ms": turn.latency_ms,
                "input_tokens": turn.input_tokens,
                "output_tokens": turn.output_tokens,
                "finish_reason": turn.finish_reason,
            },
            correlation_id=request.decision_id,
        )
        if decision.error is not None or decision.tool_call is None:
            self._reject(
                context,
                request,
                None,
                "invalid_arguments",
                decision.error or "model did not return a tool",
                details={
                    "expected_tool_call_count": 1,
                    "actual_tool_call_count": len(turn.tool_calls),
                    "finish_reason": turn.finish_reason,
                    "has_text": bool(turn.text),
                    "offered_tools": list(request.allowed_tools),
                },
            )
            return
        call = decision.tool_call
        context.events.emit(
            "tool.proposed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=request.agent_id,
            payload={
                "decision_id": request.decision_id,
                "tool_call_id": call.call_id,
                "tool_name": call.name,
                "arguments": call.arguments,
            },
            correlation_id=request.decision_id,
        )
        freshness = self._freshness_failure(context, request)
        if freshness is not None:
            self._reject(context, request, call.call_id, freshness, freshness)
            return
        try:
            intent = self.tool_registry.propose(request, call)
        except ToolValidationError as error:
            self._reject(
                context,
                request,
                call.call_id,
                error.reason,
                str(error),
            )
            return
        context.events.emit(
            "tool.accepted",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=request.agent_id,
            payload={
                "decision_id": request.decision_id,
                "tool_call_id": call.call_id,
                "tool_name": call.name,
            },
            correlation_id=request.decision_id,
        )
        self._commit(context, request, call.name, intent)

    def _commit(
        self,
        context: SystemContext,
        request: CharacterDecisionRequest,
        tool_name: str,
        intent: CharacterIntent,
    ) -> None:
        plan = context.registry.get_component(request.agent_id, PlanComponent)
        if plan.current is not None or plan.queue:
            self._reject(
                context,
                request,
                intent.tool_call_id,
                "conflicting_action",
                "character already has an action",
            )
            return
        if isinstance(intent, MoveIntent):
            plan.queue.append(
                PlanAction(action=ActionType.MOVE_TO, target=intent.target_id)
            )
        elif isinstance(intent, ActivityIntent):
            duration = intent.duration_seconds
            if intent.action.value in {"WORK", "READ"} and duration is None:
                self._reject(
                    context,
                    request,
                    intent.tool_call_id,
                    "invalid_arguments",
                    f"{intent.action.value} requires duration_seconds",
                )
                return
            plan.queue.append(
                PlanAction(
                    action=intent.action,
                    target=intent.target_id,
                    duration=duration,
                )
            )
        elif isinstance(intent, WaitIntent):
            plan.queue.append(
                PlanAction(
                    action=ActionType.IDLE,
                    duration=intent.duration_seconds,
                )
            )
        elif isinstance(intent, SkipIntent):
            pass
        elif isinstance(intent, SpeechIntent):
            if context.registry.has_component(
                request.agent_id, PendingSpeechComponent
            ):
                self._reject(
                    context,
                    request,
                    intent.tool_call_id,
                    "conflicting_action",
                    "speech is already pending",
                )
                return
            context.registry.add_component(
                request.agent_id,
                PendingSpeechComponent(
                    decision_id=intent.decision_id,
                    tool_call_id=intent.tool_call_id,
                    target_id=intent.target_id,
                    text=intent.text,
                    channel=intent.channel,
                ),
            )
        elif isinstance(intent, TravelIntent):
            plan.queue.append(
                PlanAction(
                    action=ActionType.TRAVEL_TO,
                    target=intent.target_id,
                    mode=intent.mode,
                )
            )
        else:
            raise TypeError(f"unsupported intent: {type(intent).__name__}")
        state = context.registry.get_component(
            request.agent_id, ControllerComponent
        )
        state.request_pending = False
        state.current_decision_id = None
        state.state_revision += 1
        state.last_outcome = f"{tool_name} committed"
        if isinstance(intent, SkipIntent):
            state.next_decision_time = (
                context.clock.simulation_time
                + intent.reconsider_after_seconds
            )
        context.events.emit(
            "tool.committed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=request.agent_id,
            payload={
                "decision_id": request.decision_id,
                "tool_call_id": intent.tool_call_id,
                "tool_name": tool_name,
                "intent_kind": intent.kind.value,
            },
            correlation_id=request.decision_id,
        )
        if isinstance(intent, SkipIntent):
            context.events.emit(
                "cognition.skipped",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=request.agent_id,
                payload={
                    "decision_id": request.decision_id,
                    "tool_call_id": intent.tool_call_id,
                    "reconsider_after_seconds": (
                        intent.reconsider_after_seconds
                    ),
                    "next_decision_time": state.next_decision_time,
                },
                correlation_id=request.decision_id,
            )

    def _reject(
        self,
        context: SystemContext,
        request: CharacterDecisionRequest,
        tool_call_id: str | None,
        reason: str,
        message: str,
        *,
        details: dict[str, JsonValue] | None = None,
    ) -> None:
        self._clear_pending(context, request)
        context.events.emit(
            "tool.rejected",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=request.agent_id,
            payload={
                "decision_id": request.decision_id,
                "tool_call_id": tool_call_id,
                "reason": reason,
                "message": message,
                **(details or {}),
            },
            correlation_id=request.decision_id,
        )

    @staticmethod
    def _freshness_failure(
        context: SystemContext, request: CharacterDecisionRequest
    ) -> str | None:
        if not context.registry.has_component(
            request.agent_id, ControllerComponent
        ):
            return "stale_decision"
        state = context.registry.get_component(
            request.agent_id, ControllerComponent
        )
        if (
            state.current_decision_id != request.decision_id
            or state.state_revision != request.state_revision
        ):
            return "stale_decision"
        if (
            context.registry.has_component(request.agent_id, DriveComponent)
            and context.registry.get_component(
                request.agent_id, DriveComponent
            ).state
            is not System1State.NORMAL
        ):
            return "system1_preemption"
        return None

    @staticmethod
    def _clear_pending(
        context: SystemContext, request: CharacterDecisionRequest
    ) -> None:
        if not context.registry.has_component(
            request.agent_id, ControllerComponent
        ):
            return
        state = context.registry.get_component(
            request.agent_id, ControllerComponent
        )
        if state.current_decision_id == request.decision_id:
            state.request_pending = False
            state.current_decision_id = None


def intent_payload(intent: CharacterIntent) -> dict[str, JsonValue]:
    return {
        "decision_id": intent.decision_id,
        "tool_call_id": intent.tool_call_id,
        "kind": intent.kind.value,
    }
