"""Workbench projects as external plugin sources.

A project is a plugin source directory outside the managed plugin roots. Its
identity lives in the workbench store; everything else is read from the source
itself whenever it is needed. Discovery, lifecycle, hot reload and builds treat
these sources like the source plugins under ``plugin/plugins``, except that they
never start automatically and they are imported from their own directory.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from plugin import settings
from plugin.core.entry_points import (
    describe_plugin_entry_directory_mismatch,
    normalize_plugin_entry_point,
)
from plugin.utils.source_paths import is_vendor_sync_path

PROJECT_SOURCE = "project"
# Directories whose ``.py`` files are not plugin-owned source. Shared by the
# reload preflight and the hot-reload watcher so both judge the same files.
SOURCE_EXCLUDED_DIR_NAMES = frozenset(
    {"vendor", ".venv", ".git", "__pycache__", "node_modules"}
)


@dataclass(frozen=True)
class SourceMetadata:
    plugin_id: str
    name: str
    version: str
    entry: str


@dataclass(frozen=True)
class ProjectSource:
    project_id: str
    plugin_id: str
    source_dir: Path


@dataclass(frozen=True)
class ProjectSourceFailure:
    project_id: str
    source_dir: Path
    error: str


def _load_plugin_source(source_dir: Path):
    from plugin.neko_plugin_cli.core.plugin_source import load_plugin_source

    return load_plugin_source(source_dir)


def managed_roots() -> tuple[Path, ...]:
    return tuple(
        Path(root).resolve()
        for root in (*settings.PLUGIN_CONFIG_ROOTS, settings.get_plugin_state_root())
    )


def inspect_source_directory(source_dir: Path) -> SourceMetadata:
    """Validate a resolved project directory without importing it.

    Raises ``ValueError`` with an English diagnostic when the directory cannot
    be loaded as an external source plugin.
    """
    if not source_dir.is_dir():
        raise ValueError("Project directory does not exist or is not accessible")
    for root in managed_roots():
        if source_dir.is_relative_to(root) or root.is_relative_to(source_dir):
            raise ValueError(f"Project directory overlaps a managed plugin directory: {root}")
    try:
        source = _load_plugin_source(source_dir)
    except (OSError, TypeError, KeyError) as exc:
        raise ValueError(str(exc)) from exc
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", source.plugin_id):
        raise ValueError(f"Invalid plugin ID: {source.plugin_id!r}")
    if source_dir.name != source.plugin_id:
        raise ValueError(
            f"Project directory must be named after its plugin ID '{source.plugin_id}', "
            f"not '{source_dir.name}'"
        )
    entry = source.plugin_toml.get("plugin", {}).get("entry", "")
    if not isinstance(entry, str) or ":" not in entry:
        raise ValueError("plugin.entry must name a module and class")
    manifest = source_dir / "plugin.toml"
    entry = normalize_plugin_entry_point(
        entry, config_path=manifest, builtin_plugin_root=settings.BUILTIN_PLUGIN_CONFIG_ROOT
    )
    mismatch = describe_plugin_entry_directory_mismatch(entry, config_path=manifest)
    if mismatch:
        raise ValueError(mismatch)
    module, class_name = entry.split(":", 1)
    parts = module.split(".")
    if (
        parts[0] != "plugins"
        or len(parts) < 2
        or parts[1] != source_dir.name
        or not all(part.isidentifier() for part in parts[2:])
        or not class_name.isidentifier()
    ):
        raise ValueError("Entry must use plugins.<directory>[.<module>]:<class>")
    module_dir = source_dir.joinpath(*parts[2:])
    candidates = [module_dir / "__init__.py"]
    if len(parts) > 2:
        candidates.append(module_dir.with_suffix(".py"))
    if not any(path.is_file() and path.resolve().is_relative_to(source_dir) for path in candidates):
        raise ValueError(f"Entry Python source not found for {entry}")
    return SourceMetadata(source.plugin_id, source.name, source.version, entry)


def preflight_source(source_dir: Path, plugin_id: str) -> None:
    """Check a source tree before replacing a healthy process; never imports it.

    Raises ``ValueError`` when the manifest, entry, dependencies or any
    plugin-owned ``.py`` file would fail to load.
    """
    from dataclasses import replace

    from plugin.core.registry import _parse_single_plugin_config
    from plugin.server.application.plugins.registry_service import (
        _build_discovery_payload,
        logger,
    )

    ctx = _parse_single_plugin_config(source_dir / "plugin.toml", set(), logger)
    if ctx is None or ctx.pid != plugin_id:
        raise ValueError("Plugin manifest could not be validated")
    payload = _build_discovery_payload(replace(ctx, enabled=True), plugin_id=ctx.pid)
    if payload.get("runtime_load_state") == "failed":
        raise ValueError(str(payload.get("runtime_load_error_message")))

    def walk_error(exc: OSError) -> None:
        raise ValueError(str(exc)) from exc

    for root, directories, files in os.walk(source_dir, onerror=walk_error, followlinks=False):
        directories[:] = [
            name
            for name in directories
            if name not in SOURCE_EXCLUDED_DIR_NAMES
            # `neko-plugin sync` staging/backup trees at the plugin root hold
            # (possibly half-written) third-party code, like vendor/.
            and not (Path(root) == source_dir and is_vendor_sync_path(Path(name)))
        ]
        for name in files:
            if not name.lower().endswith(".py"):
                continue
            path = Path(root) / name
            relative = path.relative_to(source_dir)
            if not path.resolve().is_relative_to(source_dir):
                raise ValueError(f"Python source escapes the project directory: {relative}")
            try:
                compile(path.read_bytes(), str(path), "exec")
            except (SyntaxError, OSError) as exc:
                raise ValueError(str(exc)) from exc


def list_project_sources_sync() -> tuple[list[ProjectSource], list[ProjectSourceFailure]]:
    """Return loadable project sources; a plugin ID is owned by its first project.

    Raises the store's ``ServerDomainError`` when the workbench store itself
    cannot be read.
    """
    from plugin.server.infrastructure import authoring_project_store as store

    sources: list[ProjectSource] = []
    failures: list[ProjectSourceFailure] = []
    owners: dict[str, Path] = {}
    for record in store.list_records():
        try:
            metadata = inspect_source_directory(record.source_dir)
        except ValueError as exc:
            failures.append(ProjectSourceFailure(record.project_id, record.source_dir, str(exc)))
            continue
        owner = owners.get(metadata.plugin_id)
        if owner is not None:
            failures.append(
                ProjectSourceFailure(
                    record.project_id,
                    record.source_dir,
                    f"Plugin ID '{metadata.plugin_id}' is already provided by {owner}",
                )
            )
            continue
        owners[metadata.plugin_id] = record.source_dir
        sources.append(ProjectSource(record.project_id, metadata.plugin_id, record.source_dir))
    return sources, failures


def project_source_dir(meta: object) -> Path | None:
    """Source directory of a registry entry that comes from a workbench project."""
    if not isinstance(meta, dict) or meta.get("source") != PROJECT_SOURCE:
        return None
    raw = meta.get("source_dir")
    return Path(raw) if isinstance(raw, str) and raw else None
