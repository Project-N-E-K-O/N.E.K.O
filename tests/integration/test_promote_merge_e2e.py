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
         patch("memory.persona.get_config_manager", return_value=cm), \
         patch("memory.reflection.get_config_manager", return_value=cm):
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
    return {
        'id': rid, 'text': text, 'entity': entity, 'status': 'confirmed',
        'source_fact_ids': ['f1', 'f2'],
        'created_at': '2026-04-22T10:00:00',
        'feedback': 'confirmed',
        'next_eligible_at': '2026-04-22T10:00:00',
        # Above EVIDENCE_PROMOTED_THRESHOLD = 2.0
        'reinforcement': 2.5, 'disputation': 0.0,
        'rein_last_signal_at': '2026-04-22T10:00:00',
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
async def test_reconciler_text_drift_raises_per_rfc_red_line(tmp_path):
    """S15-S17 PART B — RFC §3.3.6 + red line 4: when the on-disk view
    has drifted away from what the event log says (e.g. process crashed
    between event-append and view-save, then text was edited or rolled
    back manually), the persona.entry_updated handler MUST raise rather
    than silently re-apply, because the event payload deliberately
    omits the merged plaintext (red line 4: no plaintext in event log).

    This is operator-intervention territory by design — the reconciler
    refuses to advance the sentinel and surfaces the divergence so a
    human can audit the event log and decide. The bot proposed putting
    plaintext in the event payload; we explicitly reject that and
    document the trade-off here.
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

    # Roll persona.json back to pre-merge state + sentinel reset:
    # event log claims merge happened, view says it didn't.
    with open(persona_path, 'w', encoding='utf-8') as f:
        f.write(persona_pre)
    with open(sentinel_path, 'w', encoding='utf-8') as f:
        json.dump({'last_applied_event_id': None, 'ts': 'reset'}, f)
    pm._personas.pop('小天', None)

    reconciler = Reconciler(_ev)
    register_evidence_handlers(reconciler, pm, re)

    # Reconciler must stop at the divergent event (entry_updated's
    # sha256 mismatch) and NOT auto-rewrite the text. Since round-4's
    # event order was changed to emit evidence_updated FIRST (see
    # memory/persona.py amerge_into docstring), the reconciler applies
    # the idempotent evidence snapshot before hitting the diverging
    # entry_updated — so `applied` is 1, not 0. The critical invariant
    # is still: text remains un-rewritten and the sentinel stays pinned
    # at the pre-mismatch event so a human can audit.
    applied = await reconciler.areconcile('小天')
    assert applied == 1, (
        "reconciler applies the idempotent evidence_updated snapshot "
        "(emitted first post round-4 Major) then stops at the "
        "sha256-mismatching entry_updated — RFC §3.3.6"
    )

    # View's text remains in its (pre-merge / drifted) state — no
    # auto-recovery. Evidence fields DO update from the idempotent
    # snapshot replay; that is by design (RFC §3.9.6 evidence_updated
    # is a full snapshot, always safe to replay).
    persona_after = await pm.aget_persona('小天')
    entry = persona_after['master']['facts'][0]
    assert entry['text'] == 'orig text', (
        "view must NOT be auto-rewritten — RFC red line 4 keeps text "
        "out of the event payload"
    )
