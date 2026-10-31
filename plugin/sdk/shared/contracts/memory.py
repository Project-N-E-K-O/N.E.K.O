from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from ..models.exceptions import SdkError

MemoryError = SdkError
MAX_WIRE_BYTES = 1024 * 1024


def _component(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > 256
        or any(ord(c) < 32 for c in value.strip())
    ):
        raise ValueError(f"Invalid {field}")
    return value.strip()


def _encode(value: object, field: str) -> str:
    return _component(value, field).replace("%", "%25").replace(":", "%3A")


@dataclass(frozen=True, slots=True)
class MemorySubject:
    kind: str
    subject_id: str
    scope: str

    def __post_init__(self) -> None:
        kind = _component(self.kind, "subject_kind")
        sid = _component(self.subject_id, "subject_id")
        scope = _component(self.scope, "scope")
        if (
            kind not in ("group_chat", "participant", "group_participant")
            or scope == "legacy_private"
        ):
            raise ValueError("Invalid subject")
        if kind == "group_participant" and (
            len(sid.split(":")) != 3 or not all(sid.split(":"))
        ):
            raise ValueError("Invalid group participant")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "subject_id", sid)
        object.__setattr__(self, "scope", scope)

    @classmethod
    def create(cls, kind: str, subject_id: str, *, scope: str | None = None):
        kind = _component(kind, "subject_kind")
        subject_id = _component(subject_id, "subject_id")
        return cls(kind, subject_id, f"{kind}:{subject_id}" if scope is None else scope)

    @classmethod
    def group_chat(cls, platform: str, conversation_id: str):
        return cls.create(
            "group_chat",
            f"{_encode(platform, 'platform')}:{_encode(conversation_id, 'conversation_id')}",
        )

    @classmethod
    def participant(cls, platform: str, actor_id: str):
        return cls.create(
            "participant",
            f"{_encode(platform, 'platform')}:{_encode(actor_id, 'actor_id')}",
        )

    @classmethod
    def group_participant(cls, platform: str, conversation_id: str, actor_id: str):
        return cls.create(
            "group_participant",
            f"{_encode(platform, 'platform')}:{_encode(conversation_id, 'conversation_id')}:{_encode(actor_id, 'actor_id')}",
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "subject_kind": self.kind,
            "subject_id": self.subject_id,
            "scope": self.scope,
        }

    @classmethod
    def from_wire(cls, value: Any):
        if not isinstance(value, dict) or set(value) != {
            "subject_kind",
            "subject_id",
            "scope",
        }:
            raise ValueError("Invalid subject wire")
        return cls(value["subject_kind"], value["subject_id"], value["scope"])


@dataclass(frozen=True, slots=True)
class MemoryTarget:
    kind: str
    character: str
    subjects: tuple[MemorySubject, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "character", _component(self.character, "character"))
        if not isinstance(self.subjects, (tuple, list)):
            raise ValueError("Invalid subjects")
        subjects = tuple(self.subjects)
        if (
            self.kind == "private"
            and subjects
            or self.kind == "scoped"
            and (
                not 1 <= len(subjects) <= 8
                or not all(isinstance(s, MemorySubject) for s in subjects)
            )
            or self.kind not in ("private", "scoped")
        ):
            raise ValueError("Invalid target")
        object.__setattr__(self, "subjects", subjects)

    @classmethod
    def private(cls, character: str):
        return cls("private", character)

    @classmethod
    def scoped(cls, character: str, subjects):
        return cls("scoped", character, subjects)

    def to_wire(self) -> dict[str, Any]:
        result = {"kind": self.kind, "character": self.character}
        if self.kind == "scoped":
            result["subjects"] = [s.to_wire() for s in self.subjects]
        return result

    @classmethod
    def from_wire(cls, value: Any):
        if not isinstance(value, dict):
            raise ValueError("Invalid target wire")
        kind = value.get("kind")
        if kind == "private" and set(value) == {"kind", "character"}:
            return cls.private(value["character"])
        if (
            kind == "scoped"
            and set(value) == {"kind", "character", "subjects"}
            and isinstance(value["subjects"], list)
        ):
            return cls.scoped(
                value["character"],
                [MemorySubject.from_wire(s) for s in value["subjects"]],
            )
        raise ValueError("Invalid target wire")


@dataclass(frozen=True, slots=True)
class MemoryHit:
    id: str | None
    text: str
    kind: str
    entity: str | None
    subject: MemorySubject | None
    retrieval_score: float | None
    created_at: str | None
    event_start_at: str | None
    event_end_at: str | None


@dataclass(frozen=True, slots=True)
class MemorySearchResult:
    target: MemoryTarget
    query: str
    requested_time: str | None
    items: list[MemoryHit]
    candidates_total: int
    elapsed_ms: float
    diagnostics: None = None


@dataclass(frozen=True, slots=True)
class MemoryPersonaEntry:
    id: str | None
    text: str
    source: str
    protected: bool
    suppressed: bool


@dataclass(frozen=True, slots=True)
class MemoryPersonaSection:
    entity: str
    entries: list[MemoryPersonaEntry]


@dataclass(frozen=True, slots=True)
class MemoryPersona:
    target: MemoryTarget
    sections: list[MemoryPersonaSection]


def _text(value: object, name: str, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or (not optional and not value.strip()):
        raise ValueError(f"Invalid {name}")
    return value


def _id(value: object) -> str | None:
    if value is None or value == "":
        return None
    return _text(value, "id")


def decode_search(value: object) -> MemorySearchResult:
    import math

    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise ValueError("Invalid search response")
    target = MemoryTarget.from_wire(value.get("target"))
    items = []
    for item in value["items"]:
        if not isinstance(item, dict):
            raise ValueError("Invalid search item")
        subject = item.get("subject")
        score = item.get("retrieval_score")
        if score is not None and (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
        ):
            raise ValueError("Invalid score")
        items.append(
            MemoryHit(
                _id(item.get("id")),
                _text(item.get("text"), "text"),
                _text(item.get("kind"), "kind"),
                _text(item.get("entity"), "entity", True),
                None if subject is None else MemorySubject.from_wire(subject),
                score,
                *(
                    _text(item.get(k), k, True)
                    for k in ("created_at", "event_start_at", "event_end_at")
                ),
            )
        )
    total, elapsed = value.get("candidates_total"), value.get("elapsed_ms")
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total < 0
        or isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(elapsed)
        or elapsed < 0
        or value.get("diagnostics") is not None
    ):
        raise ValueError("Invalid search metadata")
    query = value.get("query")
    if not isinstance(query, str):
        raise ValueError("Invalid query")
    return MemorySearchResult(
        target,
        query,
        _text(value.get("requested_time"), "requested_time", True),
        items,
        total,
        elapsed,
    )


def decode_persona(value: object) -> MemoryPersona:
    if not isinstance(value, dict) or not isinstance(value.get("sections"), list):
        raise ValueError("Invalid persona response")
    target = MemoryTarget.from_wire(value.get("target"))
    if target.kind != "private":
        raise ValueError("Invalid persona target")
    sections = []
    for section in value["sections"]:
        if not isinstance(section, dict) or not isinstance(
            section.get("entries"), list
        ):
            raise ValueError("Invalid persona section")
        entries = []
        for e in section["entries"]:
            if (
                not isinstance(e, dict)
                or not isinstance(e.get("protected"), bool)
                or not isinstance(e.get("suppressed"), bool)
            ):
                raise ValueError("Invalid persona entry")
            entries.append(
                MemoryPersonaEntry(
                    _id(e.get("id")),
                    _text(e.get("text"), "text"),
                    _text(e.get("source"), "source"),
                    e["protected"],
                    e["suppressed"],
                )
            )
        sections.append(
            MemoryPersonaSection(_text(section.get("entity"), "entity"), entries)
        )
    return MemoryPersona(target, sections)
