import hashlib
import math
from typing import Protocol


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


class DeterministicEmbeddingProvider:
    """Local deterministic embeddings used when no provider is composed."""

    def __init__(self, dimensions: int = 8) -> None:
        if dimensions <= 0:
            raise ValueError("embedding dimensions must be greater than zero")
        self.dimensions = dimensions
        self.call_count = 0
        self.provider_name = "deterministic"

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.call_count += 1
        return tuple(self._embed_one(text) for text in texts)

    def _embed_one(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = tuple(
            digest[index] / 127.5 - 1.0 for index in range(self.dimensions)
        )
        magnitude = math.sqrt(sum(value * value for value in raw))
        if magnitude == 0:
            return tuple(0.0 for _ in raw)
        return tuple(round(value / magnitude, 12) for value in raw)
