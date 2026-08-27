from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: str = "development"
    cors_origins: list[str] = []
    data_directory: Path = Path("data/runs")
    dataset_database: str = "stage0.sqlite3"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="STAGE0_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
