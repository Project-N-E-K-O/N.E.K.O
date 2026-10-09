"""Local, CAS-protected recommendation state; no chat or memory ownership.

All filesystem work runs off the event loop. A writer keeps its OS lock until
physical workers have stopped; cancellation never releases a live writer.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import stat
import threading
import uuid
from pathlib import Path
from typing import Callable

from config.topic_recommendation_settings import TopicRecommendationSettings
from utils.storage.reparse import is_name_surrogate
from .contracts import RecommendationError, empty_state, validate_state

MAX_STATE_BYTES = TopicRecommendationSettings().max_state_bytes
_CHARACTER_ID = re.compile(r"character_[0-9a-f]{32}\Z")


def _real_directory(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISDIR(info.st_mode) and not (
        stat.S_ISLNK(info.st_mode) or
        is_name_surrogate(info)
    )


def _safe_file(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or is_name_surrogate(info):
        raise RecommendationError("store_unavailable")


class RecommendationStore:
    """One application-owned writer, with a fixed runtime-root incarnation.

    ``root_provider`` must return the actual selected runtime root, never an
    anchor fallback. ``root_guard`` verifies startup/recovery/maintenance state.
    It is called on the worker thread, including immediately before replace.
    """

    def __init__(self, root_provider: Callable[[], Path], root_guard: Callable[[], bool | None] | None = None,
                 settings: TopicRecommendationSettings | None = None):
        self._root_provider = root_provider
        self._root_guard = root_guard
        self.settings = settings or TopicRecommendationSettings()
        self._root_generation = uuid.uuid4().hex
        self._root: Path | None = None
        self._missing: dict[str, dict] = {}
        self._deleted: set[str] = set()
        self._thread_lock = threading.RLock()
        self._lock_file = None
        self._closing = threading.Event()
        self._paused = threading.Event()
        self._operations: set[asyncio.Task] = set()

    @property
    def root_generation(self) -> str:
        return self._root_generation

    @classmethod
    def for_config_manager(cls, config_manager, settings: TopicRecommendationSettings | None = None) -> "RecommendationStore":
        """Bind the existing committed root and strict startup/write fence.

        Direct reads deliberately avoid the manager's missing-file defaults
        and directory-creation helpers. A missing/corrupt fence is unknown,
        not evidence that startup has completed or writes are permitted.
        """
        def selected_root() -> Path:
            return Path(config_manager.committed_selected_root)

        def check_root() -> bool:
            if (config_manager.recovery_committed_root_unavailable or
                    config_manager.recovery_committed_root_unavailable_override):
                raise RecommendationError("store_unavailable")
            selected = Path(os.path.abspath(selected_root()))
            runtime = Path(os.path.abspath(config_manager.app_docs_dir))
            if os.path.normcase(str(selected)) != os.path.normcase(str(runtime)):
                raise RecommendationError("store_unavailable")
            root_state_path = Path(config_manager.root_state_path)
            for directory in (root_state_path.parent, *root_state_path.parent.parents):
                if not _real_directory(directory):
                    raise RecommendationError("store_unavailable")
            _safe_file(root_state_path)
            try:
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                with os.fdopen(os.open(root_state_path, flags), "rb") as source:
                    raw = source.read(64 * 1024 + 1)
                if len(raw) > 64 * 1024:
                    raise RecommendationError("store_unavailable")
                fence = json.loads(raw)
                if (not isinstance(fence, dict) or
                        type(fence.get("version")) is not int or
                        fence.get("version") != config_manager.ROOT_STATE_VERSION or
                        not isinstance(fence.get("current_root"), str) or
                        not Path(fence["current_root"]).is_absolute()):
                    raise RecommendationError("store_unavailable")
                if fence.get("mode") != "normal":
                    raise RecommendationError("maintenance")
                actual = Path(os.path.abspath(fence["current_root"]))
                if os.path.normcase(str(actual)) != os.path.normcase(str(selected)):
                    raise RecommendationError("store_unavailable")
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RecommendationError("store_unavailable") from exc
            return True

        return cls(selected_root, check_root, settings)

    def _check(self, guard: Callable[[], bool] | None = None) -> Path:
        if self._closing.is_set():
            raise RecommendationError("store_unavailable")
        if guard is not None and not guard():
            raise RecommendationError("stale_operation")
        if self._root_guard is not None and self._root_guard() is False:
            raise RecommendationError("store_unavailable")
        root = Path(os.path.abspath(self._root_provider()))
        if not _real_directory(root):
            raise RecommendationError("store_unavailable")
        # Check the full parent chain, including Windows junctions. Resolve
        # only after inspection so a link cannot be hidden by normalization.
        for parent in root.parents:
            if not _real_directory(parent):
                raise RecommendationError("store_unavailable")
        if self._root is None:
            self._root = root
        elif os.path.normcase(str(root)) != os.path.normcase(str(self._root)):
            raise RecommendationError("store_unavailable")
        return root

    def _path(self, character_id: str, *, create: bool = False, guard=None) -> Path:
        if not isinstance(character_id, str) or not _CHARACTER_ID.fullmatch(character_id):
            raise RecommendationError("invalid_character_id")
        if character_id in self._deleted:
            raise RecommendationError("character_deleted")
        root = self._check(guard)
        directory = root
        for part in ("state", "recommendation", character_id):
            directory = directory / part
            try:
                if not _real_directory(directory):
                    raise RecommendationError("store_unavailable")
            except FileNotFoundError:
                if create:
                    directory.mkdir(exist_ok=True)
                    if not _real_directory(directory):
                        raise RecommendationError("store_unavailable")
        path = directory / "state.json"
        _safe_file(path)
        return path

    def _claim_writer(self, directory: Path) -> None:
        if self._lock_file is not None:
            return
        lock_path = directory / ".writer.lock"
        _safe_file(lock_path)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(descriptor).st_size == 0:
                    handle.write(b"\0")
                handle.seek(0)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as exc:
            handle.close()
            raise RecommendationError("writer_unavailable") from exc
        self._lock_file = handle

    def _read(self, character_id: str) -> dict:
        path = self._path(character_id)
        try:
            if path.stat().st_size > self.settings.max_state_bytes:
                raise RecommendationError("capacity_exhausted")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(path, flags), "rb") as source:
                raw = source.read(self.settings.max_state_bytes + 1)
            if len(raw) > self.settings.max_state_bytes:
                raise RecommendationError("capacity_exhausted")
            state = json.loads(raw)
            validate_state(state, self.settings)
            if state["character_id"] != character_id:
                raise RecommendationError("state_corrupt")
            self._missing.pop(character_id, None)
            return state
        except FileNotFoundError:
            return self._missing.setdefault(character_id, empty_state(character_id))
        except (UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, RecommendationError):
                raise
            raise RecommendationError("state_corrupt") from exc

    def _clean_role_temporaries(self, path: Path, character_id: str, guard) -> None:
        """Retire only this writer's known crash leftovers, never arbitrary files."""
        if not path.parent.exists():
            return
        pattern = re.compile(rf"\.{re.escape(character_id)}\.[0-9a-f]{{32}}\.tmp\Z")
        for child in path.parent.iterdir():
            if pattern.fullmatch(child.name):
                self._path(character_id, guard=guard)
                _safe_file(child)
                child.unlink(missing_ok=True)

    def _replace(self, character_id: str, state: dict, guard) -> dict:
        validate_state(state, self.settings)
        raw = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > self.settings.max_state_bytes:
            raise RecommendationError("capacity_exhausted")
        path = self._path(character_id, create=True, guard=guard)
        self._claim_writer(path.parent.parent)
        temporary = path.parent / f".{character_id}.{uuid.uuid4().hex}.tmp"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                target.write(raw)
                target.flush()
                os.fsync(target.fileno())
            # Includes cancellation, controls, deletion, root and maintenance.
            # There is no await between this final check and atomic replace.
            self._path(character_id, guard=guard)
            os.replace(temporary, path)
            self._missing.pop(character_id, None)
            return copy.deepcopy(state)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    async def _run(self, function, *args, guard=None, allow_paused=False):
        if self._closing.is_set() or (self._paused.is_set() and not allow_paused):
            raise RecommendationError("store_unavailable")
        cancelled = threading.Event()

        def owned_guard():
            return (not cancelled.is_set() and (allow_paused or not self._paused.is_set())
                    and (guard is None or guard()))

        def execute():
            with self._thread_lock:
                try:
                    self._check(owned_guard)
                    return function(*args, guard=owned_guard)
                except OSError as exc:
                    raise RecommendationError("store_unavailable") from exc

        task = asyncio.create_task(asyncio.to_thread(execute))
        self._operations.add(task)
        task.add_done_callback(self._operations.discard)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            # The physical worker remains registered and retains ownership.
            # close joins it; a second operation is serialized by thread lock.
            def consume_result(done):
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(consume_result)
            raise

    async def load(self, character_id: str) -> dict:
        def load_owned(identifier, *, guard):
            self._check(guard)
            return copy.deepcopy(self._read(identifier))
        return await self._run(load_owned, character_id)

    async def root_ready(self) -> bool:
        """Strict, side-effect-free fence refresh for lifecycle adapters."""
        def check_owned(*, guard):
            self._check(guard)
            return True
        try:
            return await self._run(check_owned, allow_paused=True)
        except RecommendationError:
            return False

    async def commit(self, character_id: str, state: dict, *, expected_epoch: str, expected_revision: int, guard=None) -> dict:
        proposed = copy.deepcopy(state)

        def commit_owned(identifier, *, guard):
            path = self._path(identifier, create=True, guard=guard)
            self._claim_writer(path.parent.parent)
            current = self._read(identifier)
            if current["state_epoch"] != expected_epoch:
                raise RecommendationError("epoch_conflict")
            if current["revision"] != expected_revision:
                raise RecommendationError("revision_conflict")
            if proposed.get("character_id") != identifier or proposed.get("state_epoch") != expected_epoch:
                raise RecommendationError("state_corrupt")
            proposed["revision"] = expected_revision + 1
            return self._replace(identifier, proposed, guard)
        return await self._run(commit_owned, character_id, guard=guard)

    async def reset(self, character_id: str, *, expected_epoch: str, request_id: str, guard=None, preserve_profile: bool = False,
                    expected_confirmation: str | None = None, expected_revision: int | None = None, confirm_if=None) -> dict:
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", request_id):
            raise RecommendationError("invalid_request")

        def reset_owned(identifier, *, guard):
            path = self._path(identifier, create=True, guard=guard)
            self._claim_writer(path.parent.parent)
            current = self._read(identifier)
            for accepted in current.get("reset_requests", []):
                if accepted.get("request_id") == request_id:
                    if accepted.get("expected_epoch") != expected_epoch or accepted.get("preserve_profile", False) != preserve_profile:
                        raise RecommendationError("epoch_conflict")
                    if expected_confirmation is not None and accepted.get("expected_confirmation") != expected_confirmation:
                        raise RecommendationError("epoch_conflict")
                    self._clean_role_temporaries(path, identifier, guard)
                    return copy.deepcopy(accepted)
            if current["state_epoch"] != expected_epoch:
                raise RecommendationError("epoch_conflict")
            if expected_revision is not None and current["revision"] != expected_revision:
                raise RecommendationError("revision_conflict")
            original_guard = guard
            guard = lambda: original_guard() and (confirm_if is None or confirm_if())
            if not guard():
                raise RecommendationError("stale_operation")
            self._clean_role_temporaries(path, identifier, guard)
            reset = empty_state(identifier)
            if preserve_profile:
                for key in ("subjects", "interests", "restrictions", "deliveries"):
                    reset[key] = copy.deepcopy(current[key])
                for subject in reset["subjects"]:
                    subject["context_confirmed"] = False
            reset["revision"] = current["revision"] + 1
            receipt = {"request_id": request_id, "expected_epoch": expected_epoch,
                       "state_epoch": reset["state_epoch"], "revision": reset["revision"]}
            if preserve_profile:
                receipt["preserve_profile"] = True
            if expected_confirmation is not None:
                receipt["expected_confirmation"] = expected_confirmation
            reset["reset_requests"] = [*current.get("reset_requests", [])[-15:], receipt]
            self._replace(identifier, reset, guard)
            return copy.deepcopy(receipt)
        return await self._run(reset_owned, character_id, guard=guard)

    async def delete(self, character_id: str, *, guard=None) -> None:
        def delete_owned(identifier, *, guard):
            path = self._path(identifier, guard=guard)
            if path.parent.exists():
                self._claim_writer(path.parent.parent)
                self._clean_role_temporaries(path, identifier, guard)
            self._check(guard)
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass
            self._deleted.add(identifier)
            self._missing.pop(identifier, None)
        await self._run(delete_owned, character_id, guard=guard)

    def suspend(self) -> None:
        """Fence new operations before a root transaction, including queued writes."""
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    async def wait_idle(self, *, deadline: float) -> None:
        """Join actual physical work without consulting the unavailable root."""
        while self._operations:
            pending = tuple(self._operations)
            _, unfinished = await asyncio.wait(pending, timeout=max(0, deadline - asyncio.get_running_loop().time()))
            if unfinished:
                raise RecommendationError("closing_timeout")
            self._operations.difference_update(pending)

    async def close(self, *, deadline: float | None = None) -> None:
        self._closing.set()
        if deadline is None:
            deadline = asyncio.get_running_loop().time() + self.settings.close_timeout
        # Never hand off the OS writer lock while a to_thread replace is alive.
        await self.wait_idle(deadline=deadline)
        def release():
            with self._thread_lock:
                if self._lock_file is not None:
                    self._lock_file.close()
                    self._lock_file = None
        async with asyncio.timeout_at(deadline):
            await asyncio.to_thread(release)
