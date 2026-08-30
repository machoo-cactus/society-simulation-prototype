from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from stage0_sim.adapters.llm import (
    OpenAICompatibleClient,
    OpenAICompatibleConfiguration,
    RecordingModelClient,
    ReplayModelClient,
)
from stage0_sim.application.agents.contracts import ModelClient


class Settings(BaseSettings):
    environment: str = "development"
    cors_origins: list[str] = []
    data_directory: Path = Path("data/runs")
    dataset_database: str = "stage0.sqlite3"
    character_directory: Path = Path("characters")
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 30.0
    llm_retry_attempts: int = 3
    llm_retry_delay_seconds: float = 1.0
    llm_tool_choice: str = "required"
    llm_max_output_tokens: int = 512
    llm_max_concurrency: int = 4
    llm_record_path: Path | None = None
    llm_replay_path: Path | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="STAGE0_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def create_model_client(settings: Settings) -> ModelClient | None:
    if settings.llm_provider is None:
        return None
    if settings.llm_provider == "replay":
        if settings.llm_replay_path is None:
            raise ValueError(
                "STAGE0_LLM_REPLAY_PATH is required for the replay provider"
            )
        return ReplayModelClient.from_jsonl(settings.llm_replay_path)
    if settings.llm_provider != "openai-compatible":
        raise ValueError(f"unsupported LLM provider: {settings.llm_provider}")
    if settings.llm_base_url is None or settings.llm_model is None:
        raise ValueError(
            "STAGE0_LLM_BASE_URL and STAGE0_LLM_MODEL are required "
            "for openai-compatible"
        )
    if settings.llm_tool_choice == "none":
        raise ValueError(
            "STAGE0_LLM_TOOL_CHOICE=none is incompatible with tool-agent "
            "cognition"
        )
    client: ModelClient = OpenAICompatibleClient(
        OpenAICompatibleConfiguration(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            timeout_seconds=settings.llm_timeout_seconds,
            retry_attempts=settings.llm_retry_attempts,
            retry_delay_seconds=settings.llm_retry_delay_seconds,
            tool_choice=settings.llm_tool_choice,
        )
    )
    if settings.llm_record_path is not None:
        client = RecordingModelClient(client, settings.llm_record_path)
    return client
