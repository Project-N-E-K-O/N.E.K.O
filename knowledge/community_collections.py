"""Persistent declarations for data-only community knowledge collections."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from utils.file_utils import atomic_write_json

from .collection_specs import (
    COMMUNITY_MATCH_POLICY,
    GENERIC_REFERENCE_RESPONSE_POLICY,
    CollectionSpec,
)
from .identifiers import validate_knowledge_identifier


COMMUNITY_REGISTRY_VERSION = 1
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CommunityCollectionRecord:
    collection_id: str
    display_name: str
    storage_directory: str
    created_by_pack: str
    status: str = "active"


def validate_collection_id(value: object) -> str:
    """Validate a portable identifier before deriving a directory from it."""
    return validate_knowledge_identifier(value)


def community_storage_directory(collection_id: str) -> str:
    """Return the only directory shape accepted for a community collection."""
    return f"community/{validate_collection_id(collection_id)}"


def get_community_registry_path(knowledge_root: str | Path) -> Path:
    return Path(knowledge_root) / "collections.json"


def get_community_mutation_lock_path(knowledge_root: str | Path) -> Path:
    return Path(knowledge_root) / ".mutation.lock"


def load_community_collections(
    knowledge_root: str | Path,
) -> dict[str, CommunityCollectionRecord]:
    """Load valid records and safely ignore damaged or forged paths."""
    path = get_community_registry_path(knowledge_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning(
            "[public-knowledge] ignored invalid collection registry type=%s",
            type(exc).__name__,
        )
        return {}
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("collections")
    version = payload.get("schema_version")
    if isinstance(version, int) and version > COMMUNITY_REGISTRY_VERSION:
        # Never open a newer registry, but a downgrade must not brick the whole
        # service: return no records and let write_community_collections refuse
        # to overwrite a newer registry on a later write.
        return {}
    if version != COMMUNITY_REGISTRY_VERSION or not isinstance(rows, dict):
        return {}
    records: dict[str, CommunityCollectionRecord] = {}
    for collection_id, raw in rows.items():
        if not isinstance(raw, dict):
            continue
        try:
            normalized_id = validate_collection_id(collection_id)
            expected_directory = community_storage_directory(normalized_id)
            display_name = _display_name(raw.get("display_name"))
            created_by_pack = str(raw.get("created_by_pack") or "").strip()
            status = str(raw.get("status") or "active").strip()
            if raw.get("storage_directory") != expected_directory:
                continue
            if not created_by_pack or status not in {"active", "conflict"}:
                continue
        except ValueError:
            continue
        records[normalized_id] = CommunityCollectionRecord(
            collection_id=normalized_id,
            display_name=display_name,
            storage_directory=expected_directory,
            created_by_pack=created_by_pack,
            status=status,
        )
    return records


def write_community_collections(
    knowledge_root: str | Path,
    records: Mapping[str, CommunityCollectionRecord],
) -> None:
    """Atomically replace the lightweight community collection registry."""
    path = get_community_registry_path(knowledge_root)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        existing_version = (
            existing.get("schema_version")
            if isinstance(existing, dict)
            else None
        )
        if (
            isinstance(existing_version, int)
            and existing_version > COMMUNITY_REGISTRY_VERSION
        ):
            raise ValueError(
                "community collection registry version is newer than supported"
            )
    payload = {
        "schema_version": COMMUNITY_REGISTRY_VERSION,
        "collections": {
            collection_id: {
                key: value
                for key, value in asdict(records[collection_id]).items()
                if key != "collection_id"
            }
            for collection_id in sorted(records)
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload, ensure_ascii=False, indent=2)

def new_community_collection(
    collection_id: str,
    display_name: object,
    *,
    created_by_pack: str,
) -> CommunityCollectionRecord:
    normalized_id = validate_collection_id(collection_id)
    return CommunityCollectionRecord(
        collection_id=normalized_id,
        display_name=_display_name(display_name),
        storage_directory=community_storage_directory(normalized_id),
        created_by_pack=created_by_pack,
    )


def community_collection_spec(record: CommunityCollectionRecord) -> CollectionSpec:
    """Build the fixed, non-overridable policy for one community database."""
    return CollectionSpec(
        collection_id=record.collection_id,
        storage_directory=record.storage_directory,
        display_name=record.display_name,
        priority=0,
        auto_context_enabled=False,
        restrict_auto_context_to_registered_sources=True,
        match_policy=COMMUNITY_MATCH_POLICY,
        response_policy=GENERIC_REFERENCE_RESPONSE_POLICY,
        community_managed=True,
    )


def _display_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("collection.display_name is required")
    text = value.strip()
    if len(text) > 200:
        raise ValueError("collection.display_name exceeds the length limit")
    return text
