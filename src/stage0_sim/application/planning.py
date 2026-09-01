from dataclasses import dataclass

from stage0_sim.application.cognition import (
    LocationContext,
    PlannerContext,
    PlannerGoalContext,
    PlanResult,
    PlanValidationError,
    StationContext,
    VitalContext,
    ZoneContext,
)
from stage0_sim.domain.components import (
    ActionType,
    AffordanceExecutionComponent,
    ControllerComponent,
    DriveComponent,
    GoalComponent,
    HomeostasisComponent,
    MemoryComponent,
    PlanAction,
    PlanComponent,
    PlannerComponent,
    PositionComponent,
    System1State,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.plans import is_dialogue_capable
from stage0_sim.domain.systems.spatial_context import local_world_for_agent
from stage0_sim.domain.world import WorldMap


@dataclass(slots=True)
class MacroPlanningSystem:
    name: str = "macro_planning"
    order: int = 300

    def update(self, context: SystemContext) -> None:
        from stage0_sim.application.macro_work import (
            MacroWorkCoordinator,
            PlanningWork,
        )

        if not context.registry.has_resource(WorldMap):
            return
        coordinator = context.registry.get_resource(MacroWorkCoordinator)
        for agent_id in context.registry.query_entities(
            PlannerComponent,
            PlanComponent,
            DriveComponent,
            HomeostasisComponent,
            PositionComponent,
        ):
            planner_state = context.registry.get_component(agent_id, PlannerComponent)
            plan = context.registry.get_component(agent_id, PlanComponent)
            drive = context.registry.get_component(agent_id, DriveComponent)
            if (
                not planner_state.needs_plan
                or planner_state.request_pending
                or plan.current is not None
                or plan.queue
                or drive.state is not System1State.NORMAL
                or context.registry.has_component(
                    agent_id, AffordanceExecutionComponent
                )
                or (
                    context.registry.has_component(
                        agent_id, ControllerComponent
                    )
                    and context.registry.get_component(
                        agent_id, ControllerComponent
                    ).enabled
                )
            ):
                continue

            memory_query: str | None = None
            top_k = 1
            if context.registry.has_component(agent_id, MemoryComponent):
                memory = context.registry.get_component(agent_id, MemoryComponent)
                memory_query = _memory_query(context, agent_id, planner_state)
                top_k = memory.top_k
            world = local_world_for_agent(context.registry, agent_id)
            if world is None:
                continue
            planner_context = build_planner_context(
                context,
                agent_id,
                world,
                planner_state,
            )
            planner_state.request_pending = True
            coordinator.enqueue_planning(
                PlanningWork(
                    agent_id=agent_id,
                    context=planner_context,
                    memory_query=memory_query,
                    top_k=top_k,
                    operation_id=(
                        f"planner:{agent_id}:"
                        f"{planner_state.request_count + 1:08d}"
                    ),
                )
            )


def validate_plan(
    result: PlanResult,
    world: WorldMap,
    registry: Registry,
    agent_id: str,
) -> None:
    if not result.actions:
        raise PlanValidationError("planner returned an empty action list")
    if len(result.actions) > 16:
        raise PlanValidationError("planner returned more than 16 actions")
    zone_ids = {zone.id for zone in world.zones}
    stations = {station.id: station for station in world.stations}
    transaction_points = {
        point.id: point for point in world.transaction_points
    }
    timed_actions = {
        ActionType.WORK,
        ActionType.SOCIALIZE,
        ActionType.READ,
        ActionType.IDLE,
    }
    affordance_actions = {ActionType.EAT, ActionType.SLEEP, ActionType.RELAX}

    for action in result.actions:
        if action.action is ActionType.MOVE_TO:
            if action.target is None or (
                action.target not in zone_ids and action.target not in stations
            ):
                raise PlanValidationError(
                    f"MOVE_TO target does not exist: {action.target}"
                )
        elif action.action in timed_actions and action.duration is None:
            raise PlanValidationError(f"{action.action.value} requires a duration")
        elif action.action is ActionType.TRANSACT:
            if action.target is None or action.offer_id is None:
                raise PlanValidationError(
                    "TRANSACT requires a target and offer_id"
                )
            point = transaction_points.get(action.target)
            if point is None:
                raise PlanValidationError(
                    f"TRANSACT target does not exist: {action.target}"
                )
            if not any(
                offer.id == action.offer_id for offer in point.offers
            ):
                raise PlanValidationError(
                    f"TRANSACT offer does not exist: {action.offer_id}"
                )

        if action.action is ActionType.SOCIALIZE and (
            action.target is None
            or action.target == agent_id
            or not is_dialogue_capable(registry, action.target)
        ):
            raise PlanValidationError("invalid_social_target")
        if action.action in affordance_actions and action.target is not None:
            station = stations.get(action.target)
            if station is None or action.action.value not in station.supported_actions:
                raise PlanValidationError(
                    f"{action.action.value} target is not a compatible station: "
                    f"{action.target}"
                )


def build_planner_context(
    context: SystemContext,
    agent_id: str,
    world: WorldMap,
    planner_state: PlannerComponent,
    memories: tuple[str, ...] = (),
) -> PlannerContext:
    homeostasis = context.registry.get_component(agent_id, HomeostasisComponent)
    position = context.registry.get_component(agent_id, PositionComponent)
    zone = world.zone_at(position.coordinate)
    return PlannerContext(
        agent_id=agent_id,
        simulation_time=context.clock.simulation_time,
        vitals=VitalContext(
            satiety=homeostasis.satiety,
            energy=homeostasis.energy,
            stress=homeostasis.stress,
        ),
        location=LocationContext(
            x=position.coordinate.x,
            y=position.coordinate.y,
            zone_id=zone.id if zone is not None else None,
        ),
        zones=tuple(
            ZoneContext(id=item.id, name=item.name, zone_type=item.zone_type)
            for item in sorted(world.zones, key=lambda candidate: candidate.id)
        ),
        stations=tuple(
            StationContext(
                id=item.id,
                name=item.name,
                x=item.position.x,
                y=item.position.y,
                actions=item.supported_actions,
                available=item.available,
            )
            for item in sorted(world.stations, key=lambda candidate: candidate.id)
        ),
        daily_goals=planner_state.daily_goals,
        memories=memories,
        structured_goals=_planner_goals(context.registry, agent_id),
    )


def _memory_query(
    context: SystemContext,
    agent_id: str,
    planner_state: PlannerComponent,
) -> str:
    homeostasis = context.registry.get_component(agent_id, HomeostasisComponent)
    goals = "; ".join(planner_state.daily_goals) or "continue the daily routine"
    return (
        f"Plan how to {goals}. Current satiety {homeostasis.satiety}, "
        f"energy {homeostasis.energy}, stress {homeostasis.stress}."
    )


def _planner_goals(
    registry: Registry,
    agent_id: str,
) -> tuple[PlannerGoalContext, ...]:
    if not registry.has_component(agent_id, GoalComponent):
        return ()
    return tuple(
        PlannerGoalContext(
            id=goal.definition.id,
            description=goal.definition.description,
            status=goal.status.value,
            priority=goal.definition.priority,
            tags=goal.definition.tags,
        )
        for goal in registry.get_component(agent_id, GoalComponent).goals
    )


def action_payload(action: PlanAction) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {"action": action.action.value}
    if action.target is not None:
        payload["target"] = action.target
    if action.duration is not None:
        payload["duration"] = action.duration
    if action.mode is not None:
        payload["mode"] = action.mode.value
    if action.offer_id is not None:
        payload["offer_id"] = action.offer_id
    return payload
