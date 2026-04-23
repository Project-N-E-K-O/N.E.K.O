# -*- coding: utf-8 -*-
"""
Unit tests for the persona / reflection token-count cache.

Covers the design in `memory/persona.py` `_get_cached_token_count` /
`_aget_cached_token_count`:

  - First render computes `token_count` + `token_count_text_sha256` and
    writes them back onto the entry dict in-memory.
  - Second render reuses the cache — the tokenizer is NOT called again.
  - Rewriting `entry['text']` invalidates via fingerprint mismatch on
    the next render, triggering a clean recompute.
  - `amerge_into` explicitly invalidates the cache when it rewrites
    text, so a concurrent reader can't see new-text + stale-count.
  - Cache rides along on `asave_persona` / `asave_reflections` — a
    save-then-reload round-trip preserves the fields and the subsequent
    render is a pure cache hit with zero tokenizer calls.
  - Legacy entries without the fingerprint field (or with a corrupted
    fingerprint) get a clean recompute on first render.
  - The JSON round-trip of a cached entry does not KeyError on reload
    and the fields survive disk.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── helpers (minimal harness, keeps tests focused) ──────────────────


def _mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_config_value.return_value = False
    return cm


def _build_pm(tmpdir: str):
    """PersonaManager bound to `tmpdir` for disk round-trips.
    `event_log` injected so `amerge_into` doesn't bail on the null-check."""
    from memory.event_log import EventLog
    from memory.persona import PersonaManager

    cm = _mock_cm(tmpdir)
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.persona.get_config_manager", return_value=cm):
        event_log = EventLog()
        event_log._config_manager = cm
        pm = PersonaManager(event_log=event_log)
        pm._config_manager = cm
    return pm, event_log, cm


def _entry(eid: str, text: str, *, rein: float = 1.0) -> dict:
    """Build a persona entry with all schema fields PR-3 cares about.
    Intentionally omits `token_count` / `token_count_text_sha256` so
    the render path has to populate them itself."""
    return {
        'id': eid, 'text': text,
        'source': 'manual', 'source_id': None,
        'reinforcement': rein, 'disputation': 0.0,
        'rein_last_signal_at': None, 'disp_last_signal_at': None,
        'sub_zero_days': 0, 'sub_zero_last_increment_date': None,
        'user_fact_reinforce_count': 0,
        'merged_from_ids': [],
        'importance': 0,
        'protected': False,
        'suppress': False, 'suppressed_at': None,
        'recent_mentions': [],
    }


def _sha(text: str) -> str:
    return hashlib.sha256((text or '').encode('utf-8')).hexdigest()


# ── sync helper: deterministic cache contract ──────────────────────


def test_first_call_computes_and_caches_sync():
    """`_get_cached_token_count` populates both fields on first call and
    the value matches `count_tokens` directly."""
    from memory.persona import PersonaManager
    from utils.tokenize import count_tokens

    e = _entry('m1', '主人很喜欢猫')
    n = PersonaManager._get_cached_token_count(e)
    assert n == count_tokens('主人很喜欢猫')
    assert e['token_count'] == n
    assert e['token_count_text_sha256'] == _sha('主人很喜欢猫')


def test_second_call_uses_cache_sync():
    """Second call with `count_tokens` patched to raise still succeeds
    because the fingerprint matches and we short-circuit."""
    from memory.persona import PersonaManager

    e = _entry('m1', 'stable text')
    first = PersonaManager._get_cached_token_count(e)

    # Swap count_tokens to an exploder — cache path must not call it.
    with patch('memory.persona.count_tokens',
               side_effect=AssertionError(
                   'cache miss — count_tokens should not be called')):
        second = PersonaManager._get_cached_token_count(e)

    assert first == second > 0


def test_text_mutation_triggers_recompute_sync():
    """Direct mutation of `entry['text']` invalidates the cache via
    fingerprint mismatch → recompute fires."""
    from memory.persona import PersonaManager

    e = _entry('m1', 'original text')
    PersonaManager._get_cached_token_count(e)
    old_count = e['token_count']

    # Mutate text *after* the cache was populated — simulating a
    # non-merge codepath that forgot to invalidate explicitly. The
    # fingerprint check is the safety net.
    e['text'] = 'completely different and much much much longer text ' * 4

    recomputed = PersonaManager._get_cached_token_count(e)
    assert recomputed != old_count, (
        'text changed drastically; token count should change too'
    )
    assert e['token_count'] == recomputed
    assert e['token_count_text_sha256'] == _sha(e['text'])


def test_missing_fingerprint_field_triggers_recompute_sync():
    """Legacy entries (pre-schema-addition) have no fingerprint field.
    `.get()` returns None; the match check fails; recompute happens."""
    from memory.persona import PersonaManager

    e = _entry('m1', 'legacy pre-cache entry')
    # Simulate a legacy persona.json: the cache fields simply don't
    # exist on the dict at all.
    e.pop('token_count', None)
    e.pop('token_count_text_sha256', None)
    assert 'token_count' not in e

    n = PersonaManager._get_cached_token_count(e)
    assert n > 0
    assert e['token_count'] == n
    assert e['token_count_text_sha256'] == _sha('legacy pre-cache entry')


def test_corrupted_fingerprint_triggers_recompute_sync():
    """Fingerprint present but doesn't match text (e.g. someone edited
    text via another path without running the invalidator) → recompute."""
    from memory.persona import PersonaManager

    e = _entry('m1', 'real text')
    e['token_count'] = 999_999            # wildly wrong
    e['token_count_text_sha256'] = 'deadbeef' * 8  # bogus sha

    recomputed = PersonaManager._get_cached_token_count(e)
    assert recomputed < 999_999, (
        'mismatched fingerprint must force recompute, not trust stale count'
    )
    assert e['token_count'] == recomputed
    assert e['token_count_text_sha256'] == _sha('real text')


def test_empty_text_short_circuits_without_cache_write():
    """Empty / None text returns 0 without mutating the entry — the
    cache field default of None is fine (0 render cost anyway)."""
    from memory.persona import PersonaManager

    e = _entry('m1', '')
    assert PersonaManager._get_cached_token_count(e) == 0
    # We deliberately do NOT cache the 0 — empty is the cheapest
    # possible case and keeping the cache fields None distinguishes
    # "never rendered" from "rendered as empty".
    assert e.get('token_count') is None
    assert e.get('token_count_text_sha256') is None


# ── async helper: same contract via acount_tokens ──────────────────


@pytest.mark.asyncio
async def test_first_render_populates_cache_async():
    """End-to-end: after `_ascore_trim_entries` runs once, every kept
    entry carries populated cache fields."""
    from memory.persona import PersonaManager

    entries = [_entry('m1', '这是第一条事实' * 3, rein=3.0),
               _entry('m2', 'another latin fact entry goes here', rein=2.0)]
    kept = await PersonaManager._ascore_trim_entries(
        entries, budget=10_000, now=datetime.now(),
    )
    assert len(kept) == 2
    for e in kept:
        assert isinstance(e['token_count'], int) and e['token_count'] > 0
        assert e['token_count_text_sha256'] == _sha(e['text'])


@pytest.mark.asyncio
async def test_second_render_uses_cache_async():
    """Second render with `acount_tokens` patched to blow up must still
    succeed — the cache path is what keeps us off tiktoken."""
    from memory.persona import PersonaManager

    entries = [_entry('m1', '缓存测试文本'),
               _entry('m2', 'cache stability check text')]

    # Warm the cache.
    await PersonaManager._ascore_trim_entries(
        entries, budget=10_000, now=datetime.now(),
    )

    async def _boom(*_args, **_kwargs):
        raise AssertionError(
            'cache hit expected; acount_tokens must not be called'
        )

    with patch('memory.persona.acount_tokens', side_effect=_boom):
        kept = await PersonaManager._ascore_trim_entries(
            entries, budget=10_000, now=datetime.now(),
        )

    assert [e['id'] for e in kept] == ['m1', 'm2']


@pytest.mark.asyncio
async def test_text_change_invalidates_cache_across_renders_async():
    """Warm cache, mutate text, re-render — the acount_tokens count
    must increase by exactly 1 (the mutated entry recomputes; the
    other hits cache)."""
    from memory.persona import PersonaManager

    entries = [_entry('m1', 'stable text that will not change'),
               _entry('m2', 'mutated')]
    await PersonaManager._ascore_trim_entries(
        entries, budget=10_000, now=datetime.now(),
    )

    call_count = {'n': 0}
    from utils.tokenize import acount_tokens as real_acount

    async def _counting_acount(text, *a, **kw):
        call_count['n'] += 1
        return await real_acount(text, *a, **kw)

    # Mutate m2's text — m1 stays stable so its fingerprint still matches.
    entries[1]['text'] = 'mutated and drastically longer than before' * 3

    with patch('memory.persona.acount_tokens', side_effect=_counting_acount):
        await PersonaManager._ascore_trim_entries(
            entries, budget=10_000, now=datetime.now(),
        )
    assert call_count['n'] == 1, (
        f'expected exactly one acount_tokens call (the mutated entry); '
        f'got {call_count["n"]}'
    )


@pytest.mark.asyncio
async def test_amerge_into_invalidates_cache(tmp_path):
    """`amerge_into` rewrites `target_entry['text']`; the explicit
    invalidation in `_sync_mutate_entry` must zero out both cache
    fields so the next render recomputes against the new text."""
    from memory.persona import PersonaManager

    pm, _event_log, _cm = _build_pm(str(tmp_path))

    target = _entry('card_target', 'original target text', rein=1.0)
    persona = {'master': {'facts': [target]}}
    pm._personas['小天'] = persona

    # Warm the cache on the target entry.
    await PersonaManager._aget_cached_token_count(target)
    assert target['token_count'] is not None
    assert target['token_count_text_sha256'] is not None

    # Drive amerge_into — this rewrites text + invalidates cache.
    res = await pm.amerge_into(
        '小天',
        target_entry_id='card_target',
        merged_text='brand new merged text after absorption',
        reflection_evidence={'reinforcement': 2.0, 'disputation': 0.0},
        source_reflection_id='ref_001',
    )
    assert res == 'merged'
    assert target['text'] == 'brand new merged text after absorption'
    # Cache was cleared explicitly — next render will recompute.
    assert target['token_count'] is None
    assert target['token_count_text_sha256'] is None


@pytest.mark.asyncio
async def test_cache_survives_persona_save_reload_roundtrip(tmp_path):
    """Cache fields ride along on `asave_persona`. After eviction from
    `_personas` and a disk reload, the fields come back populated and
    a subsequent render is a pure cache hit (zero tokenizer calls)."""
    pm, _event_log, _cm = _build_pm(str(tmp_path))

    e1 = _entry('m1', '第一条持久化事实', rein=3.0)
    e2 = _entry('m2', 'second persistence fact', rein=2.0)
    persona = {'master': {'facts': [e1, e2]}}
    pm._personas['小天'] = persona

    # Warm the cache through the real render helper.
    from memory.persona import PersonaManager
    await PersonaManager._ascore_trim_entries(
        [e1, e2], budget=10_000, now=datetime.now(),
    )
    assert e1['token_count'] is not None
    assert e2['token_count'] is not None

    # Persist — this is the ride-along.
    await pm.asave_persona('小天', persona)

    # Evict the in-memory cache so a reload happens from disk.
    pm._personas.pop('小天', None)

    # Reload — `_aensure_persona_locked` re-reads persona.json.
    reloaded = await pm.aget_persona('小天')
    reloaded_entries = reloaded['master']['facts']
    assert len(reloaded_entries) == 2
    for e in reloaded_entries:
        assert isinstance(e['token_count'], int) and e['token_count'] > 0
        assert e['token_count_text_sha256'] == _sha(e['text'])

    # Second render: acount_tokens must not be called for these entries.
    async def _boom(*_a, **_kw):
        raise AssertionError(
            'cache hit expected post-reload; acount_tokens must not be called'
        )

    with patch('memory.persona.acount_tokens', side_effect=_boom):
        kept = await PersonaManager._ascore_trim_entries(
            reloaded_entries, budget=10_000, now=datetime.now(),
        )
    assert len(kept) == 2


@pytest.mark.asyncio
async def test_cache_survives_reflection_save_reload_roundtrip(tmp_path):
    """Mirror test for reflections: after `asave_reflections` +
    eviction + reload, the fields are still there."""
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine

    cm = _mock_cm(str(tmp_path))
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

    now_iso = datetime.now().isoformat()
    reflections = [
        {
            'id': 'r1', 'text': 'reflection caching test',
            'entity': 'master', 'status': 'confirmed',
            'created_at': now_iso, 'importance': 1,
            'reinforcement': 1.0, 'disputation': 0.0,
            'rein_last_signal_at': None, 'disp_last_signal_at': None,
            'sub_zero_days': 0, 'sub_zero_last_increment_date': None,
            'user_fact_reinforce_count': 0,
            'absorbed_into': None,
            'last_promote_attempt_at': None,
            'promote_attempt_count': 0,
            'promote_blocked_reason': None,
            'recent_mentions': [],
            'suppress': False, 'suppressed_at': None,
        },
    ]

    # Warm the cache via the render helper (reflections go through the
    # same generic `_ascore_trim_entries`).
    await PersonaManager._ascore_trim_entries(
        reflections, budget=10_000, now=datetime.now(),
    )
    assert reflections[0]['token_count'] is not None

    await re.asave_reflections('小天', reflections)

    # Reload from disk — `aload_reflections` runs `_filter_reflections`
    # which calls `_normalize_reflection`, so our new default fields
    # get in-place defaults for any legacy items. Our persisted cache
    # already has them so nothing gets overwritten.
    reloaded = await re.aload_reflections('小天', include_archived=False)
    assert len(reloaded) == 1
    r = reloaded[0]
    assert isinstance(r['token_count'], int) and r['token_count'] > 0
    assert r['token_count_text_sha256'] == _sha(r['text'])


def test_normalize_entry_defaults_cache_fields_to_none():
    """`_normalize_entry` is the single source of truth for fact-entry
    defaults. The cache fields must default to None so first-render
    logic knows to recompute."""
    from memory.persona import PersonaManager

    d = PersonaManager._normalize_entry('plain string fact')
    assert d['token_count'] is None
    assert d['token_count_text_sha256'] is None
    # Re-run on a dict that already has the field → idempotent; don't
    # clobber a populated cache.
    d['token_count'] = 42
    d['token_count_text_sha256'] = 'cafebabe' * 8
    d2 = PersonaManager._normalize_entry(d)
    assert d2['token_count'] == 42
    assert d2['token_count_text_sha256'] == 'cafebabe' * 8


def test_normalize_reflection_defaults_cache_fields_to_none():
    """Mirror for reflections."""
    from memory.reflection import ReflectionEngine

    r = ReflectionEngine._normalize_reflection(
        {'id': 'r1', 'text': 'anything'}
    )
    assert r['token_count'] is None
    assert r['token_count_text_sha256'] is None
    # Idempotent on a pre-populated dict.
    r['token_count'] = 7
    r['token_count_text_sha256'] = 'f' * 64
    r2 = ReflectionEngine._normalize_reflection(r)
    assert r2['token_count'] == 7
    assert r2['token_count_text_sha256'] == 'f' * 64


@pytest.mark.asyncio
async def test_cached_entry_json_roundtrip_no_keyerror(tmp_path):
    """Persist an entry with cache fields, reload via plain JSON, and
    re-run normalize — no KeyError, fields preserved."""
    from memory.persona import PersonaManager

    pm, _el, _cm = _build_pm(str(tmp_path))
    e = _entry('m1', 'roundtrip sanity check entry')
    PersonaManager._get_cached_token_count(e)  # populate cache
    pm._personas['小天'] = {'master': {'facts': [e]}}
    await pm.asave_persona('小天')

    # Read the raw JSON ourselves to confirm the fields hit disk.
    path = pm._persona_path('小天')
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    disk_entry = raw['master']['facts'][0]
    assert disk_entry['token_count'] == e['token_count']
    assert disk_entry['token_count_text_sha256'] == e['token_count_text_sha256']

    # Re-run normalize on the disk copy — must not raise and must not
    # wipe the populated cache.
    normalized = PersonaManager._normalize_entry(disk_entry)
    assert normalized['token_count'] == e['token_count']
    assert normalized['token_count_text_sha256'] == (
        e['token_count_text_sha256']
    )
