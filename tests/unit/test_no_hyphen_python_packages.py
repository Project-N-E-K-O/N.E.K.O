"""Lint: prohibit Python source inside hyphenated directories.

## Background

``plugin/neko-plugin-cli/`` historically had this layout:

```
plugin/neko-plugin-cli/
├── cli.py
├── public/
│   ├── __init__.py
│   └── *.py
└── docs/
```

Server-side ``plugin_cli/service.py`` used ``sys.path.insert(_CLI_ROOT)`` +
``from public import ...`` to treat ``public`` as a top-level package. Source mode worked, but
Nuitka standalone builds suffered two silent failures:

1. **`--include-package=...` cannot follow it**: hyphenated directories are invalid Python package names,
   so ``--include-package=plugin.neko-plugin-cli.public`` is rejected and
   ``public/*.py`` never compiles into the executable.
2. **`--include-data-dir=` filters .py by default** (see
   ``nuitka.freezer.IncludedDataFiles.default_ignored_suffixes``); even with a
   data-dir fallback, ``.py`` files silently disappear, leaving only noncode such as ``docs/*.md``.

Together these make bundle startup's ``from public import`` raise
``ModuleNotFoundError``, preventing embedded user-plugin server startup and making the entire plugin-management
UI inaccessible.

After #1109 it was renamed ``plugin/neko_plugin_cli/`` with package-style imports.
This regression test fails immediately for any ``.py`` under a hyphenated directory.

## Design tradeoff

Use only directory-name hyphens plus .py presence, without AST analysis of ``sys.path.insert``:

- This is a necessary condition for the bug; eliminating these directories eliminates the whole class.
- The rule is simple, explains itself, and has no false positives.
- Map external hyphenated CLI product names through ``pyproject.toml
  [project.scripts]`` while naming underlying Python packages with underscores.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Subtrees skipped directly by directory name.
_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "build_nuitka",
        "launcher.build",
        ".claude",  # Includes worktrees.
        ".agent",  # AI coding-assistant skill scripts: not project source and excluded from Nuitka bundles.
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".uv-cache",  # uv artifact cache; third-party extracted wheels, not project source
        "playwright_browsers",
        "data",  # data/browser_use_extensions and similar paths contain hyphenated extension IDs but no .py files.
        "site-packages",  # Virtual-environment site-packages always contains third-party code.
    }
)
# Any .venv* directory, including .venv and .venv_monitor, is a virtual environment; skip entirely.
_VENV_DIR_PATTERN = re.compile(r"^\.venv")
# Path-prefix exclusions match from the project root.
# Deliberately avoid arbitrary-depth sliding matches: single-element fragments such as ``("frontend",)`` would silently
# exclude future ``plugin/<feature>/frontend/`` or ``plugin/<x>/docs/``
# subdirectories and conceal the hyphen violations this lint detects.
_EXCLUDED_PATH_FRAGMENTS = (
    # Git may leave initialized submodule files behind when updating an old checkout.
    # Retain this legacy path exclusion after removing the submodule from the index.
    ("local_server", "cosyvoice_server", "CosyVoice"),
    ("frontend",),  # Top-level JS/Vue projects conventionally use hyphens.
    ("docs",),  # Top-level documentation-only site.
    (".github",),  # CI YAML, etc.
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
    """Do not place ``.py`` source directly in hyphenated directories.

    Name underlying Python packages with underscores; map hyphenated external CLI names through
    ``pyproject.toml [project.scripts]``.
    """
    offenders: list[str] = []
    for d in _iter_dirs():
        if "-" not in d.name:
            continue
        # Use rglob rather than glob: nested paths such as my-tool/pkg/__init__.py remain unimportable
        # because of a hyphenated ancestor and must also be reported. rglob
        # descends into noise such as __pycache__/.venv, filtered by _is_excluded_part.
        py_files = sorted(
            p.relative_to(d).as_posix()
            for p in d.rglob("*.py")
            if not any(_is_excluded_part(part) for part in p.relative_to(d).parts)
        )
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


@pytest.mark.unit
def test_removed_submodule_checkout_does_not_hide_owned_violations(tmp_path, monkeypatch) -> None:
    """Ignore a leftover submodule checkout while still checking owned source."""
    monkeypatch.setitem(globals(), "PROJECT_ROOT", tmp_path)
    leftover = tmp_path / "local_server/cosyvoice_server/CosyVoice/third_party/Matcha-TTS"
    leftover.mkdir(parents=True)
    (leftover / "model.py").write_text("", encoding="utf-8")

    test_no_hyphen_directory_contains_python_source()

    owned = tmp_path / "owned-package"
    owned.mkdir()
    (owned / "module.py").write_text("", encoding="utf-8")
    with pytest.raises(AssertionError, match="owned-package"):
        test_no_hyphen_directory_contains_python_source()
