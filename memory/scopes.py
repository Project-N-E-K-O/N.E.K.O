# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Platform-neutral subject and scope primitives for long-term memory.

The pre-group-chat schema has no scope fields. Missing fields deliberately
mean ``legacy_private``; they never mean wildcard/global access. New callers
must pass an explicit :class:`MemorySubject` for group or participant memory.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


LEGACY_PRIVATE_SCOPE = "legacy_private"
SCOPED_PERSONA_PREFIX = "@subject/"

SUBJECT_GROUP_CHAT = "group_chat"
SUBJECT_PARTICIPANT = "participant"
SUBJECT_GROUP_PARTICIPANT = "group_participant"
SUBJECT_KINDS = frozenset({
    SUBJECT_GROUP_CHAT,
    SUBJECT_PARTICIPANT,
    SUBJECT_GROUP_PARTICIPANT,
})
_SUBJECT_FIELDS = ("subject_kind", "subject_id", "scope")


class MemoryScopeError(ValueError):
    """Raised when an untrusted subject/scope descriptor is malformed."""


def _clean_component(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise MemoryScopeError(f"{field} must be a string")
    value = value.strip()
    if not value:
        raise MemoryScopeError(f"{field} must not be empty")
    if len(value) > 256:
        raise MemoryScopeError(f"{field} is too long")
    if any(ord(char) < 0x20 for char in value):
        raise MemoryScopeError(f"{field} contains control characters")
    return value


@dataclass(frozen=True, slots=True)
class MemorySubject:
    """A stable memory owner and its isolation boundary."""

    kind: str
    subject_id: str
    scope: str

    def __post_init__(self) -> None:
        kind = _clean_component(self.kind, field="subject_kind")
        if kind not in SUBJECT_KINDS:
            raise MemoryScopeError(f"unsupported subject_kind: {kind!r}")
        subject_id = _clean_component(self.subject_id, field="subject_id")
        scope = _clean_component(self.scope, field="scope")
        if scope == LEGACY_PRIVATE_SCOPE:
            raise MemoryScopeError("new subjects cannot use legacy_private scope")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "subject_id", subject_id)
        object.__setattr__(self, "scope", scope)

    @classmethod
    def create(
        cls,
        kind: str,
        subject_id: str,
        *,
        scope: str | None = None,
    ) -> "MemorySubject":
        clean_kind = _clean_component(kind, field="subject_kind")
        clean_id = _clean_component(subject_id, field="subject_id")
        return cls(clean_kind, clean_id, scope or f"{clean_kind}:{clean_id}")

    @classmethod
    def group_chat(cls, platform: str, conversation_id: str) -> "MemorySubject":
        platform = _clean_component(platform, field="platform")
        conversation_id = _clean_component(conversation_id, field="conversation_id")
        return cls.create(SUBJECT_GROUP_CHAT, f"{platform}:{conversation_id}")

    @classmethod
    def participant(cls, platform: str, actor_id: str) -> "MemorySubject":
        platform = _clean_component(platform, field="platform")
        actor_id = _clean_component(actor_id, field="actor_id")
        return cls.create(SUBJECT_PARTICIPANT, f"{platform}:{actor_id}")

    @classmethod
    def group_participant(
        cls,
        platform: str,
        conversation_id: str,
        actor_id: str,
    ) -> "MemorySubject":
        platform = _clean_component(platform, field="platform")
        conversation_id = _clean_component(conversation_id, field="conversation_id")
        actor_id = _clean_component(actor_id, field="actor_id")
        return cls.create(
            SUBJECT_GROUP_PARTICIPANT,
            f"{platform}:{conversation_id}:{actor_id}",
        )

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.subject_id}"

    @property
    def persona_section_key(self) -> str:
        return f"{SCOPED_PERSONA_PREFIX}{self.key}"

    def as_entry_fields(self) -> dict[str, str]:
        return {
            "subject_kind": self.kind,
            "subject_id": self.subject_id,
            "scope": self.scope,
        }


def coerce_subject(value: MemorySubject | Mapping[str, Any] | None) -> MemorySubject | None:
    """Normalize public API input without accepting a legacy pseudo-subject."""
    if value is None:
        return None
    if isinstance(value, MemorySubject):
        return value
    if isinstance(value, Mapping):
        return MemorySubject.create(
            value.get("subject_kind") or value.get("kind"),
            value.get("subject_id"),
            scope=value.get("scope"),
        )
    raise MemoryScopeError("subject must be a MemorySubject or mapping")


def subject_from_entry(entry: Mapping[str, Any]) -> MemorySubject | None:
    """Return an explicit subject, or ``None`` for legacy/malformed data.

    A malformed partial descriptor fails closed as legacy-private, so corrupt
    data cannot accidentally enter a group candidate pool.
    """
    kind = entry.get("subject_kind")
    subject_id = entry.get("subject_id")
    scope = entry.get("scope")
    if kind is None and subject_id is None and scope is None:
        return None
    try:
        return MemorySubject.create(kind, subject_id, scope=scope)
    except MemoryScopeError:
        return None


def is_legacy_private_entry(entry: Mapping[str, Any]) -> bool:
    """Only a fully unscoped row belongs to the pre-upgrade private corpus.

    A partially written or corrupt subject descriptor is not legacy data. It
    is excluded from every normal read path so it cannot leak into either a
    private conversation or a group candidate pool.
    """
    return all(entry.get(field) is None for field in _SUBJECT_FIELDS)


def effective_scope(entry: Mapping[str, Any]) -> str:
    subject = subject_from_entry(entry)
    return subject.scope if subject is not None else LEGACY_PRIVATE_SCOPE


def entry_matches_subject(
    entry: Mapping[str, Any],
    subject: MemorySubject | Mapping[str, Any] | None,
) -> bool:
    expected = coerce_subject(subject)
    actual = subject_from_entry(entry)
    if expected is None:
        return actual is None and is_legacy_private_entry(entry)
    return actual is not None and actual.key == expected.key and actual.scope == expected.scope


def normalize_subjects(
    subjects: Iterable[MemorySubject | Mapping[str, Any]] | None,
) -> tuple[MemorySubject, ...]:
    if subjects is None:
        return ()
    normalized: list[MemorySubject] = []
    seen: set[tuple[str, str]] = set()
    for raw in subjects:
        subject = coerce_subject(raw)
        if subject is None:
            continue
        marker = (subject.key, subject.scope)
        if marker not in seen:
            normalized.append(subject)
            seen.add(marker)
    return tuple(normalized)


def filter_entries_for_subjects(
    entries: Iterable[dict],
    subjects: Iterable[MemorySubject | Mapping[str, Any]] | None = None,
    *,
    include_legacy_private: bool | None = None,
) -> list[dict]:
    """Filter before ranking/rendering, never after it.

    With no explicit subjects the old private-memory behaviour is preserved.
    Supplying subjects switches to scoped mode and excludes legacy rows unless
    the caller explicitly opts in.
    """
    allowed = normalize_subjects(subjects)
    if include_legacy_private is None:
        include_legacy_private = not allowed
    allowed_keys = {(subject.key, subject.scope) for subject in allowed}
    result: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        actual = subject_from_entry(entry)
        if actual is None:
            if include_legacy_private and is_legacy_private_entry(entry):
                result.append(entry)
            continue
        if (actual.key, actual.scope) in allowed_keys:
            result.append(entry)
    return result


def persona_subject_from_section(section_key: str, section: Mapping[str, Any]) -> MemorySubject | None:
    """Read a scoped persona section defensively from its metadata."""
    if not isinstance(section_key, str) or not section_key.startswith(SCOPED_PERSONA_PREFIX):
        return None
    return subject_from_entry(section)
