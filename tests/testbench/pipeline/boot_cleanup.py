"""Boot-time cleanup of orphan temp files (PLAN §10 P-B).

P22 autosave landed the "rolling 3-slot autosave + boot-window pruning"
portion of the crash-safety discipline captured in
:doc:`AGENT_NOTES §4.26 #91`; this module is the follow-up that wipes
the *sub-archive* temp artefacts a hard-kill leaves behind. It is
explicitly scoped to **non-controversial, always-safe** deletions:
half-written ``.tmp`` atomic-write stragglers, stale
``memory.locked_<ts>`` rename sidecars, and orphaned SQLite sidecar
files (``*-journal`` / ``*-wal`` / ``*-shm``) whose parent ``.db`` is
long gone. It does **not** touch whole sandbox directories (that's
P-A / P-D on the PLAN §10 backlog and requires UI affordances to let
the user triage before wipe — see §3A F3 "report, don't silently
delete").

Design constraints:

* Runs **synchronously** in ``server.py::_startup_cleanup`` right after
  :func:`autosave.cleanup_old_autosaves`. At expected scale (a handful
  of sandboxes each with a few temp files) the scan completes in well
  under 100 ms, not worth an asyncio round-trip.
* Every action is logged via :func:`python_logger` at INFO level so the
  boot log has a full audit trail; aggregate stats are returned to the
  caller for a single summary log line.
* Exceptions are caught per-item: one locked-on-Windows ``.tmp`` can't
  abort the entire sweep.
* Whitelisted to the project's ``DATA_DIR``; any path outside is
  rejected up front (defensive — the scan only walks
  :data:`config.SANDBOXES_DIR` / :data:`config.SAVED_SESSIONS_DIR` /
  :data:`config.AUTOSAVE_DIR` anyway, but the guard protects against
  future refactors that accept a caller-supplied root).

Safety rationale per category:

1. ``*.tmp`` files — these come from ``_atomic_write_bytes`` /
   ``_atomic_write_json``. The contract is: write tmp → fsync → replace.
   If the ``.tmp`` still exists on disk, the ``os.replace`` never ran
   (process died mid-atomic-write) and the original file (if any) is
   intact. The ``.tmp`` carries only the bytes that would have replaced
   the original — useless after the fact. Always safe to delete.

2. ``memory.locked_<timestamp>`` directories — these are rename-aside
   sidecars from ``snapshot_store.py::_restore_memory_files`` /
   ``reset_runner.py`` when Windows held a live file handle on SQLite
   and ``robust_rmtree`` couldn't delete. The policy is to keep them
   until the next successful rewind/reset (so the user can manually
   inspect if something went wrong) but **delete once they're stale**.
   We gate on mtime > 24h: any handle that was still open on the
   referenced SQLite engine would have been closed by ``@app.on_event
   ("shutdown")``'s ``session_store.destroy()`` long ago, so after a
   full restart (= this boot cleanup) the directory is definitively
   safe to blow away.

3. Orphan SQLite sidecars (``*-journal`` / ``*-wal`` / ``*-shm``) —
   when the matching ``.db`` is gone, these files are leftovers from a
   SQLite process that didn't finish closing. SQLite itself ignores
   unmatched sidecars (next open creates fresh ones), and they only
   ever bloat the directory. Safe to delete.

Non-goals (explicitly NOT in this module):

* Orphan sandbox directories **with data inside** (no active session →
  directory on disk, but memory files / snapshots / logs present).
  That's PLAN §10 P-A (scan + report, never silent delete) and P-D
  (Diagnostics Paths sub-page UI) — both require user triage before
  deletion because the user may have legitimately kept them around as
  crash debugging material.

  **Exception** (P24 §15.2 B update, 2026-04-21): a sandbox dir that
  is **completely empty** (0 files, size == 0) after recursive scan is
  treated like a ``.tmp`` leftover — no user data to lose, nothing to
  triage. We clean these on boot and return a count so the startup
  log can say "cleaned N empty sandboxes". This is same "provably safe"
  tier as the three categories above — an empty directory structure
  contains zero bits of user work.
* ``atexit`` last-gasp flush — decided against in AGENT_NOTES #91 + #99
  ("SIGKILL can't await; SIGTERM in asyncio can't await atexit").
* SQLite WAL mode — explicitly rejected in PLAN §10 (introduces
  ``-wal`` / ``-shm`` sidecar files that aggravate the Windows file
  lock problem this module already has to clean up after).
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from tests.testbench import config as tb_config
from tests.testbench.logger import python_logger


#: How old a ``memory.locked_<ts>`` directory must be (in **hours** from
#: its mtime) before boot cleanup will rmtree it. 24h is comfortably
#: past any reasonable in-flight rewind/reset op; covers a user who
#: left the machine running overnight after crashing out.
_LOCKED_DIR_STALE_HOURS: float = 24.0

#: Filename suffixes of SQLite sidecar files that can be orphaned if
#: the writer process dies mid-commit. Any file ending in one of these
#: suffixes whose stripped-stem ``.db`` is absent gets deleted.
_SQLITE_SIDECAR_SUFFIXES: tuple[str, ...] = (
    "-journal",   # legacy rollback journal
    "-wal",       # write-ahead log (only if someone turned on WAL)
    "-shm",       # shared memory region (WAL mode)
)


# ── small utilities ────────────────────────────────────────────────


def _is_within(path: Path, root: Path) -> bool:
    """Return True iff ``path`` resolves inside ``root`` (defensive)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _try_unlink(path: Path, *, reason: str, stats: dict[str, int]) -> None:
    """Attempt to delete ``path``; log and bump stats on success.

    On failure logs a warning but does not raise — a single locked
    file on Windows cannot abort the whole sweep.
    """
    try:
        path.unlink()
        stats["files_removed"] = stats.get("files_removed", 0) + 1
        python_logger().info(
            "boot_cleanup: removed %s (%s)", path, reason,
        )
    except OSError as exc:
        stats["unlink_failures"] = stats.get("unlink_failures", 0) + 1
        python_logger().warning(
            "boot_cleanup: unlink failed for %s (%s): %s", path, reason, exc,
        )


def _try_rmtree(path: Path, *, reason: str, stats: dict[str, int]) -> None:
    """Attempt to recursively delete ``path``; log on result."""
    try:
        shutil.rmtree(path, ignore_errors=False)
        stats["dirs_removed"] = stats.get("dirs_removed", 0) + 1
        python_logger().info(
            "boot_cleanup: removed directory %s (%s)", path, reason,
        )
    except OSError as exc:
        stats["rmtree_failures"] = stats.get("rmtree_failures", 0) + 1
        python_logger().warning(
            "boot_cleanup: rmtree failed for %s (%s): %s", path, reason, exc,
        )


# ── individual scan rules ──────────────────────────────────────────


def _sweep_tmp_files(root: Path, stats: dict[str, int]) -> None:
    """Remove all ``*.tmp`` files recursively under ``root``.

    ``.tmp`` is the suffix used by ``_atomic_write_bytes`` /
    ``_atomic_write_json``. If one still exists at boot, the matching
    ``os.replace`` never ran (process died mid-atomic-write). The file
    holds at most the bytes that would have replaced the live file,
    which are already obsolete by the time we're here. Always safe.
    """
    if not root.exists():
        return
    for path in root.rglob("*.tmp"):
        if not path.is_file():
            continue
        if not _is_within(path, tb_config.DATA_DIR):
            continue  # defensive — rglob should never escape
        _try_unlink(path, reason="orphan atomic-write .tmp", stats=stats)


def _sweep_locked_memory_dirs(root: Path, stats: dict[str, int]) -> None:
    """Remove ``memory.locked_<timestamp>`` directories older than 24h.

    Created by ``snapshot_store.py`` / ``reset_runner.py`` when Windows
    held a file handle open during rewind/reset and the rename-aside
    fallback kicked in (§3A F2 / AGENT_NOTES #88). They're retained as
    manual-inspection fodder but any open handle that stopped them
    deleting originally is long released by the time we reboot — so
    past the staleness window the directory is definitively dead
    weight.
    """
    if not root.exists():
        return
    now = time.time()
    cutoff_seconds = _LOCKED_DIR_STALE_HOURS * 3600
    # Walk sandbox dirs; within each, look for any entry whose name
    # contains ``.locked_`` (matches both ``memory.locked_<ts>`` under
    # the sandbox N.E.K.O root and any future `.locked_` variant).
    for candidate in root.rglob("*.locked_*"):
        if not candidate.is_dir():
            continue
        if not _is_within(candidate, tb_config.DATA_DIR):
            continue
        try:
            mtime = candidate.stat().st_mtime
        except OSError as exc:
            python_logger().warning(
                "boot_cleanup: stat failed on %s: %s; skipping", candidate, exc,
            )
            continue
        age_seconds = now - mtime
        if age_seconds < cutoff_seconds:
            python_logger().info(
                "boot_cleanup: keeping %s (age %.1fh < %.1fh cutoff)",
                candidate, age_seconds / 3600, _LOCKED_DIR_STALE_HOURS,
            )
            continue
        _try_rmtree(
            candidate,
            reason=(
                f"stale locked-aside dir, age {age_seconds / 3600:.1f}h "
                f">= {_LOCKED_DIR_STALE_HOURS:.1f}h cutoff"
            ),
            stats=stats,
        )


def _sweep_orphan_sqlite_sidecars(root: Path, stats: dict[str, int]) -> None:
    """Remove ``*-journal`` / ``*-wal`` / ``*-shm`` whose ``.db`` is absent.

    SQLite creates these sidecars during a write transaction and cleans
    them up when the write commits. If the writing process dies between
    commit and cleanup, the sidecar lingers. If the whole ``.db`` has
    since been wiped (e.g. the sandbox was reset), the sidecar is an
    orphan — SQLite itself will happily re-create sidecars on next
    open, so deleting leftovers is always safe and keeps the directory
    tidy for diagnostics.
    """
    if not root.exists():
        return
    for suffix in _SQLITE_SIDECAR_SUFFIXES:
        # Use rglob with a literal suffix by filename ending.
        for path in root.rglob(f"*{suffix}"):
            if not path.is_file():
                continue
            if not _is_within(path, tb_config.DATA_DIR):
                continue
            # Derive the expected ``.db`` path by stripping the suffix.
            name = path.name
            if not name.endswith(suffix):
                continue
            stem = name[: -len(suffix)]
            if not stem:
                continue
            sibling_db = path.with_name(stem)
            if sibling_db.exists():
                # Companion .db still present — this sidecar may still
                # be in use (next SQLite open will check it). Skip.
                continue
            _try_unlink(
                path,
                reason=f"orphan SQLite sidecar ({suffix}; no sibling .db)",
                stats=stats,
            )


# ── public entrypoint ──────────────────────────────────────────────


def run_boot_cleanup() -> dict[str, Any]:
    """Single entry point wired into ``server.py::_startup_cleanup``.

    Returns a stats dict that the caller logs at INFO level:

    * ``files_removed`` — count of ``.tmp`` + orphan sidecars unlinked
    * ``dirs_removed`` — count of stale ``*.locked_*`` dirs rmtree'd
    * ``unlink_failures`` / ``rmtree_failures`` — best-effort misses
    * ``roots_scanned`` — which top-level roots were walked (for the
      log line to confirm the scope)

    Never raises; per-item failures surface as counter increments +
    warning logs. If the whole op fails catastrophically, the caller's
    ``try/except Exception`` in :func:`server._startup_cleanup` catches
    it so boot still succeeds.
    """
    stats: dict[str, int] = {
        "files_removed": 0,
        "dirs_removed": 0,
        "unlink_failures": 0,
        "rmtree_failures": 0,
    }
    roots_scanned: list[str] = []

    # Ordering: sandboxes first (largest tree), then archive-level
    # roots. Each sweep is idempotent so ordering is not correctness-
    # critical, only output-log-tidiness critical.
    for root in (
        tb_config.SANDBOXES_DIR,
        tb_config.SAVED_SESSIONS_DIR,
        tb_config.AUTOSAVE_DIR,
    ):
        if root.exists():
            roots_scanned.append(str(root))
        _sweep_tmp_files(root, stats)

    # Locked-aside directories live inside sandbox trees (rewind/reset
    # rename-fallback) — no point scanning the archive dirs for them.
    _sweep_locked_memory_dirs(tb_config.SANDBOXES_DIR, stats)

    # Orphan SQLite sidecars likewise live in sandbox memory subtrees.
    _sweep_orphan_sqlite_sidecars(tb_config.SANDBOXES_DIR, stats)

    # P24 §15.2 B: empty sandbox directories have no user data to lose,
    # provably safe to delete (same tier as .tmp stragglers above).
    _sweep_empty_sandboxes(tb_config.SANDBOXES_DIR, stats)

    stats["roots_scanned"] = roots_scanned
    return stats


def _sweep_empty_sandboxes(root: Path, stats: dict[str, Any]) -> None:
    """Delete sandbox subdirectories that contain zero files (0 bytes).

    Safety: the "0 file, 0 byte" criterion is provably safe —
    there is no user data to lose. The only content in such a dir is
    the directory structure itself (empty sub-folders from the initial
    ``Sandbox.create()`` scaffolding), which ``Sandbox.create()`` will
    reconstruct anyway if the id is ever re-used.

    Called unconditionally from :func:`run_boot_cleanup` — does NOT
    consult the active-session whitelist because a "currently active"
    session never has an empty sandbox (at minimum it holds ``init``
    snapshot + logger state by the time boot_cleanup runs).

    Per-item errors are logged + counted but never abort the sweep.
    """
    if not _is_within(root, tb_config.DATA_DIR):
        python_logger().error(
            "boot_cleanup: refusing to sweep non-DATA_DIR root %s", root,
        )
        return
    if not root.exists():
        return

    empty_removed = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name.endswith(".tmp"):
            # Skip hidden + .tmp-style dirs (handled by other sweeps)
            continue
        # Fast empty check: walk once counting files; stop on first hit
        has_content = False
        try:
            for p in entry.rglob("*"):
                if p.is_file():
                    # Truly empty means 0 bytes total — a 0-byte file
                    # still counts as content (matches .gitkeep pattern)
                    if p.stat().st_size > 0:
                        has_content = True
                        break
                    has_content = True  # any regular file = keep
                    break
        except OSError:
            # Can't enumerate — don't delete, we're not sure
            continue
        if has_content:
            continue
        try:
            shutil.rmtree(entry, ignore_errors=False)
            empty_removed += 1
            python_logger().info(
                "boot_cleanup: removed empty sandbox %s", entry,
            )
        except OSError as exc:
            stats["rmtree_failures"] = stats.get("rmtree_failures", 0) + 1
            python_logger().warning(
                "boot_cleanup: rmtree failed for empty sandbox %s: %s",
                entry, exc,
            )

    stats["empty_sandboxes_removed"] = empty_removed


__all__ = ["run_boot_cleanup"]
