from dataclasses import dataclass

from stage0_sim.domain.components import (
    ActionInstance,
    ActionOrigin,
    ActionType,
    ActivityComponent,
    ActivityType,
    AffordanceExecutionComponent,
    AffordanceRequestComponent,
    ConversationComponent,
    DriveComponent,
    MovementComponent,
    NavigationComponent,
    NavigationPrimitive,
    NavigationPrimitiveKind,
    NavigationStatus,
    NpcComponent,
    PlanComponent,
    PlannerComponent,
    PositionComponent,
    PossessionsComponent,
    SpatialLocationComponent,
    System1State,
    TransactionExecutionComponent,
    TransactionRequestComponent,
    TravelComponent,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.environment import EnvironmentAvailabilityRegistry
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.lineage import (
    action_lineage_payload,
    clear_plan_lineage,
    emit_action_lifecycle,
    materialize_legacy_plan,
)
from stage0_sim.domain.npcs import NpcPoolRegistry
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.spatial_context import (
    local_world_for_agent,
    shares_local_map,
)
from stage0_sim.domain.systems.travel import TravelSystem
from stage0_sim.domain.world import (
    CityWorld,
    Coordinate,
    Locator,
    SpaceRegistry,
    SpatialScale,
    TravelMode,
    TravelStatus,
    WorldLocation,
    WorldMap,
    find_path,
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
    PlanExecutionSystem()._fail(context, agent_id, plan, reason)


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
            materialize_legacy_plan(context, agent_id, plan)
            system1_navigation = (
                isinstance(plan.current, ActionInstance)
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
                if plan.current.action is ActionType.TRAVEL_TO:
                    travel = context.registry.get_component(
                        agent_id, TravelComponent
                    )
                    if travel.status in {
                        TravelStatus.ROUTE_PLANNED,
                        TravelStatus.TRAVELLING,
                    }:
                        continue
                    if travel.status in {
                        TravelStatus.ARRIVED,
                        TravelStatus.CANCELLED,
                        TravelStatus.BLOCKED,
                    }:
                        self._reset_current(plan)
                        travel.status = TravelStatus.IDLE
                if plan.waiting_for_affordance:
                    self._check_affordance(context, agent_id, plan)
                elif plan.waiting_for_transaction:
                    self._check_transaction(context, agent_id, plan)
                elif (
                    plan.current.action is ActionType.MOVE_TO
                    and plan.current_started
                    and context.registry.get_component(
                        agent_id, MovementComponent
                    ).destination
                    is None
                ):
                    self._complete(context, agent_id, plan)
                return_if_active = plan.current is not None
                if return_if_active:
                    continue

            if not plan.queue:
                self._request_replanning(context, agent_id)
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
        if not isinstance(action, ActionInstance):
            return
        if action.action is ActionType.SOCIALIZE and (
            action.target is None
            or action.target == agent_id
            or not is_dialogue_capable(context.registry, action.target)
        ):
            self._fail(context, agent_id, plan, "invalid_social_target")
            return
        plan.current_started = True
        self._emit_action(context, "plan.action_started", agent_id, action)

        if action.action is ActionType.MOVE_TO:
            if world is None:
                self._fail(
                    context,
                    agent_id,
                    plan,
                    "local_space_unavailable",
                )
                return
            destination = self._resolve_destination(
                context, agent_id, world, action.target
            )
            if destination is None:
                self._fail(context, agent_id, plan, "target_unreachable")
                return
            position = context.registry.get_component(agent_id, PositionComponent)
            if position.coordinate == destination:
                self._complete(context, agent_id, plan)
                return
            movement = context.registry.get_component(agent_id, MovementComponent)
            movement.destination = destination
            movement.path = ()
            movement.retry_after_tick = 0
            movement.action_instance = action
            return

        if action.action is ActionType.NAVIGATE:
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

        if action.action is ActionType.TRAVEL_TO:
            if (
                not context.registry.has_resource(CityWorld)
                or action.target is None
                or action.mode is None
                or not context.registry.has_component(
                    agent_id, TravelComponent
                )
            ):
                self._fail(context, agent_id, plan, "travel_precondition_failed")
                return
            if not TravelSystem().request(
                context,
                agent_id,
                action.target,
                action.mode,
                action_instance=action,
            ):
                travel = context.registry.get_component(
                    agent_id,
                    TravelComponent,
                )
                self._fail(
                    context,
                    agent_id,
                    plan,
                    travel.failure_reason or "route_not_found",
                )
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
                context.registry.get_component(
                    agent_id, PositionComponent
                ).coordinate
                != point.position
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
                context.registry.get_component(
                    customer_id, PositionComponent
                ).coordinate
                != point.position
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
            ActionType.IDLE,
        }:
            if (
                action.action in {ActionType.WORK, ActionType.READ}
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
        for other_id, other_spatial in context.registry.query(
            SpatialLocationComponent
        ):
            if (
                other_id != agent_id
                and other_spatial.locator == primitive.destination
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

    @staticmethod
    def _activity_target_valid(
        context: SystemContext,
        agent_id: str,
        world: WorldMap,
        action: ActionInstance,
    ) -> bool:
        if action.target is None:
            return True
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
            station.position == position
            and action.action.value in station.supported_actions
        )

    @staticmethod
    def _resolve_destination(
        context: SystemContext,
        agent_id: str,
        world: WorldMap,
        target: str | None,
    ) -> Coordinate | None:
        if target is None:
            return None
        try:
            return world.station(target).position
        except KeyError:
            pass
        zone = next((candidate for candidate in world.zones if candidate.id == target), None)
        if zone is None:
            return None
        position = context.registry.get_component(agent_id, PositionComponent)
        occupied = frozenset(
            other_position.coordinate
            for other_id, other_position in context.registry.query(PositionComponent)
            if other_id != agent_id
            and shares_local_map(context.registry, agent_id, other_id)
        )
        candidates: list[tuple[int, int, int, Coordinate]] = []
        for tile in zone.tiles:
            path = find_path(world.grid, position.coordinate, tile, occupied)
            if path is not None:
                candidates.append((len(path), tile.y, tile.x, tile))
        return min(candidates)[3] if candidates else None

    @staticmethod
    def _resolve_affordance_station(
        context: SystemContext,
        agent_id: str,
        world: WorldMap,
        action: ActionInstance,
    ) -> str | None:
        position = context.registry.get_component(agent_id, PositionComponent)
        if action.target is not None:
            try:
                station = world.station(action.target)
            except KeyError:
                return None
            if (
                station.position == position.coordinate
                and action.action.value in station.supported_actions
            ):
                return station.id
            return None
        matching = sorted(
            (
                station
                for station in world.stations
                if station.position == position.coordinate
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
        action = plan.current
        if isinstance(action, ActionInstance):
            self._emit_action(context, "plan.action_completed", agent_id, action)
        self._reset_current(plan)
        if not plan.queue:
            self._finish_plan(context, agent_id, plan, "completed")
            self._request_replanning(context, agent_id)

    def _fail(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
        reason: str,
    ) -> None:
        action = plan.current
        if isinstance(action, ActionInstance):
            self._emit_action(
                context,
                "plan.action_failed",
                agent_id,
                action,
                {"reason": reason},
            )
        self._reset_current(plan)
        clear_plan_lineage(
            context,
            agent_id,
            plan,
            reason=reason,
            current_status="action.cancelled",
        )
        self._request_replanning(context, agent_id)

    def _interrupt(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
        reason: str,
    ) -> None:
        action = plan.current
        if isinstance(action, ActionInstance):
            emit_action_lifecycle(
                context,
                "action.interrupted",
                agent_id,
                action,
                {"reason": reason},
            )
        self._reset_current(plan)
        clear_plan_lineage(
            context,
            agent_id,
            plan,
            reason=reason,
            current_status="action.cancelled",
        )
        self._request_replanning(context, agent_id)

    @staticmethod
    def _reset_current(plan: PlanComponent) -> None:
        plan.current = None
        plan.remaining_duration = None
        plan.previous_activity = None
        plan.waiting_for_affordance = False
        plan.waiting_for_transaction = False
        plan.current_started = False

    @staticmethod
    def _finish_plan(
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

    @staticmethod
    def _request_replanning(context: SystemContext, agent_id: str) -> None:
        if context.registry.has_component(agent_id, PlannerComponent):
            context.registry.get_component(
                agent_id, PlannerComponent
            ).needs_plan = True

    @staticmethod
    def _emit_action(
        context: SystemContext,
        event_type: str,
        agent_id: str,
        action: ActionInstance,
        extra: dict[str, JsonValue] | None = None,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "action": action.action.value,
            **action_lineage_payload(action),
        }
        if action.target is not None:
            payload["target"] = action.target
        if action.duration is not None:
            payload["duration"] = action.duration
        if action.mode is not None:
            payload["mode"] = action.mode.value
        if action.offer_id is not None:
            payload["offer_id"] = action.offer_id
        payload.update(extra or {})
        legacy_event = context.events.emit(
            event_type,
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload=payload,
            correlation_id=action.root_correlation_id,
        )
        lifecycle_type = {
            "plan.action_started": "action.started",
            "plan.action_completed": "action.completed",
            "plan.action_failed": "action.failed",
        }.get(event_type)
        if lifecycle_type is not None:
            emit_action_lifecycle(
                context,
                lifecycle_type,
                agent_id,
                action,
                extra,
                causation_id=legacy_event.event_id,
            )


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
            if isinstance(plan.current, ActionInstance):
                emit_action_lifecycle(
                    context,
                    "action.progressed",
                    agent_id,
                    plan.current,
                    {
                        "remaining_duration": plan.remaining_duration,
                        "duration": plan.current.duration,
                    },
                )
            if plan.remaining_duration > 0:
                continue
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
                        **(
                            action_lineage_payload(plan.current)
                            if isinstance(plan.current, ActionInstance)
                            else {}
                        ),
                    },
                    correlation_id=(
                        plan.current.root_correlation_id
                        if isinstance(plan.current, ActionInstance)
                        else None
                    ),
                )
            PlanExecutionSystem()._complete(context, agent_id, plan)
