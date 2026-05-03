"""Lint test：禁止把 Python 源码放进带连字符的目录。

## 背景

``plugin/neko-plugin-cli/`` 历史上是这种结构：

```
plugin/neko-plugin-cli/
├── cli.py
├── public/
│   ├── __init__.py
│   └── *.py
└── docs/
```

server 侧 ``plugin_cli/service.py`` 通过 ``sys.path.insert(_CLI_ROOT)`` +
``from public import ...`` 把 ``public`` 当顶层包用。源码模式能跑，但打成
Nuitka standalone **双重静默失败**：

1. **`--include-package=...` 跟不进**：连字符目录不是合法 Python 包名，
   ``--include-package=plugin.neko-plugin-cli.public`` 不被接受，
   ``public/*.py`` 不会被编译进 exe。
2. **`--include-data-dir=` 默认过滤 .py**（见
   ``nuitka.freezer.IncludedDataFiles.default_ignored_suffixes``），即便加
   data-dir 兜底，``.py`` 也会被默默丢，dist 里只剩 ``docs/*.md`` 这种非代码。

合并起来：bundle 里 server 启动时 ``from public import`` 直接
``ModuleNotFoundError``，embedded user plugin server 起不来，plugin 管理
UI 整个无法访问。

#1109 之后已重命名为 ``plugin/neko_plugin_cli/`` 并改成 package-style import；
本测试**防止回归**：任何带连字符的目录里出现 ``.py`` 文件都会立刻失败。

## 设计取舍

只用"目录名带连字符 + 含 .py"这一条规则，不去 AST 分析 ``sys.path.insert``：

- 这是触发 bug 的**必要条件**——只要消灭这种目录就消灭整类 bug；
- 规则简单、零误报、易解释；
- 想保留对外的 hyphen 名字（例如 CLI 工具产品名）就在 ``pyproject.toml
  [project.scripts]`` 里映射，底层 Python 包仍用下划线。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 直接按目录名跳过的子树。
_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "build_nuitka",
        "launcher.build",
        ".claude",  # 含 worktrees
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "playwright_browsers",
        "data",  # data/browser_use_extensions 等带连字符 extension id，但不含 .py
        "site-packages",  # 任何虚拟环境的 site-packages 都是第三方代码
    }
)
# 任意 .venv* 目录（.venv、.venv_monitor 等）都是虚拟环境，整体跳过。
_VENV_DIR_PATTERN = re.compile(r"^\.venv")
# 路径片段级排除（任意层级匹配即跳过整个子树）。
_EXCLUDED_PATH_FRAGMENTS = (
    ("local_server", "cosyvoice_server", "CosyVoice"),  # vendored
    ("frontend",),  # JS/Vue 项目，连字符是其命名惯例
    ("docs",),  # 纯文档站
    (".github",),  # CI yaml 等
)


def _is_excluded_part(part: str) -> bool:
    return part in _EXCLUDED_DIR_NAMES or bool(_VENV_DIR_PATTERN.match(part))


def _iter_dirs() -> list[Path]:
    out: list[Path] = []
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_dir():
            continue
        parts = path.relative_to(PROJECT_ROOT).parts
        if any(_is_excluded_part(part) for part in parts):
            continue
        if any(
            parts[: len(frag)] == frag for frag in _EXCLUDED_PATH_FRAGMENTS if len(parts) >= len(frag)
        ):
            continue
        out.append(path)
    return out


@pytest.mark.unit
def test_no_hyphen_directory_contains_python_source() -> None:
    """带连字符目录里不能直接放 ``.py`` 源文件。

    底层 Python 包用下划线命名；外部 CLI 工具产品名的连字符可在
    ``pyproject.toml [project.scripts]`` 里映射。
    """
    offenders: list[str] = []
    for d in _iter_dirs():
        if "-" not in d.name:
            continue
        py_files = sorted(p.name for p in d.glob("*.py"))
        if not py_files:
            continue
        rel = d.relative_to(PROJECT_ROOT).as_posix()
        offenders.append(f"{rel} 含有 {len(py_files)} 个 .py 文件，例如：{py_files[0]}")

    assert not offenders, (
        "发现带连字符目录里有 Python 源码（Nuitka standalone 会静默丢失）：\n  "
        + "\n  ".join(offenders)
        + "\n\n请把目录重命名为下划线形式（如 my-tool → my_tool），"
        "并改 import 路径为 package 形式。"
    )
