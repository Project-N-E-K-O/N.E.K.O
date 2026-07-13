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

"""Sanity-check a Nuitka standalone dist directory.

Run after ``build_nuitka.bat`` (local) or the ``build-desktop.yml`` Nuitka step
(CI). Exits non-zero on the first missing critical asset, before signing or
packaging into Electron.

## What it catches

- ``--include-data-dir=`` silently dropped files that Nuitka treats as code
  (e.g., the historical ``plugin/neko-plugin-cli/`` bug where ``--include-data-dir``
  filtered out ``.py`` and only ``docs/*.md`` reached dist).
- Whole top-level directories missing because of file-lock collisions in
  ``rmdir /s /q dist\\Xiao8`` followed by ``move dist\\launcher.dist dist\\Xiao8``
  nesting on top of the leftover (each such collision turns into a partial,
  half-broken bundle that boots but lacks config/static/templates).
- Built-in plugins that lost their ``plugin.toml`` (means the plugin scanner
  will produce zero plugins at runtime).

## What it does NOT do

- Does not launch the exe. That's expensive, platform-specific, and would
  drag in a network of subprocesses.
- Does not check compiled-into-exe Python modules (no way to enumerate them
  from outside the binary). Coverage of that surface lives in:
  - ``tests/unit/test_no_hyphen_python_packages.py`` — the source-level lint
  - the L3 doc rule in CLAUDE.md
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


# 关键资产清单：dist 根目录下**必须**存在的相对路径。第二项是文件名表，
# None 表示"目录非空即可"，否则是"必须包含至少一个该模式文件"。
_REQUIRED_ASSETS: tuple[tuple[str, str | None], ...] = (
    ("projectneko_server.exe", None),  # 主入口 exe（platform 在 macOS/Linux 上要重命名）
    ("config", "core_config.json"),
    ("config", "characters.json"),
    ("config", "api_providers.json"),
    # changelog/.md 与 survey/.json 是纯数据，--include-package=config 只编 .py 不带；
    # 守 dist 里确有它们，否则 /api/changelog、/api/survey（Steam-only）打包后读空。
    ("config/changelog", None),
    ("config/surveys", None),
    # 本地化角色种子目录 config/characters/<locale>.json（PR #1282）。--include-package=config
    # 只编 .py 不带；守该目录里至少有一份 locale json，否则非默认语言用户的角色种子回退错语言。
    ("config/characters", "*.json"),
    ("static", None),
    ("templates", None),
    ("assets", None),
    ("data/browser_use_prompts", None),
    ("frontend/plugin-manager/dist", "index.html"),
    ("plugin/plugins", None),
    # 应用内 OpenClaw 引导文档 + 图片，agent_router 经 /api/agent/openclaw/guide/* 提供；纯数据目录。
    ("docs/zh-CN/guide", None),
)

# 内置插件目录里每一个子目录都必须有 plugin.toml；用来抓 ``plugin/plugins``
# 整体被 ``--include-data-dir`` 包了空壳的情况。
_PLUGIN_TOML_REQUIRED_PARENT = "plugin/plugins"


def _check_asset(dist_root: Path, rel: str, must_contain: str | None) -> str | None:
    p = dist_root / rel
    if rel.endswith(".exe") or rel.endswith(".bin"):
        # 平台差异：Windows 是 .exe，Linux/macOS 没有后缀。允许 fallback。
        if not p.exists():
            stem = p.with_suffix("")
            if stem.exists():
                p = stem
        if not p.is_file() or p.stat().st_size == 0:
            return f"missing or empty: {rel} (also tried {p.with_suffix('').name})"
        return None
    if not p.exists():
        return f"missing: {rel}"
    if must_contain is None:
        if p.is_dir() and not any(p.iterdir()):
            return f"empty directory: {rel}"
        return None
    target = p / must_contain
    if "*" in must_contain:
        matches = list(p.glob(must_contain))
        if not matches:
            return f"no file matching {must_contain} in {rel}/"
        return None
    if not target.is_file():
        return f"missing: {rel}/{must_contain}"
    return None


def _check_plugin_tomls(dist_root: Path) -> list[str]:
    plugins_dir = dist_root / _PLUGIN_TOML_REQUIRED_PARENT
    if not plugins_dir.is_dir():
        # 上面 _check_asset 已经会报；不重复。
        return []
    issues: list[str] = []
    plugin_subdirs = [p for p in plugins_dir.iterdir() if p.is_dir()]
    if not plugin_subdirs:
        issues.append(f"no plugin subdirectories under {_PLUGIN_TOML_REQUIRED_PARENT}/")
        return issues
    for sub in plugin_subdirs:
        if sub.name.startswith("_"):
            continue
        if not (sub / "plugin.toml").is_file():
            issues.append(f"plugin missing plugin.toml: {sub.relative_to(dist_root).as_posix()}")
    return issues


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_plugin_stage(dist_root: Path, stage_root: Path) -> list[str]:
    """Require the installed plugin payload to exactly match the filtered stage."""

    installed_root = dist_root / _PLUGIN_TOML_REQUIRED_PARENT
    if not stage_root.is_dir():
        return [f"plugin stage does not exist: {stage_root}"]
    if not installed_root.is_dir():
        return []  # The regular required-asset check reports this more clearly.

    staged_files = {
        path.relative_to(stage_root).as_posix(): path
        for path in stage_root.rglob("*")
        if path.is_file()
    }
    installed_files = {
        path.relative_to(installed_root).as_posix(): path
        for path in installed_root.rglob("*")
        if path.is_file()
    }

    issues: list[str] = []
    missing = sorted(staged_files.keys() - installed_files.keys())
    unexpected = sorted(installed_files.keys() - staged_files.keys())
    if missing:
        issues.append(f"plugin payload missing {len(missing)} staged file(s): {missing[:5]}")
    if unexpected:
        issues.append(f"plugin payload contains {len(unexpected)} unstaged file(s): {unexpected[:5]}")

    mismatched = [
        relative
        for relative in sorted(staged_files.keys() & installed_files.keys())
        if _file_digest(staged_files[relative]) != _file_digest(installed_files[relative])
    ]
    if mismatched:
        issues.append(f"plugin payload changed after staging ({len(mismatched)} file(s)): {mismatched[:5]}")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "dist_root",
        nargs="?",
        default="dist/Xiao8",
        help="Path to Nuitka standalone dist root (default: dist/Xiao8)",
    )
    parser.add_argument(
        "--plugin-stage",
        type=Path,
        help=(
            "Filtered built-in plugin stage produced by prepare_nuitka_plugins.py; "
            "when provided, the installed plugin payload must match it exactly"
        ),
    )
    args = parser.parse_args(argv)

    dist_root = Path(args.dist_root).resolve()
    if not dist_root.is_dir():
        print(f"[FAIL] dist root does not exist or is not a directory: {dist_root}", file=sys.stderr)
        return 1

    issues: list[str] = []
    for rel, contains in _REQUIRED_ASSETS:
        problem = _check_asset(dist_root, rel, contains)
        if problem:
            issues.append(problem)

    issues.extend(_check_plugin_tomls(dist_root))
    if args.plugin_stage is not None:
        issues.extend(_check_plugin_stage(dist_root, args.plugin_stage.resolve()))

    if issues:
        print(f"[FAIL] Nuitka dist verification failed for {dist_root}:", file=sys.stderr)
        for it in issues:
            print(f"  - {it}", file=sys.stderr)
        print(
            "\nHints:\n"
            "  - 'missing config/static/templates' often means rmdir on dist\\Xiao8 "
            "failed (file lock from a previous run); kill all neko/projectneko "
            "processes and rebuild.\n"
            "  - 'plugin missing plugin.toml' means --include-data-dir=plugin/plugins "
            "ran but the source dir is empty/wrong.\n"
            "  - For Python packages that were missing on import (e.g., the historical "
            "neko-plugin-cli case), see tests/unit/test_no_hyphen_python_packages.py "
            "and the 'Nuitka packaging caveats' section in CLAUDE.md.",
            file=sys.stderr,
        )
        return 1

    print(f"[OK] Nuitka dist looks healthy: {dist_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
