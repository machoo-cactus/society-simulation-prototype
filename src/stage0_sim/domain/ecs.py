from collections.abc import Iterator
from typing import TypeVar, cast

EntityId = str
T = TypeVar("T")


class Registry:
    """Entity/component storage with deterministic entity iteration."""

    def __init__(self) -> None:
        self._entities: set[EntityId] = set()
        self._components: dict[type[object], dict[EntityId, object]] = {}
        self._resources: dict[type[object], object] = {}
        self._next_entity_number = 1

    def create_entity(self, entity_id: EntityId | None = None) -> EntityId:
        if entity_id is None:
            entity_id = f"entity-{self._next_entity_number:06d}"
            self._next_entity_number += 1
        if not entity_id:
            raise ValueError("entity_id must not be empty")
        if entity_id in self._entities:
            raise ValueError(f"entity already exists: {entity_id}")
        self._entities.add(entity_id)
        return entity_id

    def delete_entity(self, entity_id: EntityId) -> None:
        self._require_entity(entity_id)
        self._entities.remove(entity_id)
        for component_store in self._components.values():
            component_store.pop(entity_id, None)

    def entities(self) -> tuple[EntityId, ...]:
        return tuple(sorted(self._entities))

    def add_component(self, entity_id: EntityId, component: object) -> None:
        self._require_entity(entity_id)
        component_type = type(component)
        store = self._components.setdefault(component_type, {})
        if entity_id in store:
            raise ValueError(
                f"entity {entity_id} already has component {component_type.__name__}"
            )
        store[entity_id] = component

    def set_component(self, entity_id: EntityId, component: object) -> None:
        self._require_entity(entity_id)
        self._components.setdefault(type(component), {})[entity_id] = component

    def get_component(self, entity_id: EntityId, component_type: type[T]) -> T:
        self._require_entity(entity_id)
        try:
            component = self._components[component_type][entity_id]
        except KeyError as error:
            raise KeyError(
                f"entity {entity_id} has no component {component_type.__name__}"
            ) from error
        return cast(T, component)

    def has_component(self, entity_id: EntityId, component_type: type[object]) -> bool:
        self._require_entity(entity_id)
        return entity_id in self._components.get(component_type, {})

    def remove_component(self, entity_id: EntityId, component_type: type[object]) -> None:
        self._require_entity(entity_id)
        try:
            del self._components[component_type][entity_id]
        except KeyError as error:
            raise KeyError(
                f"entity {entity_id} has no component {component_type.__name__}"
            ) from error

    def query(self, component_type: type[T]) -> Iterator[tuple[EntityId, T]]:
        store = self._components.get(component_type, {})
        for entity_id in sorted(store):
            yield entity_id, cast(T, store[entity_id])

    def query_entities(self, *component_types: type[object]) -> Iterator[EntityId]:
        if not component_types:
            yield from self.entities()
            return
        matching = set(self._components.get(component_types[0], {}))
        for component_type in component_types[1:]:
            matching.intersection_update(self._components.get(component_type, {}))
        yield from sorted(matching)

    def set_resource(self, resource: object) -> None:
        self._resources[type(resource)] = resource

    def get_resource(self, resource_type: type[T]) -> T:
        try:
            resource = self._resources[resource_type]
        except KeyError as error:
            raise KeyError(f"resource not registered: {resource_type.__name__}") from error
        return cast(T, resource)

    def has_resource(self, resource_type: type[object]) -> bool:
        return resource_type in self._resources

    def _require_entity(self, entity_id: EntityId) -> None:
        if entity_id not in self._entities:
            raise KeyError(f"unknown entity: {entity_id}")
