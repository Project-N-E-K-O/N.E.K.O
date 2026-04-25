"""Three-tier reset logic for the active session (P20).

Maps directly onto ``PLAN.md §6 错误处理+复位``:

* **soft**: clear ``session.messages`` and ``session.eval_results``. Keep
  persona, memory files, clock, model_config, schema config, snapshot
  timeline.
* **medium**: soft + wipe the sandbox memory/ subdirectory (rmtree +
  recreate empty) so FactStore / ReflectionEngine / PersonaManager /
  recent.json all start from zero. Persona, clock, model_config, schema,
  snapshot timeline are still kept.
* **hard**: destroy the sandbox contents and rebuild a clean t0 state.
  Clears every snapshot **except** pre_rewind_backup / pre_reset_backup
  safety nets. Preserves model_config (so the tester doesn't re-enter
  provider keys) and the user scoring schemas directory (global, lives
  in DATA_DIR not sandbox).

Every level takes a ``pre_reset_backup`` snapshot *before* mutating any
state. That snapshot bypasses debounce (see
:data:`_NO_DEBOUNCE_TRIGGERS`) and won't be evicted to cold storage, so
it remains a reliable undo anchor even for Hard Reset.

Design notes:
  * Resetting never changes ``session.id`` — any UI component holding
    the id stays valid. The sandbox **directory** is wiped in-place for
    Hard (rmtree its contents + recreate, not rmtree the root) so the
    running Sandbox instance and its ``cm.*`` path attributes stay
    aligned.
  * After reset we emit one fresh ``init`` snapshot (``t0:init_after_<level>``)
    so the timeline reflects the new anchor, and so subsequent
    ``capture(trigger="send")`` debounces against a sensible neighbor.
  * Memory previews / script_state / auto_state are transient runtime
    state, always cleared.
"""
from __future__ import annotations

import shutil
import time
from typing import Any, Literal, TYPE_CHECKING

from tests.testbench.logger import python_logger
from tests.testbench.pipeline.snapshot_store import (
    RewindFileLockedError,
    capture_safe,
    robust_rmtree,
)
from tests.testbench.pipeline.stage_coordinator import initial_stage_state
from tests.testbench.virtual_clock import VirtualClock

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from tests.testbench.session_store import Session

ResetLevel = Literal["soft", "medium", "hard"]

#: Public set of valid levels — keep in sync with the router validator.
_VALID_LEVELS: frozenset[str] = frozenset({"soft", "medium", "hard"})


def _memory_subdir(session: "Session"):
    """Path to the sandbox memory dir (where recent/facts/reflections live)."""
    return session.sandbox._app_docs / "memory"  # noqa: SLF001


def _clear_memory_dir(session: "Session") -> int:
    """rmtree + recreate the sandbox memory subdirectory. Returns # files removed.

    Uses :func:`robust_rmtree` so Windows file-lock races (SQLAlchemy
    engines still holding time_indexed.db) get retried with gc passes
    instead of 500-ing the whole request. If files remain locked after
    retries, the directory is renamed aside so the reset can still
    install a fresh empty ``memory/`` and the old one becomes a
    `memory.locked_<ts>` sibling for manual cleanup later.
    """
    memory_dir = _memory_subdir(session)
    removed = 0
    if memory_dir.exists():
        for sub in memory_dir.rglob("*"):
            if sub.is_file():
                removed += 1
        leftovers = robust_rmtree(memory_dir)
        if leftovers and memory_dir.exists():
            trash = memory_dir.with_name(
                f"{memory_dir.name}.locked_{int(time.time())}",
            )
            try:
                memory_dir.rename(trash)
                python_logger().warning(
                    "reset_runner: %d file(s) in %s were locked; moved the "
                    "dir aside to %s and continued. Locked: %s",
                    len(leftovers), memory_dir, trash,
                    ", ".join(str(p.name) for p in leftovers[:5]),
                )
            except OSError as exc:
                python_logger().warning(
                    "reset_runner: both rmtree AND rename failed on %s "
                    "(%s); user must close external holders and retry",
                    memory_dir, exc,
                )
    try:
        memory_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        python_logger().warning(
            "reset_runner: recreate memory_dir failed on %s (%s)",
            memory_dir, exc,
        )
    return removed


def _clear_sandbox_app_docs(session: "Session") -> int:
    """Hard Reset only: rmtree the sandbox's ``_app_docs`` tree, then
    recreate the standard subdirectory skeleton so ``ConfigManager``
    paths still resolve on subsequent reads.

    Returns number of files removed. We rmtree **the contents** of
    ``_app_docs`` (not the root) — the sandbox root itself is watched
    by :class:`SnapshotStore` (for its ``.snapshots/`` subdir) and is
    tracked in config as the session identity; re-creating it would
    orphan the SnapshotStore.
    """
    app_docs = session.sandbox._app_docs  # noqa: SLF001
    removed = 0
    if app_docs.exists():
        for sub in app_docs.rglob("*"):
            if sub.is_file():
                removed += 1
        for child in list(app_docs.iterdir()):
            try:
                if child.is_dir():
                    # robust path to survive Windows file locks on SQLite
                    # db handles still held by our own Python objects.
                    leftovers = robust_rmtree(child)
                    if leftovers and child.exists():
                        trash = child.with_name(
                            f"{child.name}.locked_{int(time.time())}",
                        )
                        try:
                            child.rename(trash)
                            python_logger().warning(
                                "reset_runner: %d file(s) in %s locked; "
                                "renamed aside to %s",
                                len(leftovers), child, trash,
                            )
                        except OSError as exc:
                            python_logger().warning(
                                "reset_runner: rename %s aside failed (%s)",
                                child, exc,
                            )
                else:
                    child.unlink()
            except OSError as exc:
                python_logger().warning(
                    "reset_runner: clear %s failed (%s)", child, exc,
                )
    # Recreate the subdir skeleton the Sandbox expected after create().
    for sub in (
        "config", "memory", "plugins", "live2d", "vrm", "mmd",
        "workshop", "character_cards",
    ):
        try:
            (app_docs / sub).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            python_logger().warning(
                "reset_runner: recreate %s failed (%s)", sub, exc,
            )
    return removed


def reset_session(session: "Session", level: ResetLevel) -> dict[str, Any]:
    """Perform the three-tier reset. Caller must hold ``session.lock``.

    Returns a JSON-safe dict summarizing what happened: level / removed
    counts / which preserved fields remain. Routers pass this back to
    the UI so testers get confirmation rather than a silent "ok:true".
    """
    if level not in _VALID_LEVELS:
        raise ValueError(f"invalid reset level {level!r}")

    # Step 1: always capture a pre_reset_backup first so the user can
    # undo an accidental Hard Reset. capture_safe never raises — it'll
    # log a warning if snapshot_store is somehow None (shouldn't happen
    # for live sessions) and the reset will still proceed.
    backup_snap = capture_safe(
        session,
        trigger="pre_reset_backup",
        label=f"pre_reset:{level}",
        is_backup=True,
    )
    backup_id = backup_snap.id if backup_snap is not None else None

    stats: dict[str, Any] = {
        "level": level,
        "pre_reset_backup_id": backup_id,
        "removed": {
            "messages": 0,
            "eval_results": 0,
            "memory_files": 0,
            "app_docs_files": 0,
            "snapshots": 0,
        },
        "preserved": [],
    }

    # Step 2: clear messages + eval_results for every level.
    stats["removed"]["messages"] = len(session.messages)
    stats["removed"]["eval_results"] = len(session.eval_results)
    session.messages = []
    session.eval_results = []

    # Always reset transient runtime state — a half-running script or
    # auto-dialog paused across a reset is undefined behavior.
    session.script_state = None
    session.auto_state = None
    session.memory_previews = {}

    if level == "soft":
        stats["preserved"] = [
            "persona", "memory_files", "clock", "model_config",
            "schema_config", "snapshot_timeline", "stage_state",
        ]
    elif level == "medium":
        # Wipe memory files but keep everything else.
        stats["removed"]["memory_files"] = _clear_memory_dir(session)
        stats["preserved"] = [
            "persona", "clock", "model_config", "schema_config",
            "snapshot_timeline", "stage_state",
        ]
    else:  # hard
        # Nuke the whole _app_docs skeleton (memory + character_cards +
        # config + ...), then reset all session-level state to its
        # create()-time values while **preserving** model_config.
        preserved_model_config = dict(session.model_config or {})
        stats["removed"]["app_docs_files"] = _clear_sandbox_app_docs(session)
        # Reset persona (to empty — will be re-seeded on next preset/import).
        session.persona = {}
        # Virtual clock: brand new (no cursor, no bootstrap), just like t0.
        session.clock = VirtualClock()
        # Stage coach back to first stage with empty history.
        session.stage_state = initial_stage_state()
        # Restore model_config explicitly.
        session.model_config = preserved_model_config
        # Drop all snapshots except backups (pre_rewind_backup,
        # pre_reset_backup). This keeps the undo anchors alive.
        if session.snapshot_store is not None:
            before_total = len(session.snapshot_store.list_metadata())
            cleared = session.snapshot_store.clear(keep_backups=True)
            stats["removed"]["snapshots"] = cleared
            after_total = len(session.snapshot_store.list_metadata())
            python_logger().info(
                "reset_runner: hard reset cleared %d/%d snapshots "
                "(kept %d backup entries)",
                cleared, before_total, after_total,
            )
        stats["preserved"] = ["model_config", "schema_config_global"]

    # Step 3: re-anchor with a fresh init snapshot so the timeline starts
    # at t0 again. This is NOT a backup — it's the new "first entry".
    # Runs regardless of level so even a Soft Reset has a clean timeline
    # entry to debounce against.
    capture_safe(
        session,
        trigger="init",
        label=f"t0:init_after_{level}_reset",
    )

    python_logger().info(
        "reset_runner: %s reset complete for session %s (removed=%s)",
        level, session.id, stats["removed"],
    )
    session.logger.log_sync(
        "session.reset",
        payload={"level": level, "stats": stats},
    )
    return stats
