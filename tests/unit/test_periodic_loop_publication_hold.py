# -*- coding: utf-8 -*-
"""Periodic memory loops skip a character that a rename / delete has fenced (#2935).

A rename / delete retires the old name (and holds it until publication)
before the main process moves or removes its memory directory. The periodic
loops read the roster once per round and then work per character; a name
that is retired by the time its turn comes must be skipped, or the loop's
JSON writes recreate the released directory.

Each case runs one round of a real loop with its per-character work replaced
by a recorder, so the assertions are about which names reached the work, not
about what the work does. The fence is taken and released through the real
``review`` admission API.
"""
from __future__ import annotations

import asyncio
import itertools
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import memory_server
from app.memory_server import (
    evidence_loops,
    gates,
    long_term_sync,
    received_extraction,
    refine_loops,
    review,
    runtime,
    signal_extraction,
)

HELD = '旧名喵'
OPEN = '小八'
ROSTER = {'猫娘': {HELD: {'uid': 'uid-held'}, OPEN: {'uid': 'uid-open'}}}

_tokens = itertools.count()


async def _fence(name: str, *, hold: bool) -> str:
    token = f'periodic-loop-test-{next(_tokens)}'
    await review.cancel_character_derived_tasks(
        name,
        hold_until_publication=hold,
        claim_token=token,
        claim_generation=1,
    )
    return token


async def _unfence(name: str, token: str) -> None:
    await review.release_character_derived_task_admission_claim(name, token)


@pytest.fixture(autouse=True)
def _clean_admission_state(monkeypatch):
    monkeypatch.setattr(review, '_retired_derived_task_names', set())
    monkeypatch.setattr(review, '_publication_held_derived_task_names', set())
    monkeypatch.setattr(review, '_derived_task_admission_claims', {})
    yield


@pytest.fixture
def harness(monkeypatch):
    """Shared roster, power switch and a sleep that ends the loop after one round."""
    config_manager = MagicMock()
    config_manager.aload_characters = AsyncMock(return_value=ROSTER)
    config_manager.app_docs_dir = '/nonexistent-test-docs'
    monkeypatch.setattr(runtime, '_config_manager', config_manager)
    state = SimpleNamespace(powerful=True, sleeps=0, seen=[])

    async def powerful():
        return state.powerful

    monkeypatch.setattr(gates, '_ais_powerful_memory_enabled', powerful)
    monkeypatch.setattr(gates, '_is_idle', lambda: True)

    async def one_round_sleep(_seconds):
        # First call is the initial delay, second one closes the round.
        state.sleeps += 1
        if state.sleeps >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, 'sleep', one_round_sleep)

    def record(name, *_args, **_kwargs):
        state.seen.append(name)

    # Looked up on ``state`` at call time so a test can swap the recorder.
    async def arecord(name, *args, **kwargs):
        state.record(name)
        return None

    async def arecord_false(name, *args, **kwargs):
        state.record(name)
        return False

    async def arecord_raise(name, *args, **kwargs):
        # Stop the per-character body right after the first write-capable call.
        state.record(name)
        raise RuntimeError('stop here')

    state.record = record
    state.arecord = arecord
    state.arecord_false = arecord_false
    state.arecord_raise = arecord_raise
    return state


async def _run_round(loop_fn):
    with pytest.raises(asyncio.CancelledError):
        await loop_fn()


async def _run_locale(name, fn, *args):
    return await fn(*args)


# Each case: (id, setup(monkeypatch, harness) -> loop coroutine function).
def _rebuttal(mp, h):
    mp.setattr(runtime, 'reflection_engine', SimpleNamespace(
        aget_confirmed_reflections=h.arecord_raise,
    ))
    return evidence_loops._periodic_rebuttal_loop


def _rebuttal_disabled(mp, h):
    h.powerful = False
    mp.setattr(runtime, 'cursor_store', SimpleNamespace(aset_cursor=h.arecord))
    return evidence_loops._periodic_rebuttal_loop


def _auto_promote(mp, h):
    mp.setattr(evidence_loops, '_auto_promote_character', h.arecord)
    return evidence_loops._periodic_auto_promote_loop


def _idle_maintenance(mp, h):
    mp.setattr(runtime, 'recent_history_manager', SimpleNamespace(
        aget_recent_history=h.arecord_raise,
    ))
    return evidence_loops._periodic_idle_maintenance_loop


def _slow_recheck(mp, h):
    mp.setattr(evidence_loops, 'MEMORY_RECHECK_ENABLED', True)
    # Round-robin start is module state; pin it so the roster order holds.
    mp.setattr(evidence_loops, '_RECHECK_RR_CURSOR', 0)
    mp.setattr(runtime, 'reflection_engine', SimpleNamespace(
        arecheck_one_legacy_reflection=h.arecord_false,
    ))
    mp.setattr(runtime, 'fact_store', SimpleNamespace(
        arecheck_one_legacy_fact=h.arecord_false,
    ))
    return evidence_loops._periodic_slow_memory_recheck_loop


def _archive_sweep(mp, h):
    mp.setattr(runtime, 'reflection_engine', SimpleNamespace(
        _aload_reflections_full=h.arecord_raise,
    ))
    return evidence_loops._periodic_archive_sweep_loop


def _persona_refine(mp, h):
    mp.setattr(refine_loops, 'run_with_character_prompt_locale', _run_locale)
    mp.setattr(refine_loops, '_run_persona_refine_for_character', h.arecord_raise)
    return refine_loops._periodic_persona_refine_loop


def _reflection_refine(mp, h):
    mp.setattr(refine_loops, 'run_with_character_prompt_locale', _run_locale)
    mp.setattr(
        refine_loops, '_run_reflection_refine_with_subject_locales', h.arecord_raise,
    )
    return refine_loops._periodic_reflection_refine_loop


def _scoped_refine(mp, h):
    mp.setattr(refine_loops, 'run_with_character_prompt_locale', _run_locale)
    mp.setattr(refine_loops, '_run_scoped_refine_for_character', h.arecord_false)
    mp.setattr(refine_loops, '_scoped_refine_character_cursor', None)

    async def one_round():
        await refine_loops._run_scoped_refine_round(list(ROSTER['猫娘']))
        raise asyncio.CancelledError

    return one_round


def _reflection_synthesis(mp, h):
    mp.setattr(refine_loops, 'run_with_character_prompt_locale', _run_locale)
    mp.setattr(runtime, 'reflection_engine', SimpleNamespace(
        synthesize_reflections=h.arecord_raise,
    ))
    return refine_loops._periodic_reflection_synthesis_loop


def _signal_extraction(mp, h):
    async def run(name, fn):
        h.record(name)

    mp.setattr(signal_extraction, '_run_signal_check_with_character_locale', run)
    return signal_extraction._periodic_signal_extraction_loop


def _signal_extraction_disabled(mp, h):
    h.powerful = False
    mp.setattr(signal_extraction, '_signal_check_mark_done', h.record)
    return signal_extraction._periodic_signal_extraction_loop


def _received_extraction(mp, h):
    async def run(name, fn):
        h.record(name)

    from utils.config_manager import reserved_schema

    mp.setattr(signal_extraction, '_run_signal_check_with_character_locale', run)
    mp.setattr(reserved_schema, 'get_character_uid', lambda profile: profile.get('uid'))
    return received_extraction._periodic_received_extraction_loop


def _organizer(mp, h):
    mp.setattr(long_term_sync, 'resume_owed_capture', h.arecord)
    mp.setattr(long_term_sync, 'tend_organizer', AsyncMock(return_value=False))
    return long_term_sync._periodic_organizer_loop


def _no_storage_fence(mp):
    from utils import cloudsave_runtime

    mp.setattr(runtime, '_memory_storage_blocked_after_init', False)
    mp.setattr(cloudsave_runtime, 'is_write_fence_active', lambda _cm: False)


def _habitual_phrases(mp, h):
    from app.memory_server import habitual_phrase_loop

    _no_storage_fence(mp)
    mp.setattr(habitual_phrase_loop, '_arefresh_habitual_phrases_one', h.arecord)
    return habitual_phrase_loop._periodic_habitual_phrase_loop


def _event_log_compact(mp, h):
    _no_storage_fence(mp)
    mp.setattr(evidence_loops, '_acompact_event_log_one', h.arecord)
    return evidence_loops._periodic_event_log_compact_loop


CASES = [
    ('rebuttal', _rebuttal),
    ('rebuttal_disabled_cursor_advance', _rebuttal_disabled),
    ('auto_promote', _auto_promote),
    ('idle_maintenance', _idle_maintenance),
    ('slow_memory_recheck', _slow_recheck),
    ('archive_sweep', _archive_sweep),
    ('persona_refine', _persona_refine),
    ('reflection_refine', _reflection_refine),
    ('scoped_refine', _scoped_refine),
    ('reflection_synthesis', _reflection_synthesis),
    ('signal_extraction', _signal_extraction),
    ('signal_extraction_disabled_cursor_advance', _signal_extraction_disabled),
    ('received_extraction', _received_extraction),
    ('long_term_organizer', _organizer),
    ('habitual_phrases', _habitual_phrases),
    ('event_log_compact', _event_log_compact),
]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize('hold', [True, False], ids=['publication_hold', 'retired'])
@pytest.mark.parametrize('setup', [c[1] for c in CASES], ids=[c[0] for c in CASES])
async def test_loop_skips_fenced_character_and_resumes_after_release(
    monkeypatch, harness, setup, hold,
):
    loop_fn = setup(monkeypatch, harness)
    token = await _fence(HELD, hold=hold)
    assert not review.is_character_periodic_write_admitted(HELD)

    await _run_round(loop_fn)
    assert HELD not in harness.seen
    # The loop is not starved: the other character still gets its turn.
    assert OPEN in harness.seen

    await _unfence(HELD, token)
    assert review.is_character_periodic_write_admitted(HELD)
    harness.seen.clear()
    harness.sleeps = 0

    await _run_round(loop_fn)
    assert HELD in harness.seen


SERIAL_CASES = [
    ('rebuttal_disabled_cursor_advance', _rebuttal_disabled),
    ('idle_maintenance', _idle_maintenance),
    ('slow_memory_recheck', _slow_recheck),
    ('persona_refine', _persona_refine),
    ('reflection_refine', _reflection_refine),
    ('scoped_refine', _scoped_refine),
    ('reflection_synthesis', _reflection_synthesis),
    ('received_extraction', _received_extraction),
    ('long_term_organizer', _organizer),
    ('habitual_phrases', _habitual_phrases),
    ('event_log_compact', _event_log_compact),
]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    'setup', [c[1] for c in SERIAL_CASES], ids=[c[0] for c in SERIAL_CASES],
)
async def test_serial_loop_skips_character_fenced_mid_round(monkeypatch, harness, setup):
    """The roster is read before the fence; the fence lands while an earlier
    character is being worked on. The later character must still be skipped."""
    loop_fn = setup(monkeypatch, harness)
    record = harness.record

    # Order the roster so the open character comes first and the fenced one
    # is reached only after the fence has landed.
    roster = {'猫娘': {OPEN: {'uid': 'uid-open'}, HELD: {'uid': 'uid-held'}}}
    runtime._config_manager.aload_characters = AsyncMock(return_value=roster)
    monkeypatch.setitem(ROSTER, '猫娘', roster['猫娘'])

    fenced_during = []

    def fence_on_first_work(name, *args, **kwargs):
        record(name)
        if name == OPEN and not fenced_during:
            # A rename of HELD takes its hold while OPEN is being worked on:
            # same state the real release endpoint leaves behind, set from
            # inside the first character's work so the ordering is fixed.
            review._retired_derived_task_names.add(HELD)
            review._publication_held_derived_task_names.add(HELD)
            fenced_during.append(name)

    harness.record = fence_on_first_work
    await _run_round(loop_fn)

    assert fenced_during == [OPEN]
    assert HELD not in harness.seen


@pytest.mark.unit
def test_admission_predicate_follows_retire_and_hold_sets():
    assert review.is_character_periodic_write_admitted(HELD)
    review._retired_derived_task_names.add(HELD)
    assert not review.is_character_periodic_write_admitted(HELD)
    review._retired_derived_task_names.discard(HELD)
    review._publication_held_derived_task_names.add(HELD)
    assert not review.is_character_periodic_write_admitted(HELD)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    'setup', [c[1] for c in SERIAL_CASES], ids=[c[0] for c in SERIAL_CASES],
)
async def test_serial_loop_skips_character_renamed_away_mid_round(
    monkeypatch, harness, setup,
):
    """A whole rename finishes while an earlier character is being worked on:
    the claim is released, so the old name is neither retired nor held any
    more, but it is gone from the roster. The round's stale roster still lists
    it; the loop must not reach its work."""
    loop_fn = setup(monkeypatch, harness)
    record = harness.record

    roster = {'猫娘': {OPEN: {'uid': 'uid-open'}, HELD: {'uid': 'uid-held'}}}
    runtime._config_manager.aload_characters = AsyncMock(return_value=roster)
    monkeypatch.setitem(ROSTER, '猫娘', roster['猫娘'])

    renamed_during = []

    def rename_on_first_work(name, *args, **kwargs):
        record(name)
        if name == OPEN and not renamed_during:
            # Published: characters.json now carries the new name only, and
            # the old name's admission state is back to open.
            runtime._config_manager.aload_characters.return_value = {
                '猫娘': {OPEN: {'uid': 'uid-open'}, '新名喵': {'uid': 'uid-held'}},
            }
            renamed_during.append(name)

    harness.record = rename_on_first_work
    await _run_round(loop_fn)

    assert renamed_during == [OPEN]
    assert review.is_character_periodic_write_admitted(HELD)
    assert HELD not in harness.seen


@pytest.mark.unit
@pytest.mark.asyncio
async def test_async_gate_skips_when_roster_cannot_be_read(monkeypatch, harness):
    runtime._config_manager.aload_characters = AsyncMock(side_effect=OSError('busy'))
    assert not await review.ais_character_periodic_write_admitted(OPEN)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gate_matches_raw_roster_key_against_normalized_fence(harness):
    """The release endpoint fences the normalized name; the loops iterate the
    raw characters.json key."""
    raw = f' {HELD} '
    runtime._config_manager.aload_characters = AsyncMock(
        return_value={'猫娘': {raw: {}}},
    )
    assert await review.ais_character_periodic_write_admitted(raw)
    token = await _fence(HELD, hold=True)
    assert not review.is_character_periodic_write_admitted(raw)
    assert not await review.ais_character_periodic_write_admitted(raw)
    await _unfence(HELD, token)
    assert await review.ais_character_periodic_write_admitted(raw)
