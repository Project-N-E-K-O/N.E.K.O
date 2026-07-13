# -*- coding: utf-8 -*-
"""Phase A-3 — ReflectionEngine.apply_refine_actions: fact 不可变 + 四件套。

cluster 内 fact 是只读 info source；代码层必须 reject 任何针对 fact id
的 split / discard / modify，以及 merge.source_ids 含 fact id 的 action。
fact 可以作为 merge / modify 的 absorbed_from_fact_ids（吸收信息到产物
的 source_fact_ids 中）。"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_cm(tmpdir):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
    return cm


def _install(tmpdir):
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine

    cm = _mock_cm(tmpdir)
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.persona.manager.get_config_manager", return_value=cm), \
         patch("memory.facts.get_config_manager", return_value=cm), \
         patch("memory.reflection.manager.get_config_manager", return_value=cm):
        event_log = EventLog()
        fact_store = FactStore()
        pm = PersonaManager(event_log=event_log)
        engine = ReflectionEngine(fact_store=fact_store, persona_manager=pm)
        event_log._config_manager = cm
        fact_store._config_manager = cm
        pm._config_manager = cm
        engine._config_manager = cm
    return engine


async def _seed_refl(engine, name, **kw):
    refls = await engine.aload_reflections(name)
    refl = engine._normalize_reflection({
        'id': kw.get('id', 'r_test'),
        'text': kw.get('text', 'text'),
        'entity': kw.get('entity', 'master'),
        'status': kw.get('status', 'pending'),
        'source_fact_ids': kw.get('source_fact_ids', []),
        'created_at': datetime.now().isoformat(),
        'relation_type': kw.get('relation_type'),
        'temporal_scope': kw.get('temporal_scope'),
    })
    refls.append(refl)
    await engine.asave_reflections(name, refls)
    return refl


def _annot_refl(r, entity='master'):
    from memory.refine import annotate_entry
    return annotate_entry(r, type_='reflection', entity=entity)


def _annot_fact(fid, text='fact text', importance=5, entity='master'):
    from memory.refine import annotate_entry
    return annotate_entry(
        {'id': fid, 'text': text, 'importance': importance},
        type_='fact', entity=entity,
    )


@pytest.mark.asyncio
async def test_fact_cannot_be_split_source(tmp_path):
    engine = _install(str(tmp_path))
    cluster = [
        _annot_fact('f_immutable'),
        _annot_refl(await _seed_refl(engine, "小天", id='r1', text='r')),
    ]
    actions = [{
        'action': 'split',
        'source_id': 'f_immutable',
        'produce': [{'text': 'A'}, {'text': 'B'}],
    }]
    applied = await engine.apply_refine_actions("小天", "master", cluster, actions, 'h1')
    assert applied == 0


@pytest.mark.asyncio
async def test_fact_cannot_be_modify_source(tmp_path):
    engine = _install(str(tmp_path))
    cluster = [
        _annot_fact('f_immutable'),
        _annot_refl(await _seed_refl(engine, "小天", id='r1', text='r')),
    ]
    actions = [{
        'action': 'modify',
        'source_id': 'f_immutable',
        'produce': {'text': 'rewritten'},
        'reason': 'x',
    }]
    applied = await engine.apply_refine_actions("小天", "master", cluster, actions, 'h1b')
    assert applied == 0


@pytest.mark.asyncio
async def test_fact_cannot_be_discard_source(tmp_path):
    engine = _install(str(tmp_path))
    cluster = [
        _annot_fact('f_immutable'),
        _annot_refl(await _seed_refl(engine, "小天", id='r1', text='r')),
    ]
    actions = [{
        'action': 'discard',
        'source_id': 'f_immutable',
        'reason': 'noise',
    }]
    applied = await engine.apply_refine_actions("小天", "master", cluster, actions, 'h1c')
    assert applied == 0


@pytest.mark.asyncio
async def test_merge_rejected_when_source_ids_contains_fact(tmp_path):
    """整个 merge action 拒绝，不是 partial 应用。"""
    engine = _install(str(tmp_path))
    r1 = await _seed_refl(engine, "小天", id='r1', text='r1')
    r2 = await _seed_refl(engine, "小天", id='r2', text='r2')
    cluster = [
        _annot_refl(r1),
        _annot_refl(r2),
        _annot_fact('f1'),
    ]
    actions = [{
        'action': 'merge',
        'source_ids': ['r1', 'r2', 'f1'],
        'produce': {'text': 'merged'},
    }]
    applied = await engine.apply_refine_actions("小天", "master", cluster, actions, 'h2')
    assert applied == 0
    refls = await engine.aload_reflections("小天")
    ids = [r.get('id') for r in refls]
    assert 'r1' in ids and 'r2' in ids


@pytest.mark.asyncio
async def test_modify_absorbs_fact_into_source_fact_ids(tmp_path):
    engine = _install(str(tmp_path))
    r1 = await _seed_refl(
        engine, "小天", id='r1', text='old',
        source_fact_ids=['existing_fact'],
    )
    cluster = [
        _annot_refl(r1),
        _annot_fact('f_new', 'fact info'),
    ]
    actions = [{
        'action': 'modify',
        'source_id': 'r1',
        'absorbed_from_fact_ids': ['f_new'],
        'produce': {'text': 'new text'},
        'reason': '吸收 f_new 后表述更准',
    }]
    applied = await engine.apply_refine_actions("小天", "master", cluster, actions, 'h3')
    assert applied == 1
    refls = await engine.aload_reflections("小天")
    target = next(r for r in refls if r.get('id') == 'r1')
    assert target['text'] == 'new text'
    assert 'existing_fact' in target['source_fact_ids']
    assert 'f_new' in target['source_fact_ids']
    # 文本变 → embedding 三字段清空
    assert target['embedding'] is None
    assert target['embedding_text_sha256'] is None
    assert target['embedding_model_id'] is None
    # modification_history 记录
    mh = target.get('modification_history') or []
    assert mh and mh[-1]['reason'] == '吸收 f_new 后表述更准'
    assert mh[-1]['absorbed_fact_ids'] == ['f_new']


@pytest.mark.asyncio
async def test_merge_with_absorbed_fact_ids_combines_sources(tmp_path):
    engine = _install(str(tmp_path))
    r1 = await _seed_refl(engine, "小天", id='r1', text='a', source_fact_ids=['f_old_1'])
    r2 = await _seed_refl(engine, "小天", id='r2', text='b', source_fact_ids=['f_old_2'])
    cluster = [
        _annot_refl(r1),
        _annot_refl(r2),
        _annot_fact('f_new', 'extra info'),
    ]
    actions = [{
        'action': 'merge',
        'source_ids': ['r1', 'r2'],
        'absorbed_from_fact_ids': ['f_new'],
        'produce': {
            'text': 'merged',
            'relation_type': 'preference',
            'temporal_scope': 'pattern',
        },
    }]
    applied = await engine.apply_refine_actions("小天", "master", cluster, actions, 'h4')
    assert applied == 1
    refls = await engine.aload_reflections("小天")
    target = next(r for r in refls if r.get('text') == 'merged')
    assert set(target['source_fact_ids']) == {'f_old_1', 'f_old_2', 'f_new'}
    assert target['relation_type'] == 'preference'
    assert target['temporal_scope'] == 'pattern'
    ids = [r.get('id') for r in refls]
    assert 'r1' not in ids and 'r2' not in ids


@pytest.mark.asyncio
async def test_discard_reflection_removes_it(tmp_path):
    engine = _install(str(tmp_path))
    r1 = await _seed_refl(engine, "小天", id='r1', text='discard me')
    cluster = [_annot_refl(r1)]
    actions = [{'action': 'discard', 'source_id': 'r1', 'reason': 'noise'}]
    applied = await engine.apply_refine_actions("小天", "master", cluster, actions, 'h5')
    assert applied == 1
    refls = await engine.aload_reflections("小天")
    assert all(r.get('id') != 'r1' for r in refls)


@pytest.mark.asyncio
async def test_all_malformed_actions_does_not_stamp_reflection(tmp_path):
    """Codex P2 修复：非空 actions 全 reject (含针对 fact id 的非法 source_id)
    → applied=0 但不 stamp，等下轮重试。"""
    engine = _install(str(tmp_path))
    r1 = await _seed_refl(engine, "小天", id='r_keep', text='kept')
    cluster = [_annot_refl(r1), _annot_fact('f_ctx')]
    actions = [
        {'action': 'split', 'source_id': 'f_ctx',  # fact 不可 split
         'produce': [{'text': 'a'}, {'text': 'b'}]},
        {'action': 'invent_an_action', 'source_id': 'r_keep'},
    ]
    applied = await engine.apply_refine_actions("小天", "master", cluster, actions, 'h_bad')
    assert applied == 0
    refls = await engine.aload_reflections("小天")
    target = next(r for r in refls if r.get('id') == 'r_keep')
    assert target.get('last_refine_cluster_hash') is None
    assert target.get('last_refine_at') is None


@pytest.mark.asyncio
async def test_empty_actions_still_stamps_cluster_reflections(tmp_path):
    """Codex P1 修复：LLM 返回 [] (no-op) 也必须 stamp 整个 cluster
    内的 reflection（fact 跳过），否则下轮 cron 重复审视。"""
    engine = _install(str(tmp_path))
    r1 = await _seed_refl(engine, "小天", id='r_a', text='one')
    r2 = await _seed_refl(engine, "小天", id='r_b', text='two')
    cluster = [_annot_refl(r1), _annot_refl(r2), _annot_fact('f_ctx')]
    applied = await engine.apply_refine_actions("小天", "master", cluster, [], 'h_noop')
    assert applied == 0
    refls = await engine.aload_reflections("小天")
    by_id = {r.get('id'): r for r in refls}
    assert by_id['r_a']['last_refine_cluster_hash'] == 'h_noop'
    assert by_id['r_b']['last_refine_cluster_hash'] == 'h_noop'
    assert by_id['r_a']['last_refine_at'] and by_id['r_b']['last_refine_at']
