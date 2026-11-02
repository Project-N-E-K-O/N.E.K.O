"""Shared packaged metadata identities and source fingerprints."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from plugin._types.packaged_metadata import PACKAGED_METADATA_FILENAME
from plugin.utils.source_paths import (
    METADATA_PROBE_PREFIX,
    VENDOR_SYNC_PREFIXES,
    is_metadata_probe_path,
    is_vendor_sync_path,
)


# Only the shipped root metadata is generated. Host caches live outside
# installed code; plugin.meta.local.json remains ordinary plugin-owned data.
_GENERATED_METADATA_NAMES = frozenset({PACKAGED_METADATA_FILENAME})

# Schema 4 preserves the complete entry contract, including timeout/result
# fields lost in schema 3. Older schemas must take the isolated scan path.
PACKAGED_METADATA_SCHEMA_VERSION = 4

# Bound allocation before parsing metadata supplied by third-party packages.
MAX_PACKAGED_METADATA_BYTES = 1024 * 1024

# Construct newline bytes explicitly so source line-ending conversion cannot
# alter the normalization rules used by the source fingerprint.
_CR = bytes([13])
_LF = bytes([10])
_CRLF = _CR + _LF

# Normalize CRLF only for text files; CR bytes are significant in binary data.
TEXT_SUFFIXES_FOR_HASHING = frozenset(
    {
        ".py",
        ".pyi",
        ".toml",
        ".json",
        ".yaml",
        ".yml",
        ".ini",
        ".cfg",
        ".txt",
        ".md",
        ".csv",
        ".xml",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".sql",
    }
)

# Prune development artifacts before descending into their directory trees.
SOURCE_IGNORED_DIRS = frozenset(
    {"__pycache__", ".git", ".mypy_cache", ".ruff_cache", ".venv"}
)

# node_modules can ship with a package and affect entry registration. Reject
# the metadata fast path instead of silently excluding those source files.
SOURCE_UNFINGERPRINTABLE_DIRS = frozenset({"node_modules"})

# Omit properties: even an empty object makes the UI show a zero-field form
# instead of accepting raw JSON. Actual validation runs in the plugin process.
PLACEHOLDER_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": True,
}


class PackagedMetadataError(ValueError):
    """The packaged metadata file exists but cannot be used."""


@dataclass(slots=True)
class PackagedPluginMetadata:
    """Validated contents of one plugin's ``plugin.meta.json``."""

    entries: list[dict[str, object]] = field(default_factory=list)
    # Digest of the entries table declared in plugin.toml at packaging time, used to detect
    # changes introduced by user configuration overrides.
    entries_config_sha256: str = ""
    # Metadata registered in state.event_handlers, plus entry_id -> method name.
    # Starting a plugin previously imported it again for these two values, despite the plugin process already importing it.
    # This was redundant (Codex); carrying them leaves only the host process's import in start_plugin.
    handlers: dict[str, dict[str, object]] = field(default_factory=dict)
    entry_methods: dict[str, str] = field(default_factory=dict)
    sdk_version: str = ""
    source_sha256: str = ""
    # Whether the packaging and current machine share (os, python, arch).
    built_in_this_environment: bool = False


def build_environment() -> dict[str, str]:
    """The parts of the environment that can change what a plugin registers.

    A plugin is free to register different entries under different operating
    systems or Python versions — an optional import that only resolves on
    Windows, an entry gated on ``sys.version_info``. Packaged metadata is one
    machine's answer, so anything that treats it as *the* set of callable
    entries has to know whether it was produced here (codex).
    """
    # CPython 3.11 obtains the Windows machine value from these two variables,
    # preferring the native architecture for WOW64. platform.machine() also
    # calls win32_ver(), which starts a shell solely to discover the OS version.
    # Keep the same architecture spelling, including an unknown/empty value,
    # without paying for unrelated version discovery. Other platforms retain
    # platform.machine() so their existing metadata fingerprints stay valid.
    if sys.platform == "win32":
        architecture = os.environ.get("PROCESSOR_ARCHITEW6432", "") or os.environ.get(
            "PROCESSOR_ARCHITECTURE", ""
        )
        if architecture == "unknown":
            architecture = ""
    else:
        architecture = platform.machine()
    return {
        "os": sys.platform,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "arch": architecture,
    }


def _iter_source_files(
    plugin_dir: Path,
) -> tuple[list[tuple[str, str, os.stat_result]], bool, list[str]]:
    # Include data files as well as code: module-level plugin registration may
    # derive entries from YAML, CSV, templates or other packaged resources.
    # Descend with explicit scandir rather than rglob: prune ignored directories before descent,
    # or development directories with large object databases must be fully enumerated before filtering.
    #
    # Do not follow symlinks, but record them: following may enter huge site-packages trees or cycles,
    # while silently skipping would make retargeting a symlink invisible. Record their presence so
    # callers mark the entire tree untrusted.
    # saw_symlink flags an untrusted tree; symlinks are only its commonest source. Unreadable directories
    # and nonregular files such as FIFOs/sockets/device nodes also set it.
    root = str(plugin_dir)
    vendor = str(plugin_dir / "vendor")
    files: list[tuple[str, str, os.stat_result]] = []
    dirs: list[str] = [str(plugin_dir)]
    saw_symlink = os.path.islink(str(plugin_dir))
    stack = [str(plugin_dir)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as scan:
                children = list(scan)
        except OSError:
            saw_symlink = True
            continue
        for entry in children:
            # Skip generated work trees before descending or inspecting links.
            if current in (root, vendor) and entry.name.startswith(VENDOR_SYNC_PREFIXES):
                relative = (
                    Path(entry.name) if current == root else Path("vendor", entry.name)
                )
                if is_vendor_sync_path(relative):
                    continue
            try:
                if entry.is_symlink():
                    saw_symlink = True
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in SOURCE_UNFINGERPRINTABLE_DIRS:
                        saw_symlink = True
                        continue
                    if entry.name not in SOURCE_IGNORED_DIRS:
                        stack.append(entry.path)
                        # Check directories' own mtime too. Deleting a file updates no surviving
                        # file, so a file-mtime-only fast path misses missing source and leaves
                        # the host using schema inferred before deletion
                        # (Codex). Adding/removing entries updates the parent directory's mtime.
                        dirs.append(entry.path)
                    continue
                if (
                    current == root
                    and entry.name.startswith(METADATA_PROBE_PREFIX)
                    and is_metadata_probe_path(Path(entry.name))
                ):
                    continue
                if entry.name in _GENERATED_METADATA_NAMES and current == root:
                    # Generated output does not determine its own freshness, but only the root copy is generated.
                    # Excluding by filename everywhere also removes runtime files such as a plugin's own
                    # data/plugin.meta.json, while packaging still includes them: changing their contents
                    # would change no fingerprint (Codex).
                    continue
                if not entry.is_file(follow_symlinks=False):
                    # ⚠️ Accept only regular files. FIFOs, sockets, and device nodes pass stat(), but hashing uses
                    # read_bytes(): a FIFO with no writer blocks forever. Refresh now holds
                    # _REGISTRY_REFRESH_LOCK throughout, so one named pipe could freeze the entire plugin
                    # registry (CodeRabbit). Treat like symlinks: record it and mark the tree untrusted.
                    saw_symlink = True
                    continue
                stat_result = entry.stat(follow_symlinks=False)
            except OSError:
                saw_symlink = True
                continue
            rel_path = os.path.relpath(entry.path, str(plugin_dir)).replace(os.sep, "/")
            # Record NFC names, but read using filesystem spelling. Archive filenames are already NFC
            # (normalize_relative_posix), whereas macOS often returns decomposed names:
            # without normalization the same file yields different manifests/digests, falsely marking
            # every metadata entry stale (Codex). Conversely, opening the normalized spelling may fail
            # on filesystems preserving original spelling, so retain both.
            files.append(
                (unicodedata.normalize("NFC", rel_path), rel_path, stat_result)
            )
    files.sort(key=lambda item: item[0])
    return files, saw_symlink, dirs


@dataclass(frozen=True)
class SourceStatSummary:
    """Everything the cheap freshness checks need, from one stat walk.

    The names, the newest timestamp and the total size used to cost a separate
    descent each. They come from the same ``scandir`` walk, and the refresh path
    holds the registry lock while it runs them.
    """

    names: list[str] = field(default_factory=list)
    newest_mtime_ns: int = 0
    total_bytes: int = 0
    # Excludes directory mtimes, which writes beside a target also move.
    newest_file_mtime_ns: int = 0
    untrustworthy: bool = False


def source_stat_summary(plugin_dir: Path) -> SourceStatSummary:
    """Names, newest mtime and total size of the files the fingerprint covers.

    Sizes sit next to the timestamps because timestamps alone miss a source
    replaced without advancing its mtime — a restore that preserves metadata,
    an edit inside one tick of a coarse filesystem clock (codex). Sizes catch
    the overwhelming majority of those. What neither catches is a same-size,
    same-mtime rewrite; the only thing that would is hashing every plugin's
    whole tree on every refresh, which is the cost this file exists to avoid.

    Directory mtimes count too: deleting a source file leaves every surviving
    file untouched, so a file-only check cannot see that the tree lost a piece.
    """
    files, untrustworthy, dirs = _iter_source_files(plugin_dir)
    newest = 0
    total = 0
    for _key, _real, stat_result in files:
        newest = max(newest, stat_result.st_mtime_ns)
        total += stat_result.st_size
    newest_file = newest
    for dir_path in dirs:
        try:
            newest = max(newest, os.stat(dir_path).st_mtime_ns)
        except OSError:
            untrustworthy = True
    return SourceStatSummary(
        names=[key for key, _real, _stat in files],
        newest_mtime_ns=newest,
        total_bytes=total,
        untrustworthy=untrustworthy,
        newest_file_mtime_ns=newest_file,
    )


def source_directory_names(plugin_dir: Path) -> list[str]:
    """Sorted relative paths of every directory the walk descends into.

    The content digest covers files, so it cannot see a directory appear or
    vanish on its own. Packaging compares this across the probe: module-level
    code can create an entry from a marker directory's presence and then delete
    it, leaving both digests identical (codex).
    """
    _files, _untrustworthy, dirs = _iter_source_files(plugin_dir)
    root = str(plugin_dir)
    return sorted(
        os.path.relpath(path, root).replace(os.sep, "/")
        for path in dirs
        if path != root
    )


def empty_source_directories(plugin_dir: Path) -> list[str]:
    """Directories in the tree that hold no fingerprinted file, at any depth.

    Both exporters write files only, so a directory with nothing in it never
    reaches the installed tree — and the fingerprint covers files, so the
    installed tree still matches. A plugin that registers entries depending on a
    directory's presence would be probed with it and run without it (codex).
    """
    files, _untrustworthy, dirs = _iter_source_files(plugin_dir)
    root = str(plugin_dir)
    holding: set[str] = set()
    for _key, real_rel, _stat in files:
        parent = os.path.dirname(os.path.join(root, real_rel.replace("/", os.sep)))
        while len(parent) >= len(root):
            holding.add(parent)
            if parent == root:
                break
            parent = os.path.dirname(parent)
    return sorted(
        os.path.relpath(path, root).replace(os.sep, "/")
        for path in dirs
        if path != root and path not in holding
    )


def unicode_renamed_source_files(plugin_dir: Path) -> list[str]:
    """Staged files whose recorded name differs from their spelling on disk.

    The fingerprint records NFC, and so does the archive writer; the probe
    imports whatever the filesystem hands back. When those differ, a plugin that
    opens a decomposed literal registers fine here and breaks after extraction
    onto a spelling-preserving filesystem — while both trees fingerprint the
    same, so the host trusts the metadata anyway (codex).

    Compares the two spellings directly. Asking whether the NFC path *exists* is
    useless on exactly the filesystems this targets: macOS resolves canonically
    equivalent names, so the normalized name is always found (codex).
    """
    files, _untrustworthy, _dirs = _iter_source_files(plugin_dir)
    return [key for key, real_rel, _stat in files if key != real_rel]


def source_file_names(plugin_dir: Path) -> tuple[list[str], bool]:
    """Sorted relative paths of the files the fingerprint covers."""
    summary = source_stat_summary(plugin_dir)
    return summary.names, summary.untrustworthy


def compute_source_sha256(plugin_dir: Path) -> str:
    """Content digest of a plugin's source files, stable across packaging.

    Stamped into the metadata at packaging time. On the refresh path it is only
    reached when mtimes already suggest the sources moved: hashing every plugin
    file costs hundreds of milliseconds against tens for a stat walk, so the
    cheap check runs first and this one decides.
    """
    files, saw_symlink, _dirs = _iter_source_files(plugin_dir)
    digest = hashlib.sha256()
    if saw_symlink:
        digest.update(b"<symlink-or-unreadable>\0")
    for key, real_rel, _stat_result in files:
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        try:
            # Normalize line endings before hashing. .gitattributes pins repository text to LF, but hashing
            # must not depend on it: Windows-authored packages hashed with CRLF would otherwise
            # appear changed on Linux users' machines, degrading everything to placeholders.
            #
            # ⚠️ Fold CRLF only, never bare CR. Treating CR as LF would let replacing every LF with CR
            # preserve the digest: paths, byte counts, and hashes would all match, bypassing even
            # the slow path (Codex). Git translates only LF↔CRLF and never creates bare CR,
            # so omitting that fold preserves the intended fix.
            raw = (plugin_dir / real_rel).read_bytes()
            if Path(real_rel).suffix.lower() in TEXT_SUFFIXES_FOR_HASHING:
                raw = raw.replace(_CRLF, _LF)
            digest.update(raw)
        except OSError as exc:
            raise PackagedMetadataError(
                f"cannot read plugin source file for hashing: {real_rel}: {exc}"
            ) from exc
        digest.update(b"\0")
    return digest.hexdigest()


def entries_config_digest(conf: object, pdata: object) -> str:
    """Digest of the ``entries`` table the effective configuration declares.

    Packaging records this for the staged ``plugin.toml``; the host computes it
    from the configuration a plugin would actually run under. Equal means no
    overlay touched ``entries`` and the packaged metadata still describes this
    machine.

    Comparing digests rather than asking "does a table exist" fixes two mirror
    errors (codex). A plugin that declares ``entries`` in its own manifest was
    being treated as user-overridden, so it never got its build-time schemas and
    re-imported on every start. And an overlay that sets ``entries = []`` to
    remove them is a real override that a truthiness test reads as absence.
    """
    for table in (conf, pdata):
        if isinstance(table, Mapping) and "entries" in table:
            payload = json.dumps(
                table["entries"], sort_keys=True, ensure_ascii=False, default=str
            )
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return ""


@dataclass(frozen=True, slots=True)
class SourceTreeSnapshot:
    """What a tree looked like before the plugin was imported.

    The packager takes the same snapshot before its probe and refuses to write
    metadata when the import changed the tree: handlers derived during the
    import and a fingerprint taken after it can describe two different trees
    (codex). ``None`` from :func:`snapshot_source_tree` means the tree could
    not be read, which also refuses the upgrade.
    """

    sha256: str
    directories: tuple[str, ...]
