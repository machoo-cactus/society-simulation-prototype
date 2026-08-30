import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, cast

from stage0_sim.domain.events import JsonValue


def _canonical_json(value: JsonValue) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("locator reference must be a finite JSON value") from error


class _FrozenJsonMapping(Mapping[str, JsonValue]):
    __slots__ = ("_json",)

    def __init__(self, value: Mapping[str, JsonValue]) -> None:
        materialized = dict(value)
        if any(not isinstance(key, str) for key in materialized):
            raise ValueError("topology metadata keys must be strings")
        self._json = _canonical_json(cast(JsonValue, materialized))

    def __getitem__(self, key: str) -> JsonValue:
        return self._materialize()[key]

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._materialize())

    def __len__(self) -> int:
        return len(self._materialize())

    def __repr__(self) -> str:
        return repr(self._materialize())

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _FrozenJsonMapping):
            return self._json == other._json
        if isinstance(other, Mapping):
            try:
                return self._json == _canonical_json(
                    cast(JsonValue, dict(other))
                )
            except ValueError:
                return False
        return False

    def _materialize(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json.loads(self._json))


def _frozen_metadata(
    value: Mapping[str, JsonValue],
) -> Mapping[str, JsonValue]:
    return _FrozenJsonMapping(value)


@dataclass(frozen=True, slots=True, init=False, eq=False)
class Locator:
    space_id: str
    _reference_json: str = field(repr=False)
    stable_key: str

    def __init__(self, space_id: str, local_reference: JsonValue) -> None:
        if not space_id:
            raise ValueError("locator space_id must not be empty")
        reference_json = _canonical_json(local_reference)
        object.__setattr__(self, "space_id", space_id)
        object.__setattr__(self, "_reference_json", reference_json)
        object.__setattr__(
            self,
            "stable_key",
            f"{_canonical_json(space_id)}:{reference_json}",
        )

    @property
    def local_reference(self) -> JsonValue:
        return cast(JsonValue, json.loads(self._reference_json))

    def __hash__(self) -> int:
        return hash(self.stable_key)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Locator) and self.stable_key == other.stable_key

    def __lt__(self, other: "Locator") -> bool:
        return self.stable_key < other.stable_key


@dataclass(frozen=True, slots=True)
class Transition:
    id: str
    from_locator: Locator
    to_locator: Locator
    traversal_kind: str
    executor_id: str
    cost_model_id: str
    bidirectional: bool = False
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("transition id must not be empty")
        if not self.traversal_kind:
            raise ValueError("transition traversal_kind must not be empty")
        if not self.executor_id:
            raise ValueError("transition executor_id must not be empty")
        if not self.cost_model_id:
            raise ValueError("transition cost_model_id must not be empty")
        object.__setattr__(self, "metadata", _frozen_metadata(self.metadata))

    @property
    def stable_key(self) -> tuple[str, str, str]:
        return (self.id, self.from_locator.stable_key, self.to_locator.stable_key)

    def reverse(self) -> "Transition":
        if not self.bidirectional:
            raise ValueError(f"transition {self.id} is not bidirectional")
        return Transition(
            id=f"{self.id}:reverse",
            from_locator=self.to_locator,
            to_locator=self.from_locator,
            traversal_kind=self.traversal_kind,
            executor_id=self.executor_id,
            cost_model_id=self.cost_model_id,
            metadata=self.metadata,
        )


@dataclass(frozen=True, slots=True)
class RouteLeg:
    origin: Locator
    destination: Locator
    traversal_kind: str
    executor_id: str
    cost: float
    transition_id: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.traversal_kind:
            raise ValueError("route leg traversal_kind must not be empty")
        if not self.executor_id:
            raise ValueError("route leg executor_id must not be empty")
        if self.cost < 0:
            raise ValueError("route leg cost must not be negative")
        object.__setattr__(self, "metadata", _frozen_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class LocalRoute:
    origin: Locator
    destination: Locator
    legs: tuple[RouteLeg, ...]
    total_cost: float

    def __post_init__(self) -> None:
        if self.origin.space_id != self.destination.space_id:
            raise ValueError("local route endpoints must be in the same space")
        if self.total_cost < 0:
            raise ValueError("local route total_cost must not be negative")
        if self.legs:
            if self.legs[0].origin != self.origin:
                raise ValueError("local route first leg must start at origin")
            if self.legs[-1].destination != self.destination:
                raise ValueError("local route last leg must end at destination")
            for previous, current in zip(self.legs, self.legs[1:], strict=False):
                if previous.destination != current.origin:
                    raise ValueError("local route legs must be contiguous")
        elif self.origin != self.destination:
            raise ValueError("non-trivial local route must contain legs")


@dataclass(frozen=True, slots=True)
class Route:
    origin: Locator
    destination: Locator
    legs: tuple[RouteLeg, ...]
    planned_from_topology_revision: int

    def __post_init__(self) -> None:
        if self.planned_from_topology_revision < 0:
            raise ValueError("topology revision must not be negative")


@dataclass(frozen=True, slots=True)
class TraversalContext:
    character_id: str | None = None
    requested_mode: str | None = None
    occupied_locators: tuple[Locator, ...] = ()
    allowed_transition_ids: frozenset[str] | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.allowed_transition_ids is not None and (
            not isinstance(self.allowed_transition_ids, frozenset)
            or any(not transition_id for transition_id in self.allowed_transition_ids)
        ):
            raise ValueError(
                "allowed transition IDs must be a frozenset of non-empty IDs"
            )
        object.__setattr__(self, "metadata", _frozen_metadata(self.metadata))


class SpaceTopology(Protocol):
    @property
    def space_id(self) -> str: ...

    def resolve(self, reference: JsonValue) -> Locator: ...

    def plan_local_route(
        self,
        origin: Locator,
        destination: Locator,
        traversal_context: TraversalContext,
    ) -> LocalRoute | None: ...

    def outgoing_transitions(
        self,
        locator: Locator,
    ) -> tuple[Transition, ...]: ...


@dataclass(frozen=True, slots=True)
class Space:
    id: str
    topology: SpaceTopology
    kind: str = "generic"
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("space id must not be empty")
        if not self.kind:
            raise ValueError("space kind must not be empty")
        if self.topology.space_id != self.id:
            raise ValueError("space topology must use the space id")
        object.__setattr__(self, "metadata", _frozen_metadata(self.metadata))


class TopologyView(Protocol):
    @property
    def revision(self) -> int: ...

    def space(self, space_id: str) -> Space: ...

    def spaces(self) -> tuple[Space, ...]: ...

    def resolve(self, space_id: str, reference: JsonValue) -> Locator: ...

    def parent_spaces(self, space_id: str) -> tuple[Space, ...]: ...

    def child_spaces(self, space_id: str) -> tuple[Space, ...]: ...

    def transitions_from(self, locator: Locator) -> tuple[Transition, ...]: ...

    def registered_transitions_from_space(
        self,
        space_id: str,
    ) -> tuple[Transition, ...]: ...

    def destination_locators(self, destination_id: str) -> tuple[Locator, ...]: ...


class SpaceRegistry:
    def __init__(self) -> None:
        self._revision = 0
        self._spaces: dict[str, Space] = {}
        self._containment: set[tuple[str, str]] = set()
        self._transitions: dict[str, Transition] = {}
        self._destinations: dict[str, dict[str, Locator]] = {}

    @property
    def revision(self) -> int:
        return self._revision

    def register_space(self, space: Space) -> None:
        if space.id in self._spaces:
            raise ValueError(f"space already registered: {space.id}")
        self._spaces[space.id] = space
        self._revision += 1

    def register_containment(self, parent_space_id: str, child_space_id: str) -> None:
        self.space(parent_space_id)
        self.space(child_space_id)
        if parent_space_id == child_space_id:
            raise ValueError("a space cannot contain itself")
        relation = (parent_space_id, child_space_id)
        if relation in self._containment:
            return
        if self._is_descendant(parent_space_id, child_space_id):
            raise ValueError("space containment must not contain a cycle")
        self._containment.add(relation)
        self._revision += 1

    def register_transition(self, transition: Transition) -> None:
        if transition.id.endswith(":reverse"):
            raise ValueError(
                "transition IDs ending in ':reverse' are reserved "
                "for synthesized reverse transitions"
            )
        if transition.id in self._transitions:
            raise ValueError(f"transition already registered: {transition.id}")
        self._validate_locator(transition.from_locator)
        self._validate_locator(transition.to_locator)
        self._transitions[transition.id] = transition
        self._revision += 1

    def register_destination(
        self,
        destination_id: str,
        locator: Locator,
    ) -> None:
        if not destination_id:
            raise ValueError("destination id must not be empty")
        self._validate_locator(locator)
        locators = self._destinations.setdefault(destination_id, {})
        if locator.stable_key in locators:
            return
        locators[locator.stable_key] = locator
        self._revision += 1

    def space(self, space_id: str) -> Space:
        try:
            return self._spaces[space_id]
        except KeyError as error:
            raise KeyError(f"unknown space: {space_id}") from error

    def spaces(self) -> tuple[Space, ...]:
        return tuple(self._spaces[space_id] for space_id in sorted(self._spaces))

    def resolve(self, space_id: str, reference: JsonValue) -> Locator:
        return self.space(space_id).topology.resolve(reference)

    def parent_spaces(self, space_id: str) -> tuple[Space, ...]:
        self.space(space_id)
        parent_ids = sorted(
            parent_id
            for parent_id, child_id in self._containment
            if child_id == space_id
        )
        return tuple(self._spaces[parent_id] for parent_id in parent_ids)

    def child_spaces(self, space_id: str) -> tuple[Space, ...]:
        self.space(space_id)
        child_ids = sorted(
            child_id
            for parent_id, child_id in self._containment
            if parent_id == space_id
        )
        return tuple(self._spaces[child_id] for child_id in child_ids)

    def transition(self, transition_id: str) -> Transition:
        try:
            return self._transitions[transition_id]
        except KeyError as error:
            raise KeyError(f"unknown transition: {transition_id}") from error

    def transitions(self) -> tuple[Transition, ...]:
        return tuple(
            self._transitions[transition_id]
            for transition_id in sorted(self._transitions)
        )

    def transitions_from(self, locator: Locator) -> tuple[Transition, ...]:
        self._validate_locator(locator)
        matching = list(
            self._spaces[locator.space_id].topology.outgoing_transitions(locator)
        )
        for transition in self._transitions.values():
            if transition.from_locator == locator:
                matching.append(transition)
            elif transition.bidirectional and transition.to_locator == locator:
                matching.append(transition.reverse())
        unique = {
            transition.stable_key: transition
            for transition in matching
        }
        return tuple(unique[key] for key in sorted(unique))

    def registered_transitions_from_space(
        self,
        space_id: str,
    ) -> tuple[Transition, ...]:
        self.space(space_id)
        matching: dict[tuple[str, str, str], Transition] = {}
        for transition in self._transitions.values():
            if transition.from_locator.space_id == space_id:
                matching[transition.stable_key] = transition
            if transition.bidirectional and transition.to_locator.space_id == space_id:
                reverse = transition.reverse()
                matching[reverse.stable_key] = reverse
        return tuple(matching[key] for key in sorted(matching))

    def destination_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._destinations))

    def destination_locators(self, destination_id: str) -> tuple[Locator, ...]:
        locators = self._destinations.get(destination_id, {})
        return tuple(locators[key] for key in sorted(locators))

    def _is_descendant(self, possible_ancestor: str, space_id: str) -> bool:
        pending = [space_id]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == possible_ancestor:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(
                child_id
                for parent_id, child_id in self._containment
                if parent_id == current
            )
        return False

    def _validate_locator(self, locator: Locator) -> None:
        resolved = self.resolve(locator.space_id, locator.local_reference)
        if resolved != locator:
            raise ValueError("locator is not canonical for its space")
