import math
import re
from dataclasses import dataclass, replace

from stage0_sim.application.cognition import EmbeddingError, EmbeddingProvider
from stage0_sim.application.information.store import InformationStore
from stage0_sim.application.information_context import InformationContextCapsule
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.information import (
    InformationDocument,
    canonical_json,
    character_can_access_information,
    character_information_namespace_id,
)

_TERM_PATTERN = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)


@dataclass(frozen=True, slots=True)
class InformationQuery:
    character_id: str
    text: str
    referenced_entity_ids: tuple[str, ...] = ()
    referenced_place_ids: tuple[str, ...] = ()
    simulation_time: float = 0.0
    source_scope: tuple[str, ...] | None = None
    token_budget: int = 512

    def __post_init__(self) -> None:
        if not self.character_id.strip():
            raise ValueError("information query character_id must not be empty")
        if self.token_budget <= 0:
            raise ValueError("information query token_budget must be greater than zero")
        if not math.isfinite(self.simulation_time):
            raise ValueError("information query simulation_time must be finite")
        for values, label in (
            (self.referenced_entity_ids, "referenced_entity_ids"),
            (self.referenced_place_ids, "referenced_place_ids"),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"information query {label} must not contain empty IDs")
            if len(values) != len(set(values)):
                raise ValueError(f"information query {label} must be unique")
        if self.source_scope is not None:
            if any(not value.strip() for value in self.source_scope):
                raise ValueError(
                    "information query source_scope must not contain empty values"
                )
            if len(self.source_scope) != len(set(self.source_scope)):
                raise ValueError("information query source_scope must be unique")


RetrievedInformation = InformationContextCapsule


@dataclass(frozen=True, slots=True)
class _Anchor:
    path: str | None
    parent_path: str | None
    text: str
    rendered_parent: str
    references: frozenset[str]


class InformationRetriever:
    def __init__(
        self,
        store: InformationStore,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.store = store
        self.embedding_provider = embedding_provider
        self._anchors: dict[
            tuple[str, int, str],
            tuple[_Anchor, ...],
        ] = {}
        self._anchor_embeddings: dict[
            tuple[str, int, str, str | None, str],
            tuple[float, ...],
        ] = {}

    def retrieve(
        self,
        query: InformationQuery,
    ) -> tuple[RetrievedInformation, ...]:
        namespace_id = character_information_namespace_id(query.character_id)
        query_terms = _terms(query.text)
        query_references = frozenset(
            (*query.referenced_entity_ids, *query.referenced_place_ids)
        )
        query_embedding = (
            self._embed((query.text,))[0]
            if self.embedding_provider is not None and query.text.strip()
            else None
        )
        documents = tuple(
            document
            for document in self.store.documents(
                namespace_id=namespace_id,
                kinds=query.source_scope,
            )
            if character_can_access_information(document, query.character_id)
        )
        document_anchors = tuple(
            (document, self._document_anchors(document))
            for document in documents
        )
        if query_embedding is not None:
            self._cache_missing_anchor_embeddings(document_anchors)
        ranked: dict[tuple[str, str | None], RetrievedInformation] = {}
        for document, anchors in document_anchors:
            for anchor in anchors:
                lexical = _lexical_score(query_terms, _terms(anchor.text))
                reference = (
                    1.0
                    if query_references.intersection(anchor.references)
                    else 0.0
                )
                semantic = 0.0
                if query_embedding is not None and self.embedding_provider is not None:
                    anchor_embedding = self._anchor_embeddings[
                        self._anchor_embedding_key(document, anchor)
                    ]
                    if len(query_embedding) != len(anchor_embedding):
                        raise EmbeddingError(
                            "information retrieval embedding dimensions do not match"
                        )
                    semantic = max(
                        0.0,
                        _cosine_similarity(query_embedding, anchor_embedding),
                    )
                score = (
                    0.5 * lexical + 0.3 * reference + 0.2 * semantic
                    if query_embedding is not None
                    else 0.7 * lexical + 0.3 * reference
                )
                if score <= 0:
                    continue
                key = (document.id, anchor.parent_path)
                result = RetrievedInformation(
                    document_id=document.id,
                    document_kind=document.kind,
                    source_path=anchor.parent_path,
                    rendered_content=anchor.rendered_parent,
                    source=document.source,
                    valid_time=document.valid_time,
                    score=round(score, 12),
                    revision=document.revision,
                    recorded_at=document.recorded_at,
                )
                previous = ranked.get(key)
                if previous is None or result.score > previous.score:
                    ranked[key] = result
        ordered = sorted(
            ranked.values(),
            key=lambda result: (
                -result.score,
                result.document_kind,
                result.document_id,
                result.source_path or "",
            ),
        )
        selected: list[RetrievedInformation] = []
        used_tokens = 0
        for result in ordered:
            remaining = query.token_budget - used_tokens
            if remaining <= 0:
                break
            estimated_tokens = _estimated_tokens(result.rendered_content)
            if estimated_tokens > remaining:
                if selected:
                    continue
                result = replace(
                    result,
                    rendered_content=_truncate_to_token_budget(
                        result.rendered_content,
                        remaining,
                    ),
                )
                estimated_tokens = _estimated_tokens(result.rendered_content)
            if used_tokens + estimated_tokens > query.token_budget:
                continue
            selected.append(result)
            used_tokens += estimated_tokens
            if used_tokens >= query.token_budget:
                break
        return tuple(selected)

    def _document_anchors(
        self,
        document: InformationDocument,
    ) -> tuple[_Anchor, ...]:
        key = (document.id, document.revision, document.content_hash)
        anchors = self._anchors.get(key)
        if anchors is None:
            anchors = _derive_anchors(document.content)
            self._anchors[key] = anchors
        return anchors

    def _cache_missing_anchor_embeddings(
        self,
        document_anchors: tuple[
            tuple[InformationDocument, tuple[_Anchor, ...]],
            ...,
        ],
    ) -> None:
        if self.embedding_provider is None:
            return
        missing: list[
            tuple[
                tuple[str, int, str, str | None, str],
                str,
            ]
        ] = []
        for document, anchors in document_anchors:
            for anchor in anchors:
                key = self._anchor_embedding_key(document, anchor)
                if key not in self._anchor_embeddings:
                    missing.append((key, anchor.text))
        if not missing:
            return
        embeddings = self._embed(tuple(text for _, text in missing))
        for (key, _), embedding in zip(missing, embeddings, strict=True):
            self._anchor_embeddings[key] = embedding

    @staticmethod
    def _anchor_embedding_key(
        document: InformationDocument,
        anchor: _Anchor,
    ) -> tuple[str, int, str, str | None, str]:
        return (
            document.id,
            document.revision,
            document.content_hash,
            anchor.path,
            anchor.text,
        )

    def _embed(
        self,
        texts: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        if self.embedding_provider is None:
            return ()
        try:
            embeddings = self.embedding_provider.embed(texts)
        except EmbeddingError:
            raise
        except Exception as error:
            raise EmbeddingError(
                f"information retrieval embedding failed: {error}"
            ) from error
        if len(embeddings) != len(texts):
            raise EmbeddingError(
                "information retrieval embedding provider returned "
                "an unexpected result count"
            )
        return embeddings


def _derive_anchors(content: JsonValue) -> tuple[_Anchor, ...]:
    anchors: list[_Anchor] = []

    def visit(
        value: JsonValue,
        path: str | None,
        parent_path: str | None,
        parent: JsonValue,
    ) -> None:
        if isinstance(value, dict):
            for key in sorted(value):
                child_path = f"{path}.{key}" if path is not None else f"$.{key}"
                visit(value[key], child_path, path, value)
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                child_path = f"{path}[{index}]" if path is not None else f"$[{index}]"
                visit(item, child_path, path, value)
            return
        coherent_path = parent_path
        coherent_parent = parent
        if (
            parent_path == "$"
            and isinstance(parent, dict)
            and path is not None
            and path.startswith("$.")
        ):
            coherent_path = path
            coherent_parent = value
        rendered_parent = _render_parent(coherent_path, coherent_parent)
        anchors.append(
            _Anchor(
                path=path,
                parent_path=coherent_path,
                text=" ".join(
                    part
                    for part in (
                        path,
                        canonical_json(coherent_parent),
                        _scalar_text(value),
                    )
                    if part is not None
                ),
                rendered_parent=rendered_parent,
                references=frozenset(_string_values(coherent_parent)),
            )
        )

    if isinstance(content, (dict, list)):
        visit(content, "$", None, content)
    else:
        visit(content, None, None, content)
    return tuple(anchors)


def _render_parent(path: str | None, value: JsonValue) -> str:
    rendered = canonical_json(value)
    if path is None or path == "$":
        return rendered
    return f"{path}: {rendered}"


def _scalar_text(value: JsonValue) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _string_values(value: JsonValue) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(
            item
            for child in value
            for item in _string_values(child)
        )
    if isinstance(value, dict):
        return tuple(
            item
            for key in sorted(value)
            for item in _string_values(value[key])
        )
    return ()


def _terms(text: str) -> frozenset[str]:
    return frozenset(match.group(0).casefold() for match in _TERM_PATTERN.finditer(text))


def _lexical_score(query: frozenset[str], candidate: frozenset[str]) -> float:
    if not query:
        return 0.0
    return len(query.intersection(candidate)) / len(query)


def _estimated_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _truncate_to_token_budget(text: str, token_budget: int) -> str:
    character_budget = max(1, token_budget * 4)
    if len(text) <= character_budget:
        return text
    suffix = "…[truncated]"
    if character_budget <= len(suffix):
        return suffix[:character_budget]
    return f"{text[: character_budget - len(suffix)]}{suffix}"


def _cosine_similarity(
    left: tuple[float, ...],
    right: tuple[float, ...],
) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions do not match")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )
