#!/usr/bin/env python3
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

"""Static check: code in this repository must not set a model ``temperature``.

Policy
------
Code in this repository does not choose a sampling temperature for a model
call. Requests go out without the field and each provider applies its own
default. The exceptions, each with its reason, are the entries of
``EXEMPTIONS`` (for example voice sessions whose APIs take a temperature).
Reasons:

* reasoning models (o1 / o3 / gpt-5-thinking, Claude extended thinking) reject
  the field;
* per-call-site values (0.1, 0.3, 0.7, 1.1, ...) drifted between features and
  made regressions hard to reproduce.

What is reported (``NO_TEMPERATURE``)
-------------------------------------
1. a keyword argument named ``temperature`` in any call or class statement
   (``create_chat_llm(..., temperature=0.3)``, ``dict(temperature=0)``);
2. a function or lambda parameter named ``temperature``;
3. a ``"temperature"`` key in a dict display or comprehension
   (``{"temperature": 0.3}``, ``{**base, "temperature": t}``);
4. a store to a ``"temperature"`` subscript (``body["temperature"] = t``,
   augmented and annotated assignment included; a bare annotation without a
   value stores nothing and is not reported);
5. a store to a ``.temperature`` attribute (``client.temperature = t``);
6. a class-body field named ``temperature`` (dataclass / pydantic / TypedDict),
   including one bound inside an ``if`` / ``try`` / ``with`` / ``match`` / loop
   block of the class body (method bodies are not class scope);
7. a call that sets the key by name: ``d.setdefault("temperature", t)``,
   ``setattr(obj, "temperature", t)``, ``operator.setitem(d, "temperature", t)``,
   ``dict.fromkeys([..., "temperature"], t)``, and a ``("temperature", t)``
   pair handed to ``dict(...)`` / ``OrderedDict(...)`` / ``.update(...)``;
   the key or pair may sit in a list / tuple / set display or be produced by
   a list / set comprehension or generator
   (``dict(("temperature", v) for v in values)``).

Reading, testing for or removing the field is not reported:
``"temperature" in body``, ``body.get("temperature")``,
``body.pop("temperature", None)``, ``del body["temperature"]`` and constant
key lists such as ``("temperature", "top_p")``.

Known boundaries: keys built at runtime (concatenation, f-strings, variables
holding the name), JSON text inside string literals, and non-Python files are
not inspected.

Scope and exemptions
--------------------
The default scope is the whole repository. For the repository root the file
list comes from git: tracked files plus untracked ones that are not ignored,
so gitignored local code (plugins installed but not committed, nested
checkouts) is not scanned. Without git, or for any other path, folders are
walked. Either way virtualenvs, dependency and build folders, caches and
``.claude`` (local worktrees) are skipped. The only exemptions
are the entries of ``EXEMPTIONS``, each with its reason. A run over the
repository root (no arguments, ``.`` or its absolute path) also reports an
exemption that no longer suppresses anything, so the list cannot go stale.
Files are decoded per their source-encoding declaration (UTF-8 otherwise).

Output
------
Every violation prints as ``path:line:col  NO_TEMPERATURE  message``. Exit
status is 1 when anything is reported, 0 otherwise.

Usage:
    python scripts/check_no_temperature.py [paths...]
"""
from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import tokenize
import warnings
from pathlib import Path
from typing import Callable, Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent

# The whole repository. A package added later is covered without registering it.
DEFAULT_PATHS: list[str] = ["."]

# Folder names never descended into, at any depth.
EXCLUDE_DIRS = frozenset(
    {
        ".git",
        ".claude",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
        "site-packages",
    }
)

# Repository-relative POSIX path (a file, or a folder covering everything under
# it) -> why that code may mention ``temperature`` the ways listed above.
EXEMPTIONS: dict[str, str] = {
    "main_logic/omni_realtime_client/_transport.py": (
        "Voice: the GLM and Qwen realtime voice sessions accept a session "
        "temperature, and these values are part of the voice session setup."
        " Whole-file exemption: any other temperature added to this file is not "
        "reported, so keep this file to voice requests."
    ),
    "main_logic/omni_realtime_client/_gemini_support.py": (
        "Voice: the Gemini Live session accepts a temperature in its "
        "generation_config, and this value is part of the voice session setup."
        " Whole-file exemption: any other temperature added to this file is not "
        "reported, so keep this file to voice requests."
    ),
    "main_logic/asr_client/workers/gemini.py": (
        "Voice: Gemini speech recognition accepts a temperature, and 0 is part "
        "of the transcription request setup."
        " Whole-file exemption: any other temperature added to this file is not "
        "reported, so keep this file to voice requests."
    ),
    **{
        f"plugin/tests/unit/server/{name}.py": (
            "Tests that a temperature a plugin sends, or one saved in a model slot or "
            "sent by an older plugin manager, is accepted, dropped with a warning and "
            "never reaches the upstream request; and that one in provider settings is "
            "never merged into it."
        )
        for name in (
            "test_host_model_slots",
            "test_model_execution",
            "test_model_config_routes",
            "test_model_config_service",
            "test_model_gateway_anthropic",
            "test_model_gateway_request",
            "test_model_gateway_routes",
            "test_model_gateway_service",
        )
    },
    "tests/unit/theater_workshop/test_sdk_model_drops_temperature.py": (
        "Tests that the workshop SDK drops a temperature that a generator written "
        "for SDK 0.1.0 still passes, before the model call."
    ),
    "plugin/plugins/lifekit": (
        "Weather plugin: 'temperature' is the air temperature in forecast data; "
        "the plugin makes no model calls."
    ),
    "plugin/tests/unit/plugins/test_lifekit_config.py": (
        "Tests of the weather plugin above: forecast data with an air "
        "temperature series."
    ),
}

CODE = "NO_TEMPERATURE"
STALE_CODE = "NO_TEMPERATURE_STALE_EXEMPTION"
FIELD = "temperature"

_POLICY = (
    "Repository code must not set a model temperature; remove it so the "
    "provider default applies (see .agent/rules/neko-guide.md)."
)

_DICT_FACTORIES = frozenset({"dict", "OrderedDict"})


def _is_field_name(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == FIELD


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


_DISPLAYS = (ast.List, ast.Tuple, ast.Set)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.GeneratorExp)


def _is_field_pair(node: ast.expr) -> bool:
    return isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) == 2 and _is_field_name(node.elts[0])


def _holds(node: ast.expr, matches: Callable[[ast.expr], bool]) -> bool:
    """A list / tuple / set display with an item that ``matches``, or a
    list / set comprehension or generator whose produced element does."""
    if isinstance(node, _DISPLAYS):
        return any(matches(item) for item in node.elts)
    if isinstance(node, _COMPREHENSIONS):
        return matches(node.elt)
    return False


def _holds_field_pair(node: ast.expr) -> bool:
    """Holds / produces a ``("temperature", value)`` pair."""
    return _holds(node, _is_field_pair)


def _holds_field_name(node: ast.expr) -> bool:
    """Holds / produces the constant ``"temperature"``."""
    return _holds(node, _is_field_name)


def _call_violation(node: ast.Call) -> str | None:
    name = _call_name(node.func)
    args = node.args
    if name == "setdefault" and args and _is_field_name(args[0]):
        return "`setdefault('temperature', ...)`"
    if name in ("setattr", "setitem") and len(args) >= 2 and _is_field_name(args[1]):
        return f"`{name}(..., 'temperature', ...)`"
    if name == "fromkeys" and args and _holds_field_name(args[0]):
        return "`dict.fromkeys([... 'temperature' ...])`"
    builds_dict = name in _DICT_FACTORIES or (name == "update" and isinstance(node.func, ast.Attribute))
    if builds_dict and any(_holds_field_pair(arg) for arg in args):
        return "`('temperature', value)` pair passed to a dict builder"
    return None


_OWN_SCOPE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _class_scope_statements(node: ast.ClassDef) -> Iterator[ast.stmt]:
    """Statements that run in the class's own scope: its body plus the blocks
    of ``if`` / ``for`` / ``while`` / ``with`` / ``try`` / ``match`` inside it,
    but not the bodies of nested functions or classes (scopes of their own)."""
    stack: list[ast.stmt] = list(node.body)
    while stack:
        stmt = stack.pop()
        yield stmt
        if isinstance(stmt, _OWN_SCOPE):
            continue
        for attr in ("body", "orelse", "finalbody"):
            stack.extend(getattr(stmt, attr, None) or [])
        for handler in getattr(stmt, "handlers", None) or []:
            stack.extend(handler.body)
        for case in getattr(stmt, "cases", None) or []:
            stack.extend(case.body)


def _target_names(target: ast.expr) -> Iterator[ast.Name]:
    if isinstance(target, ast.Name):
        yield target
    elif isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            yield from _target_names(item)
    elif isinstance(target, ast.Starred):
        yield from _target_names(target.value)


def _class_field_targets(node: ast.ClassDef) -> Iterator[ast.Name]:
    for stmt in _class_scope_statements(node):
        if isinstance(stmt, ast.Assign):
            targets: list[ast.expr] = stmt.targets
        elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor)):
            targets = [stmt.target]
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            targets = [item.optional_vars for item in stmt.items if item.optional_vars is not None]
        else:
            continue
        for target in targets:
            for name in _target_names(target):
                if name.id == FIELD:
                    yield name


def check_tree(tree: ast.AST) -> list[tuple[int, int, str]]:
    """Every place in ``tree`` that sets a temperature, as (line, col, message).

    Walks the tree iteratively (``ast.walk``), so deeply nested code such as a
    long ``elif`` chain cannot hit the recursion limit.
    """
    found: list[tuple[int, int, str]] = []

    def report(node: ast.AST, what: str) -> None:
        found.append((getattr(node, "lineno", 0), getattr(node, "col_offset", 0), f"{what}. {_POLICY}"))

    # A bare annotation (``body["temperature"]: float``) stores nothing.
    bare_annotations = {
        id(node.target) for node in ast.walk(tree) if isinstance(node, ast.AnnAssign) and node.value is None
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword):  # 1. calls and class statements alike
            if node.arg == FIELD:
                report(node.value, "keyword argument `temperature=`")
        elif isinstance(node, ast.arg):  # 2.
            if node.arg == FIELD:
                report(node, "parameter named `temperature`")
        elif isinstance(node, ast.Dict):  # 3.
            for key in node.keys:
                if _is_field_name(key):
                    report(key, "dict key `'temperature'`")
        elif isinstance(node, ast.DictComp):  # 3., comprehension form
            if _is_field_name(node.key):
                report(node.key, "dict key `'temperature'`")
        elif isinstance(node, ast.Subscript):  # 4.
            if isinstance(node.ctx, ast.Store) and _is_field_name(node.slice) and id(node) not in bare_annotations:
                report(node, "assignment to `[...]['temperature']`")
        elif isinstance(node, ast.Attribute):  # 5.
            if isinstance(node.ctx, ast.Store) and node.attr == FIELD and id(node) not in bare_annotations:
                report(node, "assignment to `.temperature`")
        elif isinstance(node, ast.ClassDef):  # 6.
            for target in _class_field_targets(node):
                report(target, "class field named `temperature`")
        elif isinstance(node, ast.Call):  # 7.
            what = _call_violation(node)
            if what is not None:
                report(node, what)
    return sorted(found)


def check_source(source: str, filename: str = "<string>") -> list[tuple[int, int, str]]:
    return check_tree(ast.parse(source, filename=filename))


def _repo_relative(path: Path) -> str | None:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _is_repo_root(path: Path) -> bool:
    try:
        return path.resolve() == REPO_ROOT.resolve()
    except OSError:
        return False


def _exemption_for(rel: str | None) -> str | None:
    if rel is None:
        return None
    for key in EXEMPTIONS:
        if rel == key or rel.startswith(key + "/"):
            return key
    return None


# Variables that point git at a repository other than the one ``cwd`` is in;
# they are set inside git hooks, for example. Dropped for the calls below.
GIT_LOCATION_VARS = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_PREFIX",
    }
)


def _git_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in GIT_LOCATION_VARS}


def _git_listed_python_files(root: Path) -> list[Path] | None:
    """Python files git tracks in ``root`` plus untracked ones it does not
    ignore, or None when ``root`` is not the top of a git work tree or git is
    unavailable.

    Gitignored code (plugins installed locally but not committed, nested
    checkouts) is not repository code and is left out; a new file is still
    seen before it is ever ``git add``-ed.
    """
    # Output is read as bytes: git prints paths as UTF-8, which a Windows locale
    # code page (GBK, cp1252, ...) would mis-decode for a non-ASCII checkout path.
    env = _git_env()
    try:
        # Empty when ``root`` is the top of the work tree; no path comparison,
        # so the checkout's own path never needs decoding. It is also empty
        # inside a ``.git`` folder or a bare repository, where the ls-files
        # call below fails and the caller falls back to walking folders.
        cdup = subprocess.run(
            ["git", "rev-parse", "--show-cdup"],
            cwd=root, env=env, capture_output=True, timeout=60, check=False,
        )
        if cdup.returncode != 0 or cdup.stdout.strip():
            return None
        # No pathspec: GIT_LITERAL_PATHSPECS / GIT_GLOB_PATHSPECS would change
        # what one matches. Python picks the .py files instead.
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root, env=env, capture_output=True, timeout=120, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listed.returncode != 0:
        return None
    files: set[Path] = set()
    for raw in listed.stdout.split(b"\0"):
        if not raw.endswith(b".py"):
            continue
        rel = raw.decode("utf-8", "surrogateescape")
        if EXCLUDE_DIRS.intersection(rel.split("/")[:-1]):
            continue
        path = root / rel
        try:
            exists = path.is_file()  # a tracked file may be deleted in the work tree
        except OSError as e:  # e.g. a folder this user may not enter
            print(f"{path}: skipped — {e}", file=sys.stderr)
            continue
        if exists:
            files.add(path)
    return sorted(files)


def _report_unreadable_folder(error: OSError) -> None:
    # os.walk skips a folder it cannot list without a word; say so, as for files.
    print(f"{error.filename}: skipped folder — {error}", file=sys.stderr)


def _iter_python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for root in paths:
        if root.is_file():
            if root.suffix == ".py":
                yield root
            continue
        if not root.is_dir():
            continue
        if _is_repo_root(root):
            listed = _git_listed_python_files(root)
            if listed is not None:
                yield from listed
                continue
        for dirpath, dirnames, filenames in os.walk(root, onerror=_report_unreadable_folder):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
            for filename in sorted(filenames):
                if filename.endswith(".py"):
                    yield Path(dirpath) / filename


def _parse_file(path: Path) -> ast.Module | None:
    try:
        # Decode the way Python does: the file's coding declaration, else UTF-8.
        # An unknown declared encoding raises SyntaxError here.
        with tokenize.open(path) as fh:
            source = fh.read()
    except (OSError, UnicodeDecodeError, SyntaxError) as e:
        print(f"{path}: skipped — {e}", file=sys.stderr)
        return None
    try:
        # Other files' own warnings (e.g. invalid escape sequences) are not
        # this check's business.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(source, filename=str(path))
    except SyntaxError as e:
        print(f"{path}:{e.lineno}: syntax error — {e.msg}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forbid repository code from setting a model temperature."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files/directories to scan (default: the whole repository).",
    )
    args = parser.parse_args(argv)

    raw_paths = args.paths or DEFAULT_PATHS
    targets = [Path(p) if Path(p).is_absolute() else REPO_ROOT / p for p in raw_paths]
    # Stale exemptions can only be judged when the repository root itself is
    # scanned (no arguments, ".", or its absolute path).
    whole_repo = any(_is_repo_root(target) for target in targets)

    total = 0
    used_exemptions: set[str] = set()
    for file in _iter_python_files(targets):
        tree = _parse_file(file)
        if tree is None:
            continue
        violations = check_tree(tree)
        if not violations:
            continue
        rel = _repo_relative(file)
        exemption = _exemption_for(rel)
        if exemption is not None:
            used_exemptions.add(exemption)
            continue
        shown = rel if rel is not None else str(file)
        for lineno, col, msg in violations:
            print(f"{shown}:{lineno}:{col}  {CODE}  {msg}")
            total += 1

    if whole_repo:
        for key in sorted(set(EXEMPTIONS) - used_exemptions):
            print(
                f"{key}  {STALE_CODE}  this exemption no longer suppresses anything; "
                "delete it from EXEMPTIONS in scripts/check_no_temperature.py."
            )
            total += 1

    if total:
        print(
            f"\n{total} problem(s) found.\n"
            "Project policy: repository code does not set a model temperature. "
            "Remove the keyword, parameter, dict key or field so the request goes "
            "out without it and the provider's own default applies. An exception "
            "needs an entry with its reason in EXEMPTIONS in "
            "scripts/check_no_temperature.py.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
