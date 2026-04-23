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
        reflection_evidence={'reinforcement': 2.5, 'disputation': 0.0},
        source_reflection_id='ref_xyz', merged_from_ids=['ref_xyz'],
    )
    assert result == 'merged'

    persona_reloaded = await pm.aget_persona('小天')
    entry = persona_reloaded['master']['facts'][0]
    assert entry['text'] == 'merged new text'
    # target.rein=1.0, reflection.rein=2.5 → max=2.5 (computed under lock)
    assert entry['reinforcement'] == 2.5
    assert entry['merged_from_ids'] == ['ref_xyz']

    # Two events expected: PERSONA_EVIDENCE_UPDATED first (no-op mutate,
    # crash-safe signal) then PERSONA_ENTRY_UPDATED (canonical merge with
    # merged_from_ids sentinel). Round-4 flipped the order; the dedicated
    # order test is `test_amerge_into_evidence_updated_first_then_entry_updated`
    # — this test only asserts that both event types are present.
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
        reflection_evidence={'reinforcement': 2.0, 'disputation': 0.0},
        source_reflection_id='ref_a', merged_from_ids=['ref_a'],
    )
    assert r1 == 'merged'
    r2 = await pm.amerge_into(
        '小天', 'p_001', 'second attempt — must be ignored',
        reflection_evidence={'reinforcement': 99.0, 'disputation': 99.0},
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
        reflection_evidence={'reinforcement': 1.0, 'disputation': 0.0},
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
            reflection_evidence={'reinforcement': 2.0, 'disputation': 0.0},
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
        reflection_evidence={'reinforcement': 2.5, 'disputation': 0.0},
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


@pytest.mark.asyncio
async def test_apromote_post_llm_revalidation_blocks_persona_write(tmp_path):
    """Coderabbit round-2 (duplicate critical re-raised): the pre-LLM
    revalidation only fences up to the LLM await. The LLM call itself
    is multi-second, and another coroutine can flip the reflection's
    status to denied / merged during that window. Without a SECOND
    revalidation after the LLM returns, `aadd_fact` / `amerge_into`
    will mutate persona FIRST, and only then will _arecord_state_change's
    CAS guard refuse the status flip — leaving persona polluted with a
    fact whose source reflection is no longer eligible.
    """
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    R = _reflection('ref_in_flight', '主人爱编程', rein=2.5)
    await re.asave_reflections('小天', [R])
    await pm.asave_persona('小天', {'master': {'facts': []}})

    # Stub the LLM call to BOTH return a promote_fresh decision AND
    # simulate a concurrent rebuttal flipping the reflection to denied
    # while the LLM is "thinking".
    async def _llm_with_concurrent_flip(*_a, **_k):
        await re._arecord_state_change(
            '小天', 'ref_in_flight', 'confirmed', 'denied',
            reason='concurrent_rebuttal',
        )
        return {'action': 'promote_fresh', 'reason': 'looks novel'}

    with patch.object(
        re, '_allm_call_promotion_merge',
        AsyncMock(side_effect=_llm_with_concurrent_flip),
    ):
        outcome = await re._apromote_with_merge('小天', R)

    assert outcome == 'no_longer_eligible', (
        "post-LLM revalidation must catch the in-flight state flip"
    )

    # Persona MUST stay empty — the central guarantee is that we do NOT
    # write to persona for a reflection that is no longer eligible.
    persona = await pm.aget_persona('小天')
    assert persona['master']['facts'] == [], (
        "post-LLM revalidation failed: persona was polluted by a "
        "no-longer-eligible reflection"
    )

    # The concurrent rebuttal's `denied` must survive
    rstate = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_in_flight'
    )
    assert rstate['status'] == 'denied'
    assert rstate.get('denied_reason') == 'concurrent_rebuttal'


@pytest.mark.asyncio
async def test_apromote_post_llm_revalidation_blocks_merge_into_write(tmp_path):
    """Same protection but for the merge_into branch: the LLM picks a
    target and asks to rewrite it; meanwhile rebuttal flips status. We
    must NOT call `amerge_into` (which would rewrite the target's text).
    """
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    persona = {
        'master': {'facts': [
            _persona_entry('p_001', '原始描述', rein=1.0),
        ]},
    }
    await pm.asave_persona('小天', persona)
    R = _reflection('ref_merge_inflight', '主人喜欢编程', rein=2.5)
    await re.asave_reflections('小天', [R])

    async def _llm_with_flip(*_a, **_k):
        await re._arecord_state_change(
            '小天', 'ref_merge_inflight', 'confirmed', 'denied',
            reason='concurrent_rebuttal',
        )
        return {
            'action': 'merge_into',
            'target_id': 'persona.master.p_001',
            'merged_text': '主人非常喜欢编程',
        }

    with patch.object(
        re, '_allm_call_promotion_merge',
        AsyncMock(side_effect=_llm_with_flip),
    ):
        outcome = await re._apromote_with_merge('小天', R)

    assert outcome == 'no_longer_eligible'

    # The target's text MUST be untouched
    entry = (await pm.aget_persona('小天'))['master']['facts'][0]
    assert entry['text'] == '原始描述', (
        "post-LLM revalidation failed: target entry was rewritten by a "
        "no-longer-eligible reflection"
    )
    # And it must NOT carry the merged_from audit either
    assert 'ref_merge_inflight' not in (entry.get('merged_from_ids') or [])


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


# ── Round-4 regressions (PR #936) ─────────────────────────────────


@pytest.mark.asyncio
async def test_amerge_into_always_records_source_reflection_id(tmp_path):
    """Round-4 Minor (CodeRabbit line 923): the idempotency sentinel is
    `source_reflection_id in existing_merged_from`. If a caller passes a
    non-empty `merged_from_ids` that OMITS `source_reflection_id` (e.g.
    only contains peer merge-group ids), the previous fallback
    `(merged_from_ids or [source_reflection_id])` would skip adding the
    sentinel. A retry with the same `source_reflection_id` would then
    re-merge instead of no-op'ing — audit completeness + idempotency bug.

    The fix always appends `source_reflection_id` (if not already present)
    regardless of the caller's `merged_from_ids` input.
    """
    _ev, _fs, pm, _re, _cm = _install(str(tmp_path))
    persona = {
        'master': {'facts': [_persona_entry('p_001', 'orig', rein=1.0)]},
    }
    await pm.asave_persona('小天', persona)

    # Caller passes a non-empty list that OMITS source_reflection_id.
    r1 = await pm.amerge_into(
        '小天', 'p_001', 'first merge',
        reflection_evidence={'reinforcement': 2.0, 'disputation': 0.0},
        source_reflection_id='ref_A',
        merged_from_ids=['ref_B'],  # peer in the merge group — NOT ref_A
    )
    assert r1 == 'merged'

    entry = (await pm.aget_persona('小天'))['master']['facts'][0]
    # Both ids must end up in the audit list, preserving insertion order.
    # The sentinel (ref_A) must be present — otherwise idempotency breaks.
    assert entry['merged_from_ids'] == ['ref_B', 'ref_A'], (
        "amerge_into must always append source_reflection_id to the "
        "audit list, even when merged_from_ids is provided and omits it"
    )

    # Retry with the same source_reflection_id must now be a no-op.
    r2 = await pm.amerge_into(
        '小天', 'p_001', 'second merge — should be ignored',
        reflection_evidence={'reinforcement': 99.0, 'disputation': 99.0},
        source_reflection_id='ref_A',
        merged_from_ids=['ref_B'],
    )
    assert r2 == 'noop', (
        "retry with the same source_reflection_id must hit the "
        "idempotency gate now that the sentinel is recorded"
    )
    entry_after = (await pm.aget_persona('小天'))['master']['facts'][0]
    assert entry_after['text'] == 'first merge'
    assert entry_after['reinforcement'] == 2.0


@pytest.mark.asyncio
async def test_amerge_into_evidence_updated_first_then_entry_updated(tmp_path):
    """Round-4 Major (CodeRabbit line 1011): the two merge events must be
    emitted in the order (evidence_updated, entry_updated) so that a
    crash between them does not permanently orphan the evidence_updated
    signal (which funnel observability §3.10 relies on).

    The old order emitted entry_updated first; entry_updated writes
    `merged_from_ids` (the idempotency sentinel). A crash before the
    second event meant the retry's idempotency gate returned 'noop' and
    evidence_updated was permanently lost.

    The new order emits evidence_updated first as a no-op mutate; the
    sentinel is only written by the second event (entry_updated). A
    crash between them leaves the view in the "still not merged" state,
    so a retry re-emits BOTH events. The trade-off is a rare double-emit
    of evidence_updated on retry (funnel over-counts slightly, but never
    under-counts).
    """
    _ev, _fs, pm, _re, _cm = _install(str(tmp_path))
    persona = {
        'master': {'facts': [_persona_entry('p_001', 'orig', rein=1.0)]},
    }
    await pm.asave_persona('小天', persona)

    result = await pm.amerge_into(
        '小天', 'p_001', 'merged text',
        reflection_evidence={'reinforcement': 3.0, 'disputation': 0.0},
        source_reflection_id='ref_abc', merged_from_ids=['ref_abc'],
    )
    assert result == 'merged'

    events_path = os.path.join(str(tmp_path), '小天', 'events.ndjson')
    with open(events_path, encoding='utf-8') as f:
        events = [json.loads(line) for line in f if line.strip()]
    # Filter to the two merge-related events just emitted.
    merge_events = [
        e for e in events
        if e['type'] in ('persona.entry_updated', 'persona.evidence_updated')
    ]
    assert len(merge_events) == 2, (
        f"expected 2 merge events, got {[e['type'] for e in merge_events]}"
    )
    assert merge_events[0]['type'] == 'persona.evidence_updated', (
        "evidence_updated MUST emit first so a crash between the two "
        "writes can be recovered — see round-4 Major regression docstring"
    )
    assert merge_events[1]['type'] == 'persona.entry_updated', (
        "entry_updated MUST emit second; it writes the idempotency "
        "sentinel merged_from_ids"
    )


@pytest.mark.asyncio
async def test_amerge_into_retries_both_events_when_crashed_mid_flight(
    tmp_path, monkeypatch,
):
    """Round-4 Major (CodeRabbit line 1011) — stronger regression: simulate
    a crash AFTER evidence_updated and BEFORE entry_updated. The retry
    must re-emit BOTH events, not no-op out.

    We monkey-patch `arecord_and_save` on the event_log to raise on the
    second call (entry_updated), letting the first call (evidence_updated)
    succeed. That mirrors a process kill between the two awaits. Then
    the retry with the same `source_reflection_id` must succeed and
    bring the view into the fully-merged state.
    """
    _ev, _fs, pm, _re, _cm = _install(str(tmp_path))
    persona = {
        'master': {'facts': [_persona_entry('p_001', 'orig', rein=1.0)]},
    }
    await pm.asave_persona('小天', persona)

    real_arecord = _ev.arecord_and_save
    call_state = {'count': 0}

    async def _crashing_arecord(name, event_type, payload, **kwargs):
        call_state['count'] += 1
        if call_state['count'] == 2:
            raise RuntimeError('simulated crash between events')
        return await real_arecord(name, event_type, payload, **kwargs)

    monkeypatch.setattr(_ev, 'arecord_and_save', _crashing_arecord)

    with pytest.raises(RuntimeError, match='simulated crash'):
        await pm.amerge_into(
            '小天', 'p_001', 'merged text',
            reflection_evidence={'reinforcement': 3.0, 'disputation': 0.0},
            source_reflection_id='ref_crash', merged_from_ids=['ref_crash'],
        )

    # Only one event landed (evidence_updated); view is still un-merged
    # because entry_updated is what writes merged_from_ids.
    events_path = os.path.join(str(tmp_path), '小天', 'events.ndjson')
    with open(events_path, encoding='utf-8') as f:
        events = [json.loads(line) for line in f if line.strip()]
    assert [e['type'] for e in events] == ['persona.evidence_updated']
    entry = (await pm.aget_persona('小天'))['master']['facts'][0]
    assert entry['text'] == 'orig', (
        "view must NOT reflect the merge — entry_updated did not land"
    )
    assert 'ref_crash' not in (entry.get('merged_from_ids') or []), (
        "idempotency sentinel must NOT be on the view if entry_updated "
        "failed — otherwise retry would be stuck"
    )

    # Restore a working arecord_and_save and retry the same merge.
    monkeypatch.setattr(_ev, 'arecord_and_save', real_arecord)

    result = await pm.amerge_into(
        '小天', 'p_001', 'merged text',
        reflection_evidence={'reinforcement': 3.0, 'disputation': 0.0},
        source_reflection_id='ref_crash', merged_from_ids=['ref_crash'],
    )
    assert result == 'merged', (
        "retry must complete the merge (NOT noop) — the idempotency "
        "gate must be open because no entry_updated landed"
    )

    # After retry: both events present; evidence_updated appears twice
    # (once pre-crash, once post-retry — this is the documented
    # over-count trade-off). entry_updated appears exactly once.
    with open(events_path, encoding='utf-8') as f:
        events_after = [json.loads(line) for line in f if line.strip()]
    types_after = [e['type'] for e in events_after]
    assert types_after.count('persona.evidence_updated') == 2
    assert types_after.count('persona.entry_updated') == 1
    # View now reflects the merge.
    entry_after = (await pm.aget_persona('小天'))['master']['facts'][0]
    assert entry_after['text'] == 'merged text'
    assert 'ref_crash' in (entry_after.get('merged_from_ids') or [])


# ── concurrency: throttle check + attempt record fused under lock ──


@pytest.mark.asyncio
async def test_concurrent_promote_only_records_one_attempt(tmp_path):
    """Round-5 Major #3: the throttle check (backoff + max retries) was
    evaluated OUTSIDE the per-character lock, while
    `_arecord_promote_attempt` grabbed the lock separately later. Two
    concurrent invocations on the same eligible reflection could both
    pass the check then both record attempts — defeating the throttle
    and double-bumping `promote_attempt_count` toward the max-retries
    dead-letter.

    Fix: fuse the throttle re-check + attempt record into the same
    lock section inside `_apromote_with_merge`; the late arrival
    observes `last_promote_attempt_at` set by the winner and returns
    `skip_retry_pending`.

    This test spawns two coroutines that both call `_apromote_with_merge`
    on the same reflection; asserts only one attempt is recorded.
    """
    import asyncio
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))
    R = _reflection('ref_concurrent', '主人爱编程', rein=2.5)
    await re.asave_reflections('小天', [R])
    await pm.asave_persona('小天', {'master': {'facts': []}})

    # Stub the LLM to simulate a slow call so the two coroutines have
    # time to both pass the pre-lock throttle check. The LLM returns a
    # "reject" decision (no persona write) — we only care about the
    # throttle counter. Without the fused-lock fix, both coroutines would
    # pass the OUTER throttle check, then each take the lock serially
    # for `_arecord_promote_attempt`, resulting in count=2.
    llm_calls = 0
    llm_lock = asyncio.Lock()

    async def _slow_llm(*_a, **_k):
        nonlocal llm_calls
        async with llm_lock:
            llm_calls += 1
        await asyncio.sleep(0.01)
        return {'action': 'reject', 'reason': 'test no-op'}

    with patch.object(
        re, '_allm_call_promotion_merge',
        AsyncMock(side_effect=_slow_llm),
    ):
        # Launch both calls with the SAME stale snapshot R (mimics the
        # _aauto_promote_score_driven loop iterating a stale list).
        results = await asyncio.gather(
            re._apromote_with_merge('小天', dict(R)),
            re._apromote_with_merge('小天', dict(R)),
            return_exceptions=False,
        )

    # Exactly one call should have run the LLM path to completion
    # (the winner). The other must have short-circuited with
    # 'skip_retry_pending' when it saw the winner's attempt stamp
    # inside the lock's throttle re-check.
    winners = [r for r in results if r == 'reject']
    losers = [r for r in results if r == 'skip_retry_pending']
    assert len(winners) == 1, (
        f"exactly one coroutine should have proceeded to the LLM "
        f"call; got results={results}"
    )
    assert len(losers) == 1, (
        f"the late arrival must return skip_retry_pending; got "
        f"results={results}"
    )

    # And crucially — promote_attempt_count must be 1, not 2.
    rstate = next(
        r for r in await re._aload_reflections_full('小天')
        if r['id'] == 'ref_concurrent'
    )
    assert rstate['promote_attempt_count'] == 1, (
        f"throttle race: concurrent invocations double-bumped the "
        f"retry counter to {rstate['promote_attempt_count']} "
        f"(round-5 Major #3 regression). Expected 1."
    )


# ── Round-6 regressions (PR #936) ─────────────────────────────────


@pytest.mark.asyncio
async def test_amerge_into_aggregates_evidence_under_lock_against_freshest_target(
    tmp_path,
):
    """Round-6 Major #2 (CodeRabbit reflection.py:2158): the merge's
    conservative max-rule for evidence MUST run inside `amerge_into`
    under the per-character lock, against the CURRENTLY locked target
    entry — NOT against a stale snapshot taken outside the lock.

    Hazard: previously the caller (`_apromote_with_merge`) snapshotted
    the target via `aget_persona`, computed `max(target.rein,
    R.rein)` outside the lock, and passed the result as
    `merged_reinforcement`. If a concurrent `aapply_signal` (or
    another merge) bumped the same entry's rein between the snapshot
    and the `amerge_into` call, the merge would write back the stale
    max — effectively rolling the newer signal back.

    The fix changes the contract: callers pass `reflection_evidence`
    (the source reflection's own evidence) and `amerge_into` computes
    `max(target_locked.rein, reflection.rein)` itself under the lock.

    This test simulates the race: persona target starts at rein=1.0,
    a "concurrent" coroutine bumps it to 5.0, THEN the merge runs
    with reflection_evidence={'rein': 2.0}. Final entry rein must be
    max(5.0, 2.0) = 5.0, NOT max(1.0, 2.0) = 2.0.
    """
    _ev, _fs, pm, _re, _cm = _install(str(tmp_path))
    persona = {
        'master': {'facts': [_persona_entry('p_001', 'orig', rein=1.0)]},
    }
    await pm.asave_persona('小天', persona)

    # Simulate: a concurrent aapply_signal (or another agent) bumped
    # the target's rein to 5.0 just before our merge takes the lock.
    # In real life this is a separate coroutine; here we just mutate
    # the persisted view + cache to mirror the race outcome.
    bumped = await pm.aget_persona('小天')
    bumped['master']['facts'][0]['reinforcement'] = 5.0
    await pm.asave_persona('小天', bumped)

    # Now the merge runs. With the OLD signature the caller would pass
    # `merged_reinforcement=max(1.0, 2.0)=2.0` (stale snapshot) and
    # the merge would clobber rein from 5.0 → 2.0 — a rollback. With
    # the new signature, amerge_into computes max(target_locked=5.0,
    # reflection=2.0)=5.0 inside the lock, preserving the bump.
    result = await pm.amerge_into(
        '小天', 'p_001', 'merged text',
        reflection_evidence={'reinforcement': 2.0, 'disputation': 0.0},
        source_reflection_id='ref_race', merged_from_ids=['ref_race'],
    )
    assert result == 'merged'

    entry = (await pm.aget_persona('小天'))['master']['facts'][0]
    assert entry['reinforcement'] == 5.0, (
        f"merge clobbered the concurrently-bumped rein (round-6 Major "
        f"#2 regression): expected max(target_locked=5.0, R.rein=2.0)="
        f"5.0, got {entry['reinforcement']}. The conservative max-rule "
        f"must run under the persona lock against the freshest target, "
        f"not a snapshot taken before the lock was acquired."
    )
    # Sanity: the text rewrite still happened (merge succeeded fully).
    assert entry['text'] == 'merged text'
    assert entry['merged_from_ids'] == ['ref_race']


@pytest.mark.asyncio
async def test_amerge_into_disputation_also_under_lock(tmp_path):
    """Round-6 Major #2 symmetry: same guarantee for disputation.
    Target's disp gets bumped concurrently; merge with a smaller R.disp
    must NOT roll back the concurrent bump.
    """
    _ev, _fs, pm, _re, _cm = _install(str(tmp_path))
    persona = {
        'master': {
            'facts': [_persona_entry('p_001', 'orig', rein=2.0, disp=0.5)],
        },
    }
    await pm.asave_persona('小天', persona)

    bumped = await pm.aget_persona('小天')
    bumped['master']['facts'][0]['disputation'] = 3.0
    await pm.asave_persona('小天', bumped)

    result = await pm.amerge_into(
        '小天', 'p_001', 'merged with low disp',
        reflection_evidence={'reinforcement': 2.0, 'disputation': 0.5},
        source_reflection_id='ref_disp', merged_from_ids=['ref_disp'],
    )
    assert result == 'merged'

    entry = (await pm.aget_persona('小天'))['master']['facts'][0]
    assert entry['disputation'] == 3.0, (
        f"merge clobbered concurrently-bumped disp (round-6 Major #2): "
        f"expected max(3.0, 0.5)=3.0, got {entry['disputation']}"
    )


@pytest.mark.asyncio
async def test_apromote_with_merge_passes_reflection_evidence_not_precomputed_max(
    tmp_path,
):
    """Round-6 Major #2 contract check: `_apromote_with_merge` must
    pass the reflection's RAW evidence values (not a pre-computed
    max) to `amerge_into`. Regression target: a future refactor
    re-introducing pre-lock max computation would silently restore
    the rollback hazard. Spy on amerge_into to assert the kwargs.
    """
    _ev, _fs, pm, re, _cm = _install(str(tmp_path))

    persona = {
        'master': {'facts': [_persona_entry('p_001', 'orig', rein=4.0)]},
    }
    await pm.asave_persona('小天', persona)
    R = _reflection('ref_contract', 'merge me', rein=2.5, disp=0.0)
    await re.asave_reflections('小天', [R])

    captured = {}
    real_amerge = pm.amerge_into

    async def _spy(name, target_id, merged_text, **kwargs):
        captured.update(kwargs)
        return await real_amerge(name, target_id, merged_text, **kwargs)

    with patch.object(re, '_allm_call_promotion_merge',
                      AsyncMock(return_value={
                          'action': 'merge_into',
                          'target_id': 'persona.master.p_001',
                          'merged_text': 'merged via spy',
                      })):
        with patch.object(pm, 'amerge_into', side_effect=_spy):
            outcome = await re._apromote_with_merge('小天', dict(R))

    assert outcome == 'merge_into'
    # The contract: kwargs MUST carry `reflection_evidence` with the
    # reflection's raw values, NOT `merged_reinforcement` /
    # `merged_disputation` (the deprecated pre-lock-computed form).
    assert 'reflection_evidence' in captured, (
        "_apromote_with_merge must pass reflection_evidence kwarg "
        "(round-6 Major #2 contract); old keys merged_reinforcement/"
        "merged_disputation re-introduce the rollback hazard"
    )
    assert 'merged_reinforcement' not in captured
    assert 'merged_disputation' not in captured
    rev = captured['reflection_evidence']
    assert rev.get('reinforcement') == 2.5, (
        f"reflection_evidence.reinforcement must be the reflection's "
        f"raw rein (2.5), not max(target=4.0, R=2.5)=4.0; got {rev!r}"
    )
    assert rev.get('disputation') == 0.0
    # And the on-disk result reflects max(target=4.0, R=2.5)=4.0
    # (target wins), proving the lock-side compute happened.
    entry = (await pm.aget_persona('小天'))['master']['facts'][0]
    assert entry['reinforcement'] == 4.0
