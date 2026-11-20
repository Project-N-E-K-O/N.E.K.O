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
import sys
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = ("Dockerfile", "Dockerfile.full")
BUILD_ROOT = PurePosixPath("/src")

# What each frontend build in the Docker stage actually reads:
#  - plugin-manager runs `build-only` (plain `vite build`): type-only imports are
#    erased and test / e2e files are not part of the bundle -- except in .vue
#    files, where @vue/compiler-sfc reads the imported types used by
#    defineProps<T>() / defineEmits<T>(), so .vue keeps type imports.
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
# Known limits (none of these occur in either frontend today):
#  - only `<project>/src` is scanned: vite.config.ts, tsconfig `extends` and
#    `/// <reference path>`, CSS `@import` / `url()` and index.html are not;
#  - comments are not stripped first, so a `//` comment containing quotes or
#    parentheses inside an import clause can hide that import.
# Group "type" captures a leading `type` keyword (type-only import / export).
# The clause between `import`/`export` and `from` may span lines (multi-line
# named imports) but never another import/export statement, so a bare
# `export type { Foo }` cannot swallow the value import on the next line.
# `typeof import('...')` is captured as a type reference ("tof"), so it is kept
# only where type imports matter (react-neko-chat's `tsc -b`).
# Known approximation (errs on the safe side): a bare `import('...')` in a type
# position, e.g. `type X = import('...').Y`, counts as a runtime reference and
# may ask for a COPY the build does not strictly need.
# `import.meta.glob([...])` arrays are handled by GLOB_ARRAY so that every
# literal in the array is checked, not just the first.
SPECIFIER = re.compile(
    r"""(?:
        \b(?:import|export)\s+(?P<type>type\s+)?(?:(?:(?!\b(?:import|export)\b)[^'"`;=()])*?\s+from\s+)?
      | (?P<tof>\btypeof\s+import\s*\(\s*(?:/\*.*?\*/\s*)*)
      | (?<!typeof\s)\bimport\s*\(\s*(?:/\*.*?\*/\s*)*
      | (?P<glob>\bimport\.meta\.glob\s*\(\s*)
      | \bnew\s+URL\s*\(\s*
    )(?P<q>['"`])(?P<spec>\.\.?/[^'"`$]+)(?P=q)""",
    re.VERBOSE | re.DOTALL,
)
# The array body skips whole string literals, so a `]` inside a pattern such as
# './[ab].json' does not end the array early.
GLOB_ARRAY = re.compile(
    r"""\bimport\.meta\.glob\s*\(\s*\[(?P<items>(?:[^\]'"`]|'[^']*'|"[^"]*"|`[^`]*`)*)\]"""
)
LITERAL = re.compile(r"""(?P<q>['"`])(?P<spec>!?\.\.?/[^'"`$]+)(?P=q)""")


def _strip_query(spec: str) -> str:
    return spec.split("?", 1)[0].split("#", 1)[0]


def _specifiers(text: str, include_type_imports: bool) -> list[str]:
    """Relative paths the build resolves, in source order.

    `?raw` / `#hash` suffixes are stripped from module specifiers; glob patterns
    are kept as-is because `?` is a wildcard there.
    """
    found: list[tuple[int, str]] = []
    for match in SPECIFIER.finditer(text):
        if (match.group("type") or match.group("tof")) and not include_type_imports:
            continue
        spec = match.group("spec")
        found.append((match.start(), spec if match.group("glob") else _strip_query(spec)))
    for match in GLOB_ARRAY.finditer(text):
        for item in LITERAL.finditer(match.group("items")):
            # vite glob negation patterns ('!../x') exclude files, never read them
            if not item.group("spec").startswith("!"):
                found.append((match.start("items") + item.start(), item.group("spec")))
    return [spec for _, spec in sorted(found)]


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
        keep_types = cfg["include_type_imports"] or path.suffix == ".vue"
        for spec in _specifiers(text, keep_types):
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
    text = (ROOT / "docker" / dockerfile).read_text(encoding="utf-8")
    # Join backslash-continued instructions into one logical line.
    lines = re.sub(r"\\\s*\n", " ", text).splitlines()
    copies: list[tuple[str, PurePosixPath]] = []
    in_stage = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"FROM\s", stripped, re.IGNORECASE):
            in_stage = bool(re.search(r"\bAS\s+frontend-builder\b", stripped, re.IGNORECASE))
            continue
        if not in_stage or not re.match(r"COPY\s", stripped, re.IGNORECASE):
            continue
        flags = [a for a in stripped.split()[1:] if a.startswith("--")]
        if any(f.lower().startswith("--from") for f in flags):
            continue  # copies from another stage / image, not the build context
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
    # 仅类型导入在 build-only 下会被擦除，不应计入（只核对已知的这几个文件，
    # 以后真有值导入并补了 COPY，不应让这条自检误报）
    type_only = {
        "frontend/plugin-manager/src/composables/usePluginDisplay.ts",
        "frontend/plugin-manager/src/composables/usePluginDisplayDialog.ts",
        "frontend/plugin-manager/src/composables/pluginDisplaySession.ts",
    }
    for path in type_only:
        assert (ROOT / path).is_file(), f"{path} 不存在了，请更新这条自检"
    leaked = {t: f for t, f in targets.items() if type_only & set(f)}
    assert not leaked, f"仅类型导入被当成了运行时引用：{leaked}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import a from '../../x/a.js?raw'", ("", "../../x/a.js?raw")),
        ("import {\n  a,\n  b,\n} from \"../y\"", ("", "../y")),
        ("import type { T } from '../t'", ("type", "../t")),
        ("export type { T } from '../t'", ("type", "../t")),
        ("export * from '../e'", ("", "../e")),
        ("import '../side.css'", ("", "../side.css")),
        ("import('../d')", ("", "../d")),
        ("import(/* @vite-ignore */ '../dc')", ("", "../dc")),
        ("import(`../tl`)", ("", "../tl")),
        ("import.meta.glob('../g/*.json')", ("", "../g/*.json")),
        ("type M = typeof import('../ti')", ("type", "../ti")),
        ("type M = typeof import(/* c */ '../tc')", ("type", "../tc")),
        ("new URL('../u.png', import.meta.url)", ("", "../u.png")),
        # 不带 from 的 export type 不能吞掉下一行的值导入
        ("export type { Foo }\nimport bar from '../x.js'", ("", "../x.js")),
        ("export { a }\nimport b from '../x.js'", ("", "../x.js")),
        ("import(`../${name}`)", None),
        ("export type Foo = { a: string }\nconst q = 1", None),
    ],
)
def test_specifier_regex(source, expected):
    matches = list(SPECIFIER.finditer(source))
    if expected is None:
        assert not matches, [m.group(0) for m in matches]
        return
    assert len(matches) == 1, [m.group(0) for m in matches]
    kind = "type" if (matches[0].group("type") or matches[0].group("tof")) else ""
    assert (kind, matches[0].group("spec")) == expected


@pytest.mark.parametrize("frontend", sorted(FRONTENDS))
def test_type_references_follow_each_frontends_build(frontend):
    # plugin-manager 只跑 vite build，类型引用会被擦除；react-neko-chat 的
    # tsc -b 仍要解析它们。
    source = (
        "import type { A } from '../../../plugin/a'\n"
        "type B = typeof import('../../../plugin/b')\n"
        "type D = typeof import(/* note */ '../../../plugin/d')\n"
        "import c from '../../../static/c.js'\n"
    )
    keep_types = FRONTENDS[frontend]["include_type_imports"]
    expected = ["../../../static/c.js"]
    if keep_types:
        expected = [
            "../../../plugin/a",
            "../../../plugin/b",
            "../../../plugin/d",
            "../../../static/c.js",
        ]
    assert _specifiers(source, keep_types) == expected


def test_glob_array_checks_every_literal():
    source = "const m = import.meta.glob(['./local/*.json', '../../../static/feature/*.json', '!../../../static/skip/*.json'])"
    assert _specifiers(source, False) == ["./local/*.json", "../../../static/feature/*.json"]
    assert _specifiers("import.meta.glob(['../ga/*.json'])", False) == ["../ga/*.json"]
    # 字符串里的 ] 不能被当成数组结尾
    bracket = "import.meta.glob(['./local/[ab].json', '../../../static/feature/*.json'])"
    assert _specifiers(bracket, False) == ["./local/[ab].json", "../../../static/feature/*.json"]
    assert _specifiers("import.meta.glob('../g/*.json')", False) == ["../g/*.json"]
    # glob 里的 ? 是通配符，不是 query；模块路径上的 ?raw 才要去掉
    assert _specifiers("import.meta.glob('../../../static/a?/x.png')", False) == [
        "../../../static/a?/x.png"
    ]
    assert _specifiers("import d from '../../x/d.js?raw'", False) == ["../../x/d.js"]


def test_vue_files_keep_type_imports(tmp_path, monkeypatch):
    # .vue 里 defineProps<T>() 用到的导入类型会被 compiler-sfc 读取
    project = tmp_path / "frontend" / "plugin-manager"
    (project / "src").mkdir(parents=True)
    line = "import type {{ X }} from '../../../plugin/sdk/{}'\n"
    (project / "src" / "Comp.vue").write_text(
        "<script setup lang=\"ts\">\n" + line.format("vue-t") + "</script>\n", encoding="utf-8"
    )
    (project / "src" / "util.ts").write_text(line.format("ts-t"), encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    assert sorted(_cross_project_targets("plugin-manager")) == ["plugin/sdk/vue-t"]


def test_builder_copies_ignore_other_stages_and_join_continuations(tmp_path, monkeypatch):
    docker = tmp_path / "docker"
    docker.mkdir()
    (docker / "Dockerfile").write_text(
        "FROM node:24-slim AS frontend-builder\n"
        "COPY --from=other /src/static/x /src/static/x\n"
        "COPY --chown=1:1 \\\n    static/y /src/static/y\n"
        "FROM debian\n"
        "COPY static/z /src/static/z\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    assert _builder_copies("Dockerfile") == [("static/y", PurePosixPath("/src/static/y"))]


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
