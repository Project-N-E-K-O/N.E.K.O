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
"""Unit tests for ``scripts/check_async_blocking.py``'s atomic-write rule.

Synthetic-source coverage of the three things that rule has to get right:
it must catch a synchronous ``atomic_write_*`` reachable from a coroutine
(directly, or through one named sync helper); it must NOT fire on helpers
whose name is too generic to identify by name alone; and one call site
must be reported once, not once per matching rule.
"""
from __future__ import annotations

import ast
import importlib.util
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check_async_blocking.py"

pytestmark = pytest.mark.unit


def _load_script_module():
    spec = importlib.util.spec_from_file_location("check_async_blocking", SCRIPT_PATH)
    assert spec and spec.loader, f"failed to load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load_script_module()


def _violations(source: str) -> list[tuple[int, int, str]]:
    """Run both passes over one synthetic module, as ``main()`` would."""
    source = textwrap.dedent(source)
    tree = ast.parse(source)
    indexer = MOD.RiskySyncDefIndexer()
    indexer.index_module(tree, Path("synthetic.py"))
    async_names = MOD._collect_async_def_names(tree)
    return list(
        MOD.check_file(
            Path("synthetic.py"),
            source,
            tree,
            risky_helpers=indexer.risky,
            ambiguous_async_names=async_names & set(indexer.risky),
        )
    )


# ── detection ───────────────────────────────────────────────────────────


def test_a_direct_atomic_write_in_a_coroutine_is_flagged():
    out = _violations('''
    async def handler(payload):
        atomic_write_json(path, payload)
    ''')
    assert len(out) == 1
    assert "atomic_write_json" in out[0][2]


def test_atomic_write_text_is_flagged_too():
    out = _violations('''
    async def handler(payload):
        atomic_write_text(path, payload)
    ''')
    assert len(out) == 1
    assert "atomic_write_text" in out[0][2]


def test_a_named_sync_helper_that_writes_is_flagged_through_one_hop():
    # 这是绝大多数真实调用点的形状：协程调一个同步的 save_xxx，落盘藏在
    # helper 体内。
    out = _violations('''
    def save_storage_policy(policy):
        atomic_write_json(path, policy)

    async def handler(policy):
        save_storage_policy(policy)
    ''')
    assert len(out) == 1
    assert "save_storage_policy" in out[0][2]


def test_the_offloaded_form_is_not_flagged():
    out = _violations('''
    def save_storage_policy(policy):
        atomic_write_json(path, policy)

    async def handler(policy):
        await asyncio.to_thread(save_storage_policy, policy)
    ''')
    assert out == []


def test_a_sync_caller_is_not_flagged():
    # 规则只管事件循环上的调用；同步上下文里落盘本来就没问题。
    out = _violations('''
    def save_storage_policy(policy):
        atomic_write_json(path, policy)

    def sync_caller(policy):
        save_storage_policy(policy)
    ''')
    assert out == []


# ── 通用名去噪 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("generic", ["save", "load", "write", "flush", "update"])
def test_a_generic_helper_name_is_not_indexed(generic):
    # 索引是按名字匹配的，`save` 这种通用动词会撞上 PIL 的 img.save() 和
    # TokenTracker.save() 之类毫无关系的东西。宁可漏报也不制造假阳性 ——
    # 与本文件对 queue/thread/socket 尾名的既有取舍同一条原则。
    out = _violations(f'''
    def {generic}(payload):
        atomic_write_json(path, payload)

    async def handler(payload):
        obj.{generic}(payload)
    ''')
    assert out == []


def test_the_denylist_is_about_the_helper_name_not_the_write():
    # 反证：同一个 helper 体，换成一个够独特的名字就必须被抓到。这条保证
    # 上面那组绿不是因为规则整个失效了。
    out = _violations('''
    def save_workshop_config(payload):
        atomic_write_json(path, payload)

    async def handler(payload):
        cm.save_workshop_config(payload)
    ''')
    assert len(out) == 1


# ── 报告形状 ────────────────────────────────────────────────────────────


def test_one_call_site_is_reported_once():
    # `atomic_write_json` 既是已知阻塞调用，本身又是一个体内含
    # atomic_write_text 的同步 def —— 直接规则和传递规则会双双命中同一处。
    out = _violations('''
    def atomic_write_json(path, data):
        atomic_write_text(path, dumps(data))

    async def handler(payload):
        atomic_write_json(path, payload)
    ''')
    positions = [(lineno, col) for lineno, col, _ in out]
    assert len(positions) == len(set(positions)) == 1


def test_noqa_suppresses_the_call_site():
    out = _violations('''
    async def handler(payload):
        atomic_write_json(path, payload)  # noqa: ASYNC_BLOCK — shutdown flush
    ''')
    assert out == []


# ── 真实仓库：这条规则必须是绿的 ─────────────────────────────────────────


def test_the_repo_itself_has_no_on_loop_atomic_writes():
    """The whole point of the rule: keep the tree clean going forward."""
    assert MOD.main([]) == 0
