from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from stage0_sim.domain.components import (
    ActivityComponent,
    ActivityType,
    ControllerComponent,
    ConversationComponent,
    DriveComponent,
    EngagementCapabilityInvocation,
    EngagementExecutionComponent,
    EngagementGroupExecution,
    EngagementGroupStatus,
    EngagementInvocationGroup,
    EngagementProgram,
    EngagementProgramComponent,
    EngagementStatus,
    HomeostasisComponent,
    HomeostasisConfiguration,
    PendingEngagementComponent,
    PlanComponent,
    System1State,
)
from stage0_sim.domain.components.engagement import EngagementScalar
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.perception.auditory import (
    resolve_auditory_recipients,
)
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.homeostasis import apply_homeostasis_deltas

EXPRESSIVE_BEHAVIOR = "expressive_behavior"
AUDITORY_EXPRESSION = "auditory_expression"
BOUNDED_ACTIVITY = "bounded_activity"


class EngagementCapabilityHandler(Protocol):
    @property
    def name(self) -> str: ...

    def validate(
        self,
        context: SystemContext,
        actor_id: str,
        invocation: EngagementCapabilityInvocation,
    ) -> str | None: ...

    def duration(self, invocation: EngagementCapabilityInvocation) -> float: ...

    def commit(
        self,
        context: SystemContext,
        actor_id: str,
        program: EngagementProgram,
        group: EngagementInvocationGroup,
        invocation: EngagementCapabilityInvocation,
    ) -> None: ...


class EngagementCapabilityHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, EngagementCapabilityHandler] = {}

    def register(self, handler: EngagementCapabilityHandler) -> None:
        if not handler.name or handler.name in self._handlers:
            raise ValueError(
                f"duplicate or empty engagement capability: {handler.name!r}"
            )
        self._handlers[handler.name] = handler

    def handler(self, name: str) -> EngagementCapabilityHandler | None:
        return self._handlers.get(name)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))


@dataclass(frozen=True, slots=True)
class ExpressiveBehaviorHandler:
    name: str = EXPRESSIVE_BEHAVIOR

    def validate(
        self,
        context: SystemContext,
        actor_id: str,
        invocation: EngagementCapabilityInvocation,
    ) -> str | None:
        del context
        arguments = _argument_map(invocation)
        if set(arguments) != {
            "expression_band",
            "public_text",
            "subject_id",
            "target_id",
        }:
            return "invalid_expressive_arguments"
        if arguments["subject_id"] != actor_id:
            return "stale_actor_state"
        if not _nonempty_text(arguments["public_text"]):
            return "invalid_public_text"
        if arguments["expression_band"] not in {
            "subtle",
            "moderate",
            "emphatic",
        }:
            return "invalid_expression_band"
        return _optional_target_failure(arguments["target_id"])

    def duration(self, invocation: EngagementCapabilityInvocation) -> float:
        del invocation
        return 0.0

    def commit(
        self,
        context: SystemContext,
        actor_id: str,
        program: EngagementProgram,
        group: EngagementInvocationGroup,
        invocation: EngagementCapabilityInvocation,
    ) -> None:
        arguments = _argument_map(invocation)
        _emit_capability_committed(
            context,
            actor_id,
            program,
            group,
            invocation,
            {
                "modality": "visual",
                "disclosure": "local_visual",
                "public_text": str(arguments["public_text"]),
                "expression_band": str(arguments["expression_band"]),
                "target_id": _optional_text(arguments["target_id"]),
            },
        )


@dataclass(frozen=True, slots=True)
class AuditoryExpressionHandler:
    name: str = AUDITORY_EXPRESSION

    def validate(
        self,
        context: SystemContext,
        actor_id: str,
        invocation: EngagementCapabilityInvocation,
    ) -> str | None:
        arguments = _argument_map(invocation)
        if set(arguments) != {
            "effort_band",
            "energy_cost",
            "listener_effect",
            "listener_stress_delta",
            "mode",
            "public_text",
            "sound_band",
            "sound_range",
            "subject_id",
            "target_id",
        }:
            return "invalid_auditory_arguments"
        if arguments["subject_id"] != actor_id:
            return "stale_actor_state"
        if not context.registry.has_component(actor_id, HomeostasisComponent):
            return "homeostasis_component_missing"
        if not _nonempty_text(arguments["public_text"]):
            return "invalid_public_text"
        if not _nonnegative_number(arguments["energy_cost"]):
            return "invalid_energy_cost"
        if not _nonnegative_number(arguments["listener_stress_delta"]):
            return "invalid_listener_effect"
        if not _nonnegative_int(arguments["sound_range"]):
            return "invalid_sound_range"
        if arguments["mode"] not in {"speech", "nonverbal"}:
            return "invalid_auditory_mode"
        if arguments["sound_band"] not in {"quiet", "normal", "loud"}:
            return "invalid_sound_band"
        if arguments["effort_band"] not in {"low", "medium", "high"}:
            return "invalid_effort_band"
        if arguments["listener_effect"] not in {"none", "alarming"}:
            return "invalid_listener_effect"
        return _optional_target_failure(arguments["target_id"])

    def duration(self, invocation: EngagementCapabilityInvocation) -> float:
        del invocation
        return 0.0

    def commit(
        self,
        context: SystemContext,
        actor_id: str,
        program: EngagementProgram,
        group: EngagementInvocationGroup,
        invocation: EngagementCapabilityInvocation,
    ) -> None:
        arguments = _argument_map(invocation)
        energy_cost = _float_value(arguments["energy_cost"])
        configuration = context.registry.get_resource(HomeostasisConfiguration)
        apply_homeostasis_deltas(
            context,
            actor_id,
            source="engagement.auditory",
            deltas={
                "energy": -energy_cost,
                "social_connection": configuration.social_connection_delta,
                "happiness": configuration.social_happiness_delta,
            },
        )
        sound_range = _int_value(arguments["sound_range"])
        recipients = resolve_auditory_recipients(
            context.registry,
            actor_id,
            maximum_range=sound_range,
        )
        public_text = str(arguments["public_text"])
        mode = str(arguments["mode"])
        if mode == "speech":
            for participant_id in (
                actor_id,
                *(recipient.entity_id for recipient in recipients),
            ):
                if context.registry.has_component(
                    participant_id,
                    ConversationComponent,
                ):
                    context.registry.get_component(
                        participant_id,
                        ConversationComponent,
                    ).turns.append(public_text)
        listener_effect = str(arguments["listener_effect"])
        listener_stress_delta = _float_value(
            arguments["listener_stress_delta"]
        )
        recipient_effects: list[JsonValue] = []
        if listener_effect == "alarming":
            for recipient in recipients:
                if not context.registry.has_component(
                    recipient.entity_id,
                    HomeostasisComponent,
                ):
                    continue
                listener_homeostasis = context.registry.get_component(
                    recipient.entity_id,
                    HomeostasisComponent,
                )
                stress_before = listener_homeostasis.stress
                social_effects = apply_homeostasis_deltas(
                    context,
                    recipient.entity_id,
                    source="engagement.auditory",
                    deltas={
                        "stress": listener_stress_delta,
                        "social_connection": configuration.social_connection_delta,
                        "happiness": configuration.social_happiness_delta,
                        "fear": configuration.alarming_fear_delta,
                    },
                )
                stress_after = listener_homeostasis.stress
                actual_delta = social_effects["stress"]
                effect_evidence: dict[str, JsonValue] = {
                    "recipient_id": recipient.entity_id,
                    "stress_before": stress_before,
                    "stress_after": stress_after,
                    "stress_delta": actual_delta,
                }
                configured_effects = {
                    name: value
                    for name, value in social_effects.items()
                    if name != "stress" and value
                }
                if configured_effects:
                    effect_evidence["homeostasis_delta"] = configured_effects
                recipient_effects.append(effect_evidence)
        else:
            for recipient in recipients:
                if context.registry.has_component(
                    recipient.entity_id,
                    HomeostasisComponent,
                ):
                    apply_homeostasis_deltas(
                        context,
                        recipient.entity_id,
                        source="engagement.auditory",
                        deltas={
                            "social_connection": configuration.social_connection_delta,
                            "happiness": configuration.social_happiness_delta,
                        },
                    )
        recipient_ids: list[JsonValue] = [
            recipient.entity_id for recipient in recipients
        ]
        _emit_capability_committed(
            context,
            actor_id,
            program,
            group,
            invocation,
            {
                "modality": "auditory",
                "disclosure": "local_auditory",
                "public_text": public_text,
                "mode": mode,
                "sound_band": str(arguments["sound_band"]),
                "sound_range": sound_range,
                "effort_band": str(arguments["effort_band"]),
                "energy_cost": energy_cost,
                "listener_effect": listener_effect,
                "listener_stress_delta": listener_stress_delta,
                "target_id": _optional_text(arguments["target_id"]),
                "recipient_ids": recipient_ids,
                "recipient_effects": recipient_effects,
                "recipient_effects_applied": bool(recipient_effects),
            },
        )


@dataclass(frozen=True, slots=True)
class BoundedActivityHandler:
    name: str = BOUNDED_ACTIVITY

    def validate(
        self,
        context: SystemContext,
        actor_id: str,
        invocation: EngagementCapabilityInvocation,
    ) -> str | None:
        arguments = _argument_map(invocation)
        if set(arguments) != {
            "activity",
            "duration_band",
            "duration_seconds",
            "effort_band",
            "energy_cost",
            "stress_delta",
            "stress_effect",
            "subject_id",
            "target_id",
        }:
            return "invalid_bounded_activity_arguments"
        if arguments["subject_id"] != actor_id:
            return "stale_actor_state"
        if not context.registry.has_component(actor_id, ActivityComponent):
            return "activity_component_missing"
        if not context.registry.has_component(actor_id, HomeostasisComponent):
            return "homeostasis_component_missing"
        activity = context.registry.get_component(actor_id, ActivityComponent)
        if activity.movement_override:
            return "actor_is_moving"
        if not _nonempty_text(arguments["activity"]):
            return "invalid_activity_text"
        if not _positive_number(arguments["duration_seconds"]):
            return "invalid_activity_duration"
        if not _nonnegative_number(arguments["energy_cost"]):
            return "invalid_energy_cost"
        if not _number(arguments["stress_delta"]):
            return "invalid_stress_delta"
        if arguments["duration_band"] not in {"short", "medium", "long"}:
            return "invalid_duration_band"
        if arguments["effort_band"] not in {"low", "medium", "high"}:
            return "invalid_effort_band"
        if arguments["stress_effect"] not in {
            "calming",
            "neutral",
            "activating",
        }:
            return "invalid_stress_effect"
        return _optional_target_failure(arguments["target_id"])

    def duration(self, invocation: EngagementCapabilityInvocation) -> float:
        return _float_value(
            _argument_map(invocation)["duration_seconds"]
        )

    def commit(
        self,
        context: SystemContext,
        actor_id: str,
        program: EngagementProgram,
        group: EngagementInvocationGroup,
        invocation: EngagementCapabilityInvocation,
    ) -> None:
        arguments = _argument_map(invocation)
        energy_cost = _float_value(arguments["energy_cost"])
        stress_delta = _float_value(arguments["stress_delta"])
        configuration = context.registry.get_resource(HomeostasisConfiguration)
        calming = str(arguments["stress_effect"]) == "calming"
        apply_homeostasis_deltas(
            context,
            actor_id,
            source="engagement.bounded_activity",
            deltas={
                "energy": -energy_cost,
                "stress": stress_delta,
                "happiness": (
                    configuration.calming_happiness_delta if calming else 0.0
                ),
                "fear": configuration.calming_fear_delta if calming else 0.0,
            },
        )
        _emit_capability_committed(
            context,
            actor_id,
            program,
            group,
            invocation,
            {
                "modality": "visual",
                "disclosure": "local_visual",
                "activity": str(arguments["activity"]),
                "duration_band": str(arguments["duration_band"]),
                "duration_seconds": _float_value(
                    arguments["duration_seconds"]
                ),
                "effort_band": str(arguments["effort_band"]),
                "energy_cost": energy_cost,
                "stress_effect": str(arguments["stress_effect"]),
                "stress_delta": stress_delta,
                "target_id": _optional_text(arguments["target_id"]),
            },
        )


def build_v1_handler_registry() -> EngagementCapabilityHandlerRegistry:
    registry = EngagementCapabilityHandlerRegistry()
    registry.register(ExpressiveBehaviorHandler())
    registry.register(AuditoryExpressionHandler())
    registry.register(BoundedActivityHandler())
    return registry


@dataclass(frozen=True, slots=True)
class EngagementExecutionSystem:
    handlers: EngagementCapabilityHandlerRegistry
    name: str = "engagement_execution"
    order: int = 245

    def update(self, context: SystemContext) -> None:
        for actor_id in context.registry.query_entities(
            EngagementExecutionComponent,
            PlanComponent,
        ):
            execution = context.registry.get_component(
                actor_id,
                EngagementExecutionComponent,
            )
            if execution.status in {
                EngagementStatus.COMPLETED,
                EngagementStatus.PARTIAL,
                EngagementStatus.FAILED,
                EngagementStatus.CANCELLED,
            }:
                continue
            plan = context.registry.get_component(actor_id, PlanComponent)
            if (
                plan.current is None
                or plan.current.action_id != execution.program.action_id
                or not plan.waiting_for_engagement
            ):
                cancel_engagement_state(
                    context,
                    actor_id,
                    "engagement_action_lost",
                )
                continue
            if (
                context.registry.has_component(actor_id, DriveComponent)
                and context.registry.get_component(
                    actor_id,
                    DriveComponent,
                ).state
                is not System1State.NORMAL
            ):
                cancel_engagement_state(
                    context,
                    actor_id,
                    "system1_preemption",
                )
                continue
            if execution.status is EngagementStatus.PENDING:
                execution.status = EngagementStatus.RUNNING
                execution.started_tick = context.clock.tick
                _emit_engagement(
                    context,
                    "engagement.started",
                    actor_id,
                    execution.program,
                    {
                        "group_count": len(execution.program.groups),
                        "rejected_group_count": len(
                            execution.program.rejected_groups
                        ),
                    },
                )
            self._advance(context, actor_id, execution)

    def _advance(
        self,
        context: SystemContext,
        actor_id: str,
        execution: EngagementExecutionComponent,
    ) -> None:
        while execution.next_group_index < len(execution.program.groups):
            group = execution.program.groups[execution.next_group_index]
            group_state = execution.groups[execution.next_group_index]
            validation = self._validate_group(
                context,
                actor_id,
                group,
            )
            if validation is not None:
                self._fail_group(
                    context,
                    actor_id,
                    execution,
                    group,
                    group_state,
                    validation,
                )
                continue
            durations: list[float] = []
            for invocation in group.invocations:
                handler = self.handlers.handler(invocation.capability)
                if handler is None:
                    raise AssertionError(
                        "validated engagement capability handler disappeared"
                    )
                durations.append(handler.duration(invocation))
            duration = max(durations, default=0.0)
            if duration > 0 and group_state.status is EngagementGroupStatus.PENDING:
                self._start_timed_group(
                    context,
                    actor_id,
                    execution,
                    group,
                    group_state,
                    duration,
                )
                return
            if (
                group_state.status is EngagementGroupStatus.RUNNING
                and execution.active_until is not None
                and context.clock.simulation_time < execution.active_until
            ):
                return
            validation = self._validate_group(
                context,
                actor_id,
                group,
            )
            if validation is not None:
                self._fail_group(
                    context,
                    actor_id,
                    execution,
                    group,
                    group_state,
                    validation,
                )
                continue
            for invocation in group.invocations:
                handler = self.handlers.handler(invocation.capability)
                if handler is None:
                    raise AssertionError(
                        "validated engagement capability handler disappeared"
                    )
                handler.commit(
                    context,
                    actor_id,
                    execution.program,
                    group,
                    invocation,
                )
            if group_state.status is EngagementGroupStatus.RUNNING:
                _restore_activity(
                    context,
                    actor_id,
                    execution,
                    reason="engagement_group_completed",
                )
            group_state.status = EngagementGroupStatus.COMPLETED
            execution.active_group_id = None
            execution.active_until = None
            execution.next_group_index += 1
            _emit_engagement(
                context,
                "engagement.group_completed",
                actor_id,
                execution.program,
                {
                    "group_id": group.group_id,
                    "group_ordinal": group.ordinal,
                    "required_atomic": group.required_atomic,
                    "invocation_ids": [
                        invocation.invocation_id
                        for invocation in group.invocations
                    ],
                },
            )
        self._finish(context, actor_id, execution)

    def _validate_group(
        self,
        context: SystemContext,
        actor_id: str,
        group: EngagementInvocationGroup,
    ) -> str | None:
        bounded_count = sum(
            invocation.capability == BOUNDED_ACTIVITY
            for invocation in group.invocations
        )
        if bounded_count > 1:
            return "multiple_bounded_activities"
        for invocation in group.invocations:
            handler = self.handlers.handler(invocation.capability)
            if handler is None:
                return f"unsupported_capability:{invocation.capability}"
            failure = handler.validate(context, actor_id, invocation)
            if failure is not None:
                return f"{invocation.invocation_id}:{failure}"
        return None

    @staticmethod
    def _start_timed_group(
        context: SystemContext,
        actor_id: str,
        execution: EngagementExecutionComponent,
        group: EngagementInvocationGroup,
        group_state: EngagementGroupExecution,
        duration: float,
    ) -> None:
        activity = context.registry.get_component(actor_id, ActivityComponent)
        execution.previous_activity = activity.current
        previous = activity.current
        activity.current = ActivityType.ENGAGING
        activity.previous = None
        activity.movement_override = False
        execution.active_group_id = group.group_id
        execution.active_until = round(
            context.clock.simulation_time + duration,
            12,
        )
        group_state.status = EngagementGroupStatus.RUNNING
        if previous is not activity.current:
            context.events.emit(
                "activity.changed",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=actor_id,
                payload={
                    "previous": previous.value,
                    "current": activity.current.value,
                    "reason": "engagement_group_started",
                    **_engagement_lineage(execution.program),
                },
                correlation_id=execution.program.root_correlation_id,
            )

    @staticmethod
    def _fail_group(
        context: SystemContext,
        actor_id: str,
        execution: EngagementExecutionComponent,
        group: EngagementInvocationGroup,
        group_state: EngagementGroupExecution,
        reason: str,
    ) -> None:
        if group_state.status is EngagementGroupStatus.RUNNING:
            _restore_activity(
                context,
                actor_id,
                execution,
                reason="engagement_group_failed",
            )
        group_state.status = EngagementGroupStatus.FAILED
        group_state.failure_reason = reason
        execution.active_group_id = None
        execution.active_until = None
        execution.next_group_index += 1
        _emit_engagement(
            context,
            "engagement.group_failed",
            actor_id,
            execution.program,
            {
                "group_id": group.group_id,
                "group_ordinal": group.ordinal,
                "required_atomic": group.required_atomic,
                "invocation_ids": [
                    invocation.invocation_id
                    for invocation in group.invocations
                ],
                "reason": reason,
            },
        )

    @staticmethod
    def _finish(
        context: SystemContext,
        actor_id: str,
        execution: EngagementExecutionComponent,
    ) -> None:
        completed = sum(
            group.status is EngagementGroupStatus.COMPLETED
            for group in execution.groups
        )
        failed = sum(
            group.status is EngagementGroupStatus.FAILED
            for group in execution.groups
        )
        if completed == len(execution.groups):
            execution.status = EngagementStatus.COMPLETED
            event_type = "engagement.completed"
        elif completed:
            execution.status = EngagementStatus.PARTIAL
            event_type = "engagement.partial"
        else:
            execution.status = EngagementStatus.FAILED
            execution.failure_reason = "no_groups_committed"
            event_type = "engagement.failed"
        _emit_engagement(
            context,
            event_type,
            actor_id,
            execution.program,
            {
                "completed_group_count": completed,
                "failed_group_count": failed,
                "group_statuses": [
                    {
                        "group_id": group.group_id,
                        "status": group.status.value,
                        "failure_reason": group.failure_reason,
                    }
                    for group in execution.groups
                ],
            },
        )
        if context.registry.has_component(actor_id, ControllerComponent):
            context.registry.get_component(
                actor_id,
                ControllerComponent,
            ).last_outcome = f"engagement {execution.status.value}"


def cancel_engagement_state(
    context: SystemContext,
    actor_id: str,
    reason: str,
) -> bool:
    lineage = _current_lineage(context, actor_id)
    pending = (
        context.registry.get_component(actor_id, PendingEngagementComponent)
        if context.registry.has_component(actor_id, PendingEngagementComponent)
        else None
    )
    execution = (
        context.registry.get_component(actor_id, EngagementExecutionComponent)
        if context.registry.has_component(actor_id, EngagementExecutionComponent)
        else None
    )
    if lineage is None:
        return False
    group_statuses: list[JsonValue] = []
    if execution is not None:
        execution.status = EngagementStatus.CANCELLED
        execution.failure_reason = reason
        for group in execution.groups:
            if group.status in {
                EngagementGroupStatus.PENDING,
                EngagementGroupStatus.RUNNING,
            }:
                group.status = EngagementGroupStatus.CANCELLED
                group.failure_reason = reason
        group_statuses = [
            {
                "group_id": group.group_id,
                "group_ordinal": execution.program.groups[index].ordinal,
                "required_atomic": (
                    execution.program.groups[index].required_atomic
                ),
                "invocation_ids": [
                    invocation.invocation_id
                    for invocation in execution.program.groups[
                        index
                    ].invocations
                ],
                "status": group.status.value,
                "failure_reason": group.failure_reason,
            }
            for index, group in enumerate(execution.groups)
        ]
        _restore_activity(
            context,
            actor_id,
            execution,
            reason="engagement_cancelled",
        )
    if pending is not None:
        context.events.emit(
            "engagement.compilation_cancelled",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=actor_id,
            payload={
                **lineage,
                "reason": reason,
                "visibility": "private",
            },
            correlation_id=str(lineage["root_correlation_id"]),
        )
    context.events.emit(
        "engagement.cancelled",
        simulation_tick=context.clock.tick,
        simulation_time=context.clock.simulation_time,
        agent_id=actor_id,
        payload={
            **lineage,
            "reason": reason,
            "group_statuses": group_statuses,
        },
        correlation_id=str(lineage["root_correlation_id"]),
    )
    remove_engagement_state(context, actor_id)
    if context.registry.has_component(actor_id, ControllerComponent):
        context.registry.get_component(
            actor_id,
            ControllerComponent,
        ).last_outcome = f"engagement cancelled: {reason}"
    return True


def remove_engagement_state(context: SystemContext, actor_id: str) -> None:
    if context.registry.has_component(actor_id, EngagementExecutionComponent):
        execution = context.registry.get_component(
            actor_id,
            EngagementExecutionComponent,
        )
        _restore_activity(
            context,
            actor_id,
            execution,
            reason="engagement_state_removed",
        )
    for component_type in (
        PendingEngagementComponent,
        EngagementProgramComponent,
        EngagementExecutionComponent,
    ):
        if context.registry.has_component(actor_id, component_type):
            context.registry.remove_component(actor_id, component_type)


def _restore_activity(
    context: SystemContext,
    actor_id: str,
    execution: EngagementExecutionComponent,
    *,
    reason: str,
) -> None:
    if (
        execution.previous_activity is None
        or not context.registry.has_component(actor_id, ActivityComponent)
    ):
        return
    activity = context.registry.get_component(actor_id, ActivityComponent)
    previous = activity.current
    activity.current = execution.previous_activity
    activity.previous = None
    activity.movement_override = False
    execution.previous_activity = None
    if previous is not activity.current:
        context.events.emit(
            "activity.changed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=actor_id,
            payload={
                "previous": previous.value,
                "current": activity.current.value,
                "reason": reason,
                **_engagement_lineage(execution.program),
            },
            correlation_id=execution.program.root_correlation_id,
        )


def _current_lineage(
    context: SystemContext,
    actor_id: str,
) -> dict[str, JsonValue] | None:
    if context.registry.has_component(actor_id, EngagementExecutionComponent):
        return _engagement_lineage(
            context.registry.get_component(
                actor_id,
                EngagementExecutionComponent,
            ).program
        )
    if context.registry.has_component(actor_id, EngagementProgramComponent):
        return _engagement_lineage(
            context.registry.get_component(
                actor_id,
                EngagementProgramComponent,
            ).program
        )
    if context.registry.has_component(actor_id, PendingEngagementComponent):
        pending = context.registry.get_component(
            actor_id,
            PendingEngagementComponent,
        )
        return {
            "engagement_id": pending.engagement_id,
            "action_id": pending.action_id,
            "plan_id": pending.plan_id,
            "plan_revision": pending.plan_revision,
            "decision_id": pending.decision_id,
            "tool_call_id": pending.tool_call_id,
            "root_correlation_id": pending.root_correlation_id,
        }
    return None


def _engagement_lineage(program: EngagementProgram) -> dict[str, JsonValue]:
    return {
        "engagement_id": program.engagement_id,
        "action_id": program.action_id,
        "plan_id": program.plan_id,
        "plan_revision": program.plan_revision,
        "decision_id": program.decision_id,
        "tool_call_id": program.tool_call_id,
        "root_correlation_id": program.root_correlation_id,
    }


def _emit_engagement(
    context: SystemContext,
    event_type: str,
    actor_id: str,
    program: EngagementProgram,
    extra: Mapping[str, JsonValue] | None = None,
) -> None:
    context.events.emit(
        event_type,
        simulation_tick=context.clock.tick,
        simulation_time=context.clock.simulation_time,
        agent_id=actor_id,
        payload={
            **_engagement_lineage(program),
            **dict(extra or {}),
        },
        correlation_id=program.root_correlation_id,
    )


def _emit_capability_committed(
    context: SystemContext,
    actor_id: str,
    program: EngagementProgram,
    group: EngagementInvocationGroup,
    invocation: EngagementCapabilityInvocation,
    evidence: Mapping[str, JsonValue],
) -> None:
    _emit_engagement(
        context,
        "engagement.capability_committed",
        actor_id,
        program,
        {
            "group_id": group.group_id,
            "group_ordinal": group.ordinal,
            "required_atomic": group.required_atomic,
            "invocation_id": invocation.invocation_id,
            "invocation_ordinal": next(
                index
                for index, candidate in enumerate(group.invocations)
                if candidate.invocation_id == invocation.invocation_id
            ),
            "capability": invocation.capability,
            "consequence_tier": invocation.consequence_tier,
            "visibility": "private",
            **dict(evidence),
        },
    )


def _argument_map(
    invocation: EngagementCapabilityInvocation,
) -> dict[str, EngagementScalar]:
    return {
        argument.name: argument.value
        for argument in invocation.arguments
    }


def _optional_target_failure(value: object) -> str | None:
    if value is None or _nonempty_text(value):
        return None
    return "invalid_target_reference"


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _positive_number(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and value > 0
    )


def _nonnegative_number(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and value >= 0
    )


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0




def _float_value(value: EngagementScalar) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("engagement numeric argument is invalid")
    return float(value)


def _int_value(value: EngagementScalar) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("engagement integer argument is invalid")
    return value
