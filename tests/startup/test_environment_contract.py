import pytest
from tests.helpers.paths import REPOSITORY_ROOT

from stage0_sim.adapters.persistence.sqlite_schema import DATABASE_SCHEMA_VERSION
from stage0_sim.config import Settings


def test_default_database_name_is_schema_qualified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STAGE0_DATASET_DATABASE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.dataset_database == (
        f"stage0-v{DATABASE_SCHEMA_VERSION}.sqlite3"
    )


def test_example_environment_uses_current_default_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STAGE0_DATASET_DATABASE", raising=False)
    settings = Settings(
        _env_file=REPOSITORY_ROOT / ".env.example",
    )
    assert settings.dataset_database == (
        f"stage0-v{DATABASE_SCHEMA_VERSION}.sqlite3"
    )


def test_current_contract_documents_default_database() -> None:
    contracts = (
        REPOSITORY_ROOT / "docs" / "CURRENT_CONTRACTS.md"
    ).read_text(encoding="utf-8")
    assert f"`stage0-v{DATABASE_SCHEMA_VERSION}.sqlite3`" in contracts
