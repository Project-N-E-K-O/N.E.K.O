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


# ── Awaitable-argument slots (queue/thread/socket name heuristics) ──────


def test_an_unawaited_socket_recv_is_still_flagged():
    out = _violations('''
    async def handler(socket):
        data = socket.recv(1024)
    ''')
    assert len(out) == 1
    assert "socket.recv" in out[0][2]


def test_a_directly_awaited_recv_is_not_flagged():
    out = _violations('''
    async def handler(socket):
        raw = await socket.recv()
    ''')
    assert out == []


def test_recv_inside_wait_for_is_not_flagged():
    # services/chat_sync/runtime.py shape: a websockets connection named `socket`.
    out = _violations('''
    import asyncio

    async def handler(socket):
        reply = await asyncio.wait_for(socket.recv(), timeout=10)
        other = await asyncio.wait_for(fut=socket.recv(), timeout=10)
    ''')
    assert out == []


@pytest.mark.parametrize("wrapped", [
    "asyncio.shield(socket.recv())",
    "asyncio.ensure_future(request_queue.get())",
    "asyncio.create_task(coro=socket.recv())",
    "self.tg.create_task(socket.recv())",
    "self.event_loop.create_task(socket.recv())",
    "self._loop.create_task(socket.recv())",
    "asyncio.get_running_loop().create_task(socket.recv())",
    "asyncio.gather(socket.recv(), request_queue.get())",
    "asyncio.gather(*(socket.recv() for socket in sockets))",
    "asyncio.gather(*[q.get() for q in queues])",
    "asyncio.gather(*(socket.recv(), request_queue.get()))",
])
def test_other_awaitable_slots_are_not_flagged(wrapped):
    out = _violations(f'''
    import asyncio

    async def handler(self, socket, sockets, request_queue, queues, tg, loop):
        await {wrapped}
    ''')
    assert out == []


@pytest.mark.parametrize("call", [
    # Only asyncio's own entry points are trusted; a bare or repo helper is not.
    "await wait_for(socket.recv(), 10)",
    "await relay(socket.recv())",
    # create_task is trusted only on event loops / TaskGroups.
    "scheduler.create_task(socket.recv())",
    # A plain-name receiver is trusted only through scope analysis, never by its spelling.
    "loop.create_task(socket.recv())",
    "tg.create_task(socket.recv())",
    # Positions other than the awaitable slot keep the normal checks.
    "await asyncio.wait_for(fut, timeout=socket.recv())",
])
def test_non_slot_arguments_are_still_flagged(call):
    out = _violations(f'''
    import asyncio

    async def handler(socket, fut, loop, tg):
        {call}
    ''')
    assert len(out) == 1
    assert "socket.recv" in out[0][2]


@pytest.mark.parametrize("wrapped", [
    "asyncio.wait_for(socket.recv() if use_socket else request_queue.get(), 10)",
    "asyncio.gather(*(socket.recv() if use_socket else q.get() for q in queues))",
])
def test_both_branches_of_a_conditional_slot_are_not_flagged(wrapped):
    out = _violations(f'''
    import asyncio

    async def handler(socket, request_queue, queues, use_socket):
        await {wrapped}
    ''')
    assert out == []


def test_the_condition_of_a_conditional_slot_is_still_flagged():
    out = _violations('''
    import asyncio

    async def handler(socket, fut, other):
        await asyncio.wait_for(fut if socket.recv() else other, 10)
    ''')
    assert len(out) == 1
    assert "socket.recv" in out[0][2]


def test_constructor_bound_task_owners_are_trusted_under_any_name():
    out = _violations('''
    import asyncio

    async def handler(socket):
        runner = asyncio.get_running_loop()
        runner.create_task(socket.recv())
        async with asyncio.TaskGroup() as group:
            group.create_task(socket.recv())

            async def nested():
                group.create_task(socket.recv())
    ''')
    assert out == []


def test_a_task_owner_binding_does_not_leak_into_a_sibling_function():
    out = _violations('''
    import asyncio

    async def first(socket):
        async with asyncio.TaskGroup() as group:
            group.create_task(socket.recv())

    async def second(socket, group):
        group.create_task(socket.recv())
    ''')
    assert [lineno for lineno, _, _ in out] == [9]


@pytest.mark.parametrize("body", [
    # Rebinding to something else, in either order, revokes trust.
    "runner = asyncio.get_running_loop()\n        runner = scheduler\n        runner.create_task(socket.recv())",
    "runner = scheduler\n        runner = asyncio.get_running_loop()\n        runner.create_task(socket.recv())",
    "for group in groups:\n            group.create_task(socket.recv())",
])
def test_a_rebound_task_owner_name_is_not_trusted(body):
    out = _violations(f'''
    import asyncio

    async def handler(socket, scheduler, groups):
        {body}
    ''')
    assert len(out) == 1
    assert "socket.recv" in out[0][2]


@pytest.mark.parametrize("inner", [
    "async def inner(runner):\n            runner.create_task(socket.recv())",
    "async def inner():\n            runner = make_scheduler()\n            runner.create_task(socket.recv())",
])
def test_an_inner_scope_shadowing_a_task_owner_is_not_trusted(inner):
    out = _violations(f'''
    import asyncio

    async def handler(socket):
        runner = asyncio.get_running_loop()
        runner.create_task(socket.recv())

        {inner}
    ''')
    assert len(out) == 1
    assert "socket.recv" in out[0][2]
    assert out[0][0] > 6


@pytest.mark.parametrize("body", [
    # A comprehension target is local to the comprehension.
    "runner = asyncio.get_running_loop()\n        names = [runner for runner in runners]\n        runner.create_task(socket.recv())",
    # A bare annotation binds nothing; an annotated constructor binding counts.
    "runner: asyncio.AbstractEventLoop\n        runner = asyncio.get_running_loop()\n        runner.create_task(socket.recv())",
    "runner: asyncio.AbstractEventLoop = asyncio.get_running_loop()\n        runner.create_task(socket.recv())",
])
def test_bindings_outside_this_scope_keep_a_task_owner_trusted(body):
    out = _violations(f'''
    import asyncio

    async def handler(socket, runners):
        {body}
    ''')
    assert out == []


@pytest.mark.parametrize("body", [
    # The comprehension's own `group` shadows the outer TaskGroup.
    "async with asyncio.TaskGroup() as group:\n            [group.create_task(socket.recv()) for group in groups]",
    # A walrus inside a comprehension does rebind the enclosing name.
    "runner = asyncio.get_running_loop()\n        [(runner := s) for s in groups]\n        runner.create_task(socket.recv())",
    "runner: asyncio.AbstractEventLoop = scheduler\n        runner.create_task(socket.recv())",
])
def test_comprehension_and_annotation_rebinding_is_not_trusted(body):
    out = _violations(f'''
    import asyncio

    async def handler(socket, groups, scheduler):
        {body}
    ''')
    assert len(out) == 1
    assert "socket.recv" in out[0][2]


@pytest.mark.parametrize("body", [
    # The value of an assignment expression is what fills the slot.
    "await asyncio.wait_for((coro := socket.recv()), 10)",
    # The outermost iterable runs in the enclosing scope, before targets bind.
    "runner = asyncio.get_running_loop()\n        [x for runner in wrap(runner.create_task(socket.recv()))]",
    # A name only read inside a target expression is not rebound.
    "runner = asyncio.get_running_loop()\n        [runner.create_task(socket.recv()) for cache[runner] in items]",
])
def test_slot_and_scope_edge_cases_that_stay_trusted(body):
    out = _violations(f'''
    import asyncio

    async def handler(socket, wrap, cache, items):
        {body}
    ''')
    assert out == []


@pytest.mark.parametrize("source", [
    '''
    import asyncio

    async def handler(socket, scheduler):
        runner = asyncio.get_running_loop()

        def swap():
            nonlocal runner
            runner = scheduler

        swap()
        runner.create_task(socket.recv())
    ''',
    '''
    import asyncio

    loop = asyncio.get_event_loop()

    def swap(scheduler):
        global loop
        loop = scheduler

    async def handler(socket):
        loop.create_task(socket.recv())
    ''',
])
def test_a_nested_nonlocal_or_global_rebinding_revokes_trust(source):
    out = _violations(source)
    assert len(out) == 1
    assert "socket.recv" in out[0][2]


@pytest.mark.parametrize("source", [
    # A default is evaluated in the enclosing scope, where runner is a scheduler.
    '''
    import asyncio

    async def handler(socket, scheduler):
        runner = scheduler

        async def child(x=runner.create_task(socket.recv())):
            runner = asyncio.get_running_loop()
    ''',
    # A walrus in a default rebinds the enclosing name.
    '''
    import asyncio

    async def handler(socket, scheduler):
        runner = asyncio.get_running_loop()

        def child(x=(runner := scheduler)):
            return x

        runner.create_task(socket.recv())
    ''',
    # A wildcard import may rebind asyncio.
    '''
    import asyncio
    from fake_asyncio import *

    async def handler(socket):
        await asyncio.wait_for(socket.recv(), 10)
    ''',
    # A chained constructor on a shadowed asyncio is not trusted either.
    '''
    async def handler(socket, asyncio):
        asyncio.get_running_loop().create_task(socket.recv())
    ''',
    # Only the last boolean operand is guaranteed to be the slot value.
    '''
    import asyncio

    async def handler(socket, fallback):
        await asyncio.wait_for(socket.recv() or fallback, 10)
    ''',
])
def test_definition_time_wildcard_chain_and_boolop_cases_still_flag(source):
    out = _violations(source)
    assert len(out) == 1
    assert "socket.recv" in out[0][2]


@pytest.mark.parametrize("future, expected", [("", 1), ("from __future__ import annotations\n    ", 0)])
def test_nested_annotations_are_checked_only_when_they_run(future, expected):
    out = _violations(f'''
    {future}import time

    async def handler():
        def helper(x: time.sleep(1)) -> None:
            return x
    ''')
    assert len(out) == expected


@pytest.mark.parametrize("source", [
    # Starred arguments of a single-slot API.
    '''
    import asyncio

    async def handler(socket):
        await asyncio.wait_for(*[socket.recv(), 10])
        asyncio.create_task(*(socket.recv(),))
    ''',
    # as_completed takes an iterable of awaitables.
    '''
    import asyncio

    async def handler(socket, request_queue):
        for finished in asyncio.as_completed([socket.recv(), request_queue.get()]):
            await finished
        for finished in asyncio.as_completed(fs=(socket.recv() for _ in range(2))):
            await finished
    ''',
    # A walrus constructor binding is a task owner.
    '''
    import asyncio

    async def handler(socket):
        if (runner := asyncio.get_running_loop()):
            runner.create_task(socket.recv())
    ''',
    # Read-only global / nonlocal declarations resolve outward.
    '''
    import asyncio

    runner = asyncio.get_event_loop()

    async def handler(socket):
        global runner
        runner.create_task(socket.recv())

    async def outer(socket):
        group = asyncio.get_running_loop()

        async def inner():
            nonlocal group
            group.create_task(socket.recv())
    ''',
    # A constructor write through a declaration keeps the target trusted.
    '''
    import asyncio

    loop = asyncio.get_event_loop()

    def reset():
        global loop
        loop = asyncio.new_event_loop()

    async def handler(socket):
        loop.create_task(socket.recv())
    ''',
])
def test_more_awaitable_forms_and_declarations_stay_trusted(source):
    assert _violations(source) == []


def test_a_starred_comprehension_also_fills_later_arguments():
    # Its element runs for every yielded argument, so the `timeout` here is a
    # blocking queue read; nothing in it may be exempted.
    out = _violations('''
    import asyncio

    async def handler(socket, timeout_queue):
        await asyncio.wait_for(*(socket.recv() if i == 0 else timeout_queue.get() for i in range(2)))
    ''')
    assert any("queue.Queue.get" in msg for _, _, msg in out)


@pytest.mark.parametrize("source", [
    '''
    import asyncio

    async def handler(socket, other_loop):
        asyncio.run_coroutine_threadsafe(socket.recv(), other_loop)
    ''',
    # A module name bound only through a nested `global` constructor write.
    '''
    import asyncio

    def init():
        global loop
        loop = asyncio.new_event_loop()

    async def handler(socket):
        loop.create_task(socket.recv())
    ''',
])
def test_threadsafe_slot_and_global_only_owner_stay_trusted(source):
    assert _violations(source) == []


def test_a_class_body_read_before_its_assignment_is_not_trusted():
    out = _violations('''
    import asyncio

    async def handler(socket, scheduler):
        runner = scheduler

        class Local:
            runner.create_task(socket.recv())
            runner = asyncio.get_running_loop()
    ''')
    assert [lineno for lineno, _, _ in out] == [8]


def test_the_last_boolean_operand_fills_the_slot():
    out = _violations('''
    import asyncio

    async def handler(socket, existing):
        await asyncio.wait_for(existing or socket.recv(), 10)
    ''')
    assert out == []


def test_a_nested_global_does_not_touch_a_function_local_of_the_same_name():
    out = _violations('''
    import asyncio

    async def handler(socket, scheduler):
        runner = asyncio.get_running_loop()

        def swap_module_runner():
            global runner
            runner = scheduler

        runner.create_task(socket.recv())
    ''')
    assert out == []


def test_a_class_body_has_its_own_namespace_hidden_from_its_methods():
    out = _violations('''
    import asyncio

    async def handler(socket, scheduler):
        runner = asyncio.get_running_loop()

        class Local:
            runner = scheduler
            runner.create_task(socket.recv())

            async def method(self):
                runner.create_task(socket.recv())
    ''')
    assert [lineno for lineno, _, _ in out] == [9]


@pytest.mark.parametrize("source", [
    '''
    async def handler(socket, asyncio):
        await asyncio.wait_for(socket.recv(), 10)
    ''',
    '''
    import asyncio
    import fake_asyncio as asyncio

    async def handler(socket):
        runner = asyncio.get_running_loop()
        runner.create_task(socket.recv())
    ''',
])
def test_a_shadowed_asyncio_name_lifts_no_trust(source):
    out = _violations(source)
    assert len(out) == 1
    assert "socket.recv" in out[0][2]


def test_a_slot_does_not_skip_the_ambiguous_name_depth1_check():
    # An awaited ambiguous name is skipped (it may bind the async twin); a
    # slot argument keeps the pre-slot behaviour and is still reported.
    out = _violations('''
    import asyncio
    import time

    def load_cookies_from_file(path):
        time.sleep(1)

    class Store:
        async def load_cookies_from_file(self, path):
            return path

    async def handler(path):
        await load_cookies_from_file(path)
        await asyncio.gather(load_cookies_from_file(path))
    ''')
    assert [lineno for lineno, _, _ in out] == [14]
    assert "load_cookies_from_file" in out[0][2]


def test_a_direct_blocking_call_in_a_slot_is_still_flagged():
    # The slot only lifts the name-based type guess; requests.get still runs
    # synchronously before gather ever sees its result.
    out = _violations('''
    import asyncio

    async def handler(url):
        await asyncio.gather(requests.get(url))
    ''')
    assert len(out) == 1
    assert "requests.get" in out[0][2]


# ── Real repository: this rule must pass ─────────────────────────────────


def test_services_is_in_the_default_scope():
    assert "services" in MOD.DEFAULT_PATHS


def test_the_repo_itself_has_no_on_loop_atomic_writes():
    """The whole point of the rule: keep the tree clean going forward."""
    assert MOD.main([]) == 0
