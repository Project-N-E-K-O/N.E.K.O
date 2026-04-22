"""Per-session snapshot timeline + rewind machinery (P18).

Testbench philosophy: every destructive action (chat send / message edit /
memory commit / stage advance / persona update / script load / auto-dialog
start) should be reversible. Each such action lands a new entry in a
**linear timeline** so the user can later `rewind_to(snapshot_id)` and get
the whole session (messages + sandbox memory files + model_config +
stage_state + eval_results + persona + virtual clock) back to that point.

Design decisions (see also PLAN.md §13 "快照与回退"):

1. **Per-session, not singleton.**  Each live :class:`Session` holds one
   :class:`SnapshotStore`. Destroying the session (re-create / reset)
   drops the store so there's no cross-session leakage.

2. **Hot + cold two-tier storage.**  The most recent ``max_hot`` snapshots
   (default 30) live fully hydrated in Python memory. Older snapshots are
   compressed to ``<sandbox>/.snapshots/<id>.json.gz`` and only their
   metadata stays in RAM. Reading a cold snapshot lazily decompresses it;
   the caller sees the same :class:`Snapshot` dataclass either way.

3. **JSON + gzip, not pickle.**  Pickle is smaller on disk but opaque to
   P21 persistence (saved_sessions/*.json) which wants a stable schema.
   JSON with base64'd memory_files gives the same format for both hot
   (in-memory deep-copy) and cold (disk), and plays nicely with future
   schema_version migrations.

4. **5-second per-trigger debounce.**  If the user fires three
   ``/chat/send`` calls within 5 seconds and each one calls
   ``capture(trigger="send")``, we only keep the *latest* — middle
   captures are overwritten in place. This keeps the timeline readable
   ("7 sends" without 7 near-identical entries) without losing the final
   state. Different triggers never merge (a ``send`` followed 2s later
   by a ``memory_op`` yields two entries).

   Triggers that **never** debounce: ``init``, ``manual`` (user pressed
   the [+ 手动建快照] button explicitly), ``pre_rewind_backup``
   (automatic safety net — must always be kept).

5. **Rewind truncates forward history.**  Rewinding to snapshot S drops
   every entry *created after S* except ``pre_rewind_backup`` entries
   (which are safety nets, not real timeline nodes). Before truncating,
   we capture a fresh ``pre_rewind_backup`` of the current live state so
   the user can undo an accidental rewind. Matches PLAN §13.4 step 1.

6. **Sandbox memory files are part of the snapshot.**  Three-layer memory
   (recent / facts / reflections / persona) lives as JSON files under
   ``cm.memory_dir``. We walk that tree on every capture, read each file
   as bytes, and store ``{relpath: bytes}``. Rewind rewrites the whole
   directory: rmtree → mkdir → write each file back. This way rewind
   undoes any file-level writes made since the snapshot, not just
   in-memory fields.

7. **No session_store coupling.**  This module only knows about the
   :class:`Session` shape (reads ``.messages`` / ``.stage_state`` etc.);
   it does not import ``session_store`` to avoid a circular dep and to
   keep snapshot logic unit-testable without the lifecycle machinery.
"""
from __future__ import annotations

import base64
import copy
import gc
import gzip
import json
import os
import shutil
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from tests.testbench.logger import python_logger

if TYPE_CHECKING:  # pragma: no cover — forward refs only
    from tests.testbench.session_store import Session


# ── Tunables (Settings page can override later) ─────────────────────

#: Default max number of snapshots kept hot in memory. Older ones spill
#: to disk.  Settings → UI will let the user raise this; capping at 30
#: keeps memory ~30MB even with ~1MB memory dirs.
DEFAULT_MAX_HOT = 30

#: Per-trigger debounce window.  Captures of the **same** trigger landing
#: within this many seconds are merged (newer overwrites older in place).
DEFAULT_DEBOUNCE_SECONDS = 5.0

#: Hard cap on a single file we'll pull into a snapshot (prevents a
#: rogue 100MB file in memory_dir from exploding the whole timeline).
_MAX_MEMORY_FILE_BYTES = 10 * 1024 * 1024  # 10 MiB per-file

#: Triggers that ignore debounce — they're either init boundaries or
#: explicit user intent that we must preserve verbatim.
_NO_DEBOUNCE_TRIGGERS: frozenset[str] = frozenset({
    "init",
    "manual",
    "pre_rewind_backup",
    "pre_reset_backup",     # P20: safety net auto-created by /api/session/reset
})

#: Whitelist of triggers we accept. Anything outside this set is a
#: programming error (routers mistyping the trigger name); we log a
#: warning and still accept so capture never crashes a business path.
_KNOWN_TRIGGERS: frozenset[str] = frozenset({
    "init",
    "manual",
    "send",                 # /chat/send completed (user turn + assistant reply)
    "edit",                 # PUT or DELETE on a message
    "memory_op",            # /memory/commit/<op> succeeded
    "stage_advance",        # /api/stage/advance or /skip
    "stage_rewind",         # /api/stage/rewind (stage-only, not snapshot rewind)
    "persona_update",       # PUT /api/persona
    "script_load",          # /chat/script/load
    "script_run_all",       # /chat/script/run_all completed
    "auto_dialog_start",    # /chat/auto/start (beginning of a run)
    "pre_rewind_backup",    # safety net auto-created by rewind_to()
    "pre_reset_backup",     # safety net auto-created by soft/medium/hard reset (P20)
})


# ── Dataclasses ─────────────────────────────────────────────────────


@dataclass
class Snapshot:
    """One point-in-time snapshot of a session.

    ``messages`` / ``memory_files`` / ``model_config`` etc. are always
    **independent copies** — never aliased to the live session. Callers
    may freely mutate a Snapshot returned by :meth:`SnapshotStore.get`
    without corrupting the timeline.
    """

    # Timeline identity.
    id: str
    created_at: datetime              # real wall-clock time
    virtual_now: datetime | None      # virtual clock cursor at capture time
    label: str                        # "t0:init" / user-chosen / auto-derived
    trigger: str                      # see _KNOWN_TRIGGERS

    # Payload — deep copies at capture time.
    messages: list[dict[str, Any]] = field(default_factory=list)
    memory_files: dict[str, bytes] = field(default_factory=dict)
    model_config: dict[str, Any] = field(default_factory=dict)
    stage_state: dict[str, Any] = field(default_factory=dict)
    eval_results: list[dict[str, Any]] = field(default_factory=list)
    persona: dict[str, Any] = field(default_factory=dict)
    clock_override: dict[str, Any] | None = None

    # Flags.
    is_backup: bool = False           # True = pre_rewind_backup safety net

    def metadata(self, *, is_compressed: bool = False) -> dict[str, Any]:
        """Small JSON-safe dict for timeline listings.

        Does NOT include ``messages`` / ``memory_files`` / etc. — those
        are big and should be fetched explicitly via
        :meth:`SnapshotStore.get`. UI lists use this summary view.
        """
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(timespec="seconds"),
            "virtual_now": self.virtual_now.isoformat(timespec="seconds") if self.virtual_now else None,
            "label": self.label,
            "trigger": self.trigger,
            "is_backup": self.is_backup,
            "is_compressed": is_compressed,
            "message_count": len(self.messages),
            "memory_file_count": len(self.memory_files),
            "eval_count": len(self.eval_results),
            "stage": (self.stage_state or {}).get("current"),
        }

    # ── (de)serialization for overflow-to-disk + P21 persistence ────

    def to_json_dict(self) -> dict[str, Any]:
        """Self-describing JSON dict — bytes encoded via base64.

        Used by the overflow-to-disk path and (future P21) by the
        saved_sessions exporter. Memory files keep their relative path
        as the key so rewind can reconstruct the directory tree without
        ambiguity.
        """
        return {
            "schema_version": 1,
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "virtual_now": self.virtual_now.isoformat() if self.virtual_now else None,
            "label": self.label,
            "trigger": self.trigger,
            "is_backup": self.is_backup,
            "messages": self.messages,
            "memory_files": {
                relpath: base64.b64encode(content).decode("ascii")
                for relpath, content in self.memory_files.items()
            },
            "model_config": self.model_config,
            "stage_state": self.stage_state,
            "eval_results": self.eval_results,
            "persona": self.persona,
            "clock_override": self.clock_override,
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "Snapshot":
        """Inverse of :meth:`to_json_dict`. Tolerant to missing fields."""
        return cls(
            id=str(data["id"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            virtual_now=(
                datetime.fromisoformat(data["virtual_now"])
                if data.get("virtual_now") else None
            ),
            label=str(data.get("label") or ""),
            trigger=str(data.get("trigger") or "unknown"),
            is_backup=bool(data.get("is_backup", False)),
            messages=list(data.get("messages") or []),
            memory_files={
                relpath: base64.b64decode(b64)
                for relpath, b64 in (data.get("memory_files") or {}).items()
            },
            model_config=dict(data.get("model_config") or {}),
            stage_state=dict(data.get("stage_state") or {}),
            eval_results=list(data.get("eval_results") or []),
            persona=dict(data.get("persona") or {}),
            clock_override=data.get("clock_override"),
        )


# ── Internal helpers ────────────────────────────────────────────────


def _walk_memory_files(memory_dir: Path) -> dict[str, bytes]:
    """Recursively read all files under ``memory_dir`` into ``{relpath: bytes}``.

    * Uses forward-slash relpath keys for cross-OS portability (Windows
      sandboxes must restore to POSIX-style paths on reload, and vice
      versa — PLAN §P21).
    * Skips files bigger than :data:`_MAX_MEMORY_FILE_BYTES`; logs a
      warning but does not abort the capture.
    * Returns an empty dict if the directory does not exist (freshly
      created session before any memory write).
    """
    out: dict[str, bytes] = {}
    if not memory_dir.exists():
        return out
    for path in memory_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            python_logger().warning(
                "snapshot_store: stat failed on %s (%s); skipping", path, exc,
            )
            continue
        if size > _MAX_MEMORY_FILE_BYTES:
            python_logger().warning(
                "snapshot_store: file %s is %d bytes > cap %d; skipping to keep "
                "snapshot size sane", path, size, _MAX_MEMORY_FILE_BYTES,
            )
            continue
        try:
            content = path.read_bytes()
        except OSError as exc:
            python_logger().warning(
                "snapshot_store: read_bytes failed on %s (%s); skipping",
                path, exc,
            )
            continue
        relpath = path.relative_to(memory_dir).as_posix()
        out[relpath] = content
    return out


class RewindFileLockedError(RuntimeError):
    """Raised when rewind / reset can't clear the sandbox because a file
    is locked by another process (commonly SQLite ``time_indexed.db``
    opened by DB Browser or held by a not-yet-GC'd SQLAlchemy engine).

    The router translates this into HTTP 409 with a user-friendly
    "close these files and retry" detail, *not* a 500 stack dump. This
    error is recoverable: the user can retry after closing the holder.
    """

    def __init__(
        self,
        *,
        memory_dir: Path,
        locked_files: list[Path],
        rename_error: Exception | None = None,
    ) -> None:
        self.memory_dir = memory_dir
        self.locked_files = locked_files
        self.rename_error = rename_error
        names = ", ".join(str(p.name) for p in locked_files[:5])
        super().__init__(
            f"Cannot clear memory dir {memory_dir}: {len(locked_files)} file(s) "
            f"locked (commonly .db handles). Affected: {names}. Close any "
            f"external tools opening these files and retry.",
        )


def _dispose_all_sqlalchemy_caches() -> None:
    """Close every SQLAlchemy engine our process might still be holding
    open on ``time_indexed.db`` and friends before a destructive rmtree.

    Why this is necessary:

    ``utils/llm_client.py::SQLChatMessageHistory`` keeps a **class-level**
    ``_engine_cache: dict[connection_string, Engine]`` that is never
    released by normal instance GC. Every ``TimeIndexedMemory._ensure_
    tables_exist_with`` call (triggered by the first Prompt Preview /
    chat.send for a character) inserts an engine there and that engine
    keeps an OS-level handle on the SQLite file open — so Windows'
    rmtree during rewind/reset fails with WinError 32 even after our
    own ``TimeIndexedMemory.cleanup()`` closes its own copy.

    Also disposes the engines held by any live :class:`TimeIndexedMemory`
    instances that haven't been GC'd yet. Best-effort — import errors
    or dispose failures are logged but never raise.

    This should be called immediately before any rewind/reset rmtree
    path. After the dispose, ``gc.collect`` in :func:`robust_rmtree`
    finishes the job (Python refcount drops, SQLite releases the file
    handle, Windows allows unlink).
    """
    # Clear the class-level engine cache held by the upstream
    # SQLChatMessageHistory — this is the main offender.
    try:
        from utils.llm_client import SQLChatMessageHistory
        cache = getattr(SQLChatMessageHistory, "_engine_cache", None)
        if isinstance(cache, dict):
            for key, engine in list(cache.items()):
                try:
                    engine.dispose()
                except Exception as exc:  # noqa: BLE001
                    python_logger().warning(
                        "snapshot_store: dispose cached engine %r failed (%s)",
                        key, exc,
                    )
            cache.clear()
    except ImportError:
        pass  # upstream class moved; no-op is fine.
    except Exception as exc:  # noqa: BLE001
        python_logger().warning(
            "snapshot_store: clearing SQLChatMessageHistory cache failed (%s)",
            exc,
        )


def robust_rmtree(target: Path, *, max_retries: int = 5, sleep_ms: int = 120) -> list[Path]:
    """Robust rmtree with Windows file-lock retry.

    Return list of files that **still** couldn't be removed after all
    retries (empty list on full success). Callers that need the removal
    to succeed strictly should check the return value and surface a
    helpful error message.

    The three reasons ``shutil.rmtree`` fails on Windows that we care
    about here:

      1. **File held open by our own process** (SQLAlchemy engine for
         ``time_indexed.db``, FTS5 helper etc.). These are Python-level
         references that will be freed on ``gc.collect()`` once the
         holding object goes out of scope. We retry after a gc pass.

      2. **Read-only bit set** (happens if the process that wrote the
         file crashed mid-way). ``onexc`` handler strips the readonly
         bit then retries the unlink.

      3. **Genuinely locked by another OS process** (user opened the
         .db in DB Browser for SQLite, etc.). No retry can rescue this;
         the surviving file list is returned so the caller can tell the
         user which file to close.

    We deliberately do NOT use ``ignore_errors=True`` — that would
    silently pretend a rewind worked when a crucial file (e.g.
    ``persona.json``) was still there, producing corrupt state post-
    rewind.
    """
    if not target.exists():
        return []

    leftovers: list[Path] = []

    def _onerror(func, path, _exc_info):
        # Strip read-only bit and retry once in-place. If still fails,
        # the outer retry loop will pick it up on the next iteration.
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            # Collect as leftover; outer loop will retry after gc.
            leftovers.append(Path(path))

    for attempt in range(max_retries):
        leftovers.clear()
        # Best-effort: free Python-level refs that may still hold file
        # handles (SQLAlchemy engines that haven't been dispose()d, etc.)
        # On the second attempt, also clear the upstream's class-level
        # engine cache — that's usually the hidden offender on Windows
        # when time_indexed.db stays locked across a rewind.
        if attempt >= 1:
            _dispose_all_sqlalchemy_caches()
        gc.collect()
        try:
            shutil.rmtree(target, onerror=_onerror)
        except FileNotFoundError:
            # Concurrent cleanup already removed it; that's fine.
            return []
        # If the target is gone entirely, we succeeded.
        if not target.exists():
            return []
        # Still files inside → list them fresh, sleep, retry.
        remaining = [p for p in target.rglob("*") if p.is_file()]
        if not remaining:
            # Only empty dirs left — try one more rmtree then bail.
            try:
                shutil.rmtree(target, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
            if not target.exists():
                return []
            return []
        if attempt == max_retries - 1:
            # Last try failed; return what's left so the caller can
            # surface a precise "which file is locked" message.
            return remaining
        time.sleep(sleep_ms / 1000)
    return []


def _restore_memory_files(
    memory_dir: Path, memory_files: dict[str, bytes],
) -> None:
    """Replace the contents of ``memory_dir`` with ``memory_files``.

    Strategy: rmtree the live directory, recreate, then write each file
    byte-for-byte. We don't try to diff-patch because the sandbox sits
    entirely under our control.

    Robustness:
      * Uses :func:`robust_rmtree` with gc.collect + retry to survive
        Windows' WinError 32 when SQLAlchemy engines (e.g. for
        ``time_indexed.db``) still hold open handles right after a
        chat.send. If a file remains locked after all retries we raise
        :class:`RewindFileLockedError` with the specific path so the
        router can return a 409 with a clear "close this file" message
        instead of 500 with a Python traceback.
      * Missing source files or bad relpaths are skipped with a warning
        rather than aborting — rewind remains best-effort even if a
        snapshot is partially corrupted.
    """
    if memory_dir.exists():
        leftovers = robust_rmtree(memory_dir)
        if leftovers:
            # Second-chance: rename the problematic dir aside so the new
            # snapshot can take its place, and let the stale handles die
            # on their own schedule. Renaming is atomic on NTFS and
            # usually succeeds even when unlink can't (the directory
            # entry stays, but the old inode becomes invisible).
            trash = memory_dir.with_name(
                f"{memory_dir.name}.locked_{int(time.time())}",
            )
            try:
                memory_dir.rename(trash)
                python_logger().warning(
                    "snapshot_store: %d file(s) under %s were locked; "
                    "moved the whole dir aside to %s and continued. "
                    "Files: %s",
                    len(leftovers), memory_dir, trash,
                    ", ".join(str(p.name) for p in leftovers[:5]),
                )
            except OSError as exc:
                # Rename also failed — we genuinely can't clear the
                # directory. Surface a clean error so the router can
                # 409 the user with the exact locked file.
                raise RewindFileLockedError(
                    memory_dir=memory_dir,
                    locked_files=leftovers,
                    rename_error=exc,
                ) from None
    memory_dir.mkdir(parents=True, exist_ok=True)

    for relpath, content in memory_files.items():
        # Reject anything that would escape the target directory.
        # ``relpath`` should be a plain forward-slash key from
        # :func:`_walk_memory_files`; anything with ``..`` or an absolute
        # prefix is a malformed snapshot.
        if relpath.startswith("/") or ".." in Path(relpath).parts:
            python_logger().warning(
                "snapshot_store: rejecting suspicious relpath %r during restore",
                relpath,
            )
            continue
        target = memory_dir / relpath
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        except OSError as exc:
            python_logger().warning(
                "snapshot_store: write failed on %s (%s); continuing",
                target, exc,
            )


# ── Store ───────────────────────────────────────────────────────────


class SnapshotStore:
    """Timeline of :class:`Snapshot` entries for a single session.

    Not thread-safe. Mutating methods (``capture``, ``delete``,
    ``rewind_to``, ``update_label``) assume the caller already holds
    ``session.lock`` via :meth:`SessionStore.session_operation`. Read-only
    methods (``list_metadata``, ``get``) are cheap and safe to call
    outside the lock as long as no capture is racing.
    """

    def __init__(
        self,
        *,
        sandbox_root: Path,
        max_hot: int = DEFAULT_MAX_HOT,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    ) -> None:
        self._sandbox_root = Path(sandbox_root)
        self._cold_dir = self._sandbox_root / ".snapshots"
        self._cold_dir.mkdir(parents=True, exist_ok=True)

        self.max_hot = int(max_hot)
        self.debounce_seconds = float(debounce_seconds)

        # Chronological: oldest-first. Metadata-only for cold entries,
        # fully loaded for hot entries.  We keep both lists separate so
        # a rewind-truncate can touch either without copy-all.
        self._hot: list[Snapshot] = []
        self._cold_meta: list[dict[str, Any]] = []

        # Debounce state: last capture time (monotonic) + snapshot id
        # per trigger. Monotonic time is immune to wall-clock jumps so
        # the debounce window stays stable across clock adjustments.
        self._last_capture_monotonic: dict[str, float] = {}
        self._last_capture_id: dict[str, str] = {}

    # ── runtime config update ────────────────────────────────────────

    def update_config(
        self,
        *,
        max_hot: int | None = None,
        debounce_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Update the hot cap / debounce window on a live store.

        Validation:
          * ``max_hot`` must be ≥ 1 (0 would mean "never keep hot",
            effectively disabling in-memory access — not a coherent UX).
            Cap at 500 to prevent a user accidentally blowing up RAM
            from a misplaced zero in the input.
          * ``debounce_seconds`` must be ≥ 0.0 (0 = no debounce; still
            legal). Cap at 3600s so a fat-fingered "60000" doesn't
            silently disable same-trigger coalescing for an hour.

        Shrinking ``max_hot`` immediately spills the extra hot entries
        to cold storage via ``_enforce_hot_cap``; growing it is a no-op
        in terms of existing entries (new captures just won't get
        spilled as eagerly).

        Args:
          max_hot: new hot cap; ``None`` keeps the current value.
          debounce_seconds: new debounce window; ``None`` keeps current.

        Returns:
          Dict with the effective values after the update — the caller
          can echo this back to the client UI without re-querying.

        Raises:
          ValueError: on out-of-range inputs. Caller (router) should
            translate to HTTP 400.

        P24 Day 7 (2026-04-22): added to back the Settings → UI page's
        Snapshot limit input after it was de-``disabled``'d.
        """
        if max_hot is not None:
            mh = int(max_hot)
            if mh < 1 or mh > 500:
                raise ValueError(
                    f"max_hot must be in [1, 500], got {mh}"
                )
            self.max_hot = mh
            # Immediate spill if the new cap is below current hot size.
            self._enforce_hot_cap()
        if debounce_seconds is not None:
            ds = float(debounce_seconds)
            if ds < 0.0 or ds > 3600.0:
                raise ValueError(
                    f"debounce_seconds must be in [0, 3600], got {ds}"
                )
            self.debounce_seconds = ds
        return {
            "max_hot": self.max_hot,
            "debounce_seconds": self.debounce_seconds,
        }

    # ── capture ──────────────────────────────────────────────────────

    def capture(
        self,
        session: "Session",
        *,
        trigger: str,
        label: str | None = None,
        is_backup: bool = False,
    ) -> Snapshot:
        """Record a new snapshot of ``session``.

        Args:
          session: the live session object; its fields are deep-copied.
          trigger: one of :data:`_KNOWN_TRIGGERS`. Unknown values are
            accepted (logged as a warning) to keep business paths from
            crashing on a typo.
          label: human-readable tag (falls back to auto-derived).
          is_backup: if True, this snapshot is a pre-rewind safety net
            and is exempt from truncation and cold-eviction.

        Returns:
          The :class:`Snapshot` now in the timeline. May be a **reused**
          entry (mutated in place) when debounce merges with the prior
          capture of the same trigger.
        """
        if trigger not in _KNOWN_TRIGGERS:
            python_logger().warning(
                "snapshot_store: unknown trigger %r (will accept anyway); "
                "check routers for typos", trigger,
            )

        # 1. Debounce check — only for eligible triggers and only if the
        #    last capture of the same trigger still exists in hot (if it
        #    already spilled to cold, the window clearly lapsed).
        merged = self._try_debounce_merge(
            session, trigger=trigger, label=label, is_backup=is_backup,
        )
        if merged is not None:
            return merged

        # 2. Build a fresh Snapshot. Deep-copy every mutable field so
        #    later mutations of the live session don't leak into the
        #    snapshot.
        snap = self._build_snapshot(
            session, trigger=trigger, label=label, is_backup=is_backup,
        )
        self._hot.append(snap)

        # 3. Remember for future debounce merges.
        if trigger not in _NO_DEBOUNCE_TRIGGERS:
            self._last_capture_monotonic[trigger] = time.monotonic()
            self._last_capture_id[trigger] = snap.id

        # 4. Spill oldest to cold if we exceeded the hot cap. Backups
        #    are exempt — they're meant to stick around as undo targets.
        self._enforce_hot_cap()

        python_logger().debug(
            "snapshot_store: captured %s (trigger=%s, label=%r, messages=%d, "
            "files=%d)",
            snap.id, trigger, snap.label, len(snap.messages),
            len(snap.memory_files),
        )
        return snap

    def _try_debounce_merge(
        self,
        session: "Session",
        *,
        trigger: str,
        label: str | None,
        is_backup: bool,
    ) -> Snapshot | None:
        """Return the mutated prior snapshot if this capture should merge.

        Merge rules:
          * trigger must be in the debounced set
          * prior capture of the same trigger must still be in ``_hot``
          * elapsed time since prior capture ``< debounce_seconds``
          * caller is not requesting a backup (backups always land new)

        When merging, we overwrite the existing snapshot's payload with
        the fresh deep-copied state so rewind-to-that-id still returns
        the most recent view. The id is preserved so any UI that
        cached it stays valid.
        """
        if is_backup or trigger in _NO_DEBOUNCE_TRIGGERS:
            return None
        last_mono = self._last_capture_monotonic.get(trigger)
        last_id = self._last_capture_id.get(trigger)
        if last_mono is None or last_id is None:
            return None
        if (time.monotonic() - last_mono) >= self.debounce_seconds:
            return None

        for idx, existing in enumerate(self._hot):
            if existing.id != last_id:
                continue
            # Build fresh payload then transplant into the prior entry.
            fresh = self._build_snapshot(
                session, trigger=trigger, label=label, is_backup=False,
            )
            existing.created_at = fresh.created_at
            existing.virtual_now = fresh.virtual_now
            existing.label = fresh.label
            existing.messages = fresh.messages
            existing.memory_files = fresh.memory_files
            existing.model_config = fresh.model_config
            existing.stage_state = fresh.stage_state
            existing.eval_results = fresh.eval_results
            existing.persona = fresh.persona
            existing.clock_override = fresh.clock_override
            # Move to the end so chronological order reflects latest activity.
            if idx != len(self._hot) - 1:
                self._hot.append(self._hot.pop(idx))
            self._last_capture_monotonic[trigger] = time.monotonic()
            return existing

        # Prior id no longer in hot (evicted / deleted) — give up merging.
        return None

    def _build_snapshot(
        self,
        session: "Session",
        *,
        trigger: str,
        label: str | None,
        is_backup: bool,
    ) -> Snapshot:
        """Pure factory: build a fresh :class:`Snapshot` from live state."""
        now = datetime.now()
        virtual_now = session.clock.cursor if hasattr(session, "clock") else None
        memory_dir = Path(session.sandbox._app_docs) / "memory"  # noqa: SLF001
        auto_label = label or _default_label(trigger, session=session)
        return Snapshot(
            id=uuid4().hex[:12],
            created_at=now,
            virtual_now=virtual_now,
            label=auto_label,
            trigger=trigger,
            is_backup=is_backup,
            messages=copy.deepcopy(session.messages),
            memory_files=_walk_memory_files(memory_dir),
            model_config=copy.deepcopy(session.model_config),
            stage_state=copy.deepcopy(session.stage_state),
            eval_results=copy.deepcopy(session.eval_results),
            persona=copy.deepcopy(session.persona),
            clock_override=session.clock.to_dict() if hasattr(session, "clock") else None,
        )

    def _enforce_hot_cap(self) -> None:
        """Spill oldest non-backup snapshots to cold storage if over cap.

        We iterate oldest → newest, picking non-backup entries first.
        Backups are deliberately NOT counted against the cap so a long
        rewind chain (backup-then-backup-then-backup) never pushes real
        captures out.
        """
        # Count only non-backups toward the cap.
        non_backup_count = sum(1 for s in self._hot if not s.is_backup)
        while non_backup_count > self.max_hot:
            victim_idx = next(
                (i for i, s in enumerate(self._hot) if not s.is_backup),
                None,
            )
            if victim_idx is None:
                break  # shouldn't happen given count, but defensive
            victim = self._hot.pop(victim_idx)
            self._spill_to_cold(victim)
            non_backup_count -= 1

    def _spill_to_cold(self, snap: Snapshot) -> None:
        """Write ``snap`` to ``sandbox/.snapshots/<id>.json.gz`` and drop
        the in-memory payload; keep a metadata dict in ``_cold_meta``.

        P24 §4.1.2 (2026-04-21): switched from raw gzip-open-for-write
        (non-atomic; highest-risk writer of all 6 copies pre-P24) to
        ``atomic_io.atomic_write_gzip_json`` which does
        tmp + gzip + fsync + os.replace. Previously a power loss
        mid-spill could leave a corrupted cold snapshot that silently
        broke rewind later.
        """
        from tests.testbench.pipeline.atomic_io import atomic_write_gzip_json
        path = self._cold_dir / f"{snap.id}.json.gz"
        try:
            atomic_write_gzip_json(path, snap.to_json_dict())
        except OSError as exc:
            python_logger().warning(
                "snapshot_store: spill to cold failed for %s (%s); dropping "
                "snapshot to avoid memory exhaustion", snap.id, exc,
            )
            return
        meta = snap.metadata(is_compressed=True)
        self._cold_meta.append(meta)
        python_logger().debug(
            "snapshot_store: spilled %s to %s (%d bytes)",
            snap.id, path, path.stat().st_size if path.exists() else 0,
        )

    # ── queries ──────────────────────────────────────────────────────

    def list_metadata(self) -> list[dict[str, Any]]:
        """Return all timeline entries (cold + hot) as metadata dicts.

        Order: oldest → newest. Callers should treat the list as
        read-only; mutations do not write back into the store.
        """
        out: list[dict[str, Any]] = list(self._cold_meta)
        out.extend(s.metadata(is_compressed=False) for s in self._hot)
        return out

    def get(self, snapshot_id: str) -> Snapshot | None:
        """Fully-hydrated snapshot by id, or None if not found.

        Cold entries are transparently loaded from disk. The returned
        Snapshot is **a fresh deep-copy** (even for hot entries) so the
        caller can't accidentally mutate timeline state.
        """
        for snap in self._hot:
            if snap.id == snapshot_id:
                return _deep_copy_snapshot(snap)
        for meta in self._cold_meta:
            if meta["id"] != snapshot_id:
                continue
            path = self._cold_dir / f"{snapshot_id}.json.gz"
            if not path.exists():
                python_logger().warning(
                    "snapshot_store: cold entry %s has no disk file at %s",
                    snapshot_id, path,
                )
                return None
            try:
                with gzip.open(path, "rb") as fh:
                    data = json.loads(fh.read().decode("utf-8"))
            except (OSError, ValueError) as exc:
                python_logger().warning(
                    "snapshot_store: failed to load cold %s (%s)",
                    snapshot_id, exc,
                )
                return None
            return Snapshot.from_json_dict(data)
        return None

    # ── mutations ────────────────────────────────────────────────────

    def delete(self, snapshot_id: str) -> bool:
        """Remove a single snapshot; return True if found & deleted."""
        for idx, snap in enumerate(self._hot):
            if snap.id == snapshot_id:
                self._hot.pop(idx)
                # Drop debounce entry if it was tracking this id.
                for trig, last_id in list(self._last_capture_id.items()):
                    if last_id == snapshot_id:
                        self._last_capture_id.pop(trig, None)
                        self._last_capture_monotonic.pop(trig, None)
                return True
        for idx, meta in enumerate(self._cold_meta):
            if meta["id"] == snapshot_id:
                self._cold_meta.pop(idx)
                path = self._cold_dir / f"{snapshot_id}.json.gz"
                if path.exists():
                    try:
                        path.unlink()
                    except OSError as exc:
                        python_logger().warning(
                            "snapshot_store: unlink %s failed (%s)", path, exc,
                        )
                return True
        return False

    def update_label(self, snapshot_id: str, label: str) -> bool:
        """Rename a snapshot (cold entries update metadata + rewrite
        disk file header). Returns True if the entry was found.
        """
        label = label.strip() or "(unnamed)"
        for snap in self._hot:
            if snap.id == snapshot_id:
                snap.label = label
                return True
        for meta in self._cold_meta:
            if meta["id"] != snapshot_id:
                continue
            meta["label"] = label
            # Also rewrite the disk copy so reload sees the new label.
            snap = self.get(snapshot_id)
            if snap is None:
                return True  # metadata-only rename (disk lost); UI still shows it
            snap.label = label
            path = self._cold_dir / f"{snapshot_id}.json.gz"
            try:
                # P24 §4.1.2: second atomic-io site (alongside _spill_to_cold).
                # Rewrites an existing cold snapshot with the new label; must
                # stay crash-safe so a mid-rewrite crash doesn't corrupt the
                # previously-good snapshot.
                from tests.testbench.pipeline.atomic_io import atomic_write_gzip_json
                atomic_write_gzip_json(path, snap.to_json_dict())
            except OSError as exc:
                python_logger().warning(
                    "snapshot_store: update_label disk rewrite failed on %s (%s)",
                    path, exc,
                )
            return True
        return False

    def clear(self, *, keep_backups: bool = False) -> int:
        """Drop everything (or everything except backups). Returns count removed.

        ``keep_backups=True`` preserves ``pre_rewind_backup`` entries —
        useful for Hard Reset which wants a clean slate but still leaves
        an undo anchor. (P20 Reset subpage may use this.)
        """
        removed = 0
        if keep_backups:
            kept_hot = [s for s in self._hot if s.is_backup]
            removed += len(self._hot) - len(kept_hot)
            self._hot = kept_hot
        else:
            removed += len(self._hot)
            self._hot.clear()

        cold_kept: list[dict[str, Any]] = []
        for meta in self._cold_meta:
            if keep_backups and meta.get("is_backup"):
                cold_kept.append(meta)
                continue
            removed += 1
            path = self._cold_dir / f"{meta['id']}.json.gz"
            if path.exists():
                try:
                    path.unlink()
                except OSError as exc:
                    python_logger().warning(
                        "snapshot_store: clear unlink %s failed (%s)",
                        path, exc,
                    )
        self._cold_meta = cold_kept

        self._last_capture_id.clear()
        self._last_capture_monotonic.clear()
        return removed

    # ── rewind (the star of the show) ───────────────────────────────

    def rewind_to(
        self, session: "Session", snapshot_id: str,
    ) -> dict[str, Any]:
        """Restore session state to ``snapshot_id`` and truncate forward history.

        Steps (matches PLAN §13.4):

          1. Capture a fresh ``pre_rewind_backup`` of the current live
             state so accidental rewinds are recoverable.
          2. Load target snapshot (may decompress from cold).
          3. Replace sandbox memory files (rmtree + rewrite from bytes).
          4. Replace live session fields: messages / model_config /
             stage_state / eval_results / persona / clock.
          5. Truncate timeline: drop every snapshot created after the
             target, **except backups** (backups are user-visible safety
             nets we must never auto-delete).

        Raises:
          LookupError: if ``snapshot_id`` does not exist.
        """
        target = self.get(snapshot_id)
        if target is None:
            raise LookupError(f"snapshot {snapshot_id} not found")

        # Step 1: safety net. If the user immediately rewinds again, we
        # debounce normally (but pre_rewind_backup is in the
        # no-debounce set so every rewind always gets its own backup).
        self.capture(
            session, trigger="pre_rewind_backup",
            label=f"pre_rewind:{target.label}", is_backup=True,
        )

        # Step 2+3: rewrite memory directory. sandbox root guard: never
        # rmtree outside the session's sandbox even if the snapshot
        # somehow carries a divergent path.
        memory_dir = Path(session.sandbox._app_docs) / "memory"  # noqa: SLF001
        if not memory_dir.is_relative_to(self._sandbox_root):  # pragma: no cover
            raise RuntimeError(
                f"Refusing to rewind: memory_dir {memory_dir} escapes sandbox "
                f"{self._sandbox_root}",
            )
        _restore_memory_files(memory_dir, target.memory_files)

        # Step 4: replace live fields with deep copies (never alias the
        # snapshot payload — future captures would see concurrent
        # mutations).
        session.messages = copy.deepcopy(target.messages)
        session.model_config = copy.deepcopy(target.model_config)
        session.stage_state = copy.deepcopy(target.stage_state)
        session.eval_results = copy.deepcopy(target.eval_results)
        session.persona = copy.deepcopy(target.persona)
        if target.clock_override is not None and hasattr(session, "clock"):
            from tests.testbench.virtual_clock import VirtualClock
            session.clock = VirtualClock.from_dict(target.clock_override)

        # P12/P13 runtime state: running script / auto-dialog generators
        # are NOT carried in snapshots (they're in-memory asyncio.Events).
        # Rewinding while a script is loaded simply unloads it — the
        # snapshot's script_state field lives on session but we never
        # captured it, so it stays as whatever it was. To keep rewind
        # behavior predictable, clear transient runtime state.
        session.script_state = None
        # auto_state carries asyncio.Event; clear it too (if an
        # auto-dialog is actively running when the user rewinds, the
        # generator's finally will stop it — the router gates rewind
        # behind the session lock so no concurrent advance races us).
        session.auto_state = None
        session.memory_previews = {}

        # Step 5: truncate forward entries. Find the target's position
        # in the unified hot+cold order and drop everything after it
        # (except backups).
        ordered = self.list_metadata()
        truncate_after = False
        ids_to_drop: list[str] = []
        for meta in ordered:
            if truncate_after and not meta.get("is_backup"):
                ids_to_drop.append(meta["id"])
            if meta["id"] == target.id:
                truncate_after = True
        for sid in ids_to_drop:
            self.delete(sid)

        python_logger().info(
            "snapshot_store: rewound session %s to %s (label=%r), dropped %d "
            "forward snapshots",
            session.id, target.id, target.label, len(ids_to_drop),
        )
        return {
            "rewound_to": target.metadata(),
            "dropped_count": len(ids_to_drop),
        }


# ── module-level helpers ────────────────────────────────────────────


def _default_label(trigger: str, *, session: "Session") -> str:
    """Produce a short auto-label when the caller didn't supply one.

    Timeline UI would rather show "t7:send" than a bare uuid; keep these
    short (<32 chars) so the sidebar doesn't wrap.
    """
    # Per-trigger prefix tables; index by current message count for
    # something glanceable without reading every label.
    n = len(session.messages)
    prefix = {
        "init": "t0:init",
        "manual": "manual",
        "send": f"t{n}:send",
        "edit": f"t{n}:edit",
        "memory_op": f"t{n}:memory",
        "stage_advance": f"t{n}:stage",
        "stage_rewind": f"t{n}:stage_rewind",
        "persona_update": f"t{n}:persona",
        "script_load": f"t{n}:script_load",
        "script_run_all": f"t{n}:script_done",
        "auto_dialog_start": f"t{n}:auto_start",
        "pre_rewind_backup": f"t{n}:pre_rewind",
        "pre_reset_backup": f"t{n}:pre_reset",
    }.get(trigger, f"t{n}:{trigger}")
    return prefix


def capture_safe(
    session: "Session",
    trigger: str,
    *,
    label: str | None = None,
    is_backup: bool = False,
) -> Snapshot | None:
    """Best-effort capture wrapper for business routers.

    Business endpoints call this at the **tail** of a successful
    operation (after messages/memory/stage were committed). If the
    session lost its snapshot_store, or deep-copy explodes on a weird
    payload, we log a warning and return ``None`` rather than propagate
    — snapshot capture should never be the thing that fails a user's
    chat send.

    Callers do **not** need to wrap with try/except; this helper owns
    the error containment so each business route stays a one-liner::

        await business_commit(...)
        capture_safe(session, trigger="send")

    ``is_backup=True`` should be set when the caller is creating a
    **safety net** snapshot (e.g. :func:`SnapshotStore.rewind_to`'s
    implicit pre_rewind_backup, reset_runner's pre_reset_backup).
    Backup snapshots are exempt from the hot-cap eviction and from
    :meth:`SnapshotStore.clear` when ``keep_backups=True`` — critical
    for Hard Reset which must preserve the undo anchor.

    Returns the captured :class:`Snapshot` on success, ``None`` on any
    failure path (missing store, exception during deep-copy, etc.).
    """
    store = getattr(session, "snapshot_store", None)
    if store is None:
        python_logger().warning(
            "snapshot_store: capture_safe called on session without store "
            "(trigger=%s); skipping", trigger,
        )
        return None
    try:
        return store.capture(
            session, trigger=trigger, label=label, is_backup=is_backup,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        python_logger().warning(
            "snapshot_store: capture_safe(trigger=%s) raised %s: %s; "
            "business op was already committed, timeline will have a gap",
            trigger, type(exc).__name__, exc,
        )
        return None


def _deep_copy_snapshot(snap: Snapshot) -> Snapshot:
    """Return a deep copy of ``snap`` so callers can mutate freely."""
    return Snapshot(
        id=snap.id,
        created_at=snap.created_at,
        virtual_now=snap.virtual_now,
        label=snap.label,
        trigger=snap.trigger,
        is_backup=snap.is_backup,
        messages=copy.deepcopy(snap.messages),
        memory_files=dict(snap.memory_files),  # bytes are immutable
        model_config=copy.deepcopy(snap.model_config),
        stage_state=copy.deepcopy(snap.stage_state),
        eval_results=copy.deepcopy(snap.eval_results),
        persona=copy.deepcopy(snap.persona),
        clock_override=copy.deepcopy(snap.clock_override),
    )
