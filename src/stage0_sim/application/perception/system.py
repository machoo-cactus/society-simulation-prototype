import json
from collections import deque
from dataclasses import dataclass

from stage0_sim.application.environment import EnvironmentInformationService
from stage0_sim.application.navigation import NavigationService
from stage0_sim.domain.components import (
    ActivityComponent,
    CustodyComponent,
    EffectiveSensesComponent,
    ObjectIntrinsicComponent,
    OpenableComponent,
    PerceptionComponent,
    PhysicalObjectIdentityComponent,
    PhysicalRelationKind,
    PhysicalStateComponent,
    PositionComponent,
    ScentSourceComponent,
    SensesComponent,
    SpatialIndex,
    SpatialLocationComponent,
    SpatialParentRelationComponent,
    WearableComponent,
)
from stage0_sim.domain.environment import WeatherRuntime
from stage0_sim.domain.events import DomainEvent, JsonValue
from stage0_sim.domain.perception import (
    DisclosureClass,
    Modality,
    PerceivedFact,
    PerceptibleFact,
    sensory_sweep,
)
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.interactions import physical_object_is_exposed
from stage0_sim.domain.systems.spatial_context import local_world_for_agent
from stage0_sim.domain.world import (
    Coordinate,
    SenseModality,
    WorldGrid,
    WorldMap,
)


@dataclass(frozen=True, slots=True)
class PerceptionConfiguration:
    vision_range: int = 8
    recognition_range: int = 5
    voice_range: int = 10
    whisper_range: int = 2
    blocked_tiles_are_opaque: bool = True
    inbox_limit: int = 100
    fact_max_age_seconds: float = 300.0

    def __post_init__(self) -> None:
        if min(self.vision_range, self.recognition_range, self.voice_range) < 0:
            raise ValueError("perception ranges must not be negative")
        if self.recognition_range > self.vision_range:
            raise ValueError("recognition range must not exceed vision range")
        if self.inbox_limit <= 0 or self.fact_max_age_seconds <= 0:
            raise ValueError("perception limits must be greater than zero")


@dataclass(slots=True)
class PerceptionSystem:
    name: str = "perception"
    order: int = 250
    _event_cursor: int = 0
    _fact_sequence: int = 0

    def update(self, context: SystemContext) -> None:
        if not context.registry.has_resource(WorldMap):
            return
        configuration = context.registry.get_resource(PerceptionConfiguration)
        pending_events = context.events.events[self._event_cursor :]
        self._event_cursor = len(context.events.events)
        observers = tuple(
            context.registry.query_entities(
                PositionComponent, PerceptionComponent, SensesComponent
            )
        )
        for observer_id in observers:
            observer_world = local_world_for_agent(
                context.registry, observer_id
            )
            if observer_world is None:
                continue
            self._scan_visible(
                context,
                observer_world,
                configuration,
                observer_id,
            )
            self._scan_visible_objects(
                context,
                observer_world,
                configuration,
                observer_id,
            )
            self._scan_scents(
                context,
                observer_world,
                observer_id,
            )
        for event in pending_events:
            if event.event_type == "speech.started":
                self._route_speech(context, configuration, event, observers)
            elif event.event_type == "agent.moved":
                self._route_movement(context, event, observers)
            elif event.event_type in {"activity.changed", "affordance.started"}:
                self._route_visual_event(context, event, observers)
            elif event.event_type in {
                "interaction.started",
                "interaction.completed",
                "interaction.failed",
                "interaction.cancelled",
            }:
                self._route_interaction_event(context, event, observers)
            elif event.event_type == "time.updated":
                self._route_time_update(context, event, observers)
            elif event.event_type == "weather.changed":
                self._route_environment_update(
                    context,
                    event,
                    observers,
                    topic="weather",
                    fact_type="weather_changed",
                )
            elif event.event_type == "availability.changed":
                self._route_environment_update(
                    context,
                    event,
                    observers,
                    topic="availability",
                    fact_type="availability_changed",
                )
        for observer_id in observers:
            perception = context.registry.get_component(
                observer_id, PerceptionComponent
            )
            perception.last_processed_tick = context.clock.tick

    def _scan_visible(
        self,
        context: SystemContext,
        world: WorldMap,
        configuration: PerceptionConfiguration,
        observer_id: str,
    ) -> None:
        observer_position = context.registry.get_component(
            observer_id, PositionComponent
        ).coordinate
        senses = _effective_senses(context.registry, observer_id)
        perception = context.registry.get_component(
            observer_id, PerceptionComponent
        )
        vision_range = senses.vision_range
        if (
            context.registry.has_resource(WeatherRuntime)
            and context.registry.has_component(
                observer_id, SpatialLocationComponent
            )
            and context.registry.get_component(
                observer_id, SpatialLocationComponent
            ).location.local_coordinate
            is None
        ):
            vision_range = int(
                vision_range
                * context.registry.get_resource(
                    WeatherRuntime
                ).effects.visibility_multiplier
            )
        visible: set[str] = set()
        for subject_id in context.registry.query_entities(PositionComponent):
            if subject_id == observer_id:
                continue
            subject_position = context.registry.get_component(
                subject_id, PositionComponent
            ).coordinate
            if not _same_local_place(context, observer_id, subject_id):
                continue
            if not _can_sense(
                world.grid,
                room_id=_room_id(context, observer_id),
                origin_cells=_entity_cells(
                    context.registry,
                    observer_id,
                    observer_position,
                ),
                target_cells=_entity_cells(
                    context.registry,
                    subject_id,
                    subject_position,
                ),
                maximum_range=vision_range,
                modality=SenseModality.VISION,
                spatial_index=(
                    context.registry.get_resource(SpatialIndex)
                    if context.registry.has_resource(SpatialIndex)
                    else None
                ),
                ignored_entity_ids=frozenset({observer_id, subject_id}),
            ):
                continue
            visible.add(subject_id)
            previous = perception.last_positions.get(subject_id)
            if subject_id not in perception.visible_now:
                self._deliver(
                    context,
                    observer_id,
                    self._fact(
                        context,
                        "entity_seen",
                        Modality.VISUAL,
                        DisclosureClass.LOCAL_VISUAL,
                        subject_id=subject_id,
                        location_id=_zone_id(world, subject_position),
                        properties={
                            "coordinate": subject_position.to_payload(),
                            "display_name": _display_name(context, subject_id),
                        },
                    ),
                    salience=0.7,
                )
            elif previous is not None and previous != subject_position:
                previous_zone = _zone_id(world, previous)
                current_zone = _zone_id(world, subject_position)
                if previous_zone != current_zone:
                    if previous_zone is not None:
                        self._deliver(
                            context,
                            observer_id,
                            self._fact(
                                context,
                                "entity_left_zone",
                                Modality.VISUAL,
                                DisclosureClass.LOCAL_VISUAL,
                                subject_id=subject_id,
                                location_id=previous_zone,
                                properties={
                                    "display_name": _display_name(
                                        context, subject_id
                                    )
                                },
                            ),
                            salience=0.8,
                        )
                    if current_zone is not None:
                        self._deliver(
                            context,
                            observer_id,
                            self._fact(
                                context,
                                "entity_entered_zone",
                                Modality.VISUAL,
                                DisclosureClass.LOCAL_VISUAL,
                                subject_id=subject_id,
                                location_id=current_zone,
                                properties={
                                    "display_name": _display_name(
                                        context, subject_id
                                    )
                                },
                            ),
                            salience=0.8,
                        )
                else:
                    self._deliver(
                        context,
                        observer_id,
                        self._fact(
                            context,
                            "entity_moved",
                            Modality.VISUAL,
                            DisclosureClass.LOCAL_VISUAL,
                            subject_id=subject_id,
                            location_id=current_zone,
                            properties={
                                "coordinate": subject_position.to_payload(),
                                "display_name": _display_name(context, subject_id),
                            },
                        ),
                    )
            activity = (
                context.registry.get_component(subject_id, ActivityComponent).current.value
                if context.registry.has_component(subject_id, ActivityComponent)
                else None
            )
            perception.last_positions[subject_id] = subject_position
            if activity is not None:
                perception.last_activities[subject_id] = activity
            from stage0_sim.domain.components.perception import KnowledgeRecord

            perception.knowledge[subject_id] = KnowledgeRecord(
                subject_id=subject_id,
                last_seen_coordinate=subject_position,
                last_seen_zone_id=_zone_id(world, subject_position),
                last_activity=activity,
                observed_tick=context.clock.tick,
            )
        for lost_id in sorted(perception.visible_now - visible):
            self._deliver(
                context,
                observer_id,
                self._fact(
                    context,
                    "entity_lost",
                    Modality.VISUAL,
                    DisclosureClass.LOCAL_VISUAL,
                    subject_id=lost_id,
                    properties={"display_name": _display_name(context, lost_id)},
                ),
            )
        perception.visible_now = visible

    def _scan_visible_objects(
        self,
        context: SystemContext,
        world: WorldMap,
        configuration: PerceptionConfiguration,
        observer_id: str,
    ) -> None:
        registry = context.registry
        observer_position = registry.get_component(
            observer_id,
            PositionComponent,
        ).coordinate
        senses = _effective_senses(registry, observer_id)
        perception = registry.get_component(
            observer_id,
            PerceptionComponent,
        )
        room_id = _room_id(context, observer_id)
        visible: set[str] = set()
        for target_id in registry.query_entities(
            PhysicalObjectIdentityComponent,
            PhysicalStateComponent,
        ):
            state = registry.get_component(
                target_id,
                PhysicalStateComponent,
            )
            if state.pose.room_id != room_id:
                continue
            if not physical_object_is_exposed(registry, target_id):
                continue
            relation = (
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
                relation is not None
                and relation.kind is PhysicalRelationKind.HELD_BY
                and relation.parent_id != observer_id
                and relation.parent_id not in perception.visible_now
            ):
                continue
            target_coordinate = min(
                state.occupied_cells,
                key=lambda coordinate: (
                    abs(coordinate.x - observer_position.x)
                    + abs(coordinate.y - observer_position.y),
                    coordinate.y,
                    coordinate.x,
                ),
            )
            sweep_distance = 0
            if (
                relation is None
                or relation.kind is not PhysicalRelationKind.HELD_BY
                or relation.parent_id != observer_id
            ):
                sweep = sensory_sweep(
                    world.grid,
                    room_id=room_id,
                    origin_cells=_entity_cells(
                        registry,
                        observer_id,
                        observer_position,
                    ),
                    target_cells=state.occupied_cells,
                    maximum_range=senses.vision_range,
                    modality=SenseModality.VISION,
                    spatial_index=(
                        registry.get_resource(SpatialIndex)
                        if registry.has_resource(SpatialIndex)
                        else None
                    ),
                    ignored_entity_ids=frozenset({observer_id, target_id}),
                )
                if not sweep.clear:
                    continue
                sweep_distance = sweep.distance or 0
            visible.add(target_id)
            recognized = sweep_distance <= senses.recognition_range
            if recognized:
                perception.recognized_objects_now.add(target_id)
            else:
                perception.recognized_objects_now.discard(target_id)
            public_state = _physical_public_state(
                context,
                observer_id,
                target_id,
                recognized=recognized,
            )
            signature = json.dumps(
                public_state,
                sort_keys=True,
                separators=(",", ":"),
            )
            if target_id not in perception.visible_objects_now:
                self._deliver(
                    context,
                    observer_id,
                    self._fact(
                        context,
                        "physical_object_seen",
                        Modality.VISUAL,
                        DisclosureClass.LOCAL_VISUAL,
                        subject_id=target_id,
                        location_id=_zone_id(world, target_coordinate),
                        properties={
                            "display_name": registry.get_component(
                                target_id,
                                PhysicalObjectIdentityComponent,
                            ).name,
                            "public_state": public_state,
                        },
                    ),
                    salience=0.6,
                )
            elif perception.last_object_states.get(target_id) != signature:
                self._deliver(
                    context,
                    observer_id,
                    self._fact(
                        context,
                        "physical_object_state_changed",
                        Modality.VISUAL,
                        DisclosureClass.LOCAL_VISUAL,
                        subject_id=target_id,
                        location_id=_zone_id(world, target_coordinate),
                        properties={
                            "display_name": registry.get_component(
                                target_id,
                                PhysicalObjectIdentityComponent,
                            ).name,
                            "public_state": public_state,
                        },
                    ),
                    salience=0.8,
                )
            perception.object_knowledge[target_id] = context.clock.tick
            perception.last_object_states[target_id] = signature
        for lost_id in sorted(perception.visible_objects_now - visible):
            self._deliver(
                context,
                observer_id,
                self._fact(
                    context,
                    "physical_object_lost",
                    Modality.VISUAL,
                    DisclosureClass.LOCAL_VISUAL,
                    subject_id=lost_id,
                    properties={"display_name": _display_name(context, lost_id)},
                ),
            )
        perception.visible_objects_now = visible
        perception.recognized_objects_now.intersection_update(visible)

    def _scan_scents(
        self,
        context: SystemContext,
        world: WorldMap,
        observer_id: str,
    ) -> None:
        registry = context.registry
        senses = _effective_senses(registry, observer_id)
        perception = registry.get_component(observer_id, PerceptionComponent)
        if senses.smell_range <= 0:
            for lost_id in sorted(perception.smelled_objects_now):
                self._deliver_scent_lost(context, observer_id, lost_id)
            perception.smelled_objects_now.clear()
            return
        observer_position = registry.get_component(
            observer_id,
            PositionComponent,
        ).coordinate
        room_id = _room_id(context, observer_id)
        smelled: set[str] = set()
        for source_id in registry.query_entities(
            ScentSourceComponent,
            PhysicalStateComponent,
        ):
            state = registry.get_component(source_id, PhysicalStateComponent)
            if state.pose.room_id != room_id:
                continue
            if not physical_object_is_exposed(registry, source_id):
                continue
            source = registry.get_component(source_id, ScentSourceComponent)
            result = sensory_sweep(
                world.grid,
                room_id=room_id,
                origin_cells=_entity_cells(
                    registry,
                    observer_id,
                    observer_position,
                ),
                target_cells=state.occupied_cells,
                maximum_range=min(senses.smell_range, source.emission_range),
                modality=SenseModality.SMELL,
                spatial_index=(
                    registry.get_resource(SpatialIndex)
                    if registry.has_resource(SpatialIndex)
                    else None
                ),
                ignored_entity_ids=frozenset({observer_id, source_id}),
            )
            if not result.clear:
                continue
            smelled.add(source_id)
            signature = json.dumps(
                {
                    "scent_id": source.scent_id,
                    "description": source.description,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            fact_type = (
                "scent_detected"
                if source_id not in perception.smelled_objects_now
                else "scent_changed"
                if perception.last_scent_states.get(source_id) != signature
                else None
            )
            if fact_type is not None:
                self._deliver(
                    context,
                    observer_id,
                    self._fact(
                        context,
                        fact_type,
                        Modality.OLFACTORY,
                        DisclosureClass.LOCAL_OLFACTORY,
                        subject_id=source_id,
                        properties={
                            "display_name": _display_name(context, source_id),
                            "scent_id": source.scent_id,
                            "description": source.description,
                        },
                    ),
                    salience=0.6,
                )
            perception.last_scent_states[source_id] = signature
        for lost_id in sorted(perception.smelled_objects_now - smelled):
            self._deliver_scent_lost(context, observer_id, lost_id)
        perception.smelled_objects_now = smelled

    def _deliver_scent_lost(
        self,
        context: SystemContext,
        observer_id: str,
        source_id: str,
    ) -> None:
        self._deliver(
            context,
            observer_id,
            self._fact(
                context,
                "scent_lost",
                Modality.OLFACTORY,
                DisclosureClass.LOCAL_OLFACTORY,
                subject_id=source_id,
                properties={"display_name": _display_name(context, source_id)},
            ),
        )

    def _route_interaction_event(
        self,
        context: SystemContext,
        event: DomainEvent,
        observers: tuple[str, ...],
    ) -> None:
        if event.agent_id is None:
            return
        target_id = _string_value(event.payload.get("target_id"))
        if target_id is None:
            return
        for observer_id in observers:
            perception = context.registry.get_component(
                observer_id,
                PerceptionComponent,
            )
            if (
                event.agent_id != observer_id
                and event.agent_id not in perception.visible_now
            ):
                continue
            if (
                target_id not in perception.visible_objects_now
                and event.agent_id != observer_id
            ):
                continue
            self._deliver(
                context,
                observer_id,
                self._fact(
                    context,
                    "physical_interaction_observed",
                    Modality.VISUAL,
                    DisclosureClass.LOCAL_VISUAL,
                    subject_id=event.agent_id,
                    object_id=target_id,
                    event_id=event.event_id,
                    properties={
                        "verb": str(event.payload.get("verb", "")),
                        "status": event.event_type.removeprefix(
                            "interaction."
                        ),
                        "display_name": _display_name(
                            context,
                            event.agent_id,
                        ),
                        "target_name": _display_name(context, target_id),
                    },
                ),
                salience=0.8,
            )

    def _route_visual_event(
        self,
        context: SystemContext,
        event: DomainEvent,
        observers: tuple[str, ...],
    ) -> None:
        if event.agent_id is None or not context.registry.has_component(
            event.agent_id, PositionComponent
        ):
            return
        activity = event.payload.get("current") or event.payload.get("action")
        for observer_id in observers:
            perception = context.registry.get_component(
                observer_id, PerceptionComponent
            )
            if event.agent_id not in perception.visible_now:
                continue
            observer_world = local_world_for_agent(
                context.registry, observer_id
            )
            if observer_world is None:
                continue
            self._deliver(
                context,
                observer_id,
                self._fact(
                    context,
                    "visible_activity_started",
                    Modality.VISUAL,
                    DisclosureClass.LOCAL_VISUAL,
                    subject_id=event.agent_id,
                    location_id=_zone_id(
                        observer_world,
                        context.registry.get_component(
                            event.agent_id, PositionComponent
                        ).coordinate,
                    ),
                    event_id=event.event_id,
                    properties={
                        "activity": str(activity),
                        "display_name": _display_name(context, event.agent_id),
                    },
                ),
            )

    def _route_time_update(
        self,
        context: SystemContext,
        event: DomainEvent,
        observers: tuple[str, ...],
    ) -> None:
        for observer_id in observers:
            perception = context.registry.get_component(
                observer_id, PerceptionComponent
            )
            perception.inbox[:] = [
                item
                for item in perception.inbox
                if item.fact.fact_type != "time_updated"
            ]
            self._deliver(
                context,
                observer_id,
                self._fact(
                    context,
                    "time_updated",
                    Modality.ENVIRONMENTAL,
                    DisclosureClass.PUBLIC_WORLD,
                    event_id=event.event_id,
                    properties=dict(event.payload),
                ),
                salience=0.8,
            )

    def _route_environment_update(
        self,
        context: SystemContext,
        event: DomainEvent,
        observers: tuple[str, ...],
        *,
        topic: str,
        fact_type: str,
    ) -> None:
        service = context.registry.get_resource(EnvironmentInformationService)
        for observer_id in observers:
            resource_id = event.payload.get("resource_id")
            if (
                topic == "availability"
                and isinstance(resource_id, str)
                and not self._resource_is_known(
                    context,
                    observer_id,
                    resource_id,
                )
            ):
                continue
            result = service.query(
                observer_id,
                context.clock.simulation_time,
                frozenset({topic}),
                availability_resource_ids=(
                    frozenset({resource_id})
                    if isinstance(resource_id, str)
                    else None
                ),
            )
            if topic in result.unavailable_topics:
                continue
            perception = context.registry.get_component(
                observer_id, PerceptionComponent
            )
            if fact_type == "weather_changed":
                perception.inbox[:] = [
                    item
                    for item in perception.inbox
                    if item.fact.fact_type != fact_type
                ]
            self._deliver(
                context,
                observer_id,
                self._fact(
                    context,
                    fact_type,
                    Modality.ENVIRONMENTAL,
                    DisclosureClass.PUBLIC_WORLD,
                    subject_id=(
                        str(resource_id)
                        if isinstance(resource_id, str)
                        else None
                    ),
                    event_id=event.event_id,
                    properties={
                        **dict(event.payload),
                        "environment": result.values,
                    },
                ),
                salience=0.8,
            )

    @staticmethod
    def _resource_is_known(
        context: SystemContext,
        observer_id: str,
        resource_id: str,
    ) -> bool:
        if not context.registry.has_resource(NavigationService):
            return False
        navigation = context.registry.get_resource(NavigationService)
        return resource_id in {
            destination.id
            for destination in navigation.known_topology.destinations(
                observer_id
            )
        }

    def _route_movement(
        self,
        context: SystemContext,
        event: DomainEvent,
        observers: tuple[str, ...],
    ) -> None:
        if event.agent_id is None:
            return
        previous = _coordinate_value(event.payload.get("from"))
        current = _coordinate_value(event.payload.get("to"))
        if previous is None or current is None:
            return
        for observer_id in observers:
            if observer_id == event.agent_id:
                continue
            perception = context.registry.get_component(
                observer_id, PerceptionComponent
            )
            if event.agent_id not in perception.visible_now:
                continue
            observer_world = local_world_for_agent(
                context.registry, observer_id
            )
            if observer_world is None:
                continue
            previous_zone = _zone_id(observer_world, previous)
            current_zone = _zone_id(observer_world, current)
            fact_type = "entity_moved"
            location_id = current_zone
            if previous_zone != current_zone and previous_zone is not None:
                fact_type = "entity_left_zone"
                location_id = previous_zone
            self._deliver(
                context,
                observer_id,
                self._fact(
                    context,
                    fact_type,
                    Modality.VISUAL,
                    DisclosureClass.LOCAL_VISUAL,
                    subject_id=event.agent_id,
                    location_id=location_id,
                    event_id=event.event_id,
                    properties={
                        "coordinate": current.to_payload(),
                        "display_name": _display_name(context, event.agent_id),
                    },
                ),
                salience=0.8 if fact_type == "entity_left_zone" else 0.5,
            )

    def _route_speech(
        self,
        context: SystemContext,
        configuration: PerceptionConfiguration,
        event: DomainEvent,
        observers: tuple[str, ...],
    ) -> None:
        speaker_id = event.agent_id
        text = event.payload.get("text")
        channel = event.payload.get("channel", "voice")
        if (
            speaker_id is None
            or not isinstance(text, str)
            or not context.registry.has_component(speaker_id, PositionComponent)
        ):
            return
        source = context.registry.get_component(
            speaker_id, PositionComponent
        ).coordinate
        base_range = (
            configuration.whisper_range
            if channel == "whisper"
            else configuration.voice_range
        )
        recipients: list[str] = []
        for observer_id in observers:
            if observer_id == speaker_id:
                continue
            if not _same_local_place(context, speaker_id, observer_id):
                continue
            observer_world = local_world_for_agent(
                context.registry, observer_id
            )
            if observer_world is None:
                continue
            senses = _effective_senses(context.registry, observer_id)
            target = context.registry.get_component(
                observer_id, PositionComponent
            ).coordinate
            maximum = min(base_range, senses.hearing_range)
            result = sensory_sweep(
                observer_world.grid,
                room_id=_room_id(context, observer_id),
                origin_cells=_entity_cells(
                    context.registry,
                    speaker_id,
                    source,
                ),
                target_cells=_entity_cells(
                    context.registry,
                    observer_id,
                    target,
                ),
                maximum_range=maximum,
                modality=SenseModality.HEARING,
                spatial_index=(
                    context.registry.get_resource(SpatialIndex)
                    if context.registry.has_resource(SpatialIndex)
                    else None
                ),
                ignored_entity_ids=frozenset({speaker_id, observer_id}),
            )
            if not result.clear:
                continue
            recipients.append(observer_id)
            self._deliver(
                context,
                observer_id,
                self._fact(
                    context,
                    "heard_speech",
                    Modality.AUDITORY,
                    DisclosureClass.LOCAL_AUDITORY,
                    subject_id=speaker_id,
                    object_id=(
                        _string_value(event.payload.get("target_id"))
                    ),
                    event_id=event.event_id,
                    properties={
                        "text": text,
                        "channel": str(channel),
                        "display_name": _display_name(context, speaker_id),
                    },
                ),
                salience=0.9,
            )
        recipient_payload: list[JsonValue] = list(recipients)
        context.events.emit(
            "speech.delivered",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=speaker_id,
            payload={
                "target_id": event.payload.get("target_id"),
                "recipient_ids": recipient_payload,
                "text": text,
                "channel": str(channel),
            },
            causation_id=event.event_id,
            correlation_id=event.correlation_id or event.event_id,
        )
        from stage0_sim.domain.components import ConversationComponent

        for recipient_id in recipients:
            if context.registry.has_component(
                recipient_id, ConversationComponent
            ):
                context.registry.get_component(
                    recipient_id, ConversationComponent
                ).turns.append(text)

    def _deliver(
        self,
        context: SystemContext,
        observer_id: str,
        fact: PerceptibleFact,
        salience: float = 0.5,
    ) -> None:
        configuration = context.registry.get_resource(PerceptionConfiguration)
        perception = context.registry.get_component(
            observer_id, PerceptionComponent
        )
        minimum_tick = context.clock.tick - int(
            configuration.fact_max_age_seconds / context.clock.dt
        )
        expired = [
            item
            for item in perception.inbox
            if (
                item.fact.fact_type
                not in {"time_updated", "weather_changed"}
                and item.perceived_tick < minimum_tick
            )
        ]
        for item in expired:
            perception.inbox.remove(item)
            context.events.emit(
                "perception.dropped",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=observer_id,
                payload={
                    "fact_id": item.fact.fact_id,
                    "reason": "fact_max_age",
                    "observer_id": observer_id,
                    "perceived_tick": context.clock.tick,
                    "fact_age": round(
                        max(
                            0.0,
                            (context.clock.tick - item.fact.tick)
                            * context.clock.dt,
                        ),
                        12,
                    ),
                    "salience": item.salience,
                    "certainty": item.certainty,
                    "fact": _fact_payload(item.fact),
                },
            )
        perceived = PerceivedFact(
            fact=fact,
            observer_id=observer_id,
            perceived_tick=context.clock.tick,
            salience=salience,
        )
        perception.inbox.append(perceived)
        dropped: PerceivedFact | None = None
        if len(perception.inbox) > configuration.inbox_limit:
            dropped = min(
                perception.inbox,
                key=lambda item: (
                    item.salience,
                    item.perceived_tick,
                    item.fact.fact_id,
                ),
            )
            perception.inbox.remove(dropped)
        context.events.emit(
            "perception.delivered",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=observer_id,
            payload={
                "fact_id": fact.fact_id,
                "fact_type": fact.fact_type,
                "modality": fact.modality.value,
                "subject_id": fact.subject_id,
                "observer_id": observer_id,
                "perceived_tick": context.clock.tick,
                "fact_age": round(
                    max(0.0, (context.clock.tick - fact.tick) * context.clock.dt),
                    12,
                ),
                "salience": salience,
                "certainty": perceived.certainty,
                "disclosure": fact.disclosure.value,
                "object_id": fact.object_id,
                "location_id": fact.location_id,
                "fact": _fact_payload(fact),
            },
            causation_id=fact.event_id,
        )
        if dropped is not None:
            context.events.emit(
                "perception.dropped",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=observer_id,
                payload={
                    "fact_id": dropped.fact.fact_id,
                    "reason": "inbox_limit",
                    "observer_id": observer_id,
                    "perceived_tick": context.clock.tick,
                    "fact_age": round(
                        max(
                            0.0,
                            (context.clock.tick - dropped.fact.tick)
                            * context.clock.dt,
                        ),
                        12,
                    ),
                    "salience": dropped.salience,
                    "certainty": dropped.certainty,
                    "fact": _fact_payload(dropped.fact),
                },
            )

    def _fact(
        self,
        context: SystemContext,
        fact_type: str,
        modality: Modality,
        disclosure: DisclosureClass,
        *,
        subject_id: str | None = None,
        object_id: str | None = None,
        location_id: str | None = None,
        event_id: str | None = None,
        properties: dict[str, JsonValue] | None = None,
    ) -> PerceptibleFact:
        self._fact_sequence += 1
        return PerceptibleFact(
            fact_id=f"fact-{self._fact_sequence:08d}",
            event_id=event_id,
            tick=context.clock.tick,
            fact_type=fact_type,
            subject_id=subject_id,
            object_id=object_id,
            location_id=location_id,
            properties=properties or {},
            modality=modality,
            disclosure=disclosure,
        )


def _fact_payload(fact: PerceptibleFact) -> dict[str, JsonValue]:
    return {
        "fact_id": fact.fact_id,
        "event_id": fact.event_id,
        "tick": fact.tick,
        "fact_type": fact.fact_type,
        "subject_id": fact.subject_id,
        "object_id": fact.object_id,
        "location_id": fact.location_id,
        "properties": dict(fact.properties),
        "modality": fact.modality.value,
        "disclosure": fact.disclosure.value,
    }


def _display_name(context: SystemContext, entity_id: str) -> str:
    from stage0_sim.domain.components import CharacterProfileComponent

    if context.registry.has_component(entity_id, CharacterProfileComponent):
        return context.registry.get_component(
            entity_id, CharacterProfileComponent
        ).display_name
    if context.registry.has_component(
        entity_id,
        PhysicalObjectIdentityComponent,
    ):
        return context.registry.get_component(
            entity_id,
            PhysicalObjectIdentityComponent,
        ).name
    return entity_id


def _zone_id(world: WorldMap, coordinate: Coordinate) -> str | None:
    zone = world.zone_at(coordinate)
    return zone.id if zone is not None else None


def _can_sense(
    grid: WorldGrid,
    *,
    room_id: str,
    origin_cells: frozenset[Coordinate],
    target_cells: frozenset[Coordinate],
    maximum_range: int,
    modality: SenseModality,
    spatial_index: SpatialIndex | None = None,
    ignored_entity_ids: frozenset[str] = frozenset(),
) -> bool:
    return sensory_sweep(
        grid,
        room_id=room_id,
        origin_cells=origin_cells,
        target_cells=target_cells,
        maximum_range=maximum_range,
        modality=modality,
        spatial_index=spatial_index,
        ignored_entity_ids=ignored_entity_ids,
    ).clear


def _line_cells(origin: Coordinate, target: Coordinate) -> tuple[Coordinate, ...]:
    x0, y0, x1, y1 = origin.x, origin.y, target.x, target.y
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    error = dx - dy
    cells: list[Coordinate] = []
    while True:
        cells.append(Coordinate(x0, y0))
        if x0 == x1 and y0 == y1:
            return tuple(cells)
        doubled = 2 * error
        if doubled > -dy:
            error -= dy
            x0 += sx
        if doubled < dx:
            error += dx
            y0 += sy


def _path_distance(
    grid: WorldGrid,
    origin: Coordinate,
    target: Coordinate,
    maximum: int,
) -> int | None:
    queue: deque[tuple[Coordinate, int]] = deque([(origin, 0)])
    visited = {origin}
    while queue:
        coordinate, distance = queue.popleft()
        if coordinate == target:
            return distance
        if distance >= maximum:
            continue
        for neighbor in grid.neighbors(coordinate):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, distance + 1))
    return None


def _string_value(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None


def _same_local_place(
    context: SystemContext,
    first_id: str,
    second_id: str,
) -> bool:
    if not (
        context.registry.has_component(first_id, SpatialLocationComponent)
        and context.registry.has_component(second_id, SpatialLocationComponent)
    ):
        return True
    first = context.registry.get_component(
        first_id, SpatialLocationComponent
    ).location
    second = context.registry.get_component(
        second_id, SpatialLocationComponent
    ).location
    return (
        first.local_coordinate is not None
        and second.local_coordinate is not None
        and first.place_id == second.place_id
    )


def _room_id(context: SystemContext, entity_id: str) -> str:
    if context.registry.has_component(entity_id, PhysicalStateComponent):
        return context.registry.get_component(
            entity_id,
            PhysicalStateComponent,
        ).pose.room_id
    if context.registry.has_component(entity_id, SpatialLocationComponent):
        return context.registry.get_component(
            entity_id,
            SpatialLocationComponent,
        ).location.place_id
    return "implicit-building"


def _physical_public_state(
    context: SystemContext,
    observer_id: str,
    target_id: str,
    *,
    recognized: bool,
) -> dict[str, JsonValue]:
    registry = context.registry
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
        custody = registry.get_component(target_id, CustodyComponent)
        state["custodian_id"] = custody.custodian_id
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
                key=lambda value: value.value,
            )
        ]
    if recognized and registry.has_component(target_id, ScentSourceComponent):
        scent = registry.get_component(target_id, ScentSourceComponent)
        state["scent"] = {
            "scent_id": scent.scent_id,
            "description": scent.description,
        }
    return state


def _effective_senses(
    registry: object,
    observer_id: str,
) -> EffectiveSensesComponent:
    from stage0_sim.domain.ecs import Registry

    if not isinstance(registry, Registry):
        raise TypeError("perception requires an ECS registry")
    if registry.has_component(observer_id, EffectiveSensesComponent):
        return registry.get_component(observer_id, EffectiveSensesComponent)
    base = registry.get_component(observer_id, SensesComponent)
    return EffectiveSensesComponent(
        vision_range=base.vision_range,
        recognition_range=base.recognition_range,
        hearing_range=base.hearing_range,
        smell_range=base.smell_range,
    )


def _entity_cells(
    registry: object,
    entity_id: str,
    fallback: Coordinate,
) -> frozenset[Coordinate]:
    from stage0_sim.domain.ecs import Registry

    if not isinstance(registry, Registry):
        raise TypeError("perception requires an ECS registry")
    if registry.has_component(entity_id, PhysicalStateComponent):
        return registry.get_component(
            entity_id,
            PhysicalStateComponent,
        ).occupied_cells
    return frozenset({fallback})


def _coordinate_value(value: JsonValue) -> Coordinate | None:
    if not isinstance(value, dict):
        return None
    x = value.get("x")
    y = value.get("y")
    if not isinstance(x, int) or not isinstance(y, int):
        return None
    return Coordinate(x, y)
