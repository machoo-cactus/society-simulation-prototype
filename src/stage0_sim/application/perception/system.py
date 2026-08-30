from collections import deque
from dataclasses import dataclass

from stage0_sim.domain.components import (
    ActivityComponent,
    PerceptionComponent,
    PositionComponent,
    SensesComponent,
    SpatialLocationComponent,
)
from stage0_sim.domain.events import DomainEvent, JsonValue
from stage0_sim.domain.perception import (
    DisclosureClass,
    Modality,
    PerceivedFact,
    PerceptibleFact,
)
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.spatial_context import local_world_for_agent
from stage0_sim.domain.world import Coordinate, WorldGrid, WorldMap


@dataclass(frozen=True, slots=True)
class PerceptionConfiguration:
    vision_range: int = 8
    recognition_range: int = 5
    hearing_range: int = 10
    whisper_range: int = 2
    blocked_tiles_are_opaque: bool = True
    inbox_limit: int = 100
    fact_max_age_seconds: float = 300.0

    def __post_init__(self) -> None:
        if min(self.vision_range, self.recognition_range, self.hearing_range) < 0:
            raise ValueError("perception ranges must not be negative")
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
        world = context.registry.get_resource(WorldMap)
        configuration = context.registry.get_resource(PerceptionConfiguration)
        pending_events = context.events.events[self._event_cursor :]
        self._event_cursor = len(context.events.events)
        observers = tuple(
            context.registry.query_entities(
                PositionComponent, PerceptionComponent, SensesComponent
            )
        )
        for observer_id in observers:
            self._scan_visible(
                context,
                local_world_for_agent(context.registry, observer_id),
                configuration,
                observer_id,
            )
        for event in pending_events:
            if event.event_type == "speech.started":
                self._route_speech(context, world, configuration, event, observers)
            elif event.event_type == "agent.moved":
                self._route_movement(context, world, event, observers)
            elif event.event_type in {"activity.changed", "affordance.started"}:
                self._route_visual_event(context, world, event, observers)
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
        senses = context.registry.get_component(observer_id, SensesComponent)
        perception = context.registry.get_component(
            observer_id, PerceptionComponent
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
            if not _can_see(
                world.grid,
                observer_position,
                subject_position,
                senses.vision_range,
                configuration.blocked_tiles_are_opaque,
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

    def _route_visual_event(
        self,
        context: SystemContext,
        world: WorldMap,
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

    def _route_movement(
        self,
        context: SystemContext,
        world: WorldMap,
        event: DomainEvent,
        observers: tuple[str, ...],
    ) -> None:
        if event.agent_id is None:
            return
        previous = _coordinate_value(event.payload.get("from"))
        current = _coordinate_value(event.payload.get("to"))
        if previous is None or current is None:
            return
        previous_zone = _zone_id(world, previous)
        current_zone = _zone_id(world, current)
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
        world: WorldMap,
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
            else configuration.hearing_range
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
            senses = context.registry.get_component(observer_id, SensesComponent)
            target = context.registry.get_component(
                observer_id, PositionComponent
            ).coordinate
            maximum = int(base_range * senses.hearing_multiplier)
            distance = _path_distance(
                observer_world.grid, source, target, maximum
            )
            if distance is None:
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
            if item.perceived_tick < minimum_tick
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


def _display_name(context: SystemContext, entity_id: str) -> str:
    from stage0_sim.domain.components import CharacterProfileComponent

    if context.registry.has_component(entity_id, CharacterProfileComponent):
        return context.registry.get_component(
            entity_id, CharacterProfileComponent
        ).display_name
    return entity_id


def _zone_id(world: WorldMap, coordinate: Coordinate) -> str | None:
    zone = world.zone_at(coordinate)
    return zone.id if zone is not None else None


def _can_see(
    grid: WorldGrid,
    origin: Coordinate,
    target: Coordinate,
    maximum_range: int,
    opaque_blocks: bool,
) -> bool:
    if abs(origin.x - target.x) + abs(origin.y - target.y) > maximum_range:
        return False
    if not opaque_blocks:
        return True
    return all(cell not in grid.blocked for cell in _line_cells(origin, target)[1:-1])


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
        first.scale.value == "BUILDING"
        and second.scale.value == "BUILDING"
        and first.place_id == second.place_id
    )


def _coordinate_value(value: JsonValue) -> Coordinate | None:
    if not isinstance(value, dict):
        return None
    x = value.get("x")
    y = value.get("y")
    if not isinstance(x, int) or not isinstance(y, int):
        return None
    return Coordinate(x, y)
