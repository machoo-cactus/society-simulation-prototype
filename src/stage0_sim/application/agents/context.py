from dataclasses import replace
from typing import Literal, cast

from stage0_sim.application.agents.contracts import (
    CalendarTimeObservation,
    CharacterObservation,
    EnvironmentObservation,
    ObservationFact,
    ObservedGoal,
    ObservedItemAmount,
    ObservedOffer,
    ObservedPossession,
    ObservedServiceRequest,
    ObservedTarget,
)
from stage0_sim.application.environment import EnvironmentInformationService
from stage0_sim.application.navigation import NavigationService
from stage0_sim.application.perception.renderer import (
    DeterministicPerceptionRenderer,
)
from stage0_sim.domain.calendar import SimulationCalendar
from stage0_sim.domain.components import (
    ActivityComponent,
    CarriedLoadComponent,
    CharacterEmbodimentComponent,
    CharacterProfileComponent,
    ControllerComponent,
    CustodyComponent,
    EffectiveSensesComponent,
    EquipmentStateComponent,
    GoalComponent,
    HomeostasisComponent,
    NpcComponent,
    ObjectIntrinsicComponent,
    OpenableComponent,
    PerceptionComponent,
    PhysicalObjectIdentityComponent,
    PhysicalRelationKind,
    PositionComponent,
    PossessionsComponent,
    ScentSourceComponent,
    SpatialLocationComponent,
    SpatialParentRelationComponent,
    TransactionRequestComponent,
    WearableComponent,
)
from stage0_sim.domain.economy import (
    ItemAmount,
    ItemCatalog,
    TransactionOffer,
    TransactionPointRegistry,
    can_debit,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.environment import EnvironmentAvailabilityRegistry
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.interactions import (
    available_interactions,
    available_physical_actions,
)
from stage0_sim.domain.systems.spatial_context import local_world_for_agent
from stage0_sim.domain.world import CityWorld, TravelMode, WorldGrid, WorldMap


def build_character_observation(
    context: SystemContext, agent_id: str
) -> CharacterObservation:
    registry = context.registry
    profile = registry.get_component(agent_id, CharacterProfileComponent)
    controller = registry.get_component(agent_id, ControllerComponent)
    position = registry.get_component(agent_id, PositionComponent)
    activity = registry.get_component(agent_id, ActivityComponent)
    perception = registry.get_component(agent_id, PerceptionComponent)
    world = local_world_for_agent(registry, agent_id)
    if world is None:
        world = WorldMap(WorldGrid(1, 1))
    if registry.has_component(agent_id, NpcComponent):
        return _build_npc_observation(
            context,
            agent_id,
            profile=profile,
            controller=controller,
            position=position,
            activity=activity,
            perception=perception,
            world=world,
        )
    homeostasis = registry.get_component(agent_id, HomeostasisComponent)
    renderer = DeterministicPerceptionRenderer()
    known_characters = set(perception.visible_now) | set(perception.knowledge)
    navigation = registry.get_resource(NavigationService)
    targets = [
        ObservedTarget(
            id=destination.id,
            kind=cast(
                Literal[
                    "zone",
                    "station",
                    "transaction_point",
                    "building",
                    "outdoor",
                    "room",
                    "physical_object",
                ],
                destination.kind,
            ),
            name=destination.name,
            supported_actions=destination.supported_actions,
            offers=tuple(
                ObservedOffer(
                    id=offer.id,
                    name=offer.name,
                    character_gives=tuple(
                        _observed_item_amount(registry, amount)
                        for amount in offer.character_gives
                    ),
                    character_receives=tuple(
                        _observed_item_amount(registry, amount)
                        for amount in offer.character_receives
                    ),
                    duration=offer.duration,
                    available=_offer_available(
                        registry,
                        agent_id,
                        destination.id,
                        offer,
                    ),
                )
                for offer in destination.offers
            ),
            available=destination.available,
        )
        for destination in navigation.known_topology.destinations(agent_id)
        if destination.kind
        in {
            "room",
            "zone",
            "station",
            "transaction_point",
            "building",
            "outdoor",
        }
    ]
    targets_by_id = {target.id: target for target in targets}
    for target_id in sorted(perception.visible_objects_now):
        if not registry.has_component(
            target_id,
            PhysicalObjectIdentityComponent,
        ):
            continue
        identity = registry.get_component(
            target_id,
            PhysicalObjectIdentityComponent,
        )
        existing = targets_by_id.get(target_id)
        physical_actions = available_physical_actions(
            registry,
            agent_id,
            target_id,
        )
        interactions = available_interactions(
            registry,
            agent_id,
            target_id,
        )
        if existing is None:
            targets_by_id[target_id] = ObservedTarget(
                id=target_id,
                kind="physical_object",
                name=identity.name,
                supported_actions=physical_actions,
                available_interactions=interactions,
                public_state=_observed_physical_state(
                    registry,
                    agent_id,
                    target_id,
                    recognized=target_id in perception.recognized_objects_now,
                ),
            )
        else:
            targets_by_id[target_id] = replace(
                existing,
                supported_actions=tuple(
                    dict.fromkeys(
                        (*existing.supported_actions, *physical_actions)
                    )
                ),
                available_interactions=interactions,
                public_state=_observed_physical_state(
                    registry,
                    agent_id,
                    target_id,
                    recognized=target_id in perception.recognized_objects_now,
                ),
            )
    targets = [
        targets_by_id[target_id]
        for target_id in sorted(targets_by_id)
    ]
    available_travel_modes: tuple[str, ...] = (TravelMode.WALK.value,)
    spatial_payload: dict[str, JsonValue] | None = None
    if registry.has_resource(CityWorld):
        city = registry.get_resource(CityWorld)
        available = {TravelMode.WALK}
        available.update(vehicle.vehicle_type for vehicle in city.vehicles)
        if any(
            TravelMode.METRO in edge.allowed_modes for edge in city.edges
        ):
            available.add(TravelMode.METRO)
        available_travel_modes = tuple(
            mode.value for mode in TravelMode if mode in available
        )
    spatial_payload = _spatial_payload(registry, agent_id)
    for target_id in sorted(known_characters):
        target_profile = (
            registry.get_component(target_id, CharacterProfileComponent)
            if registry.has_component(target_id, CharacterProfileComponent)
            else None
        )
        knowledge = perception.knowledge.get(target_id)
        targets.append(
            ObservedTarget(
                id=target_id,
                kind="character",
                name=(
                    target_profile.display_name
                    if target_profile is not None
                    else target_id
                ),
                last_observed_tick=(
                    knowledge.observed_tick if knowledge is not None else None
                ),
            )
        )
    facts = tuple(
        ObservationFact(
            fact_id=item.fact.fact_id,
            fact_type=item.fact.fact_type,
            text=renderer.render_fact(item),
            tick=item.fact.tick,
            subject_id=item.fact.subject_id,
        )
        for item in perception.inbox
    )
    perception.inbox.clear()
    zone = world.zone_at(position.coordinate)
    calendar_time = None
    environment = None
    if registry.has_resource(EnvironmentInformationService):
        environment_result = registry.get_resource(
            EnvironmentInformationService
        ).query(
            agent_id,
            context.clock.simulation_time,
            availability_resource_ids=frozenset(
                str(target.id)
                for target in targets
                if target.kind
                in {
                    "zone",
                    "station",
                    "transaction_point",
                    "building",
                    "outdoor",
                }
            ),
        )
        environment = EnvironmentObservation(
            values=environment_result.values,
            unavailable_topics=environment_result.unavailable_topics,
        )
        calendar_payload = environment_result.values.get("time")
    else:
        calendar_payload = (
            registry.get_resource(SimulationCalendar).payload_at(
                context.clock.simulation_time
            )
            if registry.has_resource(SimulationCalendar)
            else None
        )
    if isinstance(calendar_payload, dict):
        calendar_time = CalendarTimeObservation(
            datetime=str(calendar_payload["datetime"]),
            date=str(calendar_payload["date"]),
            time=str(calendar_payload["time"]),
            weekday=str(calendar_payload["weekday"]),
            period=str(calendar_payload["period"]),
        )
    return CharacterObservation(
        agent_id=agent_id,
        display_name=profile.display_name,
        simulation_time=context.clock.simulation_time,
        location_id=zone.id if zone is not None else None,
        activity=activity.current.value,
        satiety=homeostasis.satiety,
        energy=homeostasis.energy,
        stress=homeostasis.stress,
        targets=tuple(targets),
        facts=facts,
        recent_outcome=controller.last_outcome,
        spatial_location=spatial_payload,
        available_travel_modes=available_travel_modes,
        calendar_time=calendar_time,
        environment=environment,
        senses=_observed_senses(registry, agent_id),
        equipment=_observed_equipment(registry, agent_id),
        carried_load=_observed_carried_load(registry, agent_id),
        possessions=_observed_possessions(registry, agent_id),
        structured_goals=_observed_goals(registry, agent_id),
    )


def _build_npc_observation(
    context: SystemContext,
    agent_id: str,
    *,
    profile: CharacterProfileComponent,
    controller: ControllerComponent,
    position: PositionComponent,
    activity: ActivityComponent,
    perception: PerceptionComponent,
    world: object,
) -> CharacterObservation:
    from stage0_sim.domain.world import WorldMap

    if not isinstance(world, WorldMap):
        raise TypeError("NPC local world must be a WorldMap")
    registry = context.registry
    npc = registry.get_component(agent_id, NpcComponent)
    point = world.transaction_point(npc.staffed_point_id)
    available = point.available
    if registry.has_resource(EnvironmentAvailabilityRegistry):
        available = registry.get_resource(
            EnvironmentAvailabilityRegistry
        ).state(point.id, base_available=point.available).available
    point_target = ObservedTarget(
        id=point.id,
        kind="transaction_point",
        name=point.name,
        available=available,
        offers=tuple(
            ObservedOffer(
                id=offer.id,
                name=offer.name,
                character_gives=tuple(
                    _observed_item_amount(registry, amount)
                    for amount in offer.character_gives
                ),
                character_receives=tuple(
                    _observed_item_amount(registry, amount)
                    for amount in offer.character_receives
                ),
                duration=offer.duration,
                available=available,
            )
            for offer in point.offers
        ),
    )
    visible_targets = [
        ObservedTarget(
            id=target_id,
            kind="character",
            name=(
                registry.get_component(
                    target_id, CharacterProfileComponent
                ).display_name
                if registry.has_component(
                    target_id, CharacterProfileComponent
                )
                else target_id
            ),
            last_observed_tick=(
                perception.knowledge[target_id].observed_tick
                if target_id in perception.knowledge
                else None
            ),
        )
        for target_id in sorted(
            set(perception.visible_now) | set(perception.knowledge)
        )
    ]
    service_requests = []
    for customer_id, request in registry.query(
        TransactionRequestComponent
    ):
        if (
            request.operator_id != agent_id
            or request.status != "awaiting_authorization"
        ):
            continue
        offer = point.offer(request.offer_id)
        service_requests.append(
            ObservedServiceRequest(
                request_id=request.request_id,
                customer_id=customer_id,
                customer_name=(
                    registry.get_component(
                        customer_id, CharacterProfileComponent
                    ).display_name
                    if registry.has_component(
                        customer_id, CharacterProfileComponent
                    )
                    else customer_id
                ),
                point_id=point.id,
                offer_id=offer.id,
                offer_name=offer.name,
                requested_at=request.requested_at,
            )
        )
    renderer = DeterministicPerceptionRenderer()
    facts = tuple(
        ObservationFact(
            fact_id=item.fact.fact_id,
            fact_type=item.fact.fact_type,
            text=renderer.render_fact(item),
            tick=item.fact.tick,
            subject_id=item.fact.subject_id,
        )
        for item in perception.inbox
    )
    perception.inbox.clear()
    spatial_payload = _spatial_payload(registry, agent_id)
    zone = world.zone_at(position.coordinate)
    return CharacterObservation(
        agent_id=agent_id,
        display_name=profile.display_name,
        simulation_time=context.clock.simulation_time,
        location_id=zone.id if zone is not None else None,
        activity=activity.current.value,
        satiety=None,
        energy=None,
        stress=None,
        targets=(point_target, *visible_targets),
        facts=facts,
        recent_outcome=controller.last_outcome,
        spatial_location=spatial_payload,
        available_travel_modes=(),
        senses=_observed_senses(registry, agent_id),
        equipment=_observed_equipment(registry, agent_id),
        carried_load=_observed_carried_load(registry, agent_id),
        possessions=(),
        service_requests=tuple(
            sorted(
                service_requests,
                key=lambda item: (item.requested_at, item.request_id),
            )
        ),
        structured_goals=_observed_goals(registry, agent_id),
    )


def _spatial_payload(
    registry: Registry,
    agent_id: str,
) -> dict[str, JsonValue] | None:
    if not registry.has_component(agent_id, SpatialLocationComponent):
        return None
    location = registry.get_component(
        agent_id, SpatialLocationComponent
    ).location
    room_id = None
    building_id = None
    city_zone_id = None
    if registry.has_resource(CityWorld):
        city = registry.get_resource(CityWorld)
        try:
            room = city.room(location.place_id)
        except KeyError:
            room = None
        if room is not None:
            building = city.building(room.building_id)
            room_id = room.id
            building_id = building.id
            city_zone_id = building.district_id
    return {
        "scale": location.scale.value,
        "place_id": location.place_id,
        "room_id": room_id,
        "building_id": building_id,
        "city_zone_id": city_zone_id,
        "local_coordinate": (
            location.local_coordinate.to_payload()
            if location.local_coordinate is not None
            else None
        ),
        "network_node_id": location.network_node_id,
        "edge_id": location.edge_id,
        "edge_progress": location.edge_progress,
    }


def _observed_goals(
    registry: Registry,
    agent_id: str,
) -> tuple[ObservedGoal, ...]:
    if not registry.has_component(agent_id, GoalComponent):
        return ()
    return tuple(
        ObservedGoal(
            id=goal.definition.id,
            description=goal.definition.description,
            status=goal.status.value,
            priority=goal.definition.priority,
            tags=goal.definition.tags,
        )
        for goal in registry.get_component(agent_id, GoalComponent).goals
    )


def _observed_item_amount(
    registry: Registry,
    amount: ItemAmount,
) -> ObservedItemAmount:
    item = registry.get_resource(ItemCatalog).item(amount.item_id)
    return ObservedItemAmount(
        item_id=item.id,
        item_name=item.name,
        unit=item.unit,
        quantity=amount.quantity,
    )


def _observed_possessions(
    registry: Registry,
    agent_id: str,
) -> tuple[ObservedPossession, ...]:
    if not registry.has_component(agent_id, PossessionsComponent):
        return ()
    catalog = registry.get_resource(ItemCatalog)
    possessions = registry.get_component(agent_id, PossessionsComponent)
    return tuple(
        ObservedPossession(
            item_id=item_id,
            item_name=catalog.item(item_id).name,
            unit=catalog.item(item_id).unit,
            quantity=quantity,
        )
        for item_id, quantity in sorted(possessions.holdings.items())
    )


def _offer_available(
    registry: Registry,
    agent_id: str,
    point_id: str,
    offer: TransactionOffer,
) -> bool:
    if not registry.has_component(agent_id, PossessionsComponent):
        return False
    possessions = registry.get_component(agent_id, PossessionsComponent)
    point_state = registry.get_resource(TransactionPointRegistry).state(
        point_id
    )
    return can_debit(
        possessions.holdings, offer.character_gives
    ) and can_debit(point_state.holdings, offer.character_receives)


def _observed_physical_state(
    registry: Registry,
    observer_id: str,
    target_id: str,
    *,
    recognized: bool,
) -> dict[str, JsonValue]:
    state: dict[str, JsonValue] = {}
    if registry.has_component(target_id, OpenableComponent):
        openable = registry.get_component(target_id, OpenableComponent)
        state["is_open"] = openable.is_open
        state["is_locked"] = openable.is_locked
    if registry.has_component(target_id, SpatialParentRelationComponent):
        relation = registry.get_component(
            target_id,
            SpatialParentRelationComponent,
        )
        state["relation"] = relation.kind.value
        state["parent_id"] = relation.parent_id
        state["slot_id"] = relation.slot_id
        if relation.kind is PhysicalRelationKind.HELD_BY:
            state["held_by"] = relation.parent_id
    if registry.has_component(target_id, CustodyComponent):
        state["custodian_id"] = registry.get_component(
            target_id,
            CustodyComponent,
        ).custodian_id
    if recognized and registry.has_component(
        target_id,
        ObjectIntrinsicComponent,
    ):
        intrinsic = registry.get_component(
            target_id,
            ObjectIntrinsicComponent,
        )
        state["semantic_size"] = {
            "dimensions_cm": (
                {
                    "length_cm": intrinsic.dimensions.length_cm,
                    "width_cm": intrinsic.dimensions.width_cm,
                    "height_cm": intrinsic.dimensions.height_cm,
                }
                if intrinsic.dimensions is not None
                else None
            ),
            "size_class": (
                intrinsic.size_class.value
                if intrinsic.size_class is not None
                else None
            ),
        }
        intrinsic_relation = (
            registry.get_component(
                target_id,
                SpatialParentRelationComponent,
            )
            if registry.has_component(
                target_id,
                SpatialParentRelationComponent,
            )
            else None
        )
        if (
            intrinsic.mass_kg is not None
            and intrinsic_relation is not None
            and intrinsic_relation.parent_id == observer_id
            and intrinsic_relation.kind in {
                PhysicalRelationKind.HELD_BY,
                PhysicalRelationKind.ATTACHED_TO,
            }
        ):
            state["mass_kg"] = intrinsic.mass_kg
    if recognized and registry.has_component(target_id, WearableComponent):
        wearable = registry.get_component(target_id, WearableComponent)
        state["wearable_slots"] = [
            slot.value
            for slot in sorted(
                wearable.compatible_slots,
                key=lambda item: item.value,
            )
        ]
    if recognized and registry.has_component(target_id, ScentSourceComponent):
        scent = registry.get_component(target_id, ScentSourceComponent)
        state["scent"] = {
            "scent_id": scent.scent_id,
            "description": scent.description,
        }
    return state


def _observed_senses(
    registry: Registry,
    agent_id: str,
) -> dict[str, JsonValue] | None:
    if not registry.has_component(agent_id, EffectiveSensesComponent):
        return None
    effective = registry.get_component(agent_id, EffectiveSensesComponent)
    return {
        "vision_range": effective.vision_range,
        "recognition_range": effective.recognition_range,
        "hearing_range": effective.hearing_range,
        "smell_range": effective.smell_range,
    }


def _observed_equipment(
    registry: Registry,
    agent_id: str,
) -> dict[str, JsonValue] | None:
    if not registry.has_component(agent_id, EquipmentStateComponent):
        return None
    equipment = registry.get_component(agent_id, EquipmentStateComponent)
    return {
        slot.value: list(object_ids)
        for slot, object_ids in sorted(
            equipment.equipped_object_ids.items(),
            key=lambda item: item[0].value,
        )
    }


def _observed_carried_load(
    registry: Registry,
    agent_id: str,
) -> dict[str, JsonValue] | None:
    if not registry.has_component(agent_id, CarriedLoadComponent):
        return None
    load = registry.get_component(agent_id, CarriedLoadComponent)
    embodiment = (
        registry.get_component(agent_id, CharacterEmbodimentComponent)
        if registry.has_component(agent_id, CharacterEmbodimentComponent)
        else None
    )
    return {
        "known_mass_kg": load.known_mass_kg,
        "unknown_mass_object_ids": list(load.unknown_mass_object_ids),
        "max_single_object_mass_kg": (
            embodiment.max_single_object_mass_kg
            if embodiment is not None
            else None
        ),
        "max_carried_mass_kg": (
            embodiment.max_carried_mass_kg
            if embodiment is not None
            else None
        ),
    }
