# -*- coding: utf-8 -*-
"""Unit tests for memory.scoped_refine — the scoped lite refine engine.

Contracts under test (群记忆系列 5/7 主线二):

  1. Bucketing: key is (subject.key, scope) per store — two groups sharing
     entity='group_chat' NEVER share a bucket / cluster / prompt; legacy,
     unstamped, protected, id-less and dead-lettered entries stay out.
  2. Trigger threshold: a subject-store pool below SCOPED_REFINE_MIN_ENTRIES
     is not eligible.
  3. Cost contract: at most ONE LLM call per pass; summary tier; extra_body
     is OMITTED (= provider-dialect thinking-off); short timeout.
  4. Rotation cursor: consecutive passes serve different buckets.
  5. Apply (persona + reflection): merge output carries the full subject
     stamp (unstamped rows are fail-closed invisible on scoped reads — the
     stamp IS the data-preservation guarantee); consumed reflection sources
     become status='merged' (kept on disk), consumed persona sources leave
     traces in version_history; survivors get stamped for hash-skip;
     garbage actions change nothing and stamp nothing.
  6. Failure path: refine_attempts bump is persisted and scoped-addressed
     (never creates a bogus top-level 'group_chat' section).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from config import SCOPED_REFINE_LLM_TIMEOUT_SECONDS, SCOPED_REFINE_MIN_ENTRIES
from memory.embeddings import stamp_embedding_fields
from memory.scopes import MemorySubject, filter_entries_for_subjects
from memory.scoped_refine import (
    STORE_PERSONA,
    STORE_REFLECTION,
    ScopedLiteRefineEngine,
    abump_scoped_persona_refine_attempts,
    abump_scoped_reflection_refine_attempts,
    apply_scoped_persona_merge,
    apply_scoped_reflection_merge,
    gather_scoped_refine_buckets,
)


GROUP_A = MemorySubject.group_chat("qq", "111")
GROUP_B = MemorySubject.group_chat("qq", "222")
MODEL_ID = "test-model"


def _stamped(entry: dict, vec: list[float]) -> dict:
    """Attach a REAL encoded embedding triple so the engine's cache
    validation passes without stubbing it away."""
    stamp_embedding_fields(
        entry, np.asarray(vec, dtype=np.float32), entry.get('text', ''),
        MODEL_ID,
    )
    return entry


def _p_entry(eid: str, text: str, subject: MemorySubject | None,
             vec: list[float] | None = None, **extra) -> dict:
    entry = {
        'id': eid, 'text': text,
        'reinforcement': 0.0, 'disputation': 0.0,
        **(subject.as_entry_fields() if subject is not None else {}),
        **extra,
    }
    if vec is not None:
        _stamped(entry, vec)
    return entry


def _r_entry(rid: str, text: str, subject: MemorySubject | None,
             vec: list[float] | None = None, **extra) -> dict:
    entry = {
        'id': rid, 'text': text, 'entity': 'group_chat',
        'status': 'confirmed', 'confirmed_at': '2026-06-01T00:00:00',
        'created_at': '2026-06-01T00:00:00',
        'source_fact_ids': [f"fact_{rid}"],
        'reinforcement': 0.1,
        **(subject.as_entry_fields() if subject is not None else {}),
        **extra,
    }
    if vec is not None:
        _stamped(entry, vec)
    return entry


def _persona_with(sections: dict) -> dict:
    return sections


class _ServiceStub:
    def is_disabled(self):
        return False

    def is_available(self):
        return True

    def model_id(self):
        return MODEL_ID


def _engine() -> ScopedLiteRefineEngine:
    cm = MagicMock()
    cm.aget_model_api_config = AsyncMock(return_value={
        'model': 'fake-summary', 'base_url': 'http://fake',
        'api_key': 'sk-fake', 'provider_type': None,
    })
    with patch('memory.scoped_refine.get_embedding_service',
               return_value=_ServiceStub()):
        engine = ScopedLiteRefineEngine(cm)
    engine._cm = cm
    return engine


def _make_llm(payload):
    resp = MagicMock()
    resp.content = json.dumps(payload)
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=resp)
    llm.aclose = AsyncMock(return_value=None)
    return llm


# ── gather_scoped_refine_buckets ─────────────────────────────────────


def test_gather_buckets_by_subject_never_by_entity():
    """A 群与 B 群的条目 entity 全是 'group_chat'——分桶必须按 subject
    隔离。按 entity 分桶的错误实现会把两群塞进一个池（跨群合并）。"""
    refls = (
        [_r_entry(f"a{i}", f"A 群反思 {i}", GROUP_A) for i in range(8)]
        + [_r_entry(f"b{i}", f"B 群反思 {i}", GROUP_B) for i in range(8)]
    )
    buckets = gather_scoped_refine_buckets({}, refls, min_entries=8)
    assert len(buckets) == 2
    by_marker = {b.marker: b for b in buckets}
    a_marker = (GROUP_A.key, GROUP_A.scope, STORE_REFLECTION)
    b_marker = (GROUP_B.key, GROUP_B.scope, STORE_REFLECTION)
    assert {e['id'] for e in by_marker[a_marker].entries} == {f"a{i}" for i in range(8)}
    assert {e['id'] for e in by_marker[b_marker].entries} == {f"b{i}" for i in range(8)}


def test_gather_threshold_gates_eligibility():
    refls = [_r_entry(f"a{i}", f"t{i}", GROUP_A) for i in range(SCOPED_REFINE_MIN_ENTRIES - 1)]
    assert gather_scoped_refine_buckets({}, refls) == []
    refls.append(_r_entry("last", "t", GROUP_A))
    buckets = gather_scoped_refine_buckets({}, refls)
    assert len(buckets) == 1


def test_gather_excludes_legacy_protected_idless_and_dead_letter():
    ok = [_r_entry(f"a{i}", f"t{i}", GROUP_A) for i in range(8)]
    legacy = _r_entry("leg", "legacy 行", None)
    partial = {'id': 'p1', 'text': 't', 'subject_kind': 'group_chat'}  # 孤儿
    protected = _r_entry("prot", "t", GROUP_A, protected=True)
    idless = {k: v for k, v in _r_entry("x", "t", GROUP_A).items() if k != 'id'}
    dead = _r_entry("dead", "t", GROUP_A, refine_attempts=5,
                    last_refine_attempt_at=datetime.now().isoformat())
    buckets = gather_scoped_refine_buckets(
        {}, ok + [legacy, partial, protected, idless, dead],
    )
    ids = {e['id'] for b in buckets for e in b.entries}
    assert ids == {f"a{i}" for i in range(8)}


def test_gather_dead_letter_self_heal_probe():
    stale_attempt = (datetime.now() - timedelta(days=1)).isoformat()
    entries = [_r_entry(f"a{i}", f"t{i}", GROUP_A) for i in range(7)]
    entries.append(_r_entry("healed", "t", GROUP_A, refine_attempts=99,
                            last_refine_attempt_at=stale_attempt))
    buckets = gather_scoped_refine_buckets({}, entries)
    assert len(buckets) == 1
    assert "healed" in {e['id'] for e in buckets[0].entries}


def test_gather_persona_entries_from_sections_by_entry_stamp():
    section = {
        GROUP_A.persona_section_key: {
            **GROUP_A.as_entry_fields(),
            'entity': GROUP_A.kind,
            'facts': [_p_entry(f"p{i}", f"条目{i}", GROUP_A) for i in range(8)],
        },
        'master': {'facts': [{'id': 'm1', 'text': 'legacy'}]},
    }
    buckets = gather_scoped_refine_buckets(section, [], min_entries=8)
    assert len(buckets) == 1
    assert buckets[0].store == STORE_PERSONA
    assert buckets[0].subject.key == GROUP_A.key


# ── engine refine_pass：成本契约与隔离 ───────────────────────────────


def _vec_pair(base: float = 1.0):
    return [base, 0.0, 0.0, 0.0], [0.98, 0.19, 0.0, 0.0]


@pytest.mark.asyncio
async def test_refine_pass_single_llm_call_and_no_cross_bucket_text():
    engine = _engine()
    va, vb = _vec_pair()
    bucket_a_entries = [
        _r_entry(f"a{i}", f"A群文本{i}", GROUP_A, va if i % 2 else vb)
        for i in range(8)
    ]
    bucket_b_entries = [
        _r_entry(f"b{i}", f"B群文本{i}", GROUP_B, va if i % 2 else vb)
        for i in range(8)
    ]
    buckets = gather_scoped_refine_buckets(
        {}, bucket_a_entries + bucket_b_entries,
    )
    assert len(buckets) == 2

    applied = []

    async def _apply(bucket, cluster, actions, cluster_hash):
        applied.append((bucket.marker, [e['id'] for e in cluster]))

    llm = _make_llm([])
    create = AsyncMock(return_value=llm)
    with patch('utils.llm_client.create_chat_llm_async', create):
        result = await engine.refine_pass(
            buckets, apply_fn=_apply, scope_label='scoped/t',
        )
    # 单 pass 只打一次 LLM。
    assert create.await_count == 1
    assert result['resolved'] == 1
    assert len(applied) == 1
    # prompt 里只出现被服务 bucket 的文本，绝无另一群的文本。
    prompt = llm.ainvoke.await_args.args[0]
    served_marker, _ = applied[0]
    if served_marker[0] == GROUP_A.key:
        assert "A群文本" in prompt and "B群文本" not in prompt
    else:
        assert "B群文本" in prompt and "A群文本" not in prompt


@pytest.mark.asyncio
async def test_refine_pass_llm_config_is_lite():
    """成本契约钉死：summary tier、不传 extra_body（= provider 方言关
    thinking）、短超时。传 extra_body=None（开 thinking）或 correction
    tier 的错误实现都要在这里翻红。"""
    engine = _engine()
    va, vb = _vec_pair()
    entries = [
        _r_entry(f"a{i}", f"文本{i}", GROUP_A, va if i % 2 else vb)
        for i in range(8)
    ]
    buckets = gather_scoped_refine_buckets({}, entries)

    async def _apply(*_a):
        return None

    llm = _make_llm([])
    create = AsyncMock(return_value=llm)
    with patch('utils.llm_client.create_chat_llm_async', create):
        await engine.refine_pass(buckets, apply_fn=_apply, scope_label='t')
    engine._cm.aget_model_api_config.assert_awaited_once_with('summary')
    kwargs = create.await_args.kwargs
    assert 'extra_body' not in kwargs
    assert kwargs['timeout'] == SCOPED_REFINE_LLM_TIMEOUT_SECONDS
    assert kwargs['max_retries'] == 0


@pytest.mark.asyncio
async def test_refine_pass_cursor_rotates_between_buckets():
    engine = _engine()
    va, vb = _vec_pair()
    entries = (
        [_r_entry(f"a{i}", f"甲{i}", GROUP_A, va if i % 2 else vb) for i in range(8)]
        + [_r_entry(f"b{i}", f"乙{i}", GROUP_B, va if i % 2 else vb) for i in range(8)]
    )
    buckets = gather_scoped_refine_buckets({}, entries)

    async def _apply(*_a):
        return None

    served = []
    with patch('utils.llm_client.create_chat_llm_async',
               AsyncMock(return_value=_make_llm([]))):
        r1 = await engine.refine_pass(buckets, apply_fn=_apply, scope_label='t')
        served.append(r1['served'])
        r2 = await engine.refine_pass(
            buckets, apply_fn=_apply, scope_label='t', start_after=r1['served'],
        )
        served.append(r2['served'])
    assert served[0] is not None and served[1] is not None
    assert served[0][:2] != served[1][:2]  # 两个不同 subject 轮流被服务


@pytest.mark.asyncio
async def test_refine_pass_hash_fresh_cluster_skipped_without_llm():
    engine = _engine()
    va, vb = _vec_pair()
    entries = [
        _r_entry(f"a{i}", f"文本{i}", GROUP_A, va if i % 2 else vb)
        for i in range(8)
    ]
    # 先跑一遍拿到 cluster_hash，再给全员盖新鲜 stamp。
    captured = {}

    async def _apply(bucket, cluster, actions, cluster_hash):
        captured['hash'] = cluster_hash
        captured['ids'] = {e['id'] for e in cluster}

    with patch('utils.llm_client.create_chat_llm_async',
               AsyncMock(return_value=_make_llm([]))):
        await engine.refine_pass(
            gather_scoped_refine_buckets({}, entries),
            apply_fn=_apply, scope_label='t',
        )
    now_iso = datetime.now().isoformat()
    for e in entries:
        if e['id'] in captured['ids']:
            e['last_refine_cluster_hash'] = captured['hash']
            e['last_refine_at'] = now_iso

    create = AsyncMock(return_value=_make_llm([]))
    with patch('utils.llm_client.create_chat_llm_async', create):
        result = await engine.refine_pass(
            gather_scoped_refine_buckets({}, entries),
            apply_fn=_apply, scope_label='t',
        )
    assert result['clusters_skipped'] >= 1
    assert create.await_count == 0  # 新鲜 hash → 零 LLM 成本


@pytest.mark.asyncio
async def test_refine_pass_failure_calls_failure_fn():
    engine = _engine()
    va, vb = _vec_pair()
    entries = [
        _r_entry(f"a{i}", f"文本{i}", GROUP_A, va if i % 2 else vb)
        for i in range(8)
    ]
    failures = []

    async def _apply(*_a):
        return None

    async def _failure(bucket, cluster, cluster_hash):
        failures.append(cluster_hash)

    boom = MagicMock()
    boom.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
    boom.aclose = AsyncMock(return_value=None)
    with patch('utils.llm_client.create_chat_llm_async',
               AsyncMock(return_value=boom)):
        result = await engine.refine_pass(
            gather_scoped_refine_buckets({}, entries),
            apply_fn=_apply, scope_label='t', failure_fn=_failure,
        )
    assert result['failed'] == 1
    assert len(failures) == 1


def test_render_cluster_trust_annotation_hook():
    """系列 7/7 的接口形状：trust_of 提供时行内出现 trust=，不提供时
    绝不出现。"""
    cluster = [
        _r_entry("a1", "文本一", GROUP_A),
        _r_entry("a2", "文本二", GROUP_A),
    ]
    plain = ScopedLiteRefineEngine._render_cluster(cluster, None)
    assert "trust=" not in plain
    annotated = ScopedLiteRefineEngine._render_cluster(
        cluster, lambda e: 0.8 if e['id'] == 'a1' else None,
    )
    assert "(id=a1, trust=0.80)" in annotated
    assert "(id=a2)" in annotated


# ── apply：真实存储栈 ────────────────────────────────────────────────


def _mock_cm_files(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    return cm


def _install(tmpdir: str):
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine

    cm = _mock_cm_files(tmpdir)
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.facts.get_config_manager", return_value=cm), \
         patch("memory.persona.manager.get_config_manager", return_value=cm), \
         patch("memory.reflection.manager.get_config_manager", return_value=cm):
        event_log = EventLog()
        event_log._config_manager = cm
        fs = FactStore()
        fs._config_manager = cm
        pm = PersonaManager(event_log=event_log)
        pm._config_manager = cm
        re = ReflectionEngine(fs, pm, event_log=event_log)
        re._config_manager = cm
    return fs, pm, re


@pytest.mark.asyncio
async def test_apply_persona_merge_stamps_subject_and_consumes_sources(tmp_path):
    fs, pm, re = _install(str(tmp_path))
    persona = await pm.aensure_persona("小天")
    section = pm._get_section_facts(persona, GROUP_A.kind, subject=GROUP_A)
    for i in range(3):
        entry = pm._build_fact_entry(
            f"群友们喜欢周五联机打游戏（表述{i}）", 'reflection_time_driven',
            None, subject=GROUP_A,
        )
        entry['id'] = f"p{i}"
        section.append(entry)
    await pm.asave_persona("小天", persona)

    cluster = [dict(e) for e in section]
    actions = [{
        'action': 'merge', 'source_ids': ['p0', 'p1'],
        'produce': {'text': '群友们固定周五晚联机打游戏'},
        'reason': 'duplicate',
    }]
    applied = await apply_scoped_persona_merge(
        pm, "小天", GROUP_A, cluster, actions, "hash123",
    )
    assert applied == 1

    persona = await pm.aensure_persona("小天")
    facts = persona[GROUP_A.persona_section_key]['facts']
    by_id = {e['id']: e for e in facts}
    assert 'p0' not in by_id and 'p1' not in by_id
    merged = next(e for e in facts if e.get('merged_from_ids'))
    # subject 戳齐全——这是 fail-closed 渲染路径上的生死线。
    assert merged['subject_kind'] == GROUP_A.kind
    assert merged['subject_id'] == GROUP_A.subject_id
    assert merged['scope'] == GROUP_A.scope
    # scoped 渲染视角能看到 merged 条目（等价于「没有静默蒸发」）。
    visible = filter_entries_for_subjects(facts, [GROUP_A])
    assert merged['id'] in {e['id'] for e in visible}
    # 源文本进 version_history（数据不丢）。
    history_texts = {h['text'] for h in merged['version_history']}
    assert "群友们喜欢周五联机打游戏（表述0）" in history_texts
    assert merged['merged_from_ids'] == ['p0', 'p1']
    # 幸存者 p2 盖了 stamp。
    assert by_id['p2']['last_refine_cluster_hash'] == "hash123"


@pytest.mark.asyncio
async def test_apply_persona_merge_cannot_touch_other_scope_rows(tmp_path):
    """同一 section key 可以混不同自定义 scope 的条目；LLM 幻觉引用了
    另一 scope 的 id 也绝不能动它。"""
    fs, pm, re = _install(str(tmp_path))
    other_scope = MemorySubject.create(
        GROUP_A.kind, GROUP_A.subject_id, scope="custom_scope",
    )
    persona = await pm.aensure_persona("小天")
    section = pm._get_section_facts(persona, GROUP_A.kind, subject=GROUP_A)
    for i in range(2):
        e = pm._build_fact_entry(f"本域条目{i}", 'manual', None, subject=GROUP_A)
        e['id'] = f"p{i}"
        section.append(e)
    foreign = pm._build_fact_entry("他域条目", 'manual', None, subject=other_scope)
    foreign['id'] = "foreign1"
    section.append(foreign)
    await pm.asave_persona("小天", persona)

    cluster = [dict(e) for e in section]  # 假设引擎泄漏了他域条目进 cluster
    actions = [{
        'action': 'merge', 'source_ids': ['p0', 'foreign1'],
        'produce': {'text': '跨域合并产物'},
    }]
    applied = await apply_scoped_persona_merge(
        pm, "小天", GROUP_A, cluster, actions, "h",
    )
    assert applied == 0  # foreign1 不可寻址 → 有效源 <2 → 拒绝
    persona = await pm.aensure_persona("小天")
    facts = persona[GROUP_A.persona_section_key]['facts']
    assert {e['id'] for e in facts} == {'p0', 'p1', 'foreign1'}


@pytest.mark.asyncio
async def test_apply_reflection_merge_full_contract(tmp_path):
    fs, pm, re = _install(str(tmp_path))
    refls = [
        _r_entry("r0", "群里最近在聊考研", GROUP_A),
        _r_entry("r1", "群聊话题以考研为主", GROUP_A),
        _r_entry("r2", "群主换了头像", GROUP_A),
    ]
    await re.asave_reflections("小天", refls)

    active = await re.aload_reflections("小天")
    cluster = [dict(r) for r in active]
    actions = [{
        'action': 'merge', 'source_ids': ['r0', 'r1'],
        'produce': {'text': '群聊近期的主要话题是考研'},
        'reason': 'duplicate',
    }]
    applied = await apply_scoped_reflection_merge(
        re, "小天", GROUP_A, cluster, actions, "hashR",
    )
    assert applied == 1

    full = await re._aload_reflections_full("小天")
    by_id = {r['id']: r for r in full}
    # 源条目保留在盘上但转终态 merged（归档不是删除的对偶语义）。
    assert by_id['r0']['status'] == 'merged'
    assert by_id['r1']['status'] == 'merged'
    merged_id = by_id['r0']['absorbed_into']
    assert merged_id and by_id['r1']['absorbed_into'] == merged_id
    merged = by_id[merged_id]
    # subject 戳 + confirmed 生命周期 + 渲染门最小正种子。
    assert merged['subject_kind'] == GROUP_A.kind
    assert merged['subject_id'] == GROUP_A.subject_id
    assert merged['scope'] == GROUP_A.scope
    assert merged['status'] == 'confirmed'
    assert merged['auto_confirmed'] is True
    assert merged['reinforcement'] >= 0.1
    assert merged['entity'] == GROUP_A.kind
    # source_fact_ids 并集（幂等/溯源都靠它）。
    assert set(merged['source_fact_ids']) == {"fact_r0", "fact_r1"}
    # 活跃读视角：merged 源不可见，产物可见。
    active_now = await re.aload_reflections("小天")
    active_ids = {r['id'] for r in active_now}
    assert 'r0' not in active_ids and 'r1' not in active_ids
    assert merged_id in active_ids
    # 幸存者 r2 盖 stamp。
    assert by_id['r2']['last_refine_cluster_hash'] == "hashR"


@pytest.mark.asyncio
async def test_apply_rejects_garbage_actions_without_stamping(tmp_path):
    fs, pm, re = _install(str(tmp_path))
    refls = [_r_entry(f"r{i}", f"文本{i}", GROUP_A) for i in range(3)]
    await re.asave_reflections("小天", refls)
    active = await re.aload_reflections("小天")
    cluster = [dict(r) for r in active]

    garbage = [
        {'action': 'discard', 'source_id': 'r0'},          # 非法 action
        {'action': 'merge', 'source_ids': ['r0']},          # <2 源
        {'action': 'merge', 'source_ids': ['r0', 'r1'],
         'produce': {'text': '   '}},                       # 空文本
    ]
    applied = await apply_scoped_reflection_merge(
        re, "小天", GROUP_A, cluster, garbage, "hashG",
    )
    assert applied == 0
    full = await re._aload_reflections_full("小天")
    assert {r['id'] for r in full} == {"r0", "r1", "r2"}
    # 垃圾输出不 stamp——下轮重试而不是 30 天静默跳过。
    assert all(not r.get('last_refine_cluster_hash') for r in full)


@pytest.mark.asyncio
async def test_apply_empty_actions_stamps_for_hash_skip(tmp_path):
    fs, pm, re = _install(str(tmp_path))
    refls = [_r_entry(f"r{i}", f"文本{i}", GROUP_A) for i in range(2)]
    await re.asave_reflections("小天", refls)
    active = await re.aload_reflections("小天")
    cluster = [dict(r) for r in active]

    applied = await apply_scoped_reflection_merge(
        re, "小天", GROUP_A, cluster, [], "hashN",
    )
    assert applied == 0
    full = await re._aload_reflections_full("小天")
    assert all(r['last_refine_cluster_hash'] == "hashN" for r in full)


@pytest.mark.asyncio
async def test_bump_helpers_persist_and_never_create_bogus_section(tmp_path):
    fs, pm, re = _install(str(tmp_path))
    persona = await pm.aensure_persona("小天")
    section = pm._get_section_facts(persona, GROUP_A.kind, subject=GROUP_A)
    e = pm._build_fact_entry("条目", 'manual', None, subject=GROUP_A)
    e['id'] = "p0"
    section.append(e)
    await pm.asave_persona("小天", persona)

    await abump_scoped_persona_refine_attempts(
        pm, "小天", GROUP_A, [dict(e)], "h",
    )
    persona = await pm.aensure_persona("小天")
    # 共享 bump 的 bug 形态：按 entity 名建出顶层 'group_chat' section。
    assert 'group_chat' not in persona
    bumped = persona[GROUP_A.persona_section_key]['facts'][0]
    assert bumped['refine_attempts'] == 1
    assert bumped['last_refine_attempt_at']

    refls = [_r_entry("r0", "文本", GROUP_A)]
    await re.asave_reflections("小天", refls)
    active = await re.aload_reflections("小天")
    await abump_scoped_reflection_refine_attempts(
        re, "小天", GROUP_A, [dict(active[0])], "h",
    )
    full = await re._aload_reflections_full("小天")
    assert full[0]['refine_attempts'] == 1


# ── prompt 与接线 ────────────────────────────────────────────────────


def test_scoped_refine_prompt_locales_and_placeholders():
    from config.prompts.prompts_memory import (
        SCOPED_MEMORY_REFINE_PROMPT,
        get_scoped_memory_refine_prompt,
    )
    assert set(SCOPED_MEMORY_REFINE_PROMPT) == {
        "zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt",
    }
    for lang, tmpl in SCOPED_MEMORY_REFINE_PROMPT.items():
        rendered = (
            get_scoped_memory_refine_prompt(lang)
            .replace("{CLUSTER}", "X")
            .replace("{COUNT}", "1")
        )
        assert "{CLUSTER}" not in rendered, lang
        assert "{COUNT}" not in rendered, lang
        # 水印分隔符全 locale 保持简体（既有约定）。
        assert "======以下为记忆群组======" in tmpl, lang
        assert "======以上为记忆群组======" in tmpl, lang
        # merge 单件套：本体四件套的другие action 不得进 lite prompt。
        assert '"action": "merge"' in tmpl, lang
        assert '"split"' not in tmpl, lang


def test_runtime_registers_scoped_refine_loop():
    """结构护栏：runtime 启动体必须注册 scoped refine cron。"""
    import inspect
    from app.memory_server import runtime as runtime_module

    src = inspect.getsource(runtime_module.ensure_memory_server_runtime_initialized)
    assert "_periodic_scoped_refine_loop()" in src


def test_scoped_refine_loop_gated_on_powerful_memory():
    import inspect
    from app.memory_server import refine_loops

    src = inspect.getsource(refine_loops._periodic_scoped_refine_loop)
    assert "_ais_powerful_memory_enabled" in src
