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
"""Fake clocks must stay inside the module under test.

``some_module.time`` is the stdlib ``time`` module itself, so patching an
attribute on it swaps that function process-wide. A stateful fake — an
iterator of timestamps — then becomes a race with every background thread the
suite leaves running: whoever calls ``time.monotonic()`` first consumes a
value, and the test fails on shifted numbers or ``StopIteration`` far from
anything it was checking. It also hands every other thread a fake clock for
the duration.

``tests/fake_clock.patch_module_clock`` rebinds the module-local name instead.
"""

import ast
import threading
import time
import types
from pathlib import Path

import pytest

from tests.fake_clock import patch_module_clock

TESTS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TESTS_ROOT.parent
_SKIP_DIRS = {".venv", "node_modules", ".git", "dist", "build", "__pycache__"}


def _test_roots() -> list[Path]:
    """Every test tree in the repo, discovered rather than listed.

    `tests/` is not the only one: `plugin/tests/` has its own pytest.ini and
    each bundled plugin can carry `plugin/plugins/<name>/tests/`. They all run
    in a Python process, so a process-wide fake clock leaks the same way there.
    A hardcoded pair would quietly stop covering the next tree someone adds.
    """
    roots = []
    for candidate in REPO_ROOT.rglob("tests"):
        if not candidate.is_dir():
            continue
        if any(part in _SKIP_DIRS for part in candidate.parts):
            continue
        # 已被更外层的 root 覆盖就不重复扫
        if any(candidate != other and other in candidate.parents for other in roots):
            continue
        roots.append(candidate)
    return roots


@pytest.mark.unit
def test_scoped_clock_is_invisible_to_other_threads(monkeypatch):
    """Another thread reading the clock must not consume the fake's values."""
    module = types.ModuleType("_clock_isolation_probe")
    module.time = time

    stamps = iter([10.0, 20.0, 30.0])
    patch_module_clock(monkeypatch, module, monotonic=lambda: next(stamps))

    # 全局时钟必须原样：别的线程读到的仍是真实时间。
    assert time.monotonic is not module.time.monotonic
    observed: list[float] = []
    thief = threading.Thread(target=lambda: observed.extend(time.monotonic() for _ in range(50)))
    thief.start()
    thief.join(timeout=5)
    assert not thief.is_alive()
    assert len(observed) == 50

    # 迭代器一个值都没被偷走。
    assert module.time.monotonic() == 10.0
    assert module.time.monotonic() == 20.0
    # 没被覆盖的属性照旧走真实 time。
    assert module.time.perf_counter is time.perf_counter
    assert isinstance(module.time.time(), float)


def _stdlib_time_names(tree: ast.AST) -> set[str]:
    """Names bound to the stdlib time module in this file.

    ``import time`` is not the only spelling — ``import time as real_time``
    appears in this repo already, and patching through the alias hits the very
    same shared module. Resolve the bindings instead of matching the literal
    word "time".
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "time":
                    names.add(alias.asname or alias.name)
    return names


def _may_mutate_on_call(replacement: ast.expr) -> bool:
    """Could invoking this fake change what the next call observes?

    That is the property that makes a process-wide clock patch dangerous: if
    another thread's call consumes or advances something, the test under way
    sees different values than it set up.

    Decidable without reading semantics:

    * a lambda whose body contains no call can only read (constants, name
      lookups, subscripts, arithmetic) — a concurrent caller cannot disturb it;
    * a lambda that *does* call something (``next(it)``, ``queue.pop()``) can;
    * a bare name or attribute is some function defined elsewhere, whose body
      this scan cannot see — ``_ticking_time`` incrementing a counter is
      exactly that shape, so treat it as unsafe rather than guess.
    """
    if isinstance(replacement, ast.Lambda):
        return any(isinstance(inner, ast.Call) for inner in ast.walk(replacement.body))
    return True


@pytest.mark.unit
def test_no_test_installs_a_mutating_fake_on_the_stdlib_time_module():
    """Discovered from the AST, not from a list of known offenders.

    Pure-read fakes (``lambda: 123.456``, ``lambda: clock["now"]``) are left
    alone: they are still process-wide, which is untidy, but no concurrent
    caller can desync them, and there are ~50 of them. Narrowing those is a
    separate sweep with no bug behind it.
    """
    offenders = []
    scanned = 0

    for root in _test_roots():
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - a broken test file fails elsewhere
                continue
            scanned += 1
            time_names = _stdlib_time_names(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, "attr", None) != "setattr":
                    continue

                # pytest 的两种写法都要认：
                #   setattr(mod.time, "monotonic", fake)        —— 属性形态
                #   setattr("pkg.mod.time.monotonic", fake)     —— 点号字符串形态
                if len(node.args) >= 3:
                    target = node.args[0]
                    # `mod.time`、裸 `time`、以及 `import time as real_time` 的
                    # 别名，指向的都是同一个 stdlib 模块
                    hits_stdlib_time = (
                        (isinstance(target, ast.Attribute) and target.attr == "time")
                        or (isinstance(target, ast.Name) and target.id in time_names)
                    )
                    if not hits_stdlib_time:
                        continue
                    replacement = node.args[2]
                elif len(node.args) == 2 and isinstance(node.args[0], ast.Constant):
                    dotted = node.args[0].value
                    if not isinstance(dotted, str) or ".time." not in f"{dotted}.":
                        continue
                    replacement = node.args[1]
                else:
                    continue

                if _may_mutate_on_call(replacement):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}"
                    )

    assert scanned > 50, f"扫描面太小，断言已失效（只扫到 {scanned} 个文件）"
    assert not offenders, (
        "这些地方把「调用一次就会改变下次观测」的假时钟装到了 stdlib time 模块上，"
        f"任何后台线程调一次就会打乱它：{offenders}。改用 "
        "tests.fake_clock.patch_module_clock(monkeypatch, <module>, monotonic=...)"
    )
