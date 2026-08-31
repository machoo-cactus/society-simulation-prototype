from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Protocol

from stage0_sim.application.information import InformationStore
from stage0_sim.domain.components import SpatialLocationComponent
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.environment import EnvironmentAvailabilityRegistry
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.information import (
    InformationDocument,
    character_can_access_information,
    character_information_namespace_id,
)
from stage0_sim.domain.systems.spatial_context import local_world_for_agent
from stage0_sim.domain.world import Locator, SpaceRegistry


@dataclass(frozen=True, slots=True)
class KnownDestination:
    id: str
    kind: str
    name: str
    locators: tuple[Locator, ...]
    supported_actions: tuple[str, ...] = ()
    available: bool = True
    availability_reason: str | None = None


class KnownTopologyProjection(Protocol):
    def destinations(self, character_id: str) -> tuple[KnownDestination, ...]: ...

    def transition_ids(self, character_id: str) -> frozenset[str]: ...


class InformationKnownTopologyProjection:
    """Project character-known topology from information documents.

    The compatibility policy includes every zone and station in the
    character's current local space, plus registered transitions whose origin
    is in that space. Global buildings, outdoor places, locators, and other
    transitions are included only when referenced by ``knowledge.place`` or
    ``knowledge.route`` documents in the character namespace.
    """

    def __init__(
        self,
        information: InformationStore,
        topology: SpaceRegistry,
        registry: Registry,
    ) -> None:
        self.information = information
        self.topology = topology
        self.registry = registry

    def destinations(self, character_id: str) -> tuple[KnownDestination, ...]:
        projected: dict[str, KnownDestination] = {}
        for destination in self._local_awareness(character_id):
            projected[destination.id] = destination
        for document in self._knowledge_documents(character_id):
            content = document.content
            if not isinstance(content, dict):
                continue
            destination_ids = self._destination_ids(
                character_id,
                document.subject_ids,
                content,
            )
            explicit_locators = self._locators(content)
            kind = self._optional_text(content.get("kind"))
            name = self._optional_text(content.get("name"))
            for destination_id in destination_ids:
                candidate = KnownDestination(
                    id=destination_id,
                    kind=kind or "place",
                    name=name or destination_id,
                    locators=explicit_locators,
                )
                previous = projected.get(destination_id)
                projected[destination_id] = (
                    candidate
                    if previous is None
                    else KnownDestination(
                        id=destination_id,
                        kind=(
                            candidate.kind
                            if previous.kind == "place"
                            else previous.kind
                        ),
                        name=(
                            candidate.name
                            if previous.name == previous.id
                            else previous.name
                        ),
                        locators=self._unique_locators(
                            (*previous.locators, *candidate.locators)
                        ),
                        supported_actions=(
                            previous.supported_actions
                            or candidate.supported_actions
                        ),
                        available=previous.available and candidate.available,
                    )
                )
        availability = (
            self.registry.get_resource(EnvironmentAvailabilityRegistry)
            if self.registry.has_resource(EnvironmentAvailabilityRegistry)
            else None
        )
        return tuple(
            (
                replace(
                    projected[destination_id],
                    available=availability.state(destination_id).available,
                    availability_reason=availability.state(
                        destination_id
                    ).reason.value,
                )
                if availability is not None
                and destination_id in availability.states
                else projected[destination_id]
            )
            for destination_id in sorted(projected)
        )

    def transition_ids(self, character_id: str) -> frozenset[str]:
        transition_ids: set[str] = set()
        current = self._current_locator(character_id)
        if current is not None:
            space = self.topology.space(current.space_id)
            local_transitions = (
                self.topology.transitions_from(current)
                if space.kind == "city"
                else self.topology.registered_transitions_from_space(
                    current.space_id
                )
            )
            transition_ids.update(
                self._base_transition_id(transition.id)
                for transition in local_transitions
            )
        for document in self._knowledge_documents(character_id):
            content = document.content
            if not isinstance(content, dict):
                continue
            transition_ids.update(self._referenced_transition_ids(content))
        return frozenset(transition_ids)

    def _knowledge_documents(
        self,
        character_id: str,
    ) -> tuple[InformationDocument, ...]:
        return tuple(
            document
            for document in self.information.documents(
                namespace_id=character_information_namespace_id(character_id),
                kinds=("knowledge.place", "knowledge.route"),
            )
            if character_can_access_information(document, character_id)
        )

    def _local_awareness(
        self,
        character_id: str,
    ) -> tuple[KnownDestination, ...]:
        if not self.registry.has_component(character_id, SpatialLocationComponent):
            return ()
        current = self._current_locator(character_id)
        if current is None:
            return ()
        world = local_world_for_agent(self.registry, character_id)
        destinations: list[KnownDestination] = []
        for zone in sorted(world.zones, key=lambda item: item.id):
            locators = tuple(
                locator
                for locator in self.topology.destination_locators(zone.id)
                if locator.space_id == current.space_id
            )
            destinations.append(
                KnownDestination(zone.id, "zone", zone.name, locators)
            )
        for station in sorted(world.stations, key=lambda item: item.id):
            locators = tuple(
                locator
                for locator in self.topology.destination_locators(station.id)
                if locator.space_id == current.space_id
            )
            destinations.append(
                KnownDestination(
                    station.id,
                    "station",
                    station.name,
                    locators,
                    station.supported_actions,
                    (
                        self.registry.get_resource(
                            EnvironmentAvailabilityRegistry
                        ).state(
                            station.id,
                            base_available=station.available,
                        ).available
                        if self.registry.has_resource(
                            EnvironmentAvailabilityRegistry
                        )
                        else station.available
                    ),
                )
            )
        return tuple(destinations)

    def _current_locator(self, character_id: str) -> Locator | None:
        if not self.registry.has_component(character_id, SpatialLocationComponent):
            return None
        return self.registry.get_component(
            character_id,
            SpatialLocationComponent,
        ).locator

    def _destination_ids(
        self,
        character_id: str,
        subject_ids: tuple[str, ...],
        content: dict[str, JsonValue],
    ) -> tuple[str, ...]:
        ids: list[str] = []
        for key in ("destination_id", "place_id"):
            value = content.get(key)
            if isinstance(value, str) and value:
                ids.append(value)
        for key in ("destination_ids", "place_ids"):
            value = content.get(key)
            if isinstance(value, list):
                ids.extend(
                    item for item in value if isinstance(item, str) and item
                )
        ids.extend(
            subject_id
            for subject_id in subject_ids
            if subject_id != character_id
        )
        return tuple(dict.fromkeys(sorted(ids)))

    @staticmethod
    def _locators(content: dict[str, JsonValue]) -> tuple[Locator, ...]:
        payloads: list[JsonValue] = []
        singular = content.get("locator")
        if singular is not None:
            payloads.append(singular)
        for key in ("locators", "known_locators"):
            value = content.get(key)
            if isinstance(value, list):
                payloads.extend(value)
        locators: list[Locator] = []
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            space_id = payload.get("space_id")
            local_reference = payload.get("local_reference")
            if isinstance(space_id, str) and local_reference is not None:
                locators.append(Locator(space_id, local_reference))
        return InformationKnownTopologyProjection._unique_locators(locators)

    @staticmethod
    def _referenced_transition_ids(
        content: dict[str, JsonValue],
    ) -> tuple[str, ...]:
        ids: list[str] = []
        singular = content.get("transition_id")
        if isinstance(singular, str) and singular:
            ids.append(singular)
        for key in ("transition_ids", "known_transition_ids"):
            value = content.get(key)
            if isinstance(value, list):
                ids.extend(
                    item for item in value if isinstance(item, str) and item
                )
        transitions = content.get("transitions")
        if isinstance(transitions, list):
            for transition in transitions:
                if isinstance(transition, str) and transition:
                    ids.append(transition)
                elif isinstance(transition, dict):
                    transition_id = transition.get("id")
                    if isinstance(transition_id, str) and transition_id:
                        ids.append(transition_id)
        return tuple(dict.fromkeys(sorted(ids)))

    @staticmethod
    def _unique_locators(locators: Iterable[Locator]) -> tuple[Locator, ...]:
        unique = {locator.stable_key: locator for locator in locators}
        return tuple(unique[key] for key in sorted(unique))

    @staticmethod
    def _base_transition_id(transition_id: str) -> str:
        return transition_id.removesuffix(":reverse")

    @staticmethod
    def _optional_text(value: JsonValue) -> str | None:
        return value if isinstance(value, str) and value else None
