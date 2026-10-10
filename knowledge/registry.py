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

"""``registry.json``: the user-owned record of installed packs.

This file and the raw files under ``packs/`` are the source of truth;
``knowledge.db`` is rebuilt from them. Per-pack policy (automatic context,
local vectors, material-type override) and disabled entries live here, not in
the database, so a rebuild never loses a user's choices.

Defaults (product decision, 2026-10-09): a newly installed pack does NOT take
part in automatic context, and DOES get local vectors computed in the
background. The global switch defaults to on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from utils.file_utils import atomic_write_text

from .models import MATERIAL_TYPES, KnowledgeSource, pack_id_is_valid


REGISTRY_SCHEMA_VERSION = 1
REGISTRY_FILE = "registry.json"
PACKS_DIR = "packs"
# Covers the largest registry the capacity limits allow (20,000 one-entry
# packs, or 20,000 disabled maximum-length titles); a write that would exceed
# it is refused instead of leaving a file the next start cannot read.
MAX_REGISTRY_BYTES = 128 * 1024 * 1024
_SHA_HEX = frozenset("0123456789abcdef")


class KnowledgeRegistryError(Exception):
    """``registry.json`` exists but cannot be trusted; nothing is written over it."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def pack_file_name(pack_id: str, pack_sha256: str) -> str:
    """Name of the raw file a pack version is installed as."""
    # Prefixed so an id like ``con`` or ``nul`` never names a Windows device.
    return f"pack-{pack_id}.{pack_sha256[:16]}.json"


@dataclass(frozen=True, slots=True)
class PackRecord:
    pack_id: str
    pack_sha256: str
    source: KnowledgeSource
    declared_material_type: str
    entries: int
    chunks: int
    material_type_override: str | None = None
    auto_context: bool = False
    local_embedding: bool = True
    disabled_titles: tuple[str, ...] = ()
    installed_at: str = ""
    updated_at: str = ""

    @property
    def effective_material_type(self) -> str:
        return self.material_type_override or self.declared_material_type

    @property
    def file_name(self) -> str:
        return pack_file_name(self.pack_id, self.pack_sha256)

    def to_json(self) -> dict[str, Any]:
        return {
            "pack_sha256": self.pack_sha256,
            "source": {
                "name": self.source.name,
                "homepage": self.source.homepage,
                "license": self.source.license,
            },
            "declared_material_type": self.declared_material_type,
            "material_type_override": self.material_type_override,
            "entries": self.entries,
            "chunks": self.chunks,
            "auto_context": self.auto_context,
            "local_embedding": self.local_embedding,
            "disabled_titles": list(self.disabled_titles),
            "installed_at": self.installed_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class Registry:
    enabled: bool = True
    packs: Mapping[str, PackRecord] = field(default_factory=dict)

    def with_pack(self, record: PackRecord) -> "Registry":
        packs = dict(self.packs)
        packs[record.pack_id] = record
        return replace(self, packs=packs)

    def without_pack(self, pack_id: str) -> "Registry":
        packs = dict(self.packs)
        packs.pop(pack_id, None)
        return replace(self, packs=packs)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "enabled": self.enabled,
            "packs": {pack_id: record.to_json() for pack_id, record in sorted(self.packs.items())},
        }


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise KnowledgeRegistryError("invalid_field")
    return value


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise KnowledgeRegistryError("invalid_field")
    return value


def _count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise KnowledgeRegistryError("invalid_field")
    return value


def _parse_record(pack_id: str, raw: object) -> PackRecord:
    if not pack_id_is_valid(pack_id) or not isinstance(raw, dict):
        raise KnowledgeRegistryError("invalid_pack_record")
    sha = _str(raw.get("pack_sha256"))
    if len(sha) != 64 or not set(sha) <= _SHA_HEX:
        raise KnowledgeRegistryError("invalid_pack_record")
    source = raw.get("source")
    if not isinstance(source, dict):
        raise KnowledgeRegistryError("invalid_pack_record")
    declared = _str(raw.get("declared_material_type"))
    override = raw.get("material_type_override")
    if declared not in MATERIAL_TYPES or (override is not None and override not in MATERIAL_TYPES):
        raise KnowledgeRegistryError("invalid_pack_record")
    disabled = raw.get("disabled_titles")
    if not isinstance(disabled, list) or any(not isinstance(item, str) for item in disabled):
        raise KnowledgeRegistryError("invalid_pack_record")
    return PackRecord(
        pack_id=pack_id,
        pack_sha256=sha,
        source=KnowledgeSource(
            name=_str(source.get("name", "")),
            homepage=_str(source.get("homepage", "")),
            license=_str(source.get("license", "")),
        ),
        declared_material_type=declared,
        material_type_override=override,
        entries=_count(raw.get("entries")),
        chunks=_count(raw.get("chunks")),
        auto_context=_bool(raw.get("auto_context")),
        local_embedding=_bool(raw.get("local_embedding")),
        disabled_titles=tuple(sorted(set(disabled))),
        installed_at=_str(raw.get("installed_at", "")),
        updated_at=_str(raw.get("updated_at", "")),
    )


def load_registry(root: Path) -> Registry:
    """Read ``registry.json``; a missing file is an empty registry."""
    path = Path(root) / REGISTRY_FILE
    try:
        # Never read more than the limit: a damaged or replaced file of any
        # size must not be loaded whole into the shared Memory Server.
        with path.open("rb") as handle:
            raw = handle.read(MAX_REGISTRY_BYTES + 1)
    except FileNotFoundError:
        return Registry()
    except OSError as exc:
        raise KnowledgeRegistryError(type(exc).__name__) from exc
    if len(raw) > MAX_REGISTRY_BYTES:
        raise KnowledgeRegistryError("registry_too_large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnowledgeRegistryError("invalid_json") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise KnowledgeRegistryError("unsupported_schema_version")
    packs = payload.get("packs")
    if not isinstance(packs, dict):
        raise KnowledgeRegistryError("invalid_packs")
    return Registry(
        enabled=_bool(payload.get("enabled", True)),
        packs={pack_id: _parse_record(pack_id, raw) for pack_id, raw in packs.items()},
    )


def save_registry(root: Path, registry: Registry) -> None:
    content = json.dumps(registry.to_json(), ensure_ascii=False, indent=2)
    if len(content.encode("utf-8")) > MAX_REGISTRY_BYTES:
        raise KnowledgeRegistryError("registry_too_large")
    atomic_write_text(Path(root) / REGISTRY_FILE, content)
