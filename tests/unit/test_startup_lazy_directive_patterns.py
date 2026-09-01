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

"""封禁话题模板必须惰性编译，且预热表要真的把它编出来。

这两条守的是同一件事的两头。``config/prompts/prompts_directives`` 里那 21 条
模板有 4 条各约 51 KB 正则源码，编译实测 294-298 ms；而这个模块坐在
memory_server 的 eager 导入链上（``app/__init__.py`` -> ``app/runtime_bindings``
-> ``memory.user_directives``），memory_server 又是 merged 模式下第一个被 import
的 app 模块。uvicorn 先 ``await lifespan.startup()`` 再 ``create_server()``，所以
这段时间花在**端口还不存在**的阶段——用户那边是 connection-refused。

把编译挪到首次访问，代价就转嫁给第一次指令抽取了；所以第二条守卫盯预热：
``MAIN_SERVER_WARMUP`` 里那条 ``"模块:属性"`` 必须真的被求值，否则这个改动只是
把 300 ms 从启动搬到了用户第一句话上，而且搬得悄无声息。
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
    """import 这个模块不许触发那 294 ms。

    起子进程来问，不在本进程里判：模块级状态跨用例存活，同一个 pytest session
    里只要有任何一条用例先碰过 ``DIRECTIVE_PATTERNS``，缓存就已经填好了，本进程
    里的断言会永远为真——那就是一条永远绿的假守卫。
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
    """预热表里必须有这一条，否则第一次指令抽取要现编 294 ms。"""
    from utils.module_warmup import MAIN_SERVER_WARMUP

    assert _WARMUP_ENTRY in MAIN_SERVER_WARMUP, (
        "DIRECTIVE_PATTERNS 改成惰性之后没登记预热：启动是快了，代价原样落到"
        "用户第一句话上"
    )


@pytest.mark.unit
def test_warmup_touches_the_attribute_not_just_the_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``"模块:属性"`` 这种条目必须真的取一次属性。

    只 import 是不够的，而且恰恰对这条毫无作用：被预热的模块此刻早就在
    ``sys.modules`` 里了（memory_server 的导入链把它拉进来的），
    ``import_module`` 直接命中缓存返回，一行代码都不会执行——贵的是那次属性求值。

    变异：把 ``_warm_one`` 里的 ``getattr`` 去掉，这条必须红。
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
