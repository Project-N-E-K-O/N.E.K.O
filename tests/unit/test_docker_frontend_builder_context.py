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

import json
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
#    defineProps<T>() / defineEmits<T>(), so .vue keeps type imports. This is
#    deliberately approximate: a .vue type import used only for e.g. ref<T>()
#    is also kept (over-reports), and types reached through an imported .ts
#    file are not followed (see the known limits below).
#  - react-neko-chat runs `tsc -b && vite build`; tsconfig.app.json includes the
#    *.test.ts(x) files, so tests and type-only imports must resolve as well.
FRONTENDS = {
    "plugin-manager": {"include_tests": False, "include_type_imports": False},
    "react-neko-chat": {"include_tests": True, "include_type_imports": True},
}
SCRIPT_SUFFIXES = {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"}
STYLE_SUFFIXES = {".css", ".scss", ".sass", ".less"}
SOURCE_SUFFIXES = SCRIPT_SUFFIXES | STYLE_SUFFIXES | {".vue"}
# Besides src/, the Docker builds also read these files at the project root:
# `vite build` loads vite.config.* (bundling its relative imports) and
# index.html, and both `vite build` and `tsc -b` follow the tsconfig chain
# (`extends`, `references`, `files` / `include`). Root *.d.ts files are pulled in
# by tsconfig includes.
ROOT_ENTRY_GLOBS = ("vite.config.*", "index.html", "*.d.ts")
TEST_FILE = re.compile(r"\.(test|spec|e2e)\.[^.]+$")
TEST_DIRS = {"e2e", "__tests__", "tests"}

# Module specifiers in script sources: `import ... from '...'`,
# `export ... from '...'`, side-effect `import '...'`, dynamic `import('...')`
# (comments such as /* @vite-ignore */ allowed before the argument),
# `import.meta.glob('...')` and `new URL('...', ...)`. Only literal relative
# paths are checked; template literals with `${}` cannot be resolved statically.
# JS / TS comments are stripped first (_strip_js_comments), so a `//` comment
# with quotes or parentheses inside an import clause cannot hide the import.
# `/// <reference path="...">` is read before stripping.
# Known limits (none of these occur in either frontend today): asset URLs in a
# .vue <template> (`<img src="../...">`), build scripts run from package.json
# (e.g. scripts/*.mjs), paths built at runtime (path.resolve(...)), scss
# `@use` / `@forward` and the second path of `@import 'a', 'b'`, and types
# reached through an imported .ts file from a .vue defineProps<T>() are not
# followed. The comment lexer treats `/` after `)` as division, so a regex
# literal right after `if (...)` containing `//` could blank the rest of its
# line.
# On the Dockerfile side (_builder_copies / _covered): only two-argument
# `COPY <src> <dst>` is understood -- JSON-form `COPY ["a", "b"]`, multi-source
# COPY and ADD are ignored, and .dockerignore / `COPY --exclude` are not taken
# into account. Coverage is a path-prefix check, so a glob with a wildcard in a
# directory segment (e.g. `static/icon?/*.svg`) may over-report.
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


TRIPLE_SLASH_PATH = re.compile(r"""^[ \t]*///[ \t]*<reference\s+path\s*=\s*(['"])(?P<spec>[^'"]+)\1""", re.M)
CSS_IMPORT = re.compile(r"""@import\s+(?:url\(\s*)?(['"]?)(?P<spec>\.\.?/[^'")\s;]+)\1""")
CSS_URL = re.compile(r"""\burl\(\s*(['"]?)(?P<spec>\.\.?/[^'")\s]+)\1\s*\)""")
HTML_REF = re.compile(r"""\b(?:src|href)\s*=\s*(['"])(?P<spec>\.\.?/[^'"]+)\1""")
VUE_BLOCK = re.compile(r"<(?P<tag>script|style)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)\s*>", re.S | re.I)
VUE_SRC = re.compile(r"""\bsrc\s*=\s*(['"])(?P<spec>\.\.?/[^'"]+)\1""")

# A `/` starts a regex literal (not division) after these characters or keywords.
_REGEX_AFTER_CHARS = set("(,=:[!&|?{};+-*%~^")
_REGEX_AFTER_WORDS = {
    "return", "typeof", "case", "do", "else", "in", "of", "new", "delete",
    "void", "throw", "yield", "await", "instanceof",
}


def _blank(text: str) -> str:
    """Replace everything except newlines with spaces (keeps line numbers)."""
    return re.sub(r"[^\n]", " ", text)


def _strip_js_comments(text: str) -> str:
    """Blank out // and /* */ comments, leaving strings and regex literals intact.

    A small lexer rather than a regex, so that `//` inside a string
    ('https://...') or a regex literal is not taken for a comment, and quotes
    or parentheses inside a comment cannot derail the import matching.
    Template literals are copied up to the next unescaped backtick; nested
    templates inside `${}` are not tracked (good enough for import scanning).
    """
    out: list[str] = []
    n = len(text)
    i = 0
    prev = ""  # last significant (non-space) code character emitted
    word = ""  # identifier ending at `prev`, if any
    word_open = False
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(_blank(text[i:j]))
            i = j
            continue
        if c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(_blank(text[i:j]))
            i = j
            continue
        if c in "'\"`":
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 1
                elif c != "`" and text[j] == "\n":
                    break  # unterminated ordinary string: stop at end of line
                j += 1
            j = min(j + 1, n)
            out.append(text[i:j])
            i = j
            prev, word, word_open = c, "", False
            continue
        if c == "/" and (
            prev == "" or prev in _REGEX_AFTER_CHARS or (word in _REGEX_AFTER_WORDS and prev == word[-1:])
        ):
            j = i + 1
            in_class = False
            while j < n and text[j] != "\n":
                ch = text[j]
                if ch == "\\":
                    j += 2
                    continue
                if in_class:
                    in_class = ch != "]"
                elif ch == "[":
                    in_class = True
                elif ch == "/":
                    break
                j += 1
            j = min(j + 1, n)
            out.append(text[i:j])
            i = j
            prev, word, word_open = ")", "", False  # a regex is an operand
            continue
        out.append(c)
        if c.isspace():
            word_open = False
        else:
            if c.isalnum() or c in "_$":
                word = word + c if word_open else c
                word_open = True
            else:
                word, word_open = "", False
            prev = c
        i += 1
    return "".join(out)


def _strip_css_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", lambda m: _blank(m.group(0)), text, flags=re.S)


def _script_specifiers(text: str, include_type_imports: bool) -> list[str]:
    refs = [m.group("spec") for m in TRIPLE_SLASH_PATH.finditer(text)]
    return refs + _specifiers(_strip_js_comments(text), include_type_imports)


def _style_specifiers(text: str) -> list[str]:
    text = _strip_css_comments(text)
    found = [(m.start(), m.group("spec")) for m in CSS_IMPORT.finditer(text)]
    found += [(m.start(), m.group("spec")) for m in CSS_URL.finditer(text)]
    # `@import url(...)` matches both patterns; keep each path once, in order.
    return list(dict.fromkeys(_strip_query(spec) for _, spec in sorted(found)))


def _html_specifiers(text: str) -> list[str]:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return [_strip_query(m.group("spec")) for m in HTML_REF.finditer(text)]


def _file_specifiers(path: Path, include_type_imports: bool) -> list[str]:
    """Relative references a build reads from one file, by file type."""
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in SCRIPT_SUFFIXES:
        return _script_specifiers(text, include_type_imports)
    if suffix in STYLE_SUFFIXES:
        return _style_specifiers(text)
    if suffix == ".html":
        return _html_specifiers(text)
    if suffix == ".vue":
        specs: list[str] = []
        for block in VUE_BLOCK.finditer(text):
            # <style src="..."> / <script src="..."> pull in an external file
            specs += [_strip_query(m.group("spec")) for m in VUE_SRC.finditer(block.group("attrs"))]
            body = block.group("body")
            if block.group("tag").lower() == "script":
                # compiler-sfc reads the types used by defineProps<T>() etc.
                specs += _script_specifiers(body, True)
            else:
                specs += _style_specifiers(body)
        return specs
    return []


def _load_jsonc(path: Path) -> dict:
    """tsconfig files are JSON with comments and trailing commas."""
    text = _strip_js_comments(path.read_text(encoding="utf-8-sig"))
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


def _tsconfig_specifiers(path: Path) -> list[str]:
    """Relative paths a tsconfig points at: extends, references, files, include."""
    data = _load_jsonc(path)
    specs: list[str] = []
    extends = data.get("extends", [])
    for spec in [extends] if isinstance(extends, str) else extends:
        if spec.startswith("."):  # bare names are npm packages
            # TypeScript appends .json to a relative extends that lacks it
            specs.append(spec if spec.endswith(".json") else spec + ".json")
    for ref in data.get("references", []):
        ref_path = Path(os.path.normpath(path.parent / ref["path"]))
        specs.append(ref["path"] + ("/tsconfig.json" if ref_path.suffix != ".json" else ""))
    for key in ("files", "include"):
        specs += [p for p in data.get(key, []) if p.startswith(".")]
    return specs


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

    def add(path: Path, specs: list[str]) -> None:
        for spec in specs:
            target = Path(os.path.normpath(path.parent / spec))
            if target.is_relative_to(project):
                continue  # stays inside the project, always copied
            # Outside the repo can never be copied from the build context; keep
            # it as a visible (uncoverable) entry instead of crashing.
            rel = target.relative_to(ROOT).as_posix() if target.is_relative_to(ROOT) else str(target)
            targets.setdefault(rel, []).append(path.relative_to(ROOT).as_posix())

    files = sorted((project / "src").rglob("*"))
    for pattern in ROOT_ENTRY_GLOBS:
        files += sorted(project.glob(pattern))
    for path in files:
        if not path.is_file() or "node_modules" in path.parts:
            continue
        if path.suffix not in SOURCE_SUFFIXES and path.suffix != ".html":
            continue
        if not cfg["include_tests"] and _is_test_file(path, project):
            continue
        add(path, _file_specifiers(path, cfg["include_type_imports"]))

    # Follow the tsconfig chain from the project's tsconfig.json.
    pending = [project / "tsconfig.json"]
    seen: set[Path] = set()
    while pending:
        tsconfig = pending.pop()
        if tsconfig in seen or not tsconfig.is_file():
            continue
        seen.add(tsconfig)
        specs = _tsconfig_specifiers(tsconfig)
        add(tsconfig, specs)
        for spec in specs:
            nxt = Path(os.path.normpath(tsconfig.parent / spec))
            if nxt.suffix == ".json":
                pending.append(nxt)
    return targets


def _builder_copies(dockerfile: str) -> list[tuple[str, PurePosixPath]]:
    """(source, destination) pairs of the frontend-builder stage's COPY lines."""
    text = (ROOT / "docker" / dockerfile).read_text(encoding="utf-8")
    # Like Docker: drop comment lines first (they never take part in line
    # continuation), then join backslash-continued instructions. Otherwise a
    # comment ending in `\` would swallow the next FROM and lose the stage
    # boundary.
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
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
        "# Run: docker run \\\n"
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


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # 注释里的引号 / 括号不能让整条导入漏掉
        ("import {\n  a, // don't touch\n} from '../../../static/x.js'", ["../../../static/x.js"]),
        ("import( // c\n  '../x')", ["../x"]),
        ("import {\n  a, /* (b) 'c' */\n} from '../y'", ["../y"]),
        # 注释里提到的导入不算
        ("// import q from '../q'\n/* import('../r') */", []),
        # 字符串和正则字面量里的 // 不是注释
        ("const u = 'http://a'; import w from '../w'", ["../w"]),
        ("const t = `//${x}`; import v from '../v'", ["../v"]),
        ("const r = /\\/\\/'/g; import z from '../z'", ["../z"]),
        ("const d = a / b; // '\nimport e from '../e'", ["../e"]),
        ("if (x) return /'/.test(y)\nimport f from '../f'", ["../f"]),
        # 字符串 / 模板 / 正则里的 /* 不是块注释开头
        ("const s = '/*'; import g from '../g'; const e = '*/'", ["../g"]),
        ("const s = \"a/*b\"; import h from '../h'", ["../h"]),
        ("const t = `/* ${x}`; import i from '../i'", ["../i"]),
        ("const r = /\\/\\*/; import j from '../j'", ["../j"]),
        ("const r = /a[//]'/; import j2 from '../j2'", ["../j2"]),
        ("const r = /[/*]/; import k from '../k'", ["../k"]),
        # 反过来：注释里的引号 / 反引号不能开出字符串
        ("/* ` */ import l from '../l'", ["../l"]),
    ],
)
def test_comments_are_stripped_before_matching(source, expected):
    assert _script_specifiers(source, False) == expected


def test_triple_slash_reference_path_is_followed():
    source = '/// <reference path="../../../shared/types.d.ts" />\n/// <reference types="vite/client" />\n'
    assert _script_specifiers(source, False) == ["../../../shared/types.d.ts"]


def test_style_and_html_references():
    css = (
        "@import './local.css';\n"
        "@import url('../../../static/a.css');\n"
        "/* url(../../../static/commented.png) */\n"
        ".x { background: url(../../../static/b.png?v=1); }\n"
        ".y { background: url('/static/runtime.png'); }\n"
    )
    assert _style_specifiers(css) == ["./local.css", "../../../static/a.css", "../../../static/b.png"]
    html = (
        '<link rel="icon" href="favicon.ico">\n'
        '<!-- <script src="../commented.js"></script> -->\n'
        '<script type="module" src="/src/main.ts"></script>\n'
        '<link rel="stylesheet" href="../static/c.css">\n'
    )
    assert _html_specifiers(html) == ["../static/c.css"]


def test_root_configs_and_tsconfig_chain_are_scanned(tmp_path, monkeypatch):
    # src/ 以外构建也会读的位置：vite.config、index.html、tsconfig 链、.vue 的 <style>
    project = tmp_path / "frontend" / "react-neko-chat"
    (project / "src").mkdir(parents=True)
    (project / "configs").mkdir()
    (tmp_path / "shared").mkdir()
    (project / "vite.config.ts").write_text(
        "import shared from '../../shared/vite-preset'\nexport default shared\n", encoding="utf-8"
    )
    (project / "index.html").write_text('<script src="../../shared/boot.js"></script>\n', encoding="utf-8")
    (project / "tsconfig.json").write_text(
        '{\n  // JSONC comment\n  "files": [],\n  "references": [{ "path": "./tsconfig.app.json" }, { "path": "./configs" }],\n}\n',
        encoding="utf-8",
    )
    (project / "tsconfig.app.json").write_text(
        '{ "extends": "../../shared/tsconfig.base.json", "include": ["src/**/*", "../../shared/types/**/*"] }',
        encoding="utf-8",
    )
    (project / "configs" / "tsconfig.json").write_text('{ "extends": "@tsconfig/node22/tsconfig.json" }', encoding="utf-8")
    # BOM + extends 不带 .json 后缀
    (tmp_path / "shared" / "tsconfig.base.json").write_text('\ufeff{ "extends": "./tsconfig.root" }', encoding="utf-8")
    (tmp_path / "shared" / "tsconfig.root.json").write_text("{}", encoding="utf-8")
    (project / "src" / "App.vue").write_text(
        "<template><p>don't // stop</p></template>\n"
        "<style>.a { background: url(../../../shared/bg.png) }</style>\n"
        '<style src="../../../shared/ext.css"></style>\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    assert sorted(_cross_project_targets("react-neko-chat")) == [
        "shared/bg.png",
        "shared/boot.js",
        "shared/ext.css",
        "shared/tsconfig.base.json",
        "shared/tsconfig.root.json",
        "shared/types/**/*",
        "shared/vite-preset",
    ]
