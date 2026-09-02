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

"""Ban-topic templates must compile lazily, and the warmup must really compile them.

These two guards cover the two ends of one change. Four of the 21 templates in
``config/prompts/prompts_directives`` carry roughly 51 KB of regex source each;
compiling the set measures 294-298 ms. That module sits on memory_server's eager
import chain (``app/__init__.py`` -> ``app/runtime_bindings`` ->
``memory.user_directives``), and memory_server is the first app module imported
in merged mode. uvicorn awaits ``lifespan.startup()`` before ``create_server()``,
so the time is spent while the port does not exist yet -- the user sees
connection-refused, not slowness.

Making the compile lazy on its own would just move the cost onto the first
directive extraction, so the second guard watches the warmup: the
``"module:attribute"`` entry in ``MAIN_SERVER_WARMUP`` has to actually be
evaluated. Otherwise this change silently relocates 300 ms from startup to the
user's first sentence.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_WARMUP_ENTRY = "config.prompts.prompts_directives:DIRECTIVE_PATTERNS"


@pytest.mark.unit
def test_directive_patterns_are_not_compiled_at_import_time() -> None:
    """Importing the module must not trigger the 294 ms compile.

    Asked in a subprocess rather than judged in-process, deliberately.
    Module-level state outlives a test, so if any earlier case in the same
    pytest session has already touched ``DIRECTIVE_PATTERNS`` the cache is
    filled and an in-process assertion would be true forever -- a guard that
    can never fail.
    """
    probe = (
        "import config.prompts.prompts_directives as D;"
        "print('CACHE=%s' % (D._DIRECTIVE_PATTERNS_CACHE is None));"
        "n = len(D.DIRECTIVE_PATTERNS);"
        "print('AFTER=%s' % (D._DIRECTIVE_PATTERNS_CACHE is not None));"
        "print('COUNT=%d' % n);"
        "print('RAW=%d' % len(D._PATTERNS_RAW))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    out = result.stdout

    assert "CACHE=True" in out, (
        "import 完就已经编译好了——那 294 ms 又回到了端口 bind 之前的启动路径上"
    )
    assert "AFTER=True" in out, "取过 DIRECTIVE_PATTERNS 之后缓存仍是空的"

    # 惰性不能顺手改变模板集合：条数必须和原始表一一对应。少一条就是少一条封禁
    # 规则，而那种缺失在功能测试里只表现为"这句没被拦住"，很难归因。
    count = next(line for line in out.splitlines() if line.startswith("COUNT="))
    raw = next(line for line in out.splitlines() if line.startswith("RAW="))
    assert count.split("=")[1] == raw.split("=")[1], (
        f"编译出来的模板数和 _PATTERNS_RAW 对不上：{count} vs {raw}"
    )


@pytest.mark.unit
def test_warmup_entry_for_directive_patterns_is_registered() -> None:
    """The warmup table must carry this entry.

    Without it the first directive extraction compiles the 294 ms of regex
    inline, on a user turn.
    """
    from utils.module_warmup import MAIN_SERVER_WARMUP

    assert _WARMUP_ENTRY in MAIN_SERVER_WARMUP, (
        "DIRECTIVE_PATTERNS 改成惰性之后没登记预热：启动是快了，代价原样落到"
        "用户第一句话上"
    )


@pytest.mark.unit
def test_warmup_touches_the_attribute_not_just_the_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``"module:attribute"`` entry must actually read the attribute.

    Importing alone is not enough, and for this entry it does nothing at all:
    the module is already in ``sys.modules`` by then, dragged in by
    memory_server's import chain, so ``import_module`` is a cache hit that runs
    no code. The expensive part is the attribute evaluation.

    Mutation: drop the ``getattr`` in ``_warm_one``; this must go red.
    """
    from utils import module_warmup

    touched: list[str] = []

    class _Recording(types.ModuleType):
        def __getattr__(self, name: str) -> object:
            touched.append(name)
            return object()

    fake = _Recording("neko_fake_warm_target")
    monkeypatch.setitem(sys.modules, "neko_fake_warm_target", fake)

    module_warmup._warm_one("neko_fake_warm_target:LAZY_THING")
    assert touched == ["LAZY_THING"], (
        f"带属性的预热条目没有真的求值，只是 import 了一遍：touched={touched}"
    )

    # 不带冒号的条目保持原样：只 import，不去乱碰属性。
    touched.clear()
    module_warmup._warm_one("neko_fake_warm_target")
    assert touched == [], f"无属性条目不该访问任何属性，却碰了 {touched}"
