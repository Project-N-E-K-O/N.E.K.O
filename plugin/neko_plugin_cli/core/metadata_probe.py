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
import sys
from pathlib import Path
from typing import Any

from plugin._types.version import SDK_VERSION
from plugin.server.infrastructure.packaged_metadata import (
    build_environment,
    PACKAGED_METADATA_FILENAME,
    PACKAGED_METADATA_SCHEMA_VERSION,
    compute_source_sha256,
    source_stat_summary,
)


class MetadataProbeError(RuntimeError):
    """The plugin's metadata could not be derived at build time."""


def _load_logger() -> Any:
    from plugin.logging_config import get_logger

    return get_logger("neko_plugin_cli.metadata_probe")


def derive_plugin_metadata(
    plugin_dir: Path, *, hash_dir: Path | None = None
) -> dict[str, object]:
    """Import ``plugin_dir``'s entry class and return its packaged metadata.

    Raises :class:`MetadataProbeError` with the underlying reason when the
    plugin cannot be imported. Callers decide what that means;
    :func:`write_packaged_metadata` turns it into a warning so packaging keeps
    working, while a caller that needs the metadata can treat it as fatal.
    """
    # 延迟导入：CLI 的其它命令不该为这条路付框架导入的钱。
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
        ctx = _parse_single_plugin_config(config_path, set(), logger)
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

    try:
        isolated = scan_plugin_metadata_isolated(
            plugin_id=ctx.pid,
            module_path=module_path,
            class_name=class_name,
            config_path=config_path,
            conf=ctx.conf,
            pdata=ctx.pdata,
            python_requirement_paths=ctx.python_requirement_paths,
        )
    except PluginMetadataScanError as exc:
        raise MetadataProbeError(
            f"importing the plugin failed ({exc.error_type}): {exc}"
        ) from exc

    stat_summary = source_stat_summary(hash_dir or plugin_dir)
    return {
        "schema_version": PACKAGED_METADATA_SCHEMA_VERSION,
        "sdk_version": SDK_VERSION,
        # 摘要算在真正打进包里的那棵树上，不是作者的源目录。构建规则
        # （tool.neko.build 的 exclude/exclude_dirs/exclude_files）可以把 .py /
        # .toml / .json 排除在包外，而用户机器上哈希的是装出来的那份——两边算的
        # 树不一样，一旦走到内容校验就会条条判成"源码变了"，把好好的 schema 换成
        # 占位（greptile）。
        "source_sha256": compute_source_sha256(hash_dir or plugin_dir),
        # 文件清单让"少了一个文件"这件事不依赖时间戳，也不依赖解包顺序。
        "source_files": stat_summary.names,
        "source_bytes": stat_summary.total_bytes,
        "build_env": build_environment(),
        "entries": list(isolated.entries_preview),
        "handlers": dict(isolated.handlers),
        "entry_methods": dict(isolated.entry_methods),
    }


def write_packaged_metadata(
    *,
    source_dir: Path,
    target_dir: Path,
) -> Path | None:
    """Derive metadata from ``source_dir`` and write it into ``target_dir``.

    Returns the written path, or ``None`` when the plugin could not be imported
    here. ``target_dir`` is the staged copy that goes into the package;
    ``source_dir`` is where the plugin's dependencies actually resolve, so the
    import runs there.

    A failure warns rather than failing the build. Packaging is not the place to
    insist that a plugin imports: the build machine may be missing an optional
    dependency, the plugin may target another OS, and refusing to produce the
    package at all would turn a metadata optimisation into a packaging gate. The
    host has defined behaviour for a package without metadata — it falls back to
    what the manifest declares — so shipping without it costs a degraded
    parameter form, not a broken plugin.
    """
    try:
        payload = derive_plugin_metadata(source_dir, hash_dir=Path(target_dir))
    except MetadataProbeError as exc:
        stale = Path(target_dir) / PACKAGED_METADATA_FILENAME
        if stale.exists():
            # 源树里本来就有一份（内置插件的就在仓库里），打包管线会先把它抄进
            # target_dir。这次没能重新生成却把那份旧的留在包里，等于拿上一次的
            # handler 和 schema 冒充这次的——而它的 source_sha 完全可能还对得上，
            # 宿主于是照单全收，本该走的 manifest 回落根本不会发生（codex）。
            try:
                stale.unlink()
            except OSError as unlink_exc:
                print(
                    f"[WARN] {Path(source_dir).name}: could not remove the stale "
                    f"{PACKAGED_METADATA_FILENAME} copied into the package "
                    f"({unlink_exc}); it may advertise metadata from an earlier "
                    "build.",
                    file=sys.stderr,
                )
        print(
            f"[WARN] {Path(source_dir).name}: could not derive plugin metadata "
            f"({exc}); packaging without {PACKAGED_METADATA_FILENAME}. Entry "
            "parameter schemas stay unavailable until the plugin runs.",
            file=sys.stderr,
        )
        return None
    meta_path = Path(target_dir) / PACKAGED_METADATA_FILENAME
    # newline="" 而不是默认：默认会在 Windows 上把 LF 翻成 CRLF，于是盘上这份生成物
    # 和仓库里存的那份天生不一致，每次 git status 都报行尾要被改写。
    meta_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    return meta_path
