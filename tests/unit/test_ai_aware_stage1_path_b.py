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
    # 三元 return 故意全用 _ 前缀——测试关心的是 mock call 参数，不是返回值。
    _persisted, _signals, _batch_ids = await fs.aextract_facts_and_detect_signals(
        '悠怡', messages=[],
    )

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
    """B cold start 时 last_b 推算 = last_a_msg_ts - N×IDLE_MIN，
    不需要独立的 LOOKBACK_MINUTES config。"""
    from app import memory_server
    from config import (
        EVIDENCE_AI_AWARE_EVERY_N_A_TICKS,
        EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES,
    )

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    state = {'last_a_msg_ts': last_a_msg_ts}  # 没有 last_b_check_ts

    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[])
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=[])
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=[])

    with patch.object(memory_server, 'time_manager', fake_time_manager), \
         patch.object(memory_server, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    # 验证 aretrieve_original_by_timeframe 被调，start_time 正好是
    # last_a_msg_ts - N × IDLE_MINUTES（cold-start lookback 推算）
    fake_time_manager.aretrieve_original_by_timeframe.assert_awaited_once()
    call_kwargs = fake_time_manager.aretrieve_original_by_timeframe.await_args
    start_time = call_kwargs.args[1]
    end_time = call_kwargs.args[2]
    expected_lookback = timedelta(
        minutes=EVIDENCE_AI_AWARE_EVERY_N_A_TICKS * EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES
    )
    assert start_time == last_a_msg_ts - expected_lookback, (
        f"cold start lookback 必须 = N×IDLE_MIN = {expected_lookback}, "
        f"实际 start={start_time}, end={end_time}, last_a={last_a_msg_ts}"
    )
    assert end_time == last_a_msg_ts, (
        "B 窗口下游边界必须是 last_a_msg_ts（A 实际处理过的最晚 msg），"
        "不是 wall-clock now"
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

    with patch.object(memory_server, 'time_manager', fake_time_manager):
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

    with patch.object(memory_server, 'time_manager', fake_time_manager), \
         patch.object(memory_server, 'fact_store', fake_fact_store):
        await memory_server._run_path_b('悠怡', state)

    # 无截断时 last fetched == last_a_msg_ts，cursor 推到此值
    assert state['last_b_check_ts'] == last_a_msg_ts, (
        f"无截断时 cursor (= last fetched) 应等于 last_a_msg_ts={last_a_msg_ts}，"
        f"实际 last_b_check_ts={state['last_b_check_ts']}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_window_empty_still_advances_cursor():
    """B 窗口内无 msg（A 处理过但都是 system msg 之类）时，cursor 也要推进。
    否则下次 B trigger 又会扫同窗口（永远空跑）。"""
    from app import memory_server

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_a_msg_ts - timedelta(minutes=20),
    }

    fake_time_manager = MagicMock()
    # 窗口完全空
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=[])

    with patch.object(memory_server, 'time_manager', fake_time_manager):
        await memory_server._run_path_b('悠怡', state)

    assert state['last_b_check_ts'] == last_a_msg_ts


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_b_filters_known_pool_by_window_and_caps():
    """已知池：只取 created_at ∈ [last_b, last_a_msg_ts] 的 fact，
    按 importance DESC，cap 到 MAX_KNOWN_POOL_FACTS。"""
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

    with patch.object(memory_server, 'time_manager', fake_time_manager), \
         patch.object(memory_server, 'fact_store', fake_fact_store):
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

    with patch.object(memory_server, 'time_manager', fake_time_manager), \
         patch.object(memory_server, 'fact_store', fake_fact_store):
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

    with patch.object(memory_server, 'time_manager', fake_time_manager), \
         patch.object(memory_server, 'fact_store', fake_fact_store):
        # 不该抛 ValueError / TypeError
        await memory_server._run_path_b('悠怡', state)

    # 验证 aextract_facts_with_known_pool 被调（说明 sort 没挂）
    fake_fact_store.aextract_facts_with_known_pool.assert_awaited_once()
    captured_known_pool = fake_fact_store.aextract_facts_with_known_pool.await_args.args[2]
    # 5 条脏 fact 全在窗口内，都应该进 pool（脏值不丢，只是排序 fallback）
    assert len(captured_known_pool) == 5


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
