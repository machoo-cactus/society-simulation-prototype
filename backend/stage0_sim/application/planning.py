from dataclasses import dataclass

from stage0_sim.application.cognition import (
    EmbeddingError,
    LocationContext,
    Planner,
    PlannerContext,
    PlannerError,
    PlanResult,
    PlanValidationError,
    StationContext,
    VitalContext,
    ZoneContext,
)
from stage0_sim.application.memory import EpisodicMemoryStore
from stage0_sim.domain.components import (
    ActionType,
    AffordanceExecutionComponent,
    DriveComponent,
    HomeostasisComponent,
    MemoryComponent,
    PlanAction,
    PlanComponent,
    PlannerComponent,
    PositionComponent,
    System1State,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.world import WorldMap


@dataclass(slots=True)
class MacroPlanningSystem:
    planner: Planner
    memory_store: EpisodicMemoryStore
    name: str = "macro_planning"
    order: int = 300

    def update(self, context: SystemContext) -> None:
        if not context.registry.has_resource(WorldMap):
            return
        world = context.registry.get_resource(WorldMap)
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
                or plan.current is not None
                or plan.queue
                or drive.state is not System1State.NORMAL
                or context.registry.has_component(
                    agent_id, AffordanceExecutionComponent
                )
            ):
                continue

            memories: tuple[str, ...] = ()
            if context.registry.has_component(agent_id, MemoryComponent):
                memory = context.registry.get_component(agent_id, MemoryComponent)
                query = _memory_query(context, agent_id, planner_state)
                try:
                    retrieved = self.memory_store.retrieve(
                        agent_id=agent_id,
                        query=query,
                        simulation_time=context.clock.simulation_time,
                        top_k=memory.top_k,
                    )
                except EmbeddingError as error:
                    context.events.emit(
                        "memory.retrieval_failed",
                        simulation_tick=context.clock.tick,
                        simulation_time=context.clock.simulation_time,
                        agent_id=agent_id,
                        payload={"message": str(error), "query": query},
                    )
                    retrieved = ()
                memories = tuple(item.record.text for item in retrieved)
                if retrieved:
                    context.events.emit(
                        "memory.retrieved",
                        simulation_tick=context.clock.tick,
                        simulation_time=context.clock.simulation_time,
                        agent_id=agent_id,
                        payload={
                            "query": query,
                            "memory_ids": [
                                item.record.id for item in retrieved
                            ],
                            "scores": [item.score for item in retrieved],
                        },
                    )
            planner_context = _build_context(
                context, agent_id, world, planner_state, memories
            )
            requested = context.events.emit(
                "planner.requested",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "daily_goals": list(planner_context.daily_goals),
                    "zone_count": len(planner_context.zones),
                    "station_count": len(planner_context.stations),
                    "memory_count": len(planner_context.memories),
                },
            )
            planner_state.request_count += 1
            try:
                result = self.planner.plan(planner_context)
                validate_plan(result, world, set(context.registry.entities()))
            except (PlannerError, PlanValidationError) as error:
                planner_state.failure_count += 1
                context.events.emit(
                    "planner.failed",
                    simulation_tick=context.clock.tick,
                    simulation_time=context.clock.simulation_time,
                    agent_id=agent_id,
                    payload={
                        "error_type": type(error).__name__,
                        "message": str(error),
                    },
                    causation_id=requested.event_id,
                    correlation_id=requested.event_id,
                )
                continue

            plan.queue.extend(result.actions)
            planner_state.needs_plan = False
            planner_state.last_planned_at = context.clock.simulation_time
            context.events.emit(
                "planner.completed",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "rationale": result.rationale,
                    "actions": [_action_payload(action) for action in result.actions],
                    "provider": result.provider,
                    "latency_ms": result.latency_ms,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                },
                causation_id=requested.event_id,
                correlation_id=requested.event_id,
            )


def validate_plan(
    result: PlanResult,
    world: WorldMap,
    agent_ids: set[str],
) -> None:
    if not result.actions:
        raise PlanValidationError("planner returned an empty action list")
    if len(result.actions) > 16:
        raise PlanValidationError("planner returned more than 16 actions")
    zone_ids = {zone.id for zone in world.zones}
    stations = {station.id: station for station in world.stations}
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

        if action.action is ActionType.SOCIALIZE and (
            action.target is None or action.target not in agent_ids
        ):
            raise PlanValidationError(
                f"SOCIALIZE target does not exist: {action.target}"
            )
        if action.action in affordance_actions and action.target is not None:
            station = stations.get(action.target)
            if station is None or action.action.value not in station.supported_actions:
                raise PlanValidationError(
                    f"{action.action.value} target is not a compatible station: "
                    f"{action.target}"
                )


def _build_context(
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


def _action_payload(action: PlanAction) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {"action": action.action.value}
    if action.target is not None:
        payload["target"] = action.target
    if action.duration is not None:
        payload["duration"] = action.duration
    return payload
