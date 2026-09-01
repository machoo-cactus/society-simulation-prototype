from dataclasses import dataclass

from stage0_sim.domain.components import (
    ActionInstance,
    ActionType,
    ActivityComponent,
    ActivityType,
    AffordanceExecutionComponent,
    AffordanceRequestComponent,
    DriveComponent,
    HomeostasisComponent,
    PositionComponent,
    System1Configuration,
    System1State,
)
from stage0_sim.domain.environment import EnvironmentAvailabilityRegistry
from stage0_sim.domain.lineage import (
    action_lineage_payload,
)
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.spatial_context import local_world_for_agent
from stage0_sim.domain.world import AffordanceStation


@dataclass(frozen=True, slots=True)
class AffordanceExecutionSystem:
    name: str = "affordance_execution"
    order: int = 180

    def update(self, context: SystemContext) -> None:
        existing_executions = tuple(
            context.registry.query_entities(AffordanceExecutionComponent)
        )
        for agent_id in existing_executions:
            self._advance(context, agent_id)

        configuration = context.registry.get_resource(System1Configuration)
        for agent_id in context.registry.query_entities(
            DriveComponent,
            PositionComponent,
            HomeostasisComponent,
            ActivityComponent,
        ):
            if agent_id in existing_executions:
                continue
            drive = context.registry.get_component(agent_id, DriveComponent)
            if (
                drive.state is not System1State.EXECUTING_CORRECTION
                or drive.active_drive is None
                or drive.target_station_id is None
                or context.registry.has_component(
                    agent_id, AffordanceExecutionComponent
                )
            ):
                continue
            action = configuration.corrective_actions[drive.active_drive]
            started, _ = self._start(
                context,
                agent_id,
                drive.target_station_id,
                action,
                source="system1",
                action_instance=drive.correction_action,
            )
            if started:
                self._advance(context, agent_id)

        for agent_id in context.registry.query_entities(
            AffordanceRequestComponent,
            PositionComponent,
            HomeostasisComponent,
            ActivityComponent,
        ):
            request = context.registry.get_component(
                agent_id, AffordanceRequestComponent
            )
            if (
                request.status != "requested"
                or context.registry.has_component(
                    agent_id, AffordanceExecutionComponent
                )
            ):
                continue
            action = ActionType(request.action)
            started, failure = self._start(
                context,
                agent_id,
                request.station_id,
                action,
                source=request.source,
                action_instance=request.action_instance,
            )
            if started:
                request.status = "running"
                self._advance(context, agent_id)
            else:
                request.status = "failed"
                request.failure_reason = failure

    def _start(
        self,
        context: SystemContext,
        agent_id: str,
        station_id: str,
        action: ActionType,
        *,
        source: str,
        action_instance: ActionInstance | None = None,
    ) -> tuple[bool, str | None]:
        world = local_world_for_agent(context.registry, agent_id)
        if world is None:
            self._emit_failure(
                context,
                agent_id,
                station_id,
                action.value,
                "local_space_unavailable",
                action_instance,
            )
            return False, "local_space_unavailable"
        try:
            station = world.station(station_id)
        except KeyError:
            self._emit_failure(
                context,
                agent_id,
                station_id,
                action.value,
                "station_not_found",
                action_instance,
            )
            return False, "station_not_found"
        failure = self._precondition_failure(
            context,
            agent_id,
            station,
            action.value,
            check_availability=True,
        )
        if failure is not None:
            self._emit_failure(
                context,
                agent_id,
                station.id,
                action.value,
                failure,
                action_instance,
            )
            return False, failure

        definition = station.action(action.value)
        homeostasis = context.registry.get_component(agent_id, HomeostasisComponent)
        activity = context.registry.get_component(agent_id, ActivityComponent)
        previous_activity = activity.current
        activity.current = _activity_for_action(action)
        activity.previous = None
        activity.movement_override = False
        started = context.events.emit(
            "affordance.started",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "station_id": station.id,
                "action": action.value,
                "duration": definition.duration,
                **action_lineage_payload(action_instance),
            },
            correlation_id=(
                action_instance.root_correlation_id
                if action_instance is not None
                else None
            ),
        )
        context.registry.add_component(
            agent_id,
            AffordanceExecutionComponent(
                station_id=station.id,
                definition=definition,
                elapsed=0.0,
                starting_satiety=homeostasis.satiety,
                starting_energy=homeostasis.energy,
                starting_stress=homeostasis.stress,
                previous_activity=previous_activity,
                correlation_id=(
                    action_instance.root_correlation_id
                    if action_instance is not None
                    else started.event_id
                ),
                source=source,
                action_instance=action_instance,
            ),
        )
        if previous_activity is not activity.current:
            context.events.emit(
                "activity.changed",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "previous": previous_activity.value,
                    "current": activity.current.value,
                    "reason": "affordance_started",
                },
                causation_id=started.event_id,
                correlation_id=(
                    action_instance.root_correlation_id
                    if action_instance is not None
                    else started.event_id
                ),
            )
        return True, None

    def _advance(self, context: SystemContext, agent_id: str) -> None:
        execution = context.registry.get_component(
            agent_id, AffordanceExecutionComponent
        )
        world = local_world_for_agent(context.registry, agent_id)
        if world is None:
            cancel_affordance(context, agent_id, "local_space_unavailable")
            return
        station = world.station(execution.station_id)
        failure = self._precondition_failure(
            context,
            agent_id,
            station,
            execution.definition.action,
            check_availability=False,
        )
        if failure is not None:
            cancel_affordance(context, agent_id, failure)
            return

        homeostasis = context.registry.get_component(agent_id, HomeostasisComponent)
        before = homeostasis.snapshot()
        remaining = execution.definition.duration - execution.elapsed
        step = min(context.clock.dt, remaining)
        execution.elapsed = round(execution.elapsed + step, 12)
        progress = execution.elapsed / execution.definition.duration
        final_values = execution.definition.effect.final_values(
            execution.starting_satiety,
            execution.starting_energy,
            execution.starting_stress,
        )
        homeostasis.satiety = _interpolate(
            execution.starting_satiety, final_values[0], progress
        )
        homeostasis.energy = _interpolate(
            execution.starting_energy, final_values[1], progress
        )
        homeostasis.stress = _interpolate(
            execution.starting_stress, final_values[2], progress
        )
        after = homeostasis.snapshot()
        context.events.emit(
            "homeostasis.changed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "activity": context.registry.get_component(
                    agent_id, ActivityComponent
                ).current.value,
                "source": "affordance",
                "station_id": execution.station_id,
                "before": before,
                "after": after,
                "derivative": {
                    "satiety": round(
                        (homeostasis.satiety - execution.starting_satiety)
                        / execution.elapsed,
                        12,
                    ),
                    "energy": round(
                        (homeostasis.energy - execution.starting_energy)
                        / execution.elapsed,
                        12,
                    ),
                    "stress": round(
                        (homeostasis.stress - execution.starting_stress)
                        / execution.elapsed,
                        12,
                    ),
                },
                **action_lineage_payload(execution.action_instance),
            },
            correlation_id=execution.correlation_id,
        )
        context.events.emit(
            "affordance.progressed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "station_id": execution.station_id,
                "action": execution.definition.action,
                "elapsed": execution.elapsed,
                "duration": execution.definition.duration,
                "progress": round(progress, 12),
                "before": before,
                "after": after,
                **action_lineage_payload(execution.action_instance),
            },
            correlation_id=execution.correlation_id,
        )
        if execution.elapsed >= execution.definition.duration:
            self._complete(context, agent_id, execution)

    @staticmethod
    def _complete(
        context: SystemContext,
        agent_id: str,
        execution: AffordanceExecutionComponent,
    ) -> None:
        homeostasis = context.registry.get_component(agent_id, HomeostasisComponent)
        context.events.emit(
            "affordance.completed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "station_id": execution.station_id,
                "action": execution.definition.action,
                "duration": execution.definition.duration,
                "homeostasis": homeostasis.snapshot(),
                **action_lineage_payload(execution.action_instance),
            },
            correlation_id=execution.correlation_id,
        )
        if (
            execution.source == "plan"
            and context.registry.has_component(
                agent_id, AffordanceRequestComponent
            )
        ):
            request = context.registry.get_component(
                agent_id, AffordanceRequestComponent
            )
            request.status = "completed"
        _restore_activity(context, agent_id, execution, "affordance_completed")
        context.registry.remove_component(agent_id, AffordanceExecutionComponent)
        if execution.source == "system1":
            from stage0_sim.domain.systems.system1 import (
                resolve_system1,
                system1_drive_recovered,
            )

            drive = context.registry.get_component(agent_id, DriveComponent)
            configuration = context.registry.get_resource(System1Configuration)
            if (
                drive.active_drive is not None
                and system1_drive_recovered(
                    homeostasis,
                    drive.active_drive,
                    configuration,
                )
            ):
                resolve_system1(
                    context,
                    agent_id,
                    drive,
                )

    @staticmethod
    def _precondition_failure(
        context: SystemContext,
        agent_id: str,
        station: AffordanceStation,
        action: str,
        *,
        check_availability: bool = True,
    ) -> str | None:
        if check_availability:
            available = station.available
            reason = "station_unavailable"
            if context.registry.has_resource(EnvironmentAvailabilityRegistry):
                state = context.registry.get_resource(
                    EnvironmentAvailabilityRegistry
                ).state(station.id, base_available=station.available)
                available = state.available
                reason = state.reason.value
            if not available:
                return reason
        if action not in station.supported_actions:
            return "action_not_supported"
        position = context.registry.get_component(agent_id, PositionComponent)
        if position.coordinate != station.position:
            return "agent_not_at_station"
        active_count = sum(
            execution.station_id == station.id
            for other_id, execution in context.registry.query(
                AffordanceExecutionComponent
            )
            if other_id != agent_id
        )
        if active_count >= station.capacity:
            return "station_at_capacity"
        return None

    @staticmethod
    def _emit_failure(
        context: SystemContext,
        agent_id: str,
        station_id: str,
        action: str,
        reason: str,
        action_instance: ActionInstance | None = None,
    ) -> None:
        context.events.emit(
            "affordance.failed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "station_id": station_id,
                "action": action,
                "reason": reason,
                **action_lineage_payload(action_instance),
            },
            correlation_id=(
                action_instance.root_correlation_id
                if action_instance is not None
                else None
            ),
        )


def cancel_affordance(context: SystemContext, agent_id: str, reason: str) -> None:
    if not context.registry.has_component(agent_id, AffordanceExecutionComponent):
        return
    execution = context.registry.get_component(
        agent_id, AffordanceExecutionComponent
    )
    context.events.emit(
        "affordance.cancelled",
        simulation_tick=context.clock.tick,
        simulation_time=context.clock.simulation_time,
        agent_id=agent_id,
        payload={
            "station_id": execution.station_id,
            "action": execution.definition.action,
            "elapsed": execution.elapsed,
            "reason": reason,
            **action_lineage_payload(execution.action_instance),
        },
        correlation_id=execution.correlation_id,
    )
    if (
        execution.source == "plan"
        and context.registry.has_component(agent_id, AffordanceRequestComponent)
    ):
        request = context.registry.get_component(
            agent_id, AffordanceRequestComponent
        )
        request.status = "failed"
        request.failure_reason = reason
    _restore_activity(context, agent_id, execution, "affordance_cancelled")
    context.registry.remove_component(agent_id, AffordanceExecutionComponent)


def _restore_activity(
    context: SystemContext,
    agent_id: str,
    execution: AffordanceExecutionComponent,
    reason: str,
) -> None:
    activity = context.registry.get_component(agent_id, ActivityComponent)
    previous = activity.current
    activity.current = execution.previous_activity
    activity.previous = None
    activity.movement_override = False
    if previous is not activity.current:
        context.events.emit(
            "activity.changed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "previous": previous.value,
                "current": activity.current.value,
                "reason": reason,
            },
            correlation_id=execution.correlation_id,
        )


def _activity_for_action(action: ActionType) -> ActivityType:
    mapping = {
        ActionType.EAT: ActivityType.EATING,
        ActionType.SLEEP: ActivityType.SLEEPING,
        ActionType.RELAX: ActivityType.RELAXING,
        ActionType.WORK: ActivityType.WORKING,
    }
    return mapping.get(action, ActivityType.IDLE)


def _interpolate(start: float, end: float, progress: float) -> float:
    return round(min(100.0, max(0.0, start + (end - start) * progress)), 12)
