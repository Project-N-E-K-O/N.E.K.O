"""Plugin directory scanner for the install-source lock subsystem.

See design §4 and task 2.1. The scanner walks the two PLUGIN_CONFIG_ROOTS
(``builtin_root`` / ``user_root``), enumerates their **top-level**
subdirectories, and produces a :class:`DiscoveredPlugin` record for each
one. The output feeds the reconciler's three-way diff in
:class:`InstallSourceManager` (design §6.2).

Design notes
------------

* **Top-level only**: only the immediate children of each root are scanned
  — nested "plugin-inside-plugin" layouts are never treated as independent
  plugins. This matches the primary-key semantics of Req 4.1, where the
  key is ``(root_id, directory_name)`` and ``directory_name`` is always a
  single path component under the root.

* **Hidden / private directories are skipped**: names starting with ``.``
  or ``_`` are ignored. These are reserved for VCS metadata
  (``.git``, ``.DS_Store``) and in-progress installer scratch areas
  (``_staging``), neither of which is a real plugin.

* **Symlinks are skipped**: per the task description a scanned child must
  satisfy ``is_dir() and not is_symlink()``. We do NOT follow symlinks
  into plugin trees because :func:`classify_plugin_path` resolves paths
  through :meth:`Path.resolve`, and a symlink pointing outside the root
  would either spuriously classify as ``PATH_OUTSIDE_ROOTS`` or
  cross-contaminate the ``builtin``/``user`` boundary.

* **Plugin-id best-effort (Fix 1)**: :meth:`_load_plugin_id` reads
  ``plugin.toml`` if present. Any failure — missing file, unreadable TOML,
  wrong types, permission errors — collapses to ``""``. Write-path callers
  (``record_import`` / ``record_market``) reuse the same staticmethod so
  that the ``plugin_id`` column is populated as soon as the directory is
  materialised, without waiting for the next reconcile.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from plugin.logging_config import get_logger
from plugin.server.application.install_source.manager import (
    InstallSourceError,
    classify_plugin_path,
)
from plugin.server.application.install_source.models import RootId

logger = get_logger("server.application.install_source")


@dataclass(frozen=True)
class DiscoveredPlugin:
    """A single plugin directory discovered by :class:`PluginDirectoryScanner`.

    ``directory_path`` is always the resolved absolute path (via
    :meth:`Path.resolve` with ``strict=False``) so that downstream callers
    can rely on it for primary-key lookups without re-normalising.

    ``plugin_id`` may be ``""`` when ``plugin.toml`` is missing or
    unreadable (Fix 1 / Req 3.3). Callers that need a guaranteed
    non-empty id must fall back to ``directory_name`` as the
    human-visible label.
    """

    root_id: RootId
    directory_name: str
    directory_path: Path
    plugin_id: str


class PluginDirectoryScanner:
    """Enumerate plugin directories under the two PLUGIN_CONFIG_ROOTS.

    The scanner is stateless between :meth:`scan` calls — each invocation
    re-reads the filesystem. Roots are resolved once in ``__init__`` so
    that relative-path inputs still classify correctly even if the CWD
    changes between construction and scanning.
    """

    def __init__(self, builtin_root: Path, user_root: Path) -> None:
        # Normalise both roots up front. ``strict=False`` keeps us happy
        # when e.g. a fresh test tmp_path hasn't materialised the
        # directory yet — :meth:`scan` handles missing-root gracefully.
        self._builtin_root: Path = builtin_root.resolve(strict=False)
        self._user_root: Path = user_root.resolve(strict=False)

    def scan(self) -> list[DiscoveredPlugin]:
        """Return a list of every plugin directory under either root.

        Iteration order is: builtin root first (entries sorted by name),
        then user root (entries sorted by name). Within each root the
        children are enumerated via ``sorted(root.iterdir())`` so that
        callers see a deterministic order that matches the serializer's
        ``(root_id, directory_name)`` sort rule (Req 13.4).

        Missing roots, symlinked roots, non-directory roots, or
        permission errors on ``iterdir`` all degrade to "contribute
        zero discoveries from this root" with a WARN log — the scanner
        never raises.
        """

        discovered: list[DiscoveredPlugin] = []
        for root_id, root in (
            ("builtin", self._builtin_root),
            ("user", self._user_root),
        ):
            discovered.extend(self._scan_root(root_id, root))
        return discovered

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _scan_root(self, root_id: RootId, root: Path) -> list[DiscoveredPlugin]:
        """Enumerate one root. Never raises."""

        if not root.exists() or not root.is_dir():
            # Fresh install or mis-configured root: nothing to scan, but
            # the reconciler still needs to run so we silently return [].
            return []

        try:
            children = sorted(root.iterdir())
        except (PermissionError, OSError) as exc:
            logger.warning(
                "PluginDirectoryScanner: cannot iterate root %s (%s): %s",
                root_id,
                root,
                exc,
            )
            return []

        out: list[DiscoveredPlugin] = []
        for child in children:
            name = child.name
            # Hidden / private scratch directories never count as plugins.
            if name.startswith(".") or name.startswith("_"):
                continue
            # Skip symlinks outright — even if they resolve to a dir we
            # don't want to cross the builtin/user boundary (see module
            # docstring).
            if child.is_symlink():
                continue
            try:
                if not child.is_dir():
                    continue
            except OSError as exc:
                # ``is_dir`` can raise on broken filesystems / ACL issues.
                logger.warning(
                    "PluginDirectoryScanner: skipping %s due to stat error: %s",
                    child,
                    exc,
                )
                continue

            resolved = child.resolve(strict=False)
            try:
                classified_root_id, directory_name = classify_plugin_path(
                    resolved,
                    builtin_root=self._builtin_root,
                    user_root=self._user_root,
                )
            except InstallSourceError as exc:
                # Shouldn't normally happen — ``child`` lives under
                # ``root`` by construction. But symlinks that we already
                # filtered out can still leave surprises on weird
                # filesystems, so we log and move on rather than abort
                # the whole scan.
                logger.warning(
                    "PluginDirectoryScanner: %s classified outside roots: %s",
                    resolved,
                    exc,
                )
                continue

            plugin_id = self._load_plugin_id(resolved)
            out.append(
                DiscoveredPlugin(
                    root_id=classified_root_id,
                    directory_name=directory_name,
                    directory_path=resolved,
                    plugin_id=plugin_id,
                )
            )
        return out

    @staticmethod
    def _load_plugin_id(dir_path: Path) -> str:
        """Best-effort read of the plugin id from ``plugin.toml`` (Fix 1).

        Lookup order:

        1. ``[plugin] id = "..."`` — the canonical layout used by the
           sample plugins shipped in this repo.
        2. Top-level ``id = "..."`` — accepted for forward compatibility
           with older / simpler plugin manifests.

        Any failure — missing ``plugin.toml``, invalid TOML, wrong value
        type, empty string, permission errors, OS errors — returns
        ``""`` without raising. The caller is expected to treat an
        empty id as a placeholder (Req 3.3) and fall back to
        ``directory_name`` for display purposes.

        This is exposed as a ``@staticmethod`` so that the write path
        in :class:`InstallSourceManager` can reuse exactly the same
        extraction logic without instantiating a scanner.
        """

        toml_path = dir_path / "plugin.toml"
        try:
            if not toml_path.is_file():
                return ""
            with toml_path.open("rb") as fp:
                data = tomllib.load(fp)
        except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
            logger.debug(
                "PluginDirectoryScanner: unable to read plugin.toml at %s: %s",
                toml_path,
                exc,
            )
            return ""

        # Prefer [plugin] id over a top-level id.
        plugin_section = data.get("plugin")
        if isinstance(plugin_section, dict):
            candidate = plugin_section.get("id")
            if isinstance(candidate, str) and candidate:
                return candidate

        candidate = data.get("id")
        if isinstance(candidate, str) and candidate:
            return candidate

        return ""
