# -*- coding: utf-8 -*-
"""
End-to-end integration tests for merge-on-promote (RFC §3.9).

Drives a real PersonaManager + ReflectionEngine + EventLog stack through
the same code paths the production background loop uses, then asserts:
  - persona.json reflects the merge
  - reflections.json reflects the status flip
  - reconciler can replay the recorded events to rebuild the same view
    (S15-S17 crash-recovery)
"""
from __future__ import annotations

import json
import os
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
        'model': 'qwen-max', 'api_key': 'fake', 'base_url': 'http://fake',
    })
    return cm


def _install(tmpdir: str):
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine

    cm = _mock_cm(tmpdir)
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
    return event_log, fs, pm, re, cm


def _persona_entry(eid: str, text: str, *, rein: float = 0.0,
                   disp: float = 0.0) -> dict:
    return {
        'id': eid, 'text': text,
        'reinforcement': rein, 'disputation': disp,
        'rein_last_signal_at': None, 'disp_last_signal_at': None,
        'sub_zero_days': 0, 'user_fact_reinforce_count': 0,
        'merged_from_ids': [],
        'protected': False,
        'suppress': False, 'suppressed_at': None,
        'recent_mentions': [],
        'source': 'manual', 'source_id': None,
    }


def _reflection_above_threshold(rid: str, text: str,
                                 entity: str = 'master') -> dict:
    # Timestamps must be "now"-relative: evidence decay is computed at
    # read time (memory/evidence.py, half-life 30 days), so a hardcoded
    # rein_last_signal_at would rot the fixture below
    # EVIDENCE_PROMOTED_THRESHOLD as wall-clock time advances and make
    # _apromote_with_merge bail with 'no_longer_eligible'.
    from datetime import datetime
    now_iso = datetime.now().isoformat()
    return {
        'id': rid, 'text': text, 'entity': entity, 'status': 'confirmed',
        'source_fact_ids': ['f1', 'f2'],
        'created_at': now_iso,
        'feedback': 'confirmed',
        'next_eligible_at': now_iso,
        # Above EVIDENCE_PROMOTED_THRESHOLD = 2.0
        'reinforcement': 2.5, 'disputation': 0.0,
        'rein_last_signal_at': now_iso,
        'disp_last_signal_at': None,
        'sub_zero_days': 0, 'sub_zero_last_increment_date': None,
        'user_fact_reinforce_count': 0,
        'absorbed_into': None,
        'last_promote_attempt_at': None,
        'promote_attempt_count': 0,
        'promote_blocked_reason': None,
        'recent_mentions': [], 'suppress': False, 'suppressed_at': None,
    }


@pytest.mark.asyncio
async def test_periodic_promote_with_mock_llm_merge_into(tmp_path):
    """Drive the same code path the periodic loop uses
    (`aauto_promote_stale`) with a mocked LLM that returns merge_into.
    Expectation: persona.json shows merged text + evidence; reflection
    flips to merged with absorbed_into.
    """
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    persona = {
        'master': {'facts': [_persona_entry('p_001', '主人爱猫', rein=1.0)]},
    }
    await pm.asave_persona('小天', persona)
    R = _reflection_above_threshold('ref_e2e', '主人很喜欢小猫')
    await re.asave_reflections('小天', [R])

    fake_decision = {
        'action': 'merge_into',
        'target_id': 'persona.master.p_001',
        'merged_text': '主人非常喜爱猫咪',
    }
    with patch.object(re, '_allm_call_promotion_merge',
                       AsyncMock(return_value=fake_decision)):
        # Same call the loop makes
        await re.aauto_promote_stale('小天')

    # Persona reflects merge
    persona2 = await pm.aget_persona('小天')
    entry = persona2['master']['facts'][0]
    assert entry['text'] == '主人非常喜爱猫咪'
    assert entry['reinforcement'] == 2.5
    assert 'ref_e2e' in entry['merged_from_ids']

    # Reflection flipped to merged + absorbed_into recorded
    rstate = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_e2e'
    )
    assert rstate['status'] == 'merged'
    assert rstate['absorbed_into'] == 'p_001'


@pytest.mark.asyncio
async def test_reconciler_replay_idempotent_after_full_apply(tmp_path):
    """S15-S17 PART A — replay is idempotent over an already-merged view.

    Original PR-3 test only covered this idempotence path (sentinel
    rolled back, view kept post-merge). Kept here as the explicit
    "everything succeeded; restart re-reads the same events" assertion.
    """
    from memory.event_log import Reconciler
    from memory.evidence_handlers import register_evidence_handlers

    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    persona = {
        'master': {'facts': [_persona_entry('p_001', 'orig text', rein=1.0)]},
    }
    await pm.asave_persona('小天', persona)
    R = _reflection_above_threshold('ref_crash', '某条要被合并的反思')
    await re.asave_reflections('小天', [R])

    fake_decision = {
        'action': 'merge_into',
        'target_id': 'persona.master.p_001',
        'merged_text': '合并后的文本',
    }
    with patch.object(re, '_allm_call_promotion_merge',
                       AsyncMock(return_value=fake_decision)):
        outcome = await re._apromote_with_merge('小天', R)
    assert outcome == 'merge_into'

    # Roll the sentinel back; persona/reflections.json are already
    # post-merge. Replay must converge to the same view (idempotent).
    reconciler = Reconciler(_ev)
    register_evidence_handlers(reconciler, pm, re)
    sentinel_path = os.path.join(str(tmp_path), '小天', 'events_applied.json')
    with open(sentinel_path, 'w', encoding='utf-8') as f:
        json.dump({'last_applied_event_id': None, 'ts': 'reset'}, f)

    applied = await reconciler.areconcile('小天')
    assert applied >= 0  # idempotent — any handler may no-op

    persona_after = await pm.aget_persona('小天')
    entry = persona_after['master']['facts'][0]
    assert entry['text'] == '合并后的文本'
    assert 'ref_crash' in entry['merged_from_ids']

    refls = await re._aload_reflections_full('小天')
    rstate = next(r for r in refls if r['id'] == 'ref_crash')
    assert rstate['status'] == 'merged'
    assert rstate['absorbed_into'] == 'p_001'


@pytest.mark.asyncio
async def test_reconciler_replays_lost_merge_text_and_skips_later_rewrite(tmp_path):
    """S15-S17 PART B: a merge whose persona.json save was lost (events
    appended, view still pre-merge) is written back on replay, because the
    entry_updated payload carries the merged text and the hash of the text
    it replaced. A view rewritten to something else afterwards is left
    alone and replay keeps going instead of pausing.
    """
    from memory.event_log import Reconciler
    from memory.evidence_handlers import register_evidence_handlers

    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    persona = {
        'master': {'facts': [_persona_entry('p_001', 'orig text', rein=1.0)]},
    }
    await pm.asave_persona('小天', persona)
    R = _reflection_above_threshold('ref_crash', '某条要被合并的反思')
    await re.asave_reflections('小天', [R])

    persona_path = os.path.join(str(tmp_path), '小天', 'persona.json')
    sentinel_path = os.path.join(
        str(tmp_path), '小天', 'events_applied.json',
    )

    # Snapshot pre-merge view; we'll restore it after the merge to
    # simulate "events written, view-save crashed".
    with open(persona_path, encoding='utf-8') as f:
        persona_pre = f.read()

    fake_decision = {
        'action': 'merge_into',
        'target_id': 'persona.master.p_001',
        'merged_text': '合并后的文本',
    }
    with patch.object(re, '_allm_call_promotion_merge',
                       AsyncMock(return_value=fake_decision)):
        outcome = await re._apromote_with_merge('小天', R)
    assert outcome == 'merge_into'

    def _rollback(text_on_disk):
        data = json.loads(persona_pre)
        data['master']['facts'][0]['text'] = text_on_disk
        with open(persona_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        with open(sentinel_path, 'w', encoding='utf-8') as f:
            json.dump({'last_applied_event_id': None, 'ts': 'reset'}, f)
        pm._personas.pop('小天', None)

    # 1. Crash: the view still holds the text the merge replaced.
    _rollback('orig text')
    reconciler = Reconciler(_ev)
    register_evidence_handlers(reconciler, pm, re)
    await reconciler.areconcile('小天')
    entry = (await pm.aget_persona('小天'))['master']['facts'][0]
    assert entry['text'] == '合并后的文本'
    assert 'ref_crash' in entry['merged_from_ids']
    assert entry.get('embedding') is None
    with open(sentinel_path, encoding='utf-8') as f:
        last = json.load(f)['last_applied_event_id']
    with open(os.path.join(str(tmp_path), '小天', 'events.ndjson'), encoding='utf-8') as f:
        assert last == json.loads(f.read().splitlines()[-1])['event_id']

    # 2. Later rewrite: the view holds neither text. Replay skips the
    #    rewrite instead of pausing, and the text stays as the user left it.
    _rollback('user typed something else')
    reconciler = Reconciler(_ev)
    register_evidence_handlers(reconciler, pm, re)
    await reconciler.areconcile('小天')
    entry = (await pm.aget_persona('小天'))['master']['facts'][0]
    assert entry['text'] == 'user typed something else'
    assert 'ref_crash' not in (entry.get('merged_from_ids') or [])
    with open(sentinel_path, encoding='utf-8') as f:
        assert json.load(f)['last_applied_event_id'] is not None
