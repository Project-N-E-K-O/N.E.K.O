"""Per-session sandbox directory + ``ConfigManager`` swap/restore.

The main app's :class:`utils.config_manager.ConfigManager` is a **process
singleton**; swapping the user data directory therefore means mutating
its live path attributes (``docs_dir`` / ``memory_dir`` / …) and
restoring them verbatim when the session ends.

The mechanics mirror :func:`tests.conftest.clean_user_data_dir` so behavior
is identical to what the existing unit tests expect.

Lifecycle::

    sandbox = Sandbox(session_id="abc").create()
    sandbox.apply()   # Patch ConfigManager -> sandbox paths
    ...               # Testbench pipeline reads/writes through ConfigManager
    sandbox.restore() # Put the original paths back
    sandbox.destroy() # Optional: rm -rf the sandbox directory
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from utils.config_manager import get_config_manager

from tests.testbench import config as tb_config
from tests.testbench.logger import python_logger

# Attributes captured and restored on ConfigManager. Kept centrally so
# forgetting one won't leak state across sessions.
#
# Sync status (2026-04-21 P24 Day 1 audit):
#
#   ``utils.config_manager.ConfigManager.__init__`` defines 14 ``*_dir``
#   attributes (docs/app_docs/config/memory/plugins/live2d/vrm/vrm_animation/
#   mmd/mmd_animation/workshop/chara/project_config/project_memory). This
#   list covers all 14 — fully synced.
#
#   ``tests.conftest.clean_user_data_dir`` (non-testbench pytest fixture)
#   only covers 11; it lags by 3 fields (``plugins_dir`` / ``mmd_dir`` /
#   ``mmd_animation_dir``). **Intentionally not fixed here** because:
#   (a) it's a non-testbench fixture, doesn't affect our sandboxing;
#   (b) patching it requires rewriting 9 explicit variable names in the
#   fixture body, larger blast radius than warranted by P24 Day 1 scope.
#   Filed as main-program-side tech debt for future standalone pass.
#
# Invariant enforced by ``smoke/p24_sandbox_attrs_sync_smoke.py`` (Day 10):
# inspect current ``ConfigManager`` for any new ``*_dir`` / ``*_path``
# public attribute not in this tuple — fail loudly so future additions
# force a sync update here.
_PATCHED_ATTRS: tuple[str, ...] = (
    "docs_dir",
    "app_docs_dir",
    "config_dir",
    "memory_dir",
    "plugins_dir",
    "live2d_dir",
    "vrm_dir",
    "vrm_animation_dir",
    "mmd_dir",
    "mmd_animation_dir",
    "workshop_dir",
    "chara_dir",
    "project_config_dir",
    "project_memory_dir",
)


class SandboxError(RuntimeError):
    """Raised for any sandbox wiring mishap (double-apply / missing dir)."""


class Sandbox:
    """Owns one session's sandbox directory + ConfigManager overrides.

    Instances are **not** reusable once :meth:`destroy` has been called;
    create a new :class:`Sandbox` for a fresh session.
    """

    def __init__(self, session_id: str, app_name: str | None = None) -> None:
        self.session_id = session_id
        # Resolve app_name from the live ConfigManager singleton rather than
        # importing ``config.APP_NAME`` directly — the testbench ships its own
        # ``tests/testbench/config.py`` which shadows the top-level ``config``
        # package whenever ``tests/testbench`` is on sys.path (e.g. when
        # launched via ``python tests/testbench/run_testbench.py``).
        self.app_name = app_name or get_config_manager().app_name
        self.root: Path = tb_config.sandbox_dir_for(session_id)
        self._app_docs: Path = self.root / self.app_name
        self._originals: dict[str, Any] | None = None
        self._applied: bool = False

    # ── directory lifecycle ────────────────────────────────────────

    def create(self) -> "Sandbox":
        """Materialise the sandbox directory tree.

        Safe to call repeatedly; existing files are left alone which lets
        callers seed the sandbox (e.g. "Import from real character") before
        :meth:`apply`.
        """
        for sub in (
            "config",
            "memory",
            "character_cards",
            "live2d",
            "vrm",
            "vrm/animation",
            "mmd",
            "mmd/animation",
            "workshop",
            "plugins",
        ):
            (self._app_docs / sub).mkdir(parents=True, exist_ok=True)
        return self

    def destroy(self) -> None:
        """Best-effort removal of the sandbox directory on disk.

        Tolerates "already gone" and partial-delete failures — callers log
        the outcome but shouldn't crash the session on cleanup errors.
        """
        if self._applied:
            raise SandboxError(
                "Sandbox.destroy() called while still applied; call restore() first.",
            )
        if self.root.exists():
            try:
                shutil.rmtree(self.root)
            except OSError as exc:
                python_logger().warning(
                    "Sandbox %s: rmtree failed on %s (%s); leaving residue",
                    self.session_id, self.root, exc,
                )

    # ── ConfigManager patching ─────────────────────────────────────

    def apply(self) -> None:
        """Redirect the global ConfigManager singleton to this sandbox.

        Must be paired with :meth:`restore`. Raises :class:`SandboxError`
        if the sandbox is already active.
        """
        if self._applied:
            raise SandboxError(f"Sandbox {self.session_id} already applied")

        if not self.root.exists():
            self.create()

        cm = get_config_manager(self.app_name)

        self._originals = {attr: getattr(cm, attr) for attr in _PATCHED_ATTRS}

        cm.docs_dir = self.root
        cm.app_docs_dir = self._app_docs
        cm.config_dir = self._app_docs / "config"
        cm.memory_dir = self._app_docs / "memory"
        cm.plugins_dir = self._app_docs / "plugins"
        cm.live2d_dir = self._app_docs / "live2d"
        cm.vrm_dir = self._app_docs / "vrm"
        cm.vrm_animation_dir = cm.vrm_dir / "animation"
        cm.mmd_dir = self._app_docs / "mmd"
        cm.mmd_animation_dir = cm.mmd_dir / "animation"
        cm.workshop_dir = self._app_docs / "workshop"
        cm.chara_dir = self._app_docs / "character_cards"
        # Point "project" dirs at the sandbox, matching conftest's choice —
        # the real main app puts them under the project tree, but the
        # testbench keeps everything under the sandbox to avoid cross-talk.
        cm.project_config_dir = cm.config_dir
        cm.project_memory_dir = cm.memory_dir

        # Singleton character cache keys off path; clear so stale entries
        # don't leak the previous session's data into this one.
        cm._characters_cache = None
        cm._characters_cache_mtime = None
        cm._characters_cache_path = None
        cm._characters_dirty = False

        self._applied = True
        python_logger().info(
            "Sandbox %s: ConfigManager patched -> %s", self.session_id, self.root,
        )

    def restore(self) -> None:
        """Undo :meth:`apply`. Idempotent when never applied."""
        if not self._applied:
            return
        assert self._originals is not None
        cm = get_config_manager(self.app_name)
        for attr, value in self._originals.items():
            setattr(cm, attr, value)

        cm._characters_cache = None
        cm._characters_cache_mtime = None
        cm._characters_cache_path = None
        cm._characters_dirty = False

        self._originals = None
        self._applied = False
        python_logger().info("Sandbox %s: ConfigManager restored", self.session_id)

    # ── introspection ──────────────────────────────────────────────

    @property
    def applied(self) -> bool:
        return self._applied

    def describe(self) -> dict[str, str]:
        """Return path info useful for UI/debug (P20 Paths subpage)."""
        return {
            "session_id": self.session_id,
            "root": str(self.root),
            "app_docs": str(self._app_docs),
            "applied": "yes" if self._applied else "no",
        }

    def real_paths(self) -> dict[str, Any]:
        """Return **pre-patch** ConfigManager paths (memory/config/chara).

        Used by the "Import from real character" flow (P05) which needs to
        read the tester's actual Documents/-hosted character cards and memory
        files *while the sandbox is active*. Reading ``cm.memory_dir`` during
        that window would wrongly return the sandbox path.

        Returns an empty dict when the sandbox is not currently applied —
        callers should treat that as "nothing to import from" and tell the
        user to create a session first.

        P24 Day 8 (2026-04-22, §12.4.A fix): also exposes the main program's
        ``_readable_docs_dir`` (CFA fallback state) so the Import UI can
        warn users when Documents is read-only and the app reads/writes
        from ``AppData\\Local`` instead — a common "I edited Documents but
        nothing changed" gotcha on Windows with Controlled Folder Access
        enabled. ``readable_docs_dir`` is None when CFA fallback is
        inactive (Documents is writable and everything goes there).
        """
        if not self._applied or not self._originals:
            return {}
        from utils.config_manager import get_config_manager
        cm = get_config_manager(self.app_name)
        readable = getattr(cm, "_readable_docs_dir", None)
        return {
            "docs_dir": Path(self._originals["docs_dir"]),
            "app_docs_dir": Path(self._originals["app_docs_dir"]),
            "config_dir": Path(self._originals["config_dir"]),
            "memory_dir": Path(self._originals["memory_dir"]),
            "chara_dir": Path(self._originals["chara_dir"]),
            "readable_docs_dir": Path(readable) if readable else None,
        }
