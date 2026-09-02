from dataclasses import dataclass, field

from stage0_sim.application.information import InformationStore
from stage0_sim.domain.components import (
    InformationNamespaceComponent,
    NavigationComponent,
    NavigationStatus,
    SpatialLocationComponent,
    TravelComponent,
)
from stage0_sim.domain.events import DomainEvent
from stage0_sim.domain.information import (
    InformationDocument,
    InformationSource,
    VisibilityLevel,
    VisibilityPolicy,
    canonical_json_hash,
)
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.world import Locator


@dataclass(slots=True)
class NavigationKnowledgeRecordingSystem:
    name: str = "navigation_knowledge_recording"
    order: int = 240
    _event_cursor: int = 0
    _active_navigation_travel: set[str] = field(default_factory=set)

    def update(self, context: SystemContext) -> None:
        events = context.events.events
        while self._event_cursor < len(events):
            event = events[self._event_cursor]
            if event.agent_id is None:
                self._event_cursor += 1
                continue
            if (
                event.event_type == "navigation.leg_started"
                and event.payload.get("primitive_kind") == "TRAVEL"
            ):
                self._active_navigation_travel.add(event.agent_id)
            elif event.event_type in {
                "navigation.failed",
                "navigation.interrupted",
            }:
                self._active_navigation_travel.discard(event.agent_id)
            elif event.event_type == "navigation.arrived":
                self._active_navigation_travel.discard(event.agent_id)
                self._record_navigation(context, event)
            elif event.event_type == "travel.arrived":
                if event.agent_id in self._active_navigation_travel:
                    self._active_navigation_travel.discard(event.agent_id)
                else:
                    self._record_standalone_travel(context, event)
            self._event_cursor += 1

    def _record_navigation(
        self,
        context: SystemContext,
        event: DomainEvent,
    ) -> None:
        character_id = event.agent_id
        if character_id is None:
            return
        navigation = context.registry.get_component(
            character_id,
            NavigationComponent,
        )
        if (
            navigation.status is not NavigationStatus.ARRIVED
            or navigation.route is None
            or navigation.target_id is None
        ):
            raise RuntimeError(
                "navigation.arrived requires retained successful route state"
            )
        transition_ids = tuple(
            dict.fromkeys(
                leg.transition_id
                for leg in navigation.route.legs
                if leg.transition_id is not None
            )
        )
        self._register(
            context,
            event,
            destination_id=navigation.target_id,
            final_locator=self._final_locator(
                context,
                character_id,
                navigation.route.destination,
            ),
            transition_ids=transition_ids,
        )

    def _record_standalone_travel(
        self,
        context: SystemContext,
        event: DomainEvent,
    ) -> None:
        character_id = event.agent_id
        if character_id is None:
            return
        travel = context.registry.get_component(character_id, TravelComponent)
        if travel.destination_id is None:
            raise RuntimeError(
                "travel.arrived requires retained successful travel state"
            )
        transition_ids = tuple(
            dict.fromkeys(
                (
                    *(leg.edge_id for leg in travel.route),
                    *(
                        (travel.destination_entrance_id,)
                        if travel.destination_entrance_id is not None
                        else ()
                    ),
                )
            )
        )
        self._register(
            context,
            event,
            destination_id=travel.destination_id,
            final_locator=self._final_locator(context, character_id),
            transition_ids=transition_ids,
        )

    @staticmethod
    def _final_locator(
        context: SystemContext,
        character_id: str,
        planned_destination: Locator | None = None,
    ) -> Locator:
        spatial = context.registry.get_component(
            character_id,
            SpatialLocationComponent,
        )
        locator = spatial.locator
        if locator is None:
            if planned_destination is None:
                raise RuntimeError(
                    "successful navigation requires a final locator"
                )
            return planned_destination
        if (
            planned_destination is not None
            and locator != planned_destination
            and locator.space_id != planned_destination.space_id
        ):
            raise RuntimeError(
                "successful navigation final locator does not match its route"
            )
        return locator

    @staticmethod
    def _register(
        context: SystemContext,
        event: DomainEvent,
        *,
        destination_id: str,
        final_locator: Locator,
        transition_ids: tuple[str, ...],
    ) -> None:
        character_id = event.agent_id
        if character_id is None:
            return
        namespace = context.registry.get_component(
            character_id,
            InformationNamespaceComponent,
        )
        reference_ids = tuple(
            dict.fromkeys(
                reference
                for reference in (event.event_id, event.correlation_id)
                if reference is not None
            )
        )
        document = InformationDocument.create(
            id=(
                f"knowledge-route:{character_id}:"
                f"{canonical_json_hash({'event_id': event.event_id})}"
            ),
            namespace_id=namespace.namespace_id,
            kind="knowledge.route",
            schema_id="knowledge.route.v1",
            subject_ids=(
                (character_id, destination_id)
                if character_id != destination_id
                else (character_id,)
            ),
            content={
                "destination_id": destination_id,
                "coordinate_system": (
                    "microcell"
                    if isinstance(final_locator.local_reference, dict)
                    and final_locator.local_reference.get("kind")
                    == "coordinate"
                    else None
                ),
                "locator": {
                    "space_id": final_locator.space_id,
                    "local_reference": final_locator.local_reference,
                },
                "transition_ids": list(transition_ids),
                "acquisition_source": "DIRECT_EXPERIENCE",
                "simulation_time": event.simulation_time,
                "navigation_event_id": event.event_id,
                "navigation_correlation_id": event.correlation_id,
            },
            source=InformationSource(
                type="DIRECT_EXPERIENCE",
                observer_id=character_id,
                reference_ids=reference_ids,
                metadata={
                    "event_type": event.event_type,
                    "navigation_event_id": event.event_id,
                    "navigation_correlation_id": event.correlation_id,
                },
            ),
            recorded_at=event.simulation_time,
            visibility=VisibilityPolicy(
                level=VisibilityLevel.PRIVATE,
                owner_ids=(character_id,),
            ),
        )
        registered = context.registry.get_resource(InformationStore).register(
            document
        )
        if registered.id in namespace.document_ids:
            return
        context.registry.set_component(
            character_id,
            InformationNamespaceComponent(
                namespace_id=namespace.namespace_id,
                document_ids=(*namespace.document_ids, registered.id),
            ),
        )
