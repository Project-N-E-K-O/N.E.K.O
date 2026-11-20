"""The Docker frontend-builder stage must copy every repo path the frontend builds read.

Both Dockerfiles build the two frontends in a stage that copies a hand-picked
list of directories (the frontend projects plus a few ``static/`` subdirs).
When a frontend source file starts importing something outside its own project
directory that is not in that list, ``npm run build-only`` / ``npm run build``
fails with ``UNRESOLVED_IMPORT`` inside Docker only -- the dev build and the
desktop build run against the full checkout and stay green. The Docker Build
workflow does not run on pull requests, so the breakage first shows up on main
(#3851: ``static/plugin-ui-sdk/display.js?raw`` added by #3643).

This test statically resolves the cross-project relative imports and checks
that the frontend-builder stage of both Dockerfiles copies a directory that
contains each target, at the same path under ``/src``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = ("Dockerfile", "Dockerfile.full")
BUILD_ROOT = PurePosixPath("/src")

# What each frontend build in the Docker stage actually reads:
#  - plugin-manager runs `build-only` (plain `vite build`): type-only imports are
#    erased and test / e2e files are not part of the bundle.
#  - react-neko-chat runs `tsc -b && vite build`; tsconfig.app.json includes the
#    *.test.ts(x) files, so tests and type-only imports must resolve as well.
FRONTENDS = {
    "plugin-manager": {"include_tests": False, "include_type_imports": False},
    "react-neko-chat": {"include_tests": True, "include_type_imports": True},
}
SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue"}
TEST_FILE = re.compile(r"\.(test|spec|e2e)\.[^.]+$")
TEST_DIRS = {"e2e", "__tests__", "tests"}

# Module specifiers in script sources: `import ... from '...'`,
# `export ... from '...'`, side-effect `import '...'`, dynamic `import('...')`
# (comments such as /* @vite-ignore */ allowed before the argument),
# `import.meta.glob('...')` and `new URL('...', ...)`. Only literal relative
# paths are checked; template literals with `${}` cannot be resolved statically.
# Known limit: CSS `@import` / `url()` and index.html are not scanned (neither
# frontend reaches outside its project from those today).
# Group "type" captures a leading `type` keyword (type-only import / export).
SPECIFIER = re.compile(
    r"""(?:
        \b(?:import|export)\s+(?P<type>type\s+)?(?:[^'"`;=()]*?\s+from\s+)?
      | (?<!typeof\s)\bimport\s*\(\s*(?:/\*.*?\*/\s*)*
      | \bimport\.meta\.glob\s*\(\s*(?:\[\s*)?
      | \bnew\s+URL\s*\(\s*
    )(?P<q>['"`])(?P<spec>\.\.?/[^'"`$]+)(?P=q)""",
    re.VERBOSE | re.DOTALL,
)


def _is_test_file(path: Path, project: Path) -> bool:
    rel = path.relative_to(project)
    return bool(TEST_FILE.search(path.name)) or any(part in TEST_DIRS for part in rel.parts)


def _cross_project_targets(name: str) -> dict[str, list[str]]:
    """Repo-relative targets outside the project dir -> importing files."""
    cfg = FRONTENDS[name]
    project = ROOT / "frontend" / name
    targets: dict[str, list[str]] = {}
    for path in sorted((project / "src").rglob("*")):
        if path.suffix not in SOURCE_SUFFIXES or "node_modules" in path.parts:
            continue
        if not cfg["include_tests"] and _is_test_file(path, project):
            continue
        text = path.read_text(encoding="utf-8")
        for match in SPECIFIER.finditer(text):
            if match.group("type") and not cfg["include_type_imports"]:
                continue
            spec = match.group("spec").split("?", 1)[0].split("#", 1)[0]
            target = Path(os.path.normpath(path.parent / spec))
            if target.is_relative_to(project):
                continue  # stays inside the project, always copied
            # Outside the repo can never be copied from the build context; keep
            # it as a visible (uncoverable) entry instead of crashing.
            rel = target.relative_to(ROOT).as_posix() if target.is_relative_to(ROOT) else str(target)
            targets.setdefault(rel, []).append(path.relative_to(ROOT).as_posix())
    return targets


def _builder_copies(dockerfile: str) -> list[tuple[str, PurePosixPath]]:
    """(source, destination) pairs of the frontend-builder stage's COPY lines."""
    lines = (ROOT / "docker" / dockerfile).read_text(encoding="utf-8").splitlines()
    copies: list[tuple[str, PurePosixPath]] = []
    in_stage = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"FROM\s", stripped, re.IGNORECASE):
            in_stage = bool(re.search(r"\bAS\s+frontend-builder\b", stripped, re.IGNORECASE))
            continue
        if not in_stage or not re.match(r"COPY\s", stripped, re.IGNORECASE):
            continue
        args = [a for a in stripped.split()[1:] if not a.startswith("--")]
        if len(args) == 2:
            copies.append((args[0].rstrip("/"), PurePosixPath(args[1])))
    assert copies, f"{dockerfile}: 没解析出 frontend-builder 阶段的 COPY，解析规则已失效"
    return copies


def _covered(target: str, copies: list[tuple[str, PurePosixPath]]) -> bool:
    for src, dst in copies:
        if (target == src or target.startswith(src + "/")) and dst == BUILD_ROOT / src:
            return True
    return False


def test_scanner_still_sees_the_known_cross_project_imports():
    # 防假绿：解析规则失效时下面的检查会因为"什么都没扫到"而通过。
    targets = _cross_project_targets("plugin-manager")
    assert "static/plugin-ui-sdk/display.js" in targets
    assert any(t.startswith("static/assets/tutorial/") for t in targets)
    # 仅类型导入在 build-only 下会被擦除，不应计入
    assert not any(t.startswith("plugin/sdk/") for t in targets)


@pytest.mark.parametrize("dockerfile", DOCKERFILES)
@pytest.mark.parametrize("frontend", sorted(FRONTENDS))
def test_frontend_builder_copies_every_cross_project_import(dockerfile, frontend):
    copies = _builder_copies(dockerfile)
    missing = {
        target: files
        for target, files in _cross_project_targets(frontend).items()
        if not _covered(target, copies)
    }
    assert not missing, (
        f"docker/{dockerfile} 的 frontend-builder 阶段没有拷这些文件，"
        f"Docker 里构建 {frontend} 会报 UNRESOLVED_IMPORT（Docker Build 不在 PR 上跑，"
        f"要到合并进 main 才会暴露）：{missing}。"
        "在两个 Dockerfile 的 frontend-builder 阶段补 COPY <目录> /src/<目录>。"
    )
