"""LLM-compatible adapters."""

from stage0_sim.adapters.llm.fake import (
    FakeDialogueGenerator,
    FakeEmbeddingProvider,
    FakePlanner,
    ScriptedPlanner,
)
from stage0_sim.adapters.llm.tool_clients import (
    FakeToolModelClient,
    OpenAICompatibleClient,
    OpenAICompatibleConfiguration,
    RecordingModelClient,
    ReplayModelClient,
    ScriptedModelClient,
)

__all__ = [
    "FakeDialogueGenerator",
    "FakeEmbeddingProvider",
    "FakePlanner",
    "FakeToolModelClient",
    "OpenAICompatibleClient",
    "OpenAICompatibleConfiguration",
    "RecordingModelClient",
    "ReplayModelClient",
    "ScriptedModelClient",
    "ScriptedPlanner",
]
