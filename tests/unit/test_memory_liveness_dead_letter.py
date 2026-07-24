# -*- coding: utf-8 -*-
"""Memory 模块 LLM 终态失败的 liveness dead-letter 兜底契约。

Background：所有"同一 input + 反复 LLM 失败 + 无 attempt counter → 永久卡死"
的后台路径都按 ``MEMORY_LIVENESS_MAX_ATTEMPTS`` (=5) 计数，达上限后强推
progress marker（cursor / 队头 / cluster_hash / op state）放弃毒输入，避免
单条毒数据让该角色整条 pipeline 哑火。详见 issue #1409。

覆盖的 7 个 site：
- Site 0a: ``_signal_check_one`` (path A Stage-1) cursor 强推到 now
- Site 0b: ``_run_path_b`` (path B Stage-1) cursor 强推到 last_fetched_ts
- Site 1:  ``_periodic_rebuttal_loop`` cursor 强推到 now
- Site 2:  ``PersonaManager.resolve_corrections`` batch entry dead-letter
- Site 3:  ``FactDedupResolver.aresolve`` batch pair dead-letter
- Site 4:  ``MemoryRefineEngine.refine_pass`` 非 fact 成员 dead-letter
- Site 7:  outbox handler append_done dead-letter（解锁 compact）

每个 site 测：
- 失败 N-1 次不动 progress marker
- 第 N 次触发 dead-letter
- 成功路径清 attempt 计数器
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────
# Site 0a — _signal_check_one (path A) Stage-1 dead-letter
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_path_a_bump_failure_under_threshold_returns_false():
    """N-1 次失败：返 False（caller 走原"保留 cursor 重试"路径）。"""
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS

    memory_server._signal_check_state.clear()
    state = memory_server._signal_check_state.setdefault(
        'neko_test_a', {'turns_since': 0, 'last_check_ts': '2026-05-18T10:00:00'},
    )
    cursor_key = state['last_check_ts']
    now = datetime(2026, 5, 18, 11, 0, 0)

    triggered = False
    for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS - 1):
        triggered = memory_server._stage1_path_a_bump_failure(
            'neko_test_a', state, cursor_key, now,
        )
    assert triggered is False, "未达 MAX 不该触发 dead-letter"
    # cursor 不动
    assert state['last_check_ts'] == '2026-05-18T10:00:00'
    # counter 累计到 MAX-1
    assert state['a_extract_failures'][cursor_key] == MEMORY_LIVENESS_MAX_ATTEMPTS - 1


@pytest.mark.unit
def test_path_a_bump_failure_at_threshold_forces_cursor():
    """第 N 次失败：返 True 并强推 cursor 到 now + 清 counter。"""
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS

    memory_server._signal_check_state.clear()
    state = memory_server._signal_check_state.setdefault(
        'neko_test_a', {'turns_since': 0, 'last_check_ts': '2026-05-18T10:00:00'},
    )
    cursor_key = state['last_check_ts']
    now = datetime(2026, 5, 18, 11, 0, 0)

    for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS - 1):
        memory_server._stage1_path_a_bump_failure(
            'neko_test_a', state, cursor_key, now,
        )
    triggered = memory_server._stage1_path_a_bump_failure(
        'neko_test_a', state, cursor_key, now,
    )
    assert triggered is True
    # cursor 强推到 now
    assert state['last_check_ts'] == now.isoformat()
    # counter 清零（mark_done 顺带做）
    assert state['a_extract_failures'] == {}


@pytest.mark.unit
def test_signal_check_mark_done_clears_path_a_failures():
    """成功路径 mark_done 必须清 path-A counter，否则 cursor 历史拖累内存。"""
    from app import memory_server

    memory_server._signal_check_state.clear()
    state = memory_server._signal_check_state.setdefault(
        'neko_test_mark', {'turns_since': 5, 'last_check_ts': 'old_cursor'},
    )
    state['a_extract_failures'] = {'old_cursor': 3, 'older_cursor': 1}

    memory_server._signal_check_mark_done('neko_test_mark', datetime(2026, 5, 18, 12, 0, 0))

    assert state['a_extract_failures'] == {}, "mark_done 必须清 path-A counter"


# ─────────────────────────────────────────────────────────────────────
# Site 0b — _run_path_b Stage-1 dead-letter
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_path_b_bump_failure_under_threshold_returns_false():
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS

    last_b_check = datetime(2026, 5, 18, 10, 0, 0)
    last_fetched = datetime(2026, 5, 18, 10, 30, 0)
    state = {'last_b_check_ts': last_b_check}
    cursor_key = last_b_check.isoformat(timespec='microseconds')

    triggered = False
    for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS - 1):
        triggered = memory_server._stage1_path_b_bump_failure(
            'neko_test_b', state, cursor_key, last_fetched,
        )
    assert triggered is False
    # cursor 不动
    assert state['last_b_check_ts'] == last_b_check
    assert state['b_extract_failures'][cursor_key] == MEMORY_LIVENESS_MAX_ATTEMPTS - 1


@pytest.mark.unit
def test_path_b_bump_failure_at_threshold_forces_cursor():
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS

    last_b_check = datetime(2026, 5, 18, 10, 0, 0)
    last_fetched = datetime(2026, 5, 18, 10, 30, 0)
    state = {'last_b_check_ts': last_b_check}
    cursor_key = last_b_check.isoformat(timespec='microseconds')

    for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS):
        triggered = memory_server._stage1_path_b_bump_failure(
            'neko_test_b', state, cursor_key, last_fetched,
        )

    assert triggered is True
    assert state['last_b_check_ts'] == last_fetched
    assert state['b_extract_failures'] == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_path_b_persisted_none_triggers_dead_letter_at_threshold():
    """End-to-end: _run_path_b 第 N 次 persisted=None 时强推 last_b_check_ts。"""
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS

    last_a_msg_ts = datetime(2026, 5, 18, 12, 0, 0)
    last_b = last_a_msg_ts - timedelta(minutes=10)
    state = {
        'last_a_msg_ts': last_a_msg_ts,
        'last_b_check_ts': last_b,
    }

    # 至少要 2 行 (user, ai) 才能过 bracket trim 进入 LLM
    base = last_b + timedelta(seconds=10)
    rows = [
        (base + timedelta(seconds=0), 's', json.dumps({
            'type': 'human', 'data': {'content': 'user msg'},
        })),
        (base + timedelta(seconds=10), 's', json.dumps({
            'type': 'ai', 'data': {'content': 'ai reply'},
        })),
    ]

    fake_time_manager = MagicMock()
    fake_time_manager.aretrieve_original_by_timeframe = AsyncMock(return_value=rows)
    fake_fact_store = MagicMock()
    fake_fact_store.aload_facts = AsyncMock(return_value=[])
    fake_fact_store.aextract_facts_with_known_pool = AsyncMock(return_value=None)

    with patch.object(memory_server.runtime, 'time_manager', fake_time_manager), \
         patch.object(memory_server.runtime, 'fact_store', fake_fact_store):
        for i in range(MEMORY_LIVENESS_MAX_ATTEMPTS):
            await memory_server._run_path_b('neko_test_b_e2e', state)

    # 第 N 次：last_b_check_ts 强推到 last_fetched_ts (=最后一行 ts)
    expected_force_to = base + timedelta(seconds=10)
    assert state['last_b_check_ts'] == expected_force_to, (
        f"第 {MEMORY_LIVENESS_MAX_ATTEMPTS} 次失败应强推 cursor 到 {expected_force_to}, "
        f"实际 {state['last_b_check_ts']}"
    )
    # counter 清零
    assert state.get('b_extract_failures', {}) == {}


# ─────────────────────────────────────────────────────────────────────
# Site 1 — rebuttal loop dead-letter
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_rebuttal_bump_failure_accumulates():
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS

    memory_server._rebuttal_failures.clear()
    cursor_key = '2026-05-18T10:00:00.000000'

    for i in range(MEMORY_LIVENESS_MAX_ATTEMPTS):
        attempts = memory_server._rebuttal_bump_failure('neko_test_reb', cursor_key)
        assert attempts == i + 1


@pytest.mark.unit
def test_rebuttal_clear_failures_removes_name():
    from app import memory_server

    memory_server._rebuttal_failures.clear()
    memory_server._rebuttal_bump_failure('neko_test_reb', 'cursor_a')
    memory_server._rebuttal_bump_failure('neko_test_reb', 'cursor_b')
    assert 'neko_test_reb' in memory_server._rebuttal_failures

    memory_server._rebuttal_clear_failures('neko_test_reb')
    assert 'neko_test_reb' not in memory_server._rebuttal_failures


# ─────────────────────────────────────────────────────────────────────
# Site 2 — PersonaManager.resolve_corrections dead-letter
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_corrections_dead_letter_at_threshold(tmp_path):
    """resolve_corrections LLM 失败 N 次后 dead-letter 队头 corrections。"""
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    from memory.persona import PersonaManager

    pm = PersonaManager()
    name = 'neko_test_corr'
    corr_path = tmp_path / f'{name}_corrections.json'
    # Seed disk first—helper reads from `aload_pending_corrections`，不读
    # batch_items 的值，只用 batch_items 提取 ``created_at`` 当 match key。
    seed_items = [
        {
            'old_text': f'old_{i}',
            'new_text': f'new_{i}',
            'entity': 'master',
            'created_at': f'2026-05-18T10:00:0{i}',
        }
        for i in range(3)
    ]
    corr_path.write_text(json.dumps(seed_items), encoding='utf-8')
    batch_keys_only = [{'created_at': it['created_at']} for it in seed_items]

    with patch.object(pm, '_corrections_path', return_value=str(corr_path)):
        # 模拟 LLM 失败 N 次（每次 LLM 看到队头同样 N 条）
        for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS):
            await pm._abump_correction_attempts_and_dead_letter(name, batch_keys_only)

        remaining = json.loads(corr_path.read_text(encoding='utf-8')) or []
        # 所有 entry 都该被 dead-letter
        assert remaining == [], (
            f"N 次失败后 batch entry 全部 dead-letter, 实际剩余: {remaining}"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_corrections_under_threshold_keeps_items(tmp_path):
    """N-1 次失败：所有 entry 保留 + resolve_attempts 字段递增。"""
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    from memory.persona import PersonaManager

    pm = PersonaManager()
    name = 'neko_test_corr_under'
    corr_path = tmp_path / f'{name}_corrections.json'
    seed_items = [
        {
            'old_text': 'oa', 'new_text': 'na',
            'entity': 'master',
            'created_at': '2026-05-18T10:00:00',
        }
    ]
    corr_path.write_text(json.dumps(seed_items), encoding='utf-8')
    batch_keys_only = [{'created_at': seed_items[0]['created_at']}]

    with patch.object(pm, '_corrections_path', return_value=str(corr_path)):
        for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS - 1):
            await pm._abump_correction_attempts_and_dead_letter(name, batch_keys_only)

        remaining = json.loads(corr_path.read_text(encoding='utf-8')) or []
        assert len(remaining) == 1
        assert remaining[0]['resolve_attempts'] == MEMORY_LIVENESS_MAX_ATTEMPTS - 1


# ─────────────────────────────────────────────────────────────────────
# Site 3 — FactDedupResolver.aresolve dead-letter
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dedup_dead_letter_at_threshold(tmp_path):
    """fact_dedup LLM 失败 N 次后 pair 从 pending queue dead-letter。"""
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    from memory.fact_dedup import FactDedupResolver

    resolver = FactDedupResolver(fact_store=MagicMock())
    name = 'neko_test_dedup'
    pending_path = tmp_path / 'pending.json'

    seed = [
        {
            'candidate_id': f'cand_{i}',
            'existing_id': f'exist_{i}',
            'candidate_text': f'c_{i}',
            'existing_text': f'e_{i}',
            'entity': 'master',
            'cosine': 0.9,
            'queued_at': '2026-05-18T10:00:00',
        }
        for i in range(3)
    ]
    pending_path.write_text(
        json.dumps(seed, ensure_ascii=False), encoding='utf-8',
    )

    def _noop_assert(*args, **kw):
        return None

    with patch.object(resolver, '_pending_path', return_value=str(pending_path)), \
         patch('memory.fact_dedup.assert_cloudsave_writable', _noop_assert):
        for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS):
            # 模拟"每轮 LLM 失败"——batch 用 disk 上当前的 pair (dead-letter 后会缩短)
            current = json.loads(pending_path.read_text(encoding='utf-8'))
            if not current:
                break
            await resolver._abump_dedup_attempts_and_dead_letter_locked(name, current)

        remaining = json.loads(pending_path.read_text(encoding='utf-8'))
        assert remaining == [], (
            f"N 次失败后 batch pair 全部 dead-letter, 实际剩余: {remaining}"
        )


# ─────────────────────────────────────────────────────────────────────
# Site 4 — refine cluster dead-letter
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_safe_int_field_handles_dirty_values():
    """Codex P2 regression：``refine_attempts`` / ``resolve_attempts`` / ``_attempt_count``
    持久化字段被手改 / migration noise 写成 ``""`` / ``"unknown"`` / list / dict
    等脏值时，``safe_int_field`` 必须兜底返 default，不让上游 list comprehension
    挂掉整个 refine pass / resolve loop。
    """
    from memory.facts import safe_int_field

    # 合法值原样返回
    assert safe_int_field({'x': 5}, 'x') == 5
    assert safe_int_field({'x': '7'}, 'x') == 7
    assert safe_int_field({'x': 0}, 'x') == 0
    # 缺失 / None → default
    assert safe_int_field({}, 'x') == 0
    assert safe_int_field({'x': None}, 'x') == 0
    assert safe_int_field({}, 'x', default=3) == 3
    # 脏值（manual edit / legacy / migration noise）→ default 不挂
    for bad in ('', 'unknown', 'high', [], {}, [1, 2]):
        assert safe_int_field({'x': bad}, 'x') == 0, (
            f"脏值 {bad!r} 必须兜底返 default 0，不能抛 ValueError/TypeError"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refine_pass_failure_fn_invoked_on_resolve_false():
    """refine_pass：_resolve_cluster 返 False 时必须调 failure_fn (Site 4 兜底)。"""
    from memory.refine import (
        FailureFn,
        MemoryRefineEngine,
        REFINE_ENTITY_KEY,
        REFINE_TYPE_KEY,
    )

    engine = MemoryRefineEngine(MagicMock())

    # Mock _compute_clusters 返回一个 cluster
    fake_cluster_member = {
        'id': 'r_001',
        'text': 'sample',
        REFINE_TYPE_KEY: 'reflection',
        REFINE_ENTITY_KEY: 'master',
        'last_refine_at': None,
        'last_refine_cluster_hash': None,
    }
    candidates_by_entity = {'master': [fake_cluster_member, dict(fake_cluster_member, id='r_002')]}

    # Mock embedding service available + cluster compute
    engine._service.is_disabled = MagicMock(return_value=False)
    engine._service.is_available = MagicMock(return_value=True)
    engine._service.model_id = MagicMock(return_value='mock_model')

    # Force _compute_clusters to return our cluster
    with patch.object(engine, '_compute_clusters', return_value=[candidates_by_entity['master']]), \
         patch.object(engine, '_resolve_cluster', AsyncMock(return_value=False)):
        captured_failures: list[tuple] = []

        async def _failure_fn(cluster, cluster_hash):
            captured_failures.append((cluster, cluster_hash))

        apply_fn = AsyncMock()
        result = await engine.refine_pass(
            candidates_by_entity,
            apply_fn=apply_fn,
            scope_label='test/scope',
            failure_fn=_failure_fn,
        )

    assert result['clusters_failed'] == 1
    assert len(captured_failures) == 1, "_resolve_cluster 返 False 必须触发 failure_fn"
    cluster_arg, hash_arg = captured_failures[0]
    assert isinstance(hash_arg, str) and len(hash_arg) > 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refine_pass_exception_invokes_failure_fn():
    """``_resolve_cluster`` 抛异常时**也**调 failure_fn（计入 refine_attempts）。

    先前设计（Codex P1 on PR #1412）把异常按"瞬态、不计数"处理，怕单次抖动
    冤枉 entry。但持续性故障（correction 模型快照下线一直超时、cloudsave 卡
    维护态、FS 只读）会让同一个毒 cluster 每 30min 原样重打 LLM 永不放弃。
    现在异常也计入预算：N=MEMORY_LIVENESS_MAX_ATTEMPTS 跨过偶发抖动，cluster
    内容一变 hash 即变、attempts 随新成员复位，所以持续故障收敛、偶发无损。
    """
    from memory.refine import (
        MemoryRefineEngine,
        REFINE_ENTITY_KEY,
        REFINE_TYPE_KEY,
    )

    engine = MemoryRefineEngine(MagicMock())
    fake_member = {
        'id': 'r_x', 'text': 'x',
        REFINE_TYPE_KEY: 'reflection',
        REFINE_ENTITY_KEY: 'master',
        'last_refine_at': None,
        'last_refine_cluster_hash': None,
    }
    candidates = {'master': [fake_member, dict(fake_member, id='r_y')]}

    engine._service.is_disabled = MagicMock(return_value=False)
    engine._service.is_available = MagicMock(return_value=True)
    engine._service.model_id = MagicMock(return_value='mock_model')

    async def _exploding_resolve(*args, **kw):
        raise RuntimeError("simulated apply_fn IO failure")

    captured_failures: list = []
    async def _failure_fn(cluster, cluster_hash):
        captured_failures.append((cluster, cluster_hash))

    with patch.object(engine, '_compute_clusters', return_value=[candidates['master']]), \
         patch.object(engine, '_resolve_cluster', _exploding_resolve):
        result = await engine.refine_pass(
            candidates,
            apply_fn=AsyncMock(),
            scope_label='test/scope',
            failure_fn=_failure_fn,
        )

    assert result['clusters_failed'] == 1, "exception 仍记 clusters_failed"
    assert len(captured_failures) == 1, (
        f"exception 应触发 failure_fn 一次（持续性故障必须计入 refine_attempts "
        f"才能 dead-letter）, 实际触发 {len(captured_failures)} 次"
    )
    cluster_arg, hash_arg = captured_failures[0]
    assert cluster_arg == candidates['master']
    assert isinstance(hash_arg, str) and len(hash_arg) > 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_persona_abump_refine_attempts_at_threshold_warns():
    """PersonaManager._abump_refine_attempts: 第 N 次 bump 命中阈值 → WARN log。"""
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    from memory.persona import PersonaManager
    from memory.refine import REFINE_ENTITY_KEY, REFINE_TYPE_KEY

    pm = PersonaManager()
    name = 'neko_test_refine'

    persona = {
        'master': {
            'facts': [
                {'id': 'p_001', 'text': 'sample', 'refine_attempts': 0},
            ]
        },
    }

    async def _fake_ensure(_n):
        return persona

    saved_persona: list = []
    async def _fake_save(_n, _persona):
        saved_persona.append(_persona)

    cluster = [
        {
            'id': 'p_001',
            REFINE_TYPE_KEY: 'persona',
            REFINE_ENTITY_KEY: 'master',
        }
    ]

    with patch.object(pm, '_aensure_persona_locked', _fake_ensure), \
         patch.object(pm, 'asave_persona', _fake_save):
        for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS):
            await pm._abump_refine_attempts(name, cluster, 'hash_1')

    # 最终 refine_attempts == MEMORY_LIVENESS_MAX_ATTEMPTS
    assert persona['master']['facts'][0]['refine_attempts'] == MEMORY_LIVENESS_MAX_ATTEMPTS
    # 每次 bump 都戳失败时刻，供 dead-letter 时间自愈（cooldown_elapsed）
    assert persona['master']['facts'][0].get('last_refine_attempt_at'), (
        "bump 必须戳 last_refine_attempt_at，否则 dead-letter 无法时间自愈"
    )


# ─────────────────────────────────────────────────────────────────────
# Site 7 — outbox dead-letter
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_outbox_pending_ops_counts_attempts(tmp_path):
    """pending_ops 应顺带把 attempt 计数附在每条返回 record 上 (Site 7 基础)。"""
    from memory.outbox import Outbox

    outbox = Outbox()
    name = 'neko_test_outbox'
    path = tmp_path / 'outbox.ndjson'

    with patch.object(outbox, '_outbox_path', return_value=str(path)):
        op_id = outbox.append_pending(name, 'test_op', {'foo': 'bar'})
        outbox.append_attempt(name, op_id)
        outbox.append_attempt(name, op_id)

        pending = outbox.pending_ops(name)
        assert len(pending) == 1
        assert pending[0]['op_id'] == op_id
        assert pending[0]['_attempt_count'] == 2


@pytest.mark.unit
def test_outbox_done_clears_attempts(tmp_path):
    """append_done 后该 op 出 pending list；attempt 计数无意义。"""
    from memory.outbox import Outbox

    outbox = Outbox()
    name = 'neko_test_outbox_done'
    path = tmp_path / 'outbox.ndjson'

    with patch.object(outbox, '_outbox_path', return_value=str(path)):
        op_id = outbox.append_pending(name, 'test_op', {})
        outbox.append_attempt(name, op_id)
        outbox.append_attempt(name, op_id)
        outbox.append_done(name, op_id)

        pending = outbox.pending_ops(name)
        assert pending == []


@pytest.mark.unit
def test_outbox_compact_preserves_attempts_for_pending(tmp_path):
    """compact 保留 still-pending op 的 attempt 行；done 的连同 attempt 一起丢。"""
    from memory.outbox import Outbox

    outbox = Outbox()
    name = 'neko_test_outbox_compact'
    path = tmp_path / 'outbox.ndjson'

    with patch.object(outbox, '_outbox_path', return_value=str(path)):
        # op1：pending + 2 attempts
        op1 = outbox.append_pending(name, 'op_type', {'k': 1})
        outbox.append_attempt(name, op1)
        outbox.append_attempt(name, op1)
        # op2：pending + 1 attempt + done
        op2 = outbox.append_pending(name, 'op_type', {'k': 2})
        outbox.append_attempt(name, op2)
        outbox.append_done(name, op2)

        dropped = outbox.compact(name)
        # op2 pending + attempt + done = 3 行 dropped；op1 = pending + 2 attempts 留
        assert dropped == 3

        pending = outbox.pending_ops(name)
        assert len(pending) == 1
        assert pending[0]['op_id'] == op1
        assert pending[0]['_attempt_count'] == 2, (
            "compact 后 op1 的 attempt 计数应该还在"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_outbox_op_dead_letters_at_threshold(tmp_path):
    """end-to-end: _run_outbox_op handler 累计失败 ≥ N → append_done dead-letter。"""
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    from memory.outbox import Outbox

    outbox = Outbox()
    name = 'neko_test_outbox_e2e'
    path = tmp_path / 'outbox.ndjson'

    with patch.object(outbox, '_outbox_path', return_value=str(path)):
        outbox.append_pending(name, 'poison_op', {'data': 'bad'})

    # Register a handler that always raises
    async def _poison_handler(_n, _p):
        raise RuntimeError("simulated poison payload — handler always raises")

    original_outbox = memory_server.runtime.outbox
    original_handlers = memory_server._OUTBOX_HANDLERS.copy()
    try:
        memory_server.runtime.outbox = outbox
        memory_server._OUTBOX_HANDLERS['poison_op'] = _poison_handler

        with patch.object(outbox, '_outbox_path', return_value=str(path)):
            # 跑 MEMORY_LIVENESS_MAX_ATTEMPTS 次模拟"每次重启都重放该 op"
            for round_idx in range(MEMORY_LIVENESS_MAX_ATTEMPTS):
                pending = outbox.pending_ops(name)
                if not pending:
                    break
                op = pending[0]
                await memory_server._run_outbox_op(name, op)

            # 第 N 次 (i.e. attempt count 达 N) 后该 op 必须 append_done dead-letter
            pending_after = outbox.pending_ops(name)
            assert pending_after == [], (
                f"达 {MEMORY_LIVENESS_MAX_ATTEMPTS} 次失败后该 op 应 dead-letter 出 pending, "
                f"实际剩 {pending_after}"
            )
    finally:
        memory_server.runtime.outbox = original_outbox
        memory_server._OUTBOX_HANDLERS.clear()
        memory_server._OUTBOX_HANDLERS.update(original_handlers)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_outbox_op_short_circuits_when_already_dead_letter(tmp_path):
    """CodeRabbit P1 regression：进门时 ``_attempt_count >= MAX`` → 跳 handler 直接 append_done。

    Setup：op 在磁盘上已经累计了 ``MEMORY_LIVENESS_MAX_ATTEMPTS`` 条 attempt
    行（边缘场景：上轮 dead-letter ``aappend_done`` 失败留下的 stuck pending）。
    断言：handler **不**被调用，直接 ``aappend_done`` 补 done。否则非幂等
    handler 会被毒 op 重复执行造成副作用。
    """
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    from memory.outbox import Outbox

    outbox = Outbox()
    name = 'neko_test_outbox_short_circuit'
    path = tmp_path / 'outbox.ndjson'

    with patch.object(outbox, '_outbox_path', return_value=str(path)):
        outbox.append_pending(name, 'side_effect_op', {})
        op_id = outbox.pending_ops(name)[0]['op_id']
        # 把 attempt 推满 → 模拟上轮 dead-letter append_done 失败的 stuck state
        for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS):
            outbox.append_attempt(name, op_id)

    handler_called = []

    async def _side_effect_handler(_n, _p):
        handler_called.append(True)
        raise RuntimeError("不应该被调用：op 已达 dead-letter 阈值")

    original_outbox = memory_server.runtime.outbox
    original_handlers = memory_server._OUTBOX_HANDLERS.copy()
    try:
        memory_server.runtime.outbox = outbox
        memory_server._OUTBOX_HANDLERS['side_effect_op'] = _side_effect_handler

        with patch.object(outbox, '_outbox_path', return_value=str(path)):
            pending = outbox.pending_ops(name)
            assert len(pending) == 1
            assert pending[0]['_attempt_count'] == MEMORY_LIVENESS_MAX_ATTEMPTS
            await memory_server._run_outbox_op(name, pending[0])

            pending_after = outbox.pending_ops(name)

        assert handler_called == [], (
            f"已达 dead-letter 阈值的 op 不该再跑 handler. 调用次数: {len(handler_called)}"
        )
        assert pending_after == [], (
            f"短路 dead-letter 后该 op 必须出 pending（append_done 已补）, "
            f"实际剩 {pending_after}"
        )
    finally:
        memory_server.runtime.outbox = original_outbox
        memory_server._OUTBOX_HANDLERS.clear()
        memory_server._OUTBOX_HANDLERS.update(original_handlers)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_outbox_op_attempt_persist_failure_keeps_pending(tmp_path):
    """Codex P1 regression：``aappend_attempt`` 抛异常时不应触发 dead-letter。

    Setup：op 已经在磁盘上有 ``MEMORY_LIVENESS_MAX_ATTEMPTS - 1`` 条 attempt
    行（边缘 case：再失败一次就会触发 dead-letter）。让 ``aappend_attempt``
    抛 IOError 模拟 transient 磁盘失败。
    断言：op 仍在 pending（没被 append_done 当 dead-letter），允许下次重放
    重新走一次 attempt。否则"未落盘的 +1" 误算成"已落盘的 N"，磁盘上看起来
    只失败了 N-1 次就被 done 永久丢，违背契约。
    """
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    from memory.outbox import Outbox

    outbox = Outbox()
    name = 'neko_test_outbox_persist_fail'
    path = tmp_path / 'outbox.ndjson'

    with patch.object(outbox, '_outbox_path', return_value=str(path)):
        outbox.append_pending(name, 'poison_op', {'data': 'bad'})
        # 预先填到 N-1 个 attempt（接下来再失败一次就该 dead-letter，正常路径下）
        op_id_from_disk = outbox.pending_ops(name)[0]['op_id']
        for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS - 1):
            outbox.append_attempt(name, op_id_from_disk)

    async def _poison_handler(_n, _p):
        raise RuntimeError("simulated poison")

    original_outbox = memory_server.runtime.outbox
    original_handlers = memory_server._OUTBOX_HANDLERS.copy()
    try:
        memory_server.runtime.outbox = outbox
        memory_server._OUTBOX_HANDLERS['poison_op'] = _poison_handler

        # 让 aappend_attempt 抛异常（模拟磁盘 transient 失败）
        async def _broken_append_attempt(*args, **kw):
            raise IOError("simulated disk transient")

        with patch.object(outbox, '_outbox_path', return_value=str(path)), \
             patch.object(outbox, 'aappend_attempt', _broken_append_attempt):
            pending = outbox.pending_ops(name)
            assert len(pending) == 1
            assert pending[0]['_attempt_count'] == MEMORY_LIVENESS_MAX_ATTEMPTS - 1
            await memory_server._run_outbox_op(name, pending[0])

        # 关键断言：op 仍在 pending，不该被 dead-letter
        with patch.object(outbox, '_outbox_path', return_value=str(path)):
            pending_after = outbox.pending_ops(name)
        assert len(pending_after) == 1, (
            f"aappend_attempt 失败时不应基于未落盘的 +1 触发 dead-letter, "
            f"应保留 pending 等下次自然重放. 实际 pending: {pending_after}"
        )
        # 磁盘上 attempt 计数仍是 N-1（本次 attempt 没落盘）
        assert pending_after[0]['_attempt_count'] == MEMORY_LIVENESS_MAX_ATTEMPTS - 1
    finally:
        memory_server.runtime.outbox = original_outbox
        memory_server._OUTBOX_HANDLERS.clear()
        memory_server._OUTBOX_HANDLERS.update(original_handlers)
