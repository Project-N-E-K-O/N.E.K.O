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


def test_atomic_write_bytes_is_flagged_too():
    out = _violations('''
    async def handler(payload):
        atomic_write_bytes(path, payload)
    ''')
    assert len(out) == 1
    assert "atomic_write_bytes" in out[0][2]


def test_a_named_sync_helper_that_writes_is_flagged_through_one_hop():
    # Most real callers look like this: coroutine invokes synchronous save_xxx, hiding persistence inside
    # the helper body.
    out = _violations('''
    def save_storage_policy(policy):
        atomic_write_json(path, policy)

    async def handler(policy):
        save_storage_policy(policy)
    ''')
    assert len(out) == 1
    assert "save_storage_policy" in out[0][2]


def test_the_module_qualified_form_is_flagged():
    # After `from utils import file_utils`, `file_utils.atomic_write_json(...)` is an
    # attribute call, missed by RISKY_BARE_CALLS (ast.Name only). Ignoring this valid style
    # would create a backdoor for ordinary imports.
    out = _violations("""
    async def handler(payload):
        file_utils.atomic_write_json(path, payload)
    """)
    assert len(out) == 1
    assert "atomic_write_json" in out[0][2]


def test_the_module_qualified_form_is_flagged_through_one_hop():
    out = _violations("""
    def save_storage_policy(policy):
        file_utils.atomic_write_text(path, dumps(policy))

    async def handler(policy):
        save_storage_policy(policy)
    """)
    assert len(out) == 1
    assert "save_storage_policy" in out[0][2]


def test_an_imported_writer_alias_is_flagged():
    out = _violations("""
    from utils.file_utils import atomic_write_json as write_json

    async def handler(payload):
        write_json(path, payload)
    """)
    assert len(out) == 1
    assert "atomic_write_json" in out[0][2]


def test_a_file_utils_module_alias_is_flagged():
    out = _violations("""
    from utils import file_utils as fu

    async def handler(payload):
        fu.atomic_write_text(path, payload)
    """)
    assert len(out) == 1
    assert "atomic_write_text" in out[0][2]


def test_an_imported_alias_is_flagged_through_one_hop():
    out = _violations("""
    from utils.file_utils import atomic_write_json as write_json

    def save_storage_policy(payload):
        write_json(path, payload)

    async def handler(payload):
        save_storage_policy(payload)
    """)
    assert len(out) == 1
    assert "save_storage_policy" in out[0][2]


def test_an_unrelated_import_alias_is_not_flagged():
    out = _violations("""
    from elsewhere import atomic_write_json as write_json

    async def handler(payload):
        write_json(path, payload)
    """)
    assert out == []


def test_an_unrelated_receiver_with_the_same_attr_is_not_flagged():
    # Negative case: recognize (file_utils, atomic_write_*), not method names alone.
    out = _violations("""
    async def handler(payload):
        my_own_writer.atomic_write_json(path, payload)
    """)
    assert out == []


def test_the_offloaded_form_is_not_flagged():
    out = _violations('''
    def save_storage_policy(policy):
        atomic_write_json(path, policy)

    async def handler(policy):
        await asyncio.to_thread(save_storage_policy, policy)
    ''')
    assert out == []


def test_workshop_path_config_read_is_flagged_on_the_event_loop():
    out = _violations("""
    async def handler():
        return get_workshop_path()
    """)
    assert len(out) == 1
    assert "get_workshop_path" in out[0][2]


def test_workshop_path_async_twin_is_allowed():
    out = _violations("""
    async def handler():
        return await get_workshop_path_async()
    """)
    assert out == []


def test_a_sync_caller_is_not_flagged():
    # Rule covers event-loop calls only; persistence in synchronous contexts is fine.
    out = _violations('''
    def save_storage_policy(policy):
        atomic_write_json(path, policy)

    def sync_caller(policy):
        save_storage_policy(policy)
    ''')
    assert out == []


# ── Generic-name noise filtering ─────────────────────────────────────────


@pytest.mark.parametrize("generic", ["save", "load", "write", "flush", "update"])
def test_a_generic_helper_name_is_not_indexed(generic):
    # Name-matched indexes make generic `save` collide with unrelated PIL img.save()
    # and TokenTracker.save(). Prefer missed findings over false positives,
    # consistent with existing queue/thread/socket suffix tradeoffs in this file.
    out = _violations(f'''
    def {generic}(payload):
        atomic_write_json(path, payload)

    async def handler(payload):
        obj.{generic}(payload)
    ''')
    assert out == []


def test_the_denylist_is_about_the_helper_name_not_the_write():
    # Negative case: identical helper body with a sufficiently distinctive name must be detected,
    # proving earlier passing cases do not mean the rule stopped working entirely.
    out = _violations('''
    def save_workshop_config(payload):
        atomic_write_json(path, payload)

    async def handler(payload):
        cm.save_workshop_config(payload)
    ''')
    assert len(out) == 1


# ── Report shape ────────────────────────────────────────────────────────


def test_one_call_site_is_reported_once():
    # `atomic_write_json` is both a known blocking call and a synchronous def containing
    # atomic_write_text, so direct and transitive rules both match one site.
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


# ── Real repository: this rule must pass ─────────────────────────────────


def test_the_repo_itself_has_no_on_loop_atomic_writes():
    """The whole point of the rule: keep the tree clean going forward."""
    assert MOD.main([]) == 0
