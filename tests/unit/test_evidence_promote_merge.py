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
async def test_find_entry_with_section_accepts_qualified_id(tmp_path):
    """Coderabbit Critical (re-evaluated as defensive): the prompt
    documents target_id as `persona.<entity>.<id>`; the reflection
    promote path strips the prefix before calling, but the helper
    should accept both forms so any other callsite (tests, manual
    replay, future plugins) doesn't have to re-implement the parser.
    """
    _ev, _fs, pm, _re, _cm = _install(str(tmp_path))
    persona = {
        'master': {'facts': [_persona_entry('p_001', 'orig', rein=1.0)]},
        'neko': {'facts': [_persona_entry('n_001', 'cat fact', rein=1.0)]},
    }
    pm._personas['小天'] = persona

    # Bare id — works (existing contract)
    ek, entry = pm._find_entry_with_section(persona, 'p_001')
    assert ek == 'master' and entry is not None

    # Fully-qualified id — also works (defensive addition)
    ek2, entry2 = pm._find_entry_with_section(
        persona, 'persona.master.p_001',
    )
    assert ek2 == 'master' and entry2 is not None
    assert entry2.get('id') == 'p_001'

    # Qualified id with WRONG entity must NOT match the bare id in
    # another section (entity scoping is enforced when present).
    ek3, entry3 = pm._find_entry_with_section(
        persona, 'persona.neko.p_001',
    )
    assert ek3 is None and entry3 is None


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


@pytest.mark.asyncio
async def test_amerge_into_event_payload_uses_bare_id_for_both_forms(tmp_path):
    """Regression (round-2 review): the EVT_PERSONA_ENTRY_UPDATED and
    EVT_PERSONA_EVIDENCE_UPDATED payloads must always carry the canonical
    bare entry id, even when the caller passes the fully-qualified
    `persona.<entity>.<id>` form. Reconciler handlers
    (`make_persona_entry_handler`, `make_persona_evidence_handler`) match
    `e.get('id') == entry_id` strictly on the bare id; a qualified id in
    payload would silently miss on crash-replay (RFC §3.9.6).
    """
    _ev, _fs, pm, _re, _cm = _install(str(tmp_path))

    async def _emit_and_get_payloads(target_id: str, src_rid: str):
        persona = {
            'master': {'facts': [_persona_entry('p_001', 'orig', rein=1.0)]},
        }
        await pm.asave_persona('小天', persona)
        result = await pm.amerge_into(
            '小天', target_id, f'merged via {target_id}',
            merged_reinforcement=2.0, merged_disputation=0.0,
            source_reflection_id=src_rid, merged_from_ids=[src_rid],
        )
        assert result == 'merged', f"merge with {target_id!r} should succeed"
        events_path = os.path.join(str(tmp_path), '小天', 'events.ndjson')
        with open(events_path, encoding='utf-8') as f:
            events = [json.loads(line) for line in f if line.strip()]
        # Take the LAST entry/evidence event (this call's events)
        entry_evt = [
            e for e in events if e['type'] == 'persona.entry_updated'
        ][-1]
        ev_evt = [
            e for e in events if e['type'] == 'persona.evidence_updated'
        ][-1]
        return entry_evt['payload'], ev_evt['payload']

    # Bare form
    bare_entry, bare_ev = await _emit_and_get_payloads('p_001', 'ref_bare')
    assert bare_entry['entry_id'] == 'p_001'
    assert bare_ev['entry_id'] == 'p_001'

    # Reset persona for clean second merge
    persona2 = {
        'master': {'facts': [_persona_entry('p_001', 'orig2', rein=1.0)]},
    }
    pm._personas['小天'] = persona2
    await pm.asave_persona('小天', persona2)

    # Fully-qualified form — payload MUST still be bare
    qual_entry, qual_ev = await _emit_and_get_payloads(
        'persona.master.p_001', 'ref_qual',
    )
    assert qual_entry['entry_id'] == 'p_001', (
        "qualified target_id must be normalized to bare id in payload"
    )
    assert qual_ev['entry_id'] == 'p_001', (
        "qualified target_id must be normalized to bare id in evidence payload"
    )


@pytest.mark.asyncio
async def test_arecord_state_change_routes_reason_by_status(tmp_path):
    """Regression (round-2 review): the `reason` arg must land in a
    status-specific field. Previously _sync_mutate wrote ANY non-None
    reason into `promote_blocked_reason`, so `denied` transitions
    (e.g. from `llm_merge_rejected` / `rejected_by_persona_add:*`)
    polluted that field. RFC §3.9.2 reserves promote_blocked_reason for
    status='promote_blocked'; denied transitions get `denied_reason`.
    """
    _ev, _fs, _pm, re, _cm = _install(str(tmp_path))

    R1 = _reflection('ref_denied_route', 'a', rein=2.5)
    R2 = _reflection('ref_blocked_route', 'b', rein=2.5)
    await re.asave_reflections('小天', [R1, R2])

    # denied transition — reason MUST go to denied_reason, NOT
    # promote_blocked_reason
    await re._arecord_state_change(
        '小天', 'ref_denied_route', 'confirmed', 'denied',
        reason='llm_merge_rejected',
    )
    rs1 = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_denied_route'
    )
    assert rs1['status'] == 'denied'
    assert rs1.get('denied_reason') == 'llm_merge_rejected', (
        "denied transition must record reason in denied_reason"
    )
    assert rs1.get('promote_blocked_reason') in (None,), (
        "denied transition must NOT pollute promote_blocked_reason"
    )

    # promote_blocked transition — reason MUST go to promote_blocked_reason
    await re._arecord_state_change(
        '小天', 'ref_blocked_route', 'confirmed', 'promote_blocked',
        reason='llm_unavailable',
    )
    rs2 = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_blocked_route'
    )
    assert rs2['status'] == 'promote_blocked'
    assert rs2.get('promote_blocked_reason') == 'llm_unavailable'
    assert rs2.get('denied_reason') in (None,)


@pytest.mark.asyncio
async def test_state_change_handler_routes_reason_by_status_on_replay(tmp_path):
    """Symmetry check: the reconciler handler must route `reason` the
    same way the live writer does — denied → denied_reason,
    promote_blocked → promote_blocked_reason. Without this the on-disk
    view diverges from a crash-replay rebuild.
    """
    from memory.evidence_handlers import (
        make_reflection_state_changed_handler,
    )

    _ev, _fs, _pm, re, _cm = _install(str(tmp_path))
    R = _reflection('ref_replay_denied', 'x', rein=2.5)
    await re.asave_reflections('小天', [R])

    handler = make_reflection_state_changed_handler(re)
    handler('小天', {
        'reflection_id': 'ref_replay_denied',
        'from': 'confirmed',
        'to': 'denied',
        'ts': '2026-04-23T00:00:00',
        'reason': 'rejected_by_persona_add:FACT_REJECTED_CARD',
    })
    rs = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_replay_denied'
    )
    assert rs['status'] == 'denied'
    assert rs.get('denied_reason') == (
        'rejected_by_persona_add:FACT_REJECTED_CARD'
    )
    assert rs.get('promote_blocked_reason') in (None,)


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


# ── concurrency: CAS on _arecord_state_change ──────────────────────


@pytest.mark.asyncio
async def test_arecord_state_change_cas_drops_stale_transition(tmp_path):
    """Coderabbit P1: a stale snapshot must NOT clobber a newer status.

    Simulate the race: rebuttal flips reflection to 'denied' while a
    promote LLM call is in flight. When promote returns and tries
    'confirmed' → 'merged', the CAS check sees current='denied' and
    drops the write. The reflection stays denied; no event is emitted.
    """
    _ev, _fs, _pm, re, _cm = _install(str(tmp_path))
    R = _reflection('ref_cas', 'x', rein=2.5)
    await re.asave_reflections('小天', [R])

    # Rebuttal-style flip: confirmed → denied
    await re._arecord_state_change(
        '小天', 'ref_cas', 'confirmed', 'denied', reason='rebuttal_test',
    )

    rstate = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_cas'
    )
    assert rstate['status'] == 'denied'

    # Now the late promote arrives with a stale `from_status='confirmed'`.
    # CAS must reject and leave status untouched.
    await re._arecord_state_change(
        '小天', 'ref_cas', 'confirmed', 'merged',
        absorbed_into='p_999',
    )

    rstate2 = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_cas'
    )
    assert rstate2['status'] == 'denied', (
        "CAS must drop the stale promote transition; rebuttal's denied wins"
    )
    assert rstate2.get('absorbed_into') is None, (
        "CAS-rejected transition must not write the new fields either"
    )


# ── concurrency: revalidation in _apromote_with_merge ──────────────


@pytest.mark.asyncio
async def test_apromote_skips_already_merged_reflection_under_lock(tmp_path):
    """Coderabbit Major: snapshot collected outside the lock can be
    stale. `_apromote_with_merge` must reload under the lock and skip
    if the reflection is no longer eligible — without bumping
    promote_attempt_count or making the LLM call.
    """
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    R = _reflection('ref_stale', 'x', rein=2.5)
    await re.asave_reflections('小天', [R])
    await pm.asave_persona('小天', {'master': {'facts': []}})

    # Simulate: between the loop's snapshot read and the promote call,
    # another coroutine flipped the reflection to 'merged'.
    await re._arecord_state_change(
        '小天', 'ref_stale', 'confirmed', 'merged',
        absorbed_into='p_already',
    )

    # The LLM mock would explode if reached — proves we short-circuited.
    with patch.object(
        re, '_allm_call_promotion_merge',
        AsyncMock(side_effect=AssertionError(
            "LLM must NOT be called when reflection is no longer eligible"
        )),
    ):
        outcome = await re._apromote_with_merge('小天', R)

    assert outcome == 'no_longer_eligible'
    rstate = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_stale'
    )
    assert rstate['status'] == 'merged', (
        "the concurrent transition must be preserved"
    )
    assert rstate.get('promote_attempt_count', 0) == 0, (
        "throttle counter must NOT be bumped for a no-longer-eligible reflection"
    )


# ── FACT_QUEUED_CORRECTION semantics ───────────────────────────────


@pytest.mark.asyncio
async def test_promote_fresh_with_queued_correction_keeps_confirmed(tmp_path):
    """Coderabbit Major: when aadd_fact returns FACT_QUEUED_CORRECTION
    (a non-card contradiction routed to the async correction queue),
    the reflection is NOT denied. The user's confirming intent is
    preserved; the queue may resolve in either direction. Reflection
    stays 'confirmed' so a future promote cycle can revisit.
    """
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    R = _reflection('ref_queued', '主人讨厌奶茶', rein=2.5)
    await re.asave_reflections('小天', [R])

    # Seed persona with a contradicting NON-card fact so aadd_fact
    # routes to the correction queue rather than rejecting outright.
    persona = {
        'master': {'facts': [
            _persona_entry('m_existing', '主人喜欢奶茶', rein=1.0),
        ]},
    }
    await pm.asave_persona('小天', persona)

    # Stub the contradiction detector to fire on this pair (the real
    # detector uses LLM heuristics; we only care about the routing here).
    with patch.object(
        pm, '_texts_may_contradict', return_value=True,
    ), patch.object(
        re, '_allm_call_promotion_merge',
        AsyncMock(return_value={
            'action': 'promote_fresh', 'reason': 'looks novel',
        }),
    ):
        outcome = await re._apromote_with_merge('小天', R)

    assert outcome == 'queued_correction'
    rstate = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_queued'
    )
    assert rstate['status'] == 'confirmed', (
        "FACT_QUEUED_CORRECTION must NOT mark reflection as denied — "
        "the user's confirming intent is preserved in the correction queue"
    )
    # Throttle counter bumped once so we don't tight-loop next cycle
    assert rstate['promote_attempt_count'] == 1


@pytest.mark.asyncio
async def test_promote_fresh_with_card_rejection_marks_denied(tmp_path):
    """Sibling test: FACT_REJECTED_CARD (character-card contradiction)
    IS a permanent terminal denial — the card is fixed and the
    reflection cannot ever be promoted."""
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    R = _reflection('ref_card_rej', '主人是机器人', rein=2.5)
    await re.asave_reflections('小天', [R])

    card_entry = _persona_entry('card_001', '主人是人类', rein=0.0)
    card_entry['source'] = 'character_card'
    persona = {'master': {'facts': [card_entry]}}
    await pm.asave_persona('小天', persona)

    with patch.object(
        pm, '_texts_may_contradict', return_value=True,
    ), patch.object(
        re, '_allm_call_promotion_merge',
        AsyncMock(return_value={'action': 'promote_fresh'}),
    ):
        outcome = await re._apromote_with_merge('小天', R)

    assert outcome == 'reject_by_persona'
    rstate = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_card_rej'
    )
    assert rstate['status'] == 'denied'
