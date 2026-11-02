"""Numeric v2 atomic file storage for Sessions and Ledgers."""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
from copy import deepcopy
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, TYPE_CHECKING
from weakref import WeakValueDictionary

import portalocker

from .numeric_v2_archive import _retry_windows_permission_error
from .numeric_v2_context import pending_transition_record
from .numeric_v2_storage_transaction import discard_temporary_file, run_storage_mutation
from .numeric_v2_narrative import apply_narrative_record, narrative_body_fingerprint
from .numeric_v2_performance import (
    is_silent_invitation_control,
    transition_source_dialogue_policy,
    valid_mixed_performance_policy,
    valid_ordered_content,
    valid_scene_narration,
)

if TYPE_CHECKING:
    from .numeric_v2_runtime import NumericV2Engine, ScriptSessionV2


STORE_SCHEMA = "neko.script.store.numeric.v2"
STORY_SESSION_INDEX_SCHEMA = "neko.script.story_session_index.numeric.v2.character-slots"


class NumericV2StoreError(ValueError):
    """Numeric v2 archive cannot be read or committed safely."""  # noqa: DOCSTRING_CJK


class NumericV2SessionExistsError(NumericV2StoreError):
    pass


class NumericV2SessionNotFoundError(NumericV2StoreError):
    pass


class NumericV2StoreRevisionConflictError(NumericV2StoreError):
    pass


@dataclass(frozen=True, slots=True)
class NumericV2StoredSession:
    session: "ScriptSessionV2"
    ledger_events: tuple[dict[str, Any], ...]


# A coroutine holding or waiting for a lock retains a strong reference; idle locks are reclaimed from the weak-reference table to prevent unbounded accumulation of Session/script IDs.
_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_STORY_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _lock(path: Path) -> asyncio.Lock:
    key = str(path.resolve())
    lock = _LOCKS.get(key)
    if lock is None:
        # A new lock must first be held strongly in a local variable and only then registered weakly; otherwise it could be reclaimed between creation and return.
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


def _story_lock(path: Path, story_id: str) -> asyncio.Lock:
    key = f"{path.resolve()}::{story_id}"
    lock = _STORY_LOCKS.get(key)
    if lock is None:
        # Match the Session file lock's lifetime; callers keep a strong reference until they enter async with.
        lock = asyncio.Lock()
        _STORY_LOCKS[key] = lock
    return lock


@asynccontextmanager
async def numeric_v2_story_session_guard(
    theater_storage_root: Path,
    story_id: str,
):
    """Reuse the lifecycle lock for a specified script's Session without loading its script package."""  # noqa: DOCSTRING_CJK

    normalized_story_id = str(story_id or "").strip()
    if not normalized_story_id:
        raise NumericV2StoreError("numeric_story_id_required")
    index_path = Path(theater_storage_root) / "numeric_v2" / "story_sessions.json"
    async with _story_lock(index_path, normalized_story_id):
        yield


def _read_story_session_slots(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    try:
        # Windows share violations (antivirus, indexer, cloud sync) are brief;
        # retry them like the archive store instead of failing the request.
        payload = json.loads(
            _retry_windows_permission_error(lambda: path.read_text(encoding="utf-8"))
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        # An existing but unreadable index is not equivalent to a new empty index; otherwise any write request would overwrite all recovery slots.
        raise NumericV2StoreError("numeric_story_session_index_read_failed") from exc
    if not isinstance(payload, dict) or payload.get("schema") != STORY_SESSION_INDEX_SCHEMA:
        raise NumericV2StoreError("numeric_story_session_index_invalid")
    stories = payload.get("stories")
    if not isinstance(stories, dict):
        raise NumericV2StoreError("numeric_story_session_index_invalid")
    normalized: dict[str, dict[str, str]] = {}
    for story_id, slots in stories.items():
        normalized_story_id = str(story_id or "").strip()
        if not normalized_story_id or not isinstance(slots, dict):
            continue
        normalized_slots = {
            str(catgirl_name).strip(): str(session_id).strip()
            for catgirl_name, session_id in slots.items()
            if str(catgirl_name).strip() and str(session_id).strip()
        }
        if normalized_slots:
            normalized[normalized_story_id] = normalized_slots
    return normalized


def _is_story_session_index_content_error(exc: NumericV2StoreError) -> bool:
    """Tell a corrupt index (a rebuildable cache) apart from a temporarily unreadable one."""

    return not isinstance(exc.__cause__, OSError)


def _write_story_session_slots(
    path: Path,
    stories: Mapping[str, Mapping[str, str]],
) -> None:
    _atomic_write_json_payload(
        path,
        {
            "schema": STORY_SESSION_INDEX_SCHEMA,
            "stories": {
                str(story_id): dict(slots)
                for story_id, slots in stories.items()
                if slots
            },
        },
    )


def _atomic_write_json_payload(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.stem}-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        _retry_windows_permission_error(lambda: os.replace(temporary_path, path))
        temporary_path = None
    finally:
        discard_temporary_file(temporary_path)


def _with_failed_path(error: NumericV2StoreError, path: Path) -> NumericV2StoreError:
    """Record which file made a strict enumeration fail so callers can report it."""
    error.path = str(path)
    return error


def _numeric_v2_session_root(theater_storage_root: Path) -> Path:
    return Path(theater_storage_root) / "numeric_v2" / "sessions"


def _numeric_v2_public_archive_root(theater_storage_root: Path) -> Path:
    return Path(theater_storage_root) / "numeric_v2" / "public_archives"


def _session_matches_character(
    binding: Mapping[str, Any],
    character_id: str,
    legacy_catgirl_name: str = "",
) -> bool:
    stored_character_id = str(binding.get("character_id") or "").strip()
    if stored_character_id:
        return stored_character_id == character_id
    return bool(
        legacy_catgirl_name
        and str(binding.get("catgirl_name") or "").strip() == legacy_catgirl_name
    )


def _read_numeric_v2_session_summary(
    path: Path,
    *,
    raise_on_io_error: bool = False,
) -> dict[str, str] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        # Keep ordinary listing tolerant as before; startup audit must distinguish temporary unreadability from corrupt contents.
        if raise_on_io_error:
            raise
        return None
    except (UnicodeError, json.JSONDecodeError) as exc:
        # If ownership cannot be confirmed before deletion, abort; startup audit handles this error through the existing corrupt-archive quarantine flow.
        if raise_on_io_error:
            raise _with_failed_path(
                NumericV2StoreError("numeric_session_read_failed"), path,
            ) from exc
        return None
    if not isinstance(payload, dict) or payload.get("schema") != STORE_SCHEMA:
        if raise_on_io_error:
            raise _with_failed_path(NumericV2StoreError("numeric_session_read_failed"), path)
        return None
    raw_session = payload.get("session")
    binding = raw_session.get("catgirl_binding") if isinstance(raw_session, dict) else None
    if not isinstance(raw_session, dict) or not isinstance(binding, dict):
        if raise_on_io_error:
            raise _with_failed_path(NumericV2StoreError("numeric_session_read_failed"), path)
        return None
    return {
        "session_id": str(raw_session.get("session_id") or path.stem),
        "story_id": str(raw_session.get("story_package_id") or "").strip(),
        "catgirl_name": str(binding.get("catgirl_name") or "").strip(),
        "character_id": str(binding.get("character_id") or "").strip(),
        "status": str(raw_session.get("status") or "active"),
        "path": str(path),
    }


def list_numeric_v2_sessions(
    theater_storage_root: Path,
    *,
    story_id: str = "",
    character_id: str = "",
    legacy_catgirl_name: str = "",
    raise_on_io_error: bool = False,
) -> list[dict[str, str]]:
    """List recognized Numeric v2 Sessions by script or character."""  # noqa: DOCSTRING_CJK

    normalized_story_id = str(story_id or "").strip()
    normalized_character_id = str(character_id or "").strip()
    normalized_legacy_name = str(legacy_catgirl_name or "").strip()
    root = _numeric_v2_session_root(theater_storage_root)
    if not root.is_dir():
        return []
    result: list[dict[str, str]] = []
    for path in sorted(root.glob("*.json")):
        summary = _read_numeric_v2_session_summary(
            path,
            raise_on_io_error=raise_on_io_error,
        )
        if summary is None:
            continue
        if normalized_story_id and summary["story_id"] != normalized_story_id:
            continue
        if normalized_character_id:
            binding_matches = summary["character_id"] == normalized_character_id or (
                not summary["character_id"]
                and normalized_legacy_name
                and summary["catgirl_name"] == normalized_legacy_name
            )
        elif normalized_legacy_name:
            # If an old character card has no character_id, narrow deletion by character name only; never interpret an empty ID as "all characters."
            binding_matches = summary["catgirl_name"] == normalized_legacy_name
        else:
            binding_matches = True
        if not binding_matches:
            continue
        result.append(summary)
    return result


def list_numeric_v2_public_archives(
    theater_storage_root: Path,
    *,
    story_id: str = "",
    character_id: str = "",
    legacy_catgirl_name: str = "",
    raise_on_io_error: bool = False,
) -> list[dict[str, str]]:
    """List public performance cold archives matching a script or character."""  # noqa: DOCSTRING_CJK

    normalized_story_id = str(story_id or "").strip()
    normalized_character_id = str(character_id or "").strip()
    normalized_legacy_name = str(legacy_catgirl_name or "").strip()
    root = _numeric_v2_public_archive_root(theater_storage_root)
    if not root.is_dir():
        return []
    result: list[dict[str, str]] = []
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            # Keep display listing tolerant; the delete transaction must treat temporary unreadability as failure, not omit the user's archive.
            if raise_on_io_error:
                raise
            continue
        except (UnicodeError, json.JSONDecodeError) as exc:
            if raise_on_io_error:
                raise _with_failed_path(
                    NumericV2StoreError("numeric_public_archive_read_failed"), path,
                ) from exc
            continue
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != "neko.theater.numeric.v2.public-archive"
        ):
            if raise_on_io_error:
                raise _with_failed_path(
                    NumericV2StoreError("numeric_public_archive_read_failed"), path,
                )
            continue
        summary = {
            "session_id": str(payload.get("session_id") or "").strip(),
            "story_id": str(payload.get("story_id") or "").strip(),
            "catgirl_name": str(payload.get("catgirl_name") or "").strip(),
            "character_id": str(payload.get("character_id") or "").strip(),
            "path": str(path),
        }
        if normalized_story_id and summary["story_id"] != normalized_story_id:
            continue
        if normalized_character_id and not (
            summary["character_id"] == normalized_character_id
            or (
                not summary["character_id"]
                and normalized_legacy_name
                and summary["catgirl_name"] == normalized_legacy_name
            )
        ):
            continue
        if (
            not normalized_character_id
            and normalized_legacy_name
            and summary["catgirl_name"] != normalized_legacy_name
        ):
            continue
        result.append(summary)
    return result


@asynccontextmanager
async def numeric_v2_session_files_guard(theater_storage_root: Path):
    """Acquire async file locks before dispatching a synchronous disk transaction."""
    session_root = _numeric_v2_session_root(theater_storage_root)
    async with _lock(session_root.parent / "story_sessions.json"), AsyncExitStack() as stack:
        # The index lock excludes creation/replacement while we collect paths.
        paths = sorted([*session_root.glob("*.json"),
                        *(session_root.parent / "public_archives").glob("*.json")])
        for path in paths:
            await stack.enter_async_context(_lock(path))
        yield


async def delete_numeric_v2_sessions(theater_storage_root: Path, **scope) -> list[dict[str, str]]:
    async with numeric_v2_session_files_guard(theater_storage_root):
        # Keep file locks until the worker finishes, including caller cancellation.
        return await run_storage_mutation(
            nullcontext, _delete_numeric_v2_sessions_unlocked, theater_storage_root, **scope,
        )


def _delete_numeric_v2_sessions_unlocked(
    theater_storage_root: Path,
    *,
    story_id: str = "",
    character_id: str = "",
    legacy_catgirl_name: str = "",
) -> list[dict[str, str]]:
    """Delete Sessions for the specified script or character and update the recovery index."""  # noqa: DOCSTRING_CJK

    normalized_story_id = str(story_id or "").strip()
    normalized_character_id = str(character_id or "").strip()
    normalized_legacy_name = str(legacy_catgirl_name or "").strip()
    if (
        not normalized_story_id
        and not normalized_character_id
        and not normalized_legacy_name
    ):
        raise NumericV2StoreError("numeric_session_delete_scope_required")
    session_root = _numeric_v2_session_root(theater_storage_root)
    index_path = session_root.parent / "story_sessions.json"
    # If the index is unreadable, fail before deleting any Session or cold archive.
    index_error: NumericV2StoreError | None = None
    try:
        stories = _read_story_session_slots(index_path)
    except NumericV2StoreError as exc:
        if not _is_story_session_index_content_error(exc):
            raise
        stories, index_error = {}, exc
    try:
        candidates = list_numeric_v2_sessions(
            theater_storage_root,
            story_id=normalized_story_id,
            character_id=normalized_character_id,
            legacy_catgirl_name=normalized_legacy_name,
            raise_on_io_error=True,
        )
    except OSError as exc:
        raise NumericV2StoreError("numeric_session_read_failed") from exc
    try:
        archive_candidates = list_numeric_v2_public_archives(
            theater_storage_root,
            story_id=normalized_story_id,
            character_id=normalized_character_id,
            legacy_catgirl_name=normalized_legacy_name,
            raise_on_io_error=True,
        )
    except OSError as exc:
        raise NumericV2StoreError("numeric_public_archive_read_failed") from exc
    if index_error is not None:
        if candidates or archive_candidates:
            raise index_error
        # A corrupt index must not block scopes that own no theater data;
        # the startup audit quarantines and rebuilds it.
        return []
    deleted: list[dict[str, str]] = []
    for candidate in candidates:
        path = Path(candidate["path"])
        try:
            current = _read_numeric_v2_session_summary(
                path,
                raise_on_io_error=True,
            )
        except OSError as exc:
            raise NumericV2StoreError("numeric_session_read_failed") from exc
        if current is None:
            continue
        if normalized_story_id and current["story_id"] != normalized_story_id:
            continue
        if normalized_character_id:
            binding_matches = current["character_id"] == normalized_character_id or (
                not current["character_id"]
                and normalized_legacy_name
                and current["catgirl_name"] == normalized_legacy_name
            )
        elif normalized_legacy_name:
            binding_matches = current["catgirl_name"] == normalized_legacy_name
        else:
            binding_matches = True
        if not binding_matches:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        deleted.append(candidate)

    # Public cold archives share the lifecycle of their script/character; deleting a recovery slot must not leave an orphan file.
    for archive in archive_candidates:
        path = Path(archive["path"])
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    if (
        normalized_story_id
        and not normalized_character_id
        and not normalized_legacy_name
    ):
        stories.pop(normalized_story_id, None)
    if normalized_character_id:
        story_ids = (
            [normalized_story_id]
            if normalized_story_id
            else list(stories)
        )
        for current_story_id in story_ids:
            if current_story_id not in stories:
                continue
            stories[current_story_id].pop(normalized_character_id, None)
            if not stories[current_story_id]:
                stories.pop(current_story_id)
    if normalized_legacy_name:
        deleted_session_ids = {item["session_id"] for item in deleted}
        for current_story_id in list(stories):
            stories[current_story_id] = {
                current_character_id: current_session_id
                for current_character_id, current_session_id in stories[
                    current_story_id
                ].items()
                if current_session_id not in deleted_session_ids
            }
            if not stories[current_story_id]:
                stories.pop(current_story_id)
    if index_path.is_file() or stories:
        _write_story_session_slots(index_path, stories)
    return deleted


def _rebind_session_file(
    path: Path,
    stories: dict[str, dict[str, str]],
    normalized_character_id: str,
    legacy_catgirl_name: str,
    catgirl_binding: Mapping[str, Any],
) -> None:
    """Rewrite one session's identity projection and migrate its story slot in ``stories``."""
    try:
        payload = json.loads(
            _retry_windows_permission_error(lambda: path.read_text(encoding="utf-8"))
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NumericV2StoreError("numeric_session_read_failed") from exc
    raw_session = payload.get("session") if isinstance(payload, dict) else None
    if not isinstance(raw_session, dict):
        raise NumericV2StoreError("numeric_session_payload_invalid")
    existing_binding = raw_session.get("catgirl_binding")
    refreshed_binding = {
        str(key): str(value)
        for key, value in catgirl_binding.items()
    }
    if isinstance(existing_binding, Mapping):
        # Replay historical Ledger data using the name facts from that Session; a character rename may refresh only the catgirl display fields.
        refreshed_binding["player_address"] = str(
            existing_binding.get("player_address") or ""
        )
    raw_session["catgirl_binding"] = refreshed_binding
    _atomic_write_json_payload(path, payload)
    story_id = str(raw_session.get("story_package_id") or "").strip()
    session_id = str(raw_session.get("session_id") or path.stem).strip()
    if story_id and session_id:
        slots = stories.get(story_id, {})
        legacy_key = str(legacy_catgirl_name or "").strip()
        # Rename only migrates an existing slot; snapshots stay unpublished.
        # An established character-ID slot wins over a stale legacy slot.
        if legacy_key != normalized_character_id and slots.get(legacy_key) == session_id:
            slots.pop(legacy_key)
            slots.setdefault(normalized_character_id, session_id)


async def update_numeric_v2_character_bindings(
    theater_storage_root: Path,
    *,
    character_id: str,
    legacy_catgirl_name: str,
    catgirl_binding: Mapping[str, Any],
) -> int:
    """Preserve progress for every script when a character card is renamed and update persisted identity projections."""  # noqa: DOCSTRING_CJK

    normalized_character_id = str(character_id or "").strip()
    if not normalized_character_id:
        raise NumericV2StoreError("numeric_character_id_required")
    session_root = _numeric_v2_session_root(theater_storage_root)
    index_path = session_root.parent / "story_sessions.json"
    # Lock order is unchanged (index, then one session path at a time) and the
    # asyncio locks stay on the loop; every read, write and fsync runs on a
    # worker. run_storage_mutation keeps a lock held until its worker finishes,
    # even if the caller is cancelled, so the rename's snapshot rollback never
    # races a half-written file.
    async with _lock(index_path):
        candidates = await asyncio.to_thread(
            list_numeric_v2_sessions,
            theater_storage_root,
            character_id=normalized_character_id,
            legacy_catgirl_name=legacy_catgirl_name,
        )
        if not candidates and not await asyncio.to_thread(index_path.is_file):
            # Nothing to rebind: do not create the index (a rename would then
            # depend on theater storage, and cloudsave would see theater content).
            return 0
        try:
            stories = await asyncio.to_thread(_read_story_session_slots, index_path)
        except NumericV2StoreError as exc:
            if candidates or not _is_story_session_index_content_error(exc):
                raise
            # Characters without theater data are not blocked by a corrupt index.
            return 0
        updated = 0
        for candidate in candidates:
            path = Path(candidate["path"])
            async with _lock(path):
                await run_storage_mutation(
                    nullcontext,
                    _rebind_session_file,
                    path,
                    stories,
                    normalized_character_id,
                    legacy_catgirl_name,
                    catgirl_binding,
                )
                updated += 1
        await run_storage_mutation(nullcontext, _write_story_session_slots, index_path, stories)
        return updated


class NumericV2SessionStore:
    """One file per Session; revalidate revision before every atomic replacement."""  # noqa: DOCSTRING_CJK

    def __init__(self, root: Path, engine: "NumericV2Engine", *, write_transaction=nullcontext):
        self.root = Path(root) / "numeric_v2" / "sessions"
        self.engine = engine
        self.write_transaction = write_transaction

    def _path(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not session_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in session_id):
            raise NumericV2StoreError("numeric_session_id_invalid")
        return self.root / f"{session_id}.json"

    @property
    def _story_session_index_path(self) -> Path:
        return self.root.parent / "story_sessions.json"

    @asynccontextmanager
    async def story_session_guard(self, story_id: str):
        async with numeric_v2_story_session_guard(self.root.parent.parent, story_id):
            yield

    def _read_story_session_index(self) -> dict[str, dict[str, str]]:
        return _read_story_session_slots(self._story_session_index_path)

    def _write_story_session_index(
        self,
        stories: Mapping[str, Mapping[str, str]],
    ) -> None:
        _write_story_session_slots(self._story_session_index_path, stories)

    async def get_story_session_id(self, story_id: str, character_id: str) -> str:
        async with _lock(self._story_session_index_path):
            stories = await asyncio.to_thread(self._read_story_session_index)
            return stories.get(str(story_id or "").strip(), {}).get(
                str(character_id or "").strip(),
                "",
            )

    async def restore_story_session(
        self,
        story_id: str,
        character_id: str,
        legacy_catgirl_name: str = "",
    ) -> NumericV2StoredSession | None:
        """Read the unique Session for a script and catgirl from the recovery index."""  # noqa: DOCSTRING_CJK

        normalized_story_id = str(story_id or "").strip()
        normalized_character_id = str(character_id or "").strip()
        normalized_legacy_name = str(legacy_catgirl_name or "").strip()
        if not normalized_story_id or not normalized_character_id:
            return None
        async with self.story_session_guard(normalized_story_id):
            return await self._restore_story_session_unlocked(
                normalized_story_id,
                normalized_character_id,
                normalized_legacy_name,
            )

    async def _restore_story_session_unlocked(
        self,
        normalized_story_id: str,
        normalized_character_id: str,
        normalized_legacy_name: str = "",
    ) -> NumericV2StoredSession | None:
        session_id = await self.get_story_session_id(
            normalized_story_id,
            normalized_character_id,
        )
        if session_id:
            try:
                indexed = await self.load(session_id)
            except NumericV2StoreError as exc:
                # Corrupt JSON, contracts, or ledgers can be repaired by cold-start audit; transient I/O errors must be returned for caller retry.
                if isinstance(exc.__cause__, OSError):
                    raise
                indexed = None
            if (
                indexed is not None
                and indexed.session.story_package_id == normalized_story_id
                and _session_matches_character(
                    indexed.session.catgirl_binding,
                    normalized_character_id,
                    normalized_legacy_name,
                )
            ):
                return indexed
        return None

    async def _mutate(self, operation):
        """Run one synchronous session write on a worker inside the storage fence.

        Callers keep the asyncio path locks on the event loop. The fence is
        entered and left on the same worker thread (Windows mutexes are
        thread-bound), and file I/O, fsync and ledger replay stay off the loop.
        """

        return await run_storage_mutation(self.write_transaction, operation)

    async def create(self, session: "ScriptSessionV2") -> NumericV2StoredSession:
        path = self._path(session.session_id)
        async with _lock(path):
            def create() -> NumericV2StoredSession:
                if path.exists():
                    raise NumericV2SessionExistsError("numeric_session_exists")
                self.engine.validate_session(session)
                stored = NumericV2StoredSession(session, ())
                self._write(path, stored, exclusive=True)
                return stored

            return await self._mutate(create)

    async def create_isolated_snapshot(
        self,
        stored: NumericV2StoredSession,
    ) -> NumericV2StoredSession:
        """Atomically write a replayed snapshot without publishing it to the script's recovery slot, for load-testing forks."""  # noqa: DOCSTRING_CJK

        path = self._path(stored.session.session_id)
        async with _lock(path):
            def create() -> NumericV2StoredSession:
                if path.exists():
                    raise NumericV2SessionExistsError("numeric_session_exists")
                # Validate the full ledger in memory first, then write it once; on failure, do not leave a partial forked chain.
                self._validate_chain(stored)
                self._write(path, stored, exclusive=True)
                return stored

            return await self._mutate(create)

    async def create_story_session(
        self,
        session: "ScriptSessionV2",
    ) -> NumericV2StoredSession:
        """Atomically create a Session file and publish its recovery slot for the script."""  # noqa: DOCSTRING_CJK

        path = self._path(session.session_id)
        index_path = self._story_session_index_path
        character_id = str(session.catgirl_binding.get("character_id") or "").strip()
        if not character_id:
            raise NumericV2StoreError("numeric_story_session_index_invalid")
        async with _lock(index_path):
            async with _lock(path):
                def create() -> NumericV2StoredSession:
                    if path.exists():
                        raise NumericV2SessionExistsError("numeric_session_exists")
                    self.engine.validate_session(session)
                    stored = NumericV2StoredSession(session, ())
                    stories = self._read_story_session_index()
                    stories.setdefault(session.story_package_id, {})[
                        character_id
                    ] = session.session_id
                    self._write(path, stored, exclusive=True)
                    try:
                        self._write_story_session_index(stories)
                    except Exception:
                        # If index publication fails, remove the newly created unreachable Session so files and recovery slots remain atomic.
                        try:
                            path.unlink()
                        except FileNotFoundError:
                            pass
                        except OSError as rollback_exc:
                            raise NumericV2StoreError(
                                "numeric_session_create_rollback_failed"
                            ) from rollback_exc
                        raise
                    return stored

                return await self._mutate(create)

    async def replace_active(
        self,
        previous_session_id: str,
        session: "ScriptSessionV2",
    ) -> NumericV2StoredSession:
        """Replace the slot's Session with a new ID so old pages cannot continue submitting, without accumulating history."""  # noqa: DOCSTRING_CJK

        previous_path = self._path(previous_session_id)
        next_path = self._path(session.session_id)
        if previous_path == next_path:
            raise NumericV2StoreError("numeric_replacement_session_id_reused")
        character_id = str(session.catgirl_binding.get("character_id") or "").strip()
        if not character_id:
            raise NumericV2StoreError("numeric_story_session_index_invalid")
        index_path = self._story_session_index_path
        async with _lock(index_path):
            async with _lock(previous_path):
                async with _lock(next_path):
                    def replace_session() -> NumericV2StoredSession:
                        if not previous_path.is_file():
                            raise NumericV2SessionNotFoundError("numeric_session_not_found")
                        previous = self._read(previous_path)
                        if previous.session.story_package_id != session.story_package_id:
                            raise NumericV2StoreError("numeric_replacement_story_mismatch")
                        if next_path.exists():
                            raise NumericV2SessionExistsError("numeric_session_exists")
                        self.engine.validate_session(session)
                        stored = NumericV2StoredSession(session, ())
                        stories = self._read_story_session_index()
                        previous_stories = deepcopy(stories)
                        stories.setdefault(session.story_package_id, {})[
                            character_id
                        ] = session.session_id
                        self._write(next_path, stored, exclusive=True)
                        try:
                            self._write_story_session_index(stories)
                            previous_path.unlink()
                        except OSError as exc:
                            try:
                                self._write_story_session_index(previous_stories)
                            except OSError:
                                pass
                            try:
                                next_path.unlink()
                            except OSError:
                                pass
                            raise NumericV2StoreError("numeric_session_replace_failed") from exc
                        return stored

                    return await self._mutate(replace_session)

    async def load(self, session_id: str) -> NumericV2StoredSession | None:
        path = self._path(session_id)
        async with _lock(path):
            # Reading and replaying the whole ledger grows with the session; keep
            # it off the event loop while the path lock stays held on the loop.
            return await asyncio.to_thread(self._load_validated, path)

    def _load_validated(self, path: Path) -> NumericV2StoredSession | None:
        try:
            stored = self._read(path)
        except NumericV2StoreError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                return None
            raise
        # Reject cross-script Sessions by persisted identity first, then replay the Ledger using the current script engine.
        if stored.session.story_package_id != self.engine.story_id:
            return None
        self._validate_chain(stored)
        return stored

    async def load_for_lifecycle(
        self,
        session_id: str,
    ) -> NumericV2StoredSession | None:
        """Read a snapshot only for finishing/deletion; do not restore old script state into the current Runtime."""  # noqa: DOCSTRING_CJK

        path = self._path(session_id)
        async with _lock(path):
            return await asyncio.to_thread(self._load_for_lifecycle_validated, path)

    def _load_for_lifecycle_validated(self, path: Path) -> NumericV2StoredSession | None:
        try:
            stored = self._read(path)
        except NumericV2StoreError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                return None
            raise
        if stored.session.story_package_id != self.engine.story_id:
            return None
        self._validate_lifecycle_chain(stored)
        return stored

    async def commit(
        self,
        session: "ScriptSessionV2",
        ledger_event: Mapping[str, Any],
    ) -> NumericV2StoredSession:
        path = self._path(session.session_id)
        async with _lock(path):
            def commit_turn() -> NumericV2StoredSession:
                if not path.is_file():
                    raise NumericV2SessionNotFoundError("numeric_session_not_found")
                current = self._read(path)
                if current.session.revision != int(ledger_event.get("base_revision", -1)):
                    raise NumericV2StoreRevisionConflictError("numeric_base_revision_mismatch")
                if current.session.status == "ended":
                    raise NumericV2StoreRevisionConflictError("session_already_ended")
                # The candidate carries the lifecycle at preparation time. End and
                # resume can change it without advancing the story revision.
                if session.lifecycle_revision != current.session.lifecycle_revision:
                    raise NumericV2StoreRevisionConflictError("numeric_base_lifecycle_revision_mismatch")
                if any(event.get("client_turn_id") == ledger_event.get("client_turn_id") for event in current.ledger_events):
                    raise NumericV2StoreRevisionConflictError("numeric_duplicate_client_turn_id")
                if session.revision != current.session.revision + 1:
                    raise NumericV2StoreError("numeric_revision_not_monotonic")
                # Forget can advance while this turn is being generated without
                # changing the story revision. Keep its durable boundary.
                committed = replace(
                    session,
                    forgotten_through_revision=current.session.forgotten_through_revision,
                )
                stored = NumericV2StoredSession(
                    committed,
                    (*current.ledger_events, deepcopy(dict(ledger_event))),
                )
                self._validate_chain(stored)
                self._write(path, stored)
                return stored

            return await self._mutate(commit_turn)

    async def settle_narrative(
        self, expected: NumericV2StoredSession, record: Mapping[str, Any] | None,
    ) -> bool:
        """Settle only the pending tail; never advance or rewrite delivered story state."""
        path = self._path(expected.session.session_id)
        async with _lock(path):
            def settle() -> bool:
                if not path.is_file():
                    return False
                current = self._load_validated(path)
                if current is None or not current.ledger_events:
                    return False
                pending = current.ledger_events[-1].get("narrative_pending")
                if (not pending or pending != expected.ledger_events[-1].get("narrative_pending")
                        or current.session.revision != expected.session.revision
                        or current.session.lifecycle_revision != expected.session.lifecycle_revision
                        or current.session.status != expected.session.status
                        or current.session.forgotten_through_revision != expected.session.forgotten_through_revision
                        or pending["body"] != narrative_body_fingerprint(current.session.performance_history[-1])):
                    return False
                events = deepcopy(list(current.ledger_events))
                history = deepcopy(list(current.session.performance_history))
                events[-1].pop("narrative_pending")
                if record is not None:
                    events[-1]["narrative_record"] = deepcopy(dict(record))
                    history[-1]["narrative_record"] = deepcopy(dict(record))
                # Rebuild the derived projection from the durable sequence. Both
                # copies of the record and the Session move in one atomic write.
                state = None
                opening = current.session.opening_performance
                node = str(self.engine.story["start_node_id"])
                if "narrative_record" in opening:
                    state = apply_narrative_record(None, opening["narrative_record"], revision=0,
                        node_id=node, from_node_id=node, performance=opening, message="")
                for event, performance in zip(events, history):
                    if "narrative_record" in event:
                        state = apply_narrative_record(state, event["narrative_record"],
                            revision=event["result_revision"], node_id=event["to_node_id"],
                            from_node_id=event["from_node_id"], performance=performance,
                            message=event["input_text"])
                stored = NumericV2StoredSession(replace(current.session,
                    narrative_state=state, performance_history=tuple(history)), tuple(events))
                self._validate_chain(stored)
                self._write(path, stored)
                return True

            return await self._mutate(settle)

    async def end_session(
        self,
        session_id: str,
        *,
        base_revision: int,
        base_lifecycle_revision: int,
        reason: str,
    ) -> NumericV2StoredSession:
        path = self._path(session_id)
        async with _lock(path):
            def end() -> NumericV2StoredSession:
                if not path.is_file():
                    raise NumericV2SessionNotFoundError("numeric_session_not_found")
                current = self._read(path)
                # The script package may have been upgraded; the finish action changes only lifecycle state, but must still verify the persisted ledger is internally continuous,
                # but do not replay old story using new script rules, or users could become permanently stuck in performance state.
                self._validate_lifecycle_chain(current)
                if current.session.revision != base_revision:
                    raise NumericV2StoreRevisionConflictError("numeric_base_revision_mismatch")
                if current.session.status == "ended":
                    # Accept only a successful replay immediately associated with this request; a delayed request from an earlier lifecycle must conflict.
                    if (
                        current.session.ended_reason == str(reason or "user_exit")
                        and current.session.lifecycle_revision == base_lifecycle_revision + 1
                    ):
                        return current
                    raise NumericV2StoreRevisionConflictError("numeric_base_revision_mismatch")
                if current.session.lifecycle_revision != base_lifecycle_revision:
                    raise NumericV2StoreRevisionConflictError("numeric_base_revision_mismatch")
                ended = replace(
                    current.session,
                    status="ended",
                    ended_reason=str(reason or "user_exit"),
                    lifecycle_revision=current.session.lifecycle_revision + 1,
                )
                stored = NumericV2StoredSession(ended, current.ledger_events)
                self._write(path, stored)
                return stored

            return await self._mutate(end)

    async def resume_session(
        self,
        session_id: str,
        *,
        base_revision: int,
        base_lifecycle_revision: int,
    ) -> NumericV2StoredSession:
        """Restore a Session the player explicitly exited; a natural story ending remains non-resumable."""  # noqa: DOCSTRING_CJK

        path = self._path(session_id)
        async with _lock(path):
            def resume() -> NumericV2StoredSession:
                if not path.is_file():
                    raise NumericV2SessionNotFoundError("numeric_session_not_found")
                current = self._read(path)
                if current.session.revision != base_revision:
                    raise NumericV2StoreRevisionConflictError("numeric_base_revision_mismatch")
                if current.session.status == "active":
                    # The same request may be retried safely after a successful continue; an old request must not pass by relying on the current active state.
                    if current.session.lifecycle_revision == base_lifecycle_revision + 1:
                        return current
                    raise NumericV2StoreRevisionConflictError("numeric_base_revision_mismatch")
                if current.session.lifecycle_revision != base_lifecycle_revision:
                    raise NumericV2StoreRevisionConflictError("numeric_base_revision_mismatch")
                if current.session.status != "ended" or current.session.ended_reason != "user_exit":
                    raise NumericV2StoreError("numeric_session_not_resumable")
                resumed = NumericV2StoredSession(
                    replace(
                        current.session,
                        status="active",
                        ended_reason=None,
                        lifecycle_revision=current.session.lifecycle_revision + 1,
                    ),
                    current.ledger_events,
                )
                self._validate_chain(resumed)
                self._write(path, resumed)
                return resumed

            return await self._mutate(resume)

    async def forget_history_through_current_revision(
        self,
        session_id: str,
        *,
        through_revision: int | None = None,
    ) -> NumericV2StoredSession:
        """Record an explicit forgetting boundary to prevent old Session continuation from projecting forgotten content again."""  # noqa: DOCSTRING_CJK

        path = self._path(session_id)
        async with _lock(path):
            def forget() -> NumericV2StoredSession:
                if not path.is_file():
                    raise NumericV2SessionNotFoundError("numeric_session_not_found")
                current = self._read(path)
                boundary = current.session.revision if through_revision is None else through_revision
                if type(boundary) is not int or not 0 <= boundary <= current.session.revision:
                    raise NumericV2StoreError("numeric_forget_revision_invalid")
                if current.session.forgotten_through_revision >= boundary:
                    return current
                forgotten = NumericV2StoredSession(
                    replace(
                        current.session,
                        forgotten_through_revision=boundary,
                    ),
                    current.ledger_events,
                )
                from .numeric_v2_runtime import NumericV2RuntimeError
                try:
                    self._validate_chain(forgotten)
                except NumericV2RuntimeError as exc:
                    if str(exc) not in {"story_package_revision_mismatch", "story_package_hash_mismatch"}:
                        raise
                    # Package changes permit lifecycle writes, not new story turns.
                    self._validate_lifecycle_chain(forgotten)
                self._write(path, forgotten)
                return forgotten

            return await self._mutate(forget)


    def _read(self, path: Path) -> NumericV2StoredSession:
        from .numeric_v2_runtime import ScriptSessionV2, _player_address_disclosed

        try:
            payload = json.loads(
                _retry_windows_permission_error(lambda: path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise NumericV2StoreError("numeric_session_read_failed") from exc
        if not isinstance(payload, dict) or payload.get("schema") != STORE_SCHEMA:
            raise NumericV2StoreError("numeric_store_schema_invalid")
        raw_session = dict(payload.get("session") or {})
        if "player_address_known" not in raw_session:
            initial_state = self.engine.story.get("initial_state")
            initial_known = bool(
                initial_state.get("player_address_known")
                if isinstance(initial_state, Mapping)
                else False
            )
            if not initial_known:
                binding = raw_session.get("catgirl_binding")
                configured_address = (
                    str(binding.get("player_address") or "").strip()
                    if isinstance(binding, Mapping)
                    else ""
                )
                events = payload.get("ledger_events")
                initial_known = bool(
                    configured_address
                    and configured_address not in {"你", "男主"}
                    and isinstance(events, list)
                    and any(
                        _player_address_disclosed(
                            str(event.get("input_text") or ""),
                            configured_address,
                        )
                        for event in events
                        if isinstance(event, Mapping)
                    )
                )
            raw_session["player_address_known"] = initial_known
        session = ScriptSessionV2.from_mapping(raw_session)
        events = payload.get("ledger_events")
        if not isinstance(events, list) or any(not isinstance(item, dict) for item in events):
            raise NumericV2StoreError("numeric_ledger_invalid")
        return NumericV2StoredSession(session, tuple(deepcopy(events)))

    def _validate_lifecycle_chain(self, stored: NumericV2StoredSession) -> None:
        """Validate an archive chain independently of script content, only for lifecycle cleanup and startup audit."""  # noqa: DOCSTRING_CJK

        session = stored.session
        if session.story_package_id != self.engine.story_id:
            raise NumericV2StoreError("story_package_id_mismatch")
        if session.status not in {"active", "ended"}:
            raise NumericV2StoreError("session_status_invalid")
        if (
            session.node_turn_count < 0
            or session.revision < 0
            or session.lifecycle_revision < 0
            or not -1 <= session.forgotten_through_revision <= session.revision
        ):
            raise NumericV2StoreError("session_counter_invalid")

        events = stored.ledger_events
        if len(events) != session.revision:
            raise NumericV2StoreError("numeric_ledger_revision_mismatch")
        if len(session.performance_history) != len(events):
            raise NumericV2StoreError("numeric_performance_history_mismatch")

        expected_node: str | None = None
        expected_metrics: dict[str, int] | None = None
        seen_turns: set[str] = set()
        for event_index, event in enumerate(events):
            expected_revision = event_index + 1
            if (
                not isinstance(event, Mapping)
                or event.get("session_id") != session.session_id
                or event.get("base_revision") != expected_revision - 1
                or event.get("result_revision") != expected_revision
            ):
                raise NumericV2StoreError("numeric_ledger_revision_chain_invalid")

            from_node_id = str(event.get("from_node_id") or "")
            to_node_id = str(event.get("to_node_id") or "")
            if not from_node_id or not to_node_id:
                raise NumericV2StoreError("numeric_ledger_node_chain_invalid")
            if expected_node is not None and from_node_id != expected_node:
                raise NumericV2StoreError("numeric_ledger_node_chain_invalid")

            before_metrics = event.get("before_metrics")
            after_metrics = event.get("after_metrics")
            if (
                not isinstance(before_metrics, Mapping)
                or not isinstance(after_metrics, Mapping)
                or any(isinstance(value, bool) or not isinstance(value, int) for value in before_metrics.values())
                or any(isinstance(value, bool) or not isinstance(value, int) for value in after_metrics.values())
            ):
                raise NumericV2StoreError("numeric_ledger_metric_chain_invalid")
            if expected_metrics is not None and dict(before_metrics) != expected_metrics:
                raise NumericV2StoreError("numeric_ledger_metric_chain_invalid")

            turn_id = str(event.get("client_turn_id") or "")
            if not turn_id or turn_id in seen_turns:
                raise NumericV2StoreError("numeric_ledger_turn_id_invalid")
            performance = session.performance_history[event_index]
            if (
                not isinstance(performance, Mapping)
                or performance.get("client_turn_id") != turn_id
                or performance.get("revision") != expected_revision
                or performance.get("from_node_id") != from_node_id
                or performance.get("to_node_id") != to_node_id
            ):
                raise NumericV2StoreError("numeric_performance_record_mismatch")

            expected_node = to_node_id
            expected_metrics = dict(after_metrics)
            seen_turns.add(turn_id)

        if events and (
            expected_node != session.current_node_id
            or expected_metrics != session.metrics
        ):
            raise NumericV2StoreError("numeric_session_not_at_ledger_tail")
        if (
            seen_turns != set(session.processed_client_turn_ids)
            or len(seen_turns) != len(session.processed_client_turn_ids)
        ):
            raise NumericV2StoreError("numeric_processed_turn_ids_mismatch")

    def _validate_chain(self, stored: NumericV2StoredSession) -> None:
        from .numeric_v2_fixed_narration import validate_delivery

        self.engine.validate_session(stored.session)
        try:
            validate_delivery(self.engine.story, stored.session.opening_performance)
        except ValueError as exc:
            raise NumericV2StoreError("numeric_fixed_narration_invalid") from exc
        events = stored.ledger_events
        if len(events) != stored.session.revision:
            raise NumericV2StoreError("numeric_ledger_revision_mismatch")
        expected_revision = 0
        expected_node = str(self.engine.story["start_node_id"])
        expected_metrics = {str(key): int(value) for key, value in self.engine.story["initial_state"]["metrics"].items()}
        seen_turns: set[str] = set()
        from .numeric_v2_runtime import MetricChangeV2, TurnRequestV2

        replay_session = self.engine.create_session(
            session_id=stored.session.session_id,
            catgirl_binding=stored.session.catgirl_binding,
            opening_performance={key: value for key, value in stored.session.opening_performance.items()
                                 if key not in {"fixed_narrations", "narrative_record"}},
            actor_budget_profile=stored.session.actor_budget_profile,
        )
        # Delivery was validated above against its captured names. Renaming must
        # not rewrite already displayed text while replaying the ledger.
        replay_session = replace(replay_session, opening_performance=stored.session.opening_performance)
        if "narrative_record" in stored.session.opening_performance:
            try:
                narrative_state = apply_narrative_record(
                    None, stored.session.opening_performance["narrative_record"], revision=0,
                    node_id=expected_node, from_node_id=expected_node,
                    performance=stored.session.opening_performance, message="",
                )
            except ValueError as exc:
                raise NumericV2StoreError("numeric_narrative_replay_invalid") from exc
            replay_session = replace(replay_session, narrative_state=narrative_state)
        if len(stored.session.performance_history) != len(events):
            raise NumericV2StoreError("numeric_performance_history_mismatch")
        for event_index, event in enumerate(events):
            expected_revision += 1
            if not isinstance(event, Mapping):
                raise NumericV2StoreError("numeric_ledger_event_invalid")
            if event.get("base_revision") != expected_revision - 1 or event.get("result_revision") != expected_revision:
                raise NumericV2StoreError("numeric_ledger_revision_chain_invalid")
            if event.get("from_node_id") != expected_node:
                raise NumericV2StoreError("numeric_ledger_node_chain_invalid")
            if event.get("before_metrics") != expected_metrics:
                raise NumericV2StoreError("numeric_ledger_metric_chain_invalid")
            turn_id = str(event.get("client_turn_id") or "")
            if not turn_id or turn_id in seen_turns:
                raise NumericV2StoreError("numeric_ledger_turn_id_invalid")
            raw_changes = event.get("metric_changes")
            if not isinstance(raw_changes, list):
                raise NumericV2StoreError("numeric_ledger_metric_changes_invalid")
            if (event.get("transition_intent") == "accept"
                    and event.get("from_node_id") != event.get("to_node_id")):
                origin = pending_transition_record(
                    replay_session, ledger_events=tuple(events[:event_index]), include_withdrawn=True,
                )
                offered_event = next((row for row in events[:event_index]
                    if isinstance(origin, Mapping) and row.get("result_revision") == origin.get("revision")), None)
                receipt = offered_event.get("program_invitation") if isinstance(offered_event, Mapping) else None
                if ((isinstance(receipt, Mapping) and receipt.get("silent") is True)
                        or (isinstance(offered_event, Mapping)
                            and offered_event.get("transition_offer_source") == "program_silent")):
                    # Receipt-backed acceptances cannot masquerade as pre-feature records.
                    if (event.get("input_source") != "suggestion"
                            or event.get("source_transition_dialogue_policy") != "forbidden"):
                        raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            try:
                changes = tuple(
                    MetricChangeV2.from_mapping(
                        {
                            key: change.get(key)
                            for key in ("metric_id", "delta", "criterion", "evidence")
                        },
                        self.engine.metric_schema,
                    )
                    for change in raw_changes
                    if isinstance(change, Mapping)
                )
                if len(changes) != len(raw_changes):
                    raise ValueError("metric_change_shape")
                scene_complete = event.get("scene_complete")
                if not isinstance(scene_complete, bool):
                    raise ValueError("scene_complete_shape")
                # Replay must use the ending authorization recorded at the time; default this off for old Ledgers and never infer it from a completion signal.
                natural_ending_ready = event.get("natural_ending_ready", False)
                if not isinstance(natural_ending_ready, bool):
                    raise ValueError("natural_ending_ready_shape")
                # Record the conditional-narration scene-exit gate from the review setting at the time; default this on for old Ledgers.
                condition_narrations_enabled = event.get("condition_narrations_enabled", True)
                if not isinstance(condition_narrations_enabled, bool):
                    raise ValueError("condition_narrations_enabled_shape")
                request = TurnRequestV2.from_mapping(
                    {
                        "client_turn_id": turn_id,
                        "base_revision": event.get("base_revision"),
                        "message": event.get("input_text"),
                        "input_source": event.get("input_source", "freeform"),
                    }
                )
                replayed = self.engine.resolve_turn(
                    replay_session,
                    request,
                    changes,
                    scene_complete=scene_complete,
                    transition_intent=str(event.get("transition_intent") or "unclear"),
                    natural_ending_ready=natural_ending_ready,
                    ledger_events=tuple(events[:event_index]) if "accepted_offer_route_id" in event else (),
                    condition_narrations_enabled=condition_narrations_enabled,
                    fact_operations=tuple(
                        dict(operation)
                        for operation in event.get("fact_operations") or []
                        if isinstance(operation, Mapping)
                    ),
                )
                performance = stored.session.performance_history[event_index]
                if not isinstance(performance, Mapping):
                    raise ValueError("performance_record_shape")
                if "narrative_pending" in event:
                    pending = event["narrative_pending"]
                    if (event_index != len(events) - 1 or not isinstance(pending, Mapping)
                            or set(pending) != {"job_id", "body", "lifecycle_revision", "forgotten_through_revision"}
                            or not isinstance(pending["job_id"], str) or len(pending["job_id"]) != 32
                            or any(c not in "0123456789abcdef" for c in pending["job_id"])
                            or pending["body"] != narrative_body_fingerprint(performance)
                            or type(pending["lifecycle_revision"]) is not int
                            or not 0 <= pending["lifecycle_revision"] <= stored.session.lifecycle_revision
                            or type(pending["forgotten_through_revision"]) is not int
                            or not -1 <= pending["forgotten_through_revision"] <= stored.session.forgotten_through_revision
                            or event.get("narrative_record", {}).get("coverage") != "unrecorded"):
                        raise NumericV2StoreError("numeric_narrative_pending_invalid")
                validate_delivery(self.engine.story, performance, session=replay_session)
            except Exception as exc:
                raise NumericV2StoreError("numeric_ledger_replay_invalid") from exc
            expected_event = replayed.ledger_event
            for field in (
                "schema",
                "session_id",
                "client_turn_id",
                "base_revision",
                "result_revision",
                "from_node_id",
                "to_node_id",
                "route_id",
                "route_status",
                "scene_complete",
                "node_turn_count",
                "status",
                "before_metrics",
                "after_metrics",
                "metric_changes",
                "fact_operations",
                "accepted_offer_route_id",
            ):
                if event.get(field) != expected_event.get(field):
                    raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            replayed_session = replayed.session
            # Invitation withdrawal boundaries affect later acceptance decisions and must match same-turn history; old records default to no boundary.
            for value in (event, performance):
                if not isinstance(value.get("transition_offer_invalidated", False), bool):
                    raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            if event.get("transition_offer_invalidated", False) != performance.get("transition_offer_invalidated", False):
                raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            if expected_event.get("transition_offer_invalidated") is True and event.get("transition_offer_invalidated") is not True:
                raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            # A new invitation source marker must match visible body text from the same turn; absent marker means retain only the old invitation.
            for value in (event, performance):
                if not isinstance(value.get("transition_offer_presented", False), bool):
                    raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            if event.get("transition_offer_presented", False) != performance.get("transition_offer_presented", False):
                raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            if ("transition_offer_source" in event) != ("transition_offer_source" in performance):
                raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            if "transition_offer_source" in event:
                if (not isinstance(event["transition_offer_source"], str)
                        or event["transition_offer_source"] not in {"reviewed", "program_silent"}
                        or event["transition_offer_source"] != performance["transition_offer_source"]
                        or event.get("transition_offer_presented") is not True):
                    raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            if "transition_offered" in event:
                # Proposal status in a new Ledger comes from the Actor body at the same revision; replay reuses only the committed value.
                committed_transition_offered = event.get("transition_offered")
                if not isinstance(committed_transition_offered, bool):
                    raise NumericV2StoreError("numeric_ledger_replay_mismatch")
                if performance.get("transition_offered", False) != committed_transition_offered:
                    raise NumericV2StoreError("numeric_ledger_replay_mismatch")
                replayed_session = replace(
                    replayed_session,
                    transition_offered=committed_transition_offered,
                )
            disclosure_version = event.get("player_address_disclosure_version")
            if disclosure_version not in {None, 2}:
                raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            if disclosure_version == 2 and "player_address_known" not in event:
                raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            if "player_address_known" in event:
                committed_address_known = event.get("player_address_known")
                if not isinstance(committed_address_known, bool):
                    raise NumericV2StoreError("numeric_ledger_replay_mismatch")
                if committed_address_known != expected_event.get("player_address_known"):
                    if disclosure_version == 2 or committed_address_known is False:
                        raise NumericV2StoreError("numeric_ledger_replay_mismatch")
                    # Before the version field was added, any nickname occurrence was committed as informed; do not corrupt existing Sessions when tightening the rules.
                    replayed_session = replace(
                        replayed_session,
                        player_address_known=True,
                    )
            if ("source_transition_dialogue_policy" in expected_event
                    and "source_transition_dialogue_policy" not in event):
                raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            for field in (
                "transition_intent",
                "before_dialogue_policy",
                "source_transition_dialogue_policy",
                "performance_dialogue_policy",
                "dialogue_policy",
            ):
                if field in event and event.get(field) != expected_event.get(field):
                    raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            performance_contract_version = performance.get("performance_contract_version")
            if performance_contract_version not in {None, 1, 2, 3}:
                raise NumericV2StoreError("numeric_performance_record_invalid")
            if (
                performance.get("client_turn_id") != turn_id
                or performance.get("revision") != expected_revision
                or performance.get("from_node_id") != expected_event["from_node_id"]
                or performance.get("to_node_id") != expected_event["to_node_id"]
            ):
                raise NumericV2StoreError("numeric_performance_record_mismatch")
            if performance_contract_version == 2:
                if expected_event["from_node_id"] == expected_event["to_node_id"]:
                    if not valid_ordered_content(
                        performance,
                        require_narration=True,
                        require_dialogue=True,
                    ):
                        raise NumericV2StoreError("numeric_performance_record_invalid")
                else:
                    segments = performance.get("segments")
                    if (
                        not isinstance(segments, list)
                        or len(segments) != 3
                        or not all(isinstance(segment, Mapping) for segment in segments)
                        or [segment.get("phase") for segment in segments]
                        != ["source_response", "transition_bridge", "target_opening"]
                        or not valid_ordered_content(segments[0], require_dialogue=True)
                        or not valid_ordered_content(segments[1], require_narration=True)
                        or not valid_ordered_content(segments[2], require_narration=True)
                    ):
                        raise NumericV2StoreError("numeric_transition_performance_invalid")
            if performance_contract_version == 3:
                if expected_event["from_node_id"] == expected_event["to_node_id"]:
                    if (
                        not is_silent_invitation_control(event, performance)
                        and not valid_mixed_performance_policy(
                            performance,
                            str(
                                event.get("performance_dialogue_policy")
                                or replayed_session.dialogue_policy
                            ),
                        )
                    ):
                        raise NumericV2StoreError("numeric_performance_record_invalid")
                else:
                    segments = performance.get("segments")
                    if (
                        not isinstance(segments, list)
                        or len(segments) != 3
                        or not all(isinstance(segment, Mapping) for segment in segments)
                        or [segment.get("phase") for segment in segments]
                        != ["source_response", "transition_bridge", "target_opening"]
                        # Source narration is an optional version 3 field; older records without it must still restore unchanged.
                        or set(segments[0]).difference({"fixed_narrations"}) not in ({"phase", "performance"}, {"phase", "performance", "scene_narration"})
                        or ("scene_narration" in segments[0] and not valid_scene_narration(segments[0]))
                        or set(segments[1]) != {"phase", "scene_narration"}
                        or set(segments[2]).difference({"fixed_narrations"}) != {"phase", "scene_narration", "performance"}
                        or not valid_mixed_performance_policy(
                            segments[0],
                            transition_source_dialogue_policy(
                                str(event.get("source_transition_dialogue_policy") or replay_session.dialogue_policy)
                            ),
                        )
                        # Contract version 3 allows an empty deduplicated beat; the target opening must remain non-empty and be injected by Runtime.
                        or not valid_scene_narration(segments[1], allow_empty=True)
                        or not valid_scene_narration(segments[2])
                        or not valid_mixed_performance_policy(
                            segments[2], replayed_session.dialogue_policy
                        )
                    ):
                        raise NumericV2StoreError("numeric_transition_performance_invalid")
            if (
                expected_event["from_node_id"] != expected_event["to_node_id"]
                and performance_contract_version in {1, 2, 3}
            ):
                segments = performance.get("segments")
                if (
                    performance.get("transition_delivered") is not True
                    or performance.get("visible_node_id") != expected_event["to_node_id"]
                    or not isinstance(segments, list)
                    or [item.get("phase") for item in segments if isinstance(item, Mapping)]
                    != ["source_response", "transition_bridge", "target_opening"]
                ):
                    raise NumericV2StoreError("numeric_transition_performance_invalid")
            if ("narrative_record" in event) != ("narrative_record" in performance):
                raise NumericV2StoreError("numeric_narrative_record_mismatch")
            if "narrative_record" in event:
                if event["narrative_record"] != performance["narrative_record"]:
                    raise NumericV2StoreError("numeric_narrative_record_mismatch")
                try:
                    narrative_state = apply_narrative_record(
                        replay_session.narrative_state, event["narrative_record"],
                        revision=expected_revision, node_id=str(expected_event["to_node_id"]),
                        from_node_id=str(expected_event["from_node_id"]),
                        performance=performance, message=request.message,
                    )
                except ValueError as exc:
                    raise NumericV2StoreError("numeric_narrative_replay_invalid") from exc
                replayed_session = replace(replayed_session, narrative_state=narrative_state)
            # Restore the committed body history during replay so it remains available to later context.
            replay_session = replace(
                replayed_session,
                performance_history=(*replayed_session.performance_history, deepcopy(dict(performance))),
            )
            expected_node = str(expected_event["to_node_id"])
            expected_metrics = dict(expected_event["after_metrics"])
            seen_turns.add(turn_id)
        if expected_node != stored.session.current_node_id or expected_metrics != stored.session.metrics:
            raise NumericV2StoreError("numeric_session_not_at_ledger_tail")
        if seen_turns != set(stored.session.processed_client_turn_ids):
            raise NumericV2StoreError("numeric_processed_turn_ids_mismatch")
        if not (
            replay_session.status == stored.session.status
            or (
                replay_session.status == "active"
                and stored.session.status == "ended"
                and stored.session.ended_reason
            )
        ):
            raise NumericV2StoreError("numeric_session_status_mismatch")
        if replay_session.revision != stored.session.revision:
            raise NumericV2StoreError("numeric_session_revision_mismatch")
        if (
            replay_session.player_address_known != stored.session.player_address_known
            or replay_session.dialogue_policy != stored.session.dialogue_policy
            or replay_session.transition_offered != stored.session.transition_offered
            or replay_session.story_state != stored.session.story_state
            or replay_session.narrative_state != stored.session.narrative_state
        ):
            raise NumericV2StoreError("numeric_session_scene_progress_mismatch")

    def _write(self, path: Path, stored: NumericV2StoredSession, *, exclusive: bool = False) -> None:
        payload = {
            "schema": STORE_SCHEMA,
            "session": stored.session.to_dict(),
            "ledger_events": deepcopy(list(stored.ledger_events)),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp", delete=False) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            if exclusive:
                try:
                    # Use the registry's publication pattern even without hard
                    # links: readers only see complete JSON and creators cannot
                    # overwrite each other. Keep the lock inode for all waiters.
                    with portalocker.Lock(str(path.parent / ".creates.lock"), mode="a", timeout=10):
                        if os.path.lexists(path):
                            raise NumericV2SessionExistsError("numeric_session_exists")
                        _retry_windows_permission_error(lambda: os.replace(temporary_path, path))
                        temporary_path = None
                except portalocker.exceptions.LockException as exc:
                    raise NumericV2StoreError("numeric_session_create_failed") from exc
            else:
                _retry_windows_permission_error(lambda: os.replace(temporary_path, path))
                temporary_path = None
        finally:
            discard_temporary_file(temporary_path)


__all__ = [
    "delete_numeric_v2_sessions",
    "list_numeric_v2_public_archives",
    "list_numeric_v2_sessions",
    "numeric_v2_story_session_guard",
    "update_numeric_v2_character_bindings",
    "NumericV2SessionExistsError",
    "NumericV2SessionNotFoundError",
    "NumericV2SessionStore",
    "NumericV2StoreError",
    "NumericV2StoreRevisionConflictError",
    "NumericV2StoredSession",
]
