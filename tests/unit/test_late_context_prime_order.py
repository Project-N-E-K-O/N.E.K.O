"""Late next-session context is judged in the order the new prompt holds it.

After a final swap the promoted session's prompt carries, in prime order:
next-session context snapshot, cache snapshot (preparation), incremental
context, incremental cache (final prime), then any context that arrived
while the final prime was awaiting (late context). The late slice must be
judged with exactly that preceding sequence.
"""

import asyncio
from types import SimpleNamespace

import pytest

from main_logic.omni_offline_client import OmniOfflineClient
from tests.unit.test_chat_context_reinjection import _manager, _swap
from tests.unit.test_hot_swap_cancellation import _drain_task

COMMENT_A = "屏幕搭话 蓝色小车停在一棵大树旁边，树叶的影子落在了车顶上。"
COMMENT_B = "屏幕搭话 远处的红色小车正在缓慢经过桥面，桥下的河水十分平静。"


async def _run(
    monkeypatch, *, context, snapshot_cache, incremental_cache, unprimed_cache=(),
    unprimed_growth="",
):
    """Prepare with ``context`` + ``snapshot_cache``, add ``incremental_cache``
    before the swap, and deliver COMMENT_B as next-session context while the
    final prime is awaiting, together with ``unprimed_cache`` (cache entries
    that land after the final prime's slice and are never primed) and
    ``unprimed_growth`` (text a still-streaming reply appends to the last
    cache entry after the final prime rendered it). Returns the promoted
    session's system text."""
    mgr, pending = _manager(monkeypatch)
    mgr.master_name = "Alice"
    # _make_swap_manager stubs the late prime out; this test is about it.
    del mgr._prime_late_next_session_context_after_swap

    async def get(*_args, **_kwargs):
        return SimpleNamespace(is_success=True, text="MEMORY\n")

    monkeypatch.setattr(
        "utils.internal_http_client.get_internal_http_client",
        lambda: SimpleNamespace(get=get),
    )
    mgr.next_session_context_messages = [dict(e) for e in context]
    mgr.message_cache_for_new_session = [dict(e) for e in snapshot_cache]

    late_delivered = []

    async def prime_while_late_context_arrives(text, skipped=False):
        if not late_delivered:
            result = await mgr.append_context(
                source="icebreaker",
                role="assistant",
                text=COMMENT_B,
                lifetime="next_session",
            )
            late_delivered.append(result)
            mgr.message_cache_for_new_session += [dict(e) for e in unprimed_cache]
            if unprimed_growth:
                mgr.message_cache_for_new_session[-1]["text"] += unprimed_growth
        await OmniOfflineClient.prime_context(pending, text, skipped=skipped)

    pending.prime_context = prime_while_late_context_arrives
    prep = asyncio.create_task(mgr._background_prepare_pending_session())
    try:
        await asyncio.wait_for(prep, 3)
        assert mgr.pending_session_warmed_up_event.is_set()
        mgr.message_cache_for_new_session += [dict(e) for e in incremental_cache]
        content = await _swap(mgr, pending)
        assert late_delivered and late_delivered[0].appended, late_delivered
        return mgr, content
    finally:
        await _drain_task(prep)
        await _drain_task(mgr.message_handler_task)


# The prose without its label, as the request view keeps it.
BODY_A = COMMENT_A[len("屏幕搭话 "):]
BODY_B = COMMENT_B[len("屏幕搭话 "):]


LAN = "Lan"  # _make_swap_manager's lanlan_name


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["snapshot", "increment"])
async def test_late_context_does_not_join_across_a_primed_user_turn(monkeypatch, where):
    """Context ends in one screen item, a real exchange sits in the cache,
    then one more item arrives late. The prompt holds a user turn between
    the two items, so there is no chain and the late item stays."""
    exchange = [
        {"role": "Alice", "text": "今天好累"},
        {"role": LAN, "text": "辛苦啦，先歇一会儿吧。"},
    ]
    mgr, content = await _run(
        monkeypatch,
        context=[{"role": "Alice", "text": "陪我聊聊"}, {"role": LAN, "text": COMMENT_A}],
        snapshot_cache=exchange if where == "snapshot" else [],
        incremental_cache=exchange if where == "increment" else [],
    )
    assert mgr.lanlan_name == LAN
    assert BODY_A in content, content
    # Prompt order: 陪我聊聊, A, 今天好累, 辛苦啦, B -> the run before the
    # trailing turn is [辛苦啦, B]: one screen item, no chain, so B stays
    # (without its label).
    assert BODY_B in content, content
    assert content.index("今天好累") < content.index(BODY_B), content
    assert "屏幕搭话" not in content, content


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["snapshot", "increment"])
async def test_late_context_joins_a_chain_that_starts_in_the_cache(monkeypatch, where):
    """The cache ends in a screen item answering a user turn; one more item
    arrives late. In the prompt they are one assistant run, so the late
    item is the chain's second comment and is left out."""
    exchange = [
        {"role": "Alice", "text": "陪我聊聊"},
        {"role": LAN, "text": COMMENT_A},
    ]
    mgr, content = await _run(
        monkeypatch,
        context=[],
        snapshot_cache=exchange if where == "snapshot" else [],
        incremental_cache=exchange if where == "increment" else [],
    )
    assert mgr.lanlan_name == LAN
    # COMMENT_A went out first, alone (unlabelled), and cannot be withdrawn.
    assert BODY_A in content, content
    assert BODY_B not in content, content


@pytest.mark.asyncio
async def test_late_context_still_joins_a_chain_inside_the_context(monkeypatch):
    """Guard for the fix: a chain that starts in the primed context and
    continues in late context is still caught."""
    mgr, content = await _run(
        monkeypatch,
        context=[{"role": "Alice", "text": "陪我聊聊"}, {"role": LAN, "text": COMMENT_A}],
        snapshot_cache=[],
        incremental_cache=[],
    )
    assert BODY_B not in content, content


@pytest.mark.asyncio
async def test_late_context_ignores_cache_entries_that_were_never_primed(monkeypatch):
    """A transcript cached while the final prime awaits is not in the prompt,
    so it must not split the run that the late item continues."""
    mgr, content = await _run(
        monkeypatch,
        context=[],
        snapshot_cache=[{"role": "Alice", "text": "陪我聊聊"}, {"role": LAN, "text": COMMENT_A}],
        incremental_cache=[],
        unprimed_cache=[{"role": "Alice", "text": "嗯嗯"}],
    )
    assert "嗯嗯" not in content, content
    assert BODY_B not in content, content


@pytest.mark.asyncio
async def test_late_context_ignores_reply_growth_that_was_never_primed(monkeypatch):
    """A reply still streaming while the final prime awaits grows its cache
    entry in place. That growth is not in the prompt, so it must not start
    a chain that the late item would then be judged to continue."""
    mgr, content = await _run(
        monkeypatch,
        context=[],
        snapshot_cache=[],
        incremental_cache=[{"role": "Alice", "text": "陪我聊聊"}, {"role": LAN, "text": "好呀。"}],
        unprimed_growth=COMMENT_A,
    )
    assert BODY_A not in content, content
    assert BODY_B in content, content
