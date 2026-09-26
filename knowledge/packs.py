"""Validated, local-only community knowledge data packs."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_bytes

from ._mutation_lock import mutation_lock
from ._strict_file import read_bounded_regular_file
from .chunking import derive_knowledge_chunks
from .filters import sanitize_external_text
from .limits import MAX_PACK_BYTES
from .models import (
    KnowledgeEntry,
    normalize_knowledge_title,
)
from .store import KnowledgeStore
from .subscriptions import canonical_pack_bytes, validate_subscription


PACK_SCHEMA_VERSION = 1
PACK_REGISTRY_SCHEMA_VERSION = 4
MATERIAL_TYPES = frozenset(("knowledge", "corpus"))
MAX_PACK_ENTRIES = 5_000
MAX_PACK_PROJECTED_CHUNKS = 5_000
MAX_PACK_TERMS_PER_ROLE = 64
MAX_PACK_TERM_BYTES_PER_ENTRY = 32 * 1024
MAX_PACK_TAGS_PER_ENTRY = 64
MAX_PACK_TAG_BYTES_PER_ENTRY = 32 * 1024
MIN_INSTALL_FREE_BYTES = 512 * 1024 * 1024
MAX_PACK_REGISTRY_BYTES = 32 * 1024 * 1024
MAX_PACK_REMOVE_INTENT_BYTES = 48 * 1024 * 1024
PACK_REMOVE_INTENT_SCHEMA_VERSION = 1
PACK_REMOVE_INTENT_NAME = "pack-remove-intent.json"
_PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TERM_ROLES = frozenset(("alias", "recognition"))


class KnowledgePackRegistryError(ValueError):
    """Raised when an existing pack registry cannot be trusted."""


class KnowledgePackRecoveryRequiredError(KnowledgePackRegistryError):
    """Raised when registry and SQLite evidence cannot be reconciled safely."""


@dataclass(frozen=True, slots=True)
class KnowledgePackSource:
    name: str
    homepage: str
    license: str


@dataclass(frozen=True, slots=True)
class KnowledgePack:
    schema_version: int
    pack_id: str
    material_type: str
    source: KnowledgePackSource
    entries: tuple[KnowledgeEntry, ...]

    @property
    def source_tag(self) -> str:
        return f"source:community.{self.pack_id}"


@dataclass(frozen=True, slots=True)
class PackInstallResult:
    pack_id: str
    source_tag: str
    material_type: str
    entries: int
    retrieval_mode: str = "bm25"


@dataclass(frozen=True, slots=True)
class PackPreflight:
    entries: int
    projected_chunks: int
    content_bytes: int
    estimated_working_bytes: int


@dataclass(frozen=True, slots=True)
class _SourceSnapshot:
    entries: tuple[KnowledgeEntry, ...]
    ready_embeddings: tuple[dict[str, object], ...]
    embedding_policy: str


@dataclass(frozen=True, slots=True)
class _RegistrySnapshot:
    payload: dict[str, Any]
    raw: bytes


def pack_payload(pack: KnowledgePack) -> dict[str, object]:
    """Return the canonical package payload; entries remain strictly five-field."""
    payload: dict[str, object] = {
        "schema_version": pack.schema_version,
        "pack_id": pack.pack_id,
        "material_type": pack.material_type,
        "source": {
            "name": pack.source.name,
            "homepage": pack.source.homepage,
            "license": pack.source.license,
        },
        "entries": [
            {
                "title": entry.title,
                "terms": {role: list(values) for role, values in entry.terms.items()},
                "tags": [tag for tag in entry.tags if not tag.startswith("source:")],
                "summary": entry.summary,
                "content": entry.content,
            }
            for entry in pack.entries
        ],
    }
    return payload


def pack_identity_sha256(pack: KnowledgePack) -> str:
    """Hash normalized pack content independently of source entry order."""
    payload = pack_payload(pack)
    entries = payload["entries"]
    if not isinstance(entries, list):  # pragma: no cover - pack_payload contract
        raise ValueError("knowledge pack entries are invalid")
    payload["entries"] = sorted(
        entries,
        key=lambda row: normalize_knowledge_title(str(row.get("title") or "")),
    )
    return hashlib.sha256(canonical_pack_bytes(payload)).hexdigest()


def validate_pack_subscription(
    pack: KnowledgePack,
    payload: object,
) -> dict[str, str]:
    """Validate one subscription and bind its material type to the pack."""
    subscription = validate_subscription(payload)
    if subscription.material_type != pack.material_type:
        raise ValueError("knowledge pack subscription material_type mismatch")
    return subscription.to_dict()


def get_pack_registry_path(database_path: str | Path) -> Path:
    return Path(database_path).with_name("packs.json")


def _pack_remove_intent_path(database_path: str | Path) -> Path:
    return Path(database_path).with_name(PACK_REMOVE_INTENT_NAME)


def _registry_bytes(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    if len(raw) > MAX_PACK_REGISTRY_BYTES:
        raise KnowledgePackRegistryError(
            "knowledge pack registry exceeds its size limit"
        )
    return raw


def _write_registry(path: Path, payload: dict[str, Any]) -> bytes:
    raw = _registry_bytes(payload)
    atomic_write_bytes(path, raw)
    _fsync_parent(path)
    return raw


def _fsync_parent(path: Path) -> None:
    if os.name == "nt":
        # atomic_write_bytes flushes the replacement file itself. Windows does
        # not expose a portable directory fsync; closing the replace handle is
        # the strongest generally available contract here.
        return
    descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load_pack(path: str | Path) -> KnowledgePack:
    input_path = Path(path)
    if input_path.stat().st_size > MAX_PACK_BYTES:
        raise ValueError("knowledge pack exceeds the size limit")
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("knowledge pack is not valid UTF-8 JSON") from exc
    return validate_pack(payload)


def validate_pack(payload: object) -> KnowledgePack:
    if not isinstance(payload, dict):
        raise ValueError("knowledge pack root must be an object")
    schema_version = payload.get("schema_version")
    if schema_version != PACK_SCHEMA_VERSION:
        raise ValueError("unsupported knowledge pack schema version")
    allowed_keys = {"schema_version", "pack_id", "material_type", "source", "entries"}
    _reject_unknown_keys(payload, allowed_keys, "knowledge pack")
    material_type = _material_type(payload.get("material_type"))
    pack_id = _identifier(payload.get("pack_id"), "pack_id")
    source_payload = payload.get("source")
    if not isinstance(source_payload, dict):
        raise ValueError("knowledge pack source must be an object")
    _reject_unknown_keys(source_payload, {"name", "homepage", "license"}, "source")
    source = KnowledgePackSource(
        name=_required_text(source_payload.get("name"), "source.name", 200),
        homepage=_optional_text(
            source_payload.get("homepage"), "source.homepage", 2_000
        ),
        license=_required_text(source_payload.get("license"), "source.license", 500),
    )
    rows = payload.get("entries")
    if not isinstance(rows, list) or not rows:
        raise ValueError("knowledge pack entries must be a non-empty array")
    if len(rows) > MAX_PACK_ENTRIES:
        raise ValueError("knowledge pack contains too many entries")
    source_tag = f"source:community.{pack_id}"
    entries: list[KnowledgeEntry] = []
    seen_titles: set[str] = set()
    for index, row in enumerate(rows):
        entry = _entry_from_payload(row, source_tag=source_tag, index=index)
        normalized_title = normalize_knowledge_title(entry.title)
        if normalized_title in seen_titles:
            raise ValueError("knowledge pack contains duplicate titles")
        seen_titles.add(normalized_title)
        entries.append(entry)
    pack = KnowledgePack(
        schema_version=int(schema_version),
        pack_id=pack_id,
        material_type=material_type,
        source=source,
        entries=tuple(entries),
    )
    preflight_pack(pack)
    return pack


def preflight_pack(pack: KnowledgePack) -> PackPreflight:
    """Bound user-controlled work before it reaches SQLite or ONNX."""
    projected_chunks = 0
    content_bytes = 0
    for entry in pack.entries:
        projected_chunks += len(
            derive_knowledge_chunks(
                entry,
                entry_key=f"{entry.source_tag}:{entry.title}",
            )
        )
        if projected_chunks > MAX_PACK_PROJECTED_CHUNKS:
            raise ValueError("knowledge pack would create too many chunks")
        content_bytes += len(entry.content.encode("utf-8"))
    # Allow room for the staging database, WAL/rollback work and float16 vectors.
    estimated = max(content_bytes * 2 + projected_chunks * 1024, 1)
    return PackPreflight(
        entries=len(pack.entries),
        projected_chunks=projected_chunks,
        content_bytes=content_bytes,
        estimated_working_bytes=estimated,
    )


def ensure_install_capacity(root: str | Path, preflight: PackPreflight) -> None:
    root_path = Path(root)
    probe = root_path if root_path.exists() else root_path.parent
    free = int(shutil.disk_usage(probe).free)
    required = max(MIN_INSTALL_FREE_BYTES, preflight.estimated_working_bytes * 2)
    if free < required:
        raise ValueError("not enough free disk space for knowledge pack staging")


def _snapshot_source(
    store: KnowledgeStore,
    source_tag: str,
    metadata: object,
) -> _SourceSnapshot:
    policy_counts = store.embedding_policy_counts(
        source_tag=source_tag,
        strict=True,
    )
    active_policies = tuple(
        policy for policy, count in policy_counts.items() if int(count) > 0
    )
    embedding_policy = (
        active_policies[0]
        if len(active_policies) == 1
        else (
            "local"
            if isinstance(metadata, dict)
            and metadata.get("local_embedding_enabled") is True
            else "prebuilt_only"
        )
    )
    return _SourceSnapshot(
        entries=tuple(
            entry
            for entry in store.list_active_entries_strict()
            if entry.source_tag == source_tag
        ),
        ready_embeddings=store.ready_embedding_records(
            source_tag=source_tag,
            strict=True,
        ),
        embedding_policy=embedding_policy,
    )


def _restore_source(
    store: KnowledgeStore,
    source_tag: str,
    snapshot: _SourceSnapshot,
) -> None:
    store.replace_source(
        source_tag,
        snapshot.entries,
        embedding_policy=snapshot.embedding_policy,
    )
    if snapshot.ready_embeddings:
        store.store_chunk_embeddings_strict(snapshot.ready_embeddings)


def install_pack(
    database_path: str | Path,
    pack: KnowledgePack,
    *,
    subscription: dict[str, str] | None = None,
    prepared_embeddings: tuple[dict[str, object], ...] = (),
    retrieval_mode: str = "bm25",
    embedding_policy: str = "prebuilt_only",
    index_metadata: dict[str, object] | None = None,
) -> PackInstallResult:
    """Replace one community source and its metadata with rollback on failure."""
    if retrieval_mode not in {"hybrid", "mixed", "bm25"}:
        raise ValueError("unsupported knowledge pack retrieval mode")
    if embedding_policy not in {"local", "prebuilt_only"}:
        raise ValueError("unsupported knowledge pack embedding policy")
    canonical_subscription = (
        validate_pack_subscription(pack, subscription)
        if subscription is not None
        else None
    )
    database_path = Path(database_path)
    registry_path = get_pack_registry_path(database_path)
    with mutation_lock(registry_path):
        _recover_pack_remove_intent_locked(database_path, registry_path)
        old_registry = _load_registry(registry_path)
        existing = old_registry.get("packs", {}).get(pack.pack_id)
        if isinstance(existing, dict):
            _validate_subscription_identity(
                existing.get("subscription"),
                canonical_subscription,
            )
        store = KnowledgeStore(database_path)
        snapshot = _snapshot_source(store, pack.source_tag, existing)
        local_embedding_enabled = embedding_policy == "local" or (
            isinstance(existing, dict)
            and existing.get("local_embedding_enabled") is True
        )
        effective_policy = "local" if local_embedding_enabled else "prebuilt_only"
        new_registry = _registry_with_pack(
            old_registry,
            pack,
            subscription=canonical_subscription,
            retrieval_mode=retrieval_mode,
            index_metadata=index_metadata,
            local_embedding_enabled=local_embedding_enabled,
        )
        _registry_bytes(new_registry)
        try:
            store.replace_source(
                pack.source_tag,
                pack.entries,
                embedding_policy=effective_policy,
            )
            if prepared_embeddings:
                stored = store.store_chunk_embeddings_strict(prepared_embeddings)
                if stored != len(prepared_embeddings):
                    raise ValueError(
                        "prebuilt knowledge index was not imported completely"
                    )
            _write_registry(registry_path, new_registry)
        except Exception:
            _restore_source(store, pack.source_tag, snapshot)
            raise
    return PackInstallResult(
        pack_id=pack.pack_id,
        source_tag=pack.source_tag,
        material_type=pack.material_type,
        entries=len(pack.entries),
        retrieval_mode=retrieval_mode,
    )


def list_installed_packs(
    database_path: str | Path,
    *,
    busy_timeout_ms: int = 5_000,
) -> tuple[dict[str, Any], ...]:
    database_path = Path(database_path)
    try:
        packs = _load_registry(get_pack_registry_path(database_path)).get("packs", {})
    except KnowledgePackRegistryError:
        return ()
    if not isinstance(packs, dict):
        return ()
    source_tags = tuple(
        dict.fromkeys(
            str(value.get("source_tag") or "")
            for value in packs.values()
            if isinstance(value, dict)
            and str(value.get("source_tag") or "").startswith("source:")
        )
    )
    statuses = (
        KnowledgeStore(
            database_path,
            busy_timeout_ms=busy_timeout_ms,
        ).source_chunk_statuses(source_tags)
        if database_path.is_file()
        else {}
    )
    items: list[dict[str, Any]] = []
    for pack_id, value in sorted(packs.items()):
        if not isinstance(value, dict):
            continue
        source_tag = str(value.get("source_tag") or "")
        status = statuses.get(
            source_tag,
            {"chunks_total": 0, "chunks_ready": 0},
        )
        items.append(
            {
                "pack_id": pack_id,
                **value,
                "prebuilt_chunks_ready": int(status["chunks_ready"]),
                "prebuilt_chunks_missing": max(
                    int(status["chunks_total"]) - int(status["chunks_ready"]),
                    0,
                ),
            }
        )
    return tuple(items)


def pack_registry_state(database_path: str | Path) -> str:
    """Return missing/ready/invalid without collapsing corruption into empty."""
    intent_path = _pack_remove_intent_path(database_path)
    try:
        intent_path.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        return "recovery_required"
    else:
        try:
            recover_pack_remove_intent(database_path)
        except KnowledgePackRegistryError:
            return "recovery_required"
    registry_path = get_pack_registry_path(database_path)
    try:
        registry_path.lstat()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "invalid"
    try:
        _load_registry(registry_path)
    except KnowledgePackRegistryError:
        return "invalid"
    return "ready"


def list_installed_pack_routing_metadata(
    database_path: str | Path,
) -> tuple[dict[str, object], ...]:
    """Read routing policy from the registry without opening SQLite."""
    try:
        packs = _load_registry(get_pack_registry_path(database_path)).get("packs", {})
    except KnowledgePackRegistryError:
        return ()
    if not isinstance(packs, dict):
        return ()
    items: list[dict[str, object]] = []
    for pack_id, value in sorted(packs.items()):
        if not isinstance(value, dict):
            continue
        source_tag = str(value.get("source_tag") or "")
        if not source_tag.startswith("source:community."):
            continue
        items.append({
            "pack_id": str(pack_id),
            "source_tag": source_tag,
            "auto_context": value.get("auto_context") is True,
            "effective_material_type": _effective_material_type(value),
        })
    return tuple(items)


def installed_source_embedding_policies(
    database_path: str | Path,
) -> dict[str, str]:
    """Return explicit generation ownership for installed community sources."""
    try:
        registry = _load_registry(get_pack_registry_path(database_path))
    except KnowledgePackRegistryError:
        return {}
    return _embedding_policies_from_registry(registry)


def _embedding_policies_from_registry(registry: dict[str, Any]) -> dict[str, str]:
    packs = registry["packs"]
    policies: dict[str, str] = {}
    for metadata in packs.values():
        source_tag = str(metadata.get("source_tag") or "")
        if not source_tag.startswith("source:community."):
            continue
        policies[source_tag] = (
            "local"
            if metadata.get("local_embedding_enabled") is True
            else "prebuilt_only"
        )
    return policies


def reconcile_installed_source_embedding_policies(
    database_path: str | Path,
) -> dict[str, str]:
    """Make the durable registry authoritative before any indexing selection."""
    database_path = Path(database_path)
    registry_path = get_pack_registry_path(database_path)
    with mutation_lock(registry_path):
        _recover_pack_remove_intent_locked(database_path, registry_path)
        policies = _embedding_policies_from_registry(_load_registry(registry_path))
        store = KnowledgeStore(database_path)
        for source_tag, policy in policies.items():
            store.set_source_embedding_policy(source_tag, policy)
        return policies


def set_pack_auto_context(
    database_path: str | Path,
    pack_id: str,
    *,
    enabled: bool,
) -> None:
    pack_id = _identifier(pack_id, "pack_id")
    registry_path = get_pack_registry_path(database_path)
    with mutation_lock(registry_path):
        _recover_pack_remove_intent_locked(Path(database_path), registry_path)
        registry = _load_registry(registry_path)
        packs = registry.get("packs")
        if not isinstance(packs, dict) or not isinstance(packs.get(pack_id), dict):
            raise ValueError("knowledge pack is not installed")
        metadata = packs[pack_id]
        packs[pack_id] = {**metadata, "auto_context": bool(enabled)}
        _write_registry(registry_path, registry)


def set_pack_material_type_override(
    database_path: str | Path,
    pack_id: str,
    *,
    material_type: str | None,
) -> None:
    """Persist a local usage override without changing entries or vectors."""
    pack_id = _identifier(pack_id, "pack_id")
    normalized = None if material_type is None else _material_type(material_type)
    registry_path = get_pack_registry_path(database_path)
    with mutation_lock(registry_path):
        _recover_pack_remove_intent_locked(Path(database_path), registry_path)
        registry = _load_registry(registry_path)
        packs = registry.get("packs")
        metadata = packs.get(pack_id) if isinstance(packs, dict) else None
        if not isinstance(metadata, dict):
            raise ValueError("knowledge pack is not installed")
        declared = _material_type(metadata.get("declared_material_type", "knowledge"))
        previous_effective = _effective_material_type(metadata)
        effective = normalized or declared
        packs[pack_id] = {
            **metadata,
            "material_type_override": normalized,
            "effective_material_type": effective,
            "auto_context": True
            if effective == "corpus" and previous_effective != "corpus"
            else bool(metadata.get("auto_context")),
        }
        registry["schema_version"] = PACK_REGISTRY_SCHEMA_VERSION
        _write_registry(registry_path, registry)


def set_pack_index_policy(
    database_path: str | Path,
    pack_id: str,
    *,
    local_embedding_enabled: bool,
) -> None:
    """Persist explicit consent for local maintenance of one community index."""
    pack_id = _identifier(pack_id, "pack_id")
    database_path = Path(database_path)
    registry_path = get_pack_registry_path(database_path)
    with mutation_lock(registry_path):
        _recover_pack_remove_intent_locked(database_path, registry_path)
        registry = _load_registry(registry_path)
        packs = registry.get("packs")
        metadata = packs.get(pack_id) if isinstance(packs, dict) else None
        if not isinstance(metadata, dict):
            raise ValueError("knowledge pack is not installed")
        source_tag = str(metadata.get("source_tag") or "")
        if not source_tag.startswith("source:community."):
            raise ValueError("only community packs have an index policy")
        policy = "local" if local_embedding_enabled else "prebuilt_only"
        store = KnowledgeStore(database_path)
        packs[pack_id] = {
            **metadata,
            "local_embedding_enabled": bool(local_embedding_enabled),
        }
        _registry_bytes(registry)
        # Publish consent first. If the process exits before SQLite converges,
        # the indexer reconciles chunks from this registry before selecting work.
        _write_registry(registry_path, registry)
        store.set_source_embedding_policy(source_tag, policy)


def _remove_intent_bytes(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(raw) > MAX_PACK_REMOVE_INTENT_BYTES:
        raise KnowledgePackRecoveryRequiredError(
            "knowledge pack remove intent exceeds its size limit"
        )
    return raw


def _write_remove_intent(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_bytes(path, _remove_intent_bytes(payload))
    _fsync_parent(path)


def _read_remove_intent(path: Path) -> dict[str, Any] | None:
    try:
        raw = read_bounded_regular_file(path, max_bytes=MAX_PACK_REMOVE_INTENT_BYTES)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise KnowledgePackRecoveryRequiredError(
            "pack_registry_recovery_required"
        ) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnowledgePackRecoveryRequiredError(
            "pack_registry_recovery_required"
        ) from exc
    expected_fields = {
        "schema_version",
        "operation_id",
        "pack_id",
        "source_tag",
        "expected_entries",
        "before_registry_sha256",
        "after_registry_sha256",
        "target_registry_base64",
        "created_at",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise KnowledgePackRecoveryRequiredError("pack_registry_recovery_required")
    operation_id = payload.get("operation_id")
    pack_id = payload.get("pack_id")
    source_tag = payload.get("source_tag")
    expected_entries = payload.get("expected_entries")
    created_at = payload.get("created_at")
    if (
        payload.get("schema_version") != PACK_REMOVE_INTENT_SCHEMA_VERSION
        or not isinstance(operation_id, str)
        or len(operation_id) != 32
        or any(character not in "0123456789abcdef" for character in operation_id)
        or not isinstance(pack_id, str)
        or not _PACK_ID_RE.fullmatch(pack_id)
        or source_tag != f"source:community.{pack_id}"
        or type(expected_entries) is not int
        or expected_entries < 0
        or type(created_at) is not int
        or created_at <= 0
    ):
        raise KnowledgePackRecoveryRequiredError("pack_registry_recovery_required")
    before_digest = payload.get("before_registry_sha256")
    after_digest = payload.get("after_registry_sha256")
    if (
        not isinstance(before_digest, str)
        or not _SHA256_RE.fullmatch(before_digest)
        or not isinstance(after_digest, str)
        or not _SHA256_RE.fullmatch(after_digest)
    ):
        raise KnowledgePackRecoveryRequiredError("pack_registry_recovery_required")
    try:
        target_raw = base64.b64decode(
            str(payload.get("target_registry_base64") or ""),
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise KnowledgePackRecoveryRequiredError(
            "pack_registry_recovery_required"
        ) from exc
    if (
        not target_raw
        or len(target_raw) > MAX_PACK_REGISTRY_BYTES
        or hashlib.sha256(target_raw).hexdigest() != after_digest
        or _registry_bytes(_decode_registry(target_raw)) != target_raw
    ):
        raise KnowledgePackRecoveryRequiredError("pack_registry_recovery_required")
    return {**payload, "target_registry_bytes": target_raw}


def _strict_source_entry_count(database_path: Path, source_tag: str) -> int:
    if not database_path.is_file():
        return 0
    usage = KnowledgeStore(database_path).community_usage(
        source_tag=source_tag,
        strict=True,
    )
    return int(usage["entries_total"])


def _delete_remove_intent(path: Path) -> None:
    path.unlink(missing_ok=True)
    _fsync_parent(path)


def _recover_pack_remove_intent_locked(
    database_path: Path,
    registry_path: Path,
) -> None:
    intent_path = _pack_remove_intent_path(database_path)
    intent = _read_remove_intent(intent_path)
    if intent is None:
        return
    try:
        registry_snapshot = _load_registry_snapshot(registry_path, missing_ok=False)
    except KnowledgePackRegistryError as exc:
        raise KnowledgePackRecoveryRequiredError(
            "pack_registry_recovery_required"
        ) from exc
    current_digest = hashlib.sha256(registry_snapshot.raw).hexdigest()
    before_digest = str(intent["before_registry_sha256"])
    after_digest = str(intent["after_registry_sha256"])
    source_tag = str(intent["source_tag"])
    expected_entries = int(intent["expected_entries"])
    actual_entries = _strict_source_entry_count(database_path, source_tag)
    if current_digest == before_digest:
        if actual_entries != expected_entries:
            raise KnowledgePackRecoveryRequiredError(
                "pack_registry_recovery_required"
            )
        _delete_remove_intent(intent_path)
        return
    if current_digest == after_digest:
        if registry_snapshot.raw != intent["target_registry_bytes"]:
            raise KnowledgePackRecoveryRequiredError(
                "pack_registry_recovery_required"
            )
        if actual_entries:
            KnowledgeStore(database_path).replace_source(source_tag, ())
        if _strict_source_entry_count(database_path, source_tag) != 0:
            raise KnowledgePackRecoveryRequiredError(
                "pack_registry_recovery_required"
            )
        _delete_remove_intent(intent_path)
        return
    raise KnowledgePackRecoveryRequiredError("pack_registry_recovery_required")


def recover_pack_remove_intent(database_path: str | Path) -> None:
    database_path = Path(database_path)
    registry_path = get_pack_registry_path(database_path)
    with mutation_lock(registry_path):
        _recover_pack_remove_intent_locked(database_path, registry_path)


def remove_pack(database_path: str | Path, pack_id: str) -> int:
    """Remove one pack using a registry-first crash-recoverable journal."""
    pack_id = _identifier(pack_id, "pack_id")
    database_path = Path(database_path)
    registry_path = get_pack_registry_path(database_path)
    with mutation_lock(registry_path):
        _recover_pack_remove_intent_locked(database_path, registry_path)
        registry_snapshot = _load_registry_snapshot(registry_path)
        registry = registry_snapshot.payload
        packs = registry.get("packs")
        metadata = packs.get(pack_id) if isinstance(packs, dict) else None
        if not isinstance(metadata, dict):
            raise ValueError("knowledge pack is not installed")
        source_tag = str(metadata.get("source_tag") or "")
        if not source_tag.startswith("source:community."):
            raise ValueError("only community packs can be removed")

        removed_entries = _strict_source_entry_count(database_path, source_tag)
        new_packs = dict(packs)
        new_packs.pop(pack_id, None)
        target_registry = {
            "schema_version": PACK_REGISTRY_SCHEMA_VERSION,
            "packs": new_packs,
        }
        target_raw = _registry_bytes(target_registry)
        intent = {
            "schema_version": PACK_REMOVE_INTENT_SCHEMA_VERSION,
            "operation_id": uuid.uuid4().hex,
            "pack_id": pack_id,
            "source_tag": source_tag,
            "expected_entries": removed_entries,
            "before_registry_sha256": hashlib.sha256(
                registry_snapshot.raw
            ).hexdigest(),
            "after_registry_sha256": hashlib.sha256(target_raw).hexdigest(),
            "target_registry_base64": base64.b64encode(target_raw).decode("ascii"),
            "created_at": int(time.time()),
        }
        intent_path = _pack_remove_intent_path(database_path)
        _write_remove_intent(intent_path, intent)
        _write_registry(registry_path, target_registry)
        KnowledgeStore(database_path).replace_source(source_tag, ())
        if _strict_source_entry_count(database_path, source_tag) != 0:
            raise KnowledgePackRecoveryRequiredError(
                "pack_registry_recovery_required"
            )
        _delete_remove_intent(intent_path)
    return removed_entries


def enabled_pack_source_tags(database_path: str | Path) -> tuple[str, ...]:
    return tuple(
        str(pack.get("source_tag"))
        for pack in list_installed_packs(database_path)
        if pack.get("auto_context") is True
        and _effective_material_type(pack) == "knowledge"
        and str(pack.get("source_tag") or "").startswith("source:")
    )


def material_source_tags(
    database_path: str | Path,
    material_types: tuple[str, ...],
) -> tuple[str, ...]:
    """Return community source tags whose effective type is allowed."""
    allowed = frozenset(_material_type(value) for value in material_types)
    return tuple(
        str(pack.get("source_tag"))
        for pack in list_installed_packs(database_path)
        if _effective_material_type(pack) in allowed
        and str(pack.get("source_tag") or "").startswith("source:community.")
    )


def _entry_from_payload(
    payload: object,
    *,
    source_tag: str,
    index: int,
) -> KnowledgeEntry:
    if not isinstance(payload, dict):
        raise ValueError(f"entries[{index}] must be an object")
    _reject_unknown_keys(
        payload,
        {"title", "terms", "tags", "summary", "content"},
        f"entries[{index}]",
    )
    terms = payload.get("terms", {})
    if not isinstance(terms, dict) or set(terms) - _TERM_ROLES:
        raise ValueError(f"entries[{index}].terms contains unsupported roles")
    term_bytes = 0
    for role in sorted(_TERM_ROLES):
        values = terms.get(role, [])
        if not isinstance(values, list) or any(
            not isinstance(value, str) for value in values
        ):
            raise ValueError(f"entries[{index}].terms.{role} must be a string array")
        if len(values) > MAX_PACK_TERMS_PER_ROLE:
            raise ValueError(f"entries[{index}].terms.{role} contains too many terms")
        term_bytes = _bounded_utf8_size(
            values,
            used_bytes=term_bytes,
            max_bytes=MAX_PACK_TERM_BYTES_PER_ENTRY,
            field=f"entries[{index}].terms",
        )
    normalized_terms = {
        role: tuple(terms.get(role, []))
        for role in sorted(_TERM_ROLES)
    }
    tags = payload.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError(f"entries[{index}].tags must be a string array")
    if len(tags) > MAX_PACK_TAGS_PER_ENTRY:
        raise ValueError(f"entries[{index}].tags contains too many tags")
    _bounded_utf8_size(
        tags,
        used_bytes=0,
        max_bytes=MAX_PACK_TAG_BYTES_PER_ENTRY,
        field=f"entries[{index}].tags",
    )
    if any(tag.startswith("source:") for tag in tags):
        raise ValueError("community entries cannot declare source tags")
    return KnowledgeEntry(
        title=_required_text(payload.get("title"), f"entries[{index}].title", 500),
        terms=normalized_terms,
        tags=(source_tag, *tags),
        summary=_optional_text(
            payload.get("summary"), f"entries[{index}].summary", 4_000
        ),
        content=_required_text(
            payload.get("content"), f"entries[{index}].content", 80_000
        ),
    )


def _bounded_utf8_size(
    values: list[str],
    *,
    used_bytes: int,
    max_bytes: int,
    field: str,
) -> int:
    total = used_bytes
    for value in values:
        remaining = max_bytes - total
        if len(value) > remaining:
            raise ValueError(f"{field} exceeds the metadata size limit")
        try:
            encoded_size = len(value.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValueError(f"{field} must contain valid UTF-8") from exc
        if encoded_size > remaining:
            raise ValueError(f"{field} exceeds the metadata size limit")
        total += encoded_size
    return total


def _identifier(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not _PACK_ID_RE.fullmatch(text):
        raise ValueError(
            f"{field} must use lowercase letters, numbers, dots, dashes or underscores"
        )
    return text


def _material_type(value: object) -> str:
    text = str(value or "").strip().lower()
    if text not in MATERIAL_TYPES:
        raise ValueError("material_type must be knowledge or corpus")
    return text


def _effective_material_type(metadata: object) -> str:
    if not isinstance(metadata, dict):
        return "knowledge"
    override = metadata.get("material_type_override")
    if override in MATERIAL_TYPES:
        return str(override)
    declared = metadata.get("declared_material_type")
    return str(declared) if declared in MATERIAL_TYPES else "knowledge"


def _required_text(value: object, field: str, max_chars: int) -> str:
    text = _optional_text(value, field, max_chars)
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _optional_text(value: object, field: str, max_chars: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    if len(value) > max_chars:
        raise ValueError(f"{field} exceeds the length limit")
    return sanitize_external_text(value, max_chars=max_chars)


def _reject_unknown_keys(payload: dict, allowed: set[str], field: str) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"{field} contains unsupported fields")


def _validate_marketplace_package_identities(packs: dict[str, Any]) -> None:
    owners: dict[str, str] = {}
    for pack_id, metadata in packs.items():
        if not isinstance(metadata, dict):
            continue
        subscription = metadata.get("subscription")
        if not isinstance(subscription, dict):
            continue
        if str(subscription.get("provider") or "") != "plugin-market":
            continue
        package_id = str(subscription.get("provider_package_id") or "")
        if not package_id:
            continue
        previous_owner = owners.setdefault(package_id, pack_id)
        if previous_owner != pack_id:
            raise KnowledgePackRegistryError(
                "knowledge pack registry contains duplicate marketplace identities"
            )


def _load_registry(
    path: Path,
    *,
    missing_ok: bool = True,
) -> dict[str, Any]:
    return _load_registry_snapshot(path, missing_ok=missing_ok).payload


def _load_registry_snapshot(
    path: Path,
    *,
    missing_ok: bool = True,
) -> _RegistrySnapshot:
    try:
        raw = read_bounded_regular_file(path, max_bytes=MAX_PACK_REGISTRY_BYTES)
    except FileNotFoundError as exc:
        if not missing_ok:
            raise KnowledgePackRegistryError(
                "knowledge pack registry disappeared during migration"
            ) from exc
        payload = {"schema_version": PACK_REGISTRY_SCHEMA_VERSION, "packs": {}}
        return _RegistrySnapshot(payload=payload, raw=_registry_bytes(payload))
    except (OSError, ValueError) as exc:
        raise KnowledgePackRegistryError(
            "knowledge pack registry is unreadable"
        ) from exc
    return _RegistrySnapshot(payload=_decode_registry(raw), raw=raw)


def _decode_registry(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnowledgePackRegistryError(
            "knowledge pack registry is corrupt"
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("packs"), dict):
        raise KnowledgePackRegistryError(
            "knowledge pack registry has an invalid structure"
        )
    previous_schema_version = payload.get("schema_version")
    if (
        isinstance(previous_schema_version, int)
        and previous_schema_version > PACK_REGISTRY_SCHEMA_VERSION
    ):
        raise KnowledgePackRegistryError(
            "knowledge pack registry uses a newer schema version"
        )
    if previous_schema_version != PACK_REGISTRY_SCHEMA_VERSION:
        raise KnowledgePackRegistryError(
            "knowledge pack registry uses an unsupported schema version"
        )
    for pack_id, metadata in tuple(payload["packs"].items()):
        if not isinstance(pack_id, str) or not _PACK_ID_RE.fullmatch(pack_id):
            raise KnowledgePackRegistryError(
                f"knowledge pack registry key {pack_id!r} is invalid"
            )
        if not isinstance(metadata, dict):
            raise KnowledgePackRegistryError(
                f"knowledge pack registry entry {pack_id!r} is invalid"
            )
        expected_source_tag = f"source:community.{pack_id}"
        if metadata.get("source_tag") != expected_source_tag:
            raise KnowledgePackRegistryError(
                f"knowledge pack registry entry {pack_id!r} has an invalid source_tag"
            )
        declared = metadata.get("declared_material_type")
        if not isinstance(declared, str) or declared not in MATERIAL_TYPES:
            raise KnowledgePackRegistryError(
                f"knowledge pack registry entry {pack_id!r} has an invalid "
                "declared_material_type"
            )
        pack_sha256 = metadata.get("pack_sha256")
        if not isinstance(pack_sha256, str) or not _SHA256_RE.fullmatch(
            pack_sha256
        ):
            raise KnowledgePackRegistryError(
                f"knowledge pack registry entry {pack_id!r} has an invalid "
                "pack_sha256"
            )
        override = metadata.get("material_type_override")
        if override is not None and (
            not isinstance(override, str) or override not in MATERIAL_TYPES
        ):
            raise KnowledgePackRegistryError(
                f"knowledge pack registry entry {pack_id!r} has an invalid "
                "material_type_override"
            )
        if "subscription" not in metadata:
            raise KnowledgePackRegistryError(
                f"knowledge pack registry entry {pack_id!r} has an invalid "
                "subscription"
            )
        subscription = metadata.get("subscription")
        if subscription is not None:
            try:
                subscription = validate_subscription(subscription).to_dict()
            except ValueError as exc:
                raise KnowledgePackRegistryError(
                    f"knowledge pack registry entry {pack_id!r} has an invalid "
                    "subscription"
                ) from exc
        effective = str(override or declared)
        auto_context = metadata.get("auto_context")
        if not isinstance(auto_context, bool):
            raise KnowledgePackRegistryError(
                f"knowledge pack registry entry {pack_id!r} has an invalid auto_context"
            )
        normalized = {
            **metadata,
            "pack_sha256": pack_sha256,
            "declared_material_type": declared,
            "material_type_override": override,
            "effective_material_type": effective,
            "auto_context": auto_context,
            "subscription": subscription,
        }
        if normalized != metadata:
            payload["packs"][pack_id] = normalized
    _validate_marketplace_package_identities(payload["packs"])
    return payload


def _registry_with_pack(
    registry: dict[str, Any],
    pack: KnowledgePack,
    *,
    subscription: dict[str, str] | None = None,
    retrieval_mode: str = "bm25",
    index_metadata: dict[str, object] | None = None,
    local_embedding_enabled: bool = False,
) -> dict[str, Any]:
    packs = dict(registry.get("packs", {}))
    previous = packs.get(pack.pack_id, {})
    auto_context = (
        previous.get("auto_context") is True
        if isinstance(previous, dict) and previous
        else pack.material_type == "corpus"
    )
    previous_subscription = (
        previous.get("subscription") if isinstance(previous, dict) else None
    )
    override = (
        str(previous.get("material_type_override"))
        if isinstance(previous, dict)
        and previous.get("material_type_override") in MATERIAL_TYPES
        else None
    )
    effective_material_type = override or pack.material_type
    packs[pack.pack_id] = {
        "source_tag": pack.source_tag,
        "pack_sha256": pack_identity_sha256(pack),
        "source": {
            "name": pack.source.name,
            "homepage": pack.source.homepage,
            "license": pack.source.license,
        },
        "entries": len(pack.entries),
        "declared_material_type": pack.material_type,
        "material_type_override": override,
        "effective_material_type": effective_material_type,
        "auto_context": auto_context,
        "subscription": subscription
        if subscription is not None
        else previous_subscription,
        "retrieval_mode": retrieval_mode,
        "index_origin": str((index_metadata or {}).get("index_origin") or "none"),
        "index_trust": str((index_metadata or {}).get("index_trust") or "none"),
        "index_validation": str(
            (index_metadata or {}).get("index_validation") or "absent"
        ),
        "index_fallback_reason": str(
            (index_metadata or {}).get("index_fallback_reason") or ""
        )[:80],
        "local_embedding_enabled": bool(local_embedding_enabled),
        "prebuilt_chunks_ready": int(
            (index_metadata or {}).get("prebuilt_chunks_ready") or 0
        ),
        "prebuilt_chunks_missing": int(
            (index_metadata or {}).get("prebuilt_chunks_missing") or 0
        ),
    }
    _validate_marketplace_package_identities(packs)
    return {"schema_version": PACK_REGISTRY_SCHEMA_VERSION, "packs": packs}


def _validate_subscription_identity(
    previous: object,
    replacement: dict[str, str] | None,
) -> None:
    previous_is_subscription = isinstance(previous, dict)
    replacement_is_subscription = isinstance(replacement, dict)
    if previous_is_subscription != replacement_is_subscription:
        raise ValueError("knowledge pack subscription identity cannot change")
    if not previous_is_subscription:
        return
    for field in ("provider", "remote_id"):
        if str(previous.get(field) or "") != str(replacement.get(field) or ""):
            raise ValueError("knowledge pack subscription identity cannot change")
    previous_package_id = str(previous.get("provider_package_id") or "")
    replacement_package_id = str(replacement.get("provider_package_id") or "")
    if previous_package_id and previous_package_id != replacement_package_id:
        raise ValueError("knowledge pack subscription identity cannot change")
    if (
        not previous_package_id
        and replacement_package_id
        and str(replacement.get("trust") or "") != "trusted_market"
    ):
        raise ValueError("knowledge pack subscription identity cannot change")
