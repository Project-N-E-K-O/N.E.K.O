"""Build registered development sources without broadening installation ownership."""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import threading
from typing import Any
import uuid

from plugin.neko_plugin_cli.core.build_rules import load_build_rules, should_skip_path
from plugin.neko_plugin_cli.core.dependencies import validate_source_dependency_layout
from plugin.neko_plugin_cli.core.plugin_source import load_plugin_source
from plugin.neko_plugin_cli.core.build import PluginBuilder
from plugin.neko_plugin_cli.public import inspect_package
from plugin.server.application.plugin_cli.development_artifacts import development_artifacts_root


def build_plugin(*args, source_only_roots=(), **kwargs):
    return PluginBuilder(source_only_roots=source_only_roots).build_plugin(*args, **kwargs)


def build_bundle(*args, source_only_roots=(), **kwargs):
    return PluginBuilder(source_only_roots=source_only_roots).build_bundle(*args, **kwargs)


def resolve_development_sources(mode: str, ref: dict | None, refs: list[dict]) -> list[Any]:
    from plugin.server.application.plugins.development import (
        development_enabled_sync, development_registry_lock,
        list_registration_records_sync, resolve_development_ref_sync,
    )

    if ref and mode != "single" or refs and mode not in {"selected", "bundle"}:
        raise ValueError("Development references do not match the build mode")
    if mode == "all":
        # Capture registered identities here; validate each source inside its
        # build group so one missing directory cannot abort unrelated builds.
        with development_registry_lock:
            return list_registration_records_sync() if development_enabled_sync() else []
    return [resolve_development_ref_sync(item["registration_id"], item["revision"])
            for item in ([ref] if ref else refs)]


def _fingerprint(source_dir: Path) -> dict[str, tuple[int, int, int, str]]:
    source = load_plugin_source(source_dir)
    rules = load_build_rules(source.pyproject_toml)
    result = {}
    # These files also drive metadata/default-profile generation even when an
    # include rule excludes them from the runtime payload.
    controls = {"plugin.toml", "pyproject.toml", "config.example.toml"}
    for root, directories, files in os.walk(source_dir, followlinks=False):
        root_path = Path(root)
        for name in list(directories):
            directory = root_path / name
            relative = directory.relative_to(source_dir)
            if should_skip_path(relative, is_dir=True, rules=rules):
                directories.remove(name)
                continue
            if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
                raise ValueError(f"Development build cannot follow linked directories: {relative}")
        for name in sorted(files):
            path = root_path / name
            relative = path.relative_to(source_dir)
            if relative.as_posix() not in controls and should_skip_path(relative, is_dir=False, rules=rules):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(source_dir):
                raise ValueError(f"Development build cannot follow linked files: {relative}")
            before = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns
            ):
                raise ValueError("Source changed during build; retry after saving your files")
            result[relative.as_posix()] = (after.st_size, after.st_mtime_ns, after.st_ctime_ns, digest)
    return result


def _require_output(path: Path, target_root: Path, source_dirs: list[Path]) -> Path:
    path = path.resolve()
    if not path.is_relative_to(target_root.resolve()):
        raise ValueError("Build output must remain inside the plugin package directory")
    if any(path.is_relative_to(source) for source in source_dirs):
        raise ValueError("Build output cannot be inside a development source directory")
    return path


async def _publish_with_operation_lock(publish) -> None:
    from plugin.server.application.plugins._env_budgets import env_seconds
    from plugin.server.application.plugins.operation_lock import (
        bounded_operation_wait, plugin_operation_lock,
    )

    with bounded_operation_wait(env_seconds("NEKO_PLUGIN_OPERATION_WAIT_BUDGET", 20.0)):
        async with plugin_operation_lock.hold():
            await asyncio.to_thread(publish)


def build_development_sources(
    *, development: list[Any], ordinary: list[Any], mode: str,
    target_root: Path, target_dir: str | None, out: str | None,
    keep_staging: bool, bundle_id: str | None, package_name: str | None,
    package_description: str | None, version: str | None,
    cancelled: threading.Event | None = None,
) -> dict[str, object]:
    from plugin.server.application.plugins.development import (
        development_snapshot_guard_sync, validate_development_snapshot_sync,
    )

    # Only registered snapshots may introduce paths outside builtin/user roots.
    by_path: dict[Path, tuple[str, Any | None]] = {}
    failed: list[dict[str, object]] = []
    ordinary_by_id = {}
    for item in ordinary:
        previous = ordinary_by_id.get(item.plugin_id)
        if previous is not None and previous.plugin_dir.resolve() != item.plugin_dir.resolve():
            if {previous.root_id, item.root_id} == {"builtin", "user"}:
                ordinary_by_id[item.plugin_id] = item if item.root_id == "user" else previous
                continue
            raise ValueError("Build sources contain conflicting plugin IDs")
        ordinary_by_id[item.plugin_id] = item
    for item in ordinary_by_id.values():
        by_path[item.plugin_dir.resolve()] = (item.plugin_id, None)
    for item in development:
        try:
            validate_development_snapshot_sync(item)
            by_path[item.source_dir.resolve()] = (item.plugin_id, item)
        except Exception as exc:
            failed.append({"plugin": item.plugin_id, "error": str(exc)})
    source_dirs = list(by_path)
    if failed and (mode in {"single", "bundle"} or not source_dirs):
        return {"built": [], "built_count": 0, "failed": failed,
                "failed_count": len(failed), "ok": False}
    if mode == "single" and len(source_dirs) != 1:
        raise ValueError("A single build accepts exactly one source")
    identities = [value[0] for value in by_path.values()]
    if len(set(identities)) != len(identities):
        raise ValueError("Build sources contain conflicting plugin IDs")
    if out and mode != "bundle" and len(source_dirs) != 1:
        raise ValueError("'out' can only be used when building a single plugin")
    output_root = _require_output(
        Path(target_dir).expanduser() if target_dir else target_root, target_root, source_dirs,
    )
    requested_output = _require_output(Path(out).expanduser(), target_root, source_dirs) if out else None
    built: list[dict[str, object]] = []
    groups = [source_dirs] if mode == "bundle" else [[source] for source in source_dirs]
    for group in groups:
        try:
            if cancelled is not None and cancelled.is_set():
                raise ValueError("Development build cancelled")
            with tempfile.TemporaryDirectory(prefix="neko-development-build-") as temporary:
                staging = Path(temporary)
                fingerprints = {}
                staged_dirs = []
                source_only_roots = []
                for index, source_dir in enumerate(group):
                    _, registration = by_path[source_dir]
                    if registration is not None:
                        validate_development_snapshot_sync(registration)
                    source = load_plugin_source(source_dir)
                    if registration is not None and source_dir.name != source.plugin_id:
                        raise ValueError(
                            f"Cannot package '{source.plugin_id}': installation uses a directory "
                            f"named '{source.plugin_id}', but the source package is '{source_dir.name}'. "
                            "Rename the source directory and update its entry/imports to match the "
                            "plugin ID, then rebind the development directory and retry."
                        )
                    validate_source_dependency_layout(source)
                    before = _fingerprint(source_dir)
                    # Keep the original package directory name for import probing.
                    staged = staging / "sources" / str(index) / source_dir.name
                    staged.mkdir(parents=True)
                    for relative, expected in before.items():
                        if cancelled is not None and cancelled.is_set():
                            raise ValueError("Development build cancelled")
                        destination = staged / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source_dir / relative, destination)
                        if hashlib.sha256(destination.read_bytes()).hexdigest() != expected[3]:
                            raise ValueError("Source changed during build; retry after saving your files")
                    if _fingerprint(source_dir) != before:
                        raise ValueError("Source changed during build; retry after saving your files")
                    fingerprints[source_dir] = before
                    staged_dirs.append(staged)
                    if registration is not None:
                        source_only_roots.append(staged)
                suffix = ".neko-bundle" if mode == "bundle" else ".neko-plugin"
                archive = staging / ("output" + suffix)
                if mode == "bundle":
                    result = build_bundle(
                        staged_dirs, archive, bundle_id=bundle_id,
                        package_name=package_name, package_description=package_description,
                        version=version or "0.1.0", keep_staging=keep_staging,
                        source_only_roots=tuple(source_only_roots),
                    )
                else:
                    result = build_plugin(staged_dirs[0], archive, keep_staging=keep_staging,
                                          source_only_roots=tuple(source_only_roots))
                if inspect_package(archive).payload_hash_verified is not True:
                    raise ValueError("Generated package failed integrity verification")
                for source_dir in group:
                    if _fingerprint(source_dir) != fingerprints[source_dir]:
                        raise ValueError("Source changed during build; retry after saving your files")
                    registration = by_path[source_dir][1]
                    if registration is not None:
                        validate_development_snapshot_sync(registration)
                # Always unique, including caller-suggested names: parallel builds
                # never replace one another or publish an incomplete zip.
                name = requested_output.stem if requested_output else result.plugin_id
                destination_root = requested_output.parent if requested_output else output_root
                publication_root = target_root
                if any(by_path[path][1] is not None for path in group):
                    # Keep caller-suggested relative directories/names, but never
                    # publish development bytes to the public package surface.
                    publication_root = development_artifacts_root(target_root)
                    destination_root = publication_root / destination_root.relative_to(target_root.resolve())
                destination = _require_output(
                    destination_root / f"{name}-{uuid.uuid4().hex}{suffix}", publication_root, source_dirs,
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                # Stage beside the final file for atomic rename across volumes.
                pending = destination.with_suffix(destination.suffix + ".pending")
                try:
                    shutil.copyfile(archive, pending)
                    registrations = [by_path[path][1] for path in group if by_path[path][1] is not None]
                    def publish() -> None:
                        with development_snapshot_guard_sync(registrations):
                            for source_dir in group:
                                if _fingerprint(source_dir) != fingerprints[source_dir]:
                                    raise ValueError("Source changed during build; retry after saving your files")
                            if cancelled is not None and cancelled.is_set():
                                raise ValueError("Development build cancelled")
                            os.replace(pending, destination)

                    asyncio.run(_publish_with_operation_lock(publish))
                finally:
                    pending.unlink(missing_ok=True)
                payload = result.model_dump(mode="json")
                payload["package_path"] = str(destination)
                built.append(payload)
        except Exception as exc:
            failed.append({"plugin": ",".join(by_path[path][0] for path in group), "error": str(exc)})
    return {"built": built, "built_count": len(built), "failed": failed,
            "failed_count": len(failed), "ok": not failed}
