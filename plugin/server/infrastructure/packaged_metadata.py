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

"""Read the metadata a plugin package carries with it.

Plugin metadata used to be produced by importing the plugin in a throwaway
subprocess on the user's machine, once per plugin, on every registry refresh.
Importing is executing: a plugin only had to sit in the plugins directory to
get its module-level code run, and starting one plugin imported every other.

The derivation now happens once, on the author's machine, at packaging time
(see ``neko_plugin_cli.core.metadata_probe``), and the result ships inside the
package as ``plugin.meta.json``. The host reads that file and can reuse a scan
the start path already needed to refresh stale schemas or write a local cache
for a different build environment.
Nothing in this module imports, executes, or subprocesses plugin code.

Entries whose schema is not available statically get
:data:`PLACEHOLDER_INPUT_SCHEMA`, and that degradation is narrower than it
sounds: argument validation runs inside the plugin process against the real
model, the agent is only ever offered plugins that are running, and the one UI
that renders a parameter form is gated on the plugin running — by which point
it has been imported on demand and its schema is real.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from plugin._types.version import SDK_VERSION
from plugin._types.packaged_metadata import (
    PACKAGED_METADATA_FILENAME,
)
from plugin.core.packaged_metadata import (
    MAX_PACKAGED_METADATA_BYTES,
    PACKAGED_METADATA_SCHEMA_VERSION,
    PLACEHOLDER_INPUT_SCHEMA,
    PackagedMetadataError,
    PackagedPluginMetadata,
    SOURCE_IGNORED_DIRS,
    SOURCE_UNFINGERPRINTABLE_DIRS,
    SourceStatSummary,
    SourceTreeSnapshot,
    TEXT_SUFFIXES_FOR_HASHING,
    _CR,
    _CRLF,
    _GENERATED_METADATA_NAMES,
    _LF,
    _iter_source_files,
    build_environment,
    compute_source_sha256,
    empty_source_directories,
    entries_config_digest,
    source_directory_names,
    source_file_names,
    source_stat_summary,
    unicode_renamed_source_files,
)
from plugin.logging_config import get_logger
from utils.file_utils import atomic_write_bytes

logger = get_logger("server.infrastructure.packaged_metadata")
PACKAGED_METADATA_CACHE_DIRECTORY = ".neko-plugin-metadata"
_LOCAL_METADATA_CACHE_MAX_FILES = 2
_local_metadata_cache_cleanup_lock = threading.Lock()


def _prune_local_metadata_cache(target: Path) -> None:
    """Bound one installation's generated caches without touching other plugins."""
    with _local_metadata_cache_cleanup_lock:
        try:
            candidates: list[tuple[int, Path, os.stat_result]] = []
            with os.scandir(target.parent) as entries:
                for entry in entries:
                    name = entry.name
                    if (
                        len(name) != 69
                        or not name.endswith(".json")
                        or any(char not in "0123456789abcdef" for char in name[:-5])
                        or name == target.name
                        or not entry.is_file(follow_symlinks=False)
                    ):
                        continue
                    # DirEntry.stat() can omit the inode on Windows; use the
                    # same stat API as the replacement check below.
                    info = os.stat(entry.path, follow_symlinks=False)
                    candidates.append((info.st_mtime_ns, Path(entry.path), info))
            candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
            for _mtime, path, scanned in candidates[
                _LOCAL_METADATA_CACHE_MAX_FILES - 1 :
            ]:
                try:
                    current = path.stat(follow_symlinks=False)
                    # Another writer may have refreshed this cache since the scan.
                    if (
                        current.st_ino == scanned.st_ino
                        and current.st_mtime_ns == scanned.st_mtime_ns
                        and current.st_size == scanned.st_size
                        and stat.S_ISREG(current.st_mode)
                    ):
                        path.unlink()
                except OSError as exc:
                    logger.debug(
                        "could not prune host metadata cache {}: {}", path, exc
                    )
        except OSError as exc:
            # Cache retention is optional; a successful write remains usable.
            logger.debug(
                "could not scan host metadata cache {}: {}", target.parent, exc
            )


def _stamp_metadata_verified(meta_path: Path, newest_source_ns: int) -> None:
    """Record that ``meta_path`` was just proven to match its sources.

    The mtime comparison is only a fast path, and archive extraction leaves it
    permanently false: whichever file lands last is newer than the metadata, so
    every refresh re-hashes the whole tree — under the registry lock, for every
    installed plugin (codex). Moving the metadata's timestamp past the sources
    turns that into a one-off cost the first time each package is read.

    Only ever called right after the content hash matched, so the timestamp
    asserts something that was true a moment ago rather than assuming it. A
    later edit still makes a source newer and sends the next read down the slow
    path.

    Best-effort by design: a read-only install just keeps paying the hash.
    """
    try:
        stamp_ns = max(newest_source_ns, time.time_ns())
        os.utime(meta_path, ns=(meta_path.stat().st_atime_ns, stamp_ns))
    except OSError as exc:
        logger.debug(
            "could not refresh the packaged metadata timestamp, its sources will "
            "be re-hashed on every refresh: path={}, err={}",
            meta_path,
            str(exc),
        )


def _environment_matches(raw: object) -> bool:
    if not isinstance(raw, Mapping):
        return False
    current = build_environment()
    return all(str(raw.get(key) or "") == value for key, value in current.items())


def _major_of(version: str) -> str:
    head = str(version or "").strip().split("+", 1)[0].split("-", 1)[0]
    return head.split(".", 1)[0] if head else ""


def _coerce_entries(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _coerce_handlers(raw: object) -> dict[str, dict[str, object]]:
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key): dict(value)
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, Mapping)
    }


def _coerce_entry_methods(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key): str(value)
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _tables_are_well_formed(raw: Mapping[str, object]) -> bool:
    """Whether the metadata tables have their required shapes.

    An empty ``handlers`` mapping is a real answer — a background-only plugin
    registers nothing — and the start path now trusts it instead of rescanning.
    That makes the difference between "empty" and "malformed" load-bearing:
    coercing a missing or non-object table into an empty one would let a broken
    package install *no* handlers while its ``entries`` advertise tools, leaving
    the plugin running with nothing dispatchable (codex). The current schema writes all
    three tables, so anything else is a package to fall back on, not to believe.
    """
    handlers = raw.get("handlers")
    entry_methods = raw.get("entry_methods")
    entries = raw.get("entries")
    if not isinstance(raw.get("entries_config_sha256"), str):
        return False
    if not isinstance(handlers, Mapping):
        return False
    if not isinstance(entry_methods, Mapping):
        return False
    if not isinstance(entries, list):
        return False
    if any(
        not isinstance(key, str) or not isinstance(value, Mapping)
        for key, value in handlers.items()
    ):
        return False
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in entry_methods.items()
    ):
        return False
    return all(isinstance(item, Mapping) for item in entries)


def _read_metadata_json(
    meta_path: Path, *, warn: bool = False
) -> tuple[Mapping[str, object], os.stat_result, bytes] | None:
    """Read one regular metadata file with a bounded allocation and JSON depth.

    Recheck the opened descriptor and use nonblocking open where available so
    replacing a regular file with a FIFO between stat and open cannot hang.
    Reading one extra byte also detects a file that grew after the size check.
    """
    try:
        meta_stat = meta_path.stat()
        if (
            not stat.S_ISREG(meta_stat.st_mode)
            or meta_stat.st_size > MAX_PACKAGED_METADATA_BYTES
        ):
            if warn:
                logger.warning(
                    "packaged metadata is not a size-capped regular file: path={}",
                    meta_path,
                )
            return None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(meta_path, flags), "rb") as handle:
            meta_stat = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(meta_stat.st_mode)
                or meta_stat.st_size > MAX_PACKAGED_METADATA_BYTES
            ):
                return None
            encoded = handle.read(MAX_PACKAGED_METADATA_BYTES + 1)
        if len(encoded) > MAX_PACKAGED_METADATA_BYTES:
            return None
        raw: Any = json.loads(encoded.decode("utf-8"))
    except FileNotFoundError:
        # The local cache is optional. Its absence is the normal first read.
        return None
    except (OSError, ValueError, RecursionError) as exc:
        if warn:
            logger.warning(
                "packaged plugin metadata unreadable, falling back to manifest: "
                "path={}, err_type={}, err={}",
                meta_path,
                type(exc).__name__,
                str(exc),
            )
        return None
    if not isinstance(raw, Mapping):
        if warn:
            logger.warning(
                "packaged plugin metadata is not an object: path={}", meta_path
            )
        return None
    return raw, meta_stat, encoded


def stale_packaged_schema_version(plugin_dir: Path) -> int | None:
    """The schema version of a real but outdated ``plugin.meta.json``, else ``None``.

    Only a regular, size-capped, well-formed JSON object whose integer
    ``schema_version`` is *older* than the current one counts. A missing or
    broken file has nothing to upgrade; a current one was refused for some
    other reason the upgrade cannot fix; and a newer one was written by a
    newer host, so rewriting it here would be a downgrade that throws away
    fields this host does not know about (greptile).
    """
    loaded = _read_metadata_json(plugin_dir / PACKAGED_METADATA_FILENAME)
    if loaded is None:
        return None
    raw, _meta_stat, _encoded = loaded
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        return None
    return version if version < PACKAGED_METADATA_SCHEMA_VERSION else None


def snapshot_source_tree(plugin_dir: Path) -> SourceTreeSnapshot | None:
    try:
        return SourceTreeSnapshot(
            sha256=compute_source_sha256(plugin_dir),
            directories=tuple(source_directory_names(plugin_dir)),
        )
    except (OSError, PackagedMetadataError):
        return None


def packaged_metadata_env_mismatched(plugin_dir: Path) -> bool:
    """Check a current-schema package's build environment without hashing sources."""
    loaded = _read_metadata_json(plugin_dir / PACKAGED_METADATA_FILENAME)
    if loaded is None:
        return False
    raw, _meta_stat, _encoded = loaded
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        return False
    if version != PACKAGED_METADATA_SCHEMA_VERSION:
        # 更旧的由 stale_packaged_schema_version 负责；更新的我们无权改写（那是降级）。
        return False
    return not _environment_matches(raw.get("build_env"))


def packaged_metadata_needs_rebuild(plugin_dir: Path) -> bool:
    """Whether a fresh scan could repair this package's schema or environment."""
    loaded = _read_metadata_json(plugin_dir / PACKAGED_METADATA_FILENAME)
    if loaded is None:
        return False
    raw, _meta_stat, _encoded = loaded
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        return False
    return version < PACKAGED_METADATA_SCHEMA_VERSION or (
        version == PACKAGED_METADATA_SCHEMA_VERSION
        and not _environment_matches(raw.get("build_env"))
    )


def local_packaged_metadata_path(plugin_dir: Path) -> Path | None:
    """Locate a host cache bound to this installation, package and environment.

    Binding the package contents also invalidates caches after replacement at
    the same path. No file in the installed directory is claimed as a cache.
    """
    loaded = _read_metadata_json(plugin_dir / PACKAGED_METADATA_FILENAME)
    if loaded is None:
        return None
    raw, _stat, _encoded = loaded
    return _local_packaged_metadata_path(plugin_dir, raw)


def _local_packaged_metadata_path(
    plugin_dir: Path, raw: Mapping[str, object]
) -> Path | None:
    """Derive a cache path from an already-read package metadata snapshot."""
    try:
        from plugin.sdk.shared.core.base_runtime import resolve_runtime_data_root

        installed = os.path.normcase(str(plugin_dir.resolve()))
        installation_key = hashlib.sha256(
            installed.encode("utf-8", errors="surrogatepass")
        ).hexdigest()
        environment_key = hashlib.sha256(
            json.dumps(build_environment(), sort_keys=True).encode("utf-8")
        ).hexdigest()
        # Rebuilt packages update source_sha256 even when extraction preserves
        # names, sizes and timestamps. Bind that digest and the entry metadata.
        package_key = hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        cache_key = hashlib.sha256(
            f"{installation_key}:{environment_key}:{package_key}".encode("ascii")
        ).hexdigest()
        cache_path = (
            resolve_runtime_data_root()
            / PACKAGED_METADATA_CACHE_DIRECTORY
            / installation_key
            / f"{cache_key}.json"
        )
        spelling = str(cache_path)
        if (
            os.name == "nt"
            and len(spelling) >= 240
            and not spelling.startswith("\\\\?\\")
        ):
            spelling = (
                "\\\\?\\UNC\\" + spelling[2:]
                if spelling.startswith("\\\\")
                else "\\\\?\\" + spelling
            )
            cache_path = Path(spelling)
        return cache_path
    except (OSError, ValueError, RecursionError):
        return None


_REBUILD_FAILURE_LIMIT = 128
_REBUILD_FAILURE_RETRY_SECONDS = 30.0
_rebuild_failures: dict[Path, tuple[tuple[object, ...], float]] = {}
_rebuild_failures_lock = threading.Lock()


def _rebuild_identity(target: Path, summary: SourceStatSummary) -> tuple[object, ...]:
    def stamp(path: Path, *, directory: bool = False) -> tuple[int, ...] | None:
        try:
            result = path.stat()
        except OSError:
            return None
        identity = (
            result.st_ino,
            result.st_mode,
            getattr(result, "st_file_attributes", 0),
        )
        # Other plugins write into the same cache directory. Their writes must
        # not clear a failed target's short backoff.
        return (
            identity
            if directory
            else (*identity, result.st_mtime_ns, result.st_ctime_ns)
        )

    return (
        stamp(target),
        stamp(target.parent, directory=True),
        summary.newest_mtime_ns,
        summary.total_bytes,
        hash(tuple(summary.names)),
    )


def _recent_rebuild_failure(target: Path, summary: SourceStatSummary) -> bool:
    identity = _rebuild_identity(target, summary)
    with _rebuild_failures_lock:
        previous = _rebuild_failures.get(target)
        if previous is None:
            return False
        if (
            previous[0] == identity
            and time.monotonic() - previous[1] < _REBUILD_FAILURE_RETRY_SECONDS
        ):
            return True
        _rebuild_failures.pop(target, None)
    return False


def _record_rebuild_failure(target: Path, summary: SourceStatSummary) -> None:
    identity = _rebuild_identity(target, summary)
    with _rebuild_failures_lock:
        if (
            target not in _rebuild_failures
            and len(_rebuild_failures) >= _REBUILD_FAILURE_LIMIT
        ):
            _rebuild_failures.pop(next(iter(_rebuild_failures)))
        _rebuild_failures[target] = (identity, time.monotonic())


def _probe_metadata_target(target: Path) -> bool:
    """Exercise real creation, writing and replacement before hashing sources."""
    probe: Path | None = None
    replacement: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=".metadata_probe_", dir=target.parent
        )
        probe = Path(name)
        replacement = probe.with_suffix(".ready")
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(b"\0")
        os.replace(probe, replacement)
        return True
    except OSError:
        return False
    finally:
        for path in (probe, replacement):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass


def snapshot_packaged_metadata_rebuild_tree(
    plugin_dir: Path,
) -> SourceTreeSnapshot | None:
    """Snapshot only trees that have a usable destination and can be cached."""
    target = (
        plugin_dir / PACKAGED_METADATA_FILENAME
        if stale_packaged_schema_version(plugin_dir) is not None
        else local_packaged_metadata_path(plugin_dir)
    )
    if target is None:
        return None
    summary = source_stat_summary(plugin_dir)
    if (
        summary.untrustworthy
        or empty_source_directories(plugin_dir)
        or unicode_renamed_source_files(plugin_dir)
        or _recent_rebuild_failure(target, summary)
        or not _probe_metadata_target(target)
    ):
        return None
    return snapshot_source_tree(plugin_dir)


def refresh_stale_packaged_metadata(
    plugin_dir: Path,
    *,
    before_scan: SourceTreeSnapshot | None,
    entries: list[dict[str, object]],
    handlers: dict[str, dict[str, object]],
    entry_methods: dict[str, str],
    conf: object,
    pdata: object,
) -> bool:
    """Rewrite an outdated ``plugin.meta.json`` from a scan of this very tree.

    A schema bump refuses every package built before it, and the plugin then
    pays one isolated import per start until its author repackages — which,
    for a plugin the author no longer touches, is forever. The start path has
    just imported the tree anyway; what it learned is exactly what the
    packager would have written, so write it, once, and the next start takes
    the fast path again.

    A package built in a **different environment** is deliberately *not* handled
    here. Its ``plugin.meta.json`` is a distributed artifact and stays
    byte-identical — rewriting it would change the bytes of an installed package,
    which feeds the manual-takeover tree hash and the "what is on disk is what
    the market published" property. That case gets a host runtime cache
    instead: :func:`write_local_packaged_metadata`.

    The same refusals the packager applies (``metadata_probe``) apply here: a
    tree with symlinks, empty directories or names that change under NFC
    cannot be described by a fingerprint, and a tree the import itself changed
    (``before_scan`` no longer matches) is one whose handlers and fingerprint
    describe different states. Both are left alone. The caller guarantees that
    the effective ``entries`` table equals the manifest's, since the file must
    describe the package, not one machine's overrides.

    Returns whether a file was written. Failing to fingerprint or write is not
    an error: a source file can vanish between enumeration and hashing, the
    directory may be read-only, and the plugin started fine without the file.
    A file the reader would refuse for its size is not written either: it would
    be current-schema and oversized, so nothing could ever repair it (codex).
    """
    stale = stale_packaged_schema_version(plugin_dir)
    if stale is None or before_scan is None:
        return False
    if not _write_scanned_packaged_metadata(
        plugin_dir / PACKAGED_METADATA_FILENAME,
        plugin_dir,
        before_scan=before_scan,
        entries=entries,
        handlers=handlers,
        entry_methods=entry_methods,
        conf=conf,
        pdata=pdata,
        subject="stale packaged metadata",
    ):
        return False
    logger.info(
        "packaged metadata upgraded in place from schema {} to {}: path={}",
        stale,
        PACKAGED_METADATA_SCHEMA_VERSION,
        plugin_dir,
    )
    return True


def _write_scanned_packaged_metadata(
    target: Path,
    plugin_dir: Path,
    *,
    before_scan: SourceTreeSnapshot,
    entries: list[dict[str, object]],
    handlers: dict[str, dict[str, object]],
    entry_methods: dict[str, str],
    conf: object,
    pdata: object,
    subject: str,
) -> bool:
    """把一次扫描的结果按打包器的格式写到 ``target``。返回是否真的写了。

    两条写路径共用这一段——改写包内那份 schema 过期的（:func:`refresh_stale_packaged_metadata`），
    以及写宿主运行时缓存（:func:`write_local_packaged_metadata`）。**拒绝理由必须共用**：
    读取方对两份文件跑的是同一套校验，一边写得出去另一边读不进来，就等于白写。

    与打包器 ``metadata_probe`` 同样的拒绝：带软链、空目录、或名字在 NFC 下会变的树
    没法用指纹描述；import 自己改动过的树（``before_scan`` 对不上）则是 handler 与
    指纹描述了两个不同状态。两种都不写。调用方负责保证生效的 ``entries`` 表就是
    manifest 自己那份——文件描述的是包，不是某台机器的覆盖。

    写失败不是错误：源文件可能在枚举和哈希之间消失，目录可能只读，而插件没有这份
    文件也照样起来了。超过读取方尺寸上限的也不写：那样它会"schema 当前且超大"，
    之后没有任何路径能再修它（codex）。
    """
    summary: SourceStatSummary | None = None
    try:
        summary = source_stat_summary(plugin_dir)
        if _recent_rebuild_failure(target, summary) or not _probe_metadata_target(
            target
        ):
            return False
        if (
            summary.untrustworthy
            or empty_source_directories(plugin_dir)
            or unicode_renamed_source_files(plugin_dir)
        ):
            logger.info(
                "{} left as is; the tree cannot be fingerprinted: path={}",
                subject,
                plugin_dir,
            )
            return False
        after_scan = snapshot_source_tree(plugin_dir)
        if after_scan != before_scan:
            logger.info(
                "{} left as is; importing the plugin changed its tree, so the "
                "scan and the fingerprint describe different states: path={}",
                subject,
                plugin_dir,
            )
            return False
        payload = {
            "schema_version": PACKAGED_METADATA_SCHEMA_VERSION,
            "sdk_version": SDK_VERSION,
            "source_sha256": before_scan.sha256,
            "source_files": summary.names,
            "source_bytes": summary.total_bytes,
            "build_env": build_environment(),
            "entries_config_sha256": entries_config_digest(conf, pdata),
            "entries": list(entries),
            "handlers": dict(handlers),
            "entry_methods": dict(entry_methods),
        }
        # 量的就是写的：按字节落盘，文本模式在 Windows 上会把换行展开成 CRLF，
        # 刚好卡在上限下的文件落到磁盘上就超了（codex）。打包器同样用 newline=""。
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        if len(encoded) > MAX_PACKAGED_METADATA_BYTES:
            logger.info(
                "{} left as is; the file would exceed the reader's size cap: "
                "path={}, bytes={}, cap={}",
                subject,
                plugin_dir,
                len(encoded),
                MAX_PACKAGED_METADATA_BYTES,
            )
            return False
        atomic_write_bytes(target, encoded)
    except (OSError, PackagedMetadataError) as exc:
        if summary is not None:
            _record_rebuild_failure(target, summary)
        # compute_source_sha256 wraps its OSError in PackagedMetadataError (a
        # ValueError); an optional optimisation must not turn that into a
        # failed start (greptile).
        logger.info(
            "{} could not be written; the plugin will rescan on every start until "
            "this succeeds: path={}, target={}, err_type={}, err={}",
            subject,
            plugin_dir,
            target.name,
            type(exc).__name__,
            str(exc),
        )
        return False
    with _rebuild_failures_lock:
        _rebuild_failures.pop(target, None)
    return True


def write_local_packaged_metadata(
    plugin_dir: Path,
    *,
    before_scan: SourceTreeSnapshot | None,
    entries: list[dict[str, object]],
    handlers: dict[str, dict[str, object]],
    entry_methods: dict[str, str],
    conf: object,
    pdata: object,
) -> bool:
    """Cache a foreign-environment scan in writable host runtime storage.

    Installed files are never modified. The caller verifies the manifest id
    and effective entry declarations; the shared writer verifies the source
    tree stayed unchanged during the scan. Each installation has a separate
    cache directory. After a successful write, keep its current file and one
    recent file; caches for other installations are never evicted. Cleanup
    failures leave the successful cache usable.
    """
    if before_scan is None:
        return False
    if not packaged_metadata_env_mismatched(plugin_dir):
        # 不是"异环境"这一种情况就不归这里管：schema 过期走
        # refresh_stale_packaged_metadata，本机包压根不需要写，缺文件则见上。
        return False
    target = local_packaged_metadata_path(plugin_dir)
    if target is None:
        return False
    if not _write_scanned_packaged_metadata(
        target,
        plugin_dir,
        before_scan=before_scan,
        entries=entries,
        handlers=handlers,
        entry_methods=entry_methods,
        conf=conf,
        pdata=pdata,
        subject="host packaged metadata cache",
    ):
        return False
    _prune_local_metadata_cache(target)
    logger.info(
        "host packaged metadata cache written for this environment "
        "(build_env={}); the packaged file is left untouched: path={}",
        build_environment(),
        plugin_dir,
    )
    return True


def _metadata_snapshot_is_current(
    meta_path: Path, expected: os.stat_result, encoded: bytes | None = None
) -> bool:
    """Reject metadata replaced, removed or modified since the bounded read."""
    try:
        current = meta_path.stat()
    except OSError:
        return False
    if (current.st_dev, current.st_ino, current.st_size) != (
        expected.st_dev, expected.st_ino, expected.st_size
    ):
        return False
    if (current.st_mtime_ns, current.st_ctime_ns) == (
        expected.st_mtime_ns, expected.st_ctime_ns
    ):
        return True
    # A concurrent reader can stamp the same, unchanged metadata file.
    # Only this rare path rereads bytes; normal reads still parse JSON once.
    if encoded is None:
        return False
    reread = _read_metadata_json(meta_path)
    if reread is None:
        return False
    _raw, reread_stat, reread_bytes = reread
    return (
        (reread_stat.st_dev, reread_stat.st_ino, reread_stat.st_size)
        == (expected.st_dev, expected.st_ino, expected.st_size)
        and reread_bytes == encoded
        and _metadata_snapshot_is_current(meta_path, reread_stat)
    )


def read_packaged_metadata(plugin_dir: Path) -> PackagedPluginMetadata | None:
    """Prefer a validated host cache, otherwise read the shipped metadata.

    Both use the same schema, SDK, environment and source freshness checks.
    Files named plugin.meta.local.json inside installed code are plugin data.
    """
    meta_path = plugin_dir / PACKAGED_METADATA_FILENAME
    loaded = _read_metadata_json(meta_path, warn=True)
    if loaded is None:
        return None
    raw, meta_stat, encoded = loaded
    if _environment_matches(raw.get("build_env")):
        return _validate_metadata_snapshot(meta_path, plugin_dir, raw, meta_stat, encoded)
    target = _local_packaged_metadata_path(plugin_dir, raw)
    local = (
        _read_packaged_metadata_from(target, plugin_dir) if target is not None else None
    )
    if local is not None and local.built_in_this_environment:
        return local if _metadata_snapshot_is_current(meta_path, meta_stat, encoded) else None
    if target is not None:
        # Reuse caches written by the former flat layout. New writes and
        # retention stay scoped to this installation's directory.
        legacy = target.parent.parent / target.name
        local = _read_packaged_metadata_from(legacy, plugin_dir)
        if local is not None and local.built_in_this_environment:
            return (
                local
                if _metadata_snapshot_is_current(meta_path, meta_stat, encoded)
                else None
            )
    return _validate_metadata_snapshot(meta_path, plugin_dir, raw, meta_stat, encoded)


def _read_packaged_metadata_from(
    meta_path: Path,
    plugin_dir: Path,
) -> PackagedPluginMetadata | None:
    """Read a bounded metadata file and validate its snapshot against the package."""
    loaded = _read_metadata_json(meta_path, warn=True)
    if loaded is None:
        return None
    raw, meta_stat, encoded = loaded
    return _validate_metadata_snapshot(meta_path, plugin_dir, raw, meta_stat, encoded)


def _validate_metadata_snapshot(
    meta_path: Path,
    plugin_dir: Path,
    raw: Mapping[str, object],
    meta_stat: os.stat_result,
    encoded: bytes,
) -> PackagedPluginMetadata | None:
    """Check snapshot stability before stamping a successful source verification."""
    validated = _validate_packaged_metadata(meta_path, plugin_dir, raw, meta_stat)
    if validated is None or not _metadata_snapshot_is_current(meta_path, meta_stat, encoded):
        return None
    result, verified_source_mtime = validated
    # Updating mtime/ctime during validation would invalidate our own snapshot,
    # and could hide an in-place rewrite that happened before the update.
    if verified_source_mtime is not None:
        _stamp_metadata_verified(meta_path, verified_source_mtime)
    return result


def _validate_packaged_metadata(
    meta_path: Path,
    plugin_dir: Path,
    raw: Mapping[str, object],
    meta_stat: os.stat_result,
) -> tuple[PackagedPluginMetadata, int | None] | None:
    """Validate metadata and return the source timestamp if its hash was checked."""

    schema_version = raw.get("schema_version")
    if schema_version != PACKAGED_METADATA_SCHEMA_VERSION:
        logger.warning(
            "packaged plugin metadata schema mismatch, falling back to manifest: "
            "path={}, found={}, expected={}",
            meta_path,
            schema_version,
            PACKAGED_METADATA_SCHEMA_VERSION,
        )
        return None

    packaged_sdk = str(raw.get("sdk_version") or "")
    # 只比大版本。schema 推导的行为跟着 SDK 的大版本走，逐个补丁号比对会让每次
    # SDK 发版把全生态的元数据一起作废。
    if _major_of(packaged_sdk) != _major_of(SDK_VERSION):
        logger.warning(
            "packaged plugin metadata SDK major mismatch, falling back to manifest: "
            "path={}, packaged={}, host={}",
            meta_path,
            packaged_sdk,
            SDK_VERSION,
        )
        return None

    if not _tables_are_well_formed(raw):
        logger.warning(
            "packaged plugin metadata has malformed entry tables, falling back "
            "to manifest: path={}",
            meta_path,
        )
        return None

    summary = source_stat_summary(plugin_dir)
    newest_source_ns = summary.newest_mtime_ns
    if summary.untrustworthy:
        logger.info(
            "plugin tree contains symlinks or unreadable entries; packaged metadata "
            "cannot be trusted to match the sources: path={}",
            plugin_dir,
        )
        return None

    packaged_sha = str(raw.get("source_sha256") or "")
    if len(packaged_sha) != 64 or any(
        char not in "0123456789abcdef" for char in packaged_sha
    ):
        # 缺了它或者写坏了，慢路径就没有可比的东西——而快路径（清单+尺寸+时间戳）
        # 全过时，这份元数据会在从未做过任何内容校验的情况下被当成权威（codex）。
        logger.warning(
            "packaged plugin metadata has no valid source digest, falling back "
            "to manifest: path={}",
            meta_path,
        )
        return None

    # 先比文件清单，再比时间戳。清单是确定性的：增删文件一定改变它，而"删掉一个
    # 文件"在时间戳上只体现为父目录 mtime 变新——那要求它严格大于 meta.json 的
    # mtime，同一个时间戳刻度内就不成立（本机过、CI 挂，就是这条）。清单还顺带让
    # 判定不依赖解包顺序（codex）。
    packaged_names = raw.get("source_files")
    if not isinstance(packaged_names, list) or not all(
        isinstance(item, str) for item in packaged_names
    ):
        # 类型也要校验，不能只 str() 强转了事。强转确实会让比对失配、从而拒绝，
        # 但那是"碰巧拒对了"，不是在表达契约——而这份文件来自第三方包
        # （coderabbit）。
        logger.warning(
            "packaged plugin metadata has no valid source file list, falling "
            "back to manifest: path={}",
            meta_path,
        )
        return None
    if sorted(packaged_names) != sorted(summary.names):
        logger.info(
            "plugin source file set differs from the packaged one; rebuild "
            "with 'neko-plugin build' to refresh its metadata: path={}",
            plugin_dir,
        )
        return None

    packaged_bytes = raw.get("source_bytes")
    if not isinstance(packaged_bytes, int) or isinstance(packaged_bytes, bool):
        # schema v3 声明了这个字段，缺了就当整份元数据不合格——和 source_files
        # 同一条判据。悄悄跳过尺寸比对的话，判定就退回到只看 mtime，而 mtime
        # 不可靠正是这个字段存在的理由（coderabbit）。
        logger.warning(
            "packaged plugin metadata has no valid source byte total, falling "
            "back to manifest: path={}",
            meta_path,
        )
        return None
    if packaged_bytes != summary.total_bytes:
        # 尺寸对不上就到此为止，别再往下走整树哈希。这份元数据要么在描述自己时
        # 就不自洽，要么源码真的变了——两种情况都该回落 manifest，而它们都不值得
        # 每次刷新在持锁状态下重读一遍整棵树（包体上限 1 GiB）。拒绝放在慢路径
        # **之前**，否则这道闸拦住的是结论、拦不住开销（coderabbit）。
        logger.info(
            "packaged plugin metadata does not match its tree's byte total, "
            "falling back to manifest: path={}, stated={}, actual={}",
            meta_path,
            packaged_bytes,
            summary.total_bytes,
        )
        return None
    verified_source_mtime = None
    if newest_source_ns > meta_stat.st_mtime_ns:
        # 时间戳只是快路径，不是判据。git 不保留 mtime，所以一份全新 clone 里源码
        # 和生成物的时间戳关系是任意的——只看 mtime 的话，内置插件会在每台新机器上
        # 集体退化成占位。所以时间戳说"可能过时"时再真算一次内容哈希来定夺；这条
        # 昂贵的路（实测约 0.36s/全部插件）只有开发者真的改过代码才会走到。
        try:
            actual_sha = compute_source_sha256(plugin_dir)
        except PackagedMetadataError as exc:
            logger.info(
                "cannot verify packaged metadata against sources: path={}, err={}",
                plugin_dir,
                str(exc),
            )
            return None
        if actual_sha != packaged_sha:
            logger.info(
                "plugin sources changed since packaging; rebuild with "
                "'neko-plugin build' to refresh its metadata: path={}",
                plugin_dir,
            )
            return None
        # The snapshot coordinator stamps this result only after confirming
        # the metadata itself did not change during validation.
        verified_source_mtime = newest_source_ns

    metadata = PackagedPluginMetadata(
        built_in_this_environment=_environment_matches(raw.get("build_env")),
        entries=_coerce_entries(raw.get("entries")),
        entries_config_sha256=str(raw.get("entries_config_sha256") or ""),
        handlers=_coerce_handlers(raw.get("handlers")),
        entry_methods=_coerce_entry_methods(raw.get("entry_methods")),
        sdk_version=packaged_sdk,
        source_sha256=packaged_sha,
    )
    return metadata, verified_source_mtime
