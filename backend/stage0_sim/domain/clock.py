from dataclasses import dataclass


@dataclass(slots=True)
class SimulationClock:
    """A fixed-step clock whose time depends only on completed ticks."""

    dt: float = 1.0
    tick: int = 0

    def __post_init__(self) -> None:
        if self.dt <= 0:
            raise ValueError("dt must be greater than zero")
        if self.tick < 0:
            raise ValueError("tick must not be negative")

    @property
    def simulation_time(self) -> float:
        return self.tick * self.dt

    def advance(self) -> None:
        self.tick += 1
