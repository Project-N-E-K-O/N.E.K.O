"""Five-field public knowledge records.

The database deliberately stores only conversational knowledge.  Source
policy and sync health are source-level concerns, not per-entry payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Iterable, Mapping

from .filters import sanitize_external_text


TERM_ROLES = ("alias", "recognition")


def _clean_values(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = sanitize_external_text(str(value), max_chars=300)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return tuple(result)


def normalize_terms(
    value: Mapping[str, Iterable[str]] | None,
) -> Mapping[str, tuple[str, ...]]:
    """Return the only supported term roles with cleaned, distinct values."""
    value = value or {}
    return MappingProxyType(
        {role: _clean_values(value.get(role, ())) for role in TERM_ROLES}
    )


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    """A compact public knowledge card; never a user or character memory."""

    title: str
    terms: Mapping[str, Iterable[str]]
    tags: tuple[str, ...]
    summary: str
    content: str

    def __post_init__(self) -> None:
        title = sanitize_external_text(self.title, max_chars=500)
        content = sanitize_external_text(self.content)
        if not title or not content:
            raise ValueError("title and content are required")
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "terms", normalize_terms(self.terms))
        object.__setattr__(self, "tags", _clean_values(self.tags))
        object.__setattr__(self, "summary", sanitize_external_text(self.summary, max_chars=4_000))
        object.__setattr__(self, "content", content)
        source_tags = [tag for tag in self.tags if tag.startswith("source:")]
        if len(source_tags) != 1:
            raise ValueError("exactly one source:* tag is required")

    @property
    def aliases(self) -> tuple[str, ...]:
        """Compatibility view; new code must use ``terms`` explicitly."""
        return self.terms["alias"]

    @property
    def recognition_terms(self) -> tuple[str, ...]:
        return self.terms["recognition"]

    @property
    def source_tag(self) -> str:
        return next(tag for tag in self.tags if tag.startswith("source:"))

    @property
    def content_hash(self) -> str:
        """Transient comparison key; it is intentionally not persisted."""
        payload = "\0".join((
            self.title,
            repr(self.terms),
            repr(self.tags),
            self.summary,
            self.content,
        ))
        return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    entry: KnowledgeEntry
    score: float


@dataclass(frozen=True, slots=True)
class UpsertResult:
    entry_id: str
    created: bool = False
    updated: bool = False
    unchanged: bool = False
