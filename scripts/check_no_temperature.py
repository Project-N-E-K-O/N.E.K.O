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
No code in this repository chooses a sampling temperature for a model call.
Requests go out without the field and each provider applies its own default.
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
6. a class-body field named ``temperature`` (dataclass / pydantic / TypedDict);
7. a call that sets the key by name: ``d.setdefault("temperature", t)``,
   ``setattr(obj, "temperature", t)``, ``operator.setitem(d, "temperature", t)``,
   ``dict.fromkeys([..., "temperature"], t)``, and a ``("temperature", t)``
   pair handed to ``dict(...)`` / ``OrderedDict(...)`` / ``.update(...)``.

Reading, testing for or removing the field is not reported:
``"temperature" in body``, ``body.get("temperature")``,
``body.pop("temperature", None)``, ``del body["temperature"]`` and constant
key lists such as ``("temperature", "top_p")``.

Known boundaries: keys built at runtime (concatenation, f-strings, variables
holding the name), JSON text inside string literals, and non-Python files are
not inspected.

Scope and exemptions
--------------------
The default scope is the whole repository, minus virtualenvs, dependency and
build folders, caches and ``.claude`` (local worktrees). The only exemptions
are the entries of ``EXEMPTIONS``, each with its reason. A run over the
default scope also reports an exemption that no longer suppresses anything,
so the list cannot go stale.

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
import sys
import warnings
from pathlib import Path
from typing import Iterable, Iterator

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
    "plugin/server/domain/model_config.py": (
        "Plugin model slot schema: the optional default temperature a user "
        "enters for a slot in the plugin manager. Part of the plugin model API; "
        "changing it needs an owner decision."
    ),
    "plugin/server/model_gateway/request.py": (
        "Plugin model gateway: fills in the user-entered slot default when the "
        "plugin's own request has no temperature. Part of the plugin model API; "
        "changing it needs an owner decision."
    ),
    **{
        f"plugin/tests/unit/server/{name}.py": (
            "Test of the plugin model gateway / slot configuration above, which "
            "accept and validate a temperature chosen by a plugin or by the user."
        )
        for name in (
            "test_host_model_slots",
            "test_model_config_routes",
            "test_model_config_service",
            "test_model_gateway_anthropic",
            "test_model_gateway_request",
            "test_model_gateway_routes",
            "test_model_gateway_service",
        )
    },
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


def _holds_field_pair(node: ast.expr) -> bool:
    """A list / tuple / set display holding a ``("temperature", value)`` pair."""
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return False
    return any(
        isinstance(item, (ast.Tuple, ast.List))
        and len(item.elts) == 2
        and _is_field_name(item.elts[0])
        for item in node.elts
    )


def _holds_field_name(node: ast.expr) -> bool:
    """A list / tuple / set display holding the constant ``"temperature"``."""
    return isinstance(node, (ast.List, ast.Tuple, ast.Set)) and any(
        _is_field_name(item) for item in node.elts
    )


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


def _class_field_targets(node: ast.ClassDef) -> Iterator[ast.Name]:
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign):
            targets: list[ast.expr] = [stmt.target]
        elif isinstance(stmt, ast.Assign):
            targets = stmt.targets
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == FIELD:
                yield target


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


def _exemption_for(rel: str | None) -> str | None:
    if rel is None:
        return None
    for key in EXEMPTIONS:
        if rel == key or rel.startswith(key + "/"):
            return key
    return None


def _iter_python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for root in paths:
        if root.is_file():
            if root.suffix == ".py":
                yield root
            continue
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
            for filename in sorted(filenames):
                if filename.endswith(".py"):
                    yield Path(dirpath) / filename


def _parse_file(path: Path) -> ast.Module | None:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
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

    whole_repo = not args.paths
    raw_paths = args.paths or DEFAULT_PATHS
    targets = [Path(p) if Path(p).is_absolute() else REPO_ROOT / p for p in raw_paths]

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
            "Project policy: no code in this repository sets a model temperature. "
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
