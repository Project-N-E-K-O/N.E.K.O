# -*- coding: utf-8 -*-
"""AI-aware Stage-1 (path B): end-to-end invariants and contract pins.

Background: path A's existing SignalLoop sees only user messages (the sole ON-mode
fact-extraction route after PR #1346), missing AI self-disclosure and grounded
screen/activity context introduced by proactive speech. Path B piggybacks on A's loop,
running AI-aware Stage-1 over user+AI messages with known pools, labeling source='ai_disclosure'
and excluding these facts from Stage-2 evidence loops.

Coverage:
1. ``_extract_role_tagged_messages_from_rows`` accepts both user/AI types,
   rendering list[dict], not list[str].
2. ``_apersist_new_facts`` persists source and marks ai_disclosure
   signal_processed=True, preventing blocked pools.
3. ``_apersist_new_facts`` monotonic source upgrade: ai_disclosure
   ->user_observation is irreversible, resetting signal_processed=False for Stage-2
   reevaluation.
4. ``aextract_facts_and_detect_signals`` unprocessed filters
   ``source != 'ai_disclosure'`` as defense in depth.
5. Path-B cadence: every ``EVIDENCE_AI_AWARE_EVERY_N_A_TICKS``
   A ticks triggers once.
6. Cold-start lookback derives from ``EVIDENCE_AI_AWARE_EVERY_N_A_TICKS
   × EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES``, without new magic constants.

Manual/e2e scope, not covered:
- Actual LLM source attribution depends on prompts/models.
- Known-pool do-not-repeat effectiveness depends on LLM compliance.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_fact_store(*, facts=None, time_indexed=None):
    """Build a FactStore whose every ``__init__``-owned attribute exists.

    The tests below drive ``_apersist_new_facts`` directly, so they need a
    store that is fully formed but talks to no config manager.  They used to
    do that with ``FactStore.__new__`` plus a hand-written list of attributes,
    which silently stranded six of them for ten weeks when #2282 added one
    field (``_persist_alocks``) to ``__init__``.  Running the real ``__init__``
    with only the singleton patched out keeps that from happening again.
    """
    from memory.facts import FactStore

    with patch('memory.facts.get_config_manager', return_value=MagicMock()):
        fs = FactStore(time_indexed_memory=time_indexed)
    fs._facts = {'悠怡': []} if facts is None else facts
    return fs


@pytest.mark.unit
def test_extract_role_tagged_messages_keeps_user_and_ai():
    """Symmetric with _extract_user_messages_from_rows: path B must accept AI messages."""
    from app.memory_server import _extract_role_tagged_messages_from_rows

    rows = [
        (datetime(2026, 5, 18, 10, 0, 0), 'sess1', json.dumps({
            'type': 'human', 'data': {'content': '主人说的话'},
        })),
        (datetime(2026, 5, 18, 10, 0, 5), 'sess1', json.dumps({
            'type': 'ai', 'data': {'content': '猫娘自己的话'},
        })),
        (datetime(2026, 5, 18, 10, 0, 10), 'sess1', json.dumps({
            'type': 'system', 'data': {'content': '系统消息不应该收'},
        })),
        (datetime(2026, 5, 18, 10, 0, 15), 'sess1', json.dumps({
            'type': 'ai', 'data': {'content': [
                {'type': 'text', 'text': 'part1 '},
                {'type': 'text', 'text': 'part2'},
            ]},
        })),
    ]
    out = _extract_role_tagged_messages_from_rows(rows)
    assert len(out) == 3, f"应收 2 human + 1 ai = 3 条，实际 {len(out)}"
    types = [m['type'] for m in out]
    assert types == ['human', 'ai', 'ai']  # Filter system messages.
    assert out[0]['data']['content'] == '主人说的话'
    assert out[1]['data']['content'] == '猫娘自己的话'
    # Join content lists into one string.
    assert out[2]['data']['content'] == 'part1 part2'


@pytest.mark.unit
def test_fact_extractors_ignore_numeric_theater_messages():
    """Fictional theater dialogue/player actions must not become real-person facts."""  # noqa: DOCSTRING_CJK
    from app.memory_server import (
        _extract_role_tagged_messages_from_rows,
        _extract_user_messages_from_rows,
    )

    theater_metadata = {'source': 'theater_numeric_v2', 'session_id': 'theater_session'}
    rows = [
        (datetime(2026, 5, 18, 10, 0, 0), 'sess1', json.dumps({
            'type': 'human',
            'data': {'content': '我住在虚构的 302 室。', 'metadata': theater_metadata},
        })),
        (datetime(2026, 5, 18, 10, 0, 5), 'sess1', json.dumps({
            'type': 'ai',
            'data': {'content': '那是我们在剧本里的家。', 'metadata': theater_metadata},
        })),
        (datetime(2026, 5, 18, 10, 0, 10), 'sess1', json.dumps({
            'type': 'human', 'data': {'content': '现实里我喜欢喝咖啡。'},
        })),
    ]

    assert _extract_user_messages_from_rows(rows) == ['现实里我喜欢喝咖啡。']
    assert _extract_role_tagged_messages_from_rows(rows) == [{
        'type': 'human',
        'data': {'content': '现实里我喜欢喝咖啡。'},
    }]


@pytest.mark.unit
def test_extract_role_tagged_messages_skips_empty_content():
    """Exclude blank content from lists, avoiding empty prompt lines."""
    from app.memory_server import _extract_role_tagged_messages_from_rows

    rows = [
        (datetime(2026, 5, 18, 10, 0, 0), 'sess1', json.dumps({
            'type': 'human', 'data': {'content': '   '},  # Whitespace only.
        })),
        (datetime(2026, 5, 18, 10, 0, 5), 'sess1', json.dumps({
            'type': 'human', 'data': {'content': '有内容'},
        })),
    ]
    out = _extract_role_tagged_messages_from_rows(rows)
    assert len(out) == 1
    assert out[0]['data']['content'] == '有内容'


@pytest.mark.unit
def test_trim_to_user_msg_bracket_strips_leading_and_trailing_ai():
    """Product contract: trim path-B Stage-1 messages between the first
    and final user messages, inclusive. Leading/trailing AI fragments
    lack user corroboration/responses and must not become facts.
    """
    from app.memory_server import _trim_to_user_msg_bracket

    # Typical: [ai, ai, human, ai, human, ai] ->retain [human, ai, human].
    msgs = [
        {'type': 'ai', 'data': {'content': 'AI proactive 试探'}},
        {'type': 'ai', 'data': {'content': '还是 AI'}},
        {'type': 'human', 'data': {'content': 'user 终于发声'}},
        {'type': 'ai', 'data': {'content': 'AI 中间回应'}},
        {'type': 'human', 'data': {'content': 'user 再发声'}},
        {'type': 'ai', 'data': {'content': 'AI 末尾独白 (user 没回应)'}},
    ]
    out = _trim_to_user_msg_bracket(msgs)
    assert len(out) == 3
    assert out[0]['data']['content'] == 'user 终于发声'
    assert out[1]['data']['content'] == 'AI 中间回应'
    assert out[2]['data']['content'] == 'user 再发声'


@pytest.mark.unit
def test_trim_to_user_msg_bracket_all_ai_returns_empty():
    """AI-only windows without human messages trim entirely empty.
    Callers skip this uncorroborated layer."""
    from app.memory_server import _trim_to_user_msg_bracket

    msgs = [
        {'type': 'ai', 'data': {'content': '独白 1'}},
        {'type': 'ai', 'data': {'content': '独白 2'}},
    ]
    assert _trim_to_user_msg_bracket(msgs) == []


@pytest.mark.unit
def test_trim_to_user_msg_bracket_single_human_returns_self():
    """One human message collapses brackets to a point, still valid because the user spoke."""
    from app.memory_server import _trim_to_user_msg_bracket

    msgs = [
        {'type': 'ai', 'data': {'content': '前置 AI'}},
        {'type': 'human', 'data': {'content': '唯一 user msg'}},
        {'type': 'ai', 'data': {'content': '后置 AI'}},
    ]
    out = _trim_to_user_msg_bracket(msgs)
    assert len(out) == 1
    assert out[0]['type'] == 'human'
    assert out[0]['data']['content'] == '唯一 user msg'


@pytest.mark.unit
def test_trim_to_user_msg_bracket_already_user_to_user_unchanged():
    """Human messages already at both ends remain unchanged."""
    from app.memory_server import _trim_to_user_msg_bracket

    msgs = [
        {'type': 'human', 'data': {'content': 'u1'}},
        {'type': 'ai', 'data': {'content': 'a1'}},
        {'type': 'human', 'data': {'content': 'u2'}},
    ]
    out = _trim_to_user_msg_bracket(msgs)
    assert out == msgs


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_trims_to_user_bracket_before_stage1():
    """End-to-end: _run_path_b must pass user-bracket-trimmed messages
    into aextract_facts_with_known_pool.

    Window: two AI +middle [user, ai, user] +two AI.
    Stage-1 must receive only the middle three.
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=10)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    base = last_b + timedelta(seconds=10)
    rows = [
        (base + timedelta(seconds=0), 's', json.dumps({
            'type': 'ai', 'data': {'content': 'AI 试探 1 (user 未印证)'},
        })),
        (base + timedelta(seconds=10), 's', json.dumps({
            'type': 'ai', 'data': {'content': 'AI 试探 2 (user 未印证)'},
        })),
        (base + timedelta(seconds=20), 's', json.dumps({
            'type': 'human', 'data': {'content': 'user 第一句'},
        })),
        (base + timedelta(seconds=30), 's', json.dumps({
            'type': 'ai', 'data': {'content': 'AI 中间回应 (有 user 包夹)'},
        })),
        (base + timedelta(seconds=40), 's', json.dumps({
            'type': 'human', 'data': {'content': 'user 最后一句'},
        })),
        (base + timedelta(seconds=50), 's', json.dumps({
            'type': 'ai', 'data': {'content': 'AI 末尾独白 (user 未回应)'},
        })),
        (base + timedelta(seconds=60), 's', json.dumps({
            'type': 'ai', 'data': {'content': 'AI 又一条独白'},
        })),
    ]

    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=rows)
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=[])
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    # Verify Stage-1 receives only the three bracketed messages.
    fake_fact_store.aextract_facts_with_known_pool.assert_awaited_once()
    sent_messages = fake_fact_store.aextract_facts_with_known_pool.await_args.args[1]
    assert len(sent_messages) == 3, (
        f"bracket trim 后应剩 3 条 (user, ai, user)，"
        f"实际 {len(sent_messages)}: {[type(m).__name__ for m in sent_messages]}"
    )
    contents = [
        m.content if hasattr(m, 'content') else m.get('data', {}).get('content', '')
        for m in sent_messages
    ]
    # Both endpoints must be user messages.
    assert 'user 第一句' in contents[0]
    assert 'user 最后一句' in contents[2]
    # Strip leading/trailing AI fragments.
    full_text = '\n'.join(contents)
    assert 'AI 试探' not in full_text, (
        f"首部 AI 试探不该入 Stage-1（user 未印证），实际 content: {full_text!r}"
    )
    assert 'AI 末尾独白' not in full_text and 'AI 又一条独白' not in full_text, (
        f"尾部 AI 独白不该入 Stage-1（user 未回应），实际 content: {full_text!r}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_no_user_msg_in_window_skips_extraction():
    """End-to-end: no user messages means empty bracket trimming;
    path B skips Stage-1 but advances cursors to avoid repeatedly scanning the same window.
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=10)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    rows = [
        (last_b + timedelta(seconds=10), 's', json.dumps({
            'type': 'ai', 'data': {'content': '纯 AI 独白 1'},
        })),
        (last_b + timedelta(seconds=20), 's', json.dumps({
            'type': 'ai', 'data': {'content': '纯 AI 独白 2'},
        })),
    ]
    last_row_ts = rows[-1][0]

    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=rows)
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=[])
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    # Stage-1 must not run.
    fake_fact_store.aextract_facts_with_known_pool.assert_not_awaited()
    # Advance cursor to last fetched row timestamp, avoiding repeated scans.
    assert state['last_b_check_ts'] == last_row_ts, (
        f"AI-only 窗口跳过后 cursor 应推到 last fetched ({last_row_ts})，"
        f"实际 {state['last_b_check_ts']}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apersist_writes_source_field_default_user_observation():
    """Path A omits default_source, persisting source='user_observation'
    with signal_processed=False for normal Stage-2 processing."""
    fs = _make_fact_store()
    fs.aload_facts = AsyncMock(return_value=[])
    fs.asave_facts = AsyncMock(return_value=None)

    extracted = [
        {'text': '博士喜欢三文鱼', 'importance': 8, 'entity': 'master'},
    ]
    with patch.object(fs, 'aload_facts', AsyncMock(return_value=[])):
        new_facts = await fs._apersist_new_facts('悠怡', extracted)

    assert len(new_facts) == 1
    assert new_facts[0]['source'] == 'user_observation'
    assert new_facts[0]['signal_processed'] is False, (
        "user_observation fact 必须 signal_processed=False 让 Stage-2 取它"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apersist_writes_source_ai_disclosure_with_signal_processed_true():
    """Path B passes default_source='ai_disclosure', persisting that source
    with signal_processed=True, bypassing Stage-2 evidence loops."""
    fs = _make_fact_store()
    fs.asave_facts = AsyncMock(return_value=None)

    extracted = [
        {'text': '悠怡觉得自己挺喜欢秋天', 'importance': 6, 'entity': 'neko'},
    ]
    with patch.object(fs, 'aload_facts', AsyncMock(return_value=[])):
        new_facts = await fs._apersist_new_facts(
            '悠怡', extracted, default_source='ai_disclosure',
        )

    assert len(new_facts) == 1
    assert new_facts[0]['source'] == 'ai_disclosure'
    assert new_facts[0]['signal_processed'] is True, (
        "ai_disclosure fact 必须写盘时 signal_processed=True 防卡 Stage-2 池"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apersist_llm_source_field_overrides_default():
    """Explicit LLM source overrides default_source, trusting its
    per-fact judgment."""
    fs = _make_fact_store()
    fs.asave_facts = AsyncMock(return_value=None)

    # LLM returns source='user_observation', caller defaults to ai_disclosure.
    # Simulate LLM recognizing user corroboration despite this being an ai_aware pass;
    # respect its user_observation attribution.
    extracted = [
        {'text': '博士喜欢咖啡', 'importance': 7, 'entity': 'master',
         'source': 'user_observation'},
    ]
    with patch.object(fs, 'aload_facts', AsyncMock(return_value=[])):
        new_facts = await fs._apersist_new_facts(
            '悠怡', extracted, default_source='ai_disclosure',
        )
    assert new_facts[0]['source'] == 'user_observation'
    # user_observation also requires signal_processed=False.
    assert new_facts[0]['signal_processed'] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apersist_monotonic_source_upgrade_ai_to_user():
    """An incoming user_observation whose SHA-256 hits a stored ai_disclosure
    fact upgrades that fact's source in place and resets signal_processed.

    In-memory semantics only — ``asave_facts`` is mocked here.  Whether the
    reset survives persistence is a separate question, and one this shape of
    test cannot answer: mocking the save is exactly what hid the monotonic
    read-merge undoing it for ten weeks.  That half lives in
    ``test_apersist_source_upgrade_survives_the_real_save``, which drives a
    real file round-trip; keep both.
    """
    import hashlib

    fs = _make_fact_store()

    text = '博士喜欢三文鱼'
    content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    existing_fact = {
        'id': 'fact_old', 'text': text, 'hash': content_hash,
        'source': 'ai_disclosure', 'signal_processed': True,
        'importance': 6, 'entity': 'master',
    }
    existing_facts_list = [existing_fact]

    fs.asave_facts = AsyncMock(return_value=None)
    extracted = [
        {'text': text, 'importance': 8, 'entity': 'master',
         'source': 'user_observation'},
    ]
    with patch.object(fs, 'aload_facts',
                      AsyncMock(return_value=existing_facts_list)):
        new_facts = await fs._apersist_new_facts(
            '悠怡', extracted, default_source='user_observation',
        )

    # No new fact returned (SHA-256 duplicate skips writes), but existing upgraded in place.
    assert new_facts == []
    assert existing_fact['source'] == 'user_observation', "user 印证后应升级"
    assert existing_fact['signal_processed'] is False, (
        "升级后必须重置 signal_processed=False 让 Stage-2 重新评估"
    )
    fs.asave_facts.assert_awaited_once_with('悠怡')


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apersist_monotonic_source_upgrade_within_same_batch():
    """Regression (Codex P2 round-10 on PR #1408): duplicate Stage-1 text in one
    payload, ai_disclosure followed by user_observation, requires
    `hash_to_existing` updates immediately after first writes; otherwise the second
    `content_hash in existing_hashes` match finds `hash_to_existing.get()` None,
    skipping monotonic upgrades and silently losing user_observation promotion.
    """
    fs = _make_fact_store()
    fs.asave_facts = AsyncMock()

    # One Stage-1 payload repeats text: ai_disclosure first, then user_observation.
    extracted = [
        {
            'text': '主人喜欢猫', 'importance': 7, 'entity': 'master',
            'source': 'ai_disclosure',
        },
        {
            'text': '主人喜欢猫', 'importance': 7, 'entity': 'master',
            'source': 'user_observation',
        },
    ]

    await fs._apersist_new_facts(
        '悠怡', extracted, default_source='ai_disclosure',
    )

    # Persist only one deduplicated fact, upgraded to user_observation.
    persisted = fs._facts['悠怡']
    assert len(persisted) == 1, (
        f"同 text 两条应 dedup 成 1 条，实际 {len(persisted)} 条"
    )
    assert persisted[0]['source'] == 'user_observation', (
        f"第二次 user_observation 必须升级第一次的 ai_disclosure，"
        f"实际 source={persisted[0]['source']!r}（说明 hash_to_existing 没在 batch 内同步）"
    )
    assert persisted[0]['signal_processed'] is False, (
        "升级后 signal_processed 必须 reset 成 False 让 Stage-2 重新评估"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apersist_source_upgrade_survives_the_real_save(tmp_path):
    """The unsealed signal_processed must still be False on disk afterwards.

    Drives a real save round-trip instead of an AsyncMock. save_facts treats
    signal_processed as a False-to-True-only field and re-applies True from
    disk (guarding against a stale cache re-queueing facts Stage-2 already
    consumed), which used to land on the very fact the upgrade had just
    unsealed and silently undo it — the source label moved but the fact never
    re-entered Stage-2.
    """
    import hashlib

    fs = _make_fact_store(facts={})
    fs._config_manager.memory_dir = str(tmp_path)

    text = '博士喜欢三文鱼'
    stored = {
        'id': 'fact_old',
        'text': text,
        'hash': hashlib.sha256(text.encode()).hexdigest()[:16],
        'source': 'ai_disclosure',
        'signal_processed': True,
        'importance': 8,
        'entity': 'master',
    }
    fs._facts['悠怡'] = [stored]
    fs.save_facts('悠怡')

    await fs._apersist_new_facts('悠怡', [
        {'text': text, 'importance': 8, 'entity': 'master'},
    ])

    with open(fs._facts_path('悠怡'), encoding='utf-8') as handle:
        persisted = json.load(handle)

    assert len(persisted) == 1
    entry = persisted[0]
    assert entry['source'] == 'user_observation'
    assert entry['signal_processed'] is False, (
        "解封必须活过 save_facts 的单调 read-merge，否则升级只换标签、永远回不到 Stage-2"
    )
    assert '_signal_reset_pending' not in entry, "内存内纸条不许落盘"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_cache_still_cannot_unset_signal_processed(tmp_path):
    """The unseal exemption stays narrow: only the upgrade itself may reset.

    Dual of the test above.  The monotonic read-merge exists so a stale
    in-memory copy cannot re-queue facts Stage-2 already consumed, and an
    exemption wide enough to also cover that case would trade one silent bug
    for another.
    """
    fs = _make_fact_store(facts={})
    fs._config_manager.memory_dir = str(tmp_path)

    fs._facts['悠怡'] = [{
        'id': 'fact_consumed',
        'text': '博士在读研',
        'source': 'user_observation',
        'signal_processed': True,
        'importance': 6,
        'entity': 'master',
    }]
    fs.save_facts('悠怡')

    # Stale memory cache says unprocessed; persisted True must win during writes.
    fs._facts['悠怡'][0]['signal_processed'] = False
    fs.save_facts('悠怡')

    with open(fs._facts_path('悠怡'), encoding='utf-8') as handle:
        persisted = json.load(handle)

    assert persisted[0]['signal_processed'] is True, (
        "没有解封纸条的 False 仍必须被单调回写顶回 True"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apersist_no_downgrade_user_to_ai():
    """Reverse case: existing user_observation plus new source=ai_disclosure
    leaves existing unchanged; user corroboration cannot regress to ai_disclosure."""
    import hashlib

    fs = _make_fact_store()

    text = '博士喜欢三文鱼'
    content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    existing_fact = {
        'id': 'fact_old', 'text': text, 'hash': content_hash,
        'source': 'user_observation', 'signal_processed': True,
        'importance': 8, 'entity': 'master',
    }
    fs.asave_facts = AsyncMock(return_value=None)

    extracted = [
        {'text': text, 'importance': 6, 'entity': 'master',
         'source': 'ai_disclosure'},
    ]
    with patch.object(fs, 'aload_facts',
                      AsyncMock(return_value=[existing_fact])):
        await fs._apersist_new_facts(
            '悠怡', extracted, default_source='ai_disclosure',
        )

    # Never downgrade.
    assert existing_fact['source'] == 'user_observation'
    # Leave signal_processed unchanged too.
    assert existing_fact['signal_processed'] is True
    # No changes means no save.
    fs.asave_facts.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stage2_filters_out_ai_disclosure_facts():
    """Stage-2 unprocessed pools filter ``source='ai_disclosure'``.
    Defense in depth: persisted signal_processed=True is first; this is second."""
    fs = _make_fact_store(facts={})

    # Three facts.json entries: unprocessed user_observation enters Stage-2;
    # unprocessed ai_disclosure must not enter, even with signal_processed=False bugs;
    # unprocessed legacy missing-source defaults to user_observation and enters.
    fake_facts = [
        {'id': 'a', 'source': 'user_observation', 'signal_processed': False,
         'importance': 8, 'created_at': '2026-05-18T10:00:00'},
        {'id': 'b', 'source': 'ai_disclosure', 'signal_processed': False,
         'importance': 7, 'created_at': '2026-05-18T10:00:01'},
        {'id': 'c', 'signal_processed': False,  # Legacy missing source.
         'importance': 6, 'created_at': '2026-05-18T10:00:02'},
    ]

    fs._allm_extract_facts = AsyncMock(return_value=[])  # Generate no new facts.
    fs._apersist_new_facts = AsyncMock(return_value=[])
    fs.aload_facts = AsyncMock(return_value=fake_facts)
    fs._aload_signal_targets = AsyncMock(return_value=[
        {'id': 'obs1', 'text': '观察', 'target_type': 'reflection'},
    ])
    fs._allm_detect_signals = AsyncMock(return_value=[])  # signals=[] but ran

    # _allm_detect_signals receives the filtered unprocessed batch.
    # Await without binding variables; test only mock arguments, not triple returns.
    # CodeQL still treats `_xxx` prefixes unused, so bind nothing.
    await fs.aextract_facts_and_detect_signals('悠怡', messages=[])

    # Verify _allm_detect_signals called without b (ai_disclosure) in its batch.
    fs._allm_detect_signals.assert_awaited_once()
    actual_batch = fs._allm_detect_signals.await_args.args[1]
    actual_ids = {f['id'] for f in actual_batch}
    assert 'a' in actual_ids, "user_observation fact 必须入 Stage-2"
    assert 'c' in actual_ids, "无 source 字段的老 fact 默认按 user_observation 入池"
    assert 'b' not in actual_ids, (
        "ai_disclosure fact 必须被 source filter 排除，"
        "防止 path B 抽出的 AI 自我披露进 evidence loop 形成自我强化"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_cold_start_lookback_derived_from_constants():
    """B cold-start last_b =last_a_msg_ts -max(N_TICKS,N_TURNS)×IDLE_MIN,
    requiring no separate LOOKBACK_MINUTES config.
    max covers sparse-turn cases where A tick separation
    greatly exceeds IDLE_MIN. N_TICKS×IDLE_MIN alone places cold-start last_b
    inside A's processed range, permanently skipping earlier AI-only messages (Codex P2
    round-6 on PR #1408).
    """
    from app import memory_server
    from config import (
        EVIDENCE_AI_AWARE_EVERY_N_A_TICKS,
        EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS,
        EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES,
    )

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    state = {'last_a_msg_ts': last_a_msg_ts}  # No last_b_check_ts.

    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[])
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=[])
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    # Verify aretrieve_original_by_timeframe start_time equals exactly
    # last_a_msg_ts - max(N_TICKS, N_TURNS) × IDLE_MIN（cold-start lookback）
    fake_time_manager.aretrieve_original_by_timeframe.assert_awaited_once()
    call_kwargs = fake_time_manager.aretrieve_original_by_timeframe.await_args
    start_time = call_kwargs.args[1]
    end_time = call_kwargs.args[2]
    expected_n = max(
        EVIDENCE_AI_AWARE_EVERY_N_A_TICKS,
        EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS,
    )
    expected_lookback = timedelta(
        minutes=expected_n * EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES
    )
    assert start_time == last_a_msg_ts - expected_lookback, (
        f"cold start lookback 必须 = max(N_TICKS, N_TURNS)×IDLE_MIN = "
        f"{expected_lookback}, 实际 start={start_time}, end={end_time}, "
        f"last_a={last_a_msg_ts}"
    )
    assert end_time == last_a_msg_ts, (
        "B 窗口下游边界必须是 last_a_msg_ts（A 实际处理过的最晚 msg），"
        "不是 wall-clock now"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_cold_start_uses_max_of_ticks_and_turns():
    """Regression (Codex P2 round-6 on PR #1408): cold-start lookback must
    use max(N_TICKS,N_TURNS) ×IDLE_MIN, never N_TICKS alone. Sparse
    turn-count triggers may span far more than piggyback estimates,
    placing cold-start last_b inside processed A ranges and permanently skipping preceding
    AI-only messages.

    N_TURNS much larger than N_TICKS (default 10 vs 3) pins this;
    N_TICKS-only implementations fail.
    """
    from app import memory_server
    from config import (
        EVIDENCE_AI_AWARE_EVERY_N_A_TICKS,
        EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS,
        EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES,
    )

    # Meaningful only when default N_TURNS>N_TICKS.
    assert EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS > EVIDENCE_AI_AWARE_EVERY_N_A_TICKS, (
        "本 test 依赖默认 N_TURNS > N_TICKS，否则 max 两者退化、测不出区别"
    )

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    state = {'last_a_msg_ts': last_a_msg_ts}

    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager):
        await memory_server._run_path_b('悠怡', state)

    start_time = fake_time_manager.aretrieve_original_by_timeframe.await_args.args[1]
    # Actual lookback must be >=N_TURNS ×IDLE_MIN, strictly >N_TICKS ×IDLE_MIN.
    n_turns_lookback = timedelta(
        minutes=EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS * EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES
    )
    n_ticks_lookback = timedelta(
        minutes=EVIDENCE_AI_AWARE_EVERY_N_A_TICKS * EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES
    )
    actual_lookback = last_a_msg_ts - start_time
    assert actual_lookback >= n_turns_lookback, (
        f"cold-start lookback ({actual_lookback}) 必须 >= N_TURNS×IDLE_MIN "
        f"({n_turns_lookback}) 才能 cover sparse turn 场景"
    )
    assert actual_lookback > n_ticks_lookback, (
        f"cold-start lookback ({actual_lookback}) 必须严格 > N_TICKS×IDLE_MIN "
        f"({n_ticks_lookback})——否则等于只看了 piggyback 估算，sparse "
        f"turn 场景下 A 真处理范围更老的 AI-only msg 会永久 skip"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_skips_when_a_never_ran():
    """Before A processes any batch (last_a_msg_ts is None), B has no source;
    return without errors."""
    from app import memory_server

    state = {}  # Entirely empty state on cold launcher start.
    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager):
        await memory_server._run_path_b('悠怡', state)

    # Without last_a_msg_ts, B must not read SQL.
    fake_time_manager.aretrieve_original_by_timeframe.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_full_window_advances_cursor_to_last_fetched_eq_last_a_msg_ts():
    """Untruncated rows reaching last_a_msg_ts make cursor=last fetched exactly
    last_a_msg_ts, the latest point A processed.

    Symmetric to test_path_b_truncated_window_...: that covers truncation,
    with last fetched<last_a_msg_ts; this covers full windows.
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_a_msg_ts - timedelta(minutes=20),
    }

    fake_time_manager = MagicMock()
    # Final row ts equals last_a_msg_ts, normal untruncated case.
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[
        (last_a_msg_ts - timedelta(minutes=5), 's', json.dumps({
            'type': 'human', 'data': {'content': '中间消息'},
        })),
        (last_a_msg_ts, 's', json.dumps({
            'type': 'ai', 'data': {'content': '最后消息恰是 A 边界'},
        })),
    ])
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=[])
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    # Untruncated last fetched==last_a_msg_ts; advance cursor there.
    assert state['last_b_check_ts'] == last_a_msg_ts, (
        f"无截断时 cursor (= last fetched) 应等于 last_a_msg_ts={last_a_msg_ts}，"
        f"实际 last_b_check_ts={state['last_b_check_ts']}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_empty_rows_preserves_cursor_for_retry():
    """Regression (Codex P1 round-5 on PR #1408):
    `aretrieve_original_by_timeframe` swallows SQL exceptions/engine-init failures/
    maintenance and returns [], indistinguishable from genuinely empty windows.
    Advancing empty-row cursors to last_a_msg_ts permanently skips [last_b,
    last_a_msg_ts] after transient SQL failures, silently losing path-B
    facts. Conservatively retain cursors for the next trigger to retry.

    Cost: truly empty windows query empty again next B trigger (SQLite empty-range
    scans are extremely fast, constant overhead). A grows windows by advancing last_a_msg_ts,
    bounded by MAX_AI_AWARE_WINDOW_MSGS LIMIT.
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    original_last_b = last_a_msg_ts - timedelta(minutes=20)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': original_last_b,
    }

    fake_time_manager = MagicMock()
    # Simulate transient SQL failures swallowed as [], indistinguishable from empty windows.
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager):
        await memory_server._run_path_b('悠怡', state)

    # Preserve cursor so the next B trigger retries the same window.
    assert state['last_b_check_ts'] == original_last_b, (
        f"rows 空时 cursor 必须保留原值 {original_last_b} (transient SQL 失败保护)，"
        f"实际推到 {state['last_b_check_ts']}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_filters_known_pool_by_window_and_caps():
    """Known pools use only ``created_at >= last_b`` lower bounds, no upper bound; see
    `test_path_b_known_pool_includes_just_written_a_facts_despite_clock_skew`
    for clock-skew contracts. Sort importance DESC, cap MAX_KNOWN_POOL_FACTS.
    """
    from app import memory_server
    from config import MAX_KNOWN_POOL_FACTS

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=20)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # Build 35 in-window facts at 30s intervals within 20-min, plus 20 outside.
    in_window_facts = [
        {'id': f'in_{i}', 'text': f'fact in {i}',
         'importance': i % 10 + 1,
         'created_at': (last_b + timedelta(seconds=i * 30)).isoformat()}
        for i in range(35)  # 35 in-window entries >MAX_KNOWN_POOL_FACTS (30).
    ]
    out_window_facts = [
        {'id': f'out_{i}', 'text': f'fact out {i}',
         'importance': 10,  # High importance, outside window.
         'created_at': (last_b - timedelta(hours=2 + i)).isoformat()}
        for i in range(20)
    ]
    all_facts = in_window_facts + out_window_facts

    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[
        (last_a_msg_ts - timedelta(minutes=3), 's', json.dumps({
            'type': 'human', 'data': {'content': '测试'},
        })),
    ])
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=all_facts)
    captured_known_pool = []

    async def capture_extract(name, messages, known_pool):
        captured_known_pool.extend(known_pool)
        return []

    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(
        side_effect=capture_extract,
    )

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    # 1. Cap enforced: MAX_KNOWN_POOL_FACTS=30 at most.
    assert len(captured_known_pool) == MAX_KNOWN_POOL_FACTS

    # 2. All in-window, with in_* ids.
    for f in captured_known_pool:
        assert f['id'].startswith('in_'), (
            f"out-of-window fact 不该入池，命中: {f['id']}"
        )

    # 3. importance DESC, highest first.
    importances = [f['importance'] for f in captured_known_pool]
    assert importances == sorted(importances, reverse=True), (
        f"已知池必须按 importance DESC 排，实际: {importances}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_truncated_window_advances_cursor_to_last_fetched_row():
    """Regression (Codex P1 PR #1408): windows exceeding MAX_AI_AWARE_WINDOW_MSGS
    return earliest N SQL rows; cursor must advance to **the actual final fetched timestamp**,
    never the original last_a_msg_ts. Otherwise unseen tails skip forever,
    silently hiding that burst from path B.
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=30)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # Truncated SQL rows reach only last_a_msg_ts -10 min; actual window is
    # 30 min, but LIMIT stops the earliest-timestamp portion at 10 min.
    truncation_boundary = last_a_msg_ts - timedelta(minutes=10)
    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[
        (last_b + timedelta(seconds=10), 's', json.dumps({
            'type': 'human', 'data': {'content': '早消息'},
        })),
        (truncation_boundary, 's', json.dumps({
            'type': 'ai', 'data': {'content': '截断边界'},
        })),
    ])
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=[])
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    assert state['last_b_check_ts'] == truncation_boundary, (
        f"cursor 必须推到 last fetched row ts ({truncation_boundary}), "
        f"不是 last_a_msg_ts ({last_a_msg_ts}). 实际: {state['last_b_check_ts']}"
    )
    # Next B trigger must continue here before reaching last_a_msg_ts.
    assert state['last_b_check_ts'] < last_a_msg_ts, (
        "truncated 窗口 + 未取尾巴 → cursor 不该追上 A，下次 B 继续处理"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_known_pool_sort_tolerates_malformed_importance():
    """Regression (Codex P2 PR #1408): dirty values in legacy / manually edited facts.json,
    such as 'importance': \"high\" / None / list, must not crash the entire path B sort
    (raw int(...) cast → ValueError → path B permanently silent for that character).
    Fall back through safe_importance.
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=20)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # Build mixed invalid importance values covering each malformed type.
    dirty_facts = [
        {'id': 'normal', 'text': 't', 'importance': 8,
         'created_at': (last_b + timedelta(minutes=1)).isoformat()},
        {'id': 'str_high', 'text': 't', 'importance': "high",  # ❌ ValueError
         'created_at': (last_b + timedelta(minutes=2)).isoformat()},
        {'id': 'none_imp', 'text': 't', 'importance': None,
         'created_at': (last_b + timedelta(minutes=3)).isoformat()},
        {'id': 'list_imp', 'text': 't', 'importance': [1, 2, 3],  # ❌ TypeError
         'created_at': (last_b + timedelta(minutes=4)).isoformat()},
        {'id': 'missing_imp', 'text': 't',  # Missing field.
         'created_at': (last_b + timedelta(minutes=5)).isoformat()},
    ]

    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[
        (last_a_msg_ts - timedelta(minutes=1), 's', json.dumps({
            'type': 'human', 'data': {'content': '测试'},
        })),
    ])
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=dirty_facts)
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        # Never raise ValueError/TypeError.
        await memory_server._run_path_b('悠怡', state)

    # Verify aextract_facts_with_known_pool ran, proving sorting survived.
    fake_fact_store.aextract_facts_with_known_pool.assert_awaited_once()
    captured_known_pool = fake_fact_store.aextract_facts_with_known_pool.await_args.args[2]
    # All five malformed facts are in-window and enter pools; bad values change sorting fallback, not inclusion.
    assert len(captured_known_pool) == 5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_allm_extract_known_pool_returns_none_on_non_list_payload():
    """Regression (Codex P1 round-9 on PR #1408):
    `_allm_extract_facts_with_known_pool` must return None for non-list payloads (such as
    `{\"facts\": [...]}`, plain str, int, etc.), treating them as terminal failure,
    never collapsing them to []—otherwise `aextract_facts_with_known_pool` also returns [],
    and `_run_path_b` treats that as \"successful extraction of 0 facts\", advances the cursor, and permanently skips that window.
    """
    from memory.facts import FactStore
    from unittest.mock import AsyncMock, MagicMock

    fs = FactStore.__new__(FactStore)
    fs._config_manager = MagicMock()
    fs._config_manager.aget_character_data = AsyncMock(return_value=(
        None, None, None, None, {'ai': 'lan', 'human': 'usr'}, None, None, None, None,
    ))
    fs._format_conversation = MagicMock(return_value='对话占位')

    # Malformed LLM payload shapes.
    for bad_payload in [
        {'facts': [{'text': 'wrapped', 'importance': 5}]},  # Wrapped object.
        'random string output',                              # Plain string.
        42,                                                  # Number.
        {},                                                  # Empty object.
        True,                                                # bool
    ]:
        fs._allm_call_with_retries = AsyncMock(return_value=bad_payload)
        out = await fs._allm_extract_facts_with_known_pool('lan', [], [])
        assert out is None, (
            f"非 list payload ({type(bad_payload).__name__}: {bad_payload!r}) "
            f"必须返 None 等同 terminal failure，实际返 {out!r}"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_aextract_facts_with_known_pool_propagates_none_from_bad_shape():
    """End-to-end companion: bad-shape payload → `_allm_extract_facts_with_known
    _pool` returns None → `aextract_facts_with_known_pool` also returns None →
    `_run_path_b` retains the cursor for a retry on the next trigger."""
    from memory.facts import FactStore
    from unittest.mock import AsyncMock, MagicMock

    fs = FactStore.__new__(FactStore)
    # Mock `_allm_extract_facts_with_known_pool` directly to verify wrappers forward None.
    fs._allm_extract_facts_with_known_pool = AsyncMock(return_value=None)
    fs._apersist_new_facts = AsyncMock(return_value=[])  # Must not be called.

    out = await fs.aextract_facts_with_known_pool('lan', [], [])
    assert out is None, (
        f"_allm_extract_facts_with_known_pool 返 None 时 wrapper 必须透传 None，"
        f"实际返 {out!r}"
    )
    # Never persist facts on failure.
    fs._apersist_new_facts.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_stage1_terminal_failure_preserves_cursor():
    """Regression (CodeRabbit + Codex P1 round-2 on PR #1408):
    ``aextract_facts_with_known_pool`` returning None means terminal Stage-1 LLM failure
    (exhausted retries / network / JSON parse, etc.); the cursor must stay unchanged.
    Collapsing None to [] as \"successful extraction of 0 facts\" permanently skips the failed window,
    so AI-aware facts from those messages can never be captured.
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    original_last_b = last_a_msg_ts - timedelta(minutes=20)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': original_last_b,
    }

    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[
        (last_a_msg_ts - timedelta(minutes=5), 's', json.dumps({
            'type': 'human', 'data': {'content': '会话有内容'},
        })),
        (last_a_msg_ts, 's', json.dumps({
            'type': 'ai', 'data': {'content': 'AI 回应'},
        })),
    ])
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=[])
    # Terminal Stage-1 failure returns None, not [].
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=None)

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    # On failure preserve cursor; next B trigger retries the same window.
    assert state['last_b_check_ts'] == original_last_b, (
        f"Stage-1 失败时 cursor 必须保留原值 {original_last_b}，"
        f"实际推到 {state['last_b_check_ts']}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_truncated_window_all_filtered_advances_to_last_fetched():
    """Regression (Codex P2 round-2 on PR #1408):
    When SQL LIMIT truncates results and every fetched row is a system message / empty content
    (all filtered out by ``_extract_role_tagged_messages_from_rows``), the cursor must advance only
    to the last fetched row timestamp, never jump to last_a_msg_ts—otherwise valid human/ai messages
    in the unfetched tail after truncation are permanently skipped.
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=30)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # Truncated SQL returns two system messages, final ts<last_a_msg_ts.
    truncation_boundary = last_a_msg_ts - timedelta(minutes=10)
    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[
        (last_b + timedelta(seconds=10), 's', json.dumps({
            'type': 'system', 'data': {'content': '系统消息1'},
        })),
        (truncation_boundary, 's', json.dumps({
            'type': 'system', 'data': {'content': '系统消息2'},
        })),
    ])
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=[])
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    assert state['last_b_check_ts'] == truncation_boundary, (
        f"截断 + 过滤全空时 cursor 必须 = last fetched ({truncation_boundary})，"
        f"不能跳到 last_a_msg_ts ({last_a_msg_ts})。"
        f"实际: {state['last_b_check_ts']}"
    )
    assert state['last_b_check_ts'] < last_a_msg_ts, (
        "截断尾巴未读到，下次 B 必须能继续覆盖"
    )
    # No extractable messages, so never call LLM.
    fake_fact_store.aextract_facts_with_known_pool.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_known_pool_preserves_microsecond_precision_at_boundary():
    """Regression (CodeRabbit on PR #1408): parsing known_pool created_at must
    retain microsecond precision. Previously, `datetime.fromisoformat(created_at_raw[:19])` truncated
    to seconds, wrongly excluding facts whose `created_at` was 0.x seconds later than `last_b`.
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 10, 0)  # Whole seconds.
    # last_b deliberately includes fractional microseconds.
    last_b = datetime(2026, 5, 18, 12, 0, 0, 500_000)  # T+0.5s
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # Fact created_at is 200ms after last_b; >=last_b must enter pools.
    # Truncating seconds yields T+0.000 before last_b T+0.500, wrongly excluding it.
    on_boundary_fact = {
        'id': 'just_after_last_b',
        'text': 'created_at 比 last_b 晚 200ms',
        'importance': 8,
        'created_at': (last_b + timedelta(microseconds=200_000)).isoformat(),
    }
    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[
        (last_a_msg_ts - timedelta(seconds=1), 's', json.dumps({
            'type': 'human', 'data': {'content': '占位'},
        })),
    ])
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=[on_boundary_fact])
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    fake_fact_store.aextract_facts_with_known_pool.assert_awaited_once()
    captured_pool = fake_fact_store.aextract_facts_with_known_pool.await_args.args[2]
    pool_ids = {f['id'] for f in captured_pool}
    assert 'just_after_last_b' in pool_ids, (
        "微秒精度边界 fact (created_at = last_b + 0.2s) 应入 known_pool，"
        "实际被排除——`[:19]` 截秒回归了"
    )


@pytest.mark.unit
def test_coerce_db_ts_strips_tz_to_naive():
    """Regression (Codex P2 round-8 on PR #1408): `_coerce_db_ts` must normalize
    TZ-aware datetime / \"...+00:00\" strings to naive values.
    All cursors / comparisons use naive semantics; aware results make last_b /
    last_a_msg_ts aware too, and comparison with naive facts.json `created_at`
    raises TypeError, permanently silencing path B.
    """
    from datetime import timezone
    from app.memory_server import _coerce_db_ts

    # Case 1: timezone-aware datetime input.
    aware_dt = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
    out = _coerce_db_ts(aware_dt)
    assert out is not None and out.tzinfo is None, (
        f"TZ-aware datetime 输入必须返 naive，实际 tzinfo={out.tzinfo!r}"
    )
    # Wall-clock value unchanged, treated as naive.
    assert out == datetime(2026, 5, 18, 12, 0, 0)

    # Case 2: ISO string with offset.
    out = _coerce_db_ts('2026-05-18T12:00:00+00:00')
    assert out is not None and out.tzinfo is None, (
        f"TZ-aware ISO 字符串必须返 naive，实际 tzinfo={out.tzinfo!r}"
    )

    # Case 3: naive ISO strings remain naive, without regression.
    out = _coerce_db_ts('2026-05-18T12:00:00.123456')
    assert out is not None and out.tzinfo is None
    assert out == datetime(2026, 5, 18, 12, 0, 0, 123456)

    # Case 4: naive datetime objects remain naive.
    naive_dt = datetime(2026, 5, 18, 12, 0, 0)
    assert _coerce_db_ts(naive_dt) == naive_dt


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_handles_tz_aware_db_rows_without_typeerror():
    """End-to-end pin (Codex P2 round-8): if SQL row timestamps are TZ-aware,
    `_coerce_db_ts` normalization makes last_a_msg_ts / last_b naive, so comparing them with naive
    facts.json created_at does not raise TypeError.
    """
    from datetime import timezone
    from app import memory_server

    # Deliberately aware state.last_a_msg_ts simulates legacy state.
    aware_anchor = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
    state = {'last_a_msg_ts': aware_anchor}

    # SQL timestamps are aware too.
    aware_row_ts = aware_anchor - timedelta(seconds=30)
    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[
        (aware_row_ts, 's', json.dumps({
            'type': 'human', 'data': {'content': '占位'},
        })),
    ])
    # facts.json: naive created_at
    fake_facts = [{
        'id': 'naive_fact',
        'text': 'naive created_at fact',
        'importance': 5,
        'created_at': (aware_anchor.replace(tzinfo=None) - timedelta(minutes=1)).isoformat(),
    }]
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=fake_facts)
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        # Never raise TypeError.
        await memory_server._run_path_b('悠怡', state)

    # Stage-1 calls prove cursor comparison and known-pool assembly survived.
    fake_fact_store.aextract_facts_with_known_pool.assert_awaited_once()
    # Advanced cursors must be naive, preventing persistent aware-state contamination.
    assert state['last_b_check_ts'].tzinfo is None, (
        f"last_b_check_ts 必须是 naive，实际 tzinfo={state['last_b_check_ts'].tzinfo!r}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_known_pool_normalizes_tz_aware_created_at():
    """Regression (Codex P1 round-7 on PR #1408): if import/
    migration writes TZ-aware `created_at` (such as \"...+00:00\") into facts.json, comparison with naive
    last_b raises TypeError and permanently fails _run_path_b → path B becomes silent for that
    character. `_run_path_b` must convert aware values to naive before comparison.
    """
    from app import memory_server
    from datetime import timezone

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=10)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # Three aware facts within utcnow's window; without normalization,
    # `>= last_b` raises TypeError immediately.
    tz_aware_facts = [
        {
            'id': f'imported_{i}',
            'text': f'TZ-aware imported fact {i}',
            'importance': 7,
            'created_at': (
                (last_b + timedelta(minutes=1 + i))
                .replace(tzinfo=timezone.utc)
                .isoformat()
            ),
        }
        for i in range(3)
    ]

    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[
        (last_a_msg_ts - timedelta(seconds=1), 's', json.dumps({
            'type': 'human', 'data': {'content': '占位 user msg'},
        })),
    ])
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=tz_aware_facts)
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        # Never raise TypeError.
        await memory_server._run_path_b('悠怡', state)

    # Stage-1 called, proving created_at comparisons survived.
    fake_fact_store.aextract_facts_with_known_pool.assert_awaited_once()
    captured_pool = fake_fact_store.aextract_facts_with_known_pool.await_args.args[2]
    pool_ids = {f['id'] for f in captured_pool}
    # All three aware facts within wall-clock windows enter pools.
    assert len(pool_ids) == 3, (
        f"TZ-aware fact 应该 normalize 后入 pool，实际 {len(pool_ids)}: {pool_ids}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_known_pool_includes_just_written_a_facts_despite_clock_skew():
    """Regression (CodeRabbit on PR #1408): A's idle/polling delay makes created_at for A facts from
    the batch that just scanned this B window slightly later than last_a_msg_ts. known
    _pool must not use ``created_at <= last_a_msg_ts`` as an upper bound, or the entire latest batch
    of A facts is excluded → B can easily extract duplicate facts from the same window.

    This test constructs window=[last_b, last_a_msg_ts], but A finishes writing those facts at last_a_msg_ts +
    30s (created_at is later than last_a_msg_ts), and asserts that these facts
    must enter known_pool.
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=20)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # Key scenario: delayed A writes make created_at>last_a_msg_ts.
    a_written_after_msg_ts = [
        {'id': f'a_late_{i}', 'text': f'A 在 idle 后才写的 fact {i}',
         'importance': 8,
         # Write delays 30s~90s, normal idle gates/LLM duration.
         'created_at': (last_a_msg_ts + timedelta(seconds=30 + i * 20)).isoformat()}
        for i in range(3)
    ]
    # Control: normally timed in-window facts must also remain.
    a_in_window = [
        {'id': 'a_in', 'text': '窗口内写的 A fact', 'importance': 7,
         'created_at': (last_b + timedelta(minutes=5)).isoformat()},
    ]
    # Control: old pre-window facts excluded by lower bounds.
    a_pre_window = [
        {'id': 'a_old', 'text': 'pre-window fact', 'importance': 10,
         'created_at': (last_b - timedelta(hours=1)).isoformat()},
    ]
    all_facts = a_written_after_msg_ts + a_in_window + a_pre_window

    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[
        (last_a_msg_ts - timedelta(minutes=1), 's', json.dumps({
            'type': 'human', 'data': {'content': '测试'},
        })),
    ])
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=all_facts)
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    fake_fact_store.aextract_facts_with_known_pool.assert_awaited_once()
    captured_pool = fake_fact_store.aextract_facts_with_known_pool.await_args.args[2]
    pool_ids = {f['id'] for f in captured_pool}

    # Delayed A facts with created_at>last_a_msg_ts must be present.
    for f in a_written_after_msg_ts:
        assert f['id'] in pool_ids, (
            f"A idle-delay fact {f['id']} (created_at > last_a_msg_ts) 被错误排除——"
            f"会让 known_pool 对最新一批 A 抽取失效，B 重复抽同窗口。"
            f"实际 pool ids: {pool_ids}"
        )
    # Normal in-window facts present too.
    assert 'a_in' in pool_ids
    # Pre-window facts remain lower-bound filtered.
    assert 'a_old' not in pool_ids, (
        "下界 created_at >= last_b 必须保留，否则会拉入很多无关老 fact 挤掉 pool 名额"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_skipped_for_ai_only_window_no_user_msgs():
    """Design pin: purely proactive / AI-only windows (without human messages) **deliberately** do not
    trigger path B. Path B piggybacks on path A's trigger (no independent scheduling);
    when the user does not participate, A extracts no user_observation and B likewise does not collect AI self-disclosures.

    Product thesis: \"90% carefree + 10% divine intervention\"—AI monologue that the user
    ignores is cheap content and must not automatically become facts that pollute memory.

    Pin through source inspection: the ``if not user_msgs_text:`` branch only marks done and returns;
    it must not call _run_path_b / bump b_tick_counter.
    """
    import inspect
    from app import memory_server

    src = inspect.getsource(memory_server._periodic_signal_extraction_loop)

    user_check_idx = src.find("if not user_msgs_text:")
    assert user_check_idx > 0
    # Stop at next return, delimiting this early-return branch.
    branch_end = src.find("return", user_check_idx)
    branch_src = src[user_check_idx:branch_end + len("return")]

    assert "_run_path_b" not in branch_src, (
        "AI-only 早 return 路径里**禁止**调 _run_path_b——product thesis "
        "明确这是廉价层，不该触发 fact 抽取"
    )
    assert "b_tick_counter" not in branch_src, (
        "AI-only 早 return 路径里**禁止** bump b_tick_counter——counter "
        "只应在 user 有 engagement 时累积"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_same_ts_cluster_overflow_advances_cursor_by_epsilon():
    """Regression (Codex P2 round-3 on PR #1408):
    aretrieve_original_by_timeframe uses inclusive BETWEEN. If a window contains
    >= MAX_AI_AWARE_WINDOW_MSGS rows all at the same timestamp (store_conversation gives all rows
    in one request the same timestamp, bulk imports, etc.), advancing the cursor to last_fetched_ts means
    the next BETWEEN retrieves the same rows again → infinite loop.
    Detect: LIMIT reached AND all fetched rows share one timestamp → add 1μs to the cursor to pass it.
    """
    from app import memory_server
    from config import MAX_AI_AWARE_WINDOW_MSGS

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=20)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # Build MAX_AI_AWARE_WINDOW_MSGS rows sharing ts T, far before last_a_msg_ts,
    # simulating bulk imports/same-batch writes.
    cluster_ts = last_b + timedelta(minutes=2)
    same_ts_rows = [
        (cluster_ts, f'sess_{i}', json.dumps({
            'type': 'human', 'data': {'content': f'msg {i}'},
        }))
        for i in range(MAX_AI_AWARE_WINDOW_MSGS)
    ]

    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(
        return_value=same_ts_rows,
    )
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=[])
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    # cursor strictly >cluster_ts so next BETWEEN never retrieves that same-ts cluster again.
    assert state['last_b_check_ts'] > cluster_ts, (
        f"同 ts 簇 LIMIT 截断时 cursor 必须 +1μs 越过该 ts ({cluster_ts})，"
        f"否则下次 BETWEEN inclusive 再捞同批 → 死循环。"
        f"实际 cursor: {state['last_b_check_ts']}"
    )
    # Advance only one microsecond, not last_a_msg_ts, preserving intervening timestamps.
    assert state['last_b_check_ts'] == cluster_ts + timedelta(microseconds=1), (
        f"epsilon 必须刚好 +1μs，实际推到 {state['last_b_check_ts']}"
    )
    assert state['last_b_check_ts'] < last_a_msg_ts, (
        "epsilon bump 仍应远小于 last_a_msg_ts，让中间窗口正常被下次 B 处理"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_truncated_but_diverse_ts_does_not_epsilon_bump():
    """Companion test: when LIMIT is reached but rows span multiple timestamps, advance the cursor to the last fetched timestamp
    (do not add 1μs, which would skip tail rows exactly at last_fetched_ts).
    """
    from app import memory_server
    from config import MAX_AI_AWARE_WINDOW_MSGS

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=30)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # MAX_AI_AWARE_WINDOW_MSGS evenly distributed timestamps simulate normal truncation.
    rows = [
        (last_b + timedelta(seconds=i), f'sess_{i}', json.dumps({
            'type': 'human', 'data': {'content': f'msg {i}'},
        }))
        for i in range(MAX_AI_AWARE_WINDOW_MSGS)
    ]
    expected_last_ts = rows[-1][0]

    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=rows)
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=[])
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    # No epsilon bump: cursor equals last fetched ts exactly.
    assert state['last_b_check_ts'] == expected_last_ts, (
        f"diverse ts 截断时 cursor 应 = last fetched ({expected_last_ts}) "
        f"无 epsilon bump，实际: {state['last_b_check_ts']}"
    )


@pytest.mark.unit
def test_post_turn_signals_only_bumps_counter_for_user_turn():
    """Design pin: ``_signal_check_record_turn`` bumps only when the user speaks;
    AI-only / proactive turns deliberately do not count—the counter measures user engagement,
    not total messages. Product thesis: \"90% carefree + 10% divine intervention\"; content without
    user confirmation is cheap content and must not trigger a fact-extraction batch.

    Pin through source inspection: calls to ``_signal_check_record_turn`` must be wrapped in an ``if user_msgs:``
    gate.
    """
    import ast
    import inspect
    import textwrap
    from app import memory_server

    src = textwrap.dedent(inspect.getsource(memory_server._run_post_turn_signals))
    tree = ast.parse(src)

    def is_record_call(node):
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_signal_check_record_turn"
        )

    guarded = any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "user_msgs"
        and any(is_record_call(child) for child in ast.walk(node))
        for node in ast.walk(tree)
    )
    assert guarded, (
        "counter bump 必须被 `if user_msgs:` gate——只算 user engagement，"
        "不算 AI-only turn"
    )


@pytest.mark.unit
def test_b_tick_counter_threshold_constant_sane():
    """N_A_TICKS must be >= 1 (never 0, which triggers B every A tick and eliminates piggybacking)."""
    from config import (
        EVIDENCE_AI_AWARE_EVERY_N_A_TICKS,
        MAX_AI_AWARE_WINDOW_MSGS,
        MAX_KNOWN_POOL_FACTS,
    )
    assert EVIDENCE_AI_AWARE_EVERY_N_A_TICKS >= 1
    assert MAX_AI_AWARE_WINDOW_MSGS >= 10  # Extreme-value safeguards.
    assert MAX_KNOWN_POOL_FACTS >= 1


@pytest.mark.unit
def test_signal_check_one_triggers_path_b_after_n_ticks():
    """Source inspection: ``_signal_check_one`` must bump b_tick_counter after A completes successfully
    and call ``_run_path_b`` when it reaches N."""
    import inspect
    from app import memory_server

    src = inspect.getsource(memory_server._periodic_signal_extraction_loop)
    assert 'b_tick_counter' in src, "signal loop 内必须维护 b_tick_counter"
    assert 'EVIDENCE_AI_AWARE_EVERY_N_A_TICKS' in src, (
        "signal loop 必须用 EVIDENCE_AI_AWARE_EVERY_N_A_TICKS 阈值判 B trigger"
    )
    assert '_run_path_b' in src, "signal loop 必须调 _run_path_b"
    assert 'last_a_msg_ts' in src, (
        "signal loop 必须记录 last_a_msg_ts 给 B 当窗口下游边界，"
        "不能用 wall-clock now（race 风险）"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unseal_marker_does_not_requeue_a_fact_another_process_already_consumed(tmp_path):
    """The unseal exemption stops at a fact somebody else already upgraded.

    Two processes can hold the same ai_disclosure fact. If the other one
    upgrades it first and Stage-2 consumes it, disk carries
    ``user_observation`` + ``signal_processed=True``. This process then
    upgrading from its stale cache and honouring its own marker would push that
    True back to False and re-queue an already-consumed fact — the very
    cross-process regression the monotonic read-merge exists to prevent.
    """
    fs = _make_fact_store(facts={})
    fs._config_manager.memory_dir = str(tmp_path)

    # Disk: another process already upgraded and Stage-2 consumed it.
    fs._facts['悠怡'] = [{
        'id': 'fact_shared',
        'text': '博士喜欢三文鱼',
        'source': 'user_observation',
        'signal_processed': True,
        'importance': 8,
        'entity': 'master',
    }]
    fs.save_facts('悠怡')

    # This process's old ai_disclosure cache just upgraded and attached its marker.
    fs._facts['悠怡'] = [{
        'id': 'fact_shared',
        'text': '博士喜欢三文鱼',
        'source': 'user_observation',
        'signal_processed': False,
        fs._SIGNAL_RESET_PENDING: True,
        'importance': 8,
        'entity': 'master',
    }]
    fs.save_facts('悠怡')

    with open(fs._facts_path('悠怡'), encoding='utf-8') as handle:
        persisted = json.load(handle)

    assert persisted[0]['signal_processed'] is True, (
        "磁盘上已经是 user_observation 说明别人升过了，纸条不许把它重新排回 Stage-2"
    )
    assert '_signal_reset_pending' not in persisted[0]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [
    {'text': '缺 id 的 ai 披露', 'source': 'ai_disclosure', 'signal_processed': True},
    {'text': '缺 id 且已吸收', 'source': 'user_observation', 'absorbed': True},
    {'text': '缺 id 且已处理', 'source': 'user_observation', 'signal_processed': True},
    # Nonstrings cannot enter sets: unhashable TypeError bypasses inner
    # JSON/OS-only exception handling just like missing ids.
    {'id': ['a', 'b'], 'text': 'id 是 list 的 ai 披露', 'source': 'ai_disclosure'},
    {'id': {'k': 'v'}, 'text': 'id 是 dict 且已处理', 'source': 'user_observation',
     'signal_processed': True},
    {'id': 12345, 'text': 'id 是数字且已吸收', 'source': 'user_observation', 'absorbed': True},
    {'id': '', 'text': 'id 是空串', 'source': 'ai_disclosure', 'signal_processed': True},
])
async def test_save_survives_disk_rows_without_ids(tmp_path, malformed):
    """A legacy or hand-edited row without ``id`` must not break persistence.

    The read-merge indexes disk rows by id. Reading that field unconditionally
    turns one malformed row into a permanent outage: the KeyError escapes the
    inner handler (which only covers JSON/OS errors), the outer one evicts the
    cache and re-raises, and every later save reloads the same row and dies
    again — no new fact ever reaches disk.
    """
    fs = _make_fact_store(facts={})
    fs._config_manager.memory_dir = str(tmp_path)

    path = fs._facts_path('悠怡')
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump([malformed], handle, ensure_ascii=False)

    fs._facts['悠怡'] = [{
        'id': 'fact_new',
        'text': '博士喜欢三文鱼',
        'source': 'user_observation',
        'signal_processed': False,
        'importance': 8,
        'entity': 'master',
    }]
    fs.save_facts('悠怡')

    with open(path, encoding='utf-8') as handle:
        persisted = json.load(handle)
    assert [f['id'] for f in persisted] == ['fact_new']
    assert persisted[0]['signal_processed'] is False, (
        "磁盘上那条没有 id，不该凭空命中 read-merge 的任何集合"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_id_less_disk_rows_do_not_contaminate_other_id_less_rows(tmp_path):
    """A missing id must not act as a key that every other id-less row matches.

    Indexing the read-merge by ``.get('id')`` without dropping ``None`` puts a
    single key in the set that every id-less in-memory row compares equal to,
    so one stale malformed row on disk would flip unrelated ones to processed.
    """
    fs = _make_fact_store(facts={})
    fs._config_manager.memory_dir = str(tmp_path)

    path = fs._facts_path('悠怡')
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump([{
            'text': '盘上那条没有 id 且已处理',
            'source': 'user_observation',
            'signal_processed': True,
            'absorbed': True,
        }], handle, ensure_ascii=False)

    fs._facts['悠怡'] = [{
        'text': '内存里另一条也没有 id，但没被处理过',
        'source': 'user_observation',
        'signal_processed': False,
        'importance': 6,
        'entity': 'master',
    }]
    fs.save_facts('悠怡')

    with open(path, encoding='utf-8') as handle:
        persisted = json.load(handle)

    assert persisted[0]['signal_processed'] is False, (
        "两条都没有 id 不代表它们是同一条，不该被 read-merge 串到一起"
    )
    assert not persisted[0].get('absorbed'), "absorbed 同理，不该凭空被回写"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_id", [12345, 3.5, True, 0, 0.0, False])
async def test_hashable_legacy_ids_still_keep_their_monotonic_flags(tmp_path, legacy_id):
    """Only unusable ids may be dropped; hashable scalars must still match.

    A legacy row keyed by a bare number matched between cache and disk before,
    so excluding it from the read-merge index would let a stale save push
    ``absorbed`` / ``signal_processed`` back to False — trading the crash on
    malformed ids for quiet re-consumption of already-processed facts.
    """
    fs = _make_fact_store(facts={})
    fs._config_manager.memory_dir = str(tmp_path)

    fs._facts['悠怡'] = [{
        'id': legacy_id,
        'text': '老库里的一条',
        'source': 'user_observation',
        'signal_processed': True,
        'absorbed': True,
    }]
    fs.save_facts('悠怡')

    # Stale caches regress both monotonic fields; persisted values must win during save.
    fs._facts['悠怡'][0]['signal_processed'] = False
    fs._facts['悠怡'][0]['absorbed'] = False
    fs.save_facts('悠怡')

    with open(fs._facts_path('悠怡'), encoding='utf-8') as handle:
        persisted = json.load(handle)
    assert persisted[0]['signal_processed'] is True
    assert persisted[0]['absorbed'] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_duplicate_disk_rows_block_the_unseal_exemption(tmp_path):
    """An id is only 'still ai_disclosure' when every disk row with it says so.

    Hand-edited or reconciled files can carry the same id twice — one row still
    ai_disclosure, one already upgraded and consumed. Reading the two sets
    independently makes the id look unsealed and lets a stale cache push the
    consumed row back to unprocessed.
    """
    fs = _make_fact_store(facts={})
    fs._config_manager.memory_dir = str(tmp_path)

    path = fs._facts_path('悠怡')
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump([
            {'id': 'fact_dup', 'text': '同 id 的旧行', 'source': 'ai_disclosure',
             'signal_processed': True},
            {'id': 'fact_dup', 'text': '同 id 已升级并消费', 'source': 'user_observation',
             'signal_processed': True},
        ], handle, ensure_ascii=False)

    fs._facts['悠怡'] = [{
        'id': 'fact_dup',
        'text': '本进程刚升级并挂了纸条',
        'source': 'user_observation',
        'signal_processed': False,
        fs._SIGNAL_RESET_PENDING: True,
    }]
    fs.save_facts('悠怡')

    with open(path, encoding='utf-8') as handle:
        persisted = json.load(handle)
    assert persisted[0]['signal_processed'] is True, (
        "磁盘上同 id 还有一行已升级并消费，纸条不该放行回写"
    )
