import random
from dataclasses import dataclass
from typing import Protocol

from stage0_sim.domain.clock import SimulationClock
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.events import EventBus


@dataclass(frozen=True, slots=True)
class SystemContext:
    clock: SimulationClock
    registry: Registry
    events: EventBus
    rng: random.Random


class System(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def order(self) -> int: ...

    def update(self, context: SystemContext) -> None: ...


class SystemExecutor:
    def __init__(self) -> None:
        self._systems: list[tuple[int, System]] = []
        self._registration_sequence = 0

    @property
    def systems(self) -> tuple[System, ...]:
        return tuple(system for _, system in sorted(self._systems, key=self._sort_key))

    def add(self, system: System) -> None:
        if any(existing.name == system.name for _, existing in self._systems):
            raise ValueError(f"system name already registered: {system.name}")
        self._systems.append((self._registration_sequence, system))
        self._registration_sequence += 1

    def update(self, context: SystemContext) -> None:
        for system in self.systems:
            system.update(context)

    @staticmethod
    def _sort_key(item: tuple[int, System]) -> tuple[int, int]:
        registration_sequence, system = item
        return system.order, registration_sequence
