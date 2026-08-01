# -*- coding: utf-8 -*-
"""Unit tests for memory.scoped_refine — the scoped lite refine engine.

Contracts under test (group-memory series 5/7, mainline 2):

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
    """Entries of group A and group B all share entity='group_chat' —
    bucketing MUST isolate by subject. An entity-keyed implementation
    would pool both groups together and merge across the boundary."""
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


def test_gather_excludes_suppressed_entries():
    """codex P2: suppressed entries live in the renderer's do-not-mention
    channel; letting them into a merge would resurface the content as an
    ordinary visible memory."""
    entries = [_r_entry(f"a{i}", f"t{i}", GROUP_A) for i in range(8)]
    entries.append(_r_entry("supp", "t", GROUP_A, suppress=True))
    buckets = gather_scoped_refine_buckets({}, entries)
    ids = {e['id'] for b in buckets for e in b.entries}
    assert "supp" not in ids


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
    """Pin the cost contract: summary tier, extra_body OMITTED (= the
    provider-dialect thinking-off default), short timeout. A wrong
    implementation passing extra_body=None (thinking ON) or using the
    correction tier must go red here."""
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


@pytest.mark.asyncio
async def test_refine_pass_all_rejected_actions_count_as_failure():
    """codex P2: a syntactically valid list whose every action is rejected
    by the apply layer must count as a cluster failure (refine_attempts
    bump via failure_fn) — otherwise the unstamped poison cluster costs one
    LLM call per cron interval forever."""
    engine = _engine()
    va, vb = _vec_pair()
    entries = [
        _r_entry(f"a{i}", f"文本{i}", GROUP_A, va if i % 2 else vb)
        for i in range(8)
    ]
    failures = []

    async def _apply(bucket, cluster, actions, cluster_hash):
        return 0  # apply 层拒绝了全部 action

    async def _failure(bucket, cluster, cluster_hash):
        failures.append(cluster_hash)

    garbage = _make_llm([{"action": "discard", "source_id": "a0"}])
    with patch('utils.llm_client.create_chat_llm_async',
               AsyncMock(return_value=garbage)):
        result = await engine.refine_pass(
            gather_scoped_refine_buckets({}, entries),
            apply_fn=_apply, scope_label='t', failure_fn=_failure,
        )
    assert result['failed'] == 1
    assert result['resolved'] == 0
    assert len(failures) == 1

    # 对照：空数组 = 明确 no-op，按成功计，不触发 failure_fn。
    failures.clear()
    with patch('utils.llm_client.create_chat_llm_async',
               AsyncMock(return_value=_make_llm([]))):
        result = await engine.refine_pass(
            gather_scoped_refine_buckets({}, entries),
            apply_fn=_apply, scope_label='t', failure_fn=_failure,
        )
    assert result['resolved'] == 1
    assert failures == []


def test_render_cluster_trust_annotation_hook():
    """Interface shape for series 7/7: with trust_of supplied the line
    carries trust=, without it the field never appears."""
    cluster = [
        _r_entry("a1", "文本一", GROUP_A),
        _r_entry("a2", "文本二", GROUP_A),
    ]
    plain = ScopedLiteRefineEngine._render_cluster(cluster, None)
    assert "trust=" not in plain
    annotated = ScopedLiteRefineEngine._render_cluster(
        cluster, lambda e: 0.8 if e['id'] == 'a1' else None,
    )
    assert "(id=a1, trust=high)" in annotated
    assert "0.80" not in annotated
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
async def test_apply_persona_merge_uses_code_side_trust_and_keeps_rollback(tmp_path):
    fs, pm, re = _install(str(tmp_path))
    persona = await pm.aensure_persona("Neko")
    section = pm._get_section_facts(persona, GROUP_A.kind, subject=GROUP_A)
    low = pm._build_fact_entry(
        "小明不喜欢猫", "manual", None, subject=GROUP_A,
        speaker_provenance={"speaker_id": "qq:1001", "speaker_trust": 0.3},
    )
    high = pm._build_fact_entry(
        "小明喜欢猫", "manual", None, subject=GROUP_A,
        speaker_provenance={"speaker_id": "qq:2002", "speaker_trust": 0.8},
    )
    low["id"], high["id"] = "low", "high"
    section.extend([low, high])
    await pm.asave_persona("Neko", persona)

    applied = await apply_scoped_persona_merge(
        pm,
        "Neko",
        GROUP_A,
        [dict(low), dict(high)],
        [{
            "action": "merge",
            "source_ids": ["low", "high"],
            "produce": {"text": "小明不喜欢猫"},
        }],
        "trust-hash",
    )
    assert applied == 1
    persona = await pm.aensure_persona("Neko")
    facts = persona[GROUP_A.persona_section_key]["facts"]
    merged = next(entry for entry in facts if entry.get("merged_from_ids"))
    assert merged["text"] == "小明喜欢猫"
    assert merged["speaker_id"] == "qq:2002"
    assert merged["speaker_trust"] == pytest.approx(0.8)
    history = {item["text"]: item for item in merged["version_history"]}
    assert history["小明不喜欢猫"]["speaker_id"] == "qq:1001"
    assert history["小明喜欢猫"]["speaker_id"] == "qq:2002"


@pytest.mark.asyncio
async def test_trust_merge_leader_must_beat_runner_up_not_weakest(tmp_path):
    fs, pm, re = _install(str(tmp_path))
    persona = await pm.aensure_persona("Neko")
    section = pm._get_section_facts(persona, GROUP_A.kind, subject=GROUP_A)
    sources = []
    for fact_id, text, speaker_id, trust in (
        ("high", "小明喜欢猫", "qq:1001", 0.80),
        ("near", "小明不喜欢猫", "qq:1002", 0.75),
        ("low", "低信任离群说法", "qq:1003", 0.30),
    ):
        entry = pm._build_fact_entry(
            text, "manual", None, subject=GROUP_A,
            speaker_provenance={
                "speaker_id": speaker_id, "speaker_trust": trust,
            },
        )
        entry["id"] = fact_id
        sources.append(entry)
    section.extend(sources)
    await pm.asave_persona("Neko", persona)

    assert await apply_scoped_persona_merge(
        pm, "Neko", GROUP_A, [dict(entry) for entry in sources],
        [{
            "action": "merge",
            "source_ids": ["high", "near", "low"],
            "produce": {"text": "保留甲乙共同信息的模型合并"},
        }],
        "runner-up-hash",
    ) == 1
    persona = await pm.aensure_persona("Neko")
    merged = next(
        entry for entry in persona[GROUP_A.persona_section_key]["facts"]
        if entry.get("merged_from_ids")
    )
    assert merged["text"] == "保留甲乙共同信息的模型合并"
    assert "speaker_id" not in merged
    assert "speaker_trust" not in merged


@pytest.mark.asyncio
async def test_trust_merge_does_not_arbitrate_one_speaker_against_itself(tmp_path):
    fs, pm, re = _install(str(tmp_path))
    persona = await pm.aensure_persona("Neko")
    section = pm._get_section_facts(persona, GROUP_A.kind, subject=GROUP_A)
    sources = []
    for fact_id, text, speaker_id, trust in (
        ("a-old", "甲的早期说法", "qq:1001", 0.90),
        ("a-new", "甲的后续补充", "qq:1001", 0.70),
        ("b", "乙的独立信息", "qq:1002", 0.50),
    ):
        entry = pm._build_fact_entry(
            text, "manual", None, subject=GROUP_A,
            speaker_provenance={
                "speaker_id": speaker_id, "speaker_trust": trust,
            },
        )
        entry["id"] = fact_id
        sources.append(entry)
    section.extend(sources)
    await pm.asave_persona("Neko", persona)

    proposed = "模型合并保留甲的补充与乙的信息"
    assert await apply_scoped_persona_merge(
        pm, "Neko", GROUP_A, [dict(entry) for entry in sources],
        [{
            "action": "merge",
            "source_ids": ["a-old", "a-new", "b"],
            "produce": {"text": proposed},
        }],
        "same-speaker-snapshots-hash",
    ) == 1
    persona = await pm.aensure_persona("Neko")
    merged = next(
        entry for entry in persona[GROUP_A.persona_section_key]["facts"]
        if entry.get("merged_from_ids")
    )
    assert merged["text"] == proposed
    assert "speaker_id" not in merged
    assert "speaker_trust" not in merged
    assert [item["speaker_id"] for item in merged["version_history"]] == [
        "qq:1001", "qq:1001", "qq:1002",
    ]
    assert [item["speaker_trust"] for item in merged["version_history"]] == [
        0.90, 0.70, 0.50,
    ]


@pytest.mark.asyncio
async def test_trust_merge_does_not_override_an_unscored_source(tmp_path):
    fs, pm, re = _install(str(tmp_path))
    persona = await pm.aensure_persona("Neko")
    section = pm._get_section_facts(persona, GROUP_A.kind, subject=GROUP_A)
    sources = []
    for fact_id, text, provenance in (
        ("high", "高信任来源", {"speaker_id": "qq:1001", "speaker_trust": 0.9}),
        ("low", "低信任来源", {"speaker_id": "qq:1002", "speaker_trust": 0.3}),
        ("legacy", "无来源但独立的信息", None),
    ):
        entry = pm._build_fact_entry(
            text, "manual", None, subject=GROUP_A,
            speaker_provenance=provenance,
        )
        entry["id"] = fact_id
        sources.append(entry)
    section.extend(sources)
    await pm.asave_persona("Neko", persona)

    proposed = "模型合并保留无来源但独立的信息"
    assert await apply_scoped_persona_merge(
        pm, "Neko", GROUP_A, [dict(entry) for entry in sources],
        [{
            "action": "merge",
            "source_ids": ["high", "low", "legacy"],
            "produce": {"text": proposed},
        }],
        "unscored-source-hash",
    ) == 1
    persona = await pm.aensure_persona("Neko")
    merged = next(
        entry for entry in persona[GROUP_A.persona_section_key]["facts"]
        if entry.get("merged_from_ids")
    )
    assert merged["text"] == proposed
    assert "speaker_id" not in merged
    assert "speaker_trust" not in merged


@pytest.mark.asyncio
async def test_trust_merge_preserves_complementary_details(tmp_path):
    fs, pm, re = _install(str(tmp_path))
    persona = await pm.aensure_persona("Neko")
    section = pm._get_section_facts(persona, GROUP_A.kind, subject=GROUP_A)
    high = pm._build_fact_entry(
        "Alice likes cats", "manual", None, subject=GROUP_A,
        speaker_provenance={"speaker_id": "qq:1001", "speaker_trust": 0.9},
    )
    low = pm._build_fact_entry(
        "Alice likes cats and lives in Tokyo", "manual", None, subject=GROUP_A,
        speaker_provenance={"speaker_id": "qq:2002", "speaker_trust": 0.3},
    )
    high["id"], low["id"] = "high", "low"
    section.extend([high, low])
    await pm.asave_persona("Neko", persona)

    proposed = "Alice likes cats and lives in Tokyo"
    assert await apply_scoped_persona_merge(
        pm, "Neko", GROUP_A, [dict(high), dict(low)], [{
            "action": "merge", "source_ids": ["high", "low"],
            "produce": {"text": proposed},
        }], "complementary-hash",
    ) == 1
    persona = await pm.aensure_persona("Neko")
    merged = next(
        entry for entry in persona[GROUP_A.persona_section_key]["facts"]
        if entry.get("merged_from_ids")
    )
    assert merged["text"] == proposed
    assert "speaker_id" not in merged
    assert {item["text"] for item in merged["version_history"]} == {
        "Alice likes cats", "Alice likes cats and lives in Tokyo",
    }


@pytest.mark.asyncio
async def test_trust_winner_must_conflict_with_every_consumed_source(tmp_path):
    fs, pm, re = _install(str(tmp_path))
    persona = await pm.aensure_persona("Neko")
    section = pm._get_section_facts(persona, GROUP_A.kind, subject=GROUP_A)
    sources = []
    for fact_id, text, speaker_id, trust in (
        ("leader", "Alice lives in Tokyo", "qq:1001", 0.90),
        ("cats", "小明喜欢猫", "qq:1002", 0.60),
        ("no-cats", "小明不喜欢猫", "qq:1003", 0.30),
    ):
        entry = pm._build_fact_entry(
            text, "manual", None, subject=GROUP_A,
            speaker_provenance={
                "speaker_id": speaker_id, "speaker_trust": trust,
            },
        )
        entry["id"] = fact_id
        sources.append(entry)
    section.extend(sources)
    await pm.asave_persona("Neko", persona)

    proposed = "Alice lives in Tokyo；小明是否喜欢猫仍有冲突"
    assert await apply_scoped_persona_merge(
        pm, "Neko", GROUP_A, [dict(entry) for entry in sources], [{
            "action": "merge",
            "source_ids": ["leader", "cats", "no-cats"],
            "produce": {"text": proposed},
        }], "mixed-cluster-hash",
    ) == 1
    persona = await pm.aensure_persona("Neko")
    merged = next(
        entry for entry in persona[GROUP_A.persona_section_key]["facts"]
        if entry.get("merged_from_ids")
    )
    assert merged["text"] == proposed
    assert "speaker_id" not in merged
    assert {item["text"] for item in merged["version_history"]} == {
        "Alice lives in Tokyo", "小明喜欢猫", "小明不喜欢猫",
    }


@pytest.mark.asyncio
async def test_reflection_trust_winner_owns_semantic_metadata(tmp_path):
    fs, pm, re = _install(str(tmp_path))
    low = _r_entry(
        "low", "小明不喜欢猫", GROUP_A,
        speaker_id="qq:1001", speaker_trust=0.3,
        relation_type="habit", temporal_scope="past",
        event_when_raw={"kind": "absolute", "value": "2026-01-01"},
        event_start_at="2026-01-01T00:00:00",
        event_end_at="2026-01-31T00:00:00",
        schema_version=1,
    )
    high = _r_entry(
        "high", "小明喜欢猫", GROUP_A,
        speaker_id="qq:2002", speaker_trust=0.9,
        relation_type="preference", temporal_scope="current",
        event_when_raw={"kind": "absolute", "value": "2026-06-01"},
        event_start_at="2026-06-01T00:00:00",
        event_end_at=None,
        schema_version=2,
    )
    await re.asave_reflections("小天", [low, high])
    cluster = [dict(row) for row in await re.aload_reflections("小天")]

    assert await apply_scoped_reflection_merge(
        re, "小天", GROUP_A, cluster, [{
            "action": "merge", "source_ids": ["low", "high"],
            "produce": {"text": "模型错误合并"},
        }], "trust-metadata-hash",
    ) == 1
    full = await re._aload_reflections_full("小天")
    merged = next(row for row in full if row.get("merged_from_ids"))
    assert merged["text"] == "小明喜欢猫"
    assert merged["relation_type"] == "preference"
    assert merged["temporal_scope"] == "current"
    assert merged["event_when_raw"] == {
        "kind": "absolute", "value": "2026-06-01",
    }
    assert merged["event_start_at"] == "2026-06-01T00:00:00"
    assert merged["event_end_at"] is None
    assert merged["schema_version"] == 2
    assert set(merged["source_fact_ids"]) == {"fact_low", "fact_high"}
    assert merged["merged_from_ids"] == ["low", "high"]


@pytest.mark.asyncio
async def test_apply_persona_merge_cannot_touch_other_scope_rows(tmp_path):
    """One section key may legally mix entries from different custom
    scopes; even if the LLM hallucinates another scope's id, that row
    must never be touched."""
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
async def test_apply_rejects_source_whose_text_changed_since_cluster(tmp_path):
    """greptile P1: the LLM's decision was made about the texts in the
    cluster snapshot. If a concurrent writer changed a source row during
    the unlocked LLM window, merging it by bare id would consume content
    the model never saw — the stale source must invalidate the action."""
    fs, pm, re = _install(str(tmp_path))
    refls = [
        _r_entry("r0", "原始文本甲", GROUP_A),
        _r_entry("r1", "原始文本乙", GROUP_A),
    ]
    await re.asave_reflections("小天", refls)
    active = await re.aload_reflections("小天")
    cluster = [dict(r) for r in active]  # LLM 看到的快照

    # LLM 调用窗口内并发写者改了 r1 的文本。
    live = await re.aload_reflections("小天")
    next(r for r in live if r['id'] == "r1")['text'] = "被并发改写的文本"
    await re.asave_reflections("小天", live)

    actions = [{
        'action': 'merge', 'source_ids': ['r0', 'r1'],
        'produce': {'text': '基于旧快照的合并结论'},
    }]
    applied = await apply_scoped_reflection_merge(
        re, "小天", GROUP_A, cluster, actions, "hashS",
    )
    assert applied == 0
    full = await re._aload_reflections_full("小天")
    by_id = {r['id']: r for r in full}
    assert set(by_id) == {"r0", "r1"}
    assert by_id['r0']['status'] == 'confirmed'
    assert by_id['r1']['text'] == "被并发改写的文本"


@pytest.mark.asyncio
async def test_apply_rejects_source_suppressed_since_cluster(tmp_path):
    """greptile round-2 P1: a source marked suppress=True during the
    unlocked LLM window has unchanged text, so the snapshot check alone
    passes — the apply layer must re-check suppression or the merge
    resurfaces do-not-mention content as an ordinary visible entry."""
    fs, pm, re = _install(str(tmp_path))
    refls = [
        _r_entry("r0", "普通反思", GROUP_A),
        _r_entry("r1", "待抑制反思", GROUP_A),
    ]
    await re.asave_reflections("小天", refls)
    active = await re.aload_reflections("小天")
    cluster = [dict(r) for r in active]

    # LLM 窗口内 r1 被标记 suppress（文本未变）。
    live = await re.aload_reflections("小天")
    next(r for r in live if r['id'] == "r1")['suppress'] = True
    await re.asave_reflections("小天", live)

    actions = [{
        'action': 'merge', 'source_ids': ['r0', 'r1'],
        'produce': {'text': '合并产物'},
    }]
    applied = await apply_scoped_reflection_merge(
        re, "小天", GROUP_A, cluster, actions, "hashSup",
    )
    assert applied == 0
    full = await re._aload_reflections_full("小天")
    by_id = {r['id']: r for r in full}
    assert set(by_id) == {"r0", "r1"}
    assert by_id['r1']['suppress'] is True
    assert by_id['r1']['status'] == 'confirmed'  # 未被消费


@pytest.mark.asyncio
async def test_reflection_merge_unions_event_window(tmp_path):
    """codex round-4: a contradiction conclusion spans every source's
    period — inheriting the first source's window would anchor 'used to X,
    later Y' to the OLD period and time-scoped recall would miss it."""
    fs, pm, re = _install(str(tmp_path))
    old = _r_entry("r0", "曾经喜欢猫", GROUP_A,
                   event_start_at="2026-01-01T00:00:00",
                   event_end_at="2026-02-01T00:00:00")
    new = _r_entry("r1", "后来讨厌猫", GROUP_A,
                   event_start_at="2026-05-01T00:00:00",
                   event_end_at="2026-06-01T00:00:00")
    await re.asave_reflections("小天", [old, new])
    active = await re.aload_reflections("小天")
    cluster = [dict(r) for r in active]
    actions = [{
        'action': 'merge', 'source_ids': ['r0', 'r1'],
        'produce': {'text': '曾喜欢猫，后来转为讨厌'},
    }]
    applied = await apply_scoped_reflection_merge(
        re, "小天", GROUP_A, cluster, actions, "hashW",
    )
    assert applied == 1
    full = await re._aload_reflections_full("小天")
    merged = next(r for r in full if r.get('merged_from_ids'))
    assert merged['event_start_at'] == "2026-01-01T00:00:00"
    assert merged['event_end_at'] == "2026-06-01T00:00:00"

    # 任一源无结束点（pattern/进行中）→ 并集也无结束点。
    ongoing = _r_entry("r2", "持续在群里活跃", GROUP_A,
                       event_start_at="2026-03-01T00:00:00",
                       event_end_at=None)
    bounded = _r_entry("r3", "上月很活跃", GROUP_A,
                       event_start_at="2026-06-01T00:00:00",
                       event_end_at="2026-06-30T00:00:00")
    await re.asave_reflections(
        "小天", (await re.aload_reflections("小天")) + [ongoing, bounded],
    )
    active2 = await re.aload_reflections("小天")
    cluster2 = [dict(r) for r in active2 if r['id'] in ("r2", "r3")]
    actions2 = [{
        'action': 'merge', 'source_ids': ['r2', 'r3'],
        'produce': {'text': '长期活跃'},
    }]
    await apply_scoped_reflection_merge(
        re, "小天", GROUP_A, cluster2, actions2, "hashW2",
    )
    full2 = await re._aload_reflections_full("小天")
    merged2 = next(
        r for r in full2
        if set(r.get('merged_from_ids') or []) == {"r2", "r3"}
    )
    assert merged2['event_start_at'] == "2026-03-01T00:00:00"
    assert merged2['event_end_at'] is None

    # Offset-bearing imports must be ordered by represented instant, not by
    # their lexicographic wall-clock strings. These two values make both the
    # lexical min and max choose the wrong source.
    offset_a = _r_entry(
        "r4", "跨时区较早", GROUP_A,
        event_start_at="2026-01-01T00:30:00+02:00",
        event_end_at="2026-01-01T00:30:00+02:00",
    )
    offset_b = _r_entry(
        "r5", "跨时区较晚", GROUP_A,
        event_start_at="2025-12-31T23:00:00+00:00",
        event_end_at="2025-12-31T23:00:00+00:00",
    )
    await re.asave_reflections(
        "小天", (await re.aload_reflections("小天")) + [offset_a, offset_b],
    )
    active3 = await re.aload_reflections("小天")
    cluster3 = [dict(r) for r in active3 if r['id'] in ("r4", "r5")]
    actions3 = [{
        'action': 'merge', 'source_ids': ['r4', 'r5'],
        'produce': {'text': '跨时区时间窗'},
    }]
    await apply_scoped_reflection_merge(
        re, "小天", GROUP_A, cluster3, actions3, "hashW3",
    )
    full3 = await re._aload_reflections_full("小天")
    merged3 = next(
        r for r in full3
        if set(r.get('merged_from_ids') or []) == {"r4", "r5"}
    )
    assert merged3['event_start_at'] == "2026-01-01T00:30:00+02:00"
    assert merged3['event_end_at'] == "2025-12-31T23:00:00+00:00"


@pytest.mark.asyncio
async def test_persona_merge_preserves_all_upstream_source_ids(tmp_path):
    """codex round-4: the merged persona entry must keep EVERY source's
    upstream reflection id so the time-driven promotion idempotency check
    still finds its carrier after a merge — otherwise the half-committed
    'persona written, reflection status save failed' retry re-promotes a
    duplicate."""
    fs, pm, re = _install(str(tmp_path))
    persona = await pm.aensure_persona("小天")
    section = pm._get_section_facts(persona, GROUP_A.kind, subject=GROUP_A)
    for i, rid in enumerate(("ref_aaa", "ref_bbb")):
        entry = pm._build_fact_entry(
            f"晋升条目{i}", 'reflection_time_driven', rid, subject=GROUP_A,
        )
        entry['id'] = f"p{i}"
        section.append(entry)
    await pm.asave_persona("小天", persona)

    cluster = [dict(e) for e in section]
    actions = [{
        'action': 'merge', 'source_ids': ['p0', 'p1'],
        'produce': {'text': '合并后的群人设条目'},
    }]
    applied = await apply_scoped_persona_merge(
        pm, "小天", GROUP_A, cluster, actions, "hashP",
    )
    assert applied == 1
    persona = await pm.aensure_persona("小天")
    merged = next(
        e for e in persona[GROUP_A.persona_section_key]['facts']
        if e.get('merged_from_ids')
    )
    assert set(merged['merged_source_ids']) == {"ref_aaa", "ref_bbb"}

    # 幂等检查对两个上游 reflection id 都能找到载体。
    assert await re._ascoped_promotion_already_applied(
        "小天", "ref_aaa", GROUP_A,
    ) is True
    assert await re._ascoped_promotion_already_applied(
        "小天", "ref_bbb", GROUP_A,
    ) is True
    assert await re._ascoped_promotion_already_applied(
        "小天", "ref_zzz", GROUP_A,
    ) is False


@pytest.mark.asyncio
async def test_apply_rejects_whole_action_when_one_of_many_sources_invalidated(tmp_path):
    """greptile round-5 P1: with >=3 named sources, invalidating ONE must
    reject the WHOLE action — the conclusion was generated from the full
    snapshot, so merging the surviving pair would persist content whose
    only support was the invalidated (e.g. freshly suppressed) source."""
    fs, pm, re = _install(str(tmp_path))
    refls = [
        _r_entry("r0", "甲说喜欢猫", GROUP_A),
        _r_entry("r1", "甲对猫很感兴趣", GROUP_A),
        _r_entry("r2", "甲养了一只猫", GROUP_A),
    ]
    await re.asave_reflections("小天", refls)
    active = await re.aload_reflections("小天")
    cluster = [dict(r) for r in active]

    # LLM 窗口内第三个源被 suppress（文本未变）。
    live = await re.aload_reflections("小天")
    next(r for r in live if r['id'] == "r2")['suppress'] = True
    await re.asave_reflections("小天", live)

    actions = [{
        'action': 'merge', 'source_ids': ['r0', 'r1', 'r2'],
        'produce': {'text': '甲喜欢猫、感兴趣并且养了一只'},
    }]
    applied = await apply_scoped_reflection_merge(
        re, "小天", GROUP_A, cluster, actions, "hashAON",
    )
    # 整条 action 拒绝——绝不「剔除 r2、合并 r0+r1」把 r2 的内容经结论
    # 文本洗出来。
    assert applied == 0
    full = await re._aload_reflections_full("小天")
    by_id = {r['id']: r for r in full}
    assert set(by_id) == {"r0", "r1", "r2"}
    assert by_id['r0']['status'] == 'confirmed'
    assert by_id['r1']['status'] == 'confirmed'


@pytest.mark.asyncio
async def test_apply_skips_stamp_for_survivor_whose_text_drifted(tmp_path):
    """codex round-3: the id-only cluster hash doesn't change when a
    survivor's text is edited during the LLM window — stamping it anyway
    would hash-skip the unreviewed text for 30 days. Drifted survivors
    stay unstamped and re-enter review next round."""
    fs, pm, re = _install(str(tmp_path))
    refls = [
        _r_entry("r0", "重复甲", GROUP_A),
        _r_entry("r1", "重复乙", GROUP_A),
        _r_entry("r2", "幸存者原文", GROUP_A),
    ]
    await re.asave_reflections("小天", refls)
    active = await re.aload_reflections("小天")
    cluster = [dict(r) for r in active]

    # LLM 窗口内幸存者 r2 被并发改写。
    live = await re.aload_reflections("小天")
    next(r for r in live if r['id'] == "r2")['text'] = "幸存者被改写"
    await re.asave_reflections("小天", live)

    actions = [{
        'action': 'merge', 'source_ids': ['r0', 'r1'],
        'produce': {'text': '合并结论'},
    }]
    applied = await apply_scoped_reflection_merge(
        re, "小天", GROUP_A, cluster, actions, "hashD",
    )
    assert applied == 1  # merge 本身照常应用
    full = await re._aload_reflections_full("小天")
    by_id = {r['id']: r for r in full}
    assert by_id['r0']['status'] == 'merged'
    # 文本漂移的幸存者不 stamp。
    assert not by_id['r2'].get('last_refine_cluster_hash')


@pytest.mark.asyncio
async def test_apply_skips_action_on_produced_id_collision(tmp_path):
    """coderabbit round-3: produced ids derive from output text + time
    salt; two identical texts in one batch can collide. The colliding
    action is skipped without consuming its sources."""
    from unittest.mock import patch as _patch

    fs, pm, re = _install(str(tmp_path))
    refls = [_r_entry(f"r{i}", f"文本{i}", GROUP_A) for i in range(4)]
    await re.asave_reflections("小天", refls)
    active = await re.aload_reflections("小天")
    cluster = [dict(r) for r in active]
    actions = [
        {'action': 'merge', 'source_ids': ['r0', 'r1'],
         'produce': {'text': '同样的结论'}},
        {'action': 'merge', 'source_ids': ['r2', 'r3'],
         'produce': {'text': '同样的结论'}},
    ]
    with _patch("memory.scoped_refine.refine_reflection_id",
                return_value="ref_fixed"):
        applied = await apply_scoped_reflection_merge(
            re, "小天", GROUP_A, cluster, actions, "hashC",
        )
    assert applied == 1
    full = await re._aload_reflections_full("小天")
    by_id = {r['id']: r for r in full}
    # 第一条应用、第二条撞车跳过：r2/r3 未被消费。
    assert by_id['r0']['status'] == 'merged'
    assert by_id['r2']['status'] == 'confirmed'
    assert by_id['r3']['status'] == 'confirmed'
    assert len([r for r in full if r['id'] == "ref_fixed"]) == 1


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
    """Structural guardrail: runtime startup must register the scoped
    refine cron."""
    import inspect
    from app.memory_server import runtime as runtime_module

    src = inspect.getsource(runtime_module.ensure_memory_server_runtime_initialized)
    assert "_periodic_scoped_refine_loop()" in src


def test_scoped_refine_loop_gated_on_powerful_memory():
    import inspect
    from app.memory_server import refine_loops

    src = inspect.getsource(refine_loops._periodic_scoped_refine_loop)
    assert "_ais_powerful_memory_enabled" in src


@pytest.mark.asyncio
async def test_scoped_refine_round_caps_calls_and_rotates_characters(monkeypatch):
    """One global round serves at most one character and rotates fairly."""
    from app.memory_server import refine_loops

    calls = []
    served = {"甲": False, "乙": True, "丙": True}

    async def _fake_run(name):
        calls.append(name)
        return served[name]

    monkeypatch.setattr(refine_loops, "_run_scoped_refine_for_character", _fake_run)
    monkeypatch.setattr(refine_loops, "_scoped_refine_character_cursor", None)

    await refine_loops._run_scoped_refine_round(["甲", "乙", "丙"])
    assert calls == ["甲", "乙"]
    assert refine_loops._scoped_refine_character_cursor == "乙"

    calls.clear()
    await refine_loops._run_scoped_refine_round(["甲", "乙", "丙"])
    assert calls == ["丙"]
    assert refine_loops._scoped_refine_character_cursor == "丙"
