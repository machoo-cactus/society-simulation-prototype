from dataclasses import dataclass

from stage0_sim.domain.components import (
    ActivityComponent,
    ActivityType,
    AffordanceExecutionComponent,
    AffordanceRequestComponent,
    ControllerComponent,
    DriveComponent,
    DriveType,
    HomeostasisComponent,
    MovementComponent,
    PlanComponent,
    PlannerComponent,
    PositionComponent,
    SpatialLocationComponent,
    System1Configuration,
    System1State,
    TravelComponent,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.affordances import cancel_affordance
from stage0_sim.domain.systems.spatial_context import (
    local_world_for_agent,
    shares_local_map,
)
from stage0_sim.domain.world import AffordanceStation, Coordinate, WorldMap, find_path


@dataclass(frozen=True, slots=True)
class System1ArbitrationSystem:
    name: str = "system1_arbitration"
    order: int = 170

    def update(self, context: SystemContext) -> None:
        configuration = context.registry.get_resource(System1Configuration)
        for agent_id in context.registry.query_entities(
            HomeostasisComponent, DriveComponent
        ):
            homeostasis = context.registry.get_component(
                agent_id, HomeostasisComponent
            )
            drive = context.registry.get_component(agent_id, DriveComponent)
            critical = self._critical_drives(homeostasis, configuration)
            self._emit_new_breaches(
                context,
                agent_id,
                homeostasis,
                configuration,
                critical - drive.critical_drives,
            )
            drive.critical_drives = critical

            if drive.state is System1State.NORMAL:
                if not critical:
                    continue
                selected = self._select_drive(homeostasis, configuration, critical)
                self._activate(context, agent_id, drive, selected, homeostasis, configuration)
            else:
                self._clear_plan(context, agent_id)
                if critical:
                    selected = self._select_drive(homeostasis, configuration, critical)
                    if selected is not drive.active_drive:
                        previous = drive.active_drive
                        cancel_affordance(context, agent_id, "drive_priority_changed")
                        self._clear_affordance_request(context, agent_id)
                        drive.active_drive = selected
                        drive.target_station_id = None
                        self._clear_movement(context, agent_id)
                        self._emit(
                            context,
                            "system1.drive_changed",
                            agent_id,
                            {
                                "previous": previous.value if previous is not None else None,
                                "current": selected.value,
                            },
                        )
                elif drive.active_drive is not None and self._is_recovered(
                    homeostasis, drive.active_drive, configuration
                ):
                    if context.registry.has_component(
                        agent_id, AffordanceExecutionComponent
                    ):
                        continue
                    self._resolve(context, agent_id, drive)
                    continue

            if context.registry.has_component(agent_id, TravelComponent):
                travel = context.registry.get_component(
                    agent_id, TravelComponent
                )
                if travel.status.value in {"ROUTE_PLANNED", "TRAVELLING"}:
                    travel.interruption_requested = True
                    continue
            self._ensure_correction_target(context, agent_id, drive, configuration)

    def _activate(
        self,
        context: SystemContext,
        agent_id: str,
        drive: DriveComponent,
        selected: DriveType,
        homeostasis: HomeostasisComponent,
        configuration: System1Configuration,
    ) -> None:
        if context.registry.has_component(agent_id, ControllerComponent):
            controller = context.registry.get_component(
                agent_id, ControllerComponent
            )
            controller.state_revision += 1
            controller.request_pending = False
            controller.current_decision_id = None
            controller.last_outcome = "interrupted by survival need"
        drive.active_drive = selected
        self._transition(context, agent_id, drive, System1State.CRITICAL_DETECTED)
        self._emit(
            context,
            "system1.activated",
            agent_id,
            {
                "drive": selected.value,
                "severity": configuration.thresholds[selected].severity(
                    self._meter_value(homeostasis, selected)
                ),
            },
        )
        self._transition(context, agent_id, drive, System1State.PREEMPTING)
        self._clear_plan(context, agent_id)
        self._clear_movement(context, agent_id)
        cancel_affordance(context, agent_id, "system1_preemption")
        self._clear_affordance_request(context, agent_id)
        self._clear_activity(context, agent_id)

    def _ensure_correction_target(
        self,
        context: SystemContext,
        agent_id: str,
        drive: DriveComponent,
        configuration: System1Configuration,
    ) -> None:
        if drive.active_drive is None:
            return
        if context.registry.has_component(
            agent_id, SpatialLocationComponent
        ):
            location = context.registry.get_component(
                agent_id, SpatialLocationComponent
            ).location
            if location.scale.value != "BUILDING":
                self._block(
                    context,
                    agent_id,
                    drive,
                    "outside_local_correction_context",
                )
                return
        if not context.registry.has_resource(WorldMap):
            self._block(context, agent_id, drive, "world_not_configured")
            return
        if not context.registry.has_component(agent_id, PositionComponent):
            self._block(context, agent_id, drive, "position_not_configured")
            return
        if not context.registry.has_component(agent_id, MovementComponent):
            self._block(context, agent_id, drive, "movement_not_configured")
            return

        world = local_world_for_agent(context.registry, agent_id)
        position = context.registry.get_component(agent_id, PositionComponent)
        selected = self._nearest_station(
            context,
            agent_id,
            position.coordinate,
            world,
            configuration.corrective_actions[drive.active_drive].value,
        )
        if selected is None:
            cancel_affordance(context, agent_id, "corrective_station_unavailable")
            self._clear_movement(context, agent_id)
            drive.target_station_id = None
            self._block(context, agent_id, drive, "no_reachable_corrective_station")
            return

        station, path_cost = selected
        movement = context.registry.get_component(agent_id, MovementComponent)
        if drive.target_station_id != station.id:
            previous_target = drive.target_station_id
            if context.registry.has_component(
                agent_id, AffordanceExecutionComponent
            ):
                cancel_affordance(context, agent_id, "corrective_station_changed")
            drive.target_station_id = station.id
            movement.destination = station.position
            movement.path = ()
            movement.retry_after_tick = 0
            movement.path_correlation_id = None
            payload: dict[str, JsonValue] = {
                "drive": drive.active_drive.value,
                "station_id": station.id,
                "position": station.position.to_payload(),
                "path_cost": path_cost,
            }
            if previous_target is not None:
                payload["previous_station_id"] = previous_target
            self._emit(
                context,
                "system1.target_selected",
                agent_id,
                payload,
            )

        if position.coordinate == station.position:
            movement.destination = None
            movement.path = ()
            self._transition(
                context, agent_id, drive, System1State.EXECUTING_CORRECTION
            )
        else:
            if movement.destination != station.position:
                movement.destination = station.position
                movement.path = ()
                movement.retry_after_tick = 0
            self._transition(
                context,
                agent_id,
                drive,
                System1State.NAVIGATING_TO_CORRECTION,
            )

    @staticmethod
    def _nearest_station(
        context: SystemContext,
        agent_id: str,
        origin: Coordinate,
        world: WorldMap,
        corrective_action: str,
    ) -> tuple[AffordanceStation, int] | None:
        occupied = frozenset(
            position.coordinate
            for other_id, position in context.registry.query(PositionComponent)
            if other_id != agent_id
            and shares_local_map(context.registry, agent_id, other_id)
        )
        candidates: list[tuple[int, str, AffordanceStation]] = []
        for station in world.stations:
            if not station.available or corrective_action not in station.supported_actions:
                continue
            active_count = sum(
                execution.station_id == station.id
                for other_id, execution in context.registry.query(
                    AffordanceExecutionComponent
                )
                if other_id != agent_id
            )
            reserved_count = sum(
                other_drive.target_station_id == station.id
                for other_id, other_drive in context.registry.query(DriveComponent)
                if other_id != agent_id
                and not context.registry.has_component(
                    other_id, AffordanceExecutionComponent
                )
            )
            if active_count + reserved_count >= station.capacity:
                continue
            path = find_path(world.grid, origin, station.position, occupied)
            if path is not None:
                candidates.append((len(path), station.id, station))
        if not candidates:
            return None
        path_cost, _, station = min(candidates, key=lambda candidate: (candidate[0], candidate[1]))
        return station, path_cost

    @staticmethod
    def _critical_drives(
        homeostasis: HomeostasisComponent,
        configuration: System1Configuration,
    ) -> frozenset[DriveType]:
        return frozenset(
            drive
            for drive in DriveType
            if configuration.thresholds[drive].is_critical(
                System1ArbitrationSystem._meter_value(homeostasis, drive)
            )
        )

    @staticmethod
    def _select_drive(
        homeostasis: HomeostasisComponent,
        configuration: System1Configuration,
        critical: frozenset[DriveType],
    ) -> DriveType:
        tie_rank = {
            drive: index for index, drive in enumerate(configuration.tie_break_order)
        }
        return max(
            critical,
            key=lambda drive: (
                configuration.thresholds[drive].severity(
                    System1ArbitrationSystem._meter_value(homeostasis, drive)
                ),
                -tie_rank[drive],
            ),
        )

    @staticmethod
    def _is_recovered(
        homeostasis: HomeostasisComponent,
        drive: DriveType,
        configuration: System1Configuration,
    ) -> bool:
        return configuration.thresholds[drive].is_recovered(
            System1ArbitrationSystem._meter_value(homeostasis, drive)
        )

    @staticmethod
    def _meter_value(homeostasis: HomeostasisComponent, drive: DriveType) -> float:
        if drive is DriveType.SATIETY:
            return homeostasis.satiety
        if drive is DriveType.ENERGY:
            return homeostasis.energy
        return homeostasis.stress

    def _emit_new_breaches(
        self,
        context: SystemContext,
        agent_id: str,
        homeostasis: HomeostasisComponent,
        configuration: System1Configuration,
        new_breaches: frozenset[DriveType],
    ) -> None:
        for drive in configuration.tie_break_order:
            if drive not in new_breaches:
                continue
            threshold = configuration.thresholds[drive]
            self._emit(
                context,
                "threshold.breached",
                agent_id,
                {
                    "drive": drive.value,
                    "value": self._meter_value(homeostasis, drive),
                    "critical_threshold": threshold.critical,
                    "direction": "high" if threshold.critical_when_high else "low",
                },
            )

    def _resolve(
        self,
        context: SystemContext,
        agent_id: str,
        drive: DriveComponent,
    ) -> None:
        resolved_drive = drive.active_drive
        self._clear_movement(context, agent_id)
        self._transition(context, agent_id, drive, System1State.RECOVERED)
        self._emit(
            context,
            "system1.resolved",
            agent_id,
            {"drive": resolved_drive.value if resolved_drive is not None else None},
        )
        drive.active_drive = None
        drive.target_station_id = None
        self._transition(context, agent_id, drive, System1State.NORMAL)

    def _block(
        self,
        context: SystemContext,
        agent_id: str,
        drive: DriveComponent,
        reason: str,
    ) -> None:
        was_blocked = drive.state is System1State.BLOCKED_SURVIVAL
        self._transition(context, agent_id, drive, System1State.BLOCKED_SURVIVAL)
        if not was_blocked:
            self._emit(
                context,
                "system1.blocked",
                agent_id,
                {
                    "drive": (
                        drive.active_drive.value
                        if drive.active_drive is not None
                        else None
                    ),
                    "reason": reason,
                },
            )

    @staticmethod
    def _clear_plan(context: SystemContext, agent_id: str) -> None:
        if not context.registry.has_component(agent_id, PlanComponent):
            return
        plan = context.registry.get_component(agent_id, PlanComponent)
        cleared_count = plan.clear()
        if cleared_count:
            System1ArbitrationSystem._emit(
                context,
                "plan.cleared",
                agent_id,
                {"reason": "system1_preemption", "cleared_actions": cleared_count},
            )
            System1ArbitrationSystem._clear_activity(context, agent_id)
            if context.registry.has_component(agent_id, PlannerComponent):
                context.registry.get_component(
                    agent_id, PlannerComponent
                ).needs_plan = True

    @staticmethod
    def _clear_affordance_request(context: SystemContext, agent_id: str) -> None:
        if context.registry.has_component(agent_id, AffordanceRequestComponent):
            context.registry.remove_component(agent_id, AffordanceRequestComponent)

    @staticmethod
    def _clear_movement(context: SystemContext, agent_id: str) -> None:
        if not context.registry.has_component(agent_id, MovementComponent):
            return
        movement = context.registry.get_component(agent_id, MovementComponent)
        movement.destination = None
        movement.path = ()
        movement.retry_after_tick = 0
        movement.path_correlation_id = None

    @staticmethod
    def _clear_activity(context: SystemContext, agent_id: str) -> None:
        if not context.registry.has_component(agent_id, ActivityComponent):
            return
        activity = context.registry.get_component(agent_id, ActivityComponent)
        previous = activity.current
        activity.current = ActivityType.IDLE
        activity.previous = None
        activity.movement_override = False
        if previous is not activity.current:
            System1ArbitrationSystem._emit(
                context,
                "activity.changed",
                agent_id,
                {
                    "previous": previous.value,
                    "current": activity.current.value,
                    "reason": "system1_preemption",
                },
            )

    @staticmethod
    def _transition(
        context: SystemContext,
        agent_id: str,
        drive: DriveComponent,
        next_state: System1State,
    ) -> None:
        if drive.state is next_state:
            return
        previous = drive.state
        drive.state = next_state
        System1ArbitrationSystem._emit(
            context,
            "system1.state_changed",
            agent_id,
            {"previous": previous.value, "current": next_state.value},
        )

    @staticmethod
    def _emit(
        context: SystemContext,
        event_type: str,
        agent_id: str,
        payload: dict[str, JsonValue],
    ) -> None:
        context.events.emit(
            event_type,
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload=payload,
        )
