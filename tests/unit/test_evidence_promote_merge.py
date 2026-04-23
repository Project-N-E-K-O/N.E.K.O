# -*- coding: utf-8 -*-
"""
Unit tests for merge-on-promote (memory-evidence-rfc §3.9).

Covers:
  - _compute_merged_evidence: max not sum (S15 evidence rule)
  - _apromote_with_merge: promote_fresh / merge_into / reject paths
  - LLM failure → skip_retry_pending, NOT promote_fresh (S14)
  - throttle: backoff window, max retries → promote_blocked
  - amerge_into idempotency (re-call with same source_reflection_id)
  - replay safety of EVT_PERSONA_ENTRY_UPDATED
  - target_id validation: must start with persona.* (RFC §3.9.7 constraint)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
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
                   disp: float = 0.0, protected: bool = False) -> dict:
    return {
        'id': eid, 'text': text,
        'reinforcement': rein, 'disputation': disp,
        'rein_last_signal_at': None, 'disp_last_signal_at': None,
        'sub_zero_days': 0, 'user_fact_reinforce_count': 0,
        'merged_from_ids': [],
        'importance': 0,
        'protected': protected,
        'suppress': False, 'suppressed_at': None,
        'recent_mentions': [],
        'source': 'manual', 'source_id': None,
    }


def _reflection(rid: str, text: str, entity: str = 'master', *,
                status: str = 'confirmed', rein: float = 2.5,
                disp: float = 0.0, attempt_count: int = 0,
                last_attempt_at: str | None = None) -> dict:
    return {
        'id': rid, 'text': text, 'entity': entity, 'status': status,
        'source_fact_ids': [], 'created_at': '2026-04-22T10:00:00',
        'feedback': None, 'next_eligible_at': '2026-04-22T10:00:00',
        'reinforcement': rein, 'disputation': disp,
        'rein_last_signal_at': '2026-04-22T10:00:00',
        'disp_last_signal_at': None,
        'sub_zero_days': 0, 'sub_zero_last_increment_date': None,
        'user_fact_reinforce_count': 0,
        'absorbed_into': None,
        'last_promote_attempt_at': last_attempt_at,
        'promote_attempt_count': attempt_count,
        'promote_blocked_reason': None,
        'recent_mentions': [], 'suppress': False, 'suppressed_at': None,
    }


# ── _compute_merged_evidence ─────────────────────────────────────


def test_compute_merged_evidence_uses_max_not_sum():
    from memory.reflection import ReflectionEngine

    target = {'reinforcement': 2.0, 'disputation': 0.0}
    ref = {'reinforcement': 1.0, 'disputation': 1.0}
    rein, disp = ReflectionEngine._compute_merged_evidence(target, ref)
    assert rein == 2.0, "merged rein should be max(target, reflection)"
    assert disp == 1.0, "merged disp should be max(target, reflection)"


def test_compute_merged_evidence_handles_missing_keys():
    from memory.reflection import ReflectionEngine

    target = {}
    ref = {'reinforcement': 1.5, 'disputation': 0.5}
    rein, disp = ReflectionEngine._compute_merged_evidence(target, ref)
    assert rein == 1.5
    assert disp == 0.5


# ── amerge_into: idempotency + dual events ───────────────────────


@pytest.mark.asyncio
async def test_amerge_into_emits_two_events_and_writes_view(tmp_path):
    _ev, _fs, pm, _re, _cm = _install(str(tmp_path))
    persona = {
        'master': {'facts': [_persona_entry('p_001', 'old text', rein=1.0)]},
    }
    pm._personas['小天'] = persona
    await pm.asave_persona('小天', persona)

    result = await pm.amerge_into(
        '小天', 'p_001', 'merged new text',
        merged_reinforcement=2.5, merged_disputation=0.0,
        source_reflection_id='ref_xyz', merged_from_ids=['ref_xyz'],
    )
    assert result == 'merged'

    persona_reloaded = await pm.aget_persona('小天')
    entry = persona_reloaded['master']['facts'][0]
    assert entry['text'] == 'merged new text'
    assert entry['reinforcement'] == 2.5
    assert entry['merged_from_ids'] == ['ref_xyz']

    # Two events expected: PERSONA_ENTRY_UPDATED then PERSONA_EVIDENCE_UPDATED
    events_path = os.path.join(str(tmp_path), '小天', 'events.ndjson')
    with open(events_path, encoding='utf-8') as f:
        events = [json.loads(line) for line in f if line.strip()]
    types = [e['type'] for e in events]
    assert 'persona.entry_updated' in types
    assert 'persona.evidence_updated' in types


@pytest.mark.asyncio
async def test_amerge_into_idempotent_on_repeat(tmp_path):
    """RFC §3.9.6: re-calling amerge_into with the same
    source_reflection_id is a no-op (the source is already in
    merged_from_ids). Important for crash-mid-flight recovery."""
    _ev, _fs, pm, _re, _cm = _install(str(tmp_path))
    persona = {
        'master': {'facts': [_persona_entry('p_001', 'orig', rein=1.0)]},
    }
    await pm.asave_persona('小天', persona)

    r1 = await pm.amerge_into(
        '小天', 'p_001', 'first merge',
        merged_reinforcement=2.0, merged_disputation=0.0,
        source_reflection_id='ref_a', merged_from_ids=['ref_a'],
    )
    assert r1 == 'merged'
    r2 = await pm.amerge_into(
        '小天', 'p_001', 'second attempt — must be ignored',
        merged_reinforcement=99.0, merged_disputation=99.0,
        source_reflection_id='ref_a', merged_from_ids=['ref_a'],
    )
    assert r2 == 'noop'

    entry = (await pm.aget_persona('小天'))['master']['facts'][0]
    assert entry['text'] == 'first merge', "second call must not overwrite"
    assert entry['reinforcement'] == 2.0


@pytest.mark.asyncio
async def test_amerge_into_unknown_target_returns_not_found(tmp_path):
    _ev, _fs, pm, _re, _cm = _install(str(tmp_path))
    await pm.asave_persona('小天', {'master': {'facts': []}})

    result = await pm.amerge_into(
        '小天', 'never_existed', 'text',
        merged_reinforcement=1.0, merged_disputation=0.0,
        source_reflection_id='ref_zzz', merged_from_ids=[],
    )
    assert result == 'not_found'


# ── _apromote_with_merge: dispatch paths ──────────────────────────


@pytest.mark.asyncio
async def test_promote_fresh_path_writes_persona_and_promotes(tmp_path):
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    R = _reflection('ref_fresh', '主人喜欢小动物', rein=2.5)
    await re.asave_reflections('小天', [R])
    await pm.asave_persona('小天', {'master': {'facts': []}})

    fake_decision = {'action': 'promote_fresh', 'reason': 'no overlap'}
    with patch.object(re, '_allm_call_promotion_merge',
                       AsyncMock(return_value=fake_decision)):
        outcome = await re._apromote_with_merge('小天', R)

    assert outcome == 'promote_fresh'
    persona = await pm.aget_persona('小天')
    texts = [e['text'] for e in persona['master']['facts']]
    assert '主人喜欢小动物' in texts

    reloaded = await re._aload_reflections_full('小天')
    rstate = next(r for r in reloaded if r['id'] == 'ref_fresh')
    assert rstate['status'] == 'promoted'


@pytest.mark.asyncio
async def test_merge_into_path_updates_target_and_marks_merged(tmp_path):
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    persona = {
        'master': {'facts': [
            _persona_entry('p_001', '主人爱猫', rein=1.0),
        ]},
    }
    await pm.asave_persona('小天', persona)
    R = _reflection('ref_merge', '主人很喜欢小猫咪', rein=2.5)
    await re.asave_reflections('小天', [R])

    fake_decision = {
        'action': 'merge_into',
        'target_id': 'persona.master.p_001',
        'merged_text': '主人非常喜爱猫咪',
    }
    with patch.object(re, '_allm_call_promotion_merge',
                       AsyncMock(return_value=fake_decision)):
        outcome = await re._apromote_with_merge('小天', R)

    assert outcome == 'merge_into'
    entry = (await pm.aget_persona('小天'))['master']['facts'][0]
    assert entry['text'] == '主人非常喜爱猫咪'
    assert entry['reinforcement'] == 2.5  # max(1.0, 2.5)
    assert 'ref_merge' in entry['merged_from_ids']

    reloaded = await re._aload_reflections_full('小天')
    rstate = next(r for r in reloaded if r['id'] == 'ref_merge')
    assert rstate['status'] == 'merged'
    assert rstate['absorbed_into'] == 'p_001'


@pytest.mark.asyncio
async def test_reject_path_marks_denied(tmp_path):
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    R = _reflection('ref_reject', '一条会被否决的观察', rein=2.5)
    await re.asave_reflections('小天', [R])
    await pm.asave_persona('小天', {'master': {'facts': []}})

    fake_decision = {'action': 'reject', 'reason': 'contradicts character_card'}
    with patch.object(re, '_allm_call_promotion_merge',
                       AsyncMock(return_value=fake_decision)):
        outcome = await re._apromote_with_merge('小天', R)

    assert outcome == 'reject'
    rstate = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_reject'
    )
    assert rstate['status'] == 'denied'
    assert rstate.get('reject_reason') == 'contradicts character_card'


@pytest.mark.asyncio
async def test_llm_failure_does_not_promote_fresh_S14(tmp_path):
    """RFC §3.9.4 / S14: LLM failure must NOT default to promote_fresh.
    Reflection stays confirmed; promote_attempt_count increments."""
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    R = _reflection('ref_llm_fail', '一条等待 LLM 决策的观察', rein=2.5)
    await re.asave_reflections('小天', [R])
    await pm.asave_persona('小天', {'master': {'facts': []}})

    with patch.object(
        re, '_allm_call_promotion_merge',
        AsyncMock(side_effect=RuntimeError('LLM timeout simulation')),
    ):
        outcome = await re._apromote_with_merge('小天', R)

    assert outcome == 'skip_retry_pending'
    rstate = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_llm_fail'
    )
    assert rstate['status'] == 'confirmed', "must NOT auto-promote on LLM fail"
    assert rstate['promote_attempt_count'] == 1
    assert rstate['last_promote_attempt_at'] is not None
    # Persona must remain empty — no silent fresh-add
    persona = await pm.aget_persona('小天')
    assert persona['master']['facts'] == []


@pytest.mark.asyncio
async def test_unknown_action_treated_as_skip(tmp_path):
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    R = _reflection('ref_unknown', 'x', rein=2.5)
    await re.asave_reflections('小天', [R])
    await pm.asave_persona('小天', {'master': {'facts': []}})

    fake = {'action': 'magic_new_action', 'random': 'data'}
    with patch.object(re, '_allm_call_promotion_merge',
                       AsyncMock(return_value=fake)):
        outcome = await re._apromote_with_merge('小天', R)
    assert outcome == 'skip_retry_pending'
    rstate = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_unknown'
    )
    assert rstate['status'] == 'confirmed'


@pytest.mark.asyncio
async def test_invalid_target_id_treated_as_invalid(tmp_path):
    """RFC §3.9.7: target_id must start with `persona.` — anything else
    (e.g. `reflection.r_X`) is rejected as a parse failure."""
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    R = _reflection('ref_bad_target', 'x', rein=2.5)
    await re.asave_reflections('小天', [R])
    await pm.asave_persona('小天', {'master': {'facts': []}})

    fake = {
        'action': 'merge_into',
        'target_id': 'reflection.r_other',  # forbidden prefix
        'merged_text': 'whatever',
    }
    with patch.object(re, '_allm_call_promotion_merge',
                       AsyncMock(return_value=fake)):
        outcome = await re._apromote_with_merge('小天', R)
    assert outcome == 'invalid_target'
    rstate = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_bad_target'
    )
    assert rstate['status'] == 'confirmed'


# ── throttle: backoff + max retries ─────────────────────────────


@pytest.mark.asyncio
async def test_recent_attempt_skipped_within_backoff(tmp_path):
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    recent = (datetime.now() - timedelta(minutes=2)).isoformat()
    R = _reflection('ref_recent', 'x', rein=2.5,
                    last_attempt_at=recent, attempt_count=1)
    await re.asave_reflections('小天', [R])
    await pm.asave_persona('小天', {'master': {'facts': []}})

    # No LLM patch needed — should short-circuit before LLM call
    outcome = await re._apromote_with_merge('小天', R)
    assert outcome == 'skip_retry_pending'

    # Counter NOT incremented because backoff path returns before
    # _arecord_promote_attempt fires.
    rstate = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_recent'
    )
    assert rstate['promote_attempt_count'] == 1


@pytest.mark.asyncio
async def test_max_retries_marks_promote_blocked(tmp_path):
    """RFC §3.9.2: 5 attempts → status='promote_blocked' with reason."""
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    # Old enough to NOT be inside backoff window
    old = (datetime.now() - timedelta(hours=2)).isoformat()
    R = _reflection('ref_blocked', 'x', rein=2.5,
                    last_attempt_at=old, attempt_count=5)
    await re.asave_reflections('小天', [R])
    await pm.asave_persona('小天', {'master': {'facts': []}})

    outcome = await re._apromote_with_merge('小天', R)
    assert outcome == 'blocked'

    # Reflection now in dead-letter — `_aload_reflections_full` returns
    # all statuses including terminal ones.
    rstate = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_blocked'
    )
    assert rstate['status'] == 'promote_blocked'
    assert rstate.get('promote_blocked_reason') == 'llm_unavailable'


# ── replay safety ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persona_entry_updated_replay_idempotent(tmp_path):
    """Replaying persona.entry_updated 5 times leaves view stable.
    The view is already in the merged state after the first merge;
    subsequent replays no-op (sha256 matches, snapshot keys equal).
    """
    from memory.evidence_handlers import make_persona_entry_handler

    _ev, _fs, pm, _re, _cm = _install(str(tmp_path))
    persona = {
        'master': {'facts': [_persona_entry('p_001', 'orig', rein=1.0)]},
    }
    await pm.asave_persona('小天', persona)
    await pm.amerge_into(
        '小天', 'p_001', 'merged target text',
        merged_reinforcement=2.5, merged_disputation=0.0,
        source_reflection_id='ref_a', merged_from_ids=['ref_a'],
    )

    # Snapshot post-merge
    after_first = json.dumps(await pm.aget_persona('小天'), sort_keys=True)

    # Build the handler and replay the recorded entry_updated event 5x
    handler = make_persona_entry_handler(pm)
    events_path = os.path.join(str(tmp_path), '小天', 'events.ndjson')
    with open(events_path, encoding='utf-8') as f:
        events = [json.loads(line) for line in f if line.strip()]
    entry_evt = next(e for e in events if e['type'] == 'persona.entry_updated')

    for _ in range(5):
        handler('小天', entry_evt['payload'])

    after_replays = json.dumps(await pm.aget_persona('小天'), sort_keys=True)
    assert after_first == after_replays, (
        "replaying entry_updated event must be idempotent on the view"
    )
