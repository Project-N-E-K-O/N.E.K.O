"""Validation and transactional storage for data-only knowledge packs."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

from utils.file_utils import atomic_write_json

from .community_collections import validate_collection_id
from .engine.filters import sanitize_external_text
from .engine.models import KnowledgeEntry
from .engine.mutation_lock import mutation_lock
from .engine.store import KnowledgeStore, KnowledgeStoreError
from .identifiers import validate_knowledge_identifier


PACK_SCHEMA_VERSION = 1
MAX_PACK_BYTES = 10 * 1024 * 1024
MAX_PACK_ENTRIES = 10_000
MAX_TERMS_PER_ROLE = 100
MAX_TAGS_PER_ENTRY = 100
MAX_TERM_OR_TAG_CHARS = 300
_TERM_ROLES = ("alias", "recognition")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class KnowledgeValidationIssue:
    """One stable, content-free validation diagnostic."""

    severity: str
    path: str
    code: str
    message: str


class KnowledgePackValidationError(ValueError):
    """Raised when a pack violates the versioned public contract."""

    def __init__(self, *issues: KnowledgeValidationIssue) -> None:
        self.issues = tuple(issues)
        message = "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        super().__init__(message or "knowledge pack validation failed")


@dataclass(frozen=True, slots=True)
class KnowledgeCollectionManifest:
    """The only collection metadata that an untrusted pack may declare."""

    display_name: str


@dataclass(frozen=True, slots=True)
class KnowledgePackSource:
    name: str
    homepage: str
    license: str


@dataclass(frozen=True, slots=True)
class KnowledgePack:
    schema_version: int
    pack_id: str
    collection_id: str
    collection: KnowledgeCollectionManifest | None
    source: KnowledgePackSource
    entries: tuple[KnowledgeEntry, ...]

    @property
    def source_tag(self) -> str:
        return f"source:community.{self.pack_id}"


@dataclass(frozen=True, slots=True)
class PackInstallResult:
    pack_id: str
    collection_id: str
    source_tag: str
    entries: int


@dataclass(frozen=True, slots=True)
class PackStorageSnapshot:
    """Private rollback material owned by the service transaction."""

    entries: tuple[KnowledgeEntry, ...]
    registry: dict[str, Any]


def canonical_pack_bytes(payload: object) -> bytes:
    """Return canonical publishing bytes with exactly one final LF."""
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def get_pack_registry_path(database_path: str | Path) -> Path:
    return Path(database_path).with_name("packs.json")


def load_pack(path: str | Path) -> KnowledgePack:
    input_path = Path(path)
    try:
        size = input_path.stat().st_size
    except OSError as exc:
        raise KnowledgePackValidationError(
            KnowledgeValidationIssue(
                "error", "$", "unreadable", "cannot read the pack file"
            )
        ) from exc
    if size > MAX_PACK_BYTES:
        _fail("$", "size_limit", "knowledge pack exceeds the size limit")
    try:
        payload = decode_json_document(input_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise KnowledgePackValidationError(
            KnowledgeValidationIssue("error", "$", "invalid_json", "not valid UTF-8 JSON")
        ) from exc
    return validate_pack(payload)


def decode_json_document(value: str) -> object:
    """Decode strict JSON and reject non-standard numeric constants."""
    return json.loads(value, parse_constant=_reject_json_constant)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def validate_pack(payload: object) -> KnowledgePack:
    """Parse the runtime contract used by the CLI and every installer."""
    if not isinstance(payload, dict):
        _fail("$", "type", "knowledge pack root must be an object")
    _reject_unknown_keys(
        payload,
        {"schema_version", "pack_id", "collection_id", "collection", "source", "entries"},
        "$",
    )
    if payload.get("schema_version") != PACK_SCHEMA_VERSION:
        _fail("schema_version", "unsupported_version", "unsupported schema version")
    pack_id = _identifier(payload.get("pack_id"), "pack_id")
    try:
        collection_id = validate_collection_id(payload.get("collection_id"))
    except ValueError as exc:
        _fail("collection_id", "invalid_identifier", str(exc))

    collection_payload = payload.get("collection")
    collection: KnowledgeCollectionManifest | None = None
    if collection_payload is not None:
        if not isinstance(collection_payload, dict):
            _fail("collection", "type", "must be an object")
        _reject_unknown_keys(collection_payload, {"display_name"}, "collection")
        collection = KnowledgeCollectionManifest(
            display_name=_required_text(
                collection_payload.get("display_name"),
                "collection.display_name",
                200,
            )
        )

    source_payload = payload.get("source")
    if not isinstance(source_payload, dict):
        _fail("source", "type", "must be an object")
    _reject_unknown_keys(source_payload, {"name", "homepage", "license"}, "source")
    source = KnowledgePackSource(
        name=_required_text(source_payload.get("name"), "source.name", 200),
        homepage=_homepage(source_payload.get("homepage")),
        license=_required_text(source_payload.get("license"), "source.license", 500),
    )
    rows = payload.get("entries")
    if not isinstance(rows, list) or not rows:
        _fail("entries", "type", "must be a non-empty array")
    if len(rows) > MAX_PACK_ENTRIES:
        _fail("entries", "entry_limit", "contains too many entries")
    source_tag = f"source:community.{pack_id}"
    entries: list[KnowledgeEntry] = []
    seen_titles: set[str] = set()
    for index, row in enumerate(rows):
        entry = _entry_from_payload(row, source_tag=source_tag, index=index)
        normalized_title = entry.title.casefold()
        if normalized_title in seen_titles:
            _fail(f"entries[{index}].title", "duplicate", "duplicate title")
        seen_titles.add(normalized_title)
        entries.append(entry)
    return KnowledgePack(
        schema_version=PACK_SCHEMA_VERSION,
        pack_id=pack_id,
        collection_id=collection_id,
        collection=collection,
        source=source,
        entries=tuple(entries),
    )


def capture_pack_storage(database_path: str | Path, pack_id: str) -> PackStorageSnapshot:
    """Capture only the source slice and registry needed for rollback."""
    source_tag = f"source:community.{_identifier(pack_id, 'pack_id')}"
    store = KnowledgeStore(database_path)
    return PackStorageSnapshot(
        entries=tuple(
            entry for entry in store.list_active_entries() if entry.source_tag == source_tag
        ),
        registry=_load_registry(get_pack_registry_path(database_path)),
    )


def restore_pack_storage(
    database_path: str | Path,
    pack_id: str,
    snapshot: PackStorageSnapshot,
) -> None:
    """Restore a captured source slice after a wider service transaction fails."""
    database_path = Path(database_path)
    source_tag = f"source:community.{_identifier(pack_id, 'pack_id')}"
    registry_path = get_pack_registry_path(database_path)
    with mutation_lock(registry_path):
        KnowledgeStore(database_path).replace_source(source_tag, snapshot.entries)
        atomic_write_json(registry_path, snapshot.registry, ensure_ascii=False, indent=2)


def install_pack(
    database_path: str | Path,
    pack: KnowledgePack,
    *,
    subscription: dict[str, str] | None = None,
) -> PackInstallResult:
    """Replace one community source and its metadata with rollback on failure."""
    database_path = Path(database_path)
    registry_path = get_pack_registry_path(database_path)
    with mutation_lock(registry_path):
        snapshot = capture_pack_storage(database_path, pack.pack_id)
        existing = snapshot.registry.get("packs", {}).get(pack.pack_id)
        if isinstance(existing, dict):
            existing_collection = str(existing.get("collection_id") or "")
            if existing_collection and existing_collection != pack.collection_id:
                raise ValueError("knowledge pack cannot change its collection")
            _validate_subscription_identity(existing.get("subscription"), subscription)
        registry = _registry_with_pack(snapshot.registry, pack, subscription=subscription)
        try:
            KnowledgeStore(database_path).replace_source(pack.source_tag, pack.entries)
        except KnowledgeStoreError as exc:
            raise ValueError("knowledge pack database is unavailable") from exc
        try:
            atomic_write_json(registry_path, registry, ensure_ascii=False, indent=2)
        except Exception:
            restore_pack_storage(database_path, pack.pack_id, snapshot)
            raise
    return PackInstallResult(pack.pack_id, pack.collection_id, pack.source_tag, len(pack.entries))


def list_installed_packs(database_path: str | Path) -> tuple[dict[str, Any], ...]:
    packs = _load_registry(get_pack_registry_path(database_path)).get("packs", {})
    if not isinstance(packs, dict):
        return ()
    return tuple(
        {"pack_id": pack_id, **value}
        for pack_id, value in sorted(packs.items())
        if isinstance(value, dict)
    )


def set_pack_auto_context(database_path: str | Path, pack_id: str, *, enabled: bool) -> None:
    pack_id = _identifier(pack_id, "pack_id")
    registry_path = get_pack_registry_path(database_path)
    with mutation_lock(registry_path):
        registry = _load_registry(registry_path)
        packs = registry.get("packs")
        if not isinstance(packs, dict) or not isinstance(packs.get(pack_id), dict):
            raise ValueError("knowledge pack is not installed")
        packs[pack_id] = {**packs[pack_id], "auto_context": bool(enabled)}
        atomic_write_json(registry_path, registry, ensure_ascii=False, indent=2)


def remove_pack(database_path: str | Path, pack_id: str) -> int:
    """Remove one community source with local registry rollback."""
    pack_id = _identifier(pack_id, "pack_id")
    database_path = Path(database_path)
    registry_path = get_pack_registry_path(database_path)
    with mutation_lock(registry_path):
        snapshot = capture_pack_storage(database_path, pack_id)
        packs = snapshot.registry.get("packs")
        metadata = packs.get(pack_id) if isinstance(packs, dict) else None
        if not isinstance(metadata, dict):
            raise ValueError("knowledge pack is not installed")
        source_tag = str(metadata.get("source_tag") or "")
        if source_tag != f"source:community.{pack_id}":
            raise ValueError("only community packs can be removed")
        new_packs = dict(packs)
        new_packs.pop(pack_id, None)
        KnowledgeStore(database_path).replace_source(source_tag, ())
        try:
            atomic_write_json(
                registry_path,
                {"schema_version": 1, "packs": new_packs},
                ensure_ascii=False,
                indent=2,
            )
        except Exception:
            restore_pack_storage(database_path, pack_id, snapshot)
            raise
    return len(snapshot.entries)


def enabled_pack_source_tags(database_path: str | Path) -> tuple[str, ...]:
    return tuple(
        str(pack.get("source_tag"))
        for pack in list_installed_packs(database_path)
        if pack.get("auto_context") is True
        and str(pack.get("source_tag") or "").startswith("source:")
    )


def _entry_from_payload(payload: object, *, source_tag: str, index: int) -> KnowledgeEntry:
    path = f"entries[{index}]"
    if not isinstance(payload, dict):
        _fail(path, "type", "must be an object")
    _reject_unknown_keys(payload, {"title", "terms", "tags", "summary", "content"}, path)
    terms = payload.get("terms", {})
    if not isinstance(terms, dict):
        _fail(f"{path}.terms", "type", "must be an object")
    _reject_unknown_keys(terms, set(_TERM_ROLES), f"{path}.terms")
    normalized_terms: dict[str, tuple[str, ...]] = {}
    for role in _TERM_ROLES:
        values = terms.get(role, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            _fail(f"{path}.terms.{role}", "type", "must be a string array")
        if len(values) > MAX_TERMS_PER_ROLE:
            _fail(f"{path}.terms.{role}", "item_limit", "contains too many values")
        if any(len(value) > MAX_TERM_OR_TAG_CHARS for value in values):
            _fail(f"{path}.terms.{role}", "length_limit", "contains an overlong value")
        normalized_terms[role] = tuple(values)
    tags = payload.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        _fail(f"{path}.tags", "type", "must be a string array")
    if len(tags) > MAX_TAGS_PER_ENTRY:
        _fail(f"{path}.tags", "item_limit", "contains too many values")
    if any(len(tag) > MAX_TERM_OR_TAG_CHARS for tag in tags):
        _fail(f"{path}.tags", "length_limit", "contains an overlong value")
    if any(tag.startswith("source:") for tag in tags):
        _fail(f"{path}.tags", "reserved_tag", "cannot declare source tags")
    try:
        return KnowledgeEntry(
            title=_required_text(payload.get("title"), f"{path}.title", 500),
            terms=normalized_terms,
            tags=(source_tag, *tags),
            summary=_optional_text(payload.get("summary"), f"{path}.summary", 4_000),
            content=_required_text(payload.get("content"), f"{path}.content", 80_000),
        )
    except KnowledgePackValidationError:
        raise
    except ValueError as exc:
        _fail(path, "invalid_entry", str(exc))


def _identifier(value: object, path: str) -> str:
    try:
        return validate_knowledge_identifier(value)
    except ValueError as exc:
        _fail(path, "invalid_identifier", str(exc))


def _required_text(value: object, path: str, max_chars: int) -> str:
    text = _optional_text(value, path, max_chars)
    if not text:
        _fail(path, "required", "is required")
    return text


def _optional_text(value: object, path: str, max_chars: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        _fail(path, "type", "must be text")
    if len(value) > max_chars:
        _fail(path, "length_limit", "exceeds the length limit")
    return sanitize_external_text(value, max_chars=max_chars)


def _homepage(value: object) -> str:
    text = _optional_text(value, "source.homepage", 2_000)
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        _fail("source.homepage", "invalid_url", "must be an HTTP(S) URL")
    return text


def _reject_unknown_keys(payload: dict, allowed: set[str], path: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        suffix = f".{unknown[0]}" if path != "$" else unknown[0]
        issue_path = f"{path}{suffix}" if path != "$" else suffix
        _fail(issue_path, "unknown_field", "is not supported")


def _fail(path: str, code: str, message: str) -> NoReturn:
    raise KnowledgePackValidationError(
        KnowledgeValidationIssue("error", path, code, message)
    )


def _load_registry(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 1, "packs": {}}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning(
            "[public-knowledge] ignored invalid pack registry type=%s",
            type(exc).__name__,
        )
        return {"schema_version": 1, "packs": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("packs"), dict):
        logger.warning("[public-knowledge] ignored invalid pack registry structure")
        return {"schema_version": 1, "packs": {}}
    return payload


def _registry_with_pack(
    registry: dict[str, Any],
    pack: KnowledgePack,
    *,
    subscription: dict[str, str] | None = None,
) -> dict[str, Any]:
    packs = dict(registry.get("packs", {}))
    previous = packs.get(pack.pack_id, {})
    auto_context = previous.get("auto_context") is True if isinstance(previous, dict) else False
    previous_subscription = previous.get("subscription") if isinstance(previous, dict) else None
    packs[pack.pack_id] = {
        "collection_id": pack.collection_id,
        "source_tag": pack.source_tag,
        "source": {
            "name": pack.source.name,
            "homepage": pack.source.homepage,
            "license": pack.source.license,
        },
        "entries": len(pack.entries),
        "auto_context": auto_context,
        "subscription": subscription if subscription is not None else previous_subscription,
    }
    return {"schema_version": 1, "packs": packs}


def _validate_subscription_identity(previous: object, replacement: dict[str, str] | None) -> None:
    previous_is_subscription = isinstance(previous, dict)
    replacement_is_subscription = isinstance(replacement, dict)
    if previous_is_subscription != replacement_is_subscription:
        raise ValueError("knowledge pack subscription identity cannot change")
    if not previous_is_subscription:
        return
    for field in ("provider", "remote_id"):
        if str(previous.get(field) or "") != str(replacement.get(field) or ""):
            raise ValueError("knowledge pack subscription identity cannot change")
