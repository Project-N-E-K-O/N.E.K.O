# -*- coding: utf-8 -*-
"""
Unit tests for the persona token-count cache.

Covers the design in `memory/persona.py` `_get_cached_token_count` /
`_aget_cached_token_count`:

  - First render computes `token_count` + `token_count_text_sha256` +
    `token_count_tokenizer` and writes them back onto the entry dict
    in-memory.
  - Second render reuses the cache — the tokenizer is NOT called again.
  - Rewriting `entry['text']` invalidates via text-fingerprint mismatch
    on the next render, triggering a clean recompute.
  - Changing the tokenizer identity (e.g. tiktoken→heuristic fallback)
    invalidates via tokenizer-fingerprint mismatch on the next render.
  - `amerge_into` and `_apply_character_card_sync` explicitly invalidate
    the cache when they rewrite text, so a concurrent reader can't see
    new-text + stale-count.
  - Cache rides along on `asave_persona` — a save-then-reload round-trip
    preserves the fields and the subsequent render is a pure cache hit
    with zero tokenizer calls.
  - Legacy entries without the fingerprint field (or with a corrupted
    fingerprint) get a clean recompute on first render.
  - The JSON round-trip of a cached entry does not KeyError on reload
    and the fields survive disk.

Reflection entries are deliberately NOT cached (see
`_ascore_trim_entries(..., cache_writeback=False)` call at the reflection
render site): reflections are always loaded fresh from disk via
`aload_reflections` / `_aload_reflections_full`, so any writeback would
be garbage-collected before the next render could reuse it. The tests at
the bottom of this file lock in that "no pollution" contract.
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
    """`_get_cached_token_count` populates all three fields on first
    call and the value matches `count_tokens` directly."""
    from memory.persona import PersonaManager
    from utils.tokenize import count_tokens, tokenizer_identity

    e = _entry('m1', '主人很喜欢猫')
    n = PersonaManager._get_cached_token_count(e)
    assert n == count_tokens('主人很喜欢猫')
    assert e['token_count'] == n
    assert e['token_count_text_sha256'] == _sha('主人很喜欢猫')
    assert e['token_count_tokenizer'] == tokenizer_identity()


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
    from utils.tokenize import tokenizer_identity

    e = _entry('m1', 'legacy pre-cache entry')
    # Simulate a legacy persona.json: the cache fields simply don't
    # exist on the dict at all.
    e.pop('token_count', None)
    e.pop('token_count_text_sha256', None)
    e.pop('token_count_tokenizer', None)
    assert 'token_count' not in e

    n = PersonaManager._get_cached_token_count(e)
    assert n > 0
    assert e['token_count'] == n
    assert e['token_count_text_sha256'] == _sha('legacy pre-cache entry')
    assert e['token_count_tokenizer'] == tokenizer_identity()


def test_corrupted_fingerprint_triggers_recompute_sync():
    """Fingerprint present but doesn't match text (e.g. someone edited
    text via another path without running the invalidator) → recompute."""
    from memory.persona import PersonaManager

    e = _entry('m1', 'real text')
    e['token_count'] = 999_999            # wildly wrong
    e['token_count_text_sha256'] = 'deadbeef' * 8  # bogus sha
    e['token_count_tokenizer'] = 'tiktoken:o200k_base'  # plausible but stale

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
    assert e.get('token_count_tokenizer') is None


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
    from utils.tokenize import tokenizer_identity
    assert len(kept) == 2
    for e in kept:
        assert isinstance(e['token_count'], int) and e['token_count'] > 0
        assert e['token_count_text_sha256'] == _sha(e['text'])
        assert e['token_count_tokenizer'] == tokenizer_identity()


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
    assert target['token_count_tokenizer'] is not None

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
    assert target['token_count_tokenizer'] is None


@pytest.mark.asyncio
async def test_character_card_sync_invalidates_cache_on_text_rewrite(tmp_path):
    """`_apply_character_card_sync` rewrites `entry['text']` in-place when
    the character card's field value changes (lines 509-513). The cache
    must be explicitly invalidated alongside the rewrite, mirroring the
    contract `amerge_into` already holds. Without the invalidator the
    stale `token_count` would ride on disk until the fingerprint safety
    net caught the mismatch on the NEXT render — correct, but slower
    (one extra sha256 per stale entry) and misleading during debug."""
    from memory.persona import PersonaManager

    pm, _event_log, _cm = _build_pm(str(tmp_path))

    # Set up a card-sourced entry WITH a warmed cache (simulating an
    # entry that has already survived one render).
    entity = 'master'
    field_name = 'nickname'
    entry_id = PersonaManager._card_entry_id(entity, field_name)
    card_entry = {
        'id': entry_id,
        'text': 'nickname: 原始称呼',
        'source': 'character_card',
        'protected': True,
        'reinforcement': 0.0, 'disputation': 0.0,
        'rein_last_signal_at': None, 'disp_last_signal_at': None,
        'sub_zero_days': 0, 'sub_zero_last_increment_date': None,
        'user_fact_reinforce_count': 0,
        'merged_from_ids': [],
        'importance': 0,
        'suppress': False, 'suppressed_at': None,
        'recent_mentions': [],
    }
    persona = {'master': {'facts': [card_entry]}}

    # Warm the cache.
    PersonaManager._get_cached_token_count(card_entry)
    assert card_entry['token_count'] is not None
    warmed_fp = card_entry['token_count_text_sha256']
    assert warmed_fp == _sha('nickname: 原始称呼')

    # Drive the sync with a changed card value → in-place text rewrite.
    changed = pm._apply_character_card_sync(
        '小天', persona,
        master_basic_config={field_name: '新的称呼改动比较大的样例文本'},
        lanlan_basic_config={},
    )
    assert changed is True
    # The mutation happened in place …
    updated = persona['master']['facts'][0]
    assert updated is card_entry
    assert updated['text'] == 'nickname: 新的称呼改动比较大的样例文本'
    # … and the invalidator ran, so the render path won't serve the
    # stale tiktoken count for new text.
    assert updated['token_count'] is None
    assert updated['token_count_text_sha256'] is None
    assert updated['token_count_tokenizer'] is None


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
    from utils.tokenize import tokenizer_identity
    reloaded = await pm.aget_persona('小天')
    reloaded_entries = reloaded['master']['facts']
    assert len(reloaded_entries) == 2
    for e in reloaded_entries:
        assert isinstance(e['token_count'], int) and e['token_count'] > 0
        assert e['token_count_text_sha256'] == _sha(e['text'])
        assert e['token_count_tokenizer'] == tokenizer_identity()

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
async def test_reflection_render_does_not_pollute_cache_fields(tmp_path):
    """Reflections have no `_personas`-style in-memory view — each render
    reads fresh from disk — so the render path MUST NOT write
    `token_count*` fields onto reflection dicts. If it did, the fields
    would ride along on the next `asave_reflections`, making a useless
    cache look authoritative on disk while every render still tokenizes.

    This test locks in the `cache_writeback=False` contract at the
    reflection call site in `arender_persona_markdown`."""
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
            'id': 'r1', 'text': 'reflection no-pollution contract',
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

    # Run the trim under the reflection-render contract.
    kept = await PersonaManager._ascore_trim_entries(
        reflections, budget=10_000, now=datetime.now(),
        cache_writeback=False,
    )
    assert len(kept) == 1
    # Trim still sorted/kept correctly — the count was computed — but
    # the fields must NOT be populated on the entry dict.
    assert 'token_count' not in reflections[0] or (
        reflections[0].get('token_count') is None
    )
    assert 'token_count_text_sha256' not in reflections[0] or (
        reflections[0].get('token_count_text_sha256') is None
    )
    assert 'token_count_tokenizer' not in reflections[0] or (
        reflections[0].get('token_count_tokenizer') is None
    )

    # Save + reload to confirm nothing pollution-y hit disk either.
    await re.asave_reflections('小天', reflections)
    reloaded = await re.aload_reflections('小天', include_archived=False)
    assert len(reloaded) == 1
    r = reloaded[0]
    assert r.get('token_count') is None
    assert r.get('token_count_text_sha256') is None
    assert r.get('token_count_tokenizer') is None


def test_normalize_entry_defaults_cache_fields_to_none():
    """`_normalize_entry` is the single source of truth for fact-entry
    defaults. The cache fields must default to None so first-render
    logic knows to recompute."""
    from memory.persona import PersonaManager

    d = PersonaManager._normalize_entry('plain string fact')
    assert d['token_count'] is None
    assert d['token_count_text_sha256'] is None
    assert d['token_count_tokenizer'] is None
    # Re-run on a dict that already has the field → idempotent; don't
    # clobber a populated cache.
    d['token_count'] = 42
    d['token_count_text_sha256'] = 'cafebabe' * 8
    d['token_count_tokenizer'] = 'tiktoken:o200k_base'
    d2 = PersonaManager._normalize_entry(d)
    assert d2['token_count'] == 42
    assert d2['token_count_text_sha256'] == 'cafebabe' * 8
    assert d2['token_count_tokenizer'] == 'tiktoken:o200k_base'


def test_normalize_reflection_does_not_default_cache_fields():
    """Regression guard: `_normalize_reflection` must NOT inject
    `token_count*` defaults. Reflections have no in-memory view
    (`_personas` has no `_reflections` twin), so caching on them would
    produce fields that look meaningful on disk but never serve a hit.

    If a future refactor ever adds a reflection-side in-memory cache,
    this test is the intentional signal that the schema can be extended
    — but it should be extended deliberately, not by default."""
    from memory.reflection import ReflectionEngine

    r = ReflectionEngine._normalize_reflection(
        {'id': 'r1', 'text': 'anything'}
    )
    assert 'token_count' not in r, (
        'reflection schema must not default token_count — see '
        'docstring for rationale'
    )
    assert 'token_count_text_sha256' not in r
    assert 'token_count_tokenizer' not in r
    # Idempotent: if a legacy reflection somehow already carries the
    # fields (e.g. from an older build of this PR that cached
    # reflections), normalize must preserve them byte-for-byte rather
    # than dropping data — even though we no longer write them.
    legacy = {
        'id': 'r2', 'text': 'legacy has fields',
        'token_count': 7,
        'token_count_text_sha256': 'f' * 64,
        'token_count_tokenizer': 'tiktoken:o200k_base',
    }
    normalized = ReflectionEngine._normalize_reflection(legacy)
    assert normalized['token_count'] == 7
    assert normalized['token_count_text_sha256'] == 'f' * 64
    assert normalized['token_count_tokenizer'] == 'tiktoken:o200k_base'


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
    assert disk_entry['token_count_tokenizer'] == e['token_count_tokenizer']

    # Re-run normalize on the disk copy — must not raise and must not
    # wipe the populated cache.
    normalized = PersonaManager._normalize_entry(disk_entry)
    assert normalized['token_count'] == e['token_count']
    assert normalized['token_count_text_sha256'] == (
        e['token_count_text_sha256']
    )
    assert normalized['token_count_tokenizer'] == (
        e['token_count_tokenizer']
    )


# ── tokenizer-identity guards (PR #939 round-1 review) ─────────────
#
# Motivation: the text-sha256 alone isn't enough. `utils.tokenize`
# silently falls back from tiktoken to a heuristic counter when the
# tiktoken encoding file is missing (e.g. Nuitka/PyInstaller packaging
# without the `o200k_base.tiktoken` data file). Counts from the two
# counters differ by a factor of ~1.5-2×. If a cache was warmed under
# tiktoken and the next render is in heuristic-fallback mode (different
# environment, fresh binary rollout), serving the cached count would
# make the budget trim off by roughly that factor. The
# `token_count_tokenizer` fingerprint catches this transition.


def test_tokenizer_change_invalidates_cache_sync():
    """Populate the cache under one tokenizer identity, then monkey-
    patch `tokenizer_identity` (via the `memory.persona` import) to
    return a different string. The next render must recompute.
    """
    from memory.persona import PersonaManager

    e = _entry('m1', 'identity guard test text')
    first = PersonaManager._get_cached_token_count(e)
    assert first > 0
    stamped_tid = e['token_count_tokenizer']
    assert stamped_tid  # populated on write

    # Swap the tokenizer identity. The underlying counter
    # (`count_tokens`) is unchanged so the recomputed value matches
    # what we already have — but the cache write path must still fire
    # and re-stamp the entry with the new identity.
    new_tid = stamped_tid + '-rolled'
    with patch('memory.persona.tokenizer_identity', return_value=new_tid):
        count_calls = {'n': 0}

        def _counting_count_tokens(text, *a, **kw):
            count_calls['n'] += 1
            return first  # deterministic — just needs to be non-zero

        with patch(
            'memory.persona.count_tokens',
            side_effect=_counting_count_tokens,
        ):
            second = PersonaManager._get_cached_token_count(e)

    assert count_calls['n'] == 1, (
        'tokenizer identity mismatch must force a recompute; '
        f'got {count_calls["n"]} count_tokens calls'
    )
    assert second == first
    assert e['token_count_tokenizer'] == new_tid


def test_heuristic_fallback_cached_separately_from_tiktoken_sync():
    """Simulate the real failure mode: cache warmed under tiktoken,
    next render in heuristic-fallback mode. Counts differ; the cache
    must miss and the stored count must update to the heuristic value.
    """
    from memory.persona import PersonaManager

    tiktoken_value = 12345
    heuristic_value = 98765
    call_log: list[str] = []

    # ── First render: pretend we're on tiktoken ─────────────────────
    with patch(
        'memory.persona.tokenizer_identity',
        return_value='tiktoken:o200k_base',
    ), patch(
        'memory.persona.count_tokens',
        side_effect=lambda *a, **kw: (call_log.append('tt'), tiktoken_value)[1],
    ):
        e = _entry('m1', 'packaging transition demo text')
        first = PersonaManager._get_cached_token_count(e)

    assert first == tiktoken_value
    assert e['token_count'] == tiktoken_value
    assert e['token_count_tokenizer'] == 'tiktoken:o200k_base'
    assert call_log == ['tt']

    # ── Second render: packaging shipped without encoding file; we
    #    silently fell back to heuristic. Cache must miss.
    with patch(
        'memory.persona.tokenizer_identity',
        return_value='heuristic:v1',
    ), patch(
        'memory.persona.count_tokens',
        side_effect=lambda *a, **kw: (
            call_log.append('heur'), heuristic_value,
        )[1],
    ):
        second = PersonaManager._get_cached_token_count(e)

    assert second == heuristic_value, (
        'heuristic-fallback render must recompute, not serve the '
        'tiktoken-era cached count'
    )
    assert call_log == ['tt', 'heur']
    assert e['token_count'] == heuristic_value
    assert e['token_count_tokenizer'] == 'heuristic:v1'


@pytest.mark.asyncio
async def test_tokenizer_change_invalidates_cache_async():
    """Async twin of the sync identity-guard test — covers the render
    hot path (`_ascore_trim_entries` → `_aget_cached_token_count`)."""
    from memory.persona import PersonaManager

    entries = [_entry('m1', 'async identity guard entry', rein=3.0)]
    # Warm the cache.
    await PersonaManager._ascore_trim_entries(
        entries, budget=10_000, now=datetime.now(),
    )
    warmed_tid = entries[0]['token_count_tokenizer']
    assert warmed_tid

    # Swap identity → second render must call acount_tokens again.
    new_tid = warmed_tid + '-rolled'
    call_count = {'n': 0}
    from utils.tokenize import acount_tokens as real_acount

    async def _counting_acount(text, *a, **kw):
        call_count['n'] += 1
        return await real_acount(text, *a, **kw)

    with patch('memory.persona.tokenizer_identity', return_value=new_tid), \
         patch('memory.persona.acount_tokens', side_effect=_counting_acount):
        await PersonaManager._ascore_trim_entries(
            entries, budget=10_000, now=datetime.now(),
        )

    assert call_count['n'] == 1, (
        'tokenizer identity mismatch must force recompute on the '
        f'async render path; got {call_count["n"]} acount_tokens calls'
    )
    assert entries[0]['token_count_tokenizer'] == new_tid


def test_tokenizer_identity_helper_shape():
    """`tokenizer_identity` is the API the cache keys off — lock its
    output shape so future refactors don't silently break the cache."""
    from utils.tokenize import tokenizer_identity

    tid = tokenizer_identity()
    assert isinstance(tid, str) and tid
    # Must be one of the two bucketed namespaces.
    assert tid.startswith('tiktoken:') or tid.startswith('heuristic:'), (
        f'unexpected tokenizer identity shape: {tid!r}'
    )


# ── poisoned-cache defensive guards (PR #939 round-3 review) ───────
#
# Motivation: `int(entry['token_count'])` on the hit path trusts the
# value that came off disk. A hand-edited or storage-corrupted
# `persona.json` could plant a non-numeric string ("??"), a null, a
# boolean, or a negative number while the sha256 + tokenizer
# fingerprints still happen to match. In that case the bare
# `int(cached)` either raises (bombs the render) or returns
# meaningless garbage (blows the budget math). The cache path must
# validate the coerced value and fall back to recompute on anything
# that isn't a non-negative int.


def _sanity_fingerprints(entry: dict) -> None:
    """Helper: set fingerprints so the match check passes and only
    the `token_count` validation gate is exercised."""
    from utils.tokenize import tokenizer_identity
    entry['token_count_text_sha256'] = _sha(entry['text'])
    entry['token_count_tokenizer'] = tokenizer_identity()


def test_non_numeric_cached_count_triggers_recompute_sync():
    """A string like `"??"` in `token_count` (hand-edit corruption)
    must NOT bomb `int(...)` — it must be treated as a cache miss and
    the entry rewritten with a real count."""
    from memory.persona import PersonaManager

    e = _entry('m1', 'poisoned cache recovery sync')
    e['token_count'] = '??'
    _sanity_fingerprints(e)

    n = PersonaManager._get_cached_token_count(e)
    assert isinstance(n, int) and n > 0, (
        'non-numeric cached count must force recompute; got non-int result'
    )
    # Writeback replaced the garbage with a real int.
    assert e['token_count'] == n
    assert isinstance(e['token_count'], int)


@pytest.mark.asyncio
async def test_non_numeric_cached_count_triggers_recompute_async():
    """Async twin — the render hot path uses `_aget_cached_token_count`
    and must survive the same corruption without exploding."""
    from memory.persona import PersonaManager

    e = _entry('m1', 'poisoned cache recovery async')
    e['token_count'] = '??'
    _sanity_fingerprints(e)

    n = await PersonaManager._aget_cached_token_count(e)
    assert isinstance(n, int) and n > 0
    assert e['token_count'] == n
    assert isinstance(e['token_count'], int)


def test_negative_cached_count_triggers_recompute_sync():
    """A negative `token_count` (e.g. `-5`) is non-sensical — you can't
    have a negative number of tokens. Treat as corruption and recompute."""
    from memory.persona import PersonaManager

    e = _entry('m1', 'negative cache recovery sync')
    e['token_count'] = -5
    _sanity_fingerprints(e)

    n = PersonaManager._get_cached_token_count(e)
    assert n >= 0
    # Writeback replaced the negative with the real, non-negative value.
    assert e['token_count'] == n
    assert e['token_count'] >= 0


@pytest.mark.asyncio
async def test_negative_cached_count_triggers_recompute_async():
    """Async twin of the negative-value guard."""
    from memory.persona import PersonaManager

    e = _entry('m1', 'negative cache recovery async')
    e['token_count'] = -5
    _sanity_fingerprints(e)

    n = await PersonaManager._aget_cached_token_count(e)
    assert n >= 0
    assert e['token_count'] == n
    assert e['token_count'] >= 0


def test_null_cached_count_with_matching_fingerprints_triggers_recompute_sync():
    """`token_count=None` but fingerprints populated is a near-legacy
    shape (e.g. someone set the sha256/tokenizer fields manually but
    left the count NULL). The existing `cached is not None` check
    already handled this; this test locks it in explicitly as part of
    the broader poisoned-cache family so the three cases (None, non-
    numeric, negative) are covered symmetrically."""
    from memory.persona import PersonaManager

    e = _entry('m1', 'null cache recovery sync')
    e['token_count'] = None
    _sanity_fingerprints(e)

    n = PersonaManager._get_cached_token_count(e)
    assert isinstance(n, int) and n > 0
    assert e['token_count'] == n


@pytest.mark.asyncio
async def test_null_cached_count_with_matching_fingerprints_triggers_recompute_async():
    """Async symmetric twin of the null-value guard."""
    from memory.persona import PersonaManager

    e = _entry('m1', 'null cache recovery async')
    e['token_count'] = None
    _sanity_fingerprints(e)

    n = await PersonaManager._aget_cached_token_count(e)
    assert isinstance(n, int) and n > 0
    assert e['token_count'] == n


def test_boolean_cached_count_triggers_recompute_sync():
    """`bool` is an `int` subclass in Python, so a naive
    `isinstance(x, int)` would accept `True` (= 1) as a valid cached
    count. That's almost never what we want — it's storage corruption,
    not a legitimate value. The `_coerce_cached_count` helper rejects
    booleans explicitly."""
    from memory.persona import PersonaManager

    e = _entry('m1', 'boolean cache poison sync')
    e['token_count'] = True  # type: ignore[assignment]
    _sanity_fingerprints(e)

    n = PersonaManager._get_cached_token_count(e)
    assert isinstance(n, int) and not isinstance(n, bool)
    # Writeback coerced through `int(n)` — we're stricter than `True==1`.
    assert e['token_count'] == n
    assert e['token_count'] is not True
    assert e['token_count'] is not False


def test_coerce_cached_count_helper_shape():
    """Direct unit coverage for `_coerce_cached_count`. Locks in the
    exact coercion contract so future refactors don't silently loosen
    it (e.g. accepting floats that truncate to negatives)."""
    from memory.persona import PersonaManager as PM

    # Accept: non-negative ints, int-parseable strings, and int-valued floats
    # (e.g. 42.0 — json.loads of "42.0" produces a float). `bool` subclass
    # check MUST run before `float` check so True/False don't slip through.
    assert PM._coerce_cached_count(0) == 0
    assert PM._coerce_cached_count(7) == 7
    assert PM._coerce_cached_count('42') == 42
    assert PM._coerce_cached_count(42.0) == 42

    # Reject: None, booleans, non-numeric strings, negative, junk types,
    # non-integer floats (1.9 → would silently truncate to 1 under int()),
    # and float infinities / NaN (int(inf) raises OverflowError).
    import math
    assert PM._coerce_cached_count(None) is None
    assert PM._coerce_cached_count(True) is None
    assert PM._coerce_cached_count(False) is None
    assert PM._coerce_cached_count('??') is None
    assert PM._coerce_cached_count('') is None
    assert PM._coerce_cached_count(-5) is None
    assert PM._coerce_cached_count(-3.0) is None
    assert PM._coerce_cached_count(1.9) is None
    assert PM._coerce_cached_count(float('inf')) is None
    assert PM._coerce_cached_count(float('nan')) is None
    assert math.isnan(float('nan'))  # sanity — nan.is_integer() is False
    assert PM._coerce_cached_count('-3') is None
    assert PM._coerce_cached_count([1, 2, 3]) is None  # type: ignore[arg-type]
    assert PM._coerce_cached_count({'nope': 1}) is None  # type: ignore[arg-type]
