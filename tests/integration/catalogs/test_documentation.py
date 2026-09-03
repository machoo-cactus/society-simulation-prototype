import re
from pathlib import Path
from urllib.parse import unquote

from stage0_sim import __version__
from stage0_sim.adapters.persistence.sqlite_schema import DATABASE_SCHEMA_VERSION
from stage0_sim.application.data_capture import DATASET_SCHEMA_VERSION
from stage0_sim.application.migrations import (
    CHARACTER_SCHEMA_VERSION,
    ELEMENT_SCHEMA_VERSION,
    SCENARIO_SCHEMA_VERSION,
)
from stage0_sim.application.scenario import CognitionSettingsDefinition
from stage0_sim.application.telemetry import TELEMETRY_SCHEMA_VERSION
from stage0_sim.config import Settings
from stage0_sim.domain.components import ActionType

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def _markdown_documents() -> tuple[Path, ...]:
    return (
        REPOSITORY_ROOT / "README.md",
        REPOSITORY_ROOT / ".github" / "copilot-instructions.md",
        REPOSITORY_ROOT / "data" / "README.md",
        *sorted((REPOSITORY_ROOT / "docs").rglob("*.md")),
    )


def test_internal_markdown_links_resolve() -> None:
    missing: list[str] = []
    for document in _markdown_documents():
        for raw_target in MARKDOWN_LINK.findall(
            document.read_text(encoding="utf-8")
        ):
            target = raw_target.split(maxsplit=1)[0].strip("<>")
            if (
                not target
                or target.startswith(("#", "http://", "https://", "mailto:"))
            ):
                continue
            relative_path = unquote(target.split("#", 1)[0])
            resolved = (document.parent / relative_path).resolve()
            if not resolved.exists():
                missing.append(
                    f"{document.relative_to(REPOSITORY_ROOT)} -> {target}"
                )
    assert not missing, "missing internal Markdown links:\n" + "\n".join(missing)


def test_current_contracts_are_documented() -> None:
    contracts = (
        REPOSITORY_ROOT / "docs" / "CURRENT_CONTRACTS.md"
    ).read_text(encoding="utf-8")
    configuration = (
        REPOSITORY_ROOT / "docs" / "CONFIGURATION.md"
    ).read_text(encoding="utf-8")
    vocabulary = (
        REPOSITORY_ROOT / "docs" / "ACTIONS_AND_EVENTS.md"
    ).read_text(encoding="utf-8")

    for field_name in Settings.model_fields:
        assert f"STAGE0_{field_name.upper()}" in configuration

    tools = set(CognitionSettingsDefinition().tool_allowlist)
    tools.add("serve_transaction")
    for tool in tools:
        assert f"`{tool}`" in vocabulary

    for action in ActionType:
        assert f"`{action.value}`" in vocabulary

    assert f"`{__version__}`" in contracts
    assert f"| Scenario source | `{SCENARIO_SCHEMA_VERSION}` |" in contracts
    assert f"| Character source | `{CHARACTER_SCHEMA_VERSION}`" in contracts
    assert f"| Reusable element source | `{ELEMENT_SCHEMA_VERSION}` |" in contracts
    assert f"`{DATASET_SCHEMA_VERSION}`" in contracts
    assert f"schema `{DATABASE_SCHEMA_VERSION}`" in contracts
    assert f"`{TELEMETRY_SCHEMA_VERSION}`" in contracts

    for event_type in (
        "engagement.requested",
        "engagement.compilation_requested",
        "engagement.compilation_completed",
        "engagement.compilation_failed",
        "engagement.compilation_cancelled",
        "engagement.started",
        "engagement.group_completed",
        "engagement.group_failed",
        "engagement.capability_committed",
        "engagement.completed",
        "engagement.partial",
        "engagement.failed",
        "engagement.cancelled",
    ):
        assert f"`{event_type}`" in vocabulary
