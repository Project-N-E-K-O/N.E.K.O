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
package as ``plugin.meta.json``. The host only ever reads that file. Nothing in
this module imports, executes, or subprocesses plugin code.

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
import platform
import stat
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from plugin._types.version import SDK_VERSION
from plugin.logging_config import get_logger

logger = get_logger("server.infrastructure.packaged_metadata")


PACKAGED_METADATA_FILENAME = "plugin.meta.json"
# 2：加入了必需的 source_files。留在 1 而对缺字段的元数据"跳过检查"是错的——
# schema 变了就该换号，否则一份没有 source_files 的元数据仍会被当成合法的第 1 版
# 接受，增删源文件时那道确定性的判据整个静默失效（coderabbit）。旧包因此回落到
# manifest 声明的 entries，重新打包即可恢复。
PACKAGED_METADATA_SCHEMA_VERSION = 3

# 解析之前先封顶。这份文件来自第三方包，而 json.loads 会把整份内容读进内存再建对象；
# 一个几百 MB 的 plugin.meta.json 足以在刷新注册表时把进程撑爆，而刷新现在整段持锁
# （codex）。1 MiB 对元数据是很宽的余量：本机 16 个内置插件里最大的一份 47 KB。
MAX_PACKAGED_METADATA_BYTES = 1024 * 1024

# 用字节码点写，避免这几个常量本身在编辑/移植途中被行尾转换动过。
_CR = bytes([13])
_LF = bytes([10])
_CRLF = _CR + _LF

# 指纹盯插件目录下的**所有**文件，不筛后缀。
#
# 原本只看 .py/.toml/.json，但插件的模块级代码经常从同目录的数据文件派生条目
# （metadata.yaml、csv、模板……）：改了那些文件而指纹不变，宿主就会一直端着按旧
# 数据推出来的 schema，而注册的元数据和运行时行为对不上是最难查的一类不一致
# （codex，也是旧扫描缓存键当年选择全量的同一个理由）。

# 下降之前就剪掉。node_modules 不在旧的扫描键忽略集里，带 vendor 树的插件会让
# 每一次遍历都陪着走一遍。
# 只有这些后缀会在摘要前做行尾归一化。二进制资源里 CR 是有意义的字节，把它换掉会
# 让两份不同的文件算出同一个摘要（codex）；而归一化本身是为了让 Windows 打的包到
# Linux 上还认得出来，那个问题只存在于文本。
TEXT_SUFFIXES_FOR_HASHING = frozenset(
    {".py", ".pyi", ".toml", ".json", ".yaml", ".yml", ".ini", ".cfg", ".txt", ".md",
     ".csv", ".xml", ".html", ".css", ".js", ".ts", ".sql"}
)

SOURCE_IGNORED_DIRS = frozenset(
    {"__pycache__", ".git", ".mypy_cache", ".ruff_cache", "node_modules", ".venv"}
)

# 未知参数结构时给的占位。
#
# ⚠️ 不能带 "properties" 键，哪怕是空对象。前端 EntryList 判"有没有 schema"用的是
# `!!(schema?.properties && typeof schema.properties === 'object')`，而 JS 里
# `!!{}` 为真——带一个空 properties 会让它渲染出零字段的表单，提交时参数恒为 {}，
# 用户连退回去手填 JSON 的入口都没有，比什么都不给更糟。
#
# additionalProperties 为真是同一个意思的另一面：这份 schema 只用来描述，任何时候
# 都不能拿它去拒绝调用。真正的参数校验在插件进程里用真模型做。
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
    # 注册进 state.event_handlers 的那份元数据，以及 entry_id -> 方法名。
    # 启动一个插件本来要为这两样再 import 它一次——插件进程自己已经 import 过，
    # 那一次纯属重复（codex）。带上之后 start_plugin 只剩宿主进程那一次导入。
    handlers: dict[str, dict[str, object]] = field(default_factory=dict)
    entry_methods: dict[str, str] = field(default_factory=dict)
    sdk_version: str = ""
    source_sha256: str = ""
    # 打包机和这台机器是不是同一套 (os, python, arch)。
    built_in_this_environment: bool = False


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


def build_environment() -> dict[str, str]:
    """The parts of the environment that can change what a plugin registers.

    A plugin is free to register different entries under different operating
    systems or Python versions — an optional import that only resolves on
    Windows, an entry gated on ``sys.version_info``. Packaged metadata is one
    machine's answer, so anything that treats it as *the* set of callable
    entries has to know whether it was produced here (codex).
    """
    return {
        "os": sys.platform,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "arch": platform.machine(),
    }


def _environment_matches(raw: object) -> bool:
    if not isinstance(raw, Mapping):
        return False
    current = build_environment()
    return all(str(raw.get(key) or "") == value for key, value in current.items())


def _iter_source_files(
    plugin_dir: Path,
) -> tuple[list[tuple[str, os.stat_result]], bool, list[str]]:
    # 手写 scandir 下降而不是 rglob：忽略目录必须在下降**之前**剪掉，否则一个带
    # 大 object database 的开发目录每次都要先枚举完才轮到忽略判断。
    #
    # 软链不跟进去，但要留痕：跟进去可能撞上 site-packages 那种巨树或者成环，而
    # 只是跳过的话，把软链重指到另一份代码不会引起任何可见变化。留痕的做法是让
    # 调用方直接把整棵树判成"不可信"。
    # saw_symlink 是"这棵树不可信"的旗子，软链只是最常见的那个来源：读不了的目录、
    # 以及 FIFO/socket/设备节点这类非普通文件也会把它立起来。
    files: list[tuple[str, os.stat_result]] = []
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
            try:
                if entry.is_symlink():
                    saw_symlink = True
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in SOURCE_IGNORED_DIRS:
                        stack.append(entry.path)
                        # 目录自己的 mtime 也要看。删掉一个文件不会让任何**幸存**
                        # 文件变新，于是纯看文件 mtime 的快路径会放过"源码少了一
                        # 块"这种改动，宿主继续端着按删除前推出来的 schema
                        # （codex）。增删条目都会更新父目录的 mtime。
                        dirs.append(entry.path)
                    continue
                if entry.name == PACKAGED_METADATA_FILENAME:
                    # 生成物不参与它自己的新鲜度判定。
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
            rel_path = os.path.relpath(entry.path, str(plugin_dir)).replace(
                os.sep, "/"
            )
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
            raw = (plugin_dir / real_rel).read_bytes()
            if Path(real_rel).suffix.lower() in TEXT_SUFFIXES_FOR_HASHING:
                raw = raw.replace(_CRLF, _LF).replace(_CR, _LF)
            digest.update(raw)
        except OSError as exc:
            raise PackagedMetadataError(
                f"cannot read plugin source file for hashing: {real_rel}: {exc}"
            ) from exc
        digest.update(b"\0")
    return digest.hexdigest()


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


def read_packaged_metadata(plugin_dir: Path) -> PackagedPluginMetadata | None:
    """Load and validate ``plugin.meta.json``, or ``None`` if unusable.

    ``None`` means "fall back to whatever the manifest declares statically, and
    placeholder the rest". Every rejection path logs why, because a silently
    ignored metadata file looks exactly like a plugin that declares no entries.
    """
    meta_path = plugin_dir / PACKAGED_METADATA_FILENAME
    try:
        meta_stat = meta_path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(meta_stat.st_mode):
        # ⚠️ 元数据文件自己也可能不是普通文件。前面那道"只收普通文件"的闸设在遍历
        # 里，而 plugin.meta.json 恰恰被排除在遍历之外（生成物不参与自己的新鲜度
        # 判定），所以它一直没被检查过。stat() 在 FIFO 上照样成功，而下面的
        # read_text() 会在没有写端时永久阻塞——刷新整段持锁（coderabbit）。
        logger.warning(
            "packaged plugin metadata is not a regular file, ignoring it: path={}",
            meta_path,
        )
        return None

    if meta_stat.st_size > MAX_PACKAGED_METADATA_BYTES:
        logger.warning(
            "packaged plugin metadata is too large to parse, falling back to "
            "manifest: path={}, size={}, limit={}",
            meta_path,
            meta_stat.st_size,
            MAX_PACKAGED_METADATA_BYTES,
        )
        return None

    try:
        raw: Any = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(
            "packaged plugin metadata unreadable, falling back to manifest: path={}, err_type={}, err={}",
            meta_path,
            type(exc).__name__,
            str(exc),
        )
        return None

    if not isinstance(raw, Mapping):
        logger.warning("packaged plugin metadata is not an object: path={}", meta_path)
        return None

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
    size_changed = (
        isinstance(packaged_bytes, int)
        and not isinstance(packaged_bytes, bool)
        and packaged_bytes != summary.total_bytes
    )
    if newest_source_ns > meta_stat.st_mtime_ns or size_changed:
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
        # 哈希刚刚证明这棵树就是打包时那棵，把这个结论盖在 meta.json 的时间戳上。
        # 不盖的话，解包顺序留下的"源码比生成物新"会一直成立，于是**每一次**刷新
        # 都要在持锁状态下重算整棵树的哈希（codex）。盖完之后源码再变照样会变新，
        # 慢路径该走还是走。
        _stamp_metadata_verified(meta_path, newest_source_ns)

    return PackagedPluginMetadata(
        built_in_this_environment=_environment_matches(raw.get("build_env")),
        entries=_coerce_entries(raw.get("entries")),
        handlers=_coerce_handlers(raw.get("handlers")),
        entry_methods=_coerce_entry_methods(raw.get("entry_methods")),
        sdk_version=packaged_sdk,
        source_sha256=packaged_sha,
    )
