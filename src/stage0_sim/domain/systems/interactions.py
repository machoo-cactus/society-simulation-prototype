from dataclasses import dataclass, replace

from stage0_sim.domain.components import (
    ActionInstance,
    ActionType,
    CharacterEmbodimentComponent,
    CharacterHandStateComponent,
    CharacterPosture,
    CharacterPostureComponent,
    ConsumableComponent,
    ContainerComponent,
    CustodyComponent,
    DriveComponent,
    EquipmentSlot,
    InteractionExecutionComponent,
    InteractionRequestComponent,
    MovementComponent,
    ObjectIntrinsicComponent,
    OccupancySlot,
    OccupancySlotsComponent,
    OpenableComponent,
    PerceptionComponent,
    PhysicalInteractionRegistry,
    PhysicalObjectIdentityComponent,
    PhysicalRelationKind,
    PhysicalStateComponent,
    PortableComponent,
    PositionComponent,
    ReadableComponent,
    SenseTransmission,
    SpatialCollisionError,
    SpatialIndex,
    SpatialIndexEntry,
    SpatialLocationComponent,
    SpatialParentRelationComponent,
    SupportComponent,
    System1State,
    UsableComponent,
    WearableComponent,
    validate_spatial_relation_acyclicity,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.interactions import (
    InteractionFailureReason,
    InteractionSpecification,
    InteractionVerb,
)
from stage0_sim.domain.lineage import action_lineage_payload
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.spatial_context import local_world_for_agent
from stage0_sim.domain.world import (
    Coordinate,
    MovementObstruction,
    PhysicalPose,
    VisionObstruction,
    footprints_touch,
)


def available_interactions(
    registry: Registry,
    actor_id: str,
    target_id: str,
) -> tuple[str, ...]:
    if (
        target_id not in registry.entities()
        or not registry.has_component(
            target_id,
            PhysicalObjectIdentityComponent,
        )
    ):
        return ()
    verbs: set[InteractionVerb] = set()
    held_by_actor = _is_held_by(registry, target_id, actor_id)
    accessible = _target_accessible(registry, actor_id, target_id)
    relation = _relation(registry, target_id)
    if registry.has_component(target_id, PortableComponent):
        if held_by_actor:
            verbs.update(
                {
                    InteractionVerb.PUT_DOWN,
                    InteractionVerb.PLACE_ON,
                    InteractionVerb.PLACE_IN,
                }
            )
        elif (
            accessible
            and (
                relation is None
                or relation.kind is not PhysicalRelationKind.HELD_BY
            )
        ):
            verbs.add(InteractionVerb.PICK_UP)
    if registry.has_component(target_id, WearableComponent):
        if _is_equipped_by(registry, target_id, actor_id):
            verbs.add(InteractionVerb.UNEQUIP)
        elif held_by_actor:
            verbs.add(InteractionVerb.EQUIP)
    if accessible and registry.has_component(target_id, OpenableComponent):
        openable = registry.get_component(target_id, OpenableComponent)
        if openable.is_open:
            verbs.add(InteractionVerb.CLOSE)
        elif not openable.is_locked:
            verbs.add(InteractionVerb.OPEN)
    if accessible and registry.has_component(
        target_id,
        OccupancySlotsComponent,
    ):
        slots = registry.get_component(target_id, OccupancySlotsComponent)
        if any(
            PhysicalRelationKind.OCCUPIES_SLOT in slot.accepted_relations
            for slot in slots.slots
        ):
            posture = (
                registry.get_component(actor_id, CharacterPostureComponent)
                if registry.has_component(
                    actor_id,
                    CharacterPostureComponent,
                )
                else None
            )
            if posture is None or posture.posture is CharacterPosture.STANDING:
                verbs.update({InteractionVerb.SIT, InteractionVerb.LIE_DOWN})
            elif posture.support_id == target_id:
                verbs.add(
                    InteractionVerb.STAND
                    if posture.posture is CharacterPosture.SITTING
                    else InteractionVerb.GET_UP
                )
    if accessible and registry.has_component(target_id, UsableComponent):
        verbs.add(InteractionVerb.USE)
    return tuple(verb.value for verb in InteractionVerb if verb in verbs)


def available_physical_actions(
    registry: Registry,
    actor_id: str,
    target_id: str,
) -> tuple[str, ...]:
    if not _target_accessible(registry, actor_id, target_id):
        return ()
    actions: list[str] = []
    if registry.has_component(target_id, ReadableComponent):
        actions.append(ActionType.READ.value)
    if registry.has_component(target_id, ConsumableComponent):
        actions.append(ActionType.DRINK.value)
    return tuple(actions)


def is_at_interaction_approach(
    registry: Registry,
    actor_id: str,
    target_id: str,
    *,
    fallback: Coordinate | None = None,
) -> bool:
    if (
        target_id in registry.entities()
        and
        registry.has_component(actor_id, PhysicalStateComponent)
        and registry.has_component(target_id, PhysicalStateComponent)
    ):
        return footprints_touch(
            registry.get_component(actor_id, PhysicalStateComponent),
            registry.get_component(target_id, PhysicalStateComponent),
        )
    if fallback is None or not registry.has_component(
        actor_id,
        PositionComponent,
    ):
        return False
    return (
        registry.get_component(actor_id, PositionComponent).coordinate
        == fallback
    )


def interaction_approach_anchors(
    registry: Registry,
    target_id: str,
    fallback: Coordinate,
) -> tuple[Coordinate, ...]:
    if registry.has_resource(PhysicalInteractionRegistry):
        anchors = registry.get_resource(
            PhysicalInteractionRegistry
        ).approach_anchors(target_id)
        if anchors:
            return anchors
    return (fallback,)


def physical_object_is_exposed(
    registry: Registry,
    target_id: str,
) -> bool:
    return not _hidden_in_closed_container(registry, target_id)


def sync_held_object_poses(
    registry: Registry,
    actor_id: str,
    room_id: str,
    anchor: Coordinate,
) -> None:
    if not registry.has_component(actor_id, CharacterHandStateComponent):
        return
    hands = registry.get_component(actor_id, CharacterHandStateComponent)
    for object_id in hands.held_object_ids:
        if not registry.has_component(object_id, PhysicalStateComponent):
            continue
        state = registry.get_component(object_id, PhysicalStateComponent)
        registry.set_component(
            object_id,
            replace(
                state,
                pose=replace(
                    state.pose,
                    room_id=room_id,
                    anchor=anchor,
                ),
            ),
        )
    for object_id, relation in registry.query(SpatialParentRelationComponent):
        if (
            relation.kind is not PhysicalRelationKind.ATTACHED_TO
            or relation.parent_id != actor_id
        ):
            continue
        if not registry.has_component(object_id, PhysicalStateComponent):
            continue
        state = registry.get_component(object_id, PhysicalStateComponent)
        registry.set_component(
            object_id,
            replace(
                state,
                pose=replace(
                    state.pose,
                    room_id=room_id,
                    anchor=anchor,
                ),
            ),
        )


def physical_activity_failure(
    registry: Registry,
    actor_id: str,
    target_id: str | None,
    action: ActionType,
) -> str | None:
    if target_id is None or target_id not in registry.entities():
        return InteractionFailureReason.UNKNOWN_TARGET.value
    required: type[ReadableComponent] | type[ConsumableComponent]
    if action is ActionType.READ:
        required = ReadableComponent
    elif action is ActionType.DRINK:
        required = ConsumableComponent
    else:
        return None
    if not registry.has_component(target_id, required):
        return InteractionFailureReason.CAPABILITY_MISSING.value
    if not _target_accessible(registry, actor_id, target_id):
        return InteractionFailureReason.TARGET_NOT_REACHABLE.value
    return None


def complete_drink(
    context: SystemContext,
    actor_id: str,
    target_id: str,
    action_instance: ActionInstance | None,
) -> str | None:
    failure = physical_activity_failure(
        context.registry,
        actor_id,
        target_id,
        ActionType.DRINK,
    )
    if failure is not None:
        return failure
    consumable = context.registry.get_component(
        target_id,
        ConsumableComponent,
    )
    remaining = consumable.servings - 1
    if remaining:
        context.registry.set_component(
            target_id,
            replace(consumable, servings=remaining),
        )
    else:
        context.registry.remove_component(target_id, ConsumableComponent)
    context.events.emit(
        "drink.completed",
        simulation_tick=context.clock.tick,
        simulation_time=context.clock.simulation_time,
        agent_id=actor_id,
        payload={
            "target_id": target_id,
            "item_id": consumable.item_id,
            "remaining_servings": remaining,
            **action_lineage_payload(action_instance),
        },
        correlation_id=(
            action_instance.root_correlation_id
            if action_instance is not None
            else None
        ),
    )
    return None


def execute_navigation_interaction(
    context: SystemContext,
    actor_id: str,
    specification: InteractionSpecification,
    action_instance: ActionInstance | None,
) -> str | None:
    requested = context.events.emit(
        "interaction.requested",
        simulation_tick=context.clock.tick,
        simulation_time=context.clock.simulation_time,
        agent_id=actor_id,
        payload={
            **_specification_payload(specification),
            "source": "navigation",
            **action_lineage_payload(action_instance),
        },
        correlation_id=_correlation_id(action_instance),
    )
    system = InteractionExecutionSystem()
    failure = system._failure(
        context.registry,
        actor_id,
        specification,
    )
    if failure is not None:
        system._emit_failure(
            context,
            actor_id,
            specification,
            failure,
            action_instance,
            causation_id=requested.event_id,
        )
        return failure
    started = context.events.emit(
        "interaction.started",
        simulation_tick=context.clock.tick,
        simulation_time=context.clock.simulation_time,
        agent_id=actor_id,
        payload={
            **_specification_payload(specification),
            "source": "navigation",
            "duration": 0.0,
            **action_lineage_payload(action_instance),
        },
        causation_id=requested.event_id,
        correlation_id=_correlation_id(action_instance),
    )
    details = system._commit(context, actor_id, specification)
    context.events.emit(
        "interaction.completed",
        simulation_tick=context.clock.tick,
        simulation_time=context.clock.simulation_time,
        agent_id=actor_id,
        payload={
            **_specification_payload(specification),
            "source": "navigation",
            **details,
            **action_lineage_payload(action_instance),
        },
        causation_id=started.event_id,
        correlation_id=_correlation_id(action_instance),
    )
    return None


@dataclass(frozen=True, slots=True)
class InteractionExecutionSystem:
    name: str = "interaction_execution"
    order: int = 145

    def update(self, context: SystemContext) -> None:
        active = tuple(
            context.registry.query_entities(InteractionExecutionComponent)
        )
        for actor_id in active:
            self._advance(context, actor_id)
        for actor_id in context.registry.query_entities(
            InteractionRequestComponent
        ):
            if actor_id in active or context.registry.has_component(
                actor_id,
                InteractionExecutionComponent,
            ):
                continue
            request = context.registry.get_component(
                actor_id,
                InteractionRequestComponent,
            )
            if request.status != "requested":
                continue
            requested = context.events.emit(
                "interaction.requested",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=actor_id,
                payload={
                    **_specification_payload(request.specification),
                    **action_lineage_payload(request.action_instance),
                },
                correlation_id=_correlation_id(request.action_instance),
            )
            failure = self._failure(
                context.registry,
                actor_id,
                request.specification,
            )
            if failure is not None:
                request.status = "failed"
                request.failure_reason = failure
                self._emit_failure(
                    context,
                    actor_id,
                    request.specification,
                    failure,
                    request.action_instance,
                    causation_id=requested.event_id,
                )
                continue
            started = context.events.emit(
                "interaction.started",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=actor_id,
                payload={
                    **_specification_payload(request.specification),
                    "duration": 1.0,
                    **action_lineage_payload(request.action_instance),
                },
                causation_id=requested.event_id,
                correlation_id=_correlation_id(request.action_instance),
            )
            context.registry.add_component(
                actor_id,
                InteractionExecutionComponent(
                    specification=request.specification,
                    source=request.source,
                    correlation_id=(
                        _correlation_id(request.action_instance)
                        or started.event_id
                    ),
                    action_instance=request.action_instance,
                ),
            )
            request.status = "running"
            self._advance(context, actor_id)

    def _advance(self, context: SystemContext, actor_id: str) -> None:
        execution = context.registry.get_component(
            actor_id,
            InteractionExecutionComponent,
        )
        failure = self._failure(
            context.registry,
            actor_id,
            execution.specification,
        )
        if failure is not None:
            self._cancel(context, actor_id, execution, failure)
            return
        execution.elapsed = round(
            min(
                execution.duration,
                execution.elapsed + context.clock.dt,
            ),
            12,
        )
        if execution.elapsed < execution.duration:
            return
        try:
            details = self._commit(
                context,
                actor_id,
                execution.specification,
            )
        except SpatialCollisionError:
            self._cancel(
                context,
                actor_id,
                execution,
                InteractionFailureReason.INTERACTION_NOT_AVAILABLE.value,
            )
            return
        context.events.emit(
            "interaction.completed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=actor_id,
            payload={
                **_specification_payload(execution.specification),
                **details,
                **action_lineage_payload(execution.action_instance),
            },
            correlation_id=execution.correlation_id,
        )
        if context.registry.has_component(
            actor_id,
            InteractionRequestComponent,
        ):
            request = context.registry.get_component(
                actor_id,
                InteractionRequestComponent,
            )
            request.status = "completed"
        context.registry.remove_component(
            actor_id,
            InteractionExecutionComponent,
        )

    def _failure(
        self,
        registry: Registry,
        actor_id: str,
        specification: InteractionSpecification,
    ) -> str | None:
        if (
            actor_id not in registry.entities()
            or specification.target_id not in registry.entities()
        ):
            return InteractionFailureReason.UNKNOWN_TARGET.value
        if (
            registry.has_component(actor_id, DriveComponent)
            and registry.get_component(actor_id, DriveComponent).state
            is not System1State.NORMAL
        ):
            return InteractionFailureReason.SYSTEM1_PREEMPTION.value
        if not _target_observable(
            registry,
            actor_id,
            specification.target_id,
        ):
            return InteractionFailureReason.TARGET_NOT_OBSERVABLE.value
        verb = specification.verb
        if verb is InteractionVerb.PICK_UP:
            return _pickup_failure(registry, actor_id, specification.target_id)
        if verb in {
            InteractionVerb.PUT_DOWN,
            InteractionVerb.PLACE_ON,
            InteractionVerb.PLACE_IN,
        }:
            held_failure = _held_failure(
                registry,
                actor_id,
                specification.target_id,
            )
            if held_failure is not None:
                return held_failure
            if verb is InteractionVerb.PUT_DOWN:
                return (
                    None
                    if _floor_pose(
                        registry,
                        actor_id,
                        specification.target_id,
                    )
                    is not None
                    else InteractionFailureReason.EXIT_POSE_UNAVAILABLE.value
                )
            return _placement_failure(registry, actor_id, specification)
        if verb in {InteractionVerb.OPEN, InteractionVerb.CLOSE}:
            return _open_close_failure(
                registry,
                actor_id,
                specification.target_id,
                verb,
            )
        if verb in {InteractionVerb.SIT, InteractionVerb.LIE_DOWN}:
            return _occupy_failure(registry, actor_id, specification)
        if verb in {InteractionVerb.STAND, InteractionVerb.GET_UP}:
            return _exit_failure(registry, actor_id, specification)
        if verb is InteractionVerb.USE:
            if not registry.has_component(
                specification.target_id,
                UsableComponent,
            ):
                return InteractionFailureReason.USE_NOT_SUPPORTED.value
            if not _target_accessible(
                registry,
                actor_id,
                specification.target_id,
            ):
                return InteractionFailureReason.TARGET_NOT_REACHABLE.value
            return None
        if verb is InteractionVerb.EQUIP:
            return _equip_failure(registry, actor_id, specification)
        if verb is InteractionVerb.UNEQUIP:
            return _unequip_failure(
                registry,
                actor_id,
                specification.target_id,
            )
        return InteractionFailureReason.INTERACTION_NOT_AVAILABLE.value

    def _commit(
        self,
        context: SystemContext,
        actor_id: str,
        specification: InteractionSpecification,
    ) -> dict[str, JsonValue]:
        verb = specification.verb
        if verb is InteractionVerb.PICK_UP:
            return _commit_pickup(
                context.registry,
                actor_id,
                specification.target_id,
            )
        if verb is InteractionVerb.PUT_DOWN:
            return _commit_put_down(
                context.registry,
                actor_id,
                specification.target_id,
            )
        if verb in {InteractionVerb.PLACE_ON, InteractionVerb.PLACE_IN}:
            return _commit_place(
                context.registry,
                actor_id,
                specification,
            )
        if verb in {InteractionVerb.OPEN, InteractionVerb.CLOSE}:
            return _commit_open_close(
                context.registry,
                specification.target_id,
                verb,
            )
        if verb in {InteractionVerb.SIT, InteractionVerb.LIE_DOWN}:
            return _commit_occupy(
                context.registry,
                actor_id,
                specification,
            )
        if verb in {InteractionVerb.STAND, InteractionVerb.GET_UP}:
            return _commit_exit(
                context.registry,
                actor_id,
                specification,
            )
        if verb is InteractionVerb.EQUIP:
            return _commit_equip(
                context.registry,
                actor_id,
                specification,
            )
        if verb is InteractionVerb.UNEQUIP:
            return _commit_unequip(
                context.registry,
                actor_id,
                specification.target_id,
            )
        usable = context.registry.get_component(
            specification.target_id,
            UsableComponent,
        )
        return {"use_kind": usable.use_kind}

    @staticmethod
    def _emit_failure(
        context: SystemContext,
        actor_id: str,
        specification: InteractionSpecification,
        reason: str,
        action_instance: ActionInstance | None,
        *,
        causation_id: str | None = None,
    ) -> None:
        context.events.emit(
            "interaction.failed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=actor_id,
            payload={
                **_specification_payload(specification),
                "reason": reason,
                **action_lineage_payload(action_instance),
            },
            causation_id=causation_id,
            correlation_id=_correlation_id(action_instance),
        )

    def _cancel(
        self,
        context: SystemContext,
        actor_id: str,
        execution: InteractionExecutionComponent,
        reason: str,
    ) -> None:
        context.events.emit(
            "interaction.cancelled",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=actor_id,
            payload={
                **_specification_payload(execution.specification),
                "reason": reason,
                "elapsed": execution.elapsed,
                **action_lineage_payload(execution.action_instance),
            },
            correlation_id=execution.correlation_id,
        )
        if context.registry.has_component(
            actor_id,
            InteractionRequestComponent,
        ):
            request = context.registry.get_component(
                actor_id,
                InteractionRequestComponent,
            )
            request.status = "failed"
            request.failure_reason = reason
        context.registry.remove_component(
            actor_id,
            InteractionExecutionComponent,
        )


def cancel_interaction(
    context: SystemContext,
    actor_id: str,
    reason: str,
) -> None:
    if context.registry.has_component(
        actor_id,
        InteractionExecutionComponent,
    ):
        InteractionExecutionSystem()._cancel(
            context,
            actor_id,
            context.registry.get_component(
                actor_id,
                InteractionExecutionComponent,
            ),
            reason,
        )
    if context.registry.has_component(
        actor_id,
        InteractionRequestComponent,
    ):
        request = context.registry.get_component(
            actor_id,
            InteractionRequestComponent,
        )
        if request.status == "requested":
            request.status = "failed"
            request.failure_reason = reason
            InteractionExecutionSystem._emit_failure(
                context,
                actor_id,
                request.specification,
                reason,
                request.action_instance,
            )


def _pickup_failure(
    registry: Registry,
    actor_id: str,
    target_id: str,
) -> str | None:
    if not registry.has_component(target_id, PortableComponent):
        return InteractionFailureReason.OBJECT_NOT_PORTABLE.value
    relation = _relation(registry, target_id)
    if relation is not None and relation.kind is PhysicalRelationKind.HELD_BY:
        return InteractionFailureReason.OBJECT_ALREADY_HELD.value
    if not _target_accessible(registry, actor_id, target_id):
        return InteractionFailureReason.TARGET_NOT_REACHABLE.value
    hands = _hands(registry, actor_id)
    portable = registry.get_component(target_id, PortableComponent)
    mass_failure = _mass_failure(registry, actor_id, target_id)
    if mass_failure is not None:
        return mass_failure
    required = 2 if portable.two_handed else 1
    if len(hands.free_hand_ids()) < required:
        return InteractionFailureReason.HANDS_FULL.value
    return None


def _equip_failure(
    registry: Registry,
    actor_id: str,
    specification: InteractionSpecification,
) -> str | None:
    target_id = specification.target_id
    if not registry.has_component(target_id, WearableComponent):
        return InteractionFailureReason.OBJECT_NOT_WEARABLE.value
    held_failure = _held_failure(registry, actor_id, target_id)
    if held_failure is not None:
        return held_failure
    if specification.slot_id is None:
        return InteractionFailureReason.EQUIPMENT_SLOT_REQUIRED.value
    try:
        slot = EquipmentSlot(specification.slot_id)
    except ValueError:
        return InteractionFailureReason.EQUIPMENT_SLOT_UNSUPPORTED.value
    wearable = registry.get_component(target_id, WearableComponent)
    if slot not in wearable.compatible_slots:
        return InteractionFailureReason.EQUIPMENT_SLOT_INCOMPATIBLE.value
    if registry.has_component(actor_id, CharacterEmbodimentComponent):
        embodiment = registry.get_component(
            actor_id,
            CharacterEmbodimentComponent,
        )
        capacity = embodiment.equipment_slot_capacities.get(slot)
        if capacity is None:
            return InteractionFailureReason.EQUIPMENT_SLOT_UNSUPPORTED.value
        if len(_equipped_in_slot(registry, actor_id, slot)) >= capacity:
            return InteractionFailureReason.EQUIPMENT_SLOT_AT_CAPACITY.value
    return _current_load_failure(registry, actor_id)


def _unequip_failure(
    registry: Registry,
    actor_id: str,
    target_id: str,
) -> str | None:
    if not _is_equipped_by(registry, target_id, actor_id):
        return InteractionFailureReason.OBJECT_NOT_EQUIPPED.value
    required_hands = (
        2
        if registry.has_component(target_id, PortableComponent)
        and registry.get_component(target_id, PortableComponent).two_handed
        else 1
    )
    if len(_hands(registry, actor_id).free_hand_ids()) < required_hands:
        return InteractionFailureReason.HANDS_FULL.value
    return None


def _held_failure(
    registry: Registry,
    actor_id: str,
    target_id: str,
) -> str | None:
    if not _is_held_by(registry, target_id, actor_id):
        return InteractionFailureReason.OBJECT_NOT_HELD.value
    return None


def _open_close_failure(
    registry: Registry,
    actor_id: str,
    target_id: str,
    verb: InteractionVerb,
) -> str | None:
    if not registry.has_component(target_id, OpenableComponent):
        return InteractionFailureReason.CAPABILITY_MISSING.value
    if not _target_accessible(registry, actor_id, target_id):
        return InteractionFailureReason.TARGET_NOT_REACHABLE.value
    openable = registry.get_component(target_id, OpenableComponent)
    if verb is InteractionVerb.OPEN:
        if openable.is_open:
            return InteractionFailureReason.OBJECT_ALREADY_OPEN.value
        if openable.is_locked:
            return InteractionFailureReason.OBJECT_LOCKED.value
        return None
    if not openable.is_open:
        return InteractionFailureReason.OBJECT_ALREADY_CLOSED.value
    if registry.has_resource(SpatialIndex) and registry.has_component(
        target_id,
        PhysicalStateComponent,
    ):
        state = registry.get_component(target_id, PhysicalStateComponent)
        closed = replace(
            state,
            movement_obstruction=openable.closed_movement_obstruction,
            vision_obstruction=openable.closed_vision_obstruction,
        )
        if (
            registry.get_resource(SpatialIndex).contains(target_id)
            and not registry.get_resource(SpatialIndex).can_place(
                closed,
                excluding=target_id,
            )
        ):
            return InteractionFailureReason.CLOSE_BLOCKED.value
    return None


def _placement_failure(
    registry: Registry,
    actor_id: str,
    specification: InteractionSpecification,
) -> str | None:
    destination_id = specification.destination_id
    if destination_id is None or destination_id not in registry.entities():
        return InteractionFailureReason.DESTINATION_REQUIRED.value
    if not _target_observable(registry, actor_id, destination_id):
        return InteractionFailureReason.TARGET_NOT_OBSERVABLE.value
    if not _target_accessible(registry, actor_id, destination_id):
        return InteractionFailureReason.DESTINATION_NOT_REACHABLE.value
    relation_kind = (
        PhysicalRelationKind.ON_SUPPORT
        if specification.verb is InteractionVerb.PLACE_ON
        else PhysicalRelationKind.IN_CONTAINER
    )
    capability_type = (
        SupportComponent
        if relation_kind is PhysicalRelationKind.ON_SUPPORT
        else ContainerComponent
    )
    if not registry.has_component(destination_id, capability_type):
        return InteractionFailureReason.CAPABILITY_MISSING.value
    if (
        relation_kind is PhysicalRelationKind.IN_CONTAINER
        and registry.has_component(destination_id, OpenableComponent)
        and not registry.get_component(
            destination_id,
            OpenableComponent,
        ).is_open
    ):
        return InteractionFailureReason.CONTAINER_CLOSED.value
    slot_id = _select_slot(
        registry,
        destination_id,
        relation_kind,
        specification.slot_id,
    )
    if isinstance(slot_id, str) and slot_id.startswith("failure:"):
        return slot_id.removeprefix("failure:")
    relations = {
        entity_id: relation
        for entity_id, relation in registry.query(
            SpatialParentRelationComponent
        )
    }
    relations[specification.target_id] = SpatialParentRelationComponent(
        destination_id,
        relation_kind,
        slot_id,
    )
    try:
        validate_spatial_relation_acyclicity(relations)
    except ValueError:
        return InteractionFailureReason.RELATION_CYCLE.value
    return None


def _occupy_failure(
    registry: Registry,
    actor_id: str,
    specification: InteractionSpecification,
) -> str | None:
    posture = _posture(registry, actor_id)
    if posture.posture is not CharacterPosture.STANDING:
        return InteractionFailureReason.POSTURE_INVALID.value
    if not _target_accessible(
        registry,
        actor_id,
        specification.target_id,
    ):
        return InteractionFailureReason.TARGET_NOT_REACHABLE.value
    slot_id = _select_slot(
        registry,
        specification.target_id,
        PhysicalRelationKind.OCCUPIES_SLOT,
        specification.slot_id,
    )
    if slot_id.startswith("failure:"):
        return slot_id.removeprefix("failure:")
    if _occupancy_pose(
        registry,
        actor_id,
        specification.target_id,
        slot_id,
    ) is None:
        return InteractionFailureReason.OCCUPANCY_POSE_UNAVAILABLE.value
    return None


def _exit_failure(
    registry: Registry,
    actor_id: str,
    specification: InteractionSpecification,
) -> str | None:
    posture = _posture(registry, actor_id)
    expected = (
        CharacterPosture.SITTING
        if specification.verb is InteractionVerb.STAND
        else CharacterPosture.LYING
    )
    if (
        posture.posture is not expected
        or posture.support_id != specification.target_id
    ):
        return InteractionFailureReason.POSTURE_INVALID.value
    if _exit_pose(
        registry,
        actor_id,
        specification.target_id,
    ) is None:
        return InteractionFailureReason.EXIT_POSE_UNAVAILABLE.value
    return None


def _commit_pickup(
    registry: Registry,
    actor_id: str,
    target_id: str,
) -> dict[str, JsonValue]:
    hands = _hands(registry, actor_id)
    portable = registry.get_component(target_id, PortableComponent)
    free = hands.free_hand_ids()
    selected = free[: 2 if portable.two_handed else 1]
    if registry.has_resource(SpatialIndex):
        spatial_index = registry.get_resource(SpatialIndex)
        if spatial_index.contains(target_id):
            spatial_index.remove(target_id)
    if portable.two_handed:
        hands.left_hand_object_id = target_id
        hands.right_hand_object_id = target_id
        slot_id = "both"
    elif selected[0] == "left":
        hands.left_hand_object_id = target_id
        slot_id = "left"
    else:
        hands.right_hand_object_id = target_id
        slot_id = "right"
    registry.set_component(
        target_id,
        SpatialParentRelationComponent(
            actor_id,
            PhysicalRelationKind.HELD_BY,
            slot_id,
        ),
    )
    custody = CustodyComponent(actor_id)
    registry.set_component(target_id, custody)
    if registry.has_component(
        target_id,
        PhysicalStateComponent,
    ) and registry.has_component(actor_id, PositionComponent):
        state = registry.get_component(target_id, PhysicalStateComponent)
        actor_position = registry.get_component(
            actor_id,
            PositionComponent,
        ).coordinate
        registry.set_component(
            target_id,
            replace(
                state,
                pose=replace(
                    state.pose,
                    room_id=_actor_room_id(registry, actor_id),
                    anchor=actor_position,
                ),
            ),
        )
    return {"hand_slot": slot_id, "custodian_id": actor_id}


def _commit_equip(
    registry: Registry,
    actor_id: str,
    specification: InteractionSpecification,
) -> dict[str, JsonValue]:
    if specification.slot_id is None:
        raise AssertionError("validated equipment interaction lost slot")
    slot = EquipmentSlot(specification.slot_id)
    _release_hands(registry, actor_id, specification.target_id)
    registry.set_component(
        specification.target_id,
        SpatialParentRelationComponent(
            actor_id,
            PhysicalRelationKind.ATTACHED_TO,
            slot.value,
        ),
    )
    if registry.has_component(
        specification.target_id,
        PhysicalStateComponent,
    ) and registry.has_component(actor_id, PositionComponent):
        state = registry.get_component(
            specification.target_id,
            PhysicalStateComponent,
        )
        registry.set_component(
            specification.target_id,
            replace(
                state,
                pose=replace(
                    state.pose,
                    room_id=_actor_room_id(registry, actor_id),
                    anchor=registry.get_component(
                        actor_id,
                        PositionComponent,
                    ).coordinate,
                ),
            ),
        )
    return {"equipment_slot": slot.value, "custodian_id": actor_id}


def _commit_unequip(
    registry: Registry,
    actor_id: str,
    target_id: str,
) -> dict[str, JsonValue]:
    hands = _hands(registry, actor_id)
    portable = (
        registry.get_component(target_id, PortableComponent)
        if registry.has_component(target_id, PortableComponent)
        else PortableComponent()
    )
    free = hands.free_hand_ids()
    if portable.two_handed:
        hands.left_hand_object_id = target_id
        hands.right_hand_object_id = target_id
        slot_id = "both"
    elif free[0] == "left":
        hands.left_hand_object_id = target_id
        slot_id = "left"
    else:
        hands.right_hand_object_id = target_id
        slot_id = "right"
    registry.set_component(
        target_id,
        SpatialParentRelationComponent(
            actor_id,
            PhysicalRelationKind.HELD_BY,
            slot_id,
        ),
    )
    registry.set_component(target_id, CustodyComponent(actor_id))
    return {"hand_slot": slot_id, "custodian_id": actor_id}


def _commit_put_down(
    registry: Registry,
    actor_id: str,
    target_id: str,
) -> dict[str, JsonValue]:
    pose = _floor_pose(registry, actor_id, target_id)
    if pose is None:
        raise SpatialCollisionError("no floor pose")
    state = registry.get_component(target_id, PhysicalStateComponent)
    next_state = replace(state, pose=pose)
    spatial_index = registry.get_resource(SpatialIndex)
    spatial_index.add(SpatialIndexEntry(target_id, next_state))
    registry.set_component(target_id, next_state)
    registry.set_component(
        target_id,
        SpatialParentRelationComponent(
            pose.room_id,
            PhysicalRelationKind.ON_FLOOR,
        ),
    )
    _release_hands(registry, actor_id, target_id)
    if registry.has_component(target_id, CustodyComponent):
        registry.remove_component(target_id, CustodyComponent)
    return {"pose": pose.anchor.to_payload()}


def _commit_place(
    registry: Registry,
    actor_id: str,
    specification: InteractionSpecification,
) -> dict[str, JsonValue]:
    destination_id = specification.destination_id
    if destination_id is None:
        raise AssertionError("validated placement lost destination")
    relation_kind = (
        PhysicalRelationKind.ON_SUPPORT
        if specification.verb is InteractionVerb.PLACE_ON
        else PhysicalRelationKind.IN_CONTAINER
    )
    selected = _select_slot(
        registry,
        destination_id,
        relation_kind,
        specification.slot_id,
    )
    if selected.startswith("failure:"):
        raise AssertionError("validated placement lost slot")
    destination_state = registry.get_component(
        destination_id,
        PhysicalStateComponent,
    )
    state = registry.get_component(
        specification.target_id,
        PhysicalStateComponent,
    )
    anchor = _slot_anchor(
        registry,
        destination_id,
        selected,
        destination_state,
    )
    next_state = replace(
        state,
        pose=PhysicalPose(
            destination_state.pose.room_id,
            anchor,
            destination_state.pose.orientation,
        ),
    )
    if relation_kind is PhysicalRelationKind.ON_SUPPORT:
        registry.get_resource(SpatialIndex).add(
            SpatialIndexEntry(specification.target_id, next_state),
            authorized_overlaps=frozenset({destination_id}),
        )
    registry.set_component(specification.target_id, next_state)
    registry.set_component(
        specification.target_id,
        SpatialParentRelationComponent(
            destination_id,
            relation_kind,
            selected,
        ),
    )
    _release_hands(registry, actor_id, specification.target_id)
    if registry.has_component(specification.target_id, CustodyComponent):
        registry.remove_component(
            specification.target_id,
            CustodyComponent,
        )
    return {
        "relation": relation_kind.value,
        "slot_id": selected,
        "destination_id": destination_id,
    }


def _commit_open_close(
    registry: Registry,
    target_id: str,
    verb: InteractionVerb,
) -> dict[str, JsonValue]:
    openable = registry.get_component(target_id, OpenableComponent)
    state = registry.get_component(target_id, PhysicalStateComponent)
    opening = verb is InteractionVerb.OPEN
    next_state = replace(
        state,
        movement_obstruction=(
            MovementObstruction.NONE
            if opening
            else openable.closed_movement_obstruction
        ),
        vision_obstruction=(
            VisionObstruction.TRANSPARENT
            if opening
            else openable.closed_vision_obstruction
        ),
        hearing_transmission=(
            SenseTransmission.PASS
            if opening
            else openable.closed_hearing_transmission
        ),
        smell_transmission=(
            SenseTransmission.PASS
            if opening
            else openable.closed_smell_transmission
        ),
    )
    if registry.has_resource(SpatialIndex):
        index = registry.get_resource(SpatialIndex)
        if index.contains(target_id):
            index.update(SpatialIndexEntry(target_id, next_state))
    registry.set_component(target_id, next_state)
    openable.is_open = opening
    return {"is_open": opening, "is_locked": openable.is_locked}


def _commit_occupy(
    registry: Registry,
    actor_id: str,
    specification: InteractionSpecification,
) -> dict[str, JsonValue]:
    slot_id = _select_slot(
        registry,
        specification.target_id,
        PhysicalRelationKind.OCCUPIES_SLOT,
        specification.slot_id,
    )
    if slot_id.startswith("failure:"):
        raise AssertionError("validated occupancy lost slot")
    pose = _occupancy_pose(
        registry,
        actor_id,
        specification.target_id,
        slot_id,
    )
    if pose is None:
        raise SpatialCollisionError("no occupancy pose")
    actor_state = registry.get_component(
        actor_id,
        PhysicalStateComponent,
    )
    next_state = replace(actor_state, pose=pose)
    registry.get_resource(SpatialIndex).update(
        SpatialIndexEntry(actor_id, next_state, dynamic=True),
        authorized_overlaps=frozenset({specification.target_id}),
    )
    registry.set_component(actor_id, next_state)
    registry.set_component(
        actor_id,
        SpatialParentRelationComponent(
            specification.target_id,
            PhysicalRelationKind.OCCUPIES_SLOT,
            slot_id,
        ),
    )
    posture = _posture(registry, actor_id)
    posture.posture = (
        CharacterPosture.SITTING
        if specification.verb is InteractionVerb.SIT
        else CharacterPosture.LYING
    )
    posture.support_id = specification.target_id
    _set_actor_position(registry, actor_id, pose.anchor)
    _clear_movement(registry, actor_id)
    return {
        "posture": posture.posture.value,
        "slot_id": slot_id,
        "pose": pose.anchor.to_payload(),
    }


def _commit_exit(
    registry: Registry,
    actor_id: str,
    specification: InteractionSpecification,
) -> dict[str, JsonValue]:
    pose = _exit_pose(registry, actor_id, specification.target_id)
    if pose is None:
        raise SpatialCollisionError("no exit pose")
    actor_state = registry.get_component(
        actor_id,
        PhysicalStateComponent,
    )
    next_state = replace(actor_state, pose=pose)
    registry.get_resource(SpatialIndex).update(
        SpatialIndexEntry(actor_id, next_state, dynamic=True)
    )
    registry.set_component(actor_id, next_state)
    if registry.has_component(actor_id, SpatialParentRelationComponent):
        registry.remove_component(
            actor_id,
            SpatialParentRelationComponent,
        )
    posture = _posture(registry, actor_id)
    posture.posture = CharacterPosture.STANDING
    posture.support_id = None
    _set_actor_position(registry, actor_id, pose.anchor)
    return {
        "posture": posture.posture.value,
        "pose": pose.anchor.to_payload(),
    }


def _select_slot(
    registry: Registry,
    destination_id: str,
    relation_kind: PhysicalRelationKind,
    requested_slot_id: str | None,
) -> str:
    if not registry.has_component(destination_id, OccupancySlotsComponent):
        return f"failure:{InteractionFailureReason.SLOT_NOT_FOUND.value}"
    slots = registry.get_component(
        destination_id,
        OccupancySlotsComponent,
    )
    candidates: tuple[OccupancySlot, ...]
    if requested_slot_id is not None:
        try:
            candidates = (slots.slot(requested_slot_id),)
        except KeyError:
            return f"failure:{InteractionFailureReason.SLOT_NOT_FOUND.value}"
    else:
        candidates = tuple(sorted(slots.slots, key=lambda slot: slot.id))
    for slot in candidates:
        if relation_kind not in slot.accepted_relations:
            if requested_slot_id is not None:
                return (
                    f"failure:"
                    f"{InteractionFailureReason.SLOT_INCOMPATIBLE.value}"
                )
            continue
        count = sum(
            relation.parent_id == destination_id
            and relation.slot_id == slot.id
            for _, relation in registry.query(
                SpatialParentRelationComponent
            )
        )
        if count < slot.capacity:
            return slot.id
        if requested_slot_id is not None:
            return f"failure:{InteractionFailureReason.SLOT_AT_CAPACITY.value}"
    return f"failure:{InteractionFailureReason.SLOT_AT_CAPACITY.value}"


def _floor_pose(
    registry: Registry,
    actor_id: str,
    target_id: str,
) -> PhysicalPose | None:
    if not (
        registry.has_component(actor_id, PhysicalStateComponent)
        and registry.has_component(target_id, PhysicalStateComponent)
        and registry.has_resource(SpatialIndex)
    ):
        return None
    actor = registry.get_component(actor_id, PhysicalStateComponent)
    target = registry.get_component(target_id, PhysicalStateComponent)
    world = local_world_for_agent(registry, actor_id)
    if world is None:
        return None
    envelope = actor.footprint.contact_envelope(
        actor.pose.anchor,
        actor.pose.orientation,
    )
    candidates = {
        Coordinate(contact.x - offset.x, contact.y - offset.y)
        for contact in envelope
        for offset in target.footprint.rotated(
            target.pose.orientation
        ).cells
    }
    index = registry.get_resource(SpatialIndex)
    for anchor in sorted(
        candidates,
        key=lambda item: (
            abs(item.x - actor.pose.anchor.x)
            + abs(item.y - actor.pose.anchor.y),
            item.y,
            item.x,
        ),
    ):
        state = replace(
            target,
            pose=PhysicalPose(
                actor.pose.room_id,
                anchor,
                target.pose.orientation,
            ),
        )
        if state.occupied_cells & actor.occupied_cells:
            continue
        if not world.grid.are_walkable(state.occupied_cells):
            continue
        if index.can_place(state, excluding=target_id):
            return state.pose
    return None


def _occupancy_pose(
    registry: Registry,
    actor_id: str,
    target_id: str,
    slot_id: str,
) -> PhysicalPose | None:
    if not registry.has_resource(PhysicalInteractionRegistry):
        return None
    anchors = registry.get_resource(
        PhysicalInteractionRegistry
    ).occupancy_anchors(target_id, slot_id)
    actor = registry.get_component(actor_id, PhysicalStateComponent)
    index = registry.get_resource(SpatialIndex)
    for anchor in anchors:
        state = replace(
            actor,
            pose=PhysicalPose(actor.pose.room_id, anchor),
        )
        if index.can_place(
            state,
            excluding=actor_id,
            authorized_overlaps=frozenset({target_id}),
        ):
            return state.pose
    return None


def _exit_pose(
    registry: Registry,
    actor_id: str,
    target_id: str,
) -> PhysicalPose | None:
    if not registry.has_resource(PhysicalInteractionRegistry):
        return None
    actor = registry.get_component(actor_id, PhysicalStateComponent)
    index = registry.get_resource(SpatialIndex)
    world = local_world_for_agent(registry, actor_id)
    if world is None:
        return None
    for anchor in registry.get_resource(
        PhysicalInteractionRegistry
    ).approach_anchors(target_id):
        state = replace(
            actor,
            pose=PhysicalPose(actor.pose.room_id, anchor),
        )
        if (
            world.grid.are_walkable(state.occupied_cells)
            and index.can_place(state, excluding=actor_id)
        ):
            return state.pose
    return None


def _slot_anchor(
    registry: Registry,
    destination_id: str,
    slot_id: str,
    destination: PhysicalStateComponent,
) -> Coordinate:
    if registry.has_resource(PhysicalInteractionRegistry):
        anchors = registry.get_resource(
            PhysicalInteractionRegistry
        ).occupancy_anchors(destination_id, slot_id)
        if anchors:
            return anchors[0]
    return destination.pose.anchor


def _target_accessible(
    registry: Registry,
    actor_id: str,
    target_id: str,
) -> bool:
    if _is_held_by(registry, target_id, actor_id) or _is_equipped_by(
        registry,
        target_id,
        actor_id,
    ):
        return True
    if _hidden_in_closed_container(registry, target_id):
        return False
    return is_at_interaction_approach(registry, actor_id, target_id)


def _target_observable(
    registry: Registry,
    actor_id: str,
    target_id: str,
) -> bool:
    if _target_accessible(registry, actor_id, target_id):
        return True
    if not registry.has_component(actor_id, PerceptionComponent):
        return False
    perception = registry.get_component(actor_id, PerceptionComponent)
    visible: set[str] = getattr(perception, "visible_objects_now", set())
    knowledge: dict[str, int] = getattr(
        perception,
        "object_knowledge",
        {},
    )
    return target_id in visible or target_id in knowledge


def _hidden_in_closed_container(
    registry: Registry,
    target_id: str,
) -> bool:
    visited: set[str] = set()
    current = target_id
    while current not in visited:
        visited.add(current)
        relation = _relation(registry, current)
        if relation is None:
            return False
        if (
            relation.kind is PhysicalRelationKind.IN_CONTAINER
            and registry.has_component(
                relation.parent_id,
                OpenableComponent,
            )
            and not registry.get_component(
                relation.parent_id,
                OpenableComponent,
            ).is_open
        ):
            return True
        current = relation.parent_id
    return True


def _is_held_by(
    registry: Registry,
    target_id: str,
    actor_id: str,
) -> bool:
    relation = _relation(registry, target_id)
    return (
        relation is not None
        and relation.kind is PhysicalRelationKind.HELD_BY
        and relation.parent_id == actor_id
    )


def _is_equipped_by(
    registry: Registry,
    target_id: str,
    actor_id: str,
) -> bool:
    relation = _relation(registry, target_id)
    return (
        relation is not None
        and relation.kind is PhysicalRelationKind.ATTACHED_TO
        and relation.parent_id == actor_id
        and relation.slot_id is not None
        and registry.has_component(target_id, WearableComponent)
    )


def _equipped_in_slot(
    registry: Registry,
    actor_id: str,
    slot: EquipmentSlot,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            object_id
            for object_id, relation in registry.query(
                SpatialParentRelationComponent
            )
            if relation.kind is PhysicalRelationKind.ATTACHED_TO
            and relation.parent_id == actor_id
            and relation.slot_id == slot.value
            and registry.has_component(object_id, WearableComponent)
        )
    )


def _mass_failure(
    registry: Registry,
    actor_id: str,
    target_id: str,
) -> str | None:
    if not registry.has_component(actor_id, CharacterEmbodimentComponent):
        return None
    intrinsic = (
        registry.get_component(target_id, ObjectIntrinsicComponent)
        if registry.has_component(target_id, ObjectIntrinsicComponent)
        else None
    )
    if intrinsic is None or intrinsic.mass_kg is None:
        return None
    embodiment = registry.get_component(
        actor_id,
        CharacterEmbodimentComponent,
    )
    if intrinsic.mass_kg > embodiment.max_single_object_mass_kg:
        return InteractionFailureReason.OBJECT_TOO_HEAVY.value
    known_mass = _known_carried_mass(registry, actor_id)
    if known_mass + intrinsic.mass_kg > embodiment.max_carried_mass_kg:
        return InteractionFailureReason.CARRY_CAPACITY_EXCEEDED.value
    return None


def _current_load_failure(
    registry: Registry,
    actor_id: str,
) -> str | None:
    if not registry.has_component(actor_id, CharacterEmbodimentComponent):
        return None
    embodiment = registry.get_component(
        actor_id,
        CharacterEmbodimentComponent,
    )
    if _known_carried_mass(registry, actor_id) > embodiment.max_carried_mass_kg:
        return InteractionFailureReason.CARRY_CAPACITY_EXCEEDED.value
    return None


def _known_carried_mass(registry: Registry, actor_id: str) -> float:
    total = 0.0
    for object_id, relation in registry.query(SpatialParentRelationComponent):
        if relation.parent_id != actor_id or relation.kind not in {
            PhysicalRelationKind.HELD_BY,
            PhysicalRelationKind.ATTACHED_TO,
        }:
            continue
        if not registry.has_component(object_id, ObjectIntrinsicComponent):
            continue
        mass = registry.get_component(
            object_id,
            ObjectIntrinsicComponent,
        ).mass_kg
        if mass is not None:
            total += mass
    return round(total, 12)


def _relation(
    registry: Registry,
    entity_id: str,
) -> SpatialParentRelationComponent | None:
    if entity_id not in registry.entities():
        return None
    return (
        registry.get_component(entity_id, SpatialParentRelationComponent)
        if registry.has_component(
            entity_id,
            SpatialParentRelationComponent,
        )
        else None
    )


def _hands(
    registry: Registry,
    actor_id: str,
) -> CharacterHandStateComponent:
    if not registry.has_component(actor_id, CharacterHandStateComponent):
        registry.add_component(actor_id, CharacterHandStateComponent())
    return registry.get_component(actor_id, CharacterHandStateComponent)


def _posture(
    registry: Registry,
    actor_id: str,
) -> CharacterPostureComponent:
    if not registry.has_component(actor_id, CharacterPostureComponent):
        registry.add_component(actor_id, CharacterPostureComponent())
    return registry.get_component(actor_id, CharacterPostureComponent)


def _release_hands(
    registry: Registry,
    actor_id: str,
    target_id: str,
) -> None:
    hands = _hands(registry, actor_id)
    if hands.left_hand_object_id == target_id:
        hands.left_hand_object_id = None
    if hands.right_hand_object_id == target_id:
        hands.right_hand_object_id = None


def _set_actor_position(
    registry: Registry,
    actor_id: str,
    coordinate: Coordinate,
) -> None:
    registry.get_component(actor_id, PositionComponent).coordinate = coordinate
    if registry.has_component(actor_id, SpatialLocationComponent):
        spatial = registry.get_component(actor_id, SpatialLocationComponent)
        if spatial.location.local_coordinate is not None:
            spatial.location = replace(
                spatial.location,
                local_coordinate=coordinate,
            )


def _clear_movement(registry: Registry, actor_id: str) -> None:
    if not registry.has_component(actor_id, MovementComponent):
        return
    movement = registry.get_component(actor_id, MovementComponent)
    movement.destination = None
    movement.path = ()
    movement.retry_after_tick = 0
    movement.distance_remainder = 0.0
    movement.planned_spatial_revision = None


def _actor_room_id(registry: Registry, actor_id: str) -> str:
    if registry.has_component(actor_id, PhysicalStateComponent):
        return registry.get_component(
            actor_id,
            PhysicalStateComponent,
        ).pose.room_id
    if registry.has_component(actor_id, SpatialLocationComponent):
        return registry.get_component(
            actor_id,
            SpatialLocationComponent,
        ).location.place_id
    return "implicit-building"


def _specification_payload(
    specification: InteractionSpecification,
) -> dict[str, JsonValue]:
    return {
        "verb": specification.verb.value,
        "target_id": specification.target_id,
        "destination_id": specification.destination_id,
        "slot_id": specification.slot_id,
    }


def _correlation_id(action: ActionInstance | None) -> str | None:
    return action.root_correlation_id if action is not None else None
