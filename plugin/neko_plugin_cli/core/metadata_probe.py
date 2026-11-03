# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Derive a plugin's entry metadata at packaging time.

``@plugin_entry`` does not declare ``input_schema``, it derives it — from a
pydantic model, or by inferring one from the handler's own type annotations.
Deriving means importing, and importing means executing the plugin.

That derivation happens here, once, on the author's machine, and the result is
written into the package as ``plugin.meta.json``. The user's machine reads that
file and never imports a plugin it has not been asked to run.

The subprocess isolation is reused as-is from the runtime scanner: the author
is running their own code, but a plugin that hangs or crashes on import should
produce a message rather than wedge the CLI.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

from plugin._types.version import SDK_VERSION
from plugin.core.packaged_metadata import (
    build_environment,
    entries_config_digest,
    PACKAGED_METADATA_FILENAME,
    PACKAGED_METADATA_SCHEMA_VERSION,
    compute_source_sha256,
    empty_source_directories,
    source_directory_names,
    unicode_renamed_source_files,
    source_stat_summary,
)


class MetadataProbeError(RuntimeError):
    """The plugin's metadata could not be derived at build time."""


def _load_logger() -> Any:
    from plugin.logging_config import get_logger

    return get_logger("neko_plugin_cli.metadata_probe")


def derive_plugin_metadata(plugin_dir: Path, *, source_only: bool = False) -> dict[str, object]:
    """Import ``plugin_dir``'s entry class and return its packaged metadata.

    One tree, imported and fingerprinted. Deriving from one tree and
    fingerprinting another is precisely the shape that lets a package advertise
    handlers derived from a file it does not carry, with a hash that verifies
    (codex), so there is no way to ask for it.

    Raises :class:`MetadataProbeError` with the underlying reason when the
    plugin cannot be imported. Callers decide what that means;
    :func:`write_packaged_metadata` turns it into a warning so packaging keeps
    working, while a caller that needs the metadata can treat it as fatal.
    """
    # Lazy import: other CLI commands should not pay framework import costs for this path.
    from plugin.core.registry import _parse_single_plugin_config
    from plugin.server.application.plugins.metadata_scanner import (
        PluginMetadataScanError,
        scan_plugin_metadata_isolated,
    )

    plugin_dir = Path(plugin_dir).expanduser().resolve()
    config_path = plugin_dir / "plugin.toml"
    if not config_path.is_file():
        raise MetadataProbeError(f"missing plugin.toml in {plugin_dir}")

    logger = _load_logger()
    try:
        # Exclude user overrides: this metadata is distributed to others and must not include the author's local profile
        # or runtime configuration (codex).
        ctx = _parse_single_plugin_config(
            config_path, set(), logger, apply_user_overlays=False
        )
    except Exception as exc:
        raise MetadataProbeError(
            f"plugin.toml could not be parsed: {type(exc).__name__}: {exc}"
        ) from exc
    if ctx is None:
        raise MetadataProbeError("plugin.toml could not be parsed or validated")

    entry = str(ctx.entry or "")
    if ":" not in entry:
        raise MetadataProbeError(
            f"entry point must be 'module:Class', got {entry!r}"
        )
    module_path, class_name = entry.split(":", 1)

    # Record the tree before probing. Plugin module-level code may write files during import
    # through initialization or caching; handlers are inferred before that, while fingerprints are computed afterward.
    # The installed tree could pass validation while importing registers entries different from plugin.meta.json
    # (codex).
    digest_before_probe = compute_source_sha256(plugin_dir)
    dirs_before_probe = source_directory_names(plugin_dir)

    try:
        isolated = scan_plugin_metadata_isolated(
            plugin_id=ctx.pid,
            module_path=module_path,
            class_name=class_name,
            config_path=config_path,
            conf=ctx.conf,
            pdata=ctx.pdata,
            python_requirement_paths=ctx.python_requirement_paths,
            # Probe the staged Python source, without creating bytecode that
            # would bypass the builder's earlier cache-file exclusion pass.
            source_only=source_only,
        )
    except PluginMetadataScanError as exc:
        raise MetadataProbeError(
            f"importing the plugin failed ({exc.error_type}): {exc}"
        ) from exc

    empty_dirs = empty_source_directories(plugin_dir)
    if empty_dirs:
        # ZIP omits directory entries, so empty directories do not install; fingerprints cover only files,
        # leaving the installed tree consistent with source_files/source_bytes/source_sha256.
        # A plugin conditionally registering entries based on directory existence would then run without that directory
        # using handlers inferred while it existed (codex).
        raise MetadataProbeError(
            "staged tree contains directories that cannot survive packaging "
            f"({empty_dirs[:3]}); packaging without metadata so the host rescans"
        )
    if (
        compute_source_sha256(plugin_dir) != digest_before_probe
        or source_directory_names(plugin_dir) != dirs_before_probe
    ):
        # Compare directories too. Content digests cover only files, so creating an empty marker directory, registering entries from it,
        # and deleting it leaves identical before/after digests (codex).
        raise MetadataProbeError(
            "importing the plugin changed the staged tree; packaging without "
            "metadata so the host rescans"
        )
    renamed = unicode_renamed_source_files(plugin_dir)
    if renamed:
        # Fingerprints and archive names use NFC, but probing imports the decomposed spelling
        # from the filesystem. On a filesystem retaining original spellings, plugin code hardcoding
        # decomposed literals cannot open files and registers something different from this probe,
        # while both trees have identical fingerprints and the host accepts them (codex). Prefer omitting metadata.
        raise MetadataProbeError(
            "staged file names change under NFC normalization "
            f"({renamed[:3]}); packaging without metadata so the host rescans"
        )
    stat_summary = source_stat_summary(plugin_dir)
    return {
        "schema_version": PACKAGED_METADATA_SCHEMA_VERSION,
        "sdk_version": SDK_VERSION,
        # Hash the actual packaged tree rather than the author's source directory. Build rules
        # (tool.neko.build exclude/exclude_dirs/exclude_files) may omit .py /
        # .toml / .json files, while user machines hash the installed tree. Different trees make
        # every content check incorrectly report source changes, replacing valid schemas with
        # placeholders (greptile). Scanning now uses the same tree; see function entry.
        "source_sha256": compute_source_sha256(plugin_dir),
        # The file inventory makes missing-file detection independent of timestamps and extraction order.
        "source_files": stat_summary.names,
        "source_bytes": stat_summary.total_bytes,
        "build_env": build_environment(),
        # The packaging-time manifest's declared entries table lets the host determine whether user overrides changed
        # entry definitions, rather than merely checking whether the table exists.
        "entries_config_sha256": entries_config_digest(ctx.conf, ctx.pdata),
        "entries": list(isolated.entries_preview),
        "handlers": dict(isolated.handlers),
        "entry_methods": dict(isolated.entry_methods),
    }


def _remove_staged_metadata(path: Path) -> None:
    """Refuse to export a stale generated file that cannot be removed."""
    if not path.exists():
        return
    try:
        path.unlink()
    except OSError:
        try:
            path.chmod(stat.S_IWRITE | stat.S_IREAD)
            path.unlink()
        except OSError as exc:
            raise MetadataProbeError(
                f"stale generated metadata cannot be removed: {path}: {exc}"
            ) from exc


def write_packaged_metadata(
    *,
    source_dir: Path,
    target_dir: Path,
    source_only: bool = False,
) -> Path | None:
    """Derive metadata from the staged tree and write it into ``target_dir``.

    Returns the written path, or ``None`` when the plugin could not be imported
    here. ``source_dir`` is only named in the warnings; the probe itself runs on
    ``target_dir``, the copy that actually goes into the package. Importing the
    author's tree instead lets a build rule exclude a file that decides which
    entries get registered — the package would then carry handlers derived from
    a file it does not contain, and because the fingerprint is taken on the
    staged tree the host's verification passes and it advertises entries the
    installed plugin never registers (codex). Dependencies resolve from the
    staged ``vendor/`` directory, which is copied with everything else before
    this runs.

    A failure warns rather than failing the build. Packaging is not the place to
    insist that a plugin imports: the build machine may be missing an optional
    dependency, the plugin may target another OS, and refusing to produce the
    package at all would turn a metadata optimisation into a packaging gate. The
    host has defined behaviour for a package without metadata — it falls back to
    what the manifest declares — so shipping without it costs a degraded
    parameter form, not a broken plugin.

    The one thing it will not do is ship a package that lies: if a stale
    ``plugin.meta.json`` copied in from the source tree cannot be removed, the
    build fails.
    """
    try:
        payload = derive_plugin_metadata(Path(target_dir), **({"source_only": True} if source_only else {}))
    except MetadataProbeError as exc:
        _remove_staged_metadata(Path(target_dir) / PACKAGED_METADATA_FILENAME)
        print(
            f"[WARN] {Path(source_dir).name}: could not derive plugin metadata "
            f"({exc}); packaging without {PACKAGED_METADATA_FILENAME}. Entry "
            "parameter schemas stay unavailable until the plugin runs.",
            file=sys.stderr,
        )
        return None
    meta_path = Path(target_dir) / PACKAGED_METADATA_FILENAME
    # Use newline="": Windows defaults translate LF to CRLF, making generated files inherently differ
    # from repository versions and causing git status to warn about line-ending changes every time.
    meta_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    return meta_path
