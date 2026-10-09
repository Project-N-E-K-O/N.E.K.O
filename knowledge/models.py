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

"""Schema-v1 knowledge packs and the records derived from them.

A pack file carries exactly ``schema_version``, ``pack_id``,
``material_type``, ``source`` and ``entries``; every entry carries at most
``title``, ``terms``, ``tags``, ``summary`` and ``content``. Chunks, hashes,
vectors and model ids are system-derived and rejected in a pack: v1 ships raw
text only and vectors are computed locally.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .text import sanitize_external_text, single_line, title_key


PACK_SCHEMA_VERSION = 1
MATERIAL_TYPES = ("knowledge", "corpus")
TERM_ROLES = ("alias", "recognition")

MAX_PACK_BYTES = 10 * 1024 * 1024
MAX_ENTRIES_PER_PACK = 5_000
MAX_TOTAL_ENTRIES = 20_000
MAX_TOTAL_PACK_BYTES = 64 * 1024 * 1024
MAX_TITLE_CHARS = 500
MAX_SUMMARY_CHARS = 4_000
MAX_CONTENT_CHARS = 80_000
MAX_TERM_CHARS = 300
MAX_TERMS_PER_ROLE = 64
MAX_TAG_CHARS = 100
MAX_TAGS_PER_ENTRY = 32
MAX_SOURCE_FIELD_CHARS = 500

_PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_PACK_KEYS = frozenset({"schema_version", "pack_id", "material_type", "source", "entries"})
_ENTRY_KEYS = frozenset({"title", "terms", "tags", "summary", "content"})
_SOURCE_KEYS = frozenset({"name", "homepage", "license"})


class KnowledgePackError(ValueError):
    """A pack was rejected; ``reason`` is a stable machine-readable code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    name: str
    homepage: str = ""
    license: str = ""


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    """One card of a pack. Never a user or character memory."""

    title: str
    summary: str
    content: str
    terms: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    tags: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return title_key(self.title)

    def to_payload(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "terms": {role: list(self.terms.get(role, ())) for role in TERM_ROLES},
            "tags": list(self.tags),
            "summary": self.summary,
            "content": self.content,
        }


@dataclass(frozen=True, slots=True)
class KnowledgePack:
    pack_id: str
    material_type: str
    source: KnowledgeSource
    entries: tuple[KnowledgeEntry, ...]


def pack_id_is_valid(value: object) -> bool:
    # fullmatch: ``$`` alone would accept a trailing newline.
    return isinstance(value, str) and bool(_PACK_ID_RE.fullmatch(value))


def _one_line(value: object, *, max_chars: int) -> str:
    return single_line(sanitize_external_text(value, max_chars=max_chars), max_chars=max_chars)


def _require_mapping(value: object, reason: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise KnowledgePackError(reason)
    return value


def _clean_list(values: object, *, max_items: int, max_chars: int) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, list):
        raise KnowledgePackError("invalid_entry")
    if len(values) > max_items:
        raise KnowledgePackError("invalid_entry")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise KnowledgePackError("invalid_entry")
        text = _one_line(value, max_chars=max_chars)
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _parse_entry(raw: object) -> KnowledgeEntry:
    entry = _require_mapping(raw, "invalid_entry")
    if not set(entry).issubset(_ENTRY_KEYS):
        raise KnowledgePackError("unexpected_entry_field")
    for name in ("title", "summary", "content"):
        if name in entry and not isinstance(entry[name], str):
            raise KnowledgePackError("invalid_entry")
    title = _one_line(entry.get("title"), max_chars=MAX_TITLE_CHARS)
    content = sanitize_external_text(entry.get("content"), max_chars=MAX_CONTENT_CHARS)
    if not title or not content:
        raise KnowledgePackError("invalid_entry", "title and content are required")
    raw_terms = entry.get("terms") or {}
    terms_map = _require_mapping(raw_terms, "invalid_entry")
    if not set(terms_map).issubset(TERM_ROLES):
        raise KnowledgePackError("unexpected_entry_field")
    terms = {
        role: _clean_list(
            terms_map.get(role), max_items=MAX_TERMS_PER_ROLE, max_chars=MAX_TERM_CHARS
        )
        for role in TERM_ROLES
    }
    return KnowledgeEntry(
        title=title,
        summary=sanitize_external_text(entry.get("summary"), max_chars=MAX_SUMMARY_CHARS),
        content=content,
        terms=terms,
        tags=_clean_list(entry.get("tags"), max_items=MAX_TAGS_PER_ENTRY, max_chars=MAX_TAG_CHARS),
    )


def parse_pack(payload: object) -> KnowledgePack:
    """Validate a decoded schema-v1 pack and return its normalized form."""
    pack = _require_mapping(payload, "invalid_pack")
    if set(pack) != _PACK_KEYS:
        raise KnowledgePackError("unexpected_pack_field")
    if pack.get("schema_version") != PACK_SCHEMA_VERSION or isinstance(
        pack.get("schema_version"), bool
    ):
        raise KnowledgePackError("unsupported_schema_version")
    pack_id = pack.get("pack_id")
    if not pack_id_is_valid(pack_id):
        raise KnowledgePackError("invalid_pack_id")
    material_type = pack.get("material_type")
    if material_type not in MATERIAL_TYPES:
        raise KnowledgePackError("invalid_material_type")
    source_map = _require_mapping(pack.get("source"), "invalid_source")
    if not set(source_map).issubset(_SOURCE_KEYS) or any(
        not isinstance(value, str) for value in source_map.values()
    ):
        raise KnowledgePackError("invalid_source")
    source = KnowledgeSource(
        name=_one_line(source_map.get("name"), max_chars=200),
        homepage=_one_line(source_map.get("homepage"), max_chars=MAX_SOURCE_FIELD_CHARS),
        license=_one_line(source_map.get("license"), max_chars=MAX_SOURCE_FIELD_CHARS),
    )
    if not source.name:
        raise KnowledgePackError("invalid_source")
    raw_entries = pack.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise KnowledgePackError("invalid_entries")
    if len(raw_entries) > MAX_ENTRIES_PER_PACK:
        raise KnowledgePackError("too_many_entries")
    entries: list[KnowledgeEntry] = []
    seen: set[str] = set()
    for raw in raw_entries:
        entry = _parse_entry(raw)
        if entry.key in seen:
            raise KnowledgePackError("duplicate_title", entry.title)
        seen.add(entry.key)
        entries.append(entry)
    return KnowledgePack(
        pack_id=str(pack_id),
        material_type=str(material_type),
        source=source,
        entries=tuple(entries),
    )


def decode_pack_bytes(raw: bytes) -> KnowledgePack:
    """Decode and validate raw pack bytes (UTF-8 JSON, at most 10 MiB)."""
    if len(raw) > MAX_PACK_BYTES:
        raise KnowledgePackError("pack_too_large")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnowledgePackError("invalid_json") from exc
    return parse_pack(payload)


def canonical_pack_bytes(pack: KnowledgePack) -> bytes:
    """Serialize the normalized pack; this is what lands in ``packs/``."""
    payload = {
        "schema_version": PACK_SCHEMA_VERSION,
        "pack_id": pack.pack_id,
        "material_type": pack.material_type,
        "source": {
            "name": pack.source.name,
            "homepage": pack.source.homepage,
            "license": pack.source.license,
        },
        "entries": [entry.to_payload() for entry in pack.entries],
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def pack_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
