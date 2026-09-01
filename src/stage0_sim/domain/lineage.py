from typing import TYPE_CHECKING

from stage0_sim.domain.components.goals import GoalComponent, GoalStatus
from stage0_sim.domain.components.planning import (
    ActionGoalLink,
    ActionInstance,
    ActionOrigin,
    GoalLinkKind,
    LineageIdGenerator,
    PlanAction,
    PlanComponent,
)
from stage0_sim.domain.events import DomainEvent, JsonValue

if TYPE_CHECKING:
    from stage0_sim.domain.systems import SystemContext


def active_goal_links(
    context: "SystemContext",
    agent_id: str,
    *,
    kind: GoalLinkKind = GoalLinkKind.CONTEXTUAL,
) -> tuple[ActionGoalLink, ...]:
    if not context.registry.has_component(agent_id, GoalComponent):
        return ()
    return tuple(
        ActionGoalLink(goal.definition.id, kind)
        for goal in context.registry.get_component(agent_id, GoalComponent).goals
        if goal.status in {GoalStatus.PENDING, GoalStatus.ACTIVE}
    )


def new_action_instance(
    context: "SystemContext",
    agent_id: str,
    *,
    origin: ActionOrigin,
    specification: PlanAction | None = None,
    action_name: str = "",
    target_id: str | None = None,
    plan_id: str | None = None,
    plan_revision: int | None = None,
    goal_links: tuple[ActionGoalLink, ...] = (),
    decision_id: str | None = None,
    tool_call_id: str | None = None,
    root_correlation_id: str | None = None,
) -> ActionInstance:
    generator = _generator(context)
    action_id = generator.new_action_id()
    return ActionInstance(
        action_id=action_id,
        origin=origin,
        created_tick=context.clock.tick,
        created_at=context.clock.simulation_time,
        root_correlation_id=root_correlation_id or plan_id or action_id,
        specification=specification,
        action_name=action_name,
        target_id=target_id,
        plan_id=plan_id,
        plan_revision=plan_revision,
        goal_links=goal_links,
        decision_id=decision_id,
        tool_call_id=tool_call_id,
    )


def new_operator_intervention_id(context: "SystemContext") -> str:
    return _generator(context).new_intervention_id()


def queue_plan_actions(
    context: "SystemContext",
    agent_id: str,
    plan: PlanComponent,
    actions: tuple[PlanAction, ...] | list[PlanAction],
    *,
    origin: ActionOrigin,
    goal_links: tuple[ActionGoalLink, ...] = (),
    decision_id: str | None = None,
    tool_call_id: str | None = None,
    root_correlation_id: str | None = None,
    causation_id: str | None = None,
) -> tuple[ActionInstance, ...]:
    if not actions:
        return ()
    generator = _generator(context)
    if plan.plan_id is None:
        plan.plan_id = generator.new_plan_id()
        plan.plan_revision = 1
        plan.origin = origin
        plan.root_correlation_id = root_correlation_id or plan.plan_id
        _emit_plan(
            context,
            "plan.created",
            agent_id,
            plan,
            causation_id=causation_id,
        )
    else:
        previous_revision = plan.plan_revision
        plan.plan_revision += 1
        plan.origin = origin
        if root_correlation_id is not None:
            plan.root_correlation_id = root_correlation_id
        event = _emit_plan(
            context,
            "plan.revised",
            agent_id,
            plan,
            {"previous_revision": previous_revision},
            causation_id=causation_id,
        )
        causation_id = event.event_id
    instances = tuple(
        new_action_instance(
            context,
            agent_id,
            origin=origin,
            specification=action,
            plan_id=plan.plan_id,
            plan_revision=plan.plan_revision,
            goal_links=goal_links,
            decision_id=decision_id,
            tool_call_id=tool_call_id,
            root_correlation_id=plan.root_correlation_id,
        )
        for action in actions
    )
    plan.queue.extend(instances)
    for instance in instances:
        emit_action_lifecycle(
            context,
            "action.queued",
            agent_id,
            instance,
            causation_id=causation_id,
        )
    return instances


def materialize_legacy_plan(
    context: "SystemContext",
    agent_id: str,
    plan: PlanComponent,
) -> None:
    legacy_current = (
        plan.current if isinstance(plan.current, PlanAction) else None
    )
    legacy_queue = [
        action for action in plan.queue if isinstance(action, PlanAction)
    ]
    if legacy_current is None and not legacy_queue:
        return
    generator = _generator(context)
    if plan.plan_id is None:
        plan.plan_id = generator.new_plan_id()
        plan.plan_revision = 1
        plan.origin = ActionOrigin.SCENARIO
        plan.root_correlation_id = plan.plan_id
        _emit_plan(context, "plan.created", agent_id, plan)
    elif plan.plan_revision < 1:
        plan.plan_revision = 1
    links = active_goal_links(context, agent_id)

    def materialize(action: PlanAction) -> ActionInstance:
        instance = new_action_instance(
            context,
            agent_id,
            origin=plan.origin or ActionOrigin.SCENARIO,
            specification=action,
            plan_id=plan.plan_id,
            plan_revision=plan.plan_revision,
            goal_links=links,
            root_correlation_id=plan.root_correlation_id,
        )
        emit_action_lifecycle(context, "action.queued", agent_id, instance)
        return instance

    if legacy_current is not None:
        plan.current = materialize(legacy_current)
    plan.queue = [
        materialize(action) if isinstance(action, PlanAction) else action
        for action in plan.queue
    ]


def clear_plan_lineage(
    context: "SystemContext",
    agent_id: str,
    plan: PlanComponent,
    *,
    reason: str,
    current_status: str = "action.cancelled",
) -> int:
    materialize_legacy_plan(context, agent_id, plan)
    plan_id = plan.plan_id
    plan_revision = plan.plan_revision
    plan_origin = plan.origin
    root_correlation_id = plan.root_correlation_id
    current = plan.current
    queued = tuple(plan.queue)
    if isinstance(current, ActionInstance):
        emit_action_lifecycle(
            context,
            current_status,
            agent_id,
            current,
            {"reason": reason},
        )
    for action in queued:
        if isinstance(action, ActionInstance):
            emit_action_lifecycle(
                context,
                "action.cancelled",
                agent_id,
                action,
                {"reason": reason, "started": False},
            )
    cleared_count = plan.clear()
    if cleared_count:
        context.events.emit(
            "plan.cleared",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "reason": reason,
                "cleared_actions": cleared_count,
                "plan_id": plan_id,
                "plan_revision": plan_revision,
                "origin": plan_origin.value if plan_origin is not None else None,
            },
            correlation_id=root_correlation_id,
        )
    return cleared_count


def action_lineage_payload(action: ActionInstance | None) -> dict[str, JsonValue]:
    if action is None:
        return {}
    return {
        "action_id": action.action_id,
        "action_origin": action.origin.value,
        "plan_id": action.plan_id,
        "plan_revision": action.plan_revision,
        "goal_ids": list(action.goal_ids),
        "goal_links": [
            {"goal_id": link.goal_id, "kind": link.kind.value}
            for link in action.goal_links
        ],
        "decision_id": action.decision_id,
        "tool_call_id": action.tool_call_id,
        "action_created_tick": action.created_tick,
        "action_created_at": action.created_at,
        "root_correlation_id": action.root_correlation_id,
    }


def action_specification_payload(action: ActionInstance) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {"action": action.action_name}
    if action.target is not None:
        payload["target"] = action.target
    if action.duration is not None:
        payload["duration"] = action.duration
    if action.mode is not None:
        payload["mode"] = action.mode.value
    if action.offer_id is not None:
        payload["offer_id"] = action.offer_id
    return payload


def emit_action_lifecycle(
    context: "SystemContext",
    event_type: str,
    agent_id: str,
    action: ActionInstance,
    extra: dict[str, JsonValue] | None = None,
    *,
    causation_id: str | None = None,
) -> DomainEvent:
    payload = {
        **action_specification_payload(action),
        **action_lineage_payload(action),
        **(extra or {}),
    }
    return context.events.emit(
        event_type,
        simulation_tick=context.clock.tick,
        simulation_time=context.clock.simulation_time,
        agent_id=agent_id,
        payload=payload,
        causation_id=causation_id,
        correlation_id=action.root_correlation_id,
    )


def _emit_plan(
    context: "SystemContext",
    event_type: str,
    agent_id: str,
    plan: PlanComponent,
    extra: dict[str, JsonValue] | None = None,
    *,
    causation_id: str | None = None,
) -> DomainEvent:
    return context.events.emit(
        event_type,
        simulation_tick=context.clock.tick,
        simulation_time=context.clock.simulation_time,
        agent_id=agent_id,
        payload={
            "plan_id": plan.plan_id,
            "plan_revision": plan.plan_revision,
            "origin": plan.origin.value if plan.origin is not None else None,
            "root_correlation_id": plan.root_correlation_id,
            **(extra or {}),
        },
        causation_id=causation_id,
        correlation_id=plan.root_correlation_id,
    )


def _generator(context: "SystemContext") -> LineageIdGenerator:
    if not context.registry.has_resource(LineageIdGenerator):
        context.registry.set_resource(LineageIdGenerator())
    return context.registry.get_resource(LineageIdGenerator)
