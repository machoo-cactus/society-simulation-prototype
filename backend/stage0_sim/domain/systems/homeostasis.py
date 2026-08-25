from dataclasses import dataclass

from stage0_sim.domain.components import (
    ActivityComponent,
    ActivityType,
    AffordanceExecutionComponent,
    HomeostasisComponent,
    HomeostasisConfiguration,
    MovementComponent,
)
from stage0_sim.domain.systems import SystemContext


@dataclass(frozen=True, slots=True)
class MovementActivitySystem:
    name: str = "movement_activity"
    order: int = 150

    def update(self, context: SystemContext) -> None:
        for agent_id in context.registry.query_entities(
            ActivityComponent, MovementComponent
        ):
            activity = context.registry.get_component(agent_id, ActivityComponent)
            movement = context.registry.get_component(agent_id, MovementComponent)
            is_moving = bool(movement.path)
            if is_moving and not activity.movement_override:
                previous = activity.current
                activity.previous = previous
                activity.current = ActivityType.WALKING
                activity.movement_override = True
                self._emit_change(context, agent_id, previous, activity.current)
            elif not is_moving and activity.movement_override:
                previous = activity.current
                activity.current = activity.previous or ActivityType.IDLE
                activity.previous = None
                activity.movement_override = False
                self._emit_change(context, agent_id, previous, activity.current)

    @staticmethod
    def _emit_change(
        context: SystemContext,
        agent_id: str,
        previous: ActivityType,
        current: ActivityType,
    ) -> None:
        context.events.emit(
            "activity.changed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={"previous": previous.value, "current": current.value},
        )


@dataclass(frozen=True, slots=True)
class HomeostasisSystem:
    name: str = "homeostasis"
    order: int = 160

    def update(self, context: SystemContext) -> None:
        configuration = context.registry.get_resource(HomeostasisConfiguration)
        for agent_id in context.registry.query_entities(
            HomeostasisComponent, ActivityComponent
        ):
            if context.registry.has_component(
                agent_id, AffordanceExecutionComponent
            ):
                continue
            homeostasis = context.registry.get_component(
                agent_id, HomeostasisComponent
            )
            activity = context.registry.get_component(agent_id, ActivityComponent)
            rates = configuration.activity_rates[activity.current]
            before = homeostasis.snapshot()
            homeostasis.integrate(rates, context.clock.dt)
            after = homeostasis.snapshot()
            context.events.emit(
                "homeostasis.changed",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "activity": activity.current.value,
                    "before": before,
                    "after": after,
                    "derivative": {
                        "satiety": rates.satiety,
                        "energy": rates.energy,
                        "stress": rates.stress,
                    },
                },
            )
