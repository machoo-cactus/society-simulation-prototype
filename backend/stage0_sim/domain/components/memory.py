from dataclasses import dataclass


@dataclass(slots=True)
class MemoryComponent:
    top_k: int = 5

    def __post_init__(self) -> None:
        if self.top_k <= 0:
            raise ValueError("memory top_k must be greater than zero")
