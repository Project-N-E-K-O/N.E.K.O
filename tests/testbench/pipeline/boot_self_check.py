"""Boot-time self-check: orphan sandbox directory detection (P24 §15.2 A / P-A).

Sibling to :mod:`boot_cleanup` (which does "always-safe"
silent deletes of ``*.tmp`` / stale ``.locked_<ts>`` / orphan SQLite
sidecars). This module does the opposite: **scan only, never delete**.
It finds sandbox directories that no longer correspond to any active
session and **reports** them so the Diagnostics → Paths UI can let the
user triage (delete / keep / inspect) — per §3A F3 "report, don't
silently delete".

Why not delete automatically?
-----------------------------
An orphan sandbox can be:

* A leftover from a previous run that was hard-killed (task manager,
  BSOD, power loss). Usually safe to delete — but the user might want
  to read ``memory/recent.json`` out of it for post-mortem.
* A sandbox the user **explicitly preserved** before hitting
  "New session" (e.g. to compare memory evolution across two runs).

Auto-deletion would destroy b/c without asking. So the scanner is
read-only; the UI adds a dedicated ``DELETE /api/system/orphans/<sid>``
entry point with a mandatory confirm-modal.

Scope
-----
* Only walks :data:`config.SANDBOXES_DIR` (whitelisted).
* Active-session whitelist: the single active session's id from
  :class:`session_store.SessionStore`. Single-session model, so at most
  one entry in the whitelist at any time.
* Autosave orphan discovery is a separate concern handled by
  :func:`autosave.list_autosaves_boot_orphans` (already reported in
  restore banner); this module does **not** duplicate that.
* Saved-session archive orphan discovery (stray ``.tar.gz`` without
  paired ``.json``) is left for a future pass — `persistence.list_saved`
  already surfaces such rows as ``broken`` entries so the Paths UI
  doesn't gain anything from also listing them here.

API
---
* :func:`scan_orphan_sandboxes` → list of orphan metadata dicts
* :func:`delete_orphan_sandbox` → delete one orphan by session_id

Both are pure Python / filesystem; no async, no session locks. The HTTP
endpoints in ``health_router`` just wrap these.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from tests.testbench import config as tb_config
from tests.testbench.logger import python_logger


class OrphanSandboxError(RuntimeError):
    """Raised when an orphan-delete operation fails in a user-meaningful
    way (nonexistent id, delete blocked by OS, path outside whitelist).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _dir_size_bytes(path: Path) -> int:
    """Recursive size in bytes. Swallow per-file errors so one locked
    handle in a deep tree doesn't abort the whole measurement.
    """
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    continue
    except OSError:
        # Even the initial rglob can fail if the dir got rm'd mid-scan.
        pass
    return total


def _dir_mtime_iso(path: Path) -> str | None:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def scan_orphan_sandboxes(
    *, active_session_ids: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Scan :data:`config.SANDBOXES_DIR` for orphan sandbox directories.

    An **orphan** is any top-level subdir of SANDBOXES_DIR whose name
    is **not** in ``active_session_ids``. Hidden directories (name
    starting with ``.``) and the ``.tmp`` convention are skipped so
    this never flags infrastructure (gitkeep files, etc.).

    Args:
      active_session_ids: set of session ids currently considered "live".
        If ``None``, defaults to ``get_session_store().get().id`` if a
        session is active, else an empty set. Callers can pass a
        larger set to whitelist multiple sessions (future multi-session
        extension).

    Returns:
      ``{"orphans": [...], "scanned_at": iso, "total_bytes": int}`` where
      each orphan dict has ``{session_id, path, size_bytes, mtime}``.
      Newest-first by mtime (most recently modified first).
    """
    if active_session_ids is None:
        from tests.testbench.session_store import get_session_store
        store = get_session_store()
        active = store.get()
        active_session_ids = frozenset({active.id}) if active is not None else frozenset()

    sandboxes_root = tb_config.SANDBOXES_DIR
    orphans: list[dict[str, Any]] = []
    total_bytes = 0

    if sandboxes_root.exists():
        for entry in sandboxes_root.iterdir():
            if not entry.is_dir():
                continue
            # Skip hidden / infra directories
            name = entry.name
            if name.startswith(".") or name.endswith(".tmp"):
                continue
            if name in active_session_ids:
                continue
            size = _dir_size_bytes(entry)
            total_bytes += size
            orphans.append({
                "session_id": name,
                "path": str(entry),
                "size_bytes": size,
                "mtime": _dir_mtime_iso(entry),
            })

    orphans.sort(key=lambda o: o.get("mtime") or "", reverse=True)

    return {
        "orphans": orphans,
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
        "total_bytes": total_bytes,
    }


def delete_orphan_sandbox(session_id: str) -> dict[str, Any]:
    """Delete one orphan sandbox directory by session_id.

    Safety guards:

    * Refuses if the id matches the **currently active** session (that
      would destroy the user's live data — big no-op).
    * Path must resolve inside :data:`config.SANDBOXES_DIR` (defensive
      traversal protection; a caller passing ``"../..."`` gets rejected).
    * Uses :func:`shutil.rmtree` with ``ignore_errors=True``. On Windows
      locked-handle scenarios this may leave a partial remnant — we
      return the remaining size so the UI can flag it rather than
      pretending success.

    Raises:
      :class:`OrphanSandboxError` with code in
      ``{"OrphanNotFound", "OrphanIsActive", "OrphanPathTraversal"}``.
    """
    if not session_id or "/" in session_id or "\\" in session_id or session_id in {".", ".."}:
        raise OrphanSandboxError("OrphanPathTraversal", f"invalid session_id: {session_id!r}")

    from tests.testbench.session_store import get_session_store
    store = get_session_store()
    active = store.get()
    if active is not None and active.id == session_id:
        raise OrphanSandboxError(
            "OrphanIsActive",
            f"session_id {session_id!r} is the currently active session, "
            "not an orphan — refuse to delete.",
        )

    target = tb_config.SANDBOXES_DIR / session_id
    # Enforce the directory is inside SANDBOXES_DIR even after resolve
    try:
        resolved = target.resolve(strict=False)
        resolved.relative_to(tb_config.SANDBOXES_DIR.resolve(strict=False))
    except (OSError, ValueError) as exc:
        raise OrphanSandboxError(
            "OrphanPathTraversal",
            f"path resolves outside sandboxes root: {target}",
        ) from exc

    if not target.exists() or not target.is_dir():
        raise OrphanSandboxError(
            "OrphanNotFound",
            f"no orphan sandbox found at {target}",
        )

    size_before = _dir_size_bytes(target)
    shutil.rmtree(target, ignore_errors=True)
    # Re-check: did it really go away?
    remaining = _dir_size_bytes(target) if target.exists() else 0
    python_logger().info(
        "boot_self_check: deleted orphan sandbox %s (%d bytes, %d bytes remaining)",
        session_id, size_before, remaining,
    )
    return {
        "session_id": session_id,
        "deleted_bytes": size_before - remaining,
        "remaining_bytes": remaining,
        "fully_removed": remaining == 0 and not target.exists(),
    }


__all__ = [
    "OrphanSandboxError",
    "scan_orphan_sandboxes",
    "delete_orphan_sandbox",
]
