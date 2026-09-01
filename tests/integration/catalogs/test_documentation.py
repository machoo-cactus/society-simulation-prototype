import re
from pathlib import Path
from urllib.parse import unquote

from stage0_sim.application.scenario import CognitionSettingsDefinition
from stage0_sim.config import Settings
from stage0_sim.domain.components import ActionType

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def _markdown_documents() -> tuple[Path, ...]:
    return (
        REPOSITORY_ROOT / "README.md",
        REPOSITORY_ROOT / ".github" / "copilot-instructions.md",
        REPOSITORY_ROOT / "examples" / "README.md",
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

    assert "stage0.dataset.v3" in (
        REPOSITORY_ROOT / "docs" / "DATA_COLLECTION.md"
    ).read_text(encoding="utf-8")
    assert "schema 8" in (
        REPOSITORY_ROOT / "docs" / "STATUS_AND_ROADMAP.md"
    ).read_text(encoding="utf-8")
