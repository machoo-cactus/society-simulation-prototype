from dataclasses import dataclass

from stage0_sim.domain.components import (
    ActionInstance,
    ActionType,
    DriveComponent,
    PlanComponent,
    PositionComponent,
    SpatialLocationComponent,
    System1State,
    TravelComponent,
)
from stage0_sim.domain.environment import (
    AvailabilityReason,
    AvailabilityState,
    EnvironmentAvailabilityRegistry,
    WeatherRuntime,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.lineage import (
    action_lineage_payload,
    emit_action_lifecycle,
)
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.world import (
    CityWorld,
    SpatialScale,
    TravelMode,
    TravelStatus,
    VehicleRegistry,
    WorldLocation,
    find_transport_route,
)


@dataclass(frozen=True, slots=True)
class TravelSystem:
    name: str = "travel"
    order: int = 175

    def update(self, context: SystemContext) -> None:
        if not context.registry.has_resource(CityWorld):
            return
        city = context.registry.get_resource(CityWorld)
        for agent_id in context.registry.query_entities(
            TravelComponent,
            SpatialLocationComponent,
            DriveComponent,
        ):
            travel = context.registry.get_component(agent_id, TravelComponent)
            drive = context.registry.get_component(agent_id, DriveComponent)
            if (
                drive.state is not System1State.NORMAL
                and travel.status is TravelStatus.TRAVELLING
            ):
                travel.interruption_requested = True
            if travel.status is TravelStatus.ROUTE_PLANNED:
                self._start(context, agent_id, travel)
            if travel.status is TravelStatus.TRAVELLING:
                self._advance(context, city, agent_id, travel)

    def request(
        self,
        context: SystemContext,
        agent_id: str,
        destination_id: str,
        mode: TravelMode,
        *,
        entrance_transition_id: str | None = None,
        outbound_transition_id: str | None = None,
        origin_network_node_id: str | None = None,
        allowed_edge_ids: frozenset[str] | None = None,
        action_instance: ActionInstance | None = None,
    ) -> bool:
        city = context.registry.get_resource(CityWorld)
        location = context.registry.get_component(
            agent_id, SpatialLocationComponent
        )
        travel = context.registry.get_component(agent_id, TravelComponent)
        travel.action_instance = action_instance
        travel.correlation_id = (
            action_instance.root_correlation_id
            if action_instance is not None
            else None
        )
        travel.failure_reason = None
        try:
            destination = city.building(destination_id)
        except KeyError:
            destination = None
        outdoor = None
        if destination is None:
            try:
                outdoor = city.outdoor_place(destination_id)
            except KeyError:
                self._failure(
                    context,
                    agent_id,
                    destination_id,
                    mode,
                    "unknown_destination",
                )
                return False
        if travel.status in {TravelStatus.ROUTE_PLANNED, TravelStatus.TRAVELLING}:
            self._failure(
                context,
                agent_id,
                destination_id,
                mode,
                "travel_in_progress",
            )
            return False
        destination_state = _availability_state(context, destination_id)
        if not destination_state.available:
            self._failure(
                context,
                agent_id,
                destination_id,
                mode,
                destination_state.reason.value,
            )
            return False
        requested_entrance_id = (
            entrance_transition_id.removesuffix(":reverse")
            if entrance_transition_id is not None
            else None
        )
        selected_entrance = None
        if destination is not None and requested_entrance_id is not None:
            selected_entrance = next(
                (
                    entrance
                    for entrance in destination.entrances
                    if entrance.id == requested_entrance_id
                ),
                None,
            )
            if selected_entrance is None:
                self._failure(
                    context,
                    agent_id,
                    destination_id,
                    mode,
                    "invalid_destination_entrance",
                )
                return False
        if destination is not None and selected_entrance is None:
            selected_entrance = destination.entrances[0]
        destination_node = (
            selected_entrance.network_node_id
            if selected_entrance is not None
            else outdoor.network_node_id
            if outdoor is not None
            else ""
        )
        origin_node, origin_failure = self._origin_node(
            city,
            location.location,
            outbound_transition_id=outbound_transition_id,
            origin_network_node_id=origin_network_node_id,
        )
        if origin_failure is not None:
            self._failure(
                context,
                agent_id,
                destination_id,
                mode,
                origin_failure,
            )
            return False
        if origin_node is None:
            self._failure(
                context,
                agent_id,
                destination_id,
                mode,
                "route_not_found",
            )
            return False
        vehicle_id = None
        if mode in {TravelMode.CAR, TravelMode.CYCLE}:
            vehicle_states = context.registry.get_resource(VehicleRegistry).states
            vehicle = next(
                (
                    item
                    for item in sorted(city.vehicles, key=lambda item: item.id)
                    if item.vehicle_type is mode
                    and _availability_state(context, item.id).available
                    and vehicle_states[item.id].driver_id is None
                    and vehicle_states[item.id].network_node_id in {
                        origin_node,
                        *(
                            edge.to_node_id
                            for edge in city.edges
                            if edge.from_node_id == origin_node
                            and TravelMode.WALK in edge.allowed_modes
                        ),
                    }
                ),
                None,
            )
            if vehicle is None:
                self._failure(
                    context,
                    agent_id,
                    destination_id,
                    mode,
                    "vehicle_not_available",
                )
                return False
            vehicle_id = vehicle.id
        dynamic_edge_ids = frozenset(
            edge.id
            for edge in city.edges
            if _availability_state(context, edge.id).available
        )
        effective_allowed_edge_ids = (
            dynamic_edge_ids
            if allowed_edge_ids is None
            else dynamic_edge_ids & allowed_edge_ids
        )
        route = find_transport_route(
            city,
            origin_node,
            destination_node,
            mode,
            allowed_edge_ids=effective_allowed_edge_ids,
        )
        if route is None:
            self._failure(
                context,
                agent_id,
                destination_id,
                mode,
                "route_not_found",
            )
            return False
        requested = context.events.emit(
            "travel.requested",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "origin_place_id": location.location.place_id,
                "destination_id": destination_id,
                "mode": mode.value,
                **action_lineage_payload(travel.action_instance),
            },
            correlation_id=(
                travel.action_instance.root_correlation_id
                if travel.action_instance is not None
                else None
            ),
        )
        travel.destination_id = destination_id
        travel.requested_mode = mode
        travel.route = route
        travel.current_leg_index = 0
        travel.leg_elapsed_seconds = 0.0
        travel.status = TravelStatus.ROUTE_PLANNED
        travel.vehicle_id = vehicle_id
        travel.destination_entrance_id = (
            selected_entrance.id
            if selected_entrance is not None
            else None
        )
        travel.failure_reason = None
        travel.correlation_id = (
            travel.action_instance.root_correlation_id
            if travel.action_instance is not None
            else requested.event_id
        )
        context.events.emit(
            "travel.route_planned",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "destination_id": destination_id,
                "mode": mode.value,
                "expected_seconds": round(
                    sum(
                        leg.duration_seconds
                        / _travel_speed_multiplier(context, leg.mode)
                        for leg in route
                    ),
                    12,
                ),
                "legs": [
                    {
                        "edge_id": leg.edge_id,
                        "from_node_id": leg.from_node_id,
                        "to_node_id": leg.to_node_id,
                        "mode": leg.mode.value,
                        "duration_seconds": leg.duration_seconds,
                    }
                    for leg in route
                ],
                "vehicle_id": vehicle_id,
                **action_lineage_payload(travel.action_instance),
            },
            causation_id=requested.event_id,
            correlation_id=travel.correlation_id,
        )
        return True

    @staticmethod
    def _start(
        context: SystemContext,
        agent_id: str,
        travel: TravelComponent,
    ) -> None:
        travel.status = TravelStatus.TRAVELLING
        location = context.registry.get_component(
            agent_id, SpatialLocationComponent
        ).location
        if location.local_coordinate is not None:
            city = context.registry.get_resource(CityWorld)
            building = city.building_for_room(location.place_id)
            context.events.emit(
                "building.exited",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "building_id": building.id,
                    "room_id": location.place_id,
                },
                correlation_id=travel.correlation_id,
            )
        context.events.emit(
            "travel.started",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "destination_id": travel.destination_id,
                "mode": (
                    travel.requested_mode.value
                    if travel.requested_mode is not None
                    else None
                ),
                "vehicle_id": travel.vehicle_id,
                **action_lineage_payload(travel.action_instance),
            },
            correlation_id=travel.correlation_id,
        )
        if travel.route:
            TravelSystem._emit_leg(
                context, "travel.leg_started", agent_id, travel
            )

    @staticmethod
    def _advance(
        context: SystemContext,
        city: CityWorld,
        agent_id: str,
        travel: TravelComponent,
    ) -> None:
        if not travel.route:
            TravelSystem._arrive(context, city, agent_id, travel)
            return
        leg = travel.route[travel.current_leg_index]
        remaining = leg.duration_seconds - travel.leg_elapsed_seconds
        multiplier = _travel_speed_multiplier(context, leg.mode)
        effective_step = context.clock.dt * multiplier
        travel.leg_elapsed_seconds = (
            leg.duration_seconds
            if remaining <= effective_step
            else round(travel.leg_elapsed_seconds + effective_step, 12)
        )
        progress = min(1.0, travel.leg_elapsed_seconds / leg.duration_seconds)
        location = context.registry.get_component(
            agent_id, SpatialLocationComponent
        )
        location.location = WorldLocation(
            scale=SpatialScale.CITY,
            place_id=city.id,
            network_node_id=(
                leg.to_node_id if progress >= 1.0 else None
            ),
            edge_id=leg.edge_id if progress < 1.0 else None,
            edge_progress=round(progress, 12) if progress < 1.0 else None,
        )
        if (
            travel.vehicle_id is not None
            and leg.mode in {TravelMode.CAR, TravelMode.CYCLE}
        ):
            vehicle = context.registry.get_resource(VehicleRegistry).states[
                travel.vehicle_id
            ]
            vehicle.network_node_id = (
                leg.to_node_id if progress >= 1.0 else None
            )
            vehicle.edge_id = leg.edge_id if progress < 1.0 else None
            vehicle.edge_progress = (
                round(progress, 12) if progress < 1.0 else None
            )
            vehicle.driver_id = agent_id
            context.events.emit(
                "vehicle.moved",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "vehicle_id": travel.vehicle_id,
                    "weather_speed_multiplier": multiplier,
                    "weather_condition": _weather_condition(context),
                    "edge_id": vehicle.edge_id,
                    "edge_progress": vehicle.edge_progress,
                    "network_node_id": vehicle.network_node_id,
                },
                correlation_id=travel.correlation_id,
            )
        context.events.emit(
            "travel.progressed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "destination_id": travel.destination_id,
                "edge_id": leg.edge_id,
                "mode": leg.mode.value,
                "progress": round(progress, 12),
                "vehicle_id": travel.vehicle_id,
                **action_lineage_payload(travel.action_instance),
            },
            correlation_id=travel.correlation_id,
        )
        if travel.action_instance is not None:
            emit_action_lifecycle(
                context,
                "action.progressed",
                agent_id,
                travel.action_instance,
                {
                    "destination_id": travel.destination_id,
                    "edge_id": leg.edge_id,
                    "progress": round(progress, 12),
                },
            )
        if progress < 1.0:
            return
        TravelSystem._emit_leg(
            context, "travel.leg_completed", agent_id, travel
        )
        if travel.interruption_requested:
            TravelSystem._cancel_at_node(context, agent_id, travel, leg.to_node_id)
            return
        travel.current_leg_index += 1
        travel.leg_elapsed_seconds = 0.0
        if travel.current_leg_index >= len(travel.route):
            destination_state = _availability_state(
                context, travel.destination_id or ""
            )
            if not destination_state.available:
                TravelSystem._block_at_node(
                    context,
                    agent_id,
                    travel,
                    leg.to_node_id,
                    destination_state.reason.value,
                )
                return
            TravelSystem._arrive(context, city, agent_id, travel)
            return
        next_leg = travel.route[travel.current_leg_index]
        next_edge_state = _availability_state(context, next_leg.edge_id)
        if not next_edge_state.available:
            TravelSystem._block_at_node(
                context,
                agent_id,
                travel,
                leg.to_node_id,
                next_edge_state.reason.value,
            )
            return
        TravelSystem._emit_leg(
            context, "travel.leg_started", agent_id, travel
        )

    @staticmethod
    def _arrive(
        context: SystemContext,
        city: CityWorld,
        agent_id: str,
        travel: TravelComponent,
    ) -> None:
        destination_id = travel.destination_id
        if destination_id is None:
            return
        try:
            destination = city.building(destination_id)
        except KeyError:
            destination = None
        if destination is not None:
            entrance = next(
                (
                    candidate
                    for candidate in destination.entrances
                    if candidate.id == travel.destination_entrance_id
                ),
                destination.entrances[0],
            )
            context.registry.get_component(
                agent_id, SpatialLocationComponent
            ).location = WorldLocation(
                scale=SpatialScale.BUILDING,
                place_id=entrance.room_id,
                local_coordinate=entrance.local_coordinate,
            )
            if context.registry.has_component(agent_id, PositionComponent):
                context.registry.get_component(
                    agent_id, PositionComponent
                ).coordinate = entrance.local_coordinate
            context.events.emit(
                "building.entered",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "building_id": destination.id,
                    "entrance_id": entrance.id,
                    "room_id": entrance.room_id,
                },
                correlation_id=travel.correlation_id,
            )
        else:
            outdoor = city.outdoor_place(destination_id)
            context.registry.get_component(
                agent_id, SpatialLocationComponent
            ).location = WorldLocation(
                scale=SpatialScale.NEIGHBORHOOD,
                place_id=outdoor.id,
                network_node_id=outdoor.network_node_id,
            )
        travel.status = TravelStatus.ARRIVED
        if travel.vehicle_id is not None:
            vehicle = context.registry.get_resource(VehicleRegistry).states[
                travel.vehicle_id
            ]
            vehicle.driver_id = None
            context.events.emit(
                "vehicle.exited",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={"vehicle_id": travel.vehicle_id},
                correlation_id=travel.correlation_id,
            )
        context.events.emit(
            "travel.arrived",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "destination_id": destination_id,
                "mode": (
                    travel.requested_mode.value
                    if travel.requested_mode is not None
                    else None
                ),
                "vehicle_id": travel.vehicle_id,
                **action_lineage_payload(travel.action_instance),
            },
            correlation_id=travel.correlation_id,
        )
        TravelSystem._complete_plan(context, agent_id)

    @staticmethod
    def _cancel_at_node(
        context: SystemContext,
        agent_id: str,
        travel: TravelComponent,
        node_id: str,
    ) -> None:
        travel.status = TravelStatus.CANCELLED
        travel.failure_reason = "system1_preemption"
        context.events.emit(
            "travel.interrupted",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "destination_id": travel.destination_id,
                "safe_node_id": node_id,
                "reason": "system1_preemption",
                **action_lineage_payload(travel.action_instance),
            },
            correlation_id=travel.correlation_id,
        )
        TravelSystem._complete_plan(context, agent_id)

    @staticmethod
    def _block_at_node(
        context: SystemContext,
        agent_id: str,
        travel: TravelComponent,
        node_id: str,
        reason: str,
    ) -> None:
        travel.status = TravelStatus.BLOCKED
        travel.failure_reason = reason
        if travel.vehicle_id is not None:
            context.registry.get_resource(VehicleRegistry).states[
                travel.vehicle_id
            ].driver_id = None
        event_type = (
            "building.entry_blocked"
            if travel.destination_id is not None
            and reason in {
                "base_unavailable",
                "closed_by_schedule",
                "closed_by_weather",
            }
            else "travel.blocked"
        )
        context.events.emit(
            event_type,
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "destination_id": travel.destination_id,
                "safe_node_id": node_id,
                "reason": reason,
                **action_lineage_payload(travel.action_instance),
            },
            correlation_id=travel.correlation_id,
        )
        TravelSystem._complete_plan(context, agent_id)

    @staticmethod
    def _complete_plan(context: SystemContext, agent_id: str) -> None:
        if not context.registry.has_component(agent_id, PlanComponent):
            return
        plan = context.registry.get_component(agent_id, PlanComponent)
        if (
            plan.current is not None
            and plan.current.action is ActionType.NAVIGATE
        ):
            return
        from stage0_sim.domain.systems.plans import PlanExecutionSystem

        travel = context.registry.get_component(agent_id, TravelComponent)
        if travel.status is TravelStatus.ARRIVED:
            PlanExecutionSystem()._complete(context, agent_id, plan)
        elif travel.status is TravelStatus.CANCELLED:
            PlanExecutionSystem()._interrupt(
                context,
                agent_id,
                plan,
                travel.failure_reason or "system1_preemption",
            )
        elif travel.status is TravelStatus.BLOCKED:
            PlanExecutionSystem()._fail(
                context,
                agent_id,
                plan,
                travel.failure_reason or "travel_blocked",
            )
        travel.action_instance = None

    @staticmethod
    def _origin_node(
        city: CityWorld,
        location: WorldLocation,
        *,
        outbound_transition_id: str | None = None,
        origin_network_node_id: str | None = None,
    ) -> tuple[str | None, str | None]:
        if location.local_coordinate is not None:
            room = city.room(location.place_id)
            building = city.building(room.building_id)
            if outbound_transition_id is not None:
                entrance_id = outbound_transition_id.removesuffix(":reverse")
                entrance = next(
                    (
                        candidate
                        for candidate in building.entrances
                        if candidate.id == entrance_id
                    ),
                    None,
                )
                if entrance is None:
                    return None, "invalid_origin_entrance"
                if (
                    entrance.room_id != room.id
                    or location.local_coordinate
                    != entrance.local_coordinate
                    or (
                        origin_network_node_id is not None
                        and entrance.network_node_id
                        != origin_network_node_id
                    )
                ):
                    return None, "origin_entrance_mismatch"
                return entrance.network_node_id, None
            if origin_network_node_id is not None:
                entrance = next(
                    (
                        candidate
                        for candidate in building.entrances
                        if candidate.network_node_id == origin_network_node_id
                        and candidate.room_id == room.id
                        and (
                            location.local_coordinate is None
                            or candidate.local_coordinate
                            == location.local_coordinate
                        )
                    ),
                    None,
                )
                if entrance is None:
                    return None, "origin_entrance_mismatch"
                return entrance.network_node_id, None
            if location.local_coordinate is not None:
                entrance = next(
                    (
                        candidate
                        for candidate in sorted(
                            building.entrances,
                            key=lambda item: item.id,
                        )
                        if candidate.local_coordinate
                        == location.local_coordinate
                        and candidate.room_id == room.id
                    ),
                    None,
                )
                if entrance is not None:
                    return entrance.network_node_id, None
            return None, "origin_not_at_entrance"
        if outbound_transition_id is not None:
            return None, "invalid_origin_entrance"
        if (
            origin_network_node_id is not None
            and location.network_node_id != origin_network_node_id
        ):
            return None, "origin_network_node_mismatch"
        if location.network_node_id is not None:
            return location.network_node_id, None
        return None, None

    @staticmethod
    def _emit_leg(
        context: SystemContext,
        event_type: str,
        agent_id: str,
        travel: TravelComponent,
    ) -> None:
        leg = travel.route[travel.current_leg_index]
        previous_mode = (
            travel.route[travel.current_leg_index - 1].mode
            if travel.current_leg_index > 0
            else None
        )
        if event_type == "travel.leg_started" and previous_mode is not leg.mode:
            context.events.emit(
                "travel.mode_changed",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "previous": (
                        previous_mode.value
                        if previous_mode is not None
                        else None
                    ),
                    "current": leg.mode.value,
                    "vehicle_id": travel.vehicle_id,
                    **action_lineage_payload(travel.action_instance),
                },
                correlation_id=travel.correlation_id,
            )
            if (
                travel.vehicle_id is not None
                and leg.mode in {TravelMode.CAR, TravelMode.CYCLE}
            ):
                context.events.emit(
                    "vehicle.boarded",
                    simulation_tick=context.clock.tick,
                    simulation_time=context.clock.simulation_time,
                    agent_id=agent_id,
                    payload={
                        "vehicle_id": travel.vehicle_id,
                        "role": "DRIVER",
                        "mode": leg.mode.value,
                    },
                    correlation_id=travel.correlation_id,
                )
            if leg.mode is TravelMode.METRO:
                context.events.emit(
                    "metro.boarded",
                    simulation_tick=context.clock.tick,
                    simulation_time=context.clock.simulation_time,
                    agent_id=agent_id,
                    payload={
                        "from_node_id": leg.from_node_id,
                        "to_node_id": leg.to_node_id,
                        "edge_id": leg.edge_id,
                    },
                    correlation_id=travel.correlation_id,
                )
        context.events.emit(
            event_type,
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "leg_index": travel.current_leg_index,
                "edge_id": leg.edge_id,
                "from_node_id": leg.from_node_id,
                "to_node_id": leg.to_node_id,
                "mode": leg.mode.value,
                "duration_seconds": leg.duration_seconds,
                **action_lineage_payload(travel.action_instance),
            },
            correlation_id=travel.correlation_id,
        )
        if event_type == "travel.leg_completed" and leg.mode is TravelMode.METRO:
            context.events.emit(
                "metro.alighted",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "node_id": leg.to_node_id,
                    "edge_id": leg.edge_id,
                    **action_lineage_payload(travel.action_instance),
                },
                correlation_id=travel.correlation_id,
            )

    @staticmethod
    def _failure(
        context: SystemContext,
        agent_id: str,
        destination_id: str,
        mode: TravelMode,
        reason: str,
    ) -> None:
        if context.registry.has_component(agent_id, TravelComponent):
            travel = context.registry.get_component(
                agent_id,
                TravelComponent,
            )
            travel.failure_reason = reason
        else:
            travel = None
        payload: dict[str, JsonValue] = {
            "destination_id": destination_id,
            "mode": mode.value,
            "reason": reason,
            **action_lineage_payload(
                travel.action_instance if travel is not None else None
            ),
        }
        context.events.emit(
            "travel.route_failed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload=payload,
            correlation_id=(
                travel.correlation_id if travel is not None else None
            ),
        )


def _availability_state(
    context: SystemContext,
    resource_id: str,
) -> AvailabilityState:
    if context.registry.has_resource(EnvironmentAvailabilityRegistry):
        return context.registry.get_resource(
            EnvironmentAvailabilityRegistry
        ).state(resource_id)
    return AvailabilityState(True, AvailabilityReason.OPEN)


def _travel_speed_multiplier(
    context: SystemContext,
    mode: TravelMode,
) -> float:
    if not context.registry.has_resource(WeatherRuntime):
        return 1.0
    effects = context.registry.get_resource(WeatherRuntime).effects
    if mode is TravelMode.WALK:
        return effects.walking_speed_multiplier
    if mode is TravelMode.CYCLE:
        return effects.cycling_speed_multiplier
    return 1.0


def _weather_condition(context: SystemContext) -> str | None:
    if not context.registry.has_resource(WeatherRuntime):
        return None
    return context.registry.get_resource(WeatherRuntime).current.condition.value
