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
"""Every node-driving test must go through tests/node_harness.

Hand-rolled ``subprocess.run`` calls to node have broken this suite twice, both
times in a way that hides what actually went wrong:

* ``node -e <script>`` blows past Windows' 32767-character command line and
  raises ``WinError 206`` before node starts, so no assertion in the test runs.
* ``text=True`` without ``encoding`` encodes stdin with the host locale, so a
  harness carrying CJK passes on a UTF-8-configured machine and dies with
  ``UnicodeEncodeError`` on a stock English Windows — i.e. on every CI runner.

Both are invisible locally to whoever writes the harness.  The shared launcher
pins the temp-file form and UTF-8, so this test keeps new harnesses on it
rather than re-deriving the raw call.

Discovered by walking the AST, not from a hand-maintained file list: a list is
exactly what a new harness file would slip past.
"""

import ast
import re
import tomllib
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TESTS_ROOT.parent
# The launcher itself is the one place allowed to call subprocess.run on node.
EXEMPT = {TESTS_ROOT / "node_harness.py"}


def _mentions_node(call: ast.Call) -> bool:
    """True when this subprocess.run call is driving node."""
    for node in ast.walk(call):
        if isinstance(node, ast.Name) and "node" in node.id.lower():
            return True
        if isinstance(node, ast.Attribute) and "node" in node.attr.lower():
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.lower() in {"node", "node.exe"} or node.value.lower().endswith("/node"):
                return True
    return False


# 全部 subprocess 入口，不只是 run：漏一个（比如 check_call）就等于给新
# harness 留了一条绕过这条契约、退回 node -e 的合法路径（Codex P2）。
_ENTRY_POINTS = frozenset({"run", "Popen", "check_output", "check_call", "call"})


def _subprocess_bindings(tree: ast.AST) -> tuple[set[str], set[str]]:
    """本文件里哪些名字指向 subprocess。

    模块名不一定叫 subprocess：``import subprocess as sp`` 与
    ``from subprocess import run`` 都是常见写法，只认字面量 "subprocess."
    等于把这两条路留给新 harness 当后门（Codex P2）。
    """
    module_aliases = {"subprocess"}
    direct_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in _ENTRY_POINTS:
                    direct_names.add(alias.asname or alias.name)
    return module_aliases, direct_names


def _subprocess_run_calls(tree: ast.AST):
    module_aliases, direct_names = _subprocess_bindings(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if (
                func.attr in _ENTRY_POINTS
                and getattr(func.value, "id", None) in module_aliases
            ):
                yield node
        elif isinstance(func, ast.Name) and func.id in direct_names:
            yield node


def test_node_harnesses_go_through_the_shared_launcher():
    offenders = []
    scanned = 0
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path in EXEMPT:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken test file fails elsewhere
            continue
        scanned += 1
        for call in _subprocess_run_calls(tree):
            if _mentions_node(call):
                offenders.append(f"{path.relative_to(TESTS_ROOT).as_posix()}:{call.lineno}")

    assert scanned > 50, f"扫描面太小，断言已失效（只扫到 {scanned} 个文件）"
    assert not offenders, (
        "这些地方直接用 subprocess 跑 node，绕开了 tests/node_harness 的"
        f"命令行长度与 UTF-8 兜底：{offenders}"
    )


def test_unit_tests_workflow_pins_locked_pyclipper():
    """The workflow's standalone pyclipper install must track uv.lock.

    It is installed outside ``uv sync`` because the group carrying it also
    carries opencv, so nothing else keeps the two in step: an index update
    could otherwise hand an unchanged commit a different release and turn the
    workflow red. Pinning without this check just moves the drift somewhere
    nobody looks.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "unit-tests.yml").read_text(
        encoding="utf-8"
    )
    pinned = re.search(r"uv pip install pyclipper==([\w.]+)", workflow)
    assert pinned, "unit-tests.yml 里的 pyclipper 安装必须钉版本"

    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = [p["version"] for p in lock["package"] if p["name"] == "pyclipper"]
    assert locked, "uv.lock 里找不到 pyclipper，断言已失效"
    assert pinned.group(1) == locked[0], (
        f"workflow 钉的是 {pinned.group(1)}，uv.lock 解析的是 {locked[0]}"
    )


@pytest.mark.parametrize("runner", ["run_node_script", "run_node_stdin"])
def test_shared_launcher_pins_utf8(runner):
    """Both runners must pin the encoding rather than inherit the locale."""
    source = (TESTS_ROOT / "node_harness.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == runner
    )
    body = ast.get_source_segment(source, func) or ""
    assert "_utf8(kwargs)" in body, f"{runner} 必须把 kwargs 过一遍 _utf8()"

    helper = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_utf8"
    )
    helper_body = ast.get_source_segment(source, helper) or ""
    assert '"encoding"] = "utf-8"' in helper_body, "_utf8 必须强制 encoding，而不是 setdefault"
