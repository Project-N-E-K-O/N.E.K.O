"""Persistent, bounded staging jobs for user-supplied knowledge packs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from utils.file_utils import atomic_write_bytes, atomic_write_json

from ._mutation_lock import mutation_lock
from .limits import MAX_READY_VECTOR_CHUNKS
from .store import KnowledgeStore, KnowledgeStoreError
from .packs import (
    KnowledgePack,
    ensure_install_capacity,
    install_pack,
    pack_identity_sha256,
    pack_payload,
    preflight_pack,
    validate_pack,
    validate_pack_subscription,
)
from .subscriptions import canonical_pack_bytes, validate_subscription


STAGING_DIRECTORY = ".staging"
MAX_COMMUNITY_ENTRIES = 20_000
MAX_COMMUNITY_CHUNKS = 20_000
MAX_COMMUNITY_CONTENT_BYTES = 64 * 1024 * 1024
TERMINAL_STATES = frozenset(("active", "cancelled", "failed"))
DEGRADED_STATE = "degraded"
TERMINAL_JOB_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_TERMINAL_JOB_DIRECTORIES = 100
# Staging metadata is a few KB in practice; this only has to exclude absurdity.
MAX_STAGED_METADATA_BYTES = 1024 * 1024
PACK_ARTIFACT_NAME = "pack.neko-knowledge.json"
LEGACY_PACK_ARTIFACT_NAME = "pack.json"
INDEX_MANIFEST_NAME = "pack.neko-knowledge.index.json"
VECTOR_ARTIFACT_NAME = "pack.neko-knowledge.vectors.f16"
IDENTITY_NAME = "identity.json"
ACTIVATION_NAME = "activation.json"
ACTIVATION_COMMITS_NAME = "activation-commits.json"
STAGING_DATABASE_NAMES = (
    "knowledge.db",
    "knowledge.db-wal",
    "knowledge.db-shm",
    "knowledge.db-journal",
)
IDENTITY_CAPACITY_FIELDS = ("entries_total", "chunks_total", "content_bytes")
IDENTITY_SUBSCRIPTION_FIELDS = ("has_subscription", "subscription_sha256")
IDENTITY_PACK_FIELDS = ("pack_sha256",)
JOB_STATES = frozenset(
    (
        "queued",
        "validating",
        "building_fts",
        "verifying_index",
        "embedding",
        "active",
        "cancelled",
        "failed",
        DEGRADED_STATE,
    )
)
_JOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}-[0-9a-f]{12}$")
_CREATING_JOB_ID_RE = re.compile(r"^\.creating-[0-9a-f]{32}$")
_JOB_SUFFIX_RE = re.compile(r"^[0-9a-f]{12}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
logger = logging.getLogger(__name__)


class KnowledgeJobRegistryError(ValueError):
    """Raised when staging state cannot be trusted for capacity decisions."""


@dataclass(frozen=True, slots=True)
class _JsonReadResult:
    state: Literal["valid", "missing", "invalid", "unreadable"]
    payload: dict[str, Any]


def _jobs_root(knowledge_root: str | Path) -> Path:
    return Path(knowledge_root) / STAGING_DIRECTORY


def _staged_artifact_limits() -> dict[str, int]:
    from .limits import (
        MAX_PACK_BYTES,
        MAX_PREBUILT_MANIFEST_BYTES,
        MAX_PREBUILT_VECTOR_BYTES,
    )

    return {
        PACK_ARTIFACT_NAME: MAX_PACK_BYTES,
        LEGACY_PACK_ARTIFACT_NAME: MAX_PACK_BYTES,
        INDEX_MANIFEST_NAME: MAX_PREBUILT_MANIFEST_BYTES,
        VECTOR_ARTIFACT_NAME: MAX_PREBUILT_VECTOR_BYTES,
    }


def _read_bounded_staged_file(path: Path, *, max_bytes: int) -> bytes:
    """Open once, validate on that descriptor, and read from the same descriptor.

    Validating a path and then re-opening it by name leaves a window in which a
    concurrent writer can swap the file, defeating both the link refusal and the
    size cap. O_NOFOLLOW makes the refusal atomic where the platform has it; on
    Windows it does not exist, so the pre-open lstat carries that half there and
    the size cap stays atomic because it is checked against the descriptor that
    is actually read.

    Raises ValueError for a rejected file so callers can tell "we refuse to read
    this" apart from a genuine I/O error.
    """
    # Not _is_link_or_reparse(): that folds "does not exist" into "is a link",
    # which would turn a missing state.json from `missing` into `invalid` and
    # change how the job state machine treats it. Absence must stay absence.
    try:
        link_metadata = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ValueError("staged file is unavailable") from exc
    file_attributes = getattr(link_metadata, "st_file_attributes", 0)
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if stat.S_ISLNK(link_metadata.st_mode) or bool(
        file_attributes & reparse_attribute
    ):
        raise ValueError("staged file is not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("staged file is not a regular file")
        if metadata.st_size > max_bytes:
            raise ValueError("staged file exceeds its size limit")
        # Read at most max_bytes + 1 so a file that grew after fstat is rejected
        # instead of being materialized in full.
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if len(raw) > max_bytes:
        raise ValueError("staged file exceeds its size limit")
    return raw


def _read_staged_artifact(job_dir: Path, name: str) -> bytes:
    """Read one fixed staged artifact through a single validated descriptor.

    Every path *leading to* staging is link-guarded, but the artifacts themselves
    were not: they were read with ``is_file()`` + ``read_bytes()``, which follows
    a symlink and materializes whatever it finds before the digest, schema or
    protocol limit is ever checked.

    Validating the path and then calling ``read_bytes()`` is not enough either —
    that re-opens by name, so a concurrent writer can swap the artifact in
    between and slip past both checks. Everything below therefore happens on one
    descriptor: open, ``fstat`` it, and read from that same descriptor.
    """
    return _read_bounded_staged_file(
        job_dir / name, max_bytes=_staged_artifact_limits()[name]
    )


def _staged_artifact_present(job_dir: Path, name: str) -> bool:
    """Report presence without following a link (a link is present but invalid)."""
    try:
        (job_dir / name).lstat()
    except OSError:
        return False
    return True


def _is_link_or_reparse(path: Path) -> bool:
    """Return whether ``path`` redirects filesystem access elsewhere."""
    try:
        metadata = path.lstat()
    except OSError:
        return True
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(file_attributes & reparse_attribute)


def _validated_jobs_root(knowledge_root: str | Path) -> Path | None:
    """Return the staging root only when it is a real child of knowledge root.

    A missing staging root is valid for read-only callers and may only be
    created by ``stage_pack`` after it has acquired the trusted registry lock.
    """
    knowledge_root = Path(knowledge_root)
    if _is_link_or_reparse(knowledge_root) or not knowledge_root.is_dir():
        return None
    jobs_root = _jobs_root(knowledge_root)
    try:
        resolved_knowledge_root = knowledge_root.resolve(strict=True)
    except OSError:
        return None
    if not resolved_knowledge_root.is_dir():
        return None
    try:
        jobs_root.lstat()
    except FileNotFoundError:
        return jobs_root
    except OSError:
        return None
    if _is_link_or_reparse(jobs_root) or not jobs_root.is_dir():
        return None
    try:
        resolved_jobs_root = jobs_root.resolve(strict=True)
    except OSError:
        return None
    if resolved_jobs_root.parent != resolved_knowledge_root:
        return None
    return jobs_root


def trusted_live_root(knowledge_root: str | Path) -> Path | None:
    """Validate the configured root before any live database/registry mutation.

    Read paths tolerate a redirected root — they only ever surface what the user
    pointed at. A destructive mutation must not: following a symlink or junction
    into an external store would delete rows from a database the user never
    configured. Staging already refuses a linked root; this is the same guard for
    the live side, which reaches ``knowledge.db`` / ``packs.json`` directly.
    """
    root = Path(knowledge_root)
    if _is_link_or_reparse(root) or not root.is_dir():
        return None
    try:
        resolved = root.resolve(strict=True)
    except OSError:
        return None
    # resolve() was previously called only to prove the path exists — its result
    # was discarded, so a redirected *ancestor* passed: the leaf is not a link,
    # yet the whole subtree lives somewhere else. Compare against the normalized
    # absolute path so an ancestor symlink/junction is refused too. os.path
    # normalization (not casefold) keeps this correct on case-insensitive
    # volumes without matching two genuinely different directories.
    try:
        declared = Path(os.path.normpath(os.path.abspath(root)))
    except OSError:
        return None
    if os.path.normcase(str(resolved)) != os.path.normcase(str(declared)):
        return None
    return root


def _create_trusted_knowledge_root(knowledge_root: str | Path) -> Path | None:
    """Create the configured write root without accepting a redirected path."""
    root = Path(knowledge_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    if _is_link_or_reparse(root) or not root.is_dir():
        return None
    try:
        root.resolve(strict=True)
    except OSError:
        return None
    return root


def _jobs_registry_lock(knowledge_root: str | Path):
    """Lock staging mutations without placing a lock below untrusted staging."""
    return mutation_lock(Path(knowledge_root) / "knowledge-job-registry")


def _state_path(job_dir: Path) -> Path:
    return job_dir / "state.json"


def _identity_path(job_dir: Path) -> Path:
    return job_dir / IDENTITY_NAME


def _activation_path(job_dir: Path) -> Path:
    return job_dir / ACTIVATION_NAME


def _activation_commits_path(knowledge_root: str | Path) -> Path | None:
    root = Path(knowledge_root)
    path = root / ACTIVATION_COMMITS_NAME
    try:
        if root.resolve() != path.parent.resolve():
            return None
    except OSError:
        return None
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        return None
    else:
        if _is_link_or_reparse(path):
            return None
    return path


def _validated_staging_database_path(
    job_dir: Path,
    *,
    require_database: bool = False,
) -> Path | None:
    """Return a staging database only when its complete file family is local."""
    safe_job_dir = _revalidated_job_dir(job_dir)
    if safe_job_dir is None:
        return None
    try:
        resolved_job_dir = safe_job_dir.resolve(strict=True)
    except OSError:
        return None
    database_exists = False
    for name in STAGING_DATABASE_NAMES:
        path = safe_job_dir / name
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return None
        if (
            _is_link_or_reparse(path)
            or not stat.S_ISREG(metadata.st_mode)
        ):
            return None
        try:
            if path.resolve(strict=True).parent != resolved_job_dir:
                return None
        except OSError:
            return None
        if name == "knowledge.db":
            database_exists = True
    if require_database and not database_exists:
        return None
    return safe_job_dir / "knowledge.db"


def _external_staging_child(
    knowledge_root: str | Path,
    name: str,
    *,
    require_existing: bool = False,
) -> Path | None:
    jobs_root = _validated_jobs_root(knowledge_root)
    if jobs_root is None:
        return None
    job_dir = jobs_root / name
    if not require_existing:
        return job_dir
    try:
        resolved_root = jobs_root.resolve(strict=True)
        resolved_job = job_dir.resolve(strict=True)
    except OSError:
        return None
    if _is_link_or_reparse(job_dir) or resolved_job.parent != resolved_root:
        return None
    return job_dir


def _external_job_dir(
    knowledge_root: str | Path,
    job_id: str,
    *,
    require_existing: bool = False,
) -> Path | None:
    """Resolve one generated job ID without permitting path traversal or links."""
    job_id = str(job_id)
    if not _JOB_ID_RE.fullmatch(job_id):
        return None
    return _external_staging_child(
        knowledge_root,
        job_id,
        require_existing=require_existing,
    )


def _external_discardable_job_dir(
    knowledge_root: str | Path,
    job_id: str,
    *,
    require_existing: bool = False,
) -> Path | None:
    """Resolve a published job or one crashed atomic-creation directory."""
    job_id = str(job_id)
    if not (_JOB_ID_RE.fullmatch(job_id) or _CREATING_JOB_ID_RE.fullmatch(job_id)):
        return None
    return _external_staging_child(
        knowledge_root,
        job_id,
        require_existing=require_existing,
    )


def _revalidated_job_dir(job_dir: Path) -> Path | None:
    """Re-resolve an internal job path through the public staging boundary."""
    if job_dir.parent.name != STAGING_DIRECTORY:
        return None
    return _external_job_dir(
        job_dir.parent.parent,
        job_dir.name,
        require_existing=True,
    )


def pack_operation_lock(knowledge_root: str | Path, pack_id: str):
    """Serialize staging, activation, and removal for one pack identity."""
    digest = hashlib.sha256(str(pack_id).encode("utf-8")).hexdigest()
    return mutation_lock(
        Path(knowledge_root) / f"knowledge-pack-operation-{digest}"
    )


def _pack_payload(pack: KnowledgePack) -> dict[str, object]:
    """Compatibility wrapper for older internal callers and tests."""
    return pack_payload(pack)


def _read_json_result(path: Path) -> _JsonReadResult:
    # Staging metadata (state/identity/activation/subscription/commits) decides
    # how the job state machine proceeds and is just as attacker-mutable as the
    # pack artifact, so it gets the same bounded, link-checked read. A rejected
    # file is deterministic — retrying cannot help — so it maps to "invalid"
    # rather than the transient "unreadable".
    for attempt in range(3):
        try:
            text = _read_bounded_staged_file(
                path, max_bytes=MAX_STAGED_METADATA_BYTES
            ).decode("utf-8")
            break
        except FileNotFoundError:
            return _JsonReadResult("missing", {})
        except ValueError:
            return _JsonReadResult("invalid", {})
        except OSError:
            if attempt == 2:
                return _JsonReadResult("unreadable", {})
            time.sleep(0.01)
        except UnicodeError:
            return _JsonReadResult("invalid", {})
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _JsonReadResult("invalid", {})
    if not isinstance(payload, dict):
        return _JsonReadResult("invalid", {})
    return _JsonReadResult("valid", payload)


def _read_json(path: Path) -> dict[str, Any]:
    return _read_json_result(path).payload


def _validated_identity_payload(
    job_dir: Path,
    payload: dict[str, Any],
) -> _JsonReadResult:
    job_id = str(payload.get("job_id") or "")
    pack_id = str(payload.get("pack_id") or "")
    identity_values = {
        "created_at": _normalized_job_timestamp(payload.get("created_at")),
        **{
            key: _normalized_nonnegative_int(payload.get(key))
            for key in IDENTITY_CAPACITY_FIELDS
        },
    }
    has_subscription = payload.get("has_subscription")
    subscription_sha256 = payload.get("subscription_sha256")
    pack_sha256 = payload.get("pack_sha256")
    subscription_identity_valid = (
        isinstance(has_subscription, bool)
        and (
            isinstance(subscription_sha256, str)
            and bool(_SHA256_RE.fullmatch(subscription_sha256))
            if has_subscription
            else subscription_sha256 == ""
        )
    )
    job_suffix = (
        job_id[len(pack_id) + 1 :]
        if pack_id and job_id.startswith(f"{pack_id}-")
        else ""
    )
    if (
        any(value is None for value in identity_values.values())
        or not subscription_identity_valid
        or not isinstance(pack_sha256, str)
        or not _SHA256_RE.fullmatch(pack_sha256)
    ):
        return _JsonReadResult("invalid", {})
    if (
        job_id != job_dir.name
        or Path(job_id).name != job_id
        or not _JOB_ID_RE.fullmatch(job_id)
        or not pack_id
        or Path(pack_id).name != pack_id
        or not _JOB_SUFFIX_RE.fullmatch(job_suffix)
    ):
        return _JsonReadResult("invalid", {})
    return _JsonReadResult(
        "valid",
        {
            "job_id": job_id,
            "pack_id": pack_id,
            **identity_values,
            "pack_sha256": pack_sha256,
            "has_subscription": has_subscription,
            "subscription_sha256": subscription_sha256,
        },
    )


def _validated_identity(job_dir: Path) -> _JsonReadResult:
    result = _read_json_result(_identity_path(job_dir))
    if result.state != "valid":
        return result
    return _validated_identity_payload(job_dir, result.payload)


def _validated_activation(
    job_dir: Path,
    identity: dict[str, Any],
) -> _JsonReadResult:
    result = _read_json_result(_activation_path(job_dir))
    if result.state != "valid":
        return result
    retrieval_mode = result.payload.get("retrieval_mode")
    expected = {
        "schema_version": 1,
        "job_id": identity["job_id"],
        "pack_id": identity["pack_id"],
        "pack_sha256": identity["pack_sha256"],
        "has_subscription": identity["has_subscription"],
        "subscription_sha256": identity["subscription_sha256"],
        "retrieval_mode": retrieval_mode,
    }
    if (
        not isinstance(retrieval_mode, str)
        or retrieval_mode not in {"bm25", "hybrid"}
        or result.payload != expected
    ):
        return _JsonReadResult("invalid", {})
    commits_result = _validated_activation_commits(job_dir.parent.parent)
    if commits_result.state != "valid":
        return _JsonReadResult("invalid", {})
    commit = commits_result.payload["commits"].get(identity["job_id"])
    if not isinstance(commit, dict):
        return _JsonReadResult("invalid", {})
    committed_at = _normalized_job_timestamp(commit.get("committed_at"))
    if committed_at is None or commit != {**expected, "committed_at": committed_at}:
        return _JsonReadResult("invalid", {})
    return _JsonReadResult("valid", expected)


def _validated_activation_commits(
    knowledge_root: str | Path,
) -> _JsonReadResult:
    path = _activation_commits_path(knowledge_root)
    if path is None:
        return _JsonReadResult("invalid", {})
    result = _read_json_result(path)
    if result.state == "missing":
        return _JsonReadResult(
            "valid",
            {"schema_version": 1, "commits": {}},
        )
    if result.state != "valid":
        return result
    if set(result.payload) != {"schema_version", "commits"}:
        return _JsonReadResult("invalid", {})
    commits = result.payload.get("commits")
    if (
        type(result.payload.get("schema_version")) is not int
        or result.payload.get("schema_version") != 1
        or not isinstance(commits, dict)
    ):
        return _JsonReadResult("invalid", {})
    normalized: dict[str, dict[str, object]] = {}
    expected_fields = {
        "schema_version",
        "job_id",
        "pack_id",
        "pack_sha256",
        "has_subscription",
        "subscription_sha256",
        "retrieval_mode",
        "committed_at",
    }
    for job_id, commit in commits.items():
        if (
            not isinstance(job_id, str)
            or not _JOB_ID_RE.fullmatch(job_id)
            or not isinstance(commit, dict)
            or set(commit) != expected_fields
            or type(commit.get("schema_version")) is not int
            or commit.get("schema_version") != 1
            or commit.get("job_id") != job_id
            or not isinstance(commit.get("pack_id"), str)
            or not isinstance(commit.get("pack_sha256"), str)
            or not _SHA256_RE.fullmatch(commit["pack_sha256"])
            or not isinstance(commit.get("has_subscription"), bool)
            or not isinstance(commit.get("subscription_sha256"), str)
            or not isinstance(commit.get("retrieval_mode"), str)
            or commit.get("retrieval_mode") not in {"bm25", "hybrid"}
        ):
            return _JsonReadResult("invalid", {})
        has_subscription = commit["has_subscription"]
        subscription_sha256 = commit["subscription_sha256"]
        if has_subscription:
            if not _SHA256_RE.fullmatch(subscription_sha256):
                return _JsonReadResult("invalid", {})
        elif subscription_sha256 != "":
            return _JsonReadResult("invalid", {})
        committed_at = _normalized_job_timestamp(commit.get("committed_at"))
        if committed_at is None:
            return _JsonReadResult("invalid", {})
        normalized[job_id] = {**commit, "committed_at": committed_at}
    return _JsonReadResult(
        "valid",
        {"schema_version": 1, "commits": normalized},
    )


def _record_activation_commit(
    knowledge_root: str | Path,
    activation: dict[str, object],
) -> None:
    path = _activation_commits_path(knowledge_root)
    if path is None:
        raise KnowledgeJobRegistryError("knowledge_activation_registry_invalid")
    with mutation_lock(path):
        result = _validated_activation_commits(knowledge_root)
        if result.state != "valid":
            raise KnowledgeJobRegistryError("knowledge_activation_registry_invalid")
        commits = dict(result.payload["commits"])
        committed_at = int(time.time())
        commits[str(activation["job_id"])] = {
            **activation,
            "committed_at": committed_at,
        }
        current_job_id = str(activation["job_id"])
        retained_history = sorted(
            (
                item
                for item in commits.items()
                if item[0] != current_job_id
            ),
            key=lambda item: (int(item[1]["committed_at"]), item[0]),
        )[-(MAX_TERMINAL_JOB_DIRECTORIES - 1):]
        ordered = sorted(
            (*retained_history, (current_job_id, commits[current_job_id])),
            key=lambda item: (int(item[1]["committed_at"]), item[0]),
        )
        atomic_write_json(
            path,
            {"schema_version": 1, "commits": dict(ordered)},
            ensure_ascii=False,
            indent=2,
        )


def _degraded_job(
    job_dir: Path,
    *,
    reason: str,
    identity: dict[str, Any] | None = None,
    orphan: bool = False,
) -> dict[str, object]:
    try:
        created_at = int(job_dir.stat().st_mtime)
    except OSError:
        created_at = 0
    trusted_created_at = (
        int(identity["created_at"])
        if identity is not None and "created_at" in identity
        else created_at
    )
    return {
        **(identity or {}),
        "job_id": str((identity or {}).get("job_id") or job_dir.name),
        "state": DEGRADED_STATE,
        "retrieval_mode": "none",
        "reason": reason,
        "created_at": trusted_created_at,
        "updated_at": created_at,
        "orphan": bool(orphan),
    }


def _normalized_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    return None


def _normalized_job_timestamp(value: object) -> int | None:
    return _normalized_nonnegative_int(value)


def _read_job(job_dir: Path) -> dict[str, object]:
    if job_dir.name.startswith(".creating-"):
        return _degraded_job(
            job_dir,
            reason="incomplete_job_creation",
            orphan=True,
        )
    state_result = _read_json_result(_state_path(job_dir))
    identity_result = _validated_identity(job_dir)
    if identity_result.state != "valid":
        return _degraded_job(
            job_dir,
            reason="invalid_job_identity",
            orphan=True,
        )
    identity = identity_result.payload
    if state_result.state == "valid":
        state = state_result.payload
        job_state = state.get("state")
        if not isinstance(job_state, str) or job_state not in JOB_STATES:
            return _degraded_job(
                job_dir,
                reason="invalid_job_state",
                identity=identity,
            )
        if any(
            str(state.get(key) or "") != str(identity.get(key) or "")
            for key in ("job_id", "pack_id")
        ):
            return _degraded_job(
                job_dir,
                reason="job_identity_mismatch",
                identity=identity,
            )
        for key in IDENTITY_CAPACITY_FIELDS:
            if key not in state:
                continue
            state_value = _normalized_nonnegative_int(state.get(key))
            if state_value != identity[key]:
                return _degraded_job(
                    job_dir,
                    reason="job_capacity_identity_mismatch",
                    identity=identity,
                )
        for key in IDENTITY_SUBSCRIPTION_FIELDS:
            if key in state and state.get(key) != identity[key]:
                return _degraded_job(
                    job_dir,
                    reason="job_subscription_identity_mismatch",
                    identity=identity,
                )
        for key in IDENTITY_PACK_FIELDS:
            if key in state and state.get(key) != identity[key]:
                return _degraded_job(
                    job_dir,
                    reason="job_pack_identity_mismatch",
                    identity=identity,
                )
        if "created_at" not in state:
            created_at = int(identity["created_at"])
        else:
            created_at = _normalized_job_timestamp(state.get("created_at"))
            if created_at is None or created_at != identity["created_at"]:
                return _degraded_job(
                    job_dir,
                    reason="invalid_job_timestamps",
                    identity=identity,
                )
        updated_at = _normalized_job_timestamp(
            state.get("updated_at", created_at)
        )
        if updated_at is None:
            return _degraded_job(
                job_dir,
                reason="invalid_job_timestamps",
                identity=identity,
            )
        if job_state == "active":
            activation = _validated_activation(job_dir, identity)
            if (
                activation.state != "valid"
                or state.get("retrieval_mode")
                != activation.payload["retrieval_mode"]
            ):
                return _degraded_job(
                    job_dir,
                    reason="active_job_commit_unverified",
                    identity=identity,
                )
        return {**state, **identity, "updated_at": updated_at}
    reason = {
        "missing": "missing_job_state",
        "invalid": "invalid_job_state",
        "unreadable": "unreadable_job_state",
    }[state_result.state]
    return _degraded_job(
        job_dir,
        reason=reason,
        identity=identity,
    )


def _write_state(
    job_dir: Path,
    current: dict[str, Any],
    **changes: object,
) -> dict[str, Any]:
    updated = {**current, **changes, "updated_at": int(time.time())}
    persisted = {
        key: value
        for key, value in updated.items()
        if key
        not in (
            IDENTITY_CAPACITY_FIELDS
            + IDENTITY_SUBSCRIPTION_FIELDS
            + IDENTITY_PACK_FIELDS
        )
    }
    atomic_write_json(_state_path(job_dir), persisted, ensure_ascii=False, indent=2)
    return updated


async def _write_state_async(
    job_dir: Path,
    **changes: object,
) -> dict[str, Any]:
    """Update the latest non-terminal state off the coordinator event loop."""
    return await asyncio.to_thread(_update_state_locked, job_dir, **changes)


def _update_state_locked(job_dir: Path, **changes: object) -> dict[str, Any]:
    """Apply changes to a fresh state snapshot without reviving a terminal job."""
    safe_job_dir = _revalidated_job_dir(job_dir)
    if safe_job_dir is None:
        return {}
    knowledge_root = safe_job_dir.parent.parent
    with _jobs_registry_lock(knowledge_root):
        safe_job_dir = _revalidated_job_dir(safe_job_dir)
        if safe_job_dir is None:
            return {}
        with mutation_lock(_state_path(safe_job_dir)):
            current = _read_job(safe_job_dir)
            if (
                not current
                or current.get("state") in TERMINAL_STATES
                or current.get("state") == DEGRADED_STATE
            ):
                return current
            return _write_state(safe_job_dir, current, **changes)


def stage_pack(
    service,
    pack: KnowledgePack,
    *,
    subscription: dict[str, str] | None = None,
    index_manifest: bytes | None = None,
    vectors: bytes | None = None,
    index_fallback_reason: str = "",
) -> dict[str, object]:
    """Persist validated source data without making it searchable yet."""
    root = Path(service.knowledge_root)
    preflight = preflight_pack(pack)
    canonical_subscription = _canonical_staged_subscription(
        subscription,
        pack=pack,
    )
    ensure_install_capacity(root, preflight)
    root = _create_trusted_knowledge_root(root)
    if root is None:
        raise KnowledgeJobRegistryError("knowledge_job_registry_path_invalid")
    jobs_root = _validated_jobs_root(root)
    if jobs_root is None:
        raise KnowledgeJobRegistryError("knowledge_job_registry_path_invalid")
    with pack_operation_lock(root, pack.pack_id), _jobs_registry_lock(root):
        jobs_root = _validated_jobs_root(root)
        if jobs_root is None:
            raise KnowledgeJobRegistryError("knowledge_job_registry_path_invalid")
        jobs_root.mkdir(parents=False, exist_ok=True)
        jobs_root = _validated_jobs_root(root)
        if jobs_root is None:
            raise KnowledgeJobRegistryError("knowledge_job_registry_path_invalid")
        _ensure_community_capacity(service, pack, preflight)
        job_id = f"{pack.pack_id}-{uuid.uuid4().hex[:12]}"
        job_dir = jobs_root / job_id
        creating_dir = jobs_root / f".creating-{uuid.uuid4().hex}"
        creating_dir.mkdir()
        now = int(time.time())
        has_prebuilt = index_manifest is not None and vectors is not None
        subscription_sha256 = (
            hashlib.sha256(canonical_pack_bytes(canonical_subscription)).hexdigest()
            if canonical_subscription is not None
            else ""
        )
        identity: dict[str, object] = {
            "job_id": job_id,
            "pack_id": pack.pack_id,
            "created_at": now,
            "entries_total": preflight.entries,
            "chunks_total": preflight.projected_chunks,
            "content_bytes": preflight.content_bytes,
            "pack_sha256": pack_identity_sha256(pack),
            "has_subscription": canonical_subscription is not None,
            "subscription_sha256": subscription_sha256,
        }
        state: dict[str, object] = {
            "job_id": job_id,
            "pack_id": pack.pack_id,
            "material_type": pack.material_type,
            "state": "queued",
            "retrieval_mode": "pending",
            "chunks_ready": 0,
            "indexed_percent": 0.0,
            "reason": "",
            "index_origin": "prebuilt" if has_prebuilt else "none",
            "index_trust": "trusted_market" if has_prebuilt else "none",
            "index_validation": "pending" if has_prebuilt else "absent",
            "index_fallback_reason": str(index_fallback_reason or "")[:80],
            "local_embedding_enabled": False,
            "prebuilt_chunks_ready": 0,
            "prebuilt_chunks_missing": preflight.projected_chunks,
            "created_at": now,
            "updated_at": now,
        }
        try:
            atomic_write_json(
                _identity_path(creating_dir),
                identity,
                ensure_ascii=False,
                indent=2,
            )
            atomic_write_bytes(
                creating_dir / PACK_ARTIFACT_NAME,
                canonical_pack_bytes(_pack_payload(pack)),
            )
            if has_prebuilt:
                atomic_write_bytes(creating_dir / INDEX_MANIFEST_NAME, index_manifest)
                atomic_write_bytes(creating_dir / VECTOR_ARTIFACT_NAME, vectors)
            if canonical_subscription is not None:
                atomic_write_json(
                    creating_dir / "subscription.json",
                    canonical_subscription,
                    ensure_ascii=False,
                )
            atomic_write_json(
                _state_path(creating_dir), state, ensure_ascii=False, indent=2
            )
            creating_dir.replace(job_dir)
        except Exception:
            shutil.rmtree(creating_dir, ignore_errors=True)
            raise
    from .indexer import notify_knowledge_index_changed

    notify_knowledge_index_changed()
    return {**state, **identity}


def _ensure_community_capacity(service, pack: KnowledgePack, preflight) -> None:
    all_jobs = list_pack_jobs(service.knowledge_root)
    if any(job.get("orphan") is True for job in all_jobs):
        raise KnowledgeJobRegistryError("knowledge_job_registry_invalid")
    pending = [job for job in all_jobs if job.get("state") not in TERMINAL_STATES]
    if any(
        job.get("pack_id") == pack.pack_id
        for job in pending
    ):
        raise ValueError("knowledge pack already has a pending import")

    totals = {"entries_total": 0, "chunks_total": 0, "content_bytes": 0}
    database_path = service.database_path()
    store = KnowledgeStore(database_path)
    if database_path.is_file():
        try:
            usage = store.community_usage(strict=True)
        except KnowledgeStoreError as exc:
            raise KnowledgeJobRegistryError(
                "knowledge_capacity_unavailable"
            ) from exc
        for key in totals:
            totals[key] += int(usage[key])

    replacement_keys = {str(job.get("pack_id") or "") for job in pending}
    replacement_keys.add(pack.pack_id)
    replacement = {"entries_total": 0, "chunks_total": 0, "content_bytes": 0}
    for pack_id in replacement_keys:
        if not pack_id:
            continue
        try:
            usage = store.community_usage(
                source_tag=f"source:community.{pack_id}",
                strict=True,
            )
        except KnowledgeStoreError as exc:
            raise KnowledgeJobRegistryError(
                "knowledge_capacity_unavailable"
            ) from exc
        for key in replacement:
            replacement[key] += int(usage[key])

    entries = (
        totals["entries_total"]
        - replacement["entries_total"]
        + sum(int(job.get("entries_total") or 0) for job in pending)
        + preflight.entries
    )
    chunks = (
        totals["chunks_total"]
        - replacement["chunks_total"]
        + sum(int(job.get("chunks_total") or 0) for job in pending)
        + preflight.projected_chunks
    )
    content_bytes = (
        totals["content_bytes"]
        - replacement["content_bytes"]
        + sum(int(job.get("content_bytes") or 0) for job in pending)
        + preflight.content_bytes
    )
    if entries > MAX_COMMUNITY_ENTRIES:
        raise ValueError("community knowledge contains too many entries")
    if chunks > MAX_COMMUNITY_CHUNKS:
        raise ValueError("community knowledge would create too many chunks")
    if content_bytes > MAX_COMMUNITY_CONTENT_BYTES:
        raise ValueError("community knowledge exceeds the total content limit")


def list_pack_jobs(
    knowledge_root: str | Path,
) -> tuple[dict[str, object], ...]:
    jobs_root = _validated_jobs_root(knowledge_root)
    if jobs_root is None:
        return ()
    with _jobs_registry_lock(knowledge_root):
        jobs_root = _validated_jobs_root(knowledge_root)
        if jobs_root is None:
            return ()
        if not jobs_root.is_dir():
            return ()
        job_dirs = tuple(
            job_dir
            for job_dir in jobs_root.iterdir()
            if job_dir.is_dir() and not _is_link_or_reparse(job_dir)
        )
        items = [_read_job(job_dir) for job_dir in job_dirs]
        _prune_terminal_jobs(jobs_root, job_dirs, items)
        items = [
            item
            for job_dir, item in zip(job_dirs, items, strict=True)
            if job_dir.is_dir()
        ]
    return tuple(
        sorted(
            items,
            key=lambda item: (
                -int(item.get("created_at") or 0),
                str(item.get("job_id") or ""),
            ),
        )
    )


def _prune_terminal_jobs(
    jobs_root: Path,
    job_dirs: tuple[Path, ...],
    items: list[dict[str, object]],
) -> None:
    now = int(time.time())
    candidates: list[tuple[int, Path]] = []
    for job_dir, item in zip(job_dirs, items, strict=True):
        job_id = str(item.get("job_id") or "")
        if (
            item.get("state") not in TERMINAL_STATES
            or not job_id
            or job_id != job_dir.name
            or Path(job_id).name != job_id
        ):
            continue
        updated_at = int(item.get("updated_at") or item.get("created_at") or 0)
        candidates.append((updated_at, job_dir))
    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1].name))
    for index, (updated_at, job_dir) in enumerate(candidates):
        expired = updated_at > 0 and now - updated_at > TERMINAL_JOB_TTL_SECONDS
        over_count = index >= MAX_TERMINAL_JOB_DIRECTORIES
        if not expired and not over_count:
            continue
        if job_dir.is_symlink() or job_dir.parent.resolve() != jobs_root.resolve():
            continue
        try:
            shutil.rmtree(job_dir)
        except OSError:
            logger.warning("failed to prune terminal knowledge job %s", job_dir.name)


def cancel_pack_job(knowledge_root: str | Path, job_id: str) -> bool:
    job_dir = _external_job_dir(knowledge_root, job_id, require_existing=True)
    if job_dir is None:
        return False
    with _jobs_registry_lock(knowledge_root):
        job_dir = _external_job_dir(knowledge_root, job_id, require_existing=True)
        if job_dir is None:
            return False
        with mutation_lock(_state_path(job_dir)):
            state = _read_job(job_dir)
            if (
                not state
                or state.get("state") in TERMINAL_STATES
                or state.get("state") == DEGRADED_STATE
            ):
                return False
            _write_state(
                job_dir,
                state,
                state="cancelled",
                retrieval_mode="none",
                reason="cancelled_by_user",
            )
            if state.get("state") != "embedding":
                _cleanup_payload(job_dir)
    return True


def discard_degraded_pack_job(knowledge_root: str | Path, job_id: str) -> bool:
    """Explicitly remove one quarantined job after validating its exact path."""
    jobs_root = _validated_jobs_root(knowledge_root)
    if jobs_root is None:
        return False
    with _jobs_registry_lock(knowledge_root):
        jobs_root = _validated_jobs_root(knowledge_root)
        if jobs_root is None:
            return False
        job_dir = _external_discardable_job_dir(
            knowledge_root,
            job_id,
            require_existing=True,
        )
        if job_dir is None:
            return False
        if not job_dir.is_dir() or _read_job(job_dir).get("state") != DEGRADED_STATE:
            return False
        shutil.rmtree(job_dir)
    return True


def _load_job_pack(job_dir: Path) -> KnowledgePack:
    # Both the canonical artifact and the legacy fallback are attacker-mutable
    # staging files, so each goes through the same bounded, link-checked read.
    for name in (PACK_ARTIFACT_NAME, LEGACY_PACK_ARTIFACT_NAME):
        if not _staged_artifact_present(job_dir, name):
            continue
        raw = _read_staged_artifact(job_dir, name)
        try:
            return validate_pack(json.loads(raw.decode("utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("staged knowledge pack is invalid") from exc
    raise ValueError("staged knowledge pack is missing")


def _load_capacity_validated_job_pack(
    job_dir: Path,
    identity: dict[str, Any],
) -> KnowledgePack | None:
    """Reload staged source and prove it still matches immutable admission data."""
    try:
        pack = _load_job_pack(job_dir)
        preflight = preflight_pack(pack)
    except (OSError, UnicodeError, ValueError):
        return None
    actual_capacity = {
        "entries_total": preflight.entries,
        "chunks_total": preflight.projected_chunks,
        "content_bytes": preflight.content_bytes,
    }
    if pack.pack_id != str(identity.get("pack_id") or ""):
        return None
    if pack_identity_sha256(pack) != identity.get("pack_sha256"):
        return None
    if any(
        actual_capacity[field] != identity.get(field)
        for field in IDENTITY_CAPACITY_FIELDS
    ):
        return None
    return pack


def _canonical_staged_subscription(
    payload: object,
    *,
    pack: KnowledgePack | None = None,
) -> dict[str, str] | None:
    if payload is None:
        return None
    canonical = (
        validate_pack_subscription(pack, payload)
        if pack is not None
        else validate_subscription(payload).to_dict()
    )
    if (
        canonical["provider"] == "plugin-market"
        and not canonical.get("provider_package_id")
    ):
        raise ValueError("plugin-market subscription requires provider_package_id")
    return canonical


def _identity_validated_subscription(
    job_dir: Path,
    identity: dict[str, Any],
) -> dict[str, str] | None:
    result = _read_json_result(job_dir / "subscription.json")
    has_subscription = identity.get("has_subscription") is True
    if not has_subscription:
        if result.state != "missing":
            raise KnowledgeJobRegistryError("knowledge_job_subscription_invalid")
        return None
    if result.state != "valid":
        raise KnowledgeJobRegistryError("knowledge_job_subscription_invalid")
    try:
        subscription = _canonical_staged_subscription(result.payload)
    except ValueError as exc:
        raise KnowledgeJobRegistryError(
            "knowledge_job_subscription_invalid"
        ) from exc
    if subscription is None:
        raise KnowledgeJobRegistryError("knowledge_job_subscription_invalid")
    digest = hashlib.sha256(canonical_pack_bytes(subscription)).hexdigest()
    if digest != identity.get("subscription_sha256"):
        raise KnowledgeJobRegistryError("knowledge_job_subscription_invalid")
    return subscription


def _cleanup_payload(job_dir: Path) -> None:
    safe_job_dir = _revalidated_job_dir(job_dir)
    if safe_job_dir is None:
        return
    knowledge_root = safe_job_dir.parent.parent
    with _jobs_registry_lock(knowledge_root):
        safe_job_dir = _revalidated_job_dir(safe_job_dir)
        if safe_job_dir is None:
            return
        _cleanup_payload_validated(safe_job_dir)


def _cleanup_payload_validated(job_dir: Path) -> None:
    for name in (
        "pack.json",
        PACK_ARTIFACT_NAME,
        INDEX_MANIFEST_NAME,
        VECTOR_ARTIFACT_NAME,
        "subscription.json",
        *STAGING_DATABASE_NAMES,
    ):
        try:
            (job_dir / name).unlink(missing_ok=True)
        except OSError:
            pass


def _capacity_mismatch_state(
    job_dir: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    return _write_state(
        job_dir,
        state,
        state=DEGRADED_STATE,
        retrieval_mode="none",
        reason="job_capacity_identity_mismatch",
    )


def _subscription_mismatch_state(
    job_dir: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    return _write_state(
        job_dir,
        state,
        state=DEGRADED_STATE,
        retrieval_mode="none",
        reason="job_subscription_identity_mismatch",
    )


def _staging_database_mismatch_state(
    job_dir: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    return _write_state(
        job_dir,
        state,
        state=DEGRADED_STATE,
        retrieval_mode="none",
        reason="knowledge_staging_database_invalid",
    )


def _prepare_job(job_dir: Path) -> dict[str, Any]:
    """Validate staging again under its trusted-root lock before processing."""
    safe_job_dir = _revalidated_job_dir(job_dir)
    if safe_job_dir is None:
        return {}
    knowledge_root = safe_job_dir.parent.parent
    with _jobs_registry_lock(knowledge_root):
        safe_job_dir = _revalidated_job_dir(safe_job_dir)
        if safe_job_dir is None:
            return {}
        return _prepare_job_validated(safe_job_dir)


def _prepare_job_validated(job_dir: Path) -> dict[str, Any]:
    """Build the staging FTS/chunks off the event loop and resume idempotently."""
    with mutation_lock(_state_path(job_dir)):
        state = _read_job(job_dir)
        if state.get("state") == DEGRADED_STATE:
            return state
        if not state or state.get("state") in TERMINAL_STATES:
            return state
        try:
            subscription = _identity_validated_subscription(job_dir, state)
        except KnowledgeJobRegistryError:
            return _subscription_mismatch_state(job_dir, state)
        pack = _load_capacity_validated_job_pack(job_dir, state)
        if pack is None:
            return _capacity_mismatch_state(job_dir, state)
        staging_store: KnowledgeStore | None = None
        if state.get("state") in {"queued", "validating", "building_fts"}:
            state = _write_state(job_dir, state, state="building_fts")
            database_path = _validated_staging_database_path(job_dir)
            if database_path is None:
                return _staging_database_mismatch_state(job_dir, state)
            staging_store = KnowledgeStore(database_path)
            staging_store.replace_source(
                pack.source_tag,
                pack.entries,
                embedding_policy="prebuilt_only",
            )
            status = staging_store.chunk_status()
            if int(status["chunks_total"]) != int(state["chunks_total"]):
                return _capacity_mismatch_state(job_dir, state)
            state = _write_state(
                job_dir,
                state,
                state="verifying_index",
            )
        if state.get("state") == "verifying_index":
            if staging_store is None:
                # ``verifying_index`` is a durable restart boundary.  Reopen and
                # reconcile the staging database instead of relying on locals
                # created by the preceding in-process state transition.
                database_path = _validated_staging_database_path(job_dir)
                if database_path is None:
                    return _staging_database_mismatch_state(job_dir, state)
                staging_store = KnowledgeStore(database_path)
                staging_store.replace_source(
                    pack.source_tag,
                    pack.entries,
                    embedding_policy="prebuilt_only",
                )
                status = staging_store.chunk_status()
                if int(status["chunks_total"]) != int(state["chunks_total"]):
                    return _capacity_mismatch_state(job_dir, state)
            manifest_path = job_dir / INDEX_MANIFEST_NAME
            vectors_path = job_dir / VECTOR_ARTIFACT_NAME
            has_manifest = _staged_artifact_present(job_dir, INDEX_MANIFEST_NAME)
            has_vectors = _staged_artifact_present(job_dir, VECTOR_ARTIFACT_NAME)
            if has_manifest and has_vectors:
                from .prebuilt_index import validate_prebuilt_index

                expected_subscription = subscription or {}
                try:
                    validated = validate_prebuilt_index(
                        _read_staged_artifact(job_dir, PACK_ARTIFACT_NAME),
                        _read_staged_artifact(job_dir, INDEX_MANIFEST_NAME),
                        _read_staged_artifact(job_dir, VECTOR_ARTIFACT_NAME),
                        expected_pack_sha256=str(
                            expected_subscription.get("artifact_sha256") or ""
                        ),
                        expected_manifest_sha256=str(
                            expected_subscription.get("index_manifest_sha256") or ""
                        ),
                        expected_vectors_sha256=str(
                            expected_subscription.get("vectors_sha256") or ""
                        ),
                    )
                    stored = staging_store.store_chunk_embeddings_strict(
                        validated.prepared_embeddings()
                    )
                    total = len(validated.chunks)
                    if stored != total:
                        raise ValueError("prebuilt index import was incomplete")
                    state = _write_state(
                        job_dir,
                        state,
                        index_origin="prebuilt",
                        index_trust="trusted_market",
                        index_validation="accepted",
                        index_fallback_reason="",
                        prebuilt_chunks_ready=total,
                        prebuilt_chunks_missing=0,
                    )
                except (OSError, ValueError):
                    manifest_path.unlink(missing_ok=True)
                    vectors_path.unlink(missing_ok=True)
                    state = _write_state(
                        job_dir,
                        state,
                        index_origin="none",
                        index_trust="none",
                        index_validation="rejected",
                        index_fallback_reason="prebuilt_index_rejected",
                        prebuilt_chunks_ready=0,
                        prebuilt_chunks_missing=int(state.get("chunks_total") or 0),
                    )
            else:
                state = _write_state(
                    job_dir,
                    state,
                    index_origin="none",
                    index_trust="none",
                    index_validation="absent",
                    prebuilt_chunks_ready=0,
                    prebuilt_chunks_missing=int(state.get("chunks_total") or 0),
                )
        return state


def _activate_job(
    service, job_dir: Path, state: dict[str, Any], *, mode: str
) -> dict[str, Any]:
    pack_id = str(state.get("pack_id") or "")
    safe_job_dir = _revalidated_job_dir(job_dir)
    if safe_job_dir is None:
        return {"state": DEGRADED_STATE, "reason": "registry_path_invalid"}
    with pack_operation_lock(
        service.knowledge_root, pack_id
    ), _jobs_registry_lock(service.knowledge_root):
        safe_job_dir = _revalidated_job_dir(safe_job_dir)
        if safe_job_dir is None:
            return {"state": DEGRADED_STATE, "reason": "registry_path_invalid"}
        return _activate_job_validated(service, safe_job_dir, state, mode=mode)


def _activate_job_validated(
    service, job_dir: Path, state: dict[str, Any], *, mode: str
) -> dict[str, Any]:
    with mutation_lock(_state_path(job_dir)):
        current = _read_job(job_dir)
        if current.get("state") == DEGRADED_STATE:
            return current
        if current.get("state") in TERMINAL_STATES:
            if current.get("state") == "cancelled":
                _cleanup_payload(job_dir)
            return current
        try:
            subscription = _identity_validated_subscription(job_dir, current)
        except KnowledgeJobRegistryError:
            return _subscription_mismatch_state(job_dir, current)
        pack = _load_capacity_validated_job_pack(job_dir, current)
        if pack is None:
            return _capacity_mismatch_state(job_dir, current)
        if mode == "hybrid" and _validated_staging_database_path(
            job_dir,
            require_database=True,
        ) is None:
            return _staging_database_mismatch_state(job_dir, current)
        embeddings = (
            _strict_staged_vector_snapshot(job_dir, current, subscription)
            if mode == "hybrid"
            else ()
        )
        database_path = service.database_path()
        with mutation_lock(database_path):
            if mode == "hybrid":
                live_ready, replaced_ready = _live_ready_capacity_snapshot(
                    service,
                    pack.pack_id,
                )
                projected_ready = max(live_ready - replaced_ready, 0) + len(
                    embeddings
                )
                if projected_ready > MAX_READY_VECTOR_CHUNKS:
                    mode = "bm25"
                    embeddings = ()
                    current = _write_state(
                        job_dir,
                        current,
                        index_origin="none",
                        index_trust="none",
                        index_validation="rejected",
                        index_fallback_reason="vector_budget_exceeded",
                        prebuilt_chunks_ready=0,
                        prebuilt_chunks_missing=int(current.get("chunks_total") or 0),
                    )
            result = install_pack(
                database_path,
                pack,
                subscription=subscription,
                prepared_embeddings=embeddings,
                retrieval_mode=mode,
                embedding_policy="prebuilt_only",
                index_metadata={
                    "index_origin": current.get("index_origin", "none"),
                    "index_trust": current.get("index_trust", "none"),
                    "index_validation": current.get("index_validation", "absent"),
                    "index_fallback_reason": current.get("index_fallback_reason", ""),
                    "prebuilt_chunks_ready": current.get("prebuilt_chunks_ready", 0),
                    "prebuilt_chunks_missing": current.get(
                        "prebuilt_chunks_missing", 0
                    ),
                },
            )
        try:
            activation = {
                "schema_version": 1,
                "job_id": current["job_id"],
                "pack_id": current["pack_id"],
                "pack_sha256": current["pack_sha256"],
                "has_subscription": current["has_subscription"],
                "subscription_sha256": current["subscription_sha256"],
                "retrieval_mode": result.retrieval_mode,
            }
            _record_activation_commit(service.knowledge_root, activation)
            atomic_write_json(
                _activation_path(job_dir),
                activation,
                ensure_ascii=False,
                indent=2,
            )
            state = _write_state(
                job_dir,
                current,
                state="active",
                retrieval_mode=result.retrieval_mode,
                chunks_ready=len(embeddings),
                indexed_percent=(
                    100.0
                    if mode == "hybrid"
                    else float(current.get("indexed_percent") or 0.0)
                ),
                reason="",
            )
        except Exception:
            # install_pack() is the durable commit point.  Keep the staging
            # payload so a later pass can reconcile the journal, but never let
            # an auxiliary state-file failure relabel the live pack as failed.
            logger.exception("knowledge pack committed but active state was not persisted")
            return {
                **current,
                "state": "active",
                "retrieval_mode": result.retrieval_mode,
                "_activation_committed": True,
                "_state_persisted": False,
            }
        try:
            service.refresh_routing_index(background=True)
        except Exception:
            logger.exception("knowledge pack activated but routing refresh failed")
    _cleanup_payload(job_dir)
    return state


def _strict_staged_vector_snapshot(
    job_dir: Path,
    state: dict[str, Any],
    subscription: dict[str, str] | None,
) -> tuple[dict[str, object], ...]:
    from .prebuilt_index import validate_prebuilt_index

    database_path = _validated_staging_database_path(
        job_dir,
        require_database=True,
    )
    if database_path is None:
        raise KnowledgeJobRegistryError("knowledge_staged_vectors_incomplete")
    expected_subscription = subscription or {}
    try:
        validated = validate_prebuilt_index(
            _read_staged_artifact(job_dir, PACK_ARTIFACT_NAME),
            _read_staged_artifact(job_dir, INDEX_MANIFEST_NAME),
            _read_staged_artifact(job_dir, VECTOR_ARTIFACT_NAME),
            expected_pack_sha256=str(
                expected_subscription.get("artifact_sha256") or ""
            ),
            expected_manifest_sha256=str(
                expected_subscription.get("index_manifest_sha256") or ""
            ),
            expected_vectors_sha256=str(
                expected_subscription.get("vectors_sha256") or ""
            ),
        )
    except (OSError, ValueError) as exc:
        raise KnowledgeJobRegistryError("knowledge_staged_vectors_incomplete") from exc
    store = KnowledgeStore(database_path)
    status = store.chunk_status(strict=True)
    expected_total = int(state.get("chunks_total") or 0)
    expected_ready = int(state.get("prebuilt_chunks_ready") or 0)
    embeddings = store.ready_embedding_records(strict=True)
    expected = validated.prepared_embeddings()
    expected_by_key = {
        (str(record["chunk_id"]), str(record["content_hash"])): record
        for record in expected
    }
    embeddings_by_key = {
        (str(record["chunk_id"]), str(record["content_hash"])): record
        for record in embeddings
    }
    if (
        state.get("index_validation") != "accepted"
        or expected_ready != expected_total
        or int(status["chunks_total"]) != expected_total
        or int(status["chunks_ready"]) != expected_ready
        or len(embeddings) != expected_ready
        or len(expected_by_key) != len(expected)
        or len(embeddings_by_key) != len(embeddings)
        or expected_by_key.keys() != embeddings_by_key.keys()
    ):
        raise KnowledgeJobRegistryError("knowledge_staged_vectors_incomplete")
    for key, expected_record in expected_by_key.items():
        record = embeddings_by_key[key]
        if (
            record.get("embedding_policy") != "prebuilt_only"
            or record.get("model_id") != expected_record.get("model_id")
            or record.get("dimensions") != expected_record.get("dimensions")
            or record.get("embedding") != expected_record.get("embedding")
        ):
            raise KnowledgeJobRegistryError("knowledge_staged_vectors_incomplete")
    return embeddings


def _live_ready_capacity_snapshot(service, pack_id: str) -> tuple[int, int]:
    database_path = service.database_path()
    if not database_path.is_file():
        return 0, 0
    store = KnowledgeStore(database_path)
    total = int(store.chunk_status(strict=True)["chunks_ready"])
    replaced = int(
        store.source_chunk_status(
            f"source:community.{pack_id}",
            strict=True,
        )["chunks_ready"]
    )
    return total, replaced


def _list_jobs_for_processing(knowledge_root: Path) -> tuple[dict[str, object], ...]:
    all_jobs = list_pack_jobs(knowledge_root)
    for item in all_jobs:
        item_job_id = str(item.get("job_id") or "")
        if (
            item.get("state") in TERMINAL_STATES
            and item_job_id
            and Path(item_job_id).name == item_job_id
        ):
            job_dir = _external_job_dir(
                knowledge_root,
                item_job_id,
                require_existing=True,
            )
            if job_dir is not None:
                _cleanup_payload(job_dir)
    return all_jobs


async def process_pack_jobs(
    service,
    *,
    batch_size: int,
    ready_vector_chunks: int,
) -> dict[str, object]:
    """Verify and activate at most one staged community pack."""

    all_jobs = await asyncio.to_thread(
        _list_jobs_for_processing,
        service.knowledge_root,
    )
    jobs = [
        item
        for item in reversed(all_jobs)
        if item.get("state") not in TERMINAL_STATES | {DEGRADED_STATE}
    ]
    if not jobs:
        return {"state": "no_work", "selected": 0, "stored": 0}
    state = jobs[0]
    job_dir = _external_job_dir(
        service.knowledge_root,
        str(state["job_id"]),
        require_existing=True,
    )
    if job_dir is None:
        return {"state": "no_work", "selected": 0, "stored": 0}
    try:
        state = await asyncio.to_thread(_prepare_job, job_dir)
        if state.get("state") == DEGRADED_STATE:
            return {"state": DEGRADED_STATE, "selected": 0, "stored": 0}
        if not state or state.get("state") in TERMINAL_STATES:
            return {"state": "no_work", "selected": 0, "stored": 0}

        has_prebuilt = state.get("index_validation") == "accepted"
        if has_prebuilt:
            activated = await asyncio.to_thread(
                _activate_job,
                service,
                job_dir,
                state,
                mode="hybrid",
            )
            if activated.get("state") == DEGRADED_STATE:
                return {"state": DEGRADED_STATE, "selected": 0, "stored": 0}
            if activated.get("state") == "failed":
                return {"state": "failed", "selected": 0, "stored": 0}
            if activated.get("state") == "cancelled":
                return {"state": "cancelled", "selected": 0, "stored": 0}
            if activated.get("retrieval_mode") != "hybrid":
                return {"state": "ready_bm25", "selected": 0, "stored": 0}
            ready = int(activated.get("chunks_ready") or 0)
            activation_state = (
                "cancelled" if activated.get("state") == "cancelled" else "ready_hybrid"
            )
            return {
                "state": activation_state,
                "selected": ready,
                "stored": ready,
            }
        activated = await asyncio.to_thread(
            _activate_job,
            service,
            job_dir,
            state,
            mode="bm25",
        )
        if activated.get("state") == DEGRADED_STATE:
            return {"state": DEGRADED_STATE, "selected": 0, "stored": 0}
        if activated.get("state") == "cancelled":
            return {"state": "cancelled", "selected": 0, "stored": 0}
        if activated.get("state") == "failed":
            return {"state": "failed", "selected": 0, "stored": 0}
        return {"state": "ready_bm25", "selected": 0, "stored": 0}
    except Exception as exc:
        safe_job_dir = _revalidated_job_dir(job_dir)
        if safe_job_dir is None:
            return {"state": DEGRADED_STATE, "selected": 0, "stored": 0}
        job_dir = safe_job_dir
        current = _read_job(job_dir)
        if current.get("state") == DEGRADED_STATE:
            return {"state": DEGRADED_STATE, "selected": 0, "stored": 0}
        if current.get("state") == "cancelled":
            _cleanup_payload(job_dir)
            return {"state": "cancelled", "selected": 0, "stored": 0}
        final_state = await _write_state_async(
            job_dir,
            state="failed",
            retrieval_mode="none",
            reason=type(exc).__name__,
        )
        if final_state.get("state") == DEGRADED_STATE:
            return {"state": DEGRADED_STATE, "selected": 0, "stored": 0}
        if final_state.get("state") == "cancelled":
            _cleanup_payload(job_dir)
            return {"state": "cancelled", "selected": 0, "stored": 0}
        if not final_state:
            return {"state": DEGRADED_STATE, "selected": 0, "stored": 0}
        _cleanup_payload(job_dir)
        return {
            "state": str(final_state.get("state") or "failed"),
            "selected": 0,
            "stored": 0,
        }
