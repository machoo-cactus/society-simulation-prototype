from dataclasses import dataclass

from stage0_sim.domain.components import (
    ActionOrigin,
    ActionType,
    ActivityComponent,
    ActivityType,
    AffordanceExecutionComponent,
    AffordanceRequestComponent,
    ControllerComponent,
    DriveComponent,
    DriveType,
    HomeostasisComponent,
    InteractionExecutionComponent,
    InteractionRequestComponent,
    MovementComponent,
    NavigationComponent,
    NavigationStatus,
    PhysicalStateComponent,
    PlanAction,
    PlanComponent,
    PositionComponent,
    SpatialIndex,
    SpatialLocationComponent,
    System1Configuration,
    System1State,
    TransactionRequestComponent,
    TravelComponent,
)
from stage0_sim.domain.environment import EnvironmentAvailabilityRegistry
from stage0_sim.domain.events import DomainEvent, JsonValue
from stage0_sim.domain.lineage import (
    action_lineage_payload,
    clear_plan_lineage,
    emit_action_lifecycle,
    new_action_instance,
    queue_plan_actions,
)
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.affordances import cancel_affordance
from stage0_sim.domain.systems.interactions import (
    cancel_interaction,
    interaction_approach_anchors,
)
from stage0_sim.domain.systems.spatial_context import (
    local_world_for_agent,
    shares_local_map,
)
from stage0_sim.domain.systems.text_actions import cancel_text_action
from stage0_sim.domain.systems.transactions import cancel_transaction
from stage0_sim.domain.world import (
    AffordanceStation,
    CardinalOrientation,
    CityWorld,
    Coordinate,
    Locator,
    NavigationPlanningError,
    RecursiveRoutePlanner,
    SpaceRegistry,
    TraversalContext,
    WorldMap,
    find_path,
)


def system1_drive_recovered(
    homeostasis: HomeostasisComponent,
    drive: DriveType,
    configuration: System1Configuration,
) -> bool:
    return configuration.thresholds[drive].is_recovered(
        system1_meter_value(homeostasis, drive)
    )


def system1_meter_value(
    homeostasis: HomeostasisComponent,
    drive: DriveType,
) -> float:
    if drive is DriveType.SATIETY:
        return homeostasis.satiety
    if drive is DriveType.ENERGY:
        return homeostasis.energy
    return homeostasis.stress


def resolve_system1(
    context: SystemContext,
    agent_id: str,
    drive: DriveComponent,
) -> None:
    System1ArbitrationSystem()._resolve_transition(context, agent_id, drive)


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
                        self._clear_plan(
                            context,
                            agent_id,
                            preserve_system1_navigation=False,
                        )
                        previous = drive.active_drive
                        if drive.correction_action is not None:
                            emit_action_lifecycle(
                                context,
                                "action.cancelled",
                                agent_id,
                                drive.correction_action,
                                {"reason": "drive_priority_changed"},
                            )
                        cancel_affordance(context, agent_id, "drive_priority_changed")
                        cancel_transaction(
                            context,
                            agent_id,
                            "drive_priority_changed",
                        )
                        self._clear_affordance_request(context, agent_id)
                        self._clear_transaction_request(context, agent_id)
                        cancel_interaction(
                            context,
                            agent_id,
                            "drive_priority_changed",
                        )
                        self._clear_interaction_request(context, agent_id)
                        cancel_text_action(
                            context,
                            agent_id,
                            "drive_priority_changed",
                        )
                        drive.active_drive = selected
                        drive.target_station_id = None
                        drive.target_position = None
                        self._clear_movement(context, agent_id)
                        changed = self._emit(
                            context,
                            "system1.drive_changed",
                            agent_id,
                            {
                                "previous": previous.value if previous is not None else None,
                                "current": selected.value,
                            },
                        )
                        drive.correction_action = new_action_instance(
                            context,
                            agent_id,
                            origin=ActionOrigin.SYSTEM1,
                            specification=PlanAction(
                                configuration.corrective_actions[selected]
                            ),
                            root_correlation_id=changed.event_id,
                        )
                        drive.correction_action_started = False
                        emit_action_lifecycle(
                            context,
                            "action.queued",
                            agent_id,
                            drive.correction_action,
                            causation_id=changed.event_id,
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
        activated = self._emit(
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
        cancel_transaction(context, agent_id, "system1_preemption")
        cancel_interaction(context, agent_id, "system1_preemption")
        cancel_text_action(context, agent_id, "system1_preemption")
        self._clear_affordance_request(context, agent_id)
        self._clear_transaction_request(context, agent_id)
        self._clear_interaction_request(context, agent_id)
        self._clear_activity(context, agent_id)
        drive.correction_action = new_action_instance(
            context,
            agent_id,
            origin=ActionOrigin.SYSTEM1,
            specification=PlanAction(
                configuration.corrective_actions[selected]
            ),
            root_correlation_id=activated.event_id,
        )
        drive.correction_action_started = False
        emit_action_lifecycle(
            context,
            "action.queued",
            agent_id,
            drive.correction_action,
            causation_id=activated.event_id,
        )

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
            if location.local_coordinate is None:
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
        if world is None:
            self._block(
                context,
                agent_id,
                drive,
                "outside_local_correction_context",
            )
            return
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
            drive.target_position = None
            self._block(context, agent_id, drive, "no_reachable_corrective_station")
            return

        station, target_position, path_cost, room_id = selected
        movement = context.registry.get_component(agent_id, MovementComponent)
        if (
            drive.target_station_id != station.id
            or drive.target_position != target_position
        ):
            previous_target = drive.target_station_id
            if context.registry.has_component(
                agent_id, AffordanceExecutionComponent
            ):
                cancel_affordance(context, agent_id, "corrective_station_changed")
            drive.target_station_id = station.id
            drive.target_position = target_position
            payload: dict[str, JsonValue] = {
                "drive": drive.active_drive.value,
                "station_id": station.id,
                "position": target_position.to_payload(),
                "path_cost": path_cost,
            }
            if previous_target is not None:
                payload["previous_station_id"] = previous_target
            payload.update(action_lineage_payload(drive.correction_action))
            selected_event = self._emit(
                context,
                "system1.target_selected",
                agent_id,
                payload,
            )
            if (
                drive.correction_action is not None
                and not drive.correction_action_started
            ):
                emit_action_lifecycle(
                    context,
                    "action.started",
                    agent_id,
                    drive.correction_action,
                    {
                        "drive": drive.active_drive.value,
                        "station_id": station.id,
                    },
                    causation_id=selected_event.event_id,
                )
                drive.correction_action_started = True

        current_location = (
            context.registry.get_component(
                agent_id, SpatialLocationComponent
            ).location
            if context.registry.has_component(
                agent_id, SpatialLocationComponent
            )
            else None
        )
        if (
            current_location is not None
            and current_location.local_coordinate is not None
            and room_id is not None
            and room_id != current_location.place_id
        ):
            self._ensure_correction_navigation(
                context,
                agent_id,
                drive,
                station.id,
            )
            return

        if position.coordinate == target_position:
            movement.destination = None
            movement.path = ()
            self._transition(
                context, agent_id, drive, System1State.EXECUTING_CORRECTION
            )
        else:
            if movement.destination != target_position:
                movement.destination = target_position
                movement.path = ()
                movement.retry_after_tick = 0
                movement.path_correlation_id = (
                    drive.correction_action.root_correlation_id
                    if drive.correction_action is not None
                    else None
                )
                movement.action_instance = drive.correction_action
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
    ) -> tuple[AffordanceStation, Coordinate, float, str | None] | None:
        occupied = frozenset(
            position.coordinate
            for other_id, position in context.registry.query(PositionComponent)
            if other_id != agent_id
            and shares_local_map(context.registry, agent_id, other_id)
        )
        candidate_worlds: list[tuple[str | None, WorldMap]] = [(None, world)]
        current_locator = None
        current_room_id = None
        if (
            context.registry.has_resource(CityWorld)
            and context.registry.has_component(
                agent_id, SpatialLocationComponent
            )
        ):
            city = context.registry.get_resource(CityWorld)
            spatial = context.registry.get_component(
                agent_id, SpatialLocationComponent
            )
            current_locator = spatial.locator
            if current_locator is not None:
                try:
                    current_room = city.room(current_locator.space_id)
                except KeyError:
                    current_room = None
                if current_room is not None:
                    current_room_id = current_room.id
                    candidate_worlds = [
                        (room.id, room.world)
                        for room in city.rooms
                        if room.building_id == current_room.building_id
                    ]
        candidates: list[
            tuple[float, str, str, AffordanceStation, Coordinate]
        ] = []
        for room_id, candidate_world in candidate_worlds:
            for station in candidate_world.stations:
                candidate = System1ArbitrationSystem._station_candidate(
                    context,
                    agent_id,
                    origin,
                    station,
                    candidate_world,
                    corrective_action,
                    room_id=room_id,
                    current_room_id=current_room_id,
                    current_locator=current_locator,
                    occupied=occupied,
                )
                if candidate is not None:
                    candidates.append(candidate)
        if not candidates:
            return None
        path_cost, _, room_sort_id, station, target_position = min(
            candidates,
            key=lambda candidate: (
                candidate[0],
                candidate[1],
                candidate[2],
            ),
        )
        return station, target_position, path_cost, room_sort_id or None

    @staticmethod
    def _station_candidate(
        context: SystemContext,
        agent_id: str,
        origin: Coordinate,
        station: AffordanceStation,
        world: WorldMap,
        corrective_action: str,
        *,
        room_id: str | None,
        current_room_id: str | None,
        current_locator: Locator | None,
        occupied: frozenset[Coordinate],
    ) -> tuple[float, str, str, AffordanceStation, Coordinate] | None:
        available = station.available
        if context.registry.has_resource(EnvironmentAvailabilityRegistry):
            available = context.registry.get_resource(
                EnvironmentAvailabilityRegistry
            ).state(
                station.id,
                base_available=station.available,
            ).available
        if not available or corrective_action not in station.supported_actions:
            return None
        active_count = sum(
            execution.station_id == station.id
            for other_id, execution in context.registry.query(
                AffordanceExecutionComponent
            )
            if other_id != agent_id
        )
        reserved_count = sum(
            other_drive.target_station_id == station.id
            for other_id, other_drive in context.registry.query(
                DriveComponent
            )
            if other_id != agent_id
            and not context.registry.has_component(
                other_id, AffordanceExecutionComponent
            )
        )
        if active_count + reserved_count >= station.capacity:
            return None
        if room_id is None or room_id == current_room_id:
            actor_state = (
                context.registry.get_component(
                    agent_id,
                    PhysicalStateComponent,
                )
                if context.registry.has_component(
                    agent_id,
                    PhysicalStateComponent,
                )
                else None
            )
            index = (
                context.registry.get_resource(SpatialIndex)
                if context.registry.has_resource(SpatialIndex)
                else None
            )
            reachable = []
            for approach in interaction_approach_anchors(
                context.registry,
                station.id,
                station.position,
            ):
                path = find_path(
                    world.grid,
                    origin,
                    approach,
                    occupied,
                    footprint=(
                        actor_state.footprint
                        if actor_state is not None
                        else None
                    ),
                    orientation=(
                        actor_state.pose.orientation
                        if actor_state is not None
                        else CardinalOrientation.NORTH
                    ),
                    spatial_index=index if actor_state is not None else None,
                    room_id=(
                        actor_state.pose.room_id
                        if actor_state is not None
                        else None
                    ),
                    entity_id=agent_id if actor_state is not None else None,
                )
                if path is not None:
                    reachable.append((len(path), approach))
            if not reachable:
                return None
            path_length, approach = min(
                reachable,
                key=lambda item: (
                    item[0],
                    item[1].y,
                    item[1].x,
                ),
            )
            return (
                float(path_length),
                station.id,
                room_id or "",
                station,
                approach,
            )
        if (
            current_locator is None
            or not context.registry.has_resource(SpaceRegistry)
        ):
            return None
        topology = context.registry.get_resource(SpaceRegistry)
        occupied_locators = tuple(
            spatial.locator
            for other_id, spatial in context.registry.query(
                SpatialLocationComponent
            )
            if other_id != agent_id and spatial.locator is not None
        )
        allowed_transition_ids = frozenset(
            transition.id
            for transition in topology.transitions()
            if transition.executor_id == "portal"
            and System1ArbitrationSystem._transition_available(
                context, transition.id, transition.metadata.get("available")
            )
        )
        try:
            route = RecursiveRoutePlanner().plan(
                topology,
                current_locator,
                topology.destination_locators(station.id),
                TraversalContext(
                    character_id=agent_id,
                    occupied_locators=occupied_locators,
                    actor_footprint=(
                        context.registry.get_component(
                            agent_id,
                            PhysicalStateComponent,
                        ).footprint
                        if context.registry.has_component(
                            agent_id,
                            PhysicalStateComponent,
                        )
                        else None
                    ),
                ),
                allowed_transition_ids=allowed_transition_ids,
            )
        except NavigationPlanningError:
            return None
        destination_reference = route.destination.local_reference
        destination_coordinate = station.position
        if isinstance(destination_reference, dict):
            x = destination_reference.get("x")
            y = destination_reference.get("y")
            if (
                isinstance(x, int)
                and not isinstance(x, bool)
                and isinstance(y, int)
                and not isinstance(y, bool)
            ):
                destination_coordinate = Coordinate(x, y)
        return (
            sum(leg.cost for leg in route.legs),
            station.id,
            room_id,
            station,
            destination_coordinate,
        )

    @staticmethod
    def _transition_available(
        context: SystemContext,
        transition_id: str,
        raw_base_available: object,
    ) -> bool:
        base_available = (
            raw_base_available
            if isinstance(raw_base_available, bool)
            else True
        )
        if not context.registry.has_resource(
            EnvironmentAvailabilityRegistry
        ):
            return base_available
        return context.registry.get_resource(
            EnvironmentAvailabilityRegistry
        ).state(
            transition_id.removesuffix(":reverse"),
            base_available=base_available,
        ).available

    def _ensure_correction_navigation(
        self,
        context: SystemContext,
        agent_id: str,
        drive: DriveComponent,
        station_id: str,
    ) -> None:
        if not (
            context.registry.has_component(agent_id, PlanComponent)
            and context.registry.has_component(
                agent_id, NavigationComponent
            )
        ):
            self._block(
                context,
                agent_id,
                drive,
                "navigation_not_configured",
            )
            return
        plan = context.registry.get_component(agent_id, PlanComponent)
        navigation = context.registry.get_component(
            agent_id, NavigationComponent
        )
        if (
            plan.current is not None
            and plan.current.origin is ActionOrigin.SYSTEM1
            and plan.current.action is ActionType.NAVIGATE
            and plan.current.target == station_id
            and navigation.status
            in {
                NavigationStatus.REQUESTED,
                NavigationStatus.PLANNED,
                NavigationStatus.NAVIGATING,
            }
        ):
            self._transition(
                context,
                agent_id,
                drive,
                System1State.NAVIGATING_TO_CORRECTION,
            )
            return
        if navigation.status is NavigationStatus.FAILED:
            self._block(
                context,
                agent_id,
                drive,
                navigation.failure_reason or "corrective_route_not_found",
            )
            return
        self._clear_movement(context, agent_id)
        queued = queue_plan_actions(
            context,
            agent_id,
            plan,
            [PlanAction(ActionType.NAVIGATE, target=station_id)],
            origin=ActionOrigin.SYSTEM1,
            root_correlation_id=(
                drive.correction_action.root_correlation_id
                if drive.correction_action is not None
                else None
            ),
        )
        plan.current = plan.queue.pop(0)
        plan.current_started = False
        navigation.request(
            station_id,
            reason="system1",
            action_instance=queued[0],
        )
        self._transition(
            context,
            agent_id,
            drive,
            System1State.NAVIGATING_TO_CORRECTION,
        )

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
        return system1_drive_recovered(homeostasis, drive, configuration)

    @staticmethod
    def _meter_value(homeostasis: HomeostasisComponent, drive: DriveType) -> float:
        return system1_meter_value(homeostasis, drive)

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
        resolve_system1(context, agent_id, drive)

    def _resolve_transition(
        self,
        context: SystemContext,
        agent_id: str,
        drive: DriveComponent,
    ) -> None:
        resolved_drive = drive.active_drive
        self._clear_movement(context, agent_id)
        self._transition(context, agent_id, drive, System1State.RECOVERED)
        resolved = self._emit(
            context,
            "system1.resolved",
            agent_id,
            {
                "drive": (
                    resolved_drive.value if resolved_drive is not None else None
                ),
                **action_lineage_payload(drive.correction_action),
            },
        )
        if drive.correction_action is not None:
            emit_action_lifecycle(
                context,
                "action.completed",
                agent_id,
                drive.correction_action,
                {
                    "drive": (
                        resolved_drive.value
                        if resolved_drive is not None
                        else None
                    )
                },
                causation_id=resolved.event_id,
            )
        drive.correction_action = None
        drive.correction_action_started = False
        drive.active_drive = None
        drive.target_station_id = None
        drive.target_position = None
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
                    **action_lineage_payload(drive.correction_action),
                },
            )

    @staticmethod
    def _clear_plan(
        context: SystemContext,
        agent_id: str,
        *,
        preserve_system1_navigation: bool = True,
    ) -> None:
        if not context.registry.has_component(agent_id, PlanComponent):
            return
        plan = context.registry.get_component(agent_id, PlanComponent)
        if (
            preserve_system1_navigation
            and plan.current is not None
            and plan.current.origin is ActionOrigin.SYSTEM1
            and plan.current.action is ActionType.NAVIGATE
        ):
            return
        if (
            context.registry.has_component(agent_id, NavigationComponent)
            and context.registry.get_component(
                agent_id,
                NavigationComponent,
            ).status
            in {
                NavigationStatus.REQUESTED,
                NavigationStatus.PLANNED,
                NavigationStatus.NAVIGATING,
            }
        ):
            navigation = context.registry.get_component(
                agent_id,
                NavigationComponent,
            )
            navigation.status = NavigationStatus.INTERRUPTED
            context.events.emit(
                "navigation.interrupted",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "target_id": navigation.target_id,
                    "reason": "system1_preemption",
                    "completed_route_legs": (
                        navigation.completed_route_legs
                    ),
                    **action_lineage_payload(navigation.action_instance),
                },
                correlation_id=(
                    navigation.action_instance.root_correlation_id
                    if navigation.action_instance is not None
                    else navigation.correlation_id
                ),
            )
        cleared_count = clear_plan_lineage(
            context,
            agent_id,
            plan,
            reason="system1_preemption",
            current_status="action.cancelled",
        )
        if cleared_count:
            System1ArbitrationSystem._clear_activity(context, agent_id)

    @staticmethod
    def _clear_affordance_request(context: SystemContext, agent_id: str) -> None:
        if context.registry.has_component(agent_id, AffordanceRequestComponent):
            context.registry.remove_component(agent_id, AffordanceRequestComponent)

    @staticmethod
    def _clear_transaction_request(
        context: SystemContext,
        agent_id: str,
    ) -> None:
        if context.registry.has_component(
            agent_id, TransactionRequestComponent
        ):
            context.registry.remove_component(
                agent_id, TransactionRequestComponent
            )

    @staticmethod
    def _clear_interaction_request(
        context: SystemContext,
        agent_id: str,
    ) -> None:
        if context.registry.has_component(
            agent_id,
            InteractionRequestComponent,
        ):
            context.registry.remove_component(
                agent_id,
                InteractionRequestComponent,
            )
        if context.registry.has_component(
            agent_id,
            InteractionExecutionComponent,
        ):
            context.registry.remove_component(
                agent_id,
                InteractionExecutionComponent,
            )

    @staticmethod
    def _clear_movement(context: SystemContext, agent_id: str) -> None:
        if not context.registry.has_component(agent_id, MovementComponent):
            return
        movement = context.registry.get_component(agent_id, MovementComponent)
        movement.destination = None
        movement.path = ()
        movement.retry_after_tick = 0
        movement.path_correlation_id = None
        movement.action_instance = None

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
    ) -> DomainEvent:
        return context.events.emit(
            event_type,
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload=payload,
        )
