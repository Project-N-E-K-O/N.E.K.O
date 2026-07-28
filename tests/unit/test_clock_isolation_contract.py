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


@pytest.mark.unit
def test_no_test_installs_a_stateful_fake_on_the_stdlib_time_module():
    """Discovered from the AST, not from a list of known offenders.

    Only the stateful shape is rejected. Constant fakes
    (``lambda: 123.456``) are still process-wide but cannot be desynced by a
    concurrent reader, and there are ~60 of them; converting those is a
    separate sweep.
    """
    offenders = []
    scanned = 0

    for path in sorted(TESTS_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken test file fails elsewhere
            continue
        scanned += 1
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", None) != "setattr" or len(node.args) < 3:
                continue
            target = node.args[0]
            # 形如 `<something>.time` —— 那就是 stdlib 模块本身
            if not (isinstance(target, ast.Attribute) and target.attr == "time"):
                continue
            replacement = node.args[2]
            stateful = any(
                isinstance(inner, ast.Call) and getattr(inner.func, "id", None) == "next"
                for inner in ast.walk(replacement)
            )
            if stateful:
                offenders.append(f"{path.relative_to(TESTS_ROOT).as_posix()}:{node.lineno}")

    assert scanned > 50, f"扫描面太小，断言已失效（只扫到 {scanned} 个文件）"
    assert not offenders, (
        "这些地方把有状态假时钟装到了 stdlib time 模块上，任何后台线程调一次"
        f" time.monotonic() 就会偷走一个值：{offenders}。改用 "
        "tests.fake_clock.patch_module_clock(monkeypatch, <module>, monotonic=...)"
    )
