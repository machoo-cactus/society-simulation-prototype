from dataclasses import dataclass, replace

from stage0_sim.domain.components import (
    ActionInstance,
    ActionOrigin,
    ActionType,
    ActivityComponent,
    ActivityType,
    AffordanceExecutionComponent,
    AffordanceRequestComponent,
    CharacterPosture,
    CharacterPostureComponent,
    ConversationComponent,
    DriveComponent,
    EngagementExecutionComponent,
    EngagementProgramComponent,
    EngagementStatus,
    HomeostasisComponent,
    HomeostasisConfiguration,
    InteractionExecutionComponent,
    InteractionRequestComponent,
    MovementComponent,
    NavigationComponent,
    NavigationPrimitive,
    NavigationPrimitiveKind,
    NavigationStatus,
    NpcComponent,
    OpenableComponent,
    PhysicalStateComponent,
    PlanComponent,
    PositionComponent,
    PossessionsComponent,
    SpatialIndex,
    SpatialIndexEntry,
    SpatialLocationComponent,
    System1State,
    TextActionExecutionComponent,
    TextActionRequestComponent,
    TransactionExecutionComponent,
    TransactionRequestComponent,
    TravelComponent,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.environment import EnvironmentAvailabilityRegistry
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.lineage import (
    ActionLifecycleEvent,
    action_lineage_payload,
    clear_plan_lineage,
    emit_action_lifecycle,
)
from stage0_sim.domain.npcs import NpcPoolRegistry
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.interactions import (
    complete_drink,
    execute_navigation_interaction,
    is_at_interaction_approach,
    physical_activity_failure,
    sync_held_object_poses,
)
from stage0_sim.domain.systems.spatial_context import (
    local_world_for_agent,
    local_world_for_space,
    shares_local_map,
)
from stage0_sim.domain.systems.travel import TravelSystem
from stage0_sim.domain.world import (
    Coordinate,
    Locator,
    SpaceRegistry,
    SpatialScale,
    TravelMode,
    TravelStatus,
    WorldLocation,
    WorldMap,
)


def is_dialogue_capable(registry: Registry, entity_id: str) -> bool:
    if entity_id not in registry.entities():
        return False
    return registry.has_component(
        entity_id, ConversationComponent
    ) and registry.has_component(entity_id, DriveComponent)


def fail_social_action(
    context: SystemContext,
    agent_id: str,
    target_id: str,
    reason: str,
) -> None:
    if (
        not context.registry.has_component(agent_id, PlanComponent)
        or not context.registry.has_component(agent_id, ActivityComponent)
    ):
        return
    plan = context.registry.get_component(agent_id, PlanComponent)
    action = plan.current
    if (
        action is None
        or action.action is not ActionType.SOCIALIZE
        or action.target != target_id
    ):
        return
    activity = context.registry.get_component(agent_id, ActivityComponent)
    previous_activity = activity.current
    activity.current = plan.previous_activity or ActivityType.IDLE
    if previous_activity is not activity.current:
        context.events.emit(
            "activity.changed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "previous": previous_activity.value,
                "current": activity.current.value,
                "reason": "plan_action_failed",
            },
        )
    fail_plan_action(context, agent_id, plan, reason)


def reset_current_plan_action(plan: PlanComponent) -> None:
    plan.current = None
    plan.remaining_duration = None
    plan.previous_activity = None
    plan.waiting_for_affordance = False
    plan.waiting_for_transaction = False
    plan.waiting_for_interaction = False
    plan.waiting_for_engagement = False
    plan.waiting_for_text = False
    plan.current_started = False


def finish_plan(
    context: SystemContext,
    agent_id: str,
    plan: PlanComponent,
    reason: str,
) -> None:
    if plan.plan_id is None:
        return
    context.events.emit(
        "plan.cleared",
        simulation_tick=context.clock.tick,
        simulation_time=context.clock.simulation_time,
        agent_id=agent_id,
        payload={
            "reason": reason,
            "cleared_actions": 0,
            "plan_id": plan.plan_id,
            "plan_revision": plan.plan_revision,
            "origin": plan.origin.value if plan.origin is not None else None,
        },
        correlation_id=plan.root_correlation_id,
    )
    plan.plan_id = None
    plan.plan_revision = 0
    plan.origin = None
    plan.root_correlation_id = None


def complete_plan_action(
    context: SystemContext,
    agent_id: str,
    plan: PlanComponent,
) -> None:
    action = plan.current
    if action is not None:
        emit_action_lifecycle(context, "action.completed", agent_id, action)
        if action.action is ActionType.ENGAGE:
            from stage0_sim.domain.systems.engagements import (
                remove_engagement_state,
            )

            remove_engagement_state(context, agent_id)
    reset_current_plan_action(plan)
    if not plan.queue:
        finish_plan(context, agent_id, plan, "completed")


def fail_plan_action(
    context: SystemContext,
    agent_id: str,
    plan: PlanComponent,
    reason: str,
) -> None:
    action = plan.current
    if action is not None:
        emit_action_lifecycle(
            context,
            "action.failed",
            agent_id,
            action,
            {"reason": reason},
        )
        if action.action is ActionType.ENGAGE:
            from stage0_sim.domain.systems.engagements import (
                remove_engagement_state,
            )

            remove_engagement_state(context, agent_id)
    reset_current_plan_action(plan)
    clear_plan_lineage(
        context,
        agent_id,
        plan,
        reason=reason,
        current_status="action.cancelled",
    )


def interrupt_plan_action(
    context: SystemContext,
    agent_id: str,
    plan: PlanComponent,
    reason: str,
) -> None:
    action = plan.current
    if action is not None:
        emit_action_lifecycle(
            context,
            "action.cancelled",
            agent_id,
            action,
            {"reason": reason},
        )
        if action.action is ActionType.ENGAGE:
            from stage0_sim.domain.systems.engagements import (
                remove_engagement_state,
            )

            remove_engagement_state(context, agent_id)
    reset_current_plan_action(plan)
    clear_plan_lineage(
        context,
        agent_id,
        plan,
        reason=reason,
        current_status="action.cancelled",
    )


@dataclass(frozen=True, slots=True)
class PlanExecutionSystem:
    name: str = "plan_execution"
    order: int = 90

    def update(self, context: SystemContext) -> None:
        if not context.registry.has_resource(WorldMap):
            return
        for agent_id in context.registry.query_entities(
            PlanComponent,
            DriveComponent,
            PositionComponent,
            MovementComponent,
            ActivityComponent,
        ):
            drive = context.registry.get_component(agent_id, DriveComponent)
            plan = context.registry.get_component(agent_id, PlanComponent)
            system1_navigation = (
                plan.current is not None
                and plan.current.origin is ActionOrigin.SYSTEM1
                and plan.current.action is ActionType.NAVIGATE
            )
            if (
                drive.state is not System1State.NORMAL
                and not system1_navigation
            ):
                continue
            if plan.current is not None:
                if plan.current.action is ActionType.NAVIGATE:
                    self._advance_navigation(context, agent_id, plan)
                    if plan.current is not None:
                        continue
                    continue
                if plan.waiting_for_affordance:
                    self._check_affordance(context, agent_id, plan)
                elif plan.waiting_for_transaction:
                    self._check_transaction(context, agent_id, plan)
                elif plan.waiting_for_interaction:
                    self._check_interaction(context, agent_id, plan)
                elif plan.waiting_for_engagement:
                    self._check_engagement(context, agent_id, plan)
                elif plan.waiting_for_text:
                    self._check_text(context, agent_id, plan)
                return_if_active = plan.current is not None
                if return_if_active:
                    continue

            if not plan.queue:
                continue
            plan.current = plan.queue.pop(0)
            self._start(
                context,
                agent_id,
                plan,
                local_world_for_agent(context.registry, agent_id),
            )

    def _start(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
        world: WorldMap | None,
    ) -> None:
        action = plan.current
        if action is None:
            return
        if action.action is ActionType.SOCIALIZE and (
            action.target is None
            or action.target == agent_id
            or not is_dialogue_capable(context.registry, action.target)
        ):
            self._fail(context, agent_id, plan, "invalid_social_target")
            return
        plan.current_started = True
        self._emit_action(context, "action.started", agent_id, action)

        if action.action is ActionType.ENGAGE:
            if (
                action.engagement is None
                or not context.registry.has_component(
                    agent_id,
                    EngagementProgramComponent,
                )
                or context.registry.has_component(
                    agent_id,
                    EngagementExecutionComponent,
                )
            ):
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "engagement_program_unavailable",
                )
                return
            program_component = context.registry.get_component(
                agent_id,
                EngagementProgramComponent,
            )
            if (
                program_component.program.engagement_id
                != action.engagement.engagement_id
                or program_component.program.action_id != action.action_id
            ):
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "engagement_program_mismatch",
                )
                return
            context.registry.add_component(
                agent_id,
                EngagementExecutionComponent(program_component.program),
            )
            plan.waiting_for_engagement = True
            return

        if action.action is ActionType.NAVIGATE:
            if (
                context.registry.has_component(
                    agent_id,
                    CharacterPostureComponent,
                )
                and context.registry.get_component(
                    agent_id,
                    CharacterPostureComponent,
                ).posture
                is not CharacterPosture.STANDING
            ):
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "posture_invalid",
                )
                return
            if not context.registry.has_component(
                agent_id,
                NavigationComponent,
            ):
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "navigation_component_missing",
                )
                return

            navigation = context.registry.get_component(
                agent_id,
                NavigationComponent,
            )
            if action.target is None:
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "navigation_target_missing",
                )
                return
            matching_active_request = (
                navigation.target_id == action.target
                and navigation.preferred_mode is action.mode
                and navigation.status
                in {
                    NavigationStatus.REQUESTED,
                    NavigationStatus.PLANNED,
                    NavigationStatus.FAILED,
                }
            )
            if not matching_active_request:
                navigation.request(
                    action.target,
                    preferred_mode=action.mode,
                    action_instance=action,
                )
            else:
                navigation.action_instance = action
            if navigation.status is NavigationStatus.REQUESTED:
                return
            if navigation.status is NavigationStatus.FAILED:
                self._fail(
                    context,
                    agent_id,
                    plan,
                    navigation.failure_reason or "navigation_planning_failed",
                )
                return
            if navigation.status is not NavigationStatus.PLANNED:
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "navigation_not_planned",
                )
                return
            navigation.status = NavigationStatus.NAVIGATING
            self._advance_navigation(context, agent_id, plan)
            return

        if action.action is ActionType.INTERACT:
            if (
                action.interaction is None
                or context.registry.has_component(
                    agent_id,
                    InteractionRequestComponent,
                )
            ):
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "interaction_precondition_failed",
                )
                return
            context.registry.add_component(
                agent_id,
                InteractionRequestComponent(
                    specification=action.interaction,
                    source="plan",
                    action_instance=action,
                ),
            )
            plan.waiting_for_interaction = True
            return

        if action.action in {
            ActionType.READ_TEXT,
            ActionType.WRITE_TEXT,
        }:
            if context.registry.has_component(
                agent_id, TextActionRequestComponent
            ):
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "text_action_precondition_failed",
                )
                return
            if action.action is ActionType.READ_TEXT:
                if action.text_read is None:
                    self._fail(
                        context,
                        agent_id,
                        plan,
                        "text_read_specification_missing",
                    )
                    return
                text_request = TextActionRequestComponent(
                    read=action.text_read,
                    action_instance=action,
                )
            else:
                if action.text_write is None:
                    self._fail(
                        context,
                        agent_id,
                        plan,
                        "text_write_specification_missing",
                    )
                    return
                text_request = TextActionRequestComponent(
                    write=action.text_write,
                    action_instance=action,
                )
            context.registry.add_component(agent_id, text_request)
            plan.waiting_for_text = True
            return

        if action.action is ActionType.TRANSACT:
            if (
                action.target is None
                or action.offer_id is None
                or world is None
                or not context.registry.has_component(
                    agent_id, PossessionsComponent
                )
            ):
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "transaction_precondition_failed",
                )
                return
            try:
                point = world.transaction_point(action.target)
                point.offer(action.offer_id)
            except KeyError:
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "transaction_precondition_failed",
                )
                return
            if (
                not is_at_interaction_approach(
                    context.registry,
                    agent_id,
                    point.id,
                    fallback=point.position,
                )
            ):
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "transaction_precondition_failed",
                )
                return
            context.registry.add_component(
                agent_id,
                TransactionRequestComponent(
                    point_id=point.id,
                    offer_id=action.offer_id,
                    source="plan",
                    action_instance=action,
                ),
            )
            plan.waiting_for_transaction = True
            return

        if action.action is ActionType.SERVE_TRANSACTION:
            if (
                action.target is None
                or not context.registry.has_component(
                    agent_id, NpcComponent
                )
                or not context.registry.has_resource(NpcPoolRegistry)
            ):
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "service_precondition_failed",
                )
                return
            matched = next(
                (
                    (customer_id, request)
                    for customer_id, request in context.registry.query(
                        TransactionRequestComponent
                    )
                    if request.request_id == action.target
                ),
                None,
            )
            if matched is None:
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "transaction_request_not_found",
                )
                return
            customer_id, request = matched
            npc = context.registry.get_component(agent_id, NpcComponent)
            if (
                request.status != "awaiting_authorization"
                or request.operator_id != agent_id
                or npc.staffed_point_id != request.point_id
                or not shares_local_map(
                    context.registry, agent_id, customer_id
                )
            ):
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "transaction_request_not_serviceable",
                )
                return
            staffing = context.registry.get_resource(
                NpcPoolRegistry
            ).staffing(request.point_id).assignment
            if (
                context.registry.get_component(
                    agent_id, PositionComponent
                ).coordinate
                != staffing.staff_position
            ):
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "operator_not_at_staff_position",
                )
                return
            customer_world = local_world_for_agent(
                context.registry, customer_id
            )
            if customer_world is None:
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "transaction_point_not_found",
                )
                return
            try:
                point = customer_world.transaction_point(request.point_id)
            except KeyError:
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "transaction_point_not_found",
                )
                return
            if (
                not is_at_interaction_approach(
                    context.registry,
                    customer_id,
                    point.id,
                    fallback=point.position,
                )
            ):
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "customer_not_at_transaction_point",
                )
                return
            request.authorized_by = agent_id
            request.status = "authorized"
            context.events.emit(
                "transaction.authorized",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=customer_id,
                payload={
                    "request_id": request.request_id,
                    "point_id": request.point_id,
                    "offer_id": request.offer_id,
                    "operator_id": agent_id,
                    **action_lineage_payload(action),
                },
                correlation_id=action.root_correlation_id,
            )
            self._complete(context, agent_id, plan)
            return

        if action.action in {
            ActionType.WORK,
            ActionType.SOCIALIZE,
            ActionType.READ,
            ActionType.DRINK,
            ActionType.IDLE,
        }:
            if (
                action.action in {ActionType.READ, ActionType.DRINK}
                and action.target is None
            ):
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "activity_target_required",
                )
                return
            if (
                action.action
                in {ActionType.WORK, ActionType.READ, ActionType.DRINK}
                and action.target is not None
                and (
                    world is None
                    or not self._activity_target_valid(
                        context, agent_id, world, action
                    )
                )
            ):
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "activity_precondition_failed",
                )
                return
            if action.duration is None:
                self._fail(context, agent_id, plan, "duration_required")
                return
            activity = context.registry.get_component(agent_id, ActivityComponent)
            plan.previous_activity = activity.current
            previous_activity = activity.current
            activity.current = (
                ActivityType.WORKING
                if action.action is ActionType.WORK
                else ActivityType.DRINKING
                if action.action is ActionType.DRINK
                else ActivityType.IDLE
            )
            activity.previous = None
            activity.movement_override = False
            plan.remaining_duration = action.duration
            if previous_activity is not activity.current:
                context.events.emit(
                    "activity.changed",
                    simulation_tick=context.clock.tick,
                    simulation_time=context.clock.simulation_time,
                    agent_id=agent_id,
                    payload={
                        "previous": previous_activity.value,
                        "current": activity.current.value,
                        "reason": "plan_action_started",
                        **action_lineage_payload(action),
                    },
                    correlation_id=action.root_correlation_id,
                )
            return

        if world is None:
            self._fail(
                context,
                agent_id,
                plan,
                "local_space_unavailable",
            )
            return
        station_id = self._resolve_affordance_station(
            context, agent_id, world, action
        )
        if station_id is None:
            self._fail(context, agent_id, plan, "affordance_precondition_failed")
            return
        context.registry.add_component(
            agent_id,
            AffordanceRequestComponent(
                station_id=station_id,
                action=action.action.value,
                source="plan",
                action_instance=action,
            ),
        )
        plan.waiting_for_affordance = True

    def _advance_navigation(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
    ) -> None:
        if not context.registry.has_component(agent_id, NavigationComponent):
            self._fail(
                context,
                agent_id,
                plan,
                "navigation_component_missing",
            )
            return
        navigation = context.registry.get_component(
            agent_id,
            NavigationComponent,
        )
        if navigation.status is NavigationStatus.FAILED:
            self._fail(
                context,
                agent_id,
                plan,
                navigation.failure_reason or "navigation_planning_failed",
            )
            return
        if navigation.status not in {
            NavigationStatus.PLANNED,
            NavigationStatus.NAVIGATING,
        }:
            return
        navigation.status = NavigationStatus.NAVIGATING
        while navigation.current_primitive_index < len(
            navigation.primitives
        ):
            primitive = navigation.primitives[
                navigation.current_primitive_index
            ]
            if primitive.kind is NavigationPrimitiveKind.INTERACT:
                if self._advance_navigation_interaction(
                    context,
                    agent_id,
                    plan,
                    navigation,
                    primitive,
                ):
                    continue
                return
            if primitive.kind is NavigationPrimitiveKind.MOVE:
                if self._advance_navigation_move(
                    context,
                    agent_id,
                    plan,
                    navigation,
                    primitive,
                ):
                    continue
                return
            if primitive.kind is NavigationPrimitiveKind.TRANSITION:
                if self._advance_navigation_transition(
                    context,
                    agent_id,
                    plan,
                    navigation,
                    primitive,
                ):
                    continue
                return
            if self._advance_navigation_travel(
                context,
                agent_id,
                plan,
                navigation,
                primitive,
            ):
                continue
            return
        navigation.status = NavigationStatus.ARRIVED
        context.events.emit(
            "navigation.arrived",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "target_id": navigation.target_id,
                "destination": (
                    {
                        "space_id": navigation.route.destination.space_id,
                        "local_reference": (
                            navigation.route.destination.local_reference
                        ),
                    }
                    if navigation.route is not None
                    else None
                ),
                "completed_route_legs": navigation.completed_route_legs,
                **action_lineage_payload(navigation.action_instance),
            },
            correlation_id=navigation.correlation_id,
        )
        self._complete(context, agent_id, plan)

    def _advance_navigation_interaction(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
        navigation: NavigationComponent,
        primitive: NavigationPrimitive,
    ) -> bool:
        if primitive.interaction is None:
            self._fail_navigation(
                context,
                agent_id,
                plan,
                navigation,
                "navigation_interaction_missing",
            )
            return False
        if not context.registry.has_component(
            primitive.interaction.target_id,
            OpenableComponent,
        ):
            self._fail_navigation(
                context,
                agent_id,
                plan,
                navigation,
                "navigation_door_missing",
            )
            return False
        current = context.registry.get_component(
            agent_id,
            SpatialLocationComponent,
        ).locator
        if current != primitive.origin:
            self._fail_navigation(
                context,
                agent_id,
                plan,
                navigation,
                "navigation_interaction_origin_mismatch",
            )
            return False
        openable = context.registry.get_component(
            primitive.interaction.target_id,
            OpenableComponent,
        )
        if not openable.is_open:
            failure = execute_navigation_interaction(
                context,
                agent_id,
                primitive.interaction,
                navigation.action_instance,
            )
            if failure is not None:
                self._fail_navigation(
                    context,
                    agent_id,
                    plan,
                    navigation,
                    failure,
                )
                return False
        context.events.emit(
            "navigation.interaction_completed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "target_id": primitive.interaction.target_id,
                "verb": primitive.interaction.verb.value,
                "primitive_index": navigation.current_primitive_index,
                **action_lineage_payload(navigation.action_instance),
            },
            correlation_id=navigation.correlation_id,
        )
        navigation.current_primitive_index += 1
        return True

    def _advance_navigation_move(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
        navigation: NavigationComponent,
        primitive: NavigationPrimitive,
    ) -> bool:
        if (
            not context.registry.has_component(agent_id, MovementComponent)
            or not context.registry.has_component(
                agent_id,
                SpatialLocationComponent,
            )
        ):
            self._fail_navigation(
                context,
                agent_id,
                plan,
                navigation,
                "movement_precondition_failed",
            )
            return False
        current = context.registry.get_component(
            agent_id,
            SpatialLocationComponent,
        ).locator
        if current == primitive.destination:
            self._complete_navigation_primitive(navigation, primitive)
            return True
        if current is None or current.space_id != primitive.destination.space_id:
            self._fail_navigation(
                context,
                agent_id,
                plan,
                navigation,
                "navigation_space_mismatch",
            )
            return False
        reference = primitive.destination.local_reference
        x = reference.get("x") if isinstance(reference, dict) else None
        y = reference.get("y") if isinstance(reference, dict) else None
        if (
            not isinstance(reference, dict)
            or reference.get("kind") != "coordinate"
            or not isinstance(x, int)
            or isinstance(x, bool)
            or not isinstance(y, int)
            or isinstance(y, bool)
        ):
            self._fail_navigation(
                context,
                agent_id,
                plan,
                navigation,
                "unsupported_local_locator",
            )
            return False
        destination = Coordinate(x, y)
        movement = context.registry.get_component(
            agent_id,
            MovementComponent,
        )
        if movement.destination is None:
            self._emit_navigation_leg_started(
                context,
                agent_id,
                navigation,
                primitive,
            )
            movement.destination = destination
            movement.path = ()
            movement.retry_after_tick = 0
            movement.path_correlation_id = navigation.correlation_id
            movement.action_instance = navigation.action_instance
        elif movement.destination != destination:
            self._fail_navigation(
                context,
                agent_id,
                plan,
                navigation,
                "conflicting_movement",
            )
        return False

    def _advance_navigation_transition(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
        navigation: NavigationComponent,
        primitive: NavigationPrimitive,
    ) -> bool:
        if (
            not context.registry.has_resource(SpaceRegistry)
            or not context.registry.has_component(
                agent_id, SpatialLocationComponent
            )
            or not context.registry.has_component(agent_id, PositionComponent)
        ):
            self._fail_navigation(
                context,
                agent_id,
                plan,
                navigation,
                "transition_precondition_failed",
            )
            return False
        topology = context.registry.get_resource(SpaceRegistry)
        spatial = context.registry.get_component(
            agent_id, SpatialLocationComponent
        )
        current = spatial.locator
        if current != primitive.origin:
            self._fail_navigation(
                context,
                agent_id,
                plan,
                navigation,
                "transition_origin_mismatch",
            )
            return False
        transition = next(
            (
                candidate
                for candidate in topology.transitions_from(current)
                if candidate.id == primitive.transition_id
                and candidate.to_locator == primitive.destination
            ),
            None,
        )
        if transition is None:
            self._fail_navigation(
                context,
                agent_id,
                plan,
                navigation,
                "transition_unavailable",
            )
            return False
        base_id = transition.id.removesuffix(":reverse")
        base_transition = topology.transition(base_id)
        base_available = base_transition.metadata.get("available", True)
        if not isinstance(base_available, bool):
            base_available = True
        if context.registry.has_resource(EnvironmentAvailabilityRegistry):
            state = context.registry.get_resource(
                EnvironmentAvailabilityRegistry
            ).state(base_id, base_available=base_available)
            available = state.available
            failure_reason = state.reason.value
        else:
            available = base_available
            failure_reason = "base_unavailable"
        if not available:
            self._fail_navigation(
                context,
                agent_id,
                plan,
                navigation,
                failure_reason,
            )
            return False
        destination_coordinate = self._coordinate_locator(
            primitive.destination
        )
        if destination_coordinate is None:
            self._fail_navigation(
                context,
                agent_id,
                plan,
                navigation,
                "unsupported_transition_destination",
            )
            return False
        next_physical_state: PhysicalStateComponent | None = None
        if context.registry.has_component(
            agent_id,
            PhysicalStateComponent,
        ) and context.registry.has_resource(SpatialIndex):
            physical_state = context.registry.get_component(
                agent_id,
                PhysicalStateComponent,
            )
            next_physical_state = replace(
                physical_state,
                pose=replace(
                    physical_state.pose,
                    room_id=primitive.destination.space_id,
                    anchor=destination_coordinate,
                ),
            )
            destination_world = local_world_for_space(
                context.registry,
                primitive.destination.space_id,
            )
            spatial_index = context.registry.get_resource(SpatialIndex)
            if (
                destination_world is None
                or not destination_world.grid.are_walkable(
                    next_physical_state.occupied_cells
                )
                or not spatial_index.can_place(
                    next_physical_state,
                    excluding=agent_id,
                )
            ):
                self._fail_navigation(
                    context,
                    agent_id,
                    plan,
                    navigation,
                    "transition_destination_occupied",
                )
                return False
        self._emit_navigation_leg_started(
            context,
            agent_id,
            navigation,
            primitive,
        )
        spatial.location = WorldLocation(
            scale=SpatialScale.BUILDING,
            place_id=primitive.destination.space_id,
            local_coordinate=destination_coordinate,
        )
        context.registry.get_component(
            agent_id, PositionComponent
        ).coordinate = destination_coordinate
        if next_physical_state is not None:
            context.registry.get_resource(SpatialIndex).update(
                SpatialIndexEntry(
                    agent_id,
                    next_physical_state,
                    dynamic=True,
                )
            )
            context.registry.set_component(agent_id, next_physical_state)
            sync_held_object_poses(
                context.registry,
                agent_id,
                next_physical_state.pose.room_id,
                destination_coordinate,
            )
        if context.registry.has_component(agent_id, MovementComponent):
            movement = context.registry.get_component(
                agent_id, MovementComponent
            )
            movement.destination = None
            movement.path = ()
            movement.retry_after_tick = 0
        context.events.emit(
            "portal.traversed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "portal_id": base_id,
                "transition_id": transition.id,
                "from_room_id": primitive.origin.space_id,
                "to_room_id": primitive.destination.space_id,
                "from": primitive.origin.local_reference,
                "to": primitive.destination.local_reference,
                **action_lineage_payload(navigation.action_instance),
            },
            correlation_id=navigation.correlation_id,
        )
        self._complete_navigation_primitive(navigation, primitive)
        return True

    @staticmethod
    def _coordinate_locator(locator: Locator) -> Coordinate | None:
        reference = locator.local_reference
        if not isinstance(reference, dict) or reference.get("kind") != "coordinate":
            return None
        x = reference.get("x")
        y = reference.get("y")
        if (
            not isinstance(x, int)
            or isinstance(x, bool)
            or not isinstance(y, int)
            or isinstance(y, bool)
        ):
            return None
        return Coordinate(x, y)

    def _advance_navigation_travel(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
        navigation: NavigationComponent,
        primitive: NavigationPrimitive,
    ) -> bool:
        if not context.registry.has_component(agent_id, TravelComponent):
            self._fail_navigation(
                context,
                agent_id,
                plan,
                navigation,
                "travel_precondition_failed",
            )
            return False
        travel = context.registry.get_component(agent_id, TravelComponent)
        if travel.status is TravelStatus.IDLE:
            self._emit_navigation_leg_started(
                context,
                agent_id,
                navigation,
                primitive,
            )
            if not TravelSystem().request(
                context,
                agent_id,
                primitive.destination_id or "",
                primitive.mode or TravelMode.WALK,
                entrance_transition_id=primitive.entrance_transition_id,
                outbound_transition_id=primitive.outbound_transition_id,
                origin_network_node_id=primitive.origin_network_node_id,
                allowed_edge_ids=frozenset(primitive.route_edge_ids),
                action_instance=navigation.action_instance,
            ):
                self._fail_navigation(
                    context,
                    agent_id,
                    plan,
                    navigation,
                    travel.failure_reason or "route_not_found",
                )
            return False
        if travel.status in {
            TravelStatus.ROUTE_PLANNED,
            TravelStatus.TRAVELLING,
        }:
            return False
        if travel.status is TravelStatus.ARRIVED:
            travel.status = TravelStatus.IDLE
            self._complete_navigation_primitive(navigation, primitive)
            return True
        if travel.status is TravelStatus.CANCELLED:
            navigation.status = NavigationStatus.INTERRUPTED
            context.events.emit(
                "navigation.interrupted",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "target_id": navigation.target_id,
                    "reason": "travel_interrupted",
                    **action_lineage_payload(navigation.action_instance),
                },
                correlation_id=navigation.correlation_id,
            )
            return False
        self._fail_navigation(
            context,
            agent_id,
            plan,
            navigation,
            "travel_blocked",
        )
        return False

    @staticmethod
    def _complete_navigation_primitive(
        navigation: NavigationComponent,
        primitive: NavigationPrimitive,
    ) -> None:
        navigation.completed_route_legs = primitive.route_leg_end
        navigation.current_primitive_index += 1

    @staticmethod
    def _emit_navigation_leg_started(
        context: SystemContext,
        agent_id: str,
        navigation: NavigationComponent,
        primitive: NavigationPrimitive,
    ) -> None:
        context.events.emit(
            "navigation.leg_started",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "target_id": navigation.target_id,
                "primitive_index": navigation.current_primitive_index,
                "primitive_kind": primitive.kind.value,
                "route_leg_start": primitive.route_leg_start,
                "route_leg_end": primitive.route_leg_end,
                "origin": {
                    "space_id": primitive.origin.space_id,
                    "local_reference": primitive.origin.local_reference,
                },
                "destination": {
                    "space_id": primitive.destination.space_id,
                    "local_reference": primitive.destination.local_reference,
                },
                **action_lineage_payload(navigation.action_instance),
            },
            correlation_id=navigation.correlation_id,
        )

    def _fail_navigation(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
        navigation: NavigationComponent,
        reason: str,
    ) -> None:
        navigation.status = NavigationStatus.FAILED
        navigation.failure_reason = reason
        context.events.emit(
            "navigation.failed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "target_id": navigation.target_id,
                "reason": reason,
                "completed_route_legs": navigation.completed_route_legs,
                **action_lineage_payload(navigation.action_instance),
            },
            correlation_id=navigation.correlation_id,
        )
        self._fail(context, agent_id, plan, reason)

    def _check_affordance(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
    ) -> None:
        if context.registry.has_component(agent_id, AffordanceRequestComponent):
            request = context.registry.get_component(
                agent_id, AffordanceRequestComponent
            )
            if request.status == "failed":
                reason = request.failure_reason or "affordance_failed"
                context.registry.remove_component(
                    agent_id, AffordanceRequestComponent
                )
                self._fail(context, agent_id, plan, reason)
            elif request.status == "completed":
                context.registry.remove_component(
                    agent_id, AffordanceRequestComponent
                )
                self._complete(context, agent_id, plan)
            return
        if not context.registry.has_component(
            agent_id, AffordanceExecutionComponent
        ):
            self._fail(context, agent_id, plan, "affordance_request_lost")

    def _check_transaction(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
    ) -> None:
        if context.registry.has_component(
            agent_id, TransactionRequestComponent
        ):
            request = context.registry.get_component(
                agent_id, TransactionRequestComponent
            )
            if request.status == "failed":
                reason = request.failure_reason or "transaction_failed"
                context.registry.remove_component(
                    agent_id, TransactionRequestComponent
                )
                self._fail(context, agent_id, plan, reason)
            elif request.status == "completed":
                context.registry.remove_component(
                    agent_id, TransactionRequestComponent
                )
                self._complete(context, agent_id, plan)
            return
        if not context.registry.has_component(
            agent_id, TransactionExecutionComponent
        ):
            self._fail(
                context,
                agent_id,
                plan,
                "transaction_request_lost",
            )

    def _check_interaction(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
    ) -> None:
        if context.registry.has_component(
            agent_id,
            InteractionRequestComponent,
        ):
            request = context.registry.get_component(
                agent_id,
                InteractionRequestComponent,
            )
            if request.status == "failed":
                reason = request.failure_reason or "interaction_failed"
                context.registry.remove_component(
                    agent_id,
                    InteractionRequestComponent,
                )
                self._fail(context, agent_id, plan, reason)
            elif request.status == "completed":
                context.registry.remove_component(
                    agent_id,
                    InteractionRequestComponent,
                )
                self._complete(context, agent_id, plan)
            return
        if not context.registry.has_component(
            agent_id,
            InteractionExecutionComponent,
        ):
            self._fail(
                context,
                agent_id,
                plan,
                "interaction_request_lost",
            )

    def _check_engagement(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
    ) -> None:
        if not context.registry.has_component(
            agent_id,
            EngagementExecutionComponent,
        ):
            self._fail(
                context,
                agent_id,
                plan,
                "engagement_execution_lost",
            )
            return
        execution = context.registry.get_component(
            agent_id,
            EngagementExecutionComponent,
        )
        if execution.status in {
            EngagementStatus.COMPLETED,
            EngagementStatus.PARTIAL,
        }:
            self._complete(context, agent_id, plan)
        elif execution.status is EngagementStatus.FAILED:
            self._fail(
                context,
                agent_id,
                plan,
                execution.failure_reason or "engagement_failed",
            )
        elif execution.status is EngagementStatus.CANCELLED:
            self._interrupt(
                context,
                agent_id,
                plan,
                execution.failure_reason or "engagement_cancelled",
            )

    def _check_text(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
    ) -> None:
        if context.registry.has_component(
            agent_id, TextActionRequestComponent
        ):
            request = context.registry.get_component(
                agent_id, TextActionRequestComponent
            )
            if request.status == "failed":
                reason = request.failure_reason or "text_action_failed"
                context.registry.remove_component(
                    agent_id, TextActionRequestComponent
                )
                self._fail(context, agent_id, plan, reason)
            elif request.status == "completed":
                context.registry.remove_component(
                    agent_id, TextActionRequestComponent
                )
                self._complete(context, agent_id, plan)
            return
        if not context.registry.has_component(
            agent_id, TextActionExecutionComponent
        ):
            self._fail(
                context,
                agent_id,
                plan,
                "text_action_request_lost",
            )

    @staticmethod
    def _activity_target_valid(
        context: SystemContext,
        agent_id: str,
        world: WorldMap,
        action: ActionInstance,
    ) -> bool:
        if action.target is None:
            return True
        if action.action in {ActionType.READ, ActionType.DRINK}:
            return (
                physical_activity_failure(
                    context.registry,
                    agent_id,
                    action.target,
                    action.action,
                )
                is None
            )
        position = context.registry.get_component(
            agent_id, PositionComponent
        ).coordinate
        try:
            station = world.station(action.target)
        except KeyError:
            zone = next(
                (candidate for candidate in world.zones if candidate.id == action.target),
                None,
            )
            return zone is not None and position in zone.tiles
        return (
            is_at_interaction_approach(
                context.registry,
                agent_id,
                station.id,
                fallback=station.position,
            )
            and action.action.value in station.supported_actions
        )

    @staticmethod
    def _resolve_affordance_station(
        context: SystemContext,
        agent_id: str,
        world: WorldMap,
        action: ActionInstance,
    ) -> str | None:
        if action.target is not None:
            try:
                station = world.station(action.target)
            except KeyError:
                return None
            if (
                is_at_interaction_approach(
                    context.registry,
                    agent_id,
                    station.id,
                    fallback=station.position,
                )
                and action.action.value in station.supported_actions
            ):
                return station.id
            return None
        matching = sorted(
            (
                station
                for station in world.stations
                if is_at_interaction_approach(
                    context.registry,
                    agent_id,
                    station.id,
                    fallback=station.position,
                )
                and action.action.value in station.supported_actions
            ),
            key=lambda station: station.id,
        )
        return matching[0].id if matching else None

    def _complete(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
    ) -> None:
        complete_plan_action(context, agent_id, plan)

    def _fail(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
        reason: str,
    ) -> None:
        fail_plan_action(context, agent_id, plan, reason)

    def _interrupt(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
        reason: str,
    ) -> None:
        interrupt_plan_action(context, agent_id, plan, reason)

    @staticmethod
    def _reset_current(plan: PlanComponent) -> None:
        reset_current_plan_action(plan)

    @staticmethod
    def _finish_plan(
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
        reason: str,
    ) -> None:
        finish_plan(context, agent_id, plan, reason)

    @staticmethod
    def _emit_action(
        context: SystemContext,
        event_type: ActionLifecycleEvent,
        agent_id: str,
        action: ActionInstance,
        extra: dict[str, JsonValue] | None = None,
    ) -> None:
        emit_action_lifecycle(context, event_type, agent_id, action, extra)


@dataclass(frozen=True, slots=True)
class TimedPlanActionSystem:
    name: str = "timed_plan_action"
    order: int = 165

    def update(self, context: SystemContext) -> None:
        for agent_id in context.registry.query_entities(
            PlanComponent, ActivityComponent, DriveComponent
        ):
            drive = context.registry.get_component(agent_id, DriveComponent)
            plan = context.registry.get_component(agent_id, PlanComponent)
            if (
                drive.state is not System1State.NORMAL
                or plan.current is None
                or plan.remaining_duration is None
            ):
                continue
            plan.remaining_duration = round(
                max(0.0, plan.remaining_duration - context.clock.dt), 12
            )
            if plan.remaining_duration > 0:
                continue
            if (
                plan.current.action in {ActionType.READ, ActionType.DRINK}
                and physical_activity_failure(
                    context.registry,
                    agent_id,
                    plan.current.target,
                    plan.current.action,
                )
                is not None
            ):
                activity = context.registry.get_component(
                    agent_id,
                    ActivityComponent,
                )
                activity.current = plan.previous_activity or ActivityType.IDLE
                fail_plan_action(
                    context,
                    agent_id,
                    plan,
                    "activity_precondition_failed",
                )
                continue
            if (
                plan.current.action is ActionType.DRINK
                and plan.current.target is not None
            ):
                failure = complete_drink(
                    context,
                    agent_id,
                    plan.current.target,
                    plan.current,
                )
                if failure is not None:
                    fail_plan_action(context, agent_id, plan, failure)
                    continue
            if (
                plan.current.action is ActionType.READ
                and context.registry.has_component(
                    agent_id, HomeostasisComponent
                )
                and context.registry.has_resource(HomeostasisConfiguration)
            ):
                from stage0_sim.domain.systems.homeostasis import (
                    apply_homeostasis_deltas,
                )

                configuration = context.registry.get_resource(
                    HomeostasisConfiguration
                )
                apply_homeostasis_deltas(
                    context,
                    agent_id,
                    source="read",
                    deltas={"happiness": configuration.read_happiness_delta},
                    details=action_lineage_payload(plan.current),
                )
            activity = context.registry.get_component(agent_id, ActivityComponent)
            previous_activity = activity.current
            activity.current = plan.previous_activity or ActivityType.IDLE
            if previous_activity is not activity.current:
                context.events.emit(
                    "activity.changed",
                    simulation_tick=context.clock.tick,
                    simulation_time=context.clock.simulation_time,
                    agent_id=agent_id,
                    payload={
                        "previous": previous_activity.value,
                        "current": activity.current.value,
                        "reason": "plan_action_completed",
                        **action_lineage_payload(plan.current),
                    },
                    correlation_id=plan.current.root_correlation_id,
                )
            complete_plan_action(context, agent_id, plan)
