# -*- coding: utf-8 -*-
"""Phase A-3 — PersonaManager.apply_refine_actions: 四件套 + protected 兜底。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_cm(tmpdir: str):
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


def _install(tmpdir: str):
    from memory.event_log import EventLog
    from memory.persona import PersonaManager
    cm = _mock_cm(tmpdir)
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.persona.manager.get_config_manager", return_value=cm):
        event_log = EventLog()
        event_log._config_manager = cm
        pm = PersonaManager(event_log=event_log)
        pm._config_manager = cm
    return pm


async def _seed(pm, name, text, **overrides):
    persona = await pm.aensure_persona(name)
    entry = pm._normalize_entry(text)
    entry.update(overrides)
    pm._get_section_facts(persona, "master").append(entry)
    await pm.asave_persona(name, persona)
    persona = await pm.aensure_persona(name)
    return next(
        e for e in pm._get_section_facts(persona, "master")
        if isinstance(e, dict) and e.get("text") == text
    )


def _annotate(entry, entity='master'):
    from memory.refine import annotate_entry
    return annotate_entry(entry, type_='persona', entity=entity)


@pytest.mark.asyncio
async def test_merge_consumes_sources_and_produces_new_entry(tmp_path):
    pm = _install(str(tmp_path))
    s1 = await _seed(pm, "小天", "主人喜欢咖啡", id='p_coffee_1')
    s2 = await _seed(pm, "小天", "主人早上要靠咖啡因开机", id='p_coffee_2')
    cluster = [_annotate(s1), _annotate(s2)]
    actions = [{
        'action': 'merge',
        'source_ids': ['p_coffee_1', 'p_coffee_2'],
        'produce': {'text': '主人对咖啡有强依赖性'},
    }]
    applied = await pm.apply_refine_actions("小天", "master", cluster, actions, 'h001')
    assert applied == 1
    persona = await pm.aensure_persona("小天")
    section = pm._get_section_facts(persona, "master")
    texts = [e.get('text') for e in section if isinstance(e, dict)]
    assert "主人喜欢咖啡" not in texts
    assert "主人早上要靠咖啡因开机" not in texts
    assert "主人对咖啡有强依赖性" in texts
    merged = next(e for e in section if e.get('text') == '主人对咖啡有强依赖性')
    assert len(merged.get('version_history') or []) == 2


@pytest.mark.asyncio
async def test_split_consumes_source_and_produces_multiple(tmp_path):
    pm = _install(str(tmp_path))
    src = await _seed(pm, "小天", "主人喜欢咖啡且早起", id='p_mixed_1')
    cluster = [_annotate(src)]
    actions = [{
        'action': 'split',
        'source_id': 'p_mixed_1',
        'produce': [
            {'text': '主人喜欢咖啡'},
            {'text': '主人早起'},
        ],
    }]
    applied = await pm.apply_refine_actions("小天", "master", cluster, actions, 'h002')
    assert applied == 1
    persona = await pm.aensure_persona("小天")
    texts = [
        e.get('text') for e in pm._get_section_facts(persona, "master")
        if isinstance(e, dict)
    ]
    assert "主人喜欢咖啡且早起" not in texts
    assert "主人喜欢咖啡" in texts
    assert "主人早起" in texts


@pytest.mark.asyncio
async def test_split_carries_forward_evidence_proportionally(tmp_path):
    """Codex P1 修复：split 不能默默清零 evidence。可分配 counters 按
    1/N 分摊，时间戳/天数/溯源直接继承。"""
    pm = _install(str(tmp_path))
    src = await _seed(
        pm, "小天", "主人喜欢咖啡且早起",
        id='p_mixed',
        reinforcement=6.0,
        disputation=2.0,
        user_fact_reinforce_count=4,
        sub_zero_days=3,
        rein_last_signal_at='2026-04-01T10:00:00',
        disp_last_signal_at='2026-04-02T10:00:00',
        sub_zero_last_increment_date='2026-04-03',
    )
    cluster = [_annotate(src)]
    actions = [{
        'action': 'split',
        'source_id': 'p_mixed',
        'produce': [
            {'text': '主人喜欢咖啡'},
            {'text': '主人早起'},
        ],
    }]
    applied = await pm.apply_refine_actions("小天", "master", cluster, actions, 'h_split_ev')
    assert applied == 1
    persona = await pm.aensure_persona("小天")
    section = pm._get_section_facts(persona, "master")
    produced = [e for e in section if e.get('text') in ('主人喜欢咖啡', '主人早起')]
    assert len(produced) == 2
    for e in produced:
        # 等分：reinforcement 6/2=3.0, disputation 2/2=1.0, user_count 4//2=2
        assert e['reinforcement'] == pytest.approx(3.0)
        assert e['disputation'] == pytest.approx(1.0)
        assert e['user_fact_reinforce_count'] == 2
        # 时间戳 / 天数 / 溯源直接继承（不分摊）
        assert e['rein_last_signal_at'] == '2026-04-01T10:00:00'
        assert e['disp_last_signal_at'] == '2026-04-02T10:00:00'
        assert e['sub_zero_days'] == 3
        assert e['sub_zero_last_increment_date'] == '2026-04-03'


@pytest.mark.asyncio
async def test_modify_keeps_id_appends_version_history(tmp_path):
    pm = _install(str(tmp_path))
    src = await _seed(pm, "小天", "主人住在东京", id='p_loc_1')
    cluster = [_annotate(src)]
    actions = [{
        'action': 'modify',
        'source_id': 'p_loc_1',
        'produce': {'text': '主人最近搬到了大阪'},
        'reason': '基于近期 fact_xyz',
    }]
    applied = await pm.apply_refine_actions("小天", "master", cluster, actions, 'h003')
    assert applied == 1
    persona = await pm.aensure_persona("小天")
    target = next(
        e for e in pm._get_section_facts(persona, "master")
        if e.get('id') == 'p_loc_1'
    )
    assert target['text'] == '主人最近搬到了大阪'
    history = target.get('version_history') or []
    assert history and history[-1]['text'] == '主人住在东京'
    assert history[-1]['reason'] == '基于近期 fact_xyz'


@pytest.mark.asyncio
async def test_discard_removes_entry(tmp_path):
    pm = _install(str(tmp_path))
    src = await _seed(pm, "小天", "obsolete entry", id='p_old_1')
    cluster = [_annotate(src)]
    actions = [{
        'action': 'discard', 'source_id': 'p_old_1', 'reason': '已被证伪',
    }]
    applied = await pm.apply_refine_actions("小天", "master", cluster, actions, 'h004')
    assert applied == 1
    persona = await pm.aensure_persona("小天")
    ids = [e.get('id') for e in pm._get_section_facts(persona, "master")]
    assert 'p_old_1' not in ids


@pytest.mark.asyncio
async def test_discard_rejected_for_protected_entry(tmp_path):
    """protected (character-card) entry 不可被 discard，即使采集层漏检也兜住。"""
    pm = _install(str(tmp_path))
    src = await _seed(pm, "小天", "character card item", id='p_card_1', protected=True)
    cluster = [_annotate(src)]
    actions = [{
        'action': 'discard', 'source_id': 'p_card_1', 'reason': 'x',
    }]
    applied = await pm.apply_refine_actions("小天", "master", cluster, actions, 'h005')
    assert applied == 0
    persona = await pm.aensure_persona("小天")
    ids = [e.get('id') for e in pm._get_section_facts(persona, "master")]
    assert 'p_card_1' in ids


@pytest.mark.asyncio
async def test_invalid_action_ignored_but_others_still_apply(tmp_path):
    pm = _install(str(tmp_path))
    src = await _seed(pm, "小天", "x", id='p1')
    cluster = [_annotate(src)]
    actions = [
        {'action': 'invent_an_action', 'source_id': 'p1'},
        {'action': 'discard', 'source_id': 'p1', 'reason': 'cleanup'},
    ]
    applied = await pm.apply_refine_actions("小天", "master", cluster, actions, 'h006')
    assert applied == 1
    persona = await pm.aensure_persona("小天")
    ids = [e.get('id') for e in pm._get_section_facts(persona, "master")]
    assert 'p1' not in ids


@pytest.mark.asyncio
async def test_survivor_gets_cluster_hash_stamp(tmp_path):
    """未被 consume 的 cluster 成员应 stamp 上 cluster_hash + last_refine_at。"""
    pm = _install(str(tmp_path))
    s1 = await _seed(pm, "小天", "kept entry", id='p_keep')
    s2 = await _seed(pm, "小天", "to discard", id='p_drop')
    cluster = [_annotate(s1), _annotate(s2)]
    actions = [{'action': 'discard', 'source_id': 'p_drop', 'reason': 'x'}]
    await pm.apply_refine_actions("小天", "master", cluster, actions, 'h007')
    persona = await pm.aensure_persona("小天")
    survivor = next(
        e for e in pm._get_section_facts(persona, "master")
        if e.get('id') == 'p_keep'
    )
    assert survivor['last_refine_cluster_hash'] == 'h007'
    assert survivor['last_refine_at']


@pytest.mark.asyncio
async def test_all_malformed_actions_does_not_stamp(tmp_path):
    """Codex P2 修复：非空 actions 但全部 reject (语义垃圾) 不应 stamp，
    否则 cluster 会被错误标记为 fresh 推迟 1 个月才重审。"""
    pm = _install(str(tmp_path))
    s1 = await _seed(pm, "小天", "kept", id='p_keep')
    cluster = [_annotate(s1)]
    actions = [
        {'action': 'invent_an_action', 'source_id': 'p_keep'},
        {'action': 'also_unknown', 'source_id': 'p_keep'},
    ]
    applied = await pm.apply_refine_actions("小天", "master", cluster, actions, 'h_bad')
    assert applied == 0
    persona = await pm.aensure_persona("小天")
    survivor = next(
        e for e in pm._get_section_facts(persona, "master")
        if e.get('id') == 'p_keep'
    )
    assert survivor.get('last_refine_cluster_hash') is None
    assert survivor.get('last_refine_at') is None


@pytest.mark.asyncio
async def test_empty_actions_still_stamps_cluster_members(tmp_path):
    """Codex P1 修复：LLM 返回 [] (no-op) 也必须 stamp 整个 cluster，
    否则下轮 cron 重复审视同 cluster，cluster_hash skip 失效。"""
    pm = _install(str(tmp_path))
    s1 = await _seed(pm, "小天", "entry one", id='p_one')
    s2 = await _seed(pm, "小天", "entry two", id='p_two')
    cluster = [_annotate(s1), _annotate(s2)]
    applied = await pm.apply_refine_actions("小天", "master", cluster, [], 'h_noop')
    assert applied == 0
    persona = await pm.aensure_persona("小天")
    e1 = next(e for e in pm._get_section_facts(persona, "master") if e.get('id') == 'p_one')
    e2 = next(e for e in pm._get_section_facts(persona, "master") if e.get('id') == 'p_two')
    assert e1['last_refine_cluster_hash'] == 'h_noop'
    assert e2['last_refine_cluster_hash'] == 'h_noop'
    assert e1['last_refine_at'] and e2['last_refine_at']
