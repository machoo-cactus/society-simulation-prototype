"""LLM-compatible adapters."""

from stage0_sim.adapters.llm.fake import (
    FakeEmbeddingProvider,
)
from stage0_sim.adapters.llm.tool_clients import (
    OpenAICompatibleClient,
    OpenAICompatibleConfiguration,
    RecordingModelClient,
    ReplayModelClient,
    ScriptedModelClient,
)

__all__ = [
    "FakeEmbeddingProvider",
    "OpenAICompatibleClient",
    "OpenAICompatibleConfiguration",
    "RecordingModelClient",
    "ReplayModelClient",
    "ScriptedModelClient",
]
