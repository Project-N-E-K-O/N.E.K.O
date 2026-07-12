# -*- coding: utf-8 -*-
"""AI-aware Stage-1 (path B) — 端到端不变量与 contract pin。

设计 background：path A 已有的 SignalLoop 只看 user msgs（PR #1346 之后
ON-mode 唯一 fact 抽取路径），导致 AI 自我披露 + proactive 引入的屏幕/活动
上下文 grounded fact 全失明。path B 在 A 循环里 piggyback 触发，跑 AI-aware
Stage-1（含 user+ai 全消息 + known pool 提示），fact 标 source='ai_disclosure'
不进 Stage-2 evidence loop。

测试覆盖：
1. ``_extract_role_tagged_messages_from_rows``：收 user + ai 双 type，
   渲染 list[dict] 而非 list[str]
2. ``_apersist_new_facts``：source 字段持久化 + ai_disclosure 写盘时
   signal_processed=True（防卡池）
3. ``_apersist_new_facts``：monotonic source upgrade（ai_disclosure
   → user_observation 不可逆 + 重置 signal_processed=False 让 Stage-2
   重新评估）
4. ``aextract_facts_and_detect_signals``：unprocessed pool filter
   ``source != 'ai_disclosure'`` 双重防御
5. Path B trigger cadence：每 ``EVIDENCE_AI_AWARE_EVERY_N_A_TICKS``
   次 A tick 触发一次
6. Path B cold-start lookback：从 ``EVIDENCE_AI_AWARE_EVERY_N_A_TICKS
   × EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES`` 推算，不要新魔法常数

测不到的部分（留 manual / e2e）：
- 实际 LLM 是否正确分配 source（依赖 prompt + 模型）
- known_pool 是否真起到 do-not-repeat 效果（依赖 LLM 听话程度）
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.unit
def test_extract_role_tagged_messages_keeps_user_and_ai():
    """跟 _extract_user_messages_from_rows 形成对偶：path B 必须收 ai msg。"""
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
    assert types == ['human', 'ai', 'ai']  # system 被滤
    assert out[0]['data']['content'] == '主人说的话'
    assert out[1]['data']['content'] == '猫娘自己的话'
    # content list 形态拼成单 str
    assert out[2]['data']['content'] == 'part1 part2'


@pytest.mark.unit
def test_extract_role_tagged_messages_skips_empty_content():
    """空白 content 不该入 list，防 prompt 渲染出空行。"""
    from app.memory_server import _extract_role_tagged_messages_from_rows

    rows = [
        (datetime(2026, 5, 18, 10, 0, 0), 'sess1', json.dumps({
            'type': 'human', 'data': {'content': '   '},  # 纯空白
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
    """Product thesis pin：path B 喂给 Stage-1 的消息必须先 trim 到
    首条 user msg → 末条 user msg 之间（含两端）。首尾的 AI 残段是
    "user 没印证 / 没回应过的廉价层"，不该入 fact。
    """
    from app.memory_server import _trim_to_user_msg_bracket

    # 典型 case：[ai, ai, human, ai, human, ai] → 留 [human, ai, human]
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
    """纯 AI-only 窗口（无 human msg）→ 完全 trim 空。
    Caller 视作廉价层 skip。"""
    from app.memory_server import _trim_to_user_msg_bracket

    msgs = [
        {'type': 'ai', 'data': {'content': '独白 1'}},
        {'type': 'ai', 'data': {'content': '独白 2'}},
    ]
    assert _trim_to_user_msg_bracket(msgs) == []


@pytest.mark.unit
def test_trim_to_user_msg_bracket_single_human_returns_self():
    """只有一条 human msg → bracket 退化为单点。仍然合法（user 有发声）。"""
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
    """首尾本就是 human msg → 不变。"""
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
    """End-to-end pin：_run_path_b 喂给 aextract_facts_with_known_pool
    的 messages 必须已经被 user-msg-bracket trim 过。

    构造窗口：前 2 条 AI + 中间 [user, ai, user] + 后 2 条 AI。
    断言 Stage-1 收到的 messages 只有中间 3 条。
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

    # 验证 Stage-1 被调，且 messages 只含 bracket 内 3 条
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
    # 首尾必须是 user msg
    assert 'user 第一句' in contents[0]
    assert 'user 最后一句' in contents[2]
    # 首尾 AI 残段必须被剥掉
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
    """End-to-end pin：窗口里完全无 user msg → bracket trim 后为空 →
    path B 不调 Stage-1（廉价层 skip），cursor 仍照常推进避免反复扫同窗口。
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

    # Stage-1 不该被调
    fake_fact_store.aextract_facts_with_known_pool.assert_not_awaited()
    # cursor 推到 last fetched row ts（避免下次反复扫同窗口）
    assert state['last_b_check_ts'] == last_row_ts, (
        f"AI-only 窗口跳过后 cursor 应推到 last fetched ({last_row_ts})，"
        f"实际 {state['last_b_check_ts']}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apersist_writes_source_field_default_user_observation():
    """path A 调用方不传 default_source → 落盘 source='user_observation'，
    signal_processed=False（正常进 Stage-2）。"""
    from memory.facts import FactStore

    fs = FactStore.__new__(FactStore)
    fs._config_manager = MagicMock()
    fs._time_indexed = None
    fs._facts = {'悠怡': []}
    fs._locks = {}
    import threading
    fs._locks_guard = threading.Lock()
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
    """path B 调用方传 default_source='ai_disclosure' → 落盘 source 字段
    一致，signal_processed=True（不进 Stage-2 evidence loop）。"""
    from memory.facts import FactStore

    fs = FactStore.__new__(FactStore)
    fs._config_manager = MagicMock()
    fs._time_indexed = None
    fs._facts = {'悠怡': []}
    fs._locks = {}
    import threading
    fs._locks_guard = threading.Lock()
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
    """LLM 显式输出的 source 字段优先于 default_source（trust LLM 的
    per-fact 判断）。"""
    from memory.facts import FactStore

    fs = FactStore.__new__(FactStore)
    fs._config_manager = MagicMock()
    fs._time_indexed = None
    fs._facts = {'悠怡': []}
    fs._locks = {}
    import threading
    fs._locks_guard = threading.Lock()
    fs.asave_facts = AsyncMock(return_value=None)

    # LLM 输出 source='user_observation'，但 caller 传 default='ai_disclosure'
    # 模拟 LLM 判断"虽然在 ai_aware pass 里，但这条 fact 实际靠 user msg
    # 印证的"——这种情况 LLM 标 user_observation 应该被尊重
    extracted = [
        {'text': '博士喜欢咖啡', 'importance': 7, 'entity': 'master',
         'source': 'user_observation'},
    ]
    with patch.object(fs, 'aload_facts', AsyncMock(return_value=[])):
        new_facts = await fs._apersist_new_facts(
            '悠怡', extracted, default_source='ai_disclosure',
        )
    assert new_facts[0]['source'] == 'user_observation'
    # 又因为 source 是 user_observation，signal_processed 应该 False
    assert new_facts[0]['signal_processed'] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apersist_monotonic_source_upgrade_ai_to_user():
    """SHA-256 撞已有 ai_disclosure fact + 新 fact source=user_observation
    → in-place 升级 existing.source + 重置 signal_processed=False。"""
    from memory.facts import FactStore
    import hashlib

    fs = FactStore.__new__(FactStore)
    fs._config_manager = MagicMock()
    fs._time_indexed = None
    fs._facts = {'悠怡': []}
    fs._locks = {}
    import threading
    fs._locks_guard = threading.Lock()

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

    # 不返新 fact（SHA-256 撞了 skip 写），但 in-place 升级了 existing
    assert new_facts == []
    assert existing_fact['source'] == 'user_observation', "user 印证后应升级"
    assert existing_fact['signal_processed'] is False, (
        "升级后必须重置 signal_processed=False 让 Stage-2 重新评估"
    )
    fs.asave_facts.assert_awaited_once_with('悠怡')


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apersist_monotonic_source_upgrade_within_same_batch():
    """Regression (Codex P2 round-10 on PR #1408)：同一次 Stage-1 extracted
    payload 里若同 text 出现两次（先 ai_disclosure 后 user_observation），
    `hash_to_existing` 必须在第一次写入后同步更新——否则第二次命中
    `content_hash in existing_hashes` 时 `hash_to_existing.get()` 返 None，
    monotonic upgrade 路径被跳过，user_observation 升级被静默丢弃。
    """
    from memory.facts import FactStore

    fs = FactStore.__new__(FactStore)
    fs._config_manager = MagicMock()
    fs._time_indexed = None
    fs._facts = {'悠怡': []}
    fs._locks = {}
    import threading
    fs._locks['悠怡'] = threading.RLock()
    fs.asave_facts = AsyncMock()

    # 模拟单次 Stage-1 payload 包含同 text 两条：先 ai_disclosure 后 user_observation
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

    # 落盘只有 1 条（dedup 生效），且 source 升级到 user_observation
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
async def test_apersist_no_downgrade_user_to_ai():
    """反向：撞已有 user_observation fact + 新 fact source=ai_disclosure
    → existing 不动（user 印证不可逆退回 ai_disclosure）。"""
    from memory.facts import FactStore
    import hashlib

    fs = FactStore.__new__(FactStore)
    fs._config_manager = MagicMock()
    fs._time_indexed = None
    fs._facts = {'悠怡': []}
    fs._locks = {}
    import threading
    fs._locks_guard = threading.Lock()

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

    # 不能降级
    assert existing_fact['source'] == 'user_observation'
    # signal_processed 也不该被乱动
    assert existing_fact['signal_processed'] is True
    # 没改任何东西 → 不该 save
    fs.asave_facts.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stage2_filters_out_ai_disclosure_facts():
    """Stage-2 unprocessed pool 必须 filter ``source='ai_disclosure'``。
    双重防御（写盘 signal_processed=True 是第一层；这是第二层）。"""
    from memory.facts import FactStore

    fs = FactStore.__new__(FactStore)
    fs._config_manager = MagicMock()
    fs._time_indexed = None
    fs._facts = {}
    fs._locks = {}
    import threading
    fs._locks_guard = threading.Lock()

    # 模拟 facts.json 三条 fact：一条 user_observation 未处理（应入 Stage-2 池），
    # 一条 ai_disclosure 未处理（**不该**入池，即使 signal_processed=False bug），
    # 一条无 source 字段未处理（老数据，default user_observation 视图，应入池）
    fake_facts = [
        {'id': 'a', 'source': 'user_observation', 'signal_processed': False,
         'importance': 8, 'created_at': '2026-05-18T10:00:00'},
        {'id': 'b', 'source': 'ai_disclosure', 'signal_processed': False,
         'importance': 7, 'created_at': '2026-05-18T10:00:01'},
        {'id': 'c', 'signal_processed': False,  # 无 source 字段（老数据）
         'importance': 6, 'created_at': '2026-05-18T10:00:02'},
    ]

    fs._allm_extract_facts = AsyncMock(return_value=[])  # 不产生新 fact
    fs._apersist_new_facts = AsyncMock(return_value=[])
    fs.aload_facts = AsyncMock(return_value=fake_facts)
    fs._aload_signal_targets = AsyncMock(return_value=[
        {'id': 'obs1', 'text': '观察', 'target_type': 'reflection'},
    ])
    fs._allm_detect_signals = AsyncMock(return_value=[])  # signals=[] but ran

    # _allm_detect_signals 被调用时传的 batch 就是过 filter 后的 unprocessed。
    # 这里直接 await 不绑变量——测试只关心 mock call 参数，不关心三元 return
    # （CodeQL 对 `_xxx` 前缀仍判 unused，故彻底不绑）。
    await fs.aextract_facts_and_detect_signals('悠怡', messages=[])

    # 验证 _allm_detect_signals 被调，且其 batch 参数里**没有** b（ai_disclosure）
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
    """B cold start 时 last_b 推算 = last_a_msg_ts - max(N_TICKS, N_TURNS)×IDLE_MIN，
    不需要独立的 LOOKBACK_MINUTES config。
    取 max 而不是单 N_TICKS 是为了 cover sparse turn 场景下 A 两 tick 跨度
    >> IDLE_MIN 的情况——若只看 N_TICKS×IDLE_MIN，cold start last_b 会落在
    A 真正处理过的范围之内，B 永久 skip 那段前的 AI-only msg（Codex P2
    round-6 on PR #1408）。
    """
    from app import memory_server
    from config import (
        EVIDENCE_AI_AWARE_EVERY_N_A_TICKS,
        EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS,
        EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES,
    )

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    state = {'last_a_msg_ts': last_a_msg_ts}  # 没有 last_b_check_ts

    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[])
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=[])
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    # 验证 aretrieve_original_by_timeframe 被调，start_time 正好是
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
    """Regression (Codex P2 round-6 on PR #1408)：cold-start lookback 必须
    取 max(N_TICKS, N_TURNS) × IDLE_MIN，而不能只用 N_TICKS。否则在 sparse
    turn 场景（turn-count gate 触发的 A tick 跨度 >> piggyback 估算）下，
    cold start last_b 落在 A 真正处理范围之内 → B 永久 skip 那段前的
    AI-only msg。

    用 N_TURNS 远大于 N_TICKS（默认 10 vs 3）来锁死这个语义：若实现只用
    N_TICKS，本测试会失败。
    """
    from app import memory_server
    from config import (
        EVIDENCE_AI_AWARE_EVERY_N_A_TICKS,
        EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS,
        EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES,
    )

    # 仅在默认配置 N_TURNS > N_TICKS 时此 regression 才有意义
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
    # 关键：实际用的 lookback 必须 >= N_TURNS × IDLE_MIN（严格 > N_TICKS × IDLE_MIN）
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
    """A 还没成功处理过任何 batch（last_a_msg_ts is None）时 B 无源可看，
    应直接返回不报错。"""
    from app import memory_server

    state = {}  # 完全空 state（cold launcher start）
    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager):
        await memory_server._run_path_b('悠怡', state)

    # 无 last_a_msg_ts → B 不该读 SQL
    fake_time_manager.aretrieve_original_by_timeframe.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_full_window_advances_cursor_to_last_fetched_eq_last_a_msg_ts():
    """无截断（rows 全部覆盖到 last_a_msg_ts）时 cursor = last fetched 恰好 =
    last_a_msg_ts。语义上等价"推到 A 处理过的最晚点"。

    （跟 test_path_b_truncated_window_... 形成对偶：那条覆盖截断情况，
    cursor 推到 last fetched < last_a_msg_ts；这条覆盖无截断情况。）
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_a_msg_ts - timedelta(minutes=20),
    }

    fake_time_manager = MagicMock()
    # 最后一行 ts 就是 last_a_msg_ts（无截断的正常情况）
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

    # 无截断时 last fetched == last_a_msg_ts，cursor 推到此值
    assert state['last_b_check_ts'] == last_a_msg_ts, (
        f"无截断时 cursor (= last fetched) 应等于 last_a_msg_ts={last_a_msg_ts}，"
        f"实际 last_b_check_ts={state['last_b_check_ts']}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_empty_rows_preserves_cursor_for_retry():
    """Regression (Codex P1 round-5 on PR #1408)：
    `aretrieve_original_by_timeframe` 在 SQL exception / engine init 失败 /
    维护态都 swallow + 返 []，从 caller 端无法区分"真空窗口"vs"transient
    读失败"。如果在 rows 空时把 cursor 推到 last_a_msg_ts，整段 [last_b,
    last_a_msg_ts] 会在 SQL transient 失败下被永久 skip → path B 静默丢
    fact。保守做法：rows 空时 cursor 不推，下次 trigger 重试同窗口。

    代价：真正的空窗口下次 B trigger 还会再 query 一次空（SQLite 空范围
    scan 极快，常数代价）；A 推进 last_a_msg_ts 后窗口范围会增长但被
    MAX_AI_AWARE_WINDOW_MSGS LIMIT 兜底。
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    original_last_b = last_a_msg_ts - timedelta(minutes=20)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': original_last_b,
    }

    fake_time_manager = MagicMock()
    # 模拟 SQL transient 失败（time_manager swallow 返 []，跟真空窗口同形态）
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[])

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager):
        await memory_server._run_path_b('悠怡', state)

    # 关键契约：cursor 必须保持原值，下次 B trigger 重试该窗口
    assert state['last_b_check_ts'] == original_last_b, (
        f"rows 空时 cursor 必须保留原值 {original_last_b} (transient SQL 失败保护)，"
        f"实际推到 {state['last_b_check_ts']}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_filters_known_pool_by_window_and_caps():
    """已知池：只看 ``created_at >= last_b`` 下界（不设上界——见
    `test_path_b_known_pool_includes_just_written_a_facts_despite_clock_skew`
    的 clock-skew 契约），按 importance DESC，cap 到 MAX_KNOWN_POOL_FACTS。
    """
    from app import memory_server
    from config import MAX_KNOWN_POOL_FACTS

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=20)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # 构造 35 条窗口内 fact（间隔 30s 把全部塞进 20-min 窗口）+ 20 条窗口外
    in_window_facts = [
        {'id': f'in_{i}', 'text': f'fact in {i}',
         'importance': i % 10 + 1,
         'created_at': (last_b + timedelta(seconds=i * 30)).isoformat()}
        for i in range(35)  # 35 条 in window > MAX_KNOWN_POOL_FACTS (30)
    ]
    out_window_facts = [
        {'id': f'out_{i}', 'text': f'fact out {i}',
         'importance': 10,  # 高 importance 但不在窗口
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

    # 1. cap 生效（最多 MAX_KNOWN_POOL_FACTS = 30 条）
    assert len(captured_known_pool) == MAX_KNOWN_POOL_FACTS

    # 2. 全部来自窗口内（id 都是 in_*）
    for f in captured_known_pool:
        assert f['id'].startswith('in_'), (
            f"out-of-window fact 不该入池，命中: {f['id']}"
        )

    # 3. 按 importance DESC（前几个 importance 最高）
    importances = [f['importance'] for f in captured_known_pool]
    assert importances == sorted(importances, reverse=True), (
        f"已知池必须按 importance DESC 排，实际: {importances}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_truncated_window_advances_cursor_to_last_fetched_row():
    """Regression (Codex P1 PR #1408)：当窗口里消息数 > MAX_AI_AWARE_WINDOW_MSGS
    时 SQL LIMIT 只取最早 N 行，cursor 必须推到**实际取到的最后一行 ts**，
    不是 window 原本的 last_a_msg_ts。否则未取到的尾巴永久 skip → path B
    对那段 burst 静默失明。
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=30)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # 模拟 SQL 返回截断的 rows：只到 last_a_msg_ts - 10 min（实际窗口是
    # 30 min，但 LIMIT 把 ts 最早的部分截到 10 min 处就停了）
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
    # 关键：下次 B trigger 必须能从这里继续 = 还没追上 last_a_msg_ts
    assert state['last_b_check_ts'] < last_a_msg_ts, (
        "truncated 窗口 + 未取尾巴 → cursor 不该追上 A，下次 B 继续处理"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_known_pool_sort_tolerates_malformed_importance():
    """Regression (Codex P2 PR #1408)：legacy / 手改 facts.json 里
    'importance': "high" / None / list 等脏值不该让整个 path B sort 挂
    （raw int(...) cast → ValueError → path B 对该角色永久哑火）。
    用 safe_importance 兜底。
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=20)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # 构造一批脏 importance（每种异常类型混入）
    dirty_facts = [
        {'id': 'normal', 'text': 't', 'importance': 8,
         'created_at': (last_b + timedelta(minutes=1)).isoformat()},
        {'id': 'str_high', 'text': 't', 'importance': "high",  # ❌ ValueError
         'created_at': (last_b + timedelta(minutes=2)).isoformat()},
        {'id': 'none_imp', 'text': 't', 'importance': None,
         'created_at': (last_b + timedelta(minutes=3)).isoformat()},
        {'id': 'list_imp', 'text': 't', 'importance': [1, 2, 3],  # ❌ TypeError
         'created_at': (last_b + timedelta(minutes=4)).isoformat()},
        {'id': 'missing_imp', 'text': 't',  # 字段缺失
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
        # 不该抛 ValueError / TypeError
        await memory_server._run_path_b('悠怡', state)

    # 验证 aextract_facts_with_known_pool 被调（说明 sort 没挂）
    fake_fact_store.aextract_facts_with_known_pool.assert_awaited_once()
    captured_known_pool = fake_fact_store.aextract_facts_with_known_pool.await_args.args[2]
    # 5 条脏 fact 全在窗口内，都应该进 pool（脏值不丢，只是排序 fallback）
    assert len(captured_known_pool) == 5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_allm_extract_known_pool_returns_none_on_non_list_payload():
    """Regression (Codex P1 round-9 on PR #1408)：
    `_allm_extract_facts_with_known_pool` 收到非 list payload (如
    `{"facts": [...]}`、纯 str、int 等) 必须返 None 等同 terminal failure，
    不能折叠成 []——否则 `aextract_facts_with_known_pool` 会再继续返 []，
    `_run_path_b` 把它当成"成功 0 抽"推 cursor，那段窗口永久 skip。
    """
    from memory.facts import FactStore
    from unittest.mock import AsyncMock, MagicMock

    fs = FactStore.__new__(FactStore)
    fs._config_manager = MagicMock()
    fs._config_manager.aget_character_data = AsyncMock(return_value=(
        None, None, None, None, {'ai': 'lan', 'human': 'usr'}, None, None, None, None,
    ))
    fs._format_conversation = MagicMock(return_value='对话占位')

    # 各种 LLM 错形态 payload
    for bad_payload in [
        {'facts': [{'text': 'wrapped', 'importance': 5}]},  # 对象包一层
        'random string output',                              # 纯字符串
        42,                                                  # 数字
        {},                                                  # 空对象
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
    """End-to-end 配套：bad-shape payload → `_allm_extract_facts_with_known
    _pool` 返 None → `aextract_facts_with_known_pool` 也返 None →
    `_run_path_b` 保留 cursor 下次 trigger 重试。"""
    from memory.facts import FactStore
    from unittest.mock import AsyncMock, MagicMock

    fs = FactStore.__new__(FactStore)
    # `_allm_extract_facts_with_known_pool` 直接 mock，验证 wrapper 透传 None
    fs._allm_extract_facts_with_known_pool = AsyncMock(return_value=None)
    fs._apersist_new_facts = AsyncMock(return_value=[])  # 不该被调

    out = await fs.aextract_facts_with_known_pool('lan', [], [])
    assert out is None, (
        f"_allm_extract_facts_with_known_pool 返 None 时 wrapper 必须透传 None，"
        f"实际返 {out!r}"
    )
    # persist 不该被调（失败时 fact 不该落盘）
    fs._apersist_new_facts.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_stage1_terminal_failure_preserves_cursor():
    """Regression (CodeRabbit + Codex P1 round-2 on PR #1408)：
    ``aextract_facts_with_known_pool`` 返 None 表示 Stage-1 LLM 终态失败
    （重试耗尽 / network / JSON parse 等），cursor 必须保留不推进。
    若把 None 折叠成 [] 当成"成功 0 抽"，失败窗口会被永久 skip，那批
    msg 的 AI-aware fact 永远抓不到。
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
    # Stage-1 LLM 终态失败 → 返 None（不是 []）
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=None)

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    # 关键契约：失败时 cursor 必须保持原值，下次 B trigger 重试同窗口
    assert state['last_b_check_ts'] == original_last_b, (
        f"Stage-1 失败时 cursor 必须保留原值 {original_last_b}，"
        f"实际推到 {state['last_b_check_ts']}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_truncated_window_all_filtered_advances_to_last_fetched():
    """Regression (Codex P2 round-2 on PR #1408)：
    SQL LIMIT 截断 + 取到的 rows 全是 system msg / 空 content（被
    ``_extract_role_tagged_messages_from_rows`` 过滤光）时，cursor 必须只
    推到 last fetched row ts，不能跳到 last_a_msg_ts——否则截断后未取
    到的尾巴里若有有效 human/ai msg 会被永久 skip。
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=30)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # 模拟 SQL 截断：返 2 行但都是 system msg，最后一行 ts < last_a_msg_ts
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
    # 而且不该调 LLM（没消息可抽）
    fake_fact_store.aextract_facts_with_known_pool.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_known_pool_preserves_microsecond_precision_at_boundary():
    """Regression (CodeRabbit on PR #1408)：known_pool 的 created_at 解析必
    须保留微秒精度。先前 `datetime.fromisoformat(created_at_raw[:19])` 截
    到秒会让"`created_at` 比 `last_b` 晚 0.x 秒"的 fact 被误判出窗口。
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 10, 0)  # 整秒
    # last_b 故意带微秒小数
    last_b = datetime(2026, 5, 18, 12, 0, 0, 500_000)  # T+0.5s
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # 关键 fact: created_at 比 last_b 晚 200ms（仍 >= last_b 应该进 pool）
    # 若截秒，fact 会被解析成 T+0.000，比 last_b T+0.500 早 → 误排
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
    """Regression (Codex P2 round-8 on PR #1408)：`_coerce_db_ts` 必须把
    TZ-aware datetime / "...+00:00" 字符串都归一化成 naive。
    所有 cursor / 比较都按 naive 语义工作，aware 出来会让 last_b /
    last_a_msg_ts 也 aware，跟 naive facts.json `created_at` 比较时
    抛 TypeError 永久哑火 path B。
    """
    from datetime import timezone
    from app.memory_server import _coerce_db_ts

    # Case 1: TZ-aware datetime 对象输入
    aware_dt = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
    out = _coerce_db_ts(aware_dt)
    assert out is not None and out.tzinfo is None, (
        f"TZ-aware datetime 输入必须返 naive，实际 tzinfo={out.tzinfo!r}"
    )
    # 时钟值不变（口径上当 naive 处理）
    assert out == datetime(2026, 5, 18, 12, 0, 0)

    # Case 2: ISO 字符串带 offset 输入
    out = _coerce_db_ts('2026-05-18T12:00:00+00:00')
    assert out is not None and out.tzinfo is None, (
        f"TZ-aware ISO 字符串必须返 naive，实际 tzinfo={out.tzinfo!r}"
    )

    # Case 3: naive ISO 字符串保持 naive（无回归）
    out = _coerce_db_ts('2026-05-18T12:00:00.123456')
    assert out is not None and out.tzinfo is None
    assert out == datetime(2026, 5, 18, 12, 0, 0, 123456)

    # Case 4: naive datetime 对象保持 naive
    naive_dt = datetime(2026, 5, 18, 12, 0, 0)
    assert _coerce_db_ts(naive_dt) == naive_dt


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_handles_tz_aware_db_rows_without_typeerror():
    """End-to-end pin (Codex P2 round-8)：若 SQL row ts 是 TZ-aware，
    `_coerce_db_ts` 归一化后 last_a_msg_ts / last_b 都是 naive，跟 naive
    facts.json created_at 比较不抛 TypeError。
    """
    from datetime import timezone
    from app import memory_server

    # state.last_a_msg_ts 故意构造成 aware（模拟旧代码遗留 state）
    aware_anchor = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
    state = {'last_a_msg_ts': aware_anchor}

    # SQL row ts 也是 aware
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
        # 不该抛 TypeError
        await memory_server._run_path_b('悠怡', state)

    # Stage-1 被调说明 cursor 比较 + known_pool 构建都没炸
    fake_fact_store.aextract_facts_with_known_pool.assert_awaited_once()
    # 推进后的 cursor 必须是 naive（避免 state 里残留 aware 继续污染）
    assert state['last_b_check_ts'].tzinfo is None, (
        f"last_b_check_ts 必须是 naive，实际 tzinfo={state['last_b_check_ts'].tzinfo!r}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_known_pool_normalizes_tz_aware_created_at():
    """Regression (Codex P1 round-7 on PR #1408)：facts.json 里若被 import/
    migration 写入了 TZ-aware `created_at`（如 "...+00:00"），跟 naive 的
    last_b 比较会抛 TypeError 让 _run_path_b 永久 fail → path B 对该角色
    哑火。`_run_path_b` 必须把 aware 转 naive 再比较。
    """
    from app import memory_server
    from datetime import timezone

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=10)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # 三条 TZ-aware fact，在窗口内（utcnow 视角）。若实现不 normalize，
    # `>= last_b` 立即抛 TypeError。
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
        # 不该抛 TypeError
        await memory_server._run_path_b('悠怡', state)

    # Stage-1 被调（说明 created_at 比较没炸）
    fake_fact_store.aextract_facts_with_known_pool.assert_awaited_once()
    captured_pool = fake_fact_store.aextract_facts_with_known_pool.await_args.args[2]
    pool_ids = {f['id'] for f in captured_pool}
    # 3 条 TZ-aware fact（wall-clock 视角在窗口内）都应该进 pool
    assert len(pool_ids) == 3, (
        f"TZ-aware fact 应该 normalize 后入 pool，实际 {len(pool_ids)}: {pool_ids}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_known_pool_includes_just_written_a_facts_despite_clock_skew():
    """Regression (CodeRabbit on PR #1408)：A 的 idle/polling 延迟让"刚扫
    完本 B 窗口"那批 A facts 的 created_at 普遍略晚于 last_a_msg_ts。known
    _pool 不该用 ``created_at <= last_a_msg_ts`` 做上界过滤，否则最新一批
    A facts 整批被排除 → B 容易和 A 重复抽同一窗口。

    本测试构造：window=[last_b, last_a_msg_ts]，但 A 在 last_a_msg_ts +
    30s 才写完那批 fact（created_at 比 last_a_msg_ts 晚），断言这些 fact
    必须进 known_pool。
    """
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=20)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # 关键场景：A 写入延迟 → created_at 在 last_a_msg_ts 之后
    a_written_after_msg_ts = [
        {'id': f'a_late_{i}', 'text': f'A 在 idle 后才写的 fact {i}',
         'importance': 8,
         # 写入延迟 30s ~ 90s（普通 idle gate / LLM call 时长）
         'created_at': (last_a_msg_ts + timedelta(seconds=30 + i * 20)).isoformat()}
        for i in range(3)
    ]
    # 对照组：窗口内正常时间写的 fact，必须也在
    a_in_window = [
        {'id': 'a_in', 'text': '窗口内写的 A fact', 'importance': 7,
         'created_at': (last_b + timedelta(minutes=5)).isoformat()},
    ]
    # 对照组：窗口前的 old fact，不该在（下界过滤）
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

    # 关键断言：A 写入延迟产生的 facts（created_at > last_a_msg_ts）必须在
    for f in a_written_after_msg_ts:
        assert f['id'] in pool_ids, (
            f"A idle-delay fact {f['id']} (created_at > last_a_msg_ts) 被错误排除——"
            f"会让 known_pool 对最新一批 A 抽取失效，B 重复抽同窗口。"
            f"实际 pool ids: {pool_ids}"
        )
    # 窗口内正常 fact 也在
    assert 'a_in' in pool_ids
    # 窗口前 old fact 仍被下界过滤掉
    assert 'a_old' not in pool_ids, (
        "下界 created_at >= last_b 必须保留，否则会拉入很多无关老 fact 挤掉 pool 名额"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_skipped_for_ai_only_window_no_user_msgs():
    """Design pin：纯 proactive / AI-only 窗口（没 human msg）**故意**不
    触发 path B。Path B 是 piggyback path A 的 trigger 跑（不独立调度），
    user 没参与的窗口里 A 不抽 user_observation，B 也跟着不拣 AI 自我披露。

    Product thesis："90% 没心没肺 + 10% 神明降临"——AI 自言自语 + user
    不搭理的内容是廉价层，不该自动当 fact 沉淀污染 memory。

    源码扫描钉死：``if not user_msgs_text:`` 分支只 mark_done + return，
    禁止在该分支调 _run_path_b / bump b_tick_counter。
    """
    import inspect
    from app import memory_server

    src = inspect.getsource(memory_server._periodic_signal_extraction_loop)

    user_check_idx = src.find("if not user_msgs_text:")
    assert user_check_idx > 0
    # 截到下一个 return（该早 return 分支的边界）
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
    """Regression (Codex P2 round-3 on PR #1408)：
    aretrieve_original_by_timeframe 用 inclusive BETWEEN，若窗口里
    >= MAX_AI_AWARE_WINDOW_MSGS 行全在同一 ts（store_conversation 给一次
    请求所有 row 同 ts，bulk import 等），cursor 推到 last_fetched_ts 后
    下次 BETWEEN 仍把这批 row 全捞回来 → 无限循环。
    检测：LIMIT 拉满 AND 所有 fetched row 同 ts → cursor +1μs 越过该 ts。
    """
    from app import memory_server
    from config import MAX_AI_AWARE_WINDOW_MSGS

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=20)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # 构造 MAX_AI_AWARE_WINDOW_MSGS 行全在同一 ts T（远早于 last_a_msg_ts，
    # 模拟 bulk import / 同 batch 写入）
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

    # 关键断言：cursor 严格 > cluster_ts，下次 BETWEEN 不会再捞同 ts 簇
    assert state['last_b_check_ts'] > cluster_ts, (
        f"同 ts 簇 LIMIT 截断时 cursor 必须 +1μs 越过该 ts ({cluster_ts})，"
        f"否则下次 BETWEEN inclusive 再捞同批 → 死循环。"
        f"实际 cursor: {state['last_b_check_ts']}"
    )
    # 且只越过 1 微秒（不滥推到 last_a_msg_ts，否则中间正常的 ts 也被 skip）
    assert state['last_b_check_ts'] == cluster_ts + timedelta(microseconds=1), (
        f"epsilon 必须刚好 +1μs，实际推到 {state['last_b_check_ts']}"
    )
    assert state['last_b_check_ts'] < last_a_msg_ts, (
        "epsilon bump 仍应远小于 last_a_msg_ts，让中间窗口正常被下次 B 处理"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_truncated_but_diverse_ts_does_not_epsilon_bump():
    """对偶 test：LIMIT 拉满但 rows 跨多个 ts 时，cursor 推到 last fetched 即可
    （不该 +1μs 越过——否则会 skip 掉刚好在 last_fetched_ts 的 tail row）。
    """
    from app import memory_server
    from config import MAX_AI_AWARE_WINDOW_MSGS

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=30)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # MAX_AI_AWARE_WINDOW_MSGS 行，ts 均匀分布（不同 ts，模拟正常截断）
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

    # 不该 epsilon bump——cursor 必须恰好 = last fetched ts
    assert state['last_b_check_ts'] == expected_last_ts, (
        f"diverse ts 截断时 cursor 应 = last fetched ({expected_last_ts}) "
        f"无 epsilon bump，实际: {state['last_b_check_ts']}"
    )


@pytest.mark.unit
def test_post_turn_signals_only_bumps_counter_for_user_turn():
    """Design pin：``_signal_check_record_turn`` 只在 user 发声时 bump，
    AI-only / proactive turn 故意不算——counter 是 user engagement 信号，
    不是消息总数。Product thesis："90% 没心没肺 + 10% 神明降临"，没有
    user 印证的内容是廉价层，不该触发 fact 抽取 batch。

    源码扫描钉死：``_signal_check_record_turn`` 调用必须被 ``if user_msgs:``
    gate 包裹。
    """
    import inspect
    from app import memory_server

    src = inspect.getsource(memory_server._run_post_turn_signals)

    record_idx = src.find("_signal_check_record_turn(lanlan_name)")
    assert record_idx > 0, "_signal_check_record_turn 调用必须存在"

    # 往前找最近的非空 `if ...` 行：必须是 `if user_msgs:`
    preceding = src[max(0, record_idx - 200):record_idx]
    lines = [ln.strip() for ln in preceding.split("\n") if ln.strip()]
    last_if = None
    for ln in reversed(lines):
        if ln.startswith("if "):
            last_if = ln
            break
    assert last_if is not None, "counter bump 紧上方必须有 if 条件"
    assert "user_msgs" in last_if, (
        f"counter bump 必须被 `if user_msgs:` gate——只算 user engagement，"
        f"不算 AI-only turn。当前 if: {last_if!r}"
    )


@pytest.mark.unit
def test_b_tick_counter_threshold_constant_sane():
    """N_A_TICKS 必须 >= 1（不能 0 否则每 A tick 都触发 B，退化成无 piggyback）。"""
    from config import (
        EVIDENCE_AI_AWARE_EVERY_N_A_TICKS,
        MAX_AI_AWARE_WINDOW_MSGS,
        MAX_KNOWN_POOL_FACTS,
    )
    assert EVIDENCE_AI_AWARE_EVERY_N_A_TICKS >= 1
    assert MAX_AI_AWARE_WINDOW_MSGS >= 10  # 极端值防呆
    assert MAX_KNOWN_POOL_FACTS >= 1


@pytest.mark.unit
def test_signal_check_one_triggers_path_b_after_n_ticks():
    """源码扫描：``_signal_check_one`` 必须在 A 成功跑完后 bump b_tick_counter
    并在达 N 时调 ``_run_path_b``。"""
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
