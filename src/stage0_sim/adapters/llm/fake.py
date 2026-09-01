from stage0_sim.application.cognition import DeterministicEmbeddingProvider


class FakeEmbeddingProvider(DeterministicEmbeddingProvider):
    def __init__(self, dimensions: int = 8) -> None:
        super().__init__(dimensions)
        self.provider_name = "fake"
