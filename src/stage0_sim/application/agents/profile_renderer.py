import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from stage0_sim.domain.events import JsonValue


@dataclass(frozen=True, slots=True)
class RenderedCharacterProfile:
    markdown: str
    content_hash: str


class CharacterDescriptionRenderer:
    version = "character-description-v1"

    def render(
        self,
        *,
        template_id: str,
        template_version: int,
        sections: Sequence[str],
        profile: Mapping[str, JsonValue],
    ) -> RenderedCharacterProfile:
        content = json.dumps(
            profile,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        blocks = [
            "# Character Profile",
            "",
            f"Template: `{template_id}` version {template_version}",
        ]
        for section_id in sections:
            value = profile.get(section_id)
            if not isinstance(value, dict):
                continue
            rows = _section_rows(value)
            if not rows:
                continue
            blocks.extend(
                [
                    "",
                    f"## {_title(section_id)}",
                    "",
                    "| Field | Value |",
                    "|---|---|",
                    *(
                        f"| {_escape(label)} | {_escape(rendered)} |"
                        for label, rendered in rows
                    ),
                ]
            )
        custom_sections = profile.get("custom_sections")
        if isinstance(custom_sections, list):
            for raw_section in custom_sections:
                if (
                    not isinstance(raw_section, dict)
                    or raw_section.get("prompt_visible") is False
                ):
                    continue
                title = raw_section.get("title")
                fields = raw_section.get("fields")
                if not isinstance(title, str) or not isinstance(fields, list):
                    continue
                rows = []
                for field in fields:
                    label = field.get("label") if isinstance(field, dict) else None
                    if (
                        isinstance(field, dict)
                        and field.get("prompt_visible") is not False
                        and isinstance(label, str)
                        and field.get("value") is not None
                    ):
                        rows.append(
                            (
                                label,
                                _render_value(field["value"]),
                            )
                        )
                if rows:
                    blocks.extend(
                        [
                            "",
                            f"## {title}",
                            "",
                            "| Field | Value |",
                            "|---|---|",
                            *(
                                f"| {_escape(label)} | {_escape(value)} |"
                                for label, value in rows
                            ),
                        ]
                    )
        markdown = "\n".join(blocks)
        if len(markdown) > 16_000:
            raise ValueError("rendered character profile exceeds 16000 characters")
        return RenderedCharacterProfile(markdown, content_hash)


def _section_rows(section: Mapping[str, JsonValue]) -> list[tuple[str, str]]:
    return [
        (_title(key), _render_value(value))
        for key, value in section.items()
        if value not in (None, "", [], {})
    ]


def _render_value(value: JsonValue) -> str:
    if isinstance(value, list):
        return "; ".join(_render_value(item) for item in value)
    if isinstance(value, dict):
        return "; ".join(
            f"{_title(key)}: {_render_value(item)}"
            for key, item in value.items()
        )
    if value is None:
        return ""
    return str(value)


def _title(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")
