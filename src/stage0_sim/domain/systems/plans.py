from dataclasses import dataclass

from stage0_sim.domain.components import (
    ActionType,
    ActivityComponent,
    ActivityType,
    AffordanceExecutionComponent,
    AffordanceRequestComponent,
    ConversationComponent,
    DriveComponent,
    MovementComponent,
    PlanAction,
    PlanComponent,
    PlannerComponent,
    PositionComponent,
    System1State,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.world import Coordinate, WorldMap, find_path


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
        world = context.registry.get_resource(WorldMap)
        for agent_id in context.registry.query_entities(
            PlanComponent,
            DriveComponent,
            PositionComponent,
            MovementComponent,
            ActivityComponent,
        ):
            drive = context.registry.get_component(agent_id, DriveComponent)
            if drive.state is not System1State.NORMAL:
                continue
            plan = context.registry.get_component(agent_id, PlanComponent)
            if plan.current is not None:
                if plan.waiting_for_affordance:
                    self._check_affordance(context, agent_id, plan)
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
            self._start(context, agent_id, plan, world)

    def _start(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
        world: WorldMap,
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
        self._emit_action(context, "plan.action_started", agent_id, action)

        if action.action is ActionType.MOVE_TO:
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
                and not self._activity_target_valid(
                    context, agent_id, world, action
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
                    },
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
            ),
        )
        plan.waiting_for_affordance = True

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

    @staticmethod
    def _activity_target_valid(
        context: SystemContext,
        agent_id: str,
        world: WorldMap,
        action: PlanAction,
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
        action: PlanAction,
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
        if action is not None:
            self._emit_action(context, "plan.action_completed", agent_id, action)
        self._reset_current(plan)
        if not plan.queue:
            self._request_replanning(context, agent_id)

    def _fail(
        self,
        context: SystemContext,
        agent_id: str,
        plan: PlanComponent,
        reason: str,
    ) -> None:
        action = plan.current
        if action is not None:
            self._emit_action(
                context,
                "plan.action_failed",
                agent_id,
                action,
                {"reason": reason},
            )
        self._reset_current(plan)
        plan.queue.clear()
        self._request_replanning(context, agent_id)

    @staticmethod
    def _reset_current(plan: PlanComponent) -> None:
        plan.current = None
        plan.remaining_duration = None
        plan.previous_activity = None
        plan.waiting_for_affordance = False
        plan.current_started = False

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
        action: PlanAction,
        extra: dict[str, JsonValue] | None = None,
    ) -> None:
        payload: dict[str, JsonValue] = {"action": action.action.value}
        if action.target is not None:
            payload["target"] = action.target
        if action.duration is not None:
            payload["duration"] = action.duration
        payload.update(extra or {})
        context.events.emit(
            event_type,
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload=payload,
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
                    },
                )
            PlanExecutionSystem()._complete(context, agent_id, plan)
