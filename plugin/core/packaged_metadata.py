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
from plugin.utils.source_paths import is_metadata_probe_path, is_vendor_sync_path


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
    # 打包时那份 plugin.toml 声明的 entries 表的摘要，用来判断用户的配置覆盖
    # 有没有动过它。
    entries_config_sha256: str = ""
    # 注册进 state.event_handlers 的那份元数据，以及 entry_id -> 方法名。
    # 启动一个插件本来要为这两样再 import 它一次——插件进程自己已经 import 过，
    # 那一次纯属重复（codex）。带上之后 start_plugin 只剩宿主进程那一次导入。
    handlers: dict[str, dict[str, object]] = field(default_factory=dict)
    entry_methods: dict[str, str] = field(default_factory=dict)
    sdk_version: str = ""
    source_sha256: str = ""
    # 打包机和这台机器是不是同一套 (os, python, arch)。
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
    # 手写 scandir 下降而不是 rglob：忽略目录必须在下降**之前**剪掉，否则一个带
    # 大 object database 的开发目录每次都要先枚举完才轮到忽略判断。
    #
    # 软链不跟进去，但要留痕：跟进去可能撞上 site-packages 那种巨树或者成环，而
    # 只是跳过的话，把软链重指到另一份代码不会引起任何可见变化。留痕的做法是让
    # 调用方直接把整棵树判成"不可信"。
    # saw_symlink 是"这棵树不可信"的旗子，软链只是最常见的那个来源：读不了的目录、
    # 以及 FIFO/socket/设备节点这类非普通文件也会把它立起来。
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
            if entry.name.startswith(".vendor.") and current in (root, vendor):
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
                        # 目录自己的 mtime 也要看。删掉一个文件不会让任何**幸存**
                        # 文件变新，于是纯看文件 mtime 的快路径会放过"源码少了一
                        # 块"这种改动，宿主继续端着按删除前推出来的 schema
                        # （codex）。增删条目都会更新父目录的 mtime。
                        dirs.append(entry.path)
                    continue
                if is_metadata_probe_path(Path(entry.path).relative_to(plugin_dir)):
                    continue
                if entry.name in _GENERATED_METADATA_NAMES and current == str(
                    plugin_dir
                ):
                    # 生成物不参与它自己的新鲜度判定——但只有根部那一份是生成物。
                    # 按文件名一刀切会把插件自己带的 data/plugin.meta.json 这种运行
                    # 时文件也排除掉，而打包管线照样把它放进包里：改它的内容不会让
                    # 任何指纹变化（codex）。
                    continue
                if not entry.is_file(follow_symlinks=False):
                    # ⚠️ 只收普通文件。FIFO、socket、设备节点都能通过 stat()，而摘要
                    # 那一步是 read_bytes()——没有写端的 FIFO 上它会永久阻塞，而刷新
                    # 现在整段握着 _REGISTRY_REFRESH_LOCK，一个命名管道就能把整个插件
                    # 注册表焊死（coderabbit）。和软链同样处理：留痕，让整棵树不可信。
                    saw_symlink = True
                    continue
                stat_result = entry.stat(follow_symlinks=False)
            except OSError:
                saw_symlink = True
                continue
            rel_path = os.path.relpath(entry.path, str(plugin_dir)).replace(os.sep, "/")
            # 记录用 NFC 拼写，读盘用文件系统给的那个。打包器写进包里的档案名已经
            # 是 NFC（normalize_relative_posix），而 macOS 交出来的常常是分解形式：
            # 不归一化的话，同一个文件名在两边算出两份清单和两份摘要，元数据条条
            # 被判过时（codex）。反过来，用归一化后的名字去 open() 在保留原拼写的
            # 文件系统上会直接找不到文件，所以两个拼写都要留着。
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
            # 行尾归一化之后再摘要。这个仓库用 .gitattributes 把文本钉成 LF，但哈希
            # 不该依赖那份配置：作者在 Windows 上打的包一旦带着 CRLF 算出来的摘要，
            # 到 Linux 用户机器上就会条条判成"源码变了"，全部退化成占位。
            #
            # ⚠️ 只折 CRLF，不折裸 CR。把 CR 也当 LF 会让"把每个 LF 换成 CR"这种
            # 改动和原文摘要相同——路径、字节数、内容哈希全对得上，慢路径也拦不住
            # （codex）。而 git 的行尾翻译只在 LF↔CRLF 之间发生，从不产生裸 CR，
            # 所以少折这一层不影响它本来要解决的问题。
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
