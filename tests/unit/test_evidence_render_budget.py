# -*- coding: utf-8 -*-
"""
Unit tests for the P-D render budget pipeline (memory-evidence-rfc §3.6).

Covers:
  - utils.tokenize: tiktoken happy path + heuristic fallback (one-shot warn)
  - PersonaManager._score_trim_entries / _ascore_trim_entries:
      * preserves protected entries regardless of budget (S12)
      * sorts by (evidence_score, importance) DESC
      * stops at first overflow (kept text token sum ≤ budget)
  - 3-phase render: persona budget independent from reflection budget (S11)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── utils.tokenize ──────────────────────────────────────────────────


def test_count_tokens_uses_tiktoken_for_chinese():
    from utils.tokenize import _reset_fallback_warned_for_tests, count_tokens

    _reset_fallback_warned_for_tests()
    n = count_tokens("测试")
    assert n > 0, "tiktoken should produce non-zero tokens for non-empty text"
    # Empty string short-circuits to 0 — independent of encoder.
    assert count_tokens("") == 0


@pytest.mark.asyncio
async def test_acount_tokens_runs_in_thread():
    from utils.tokenize import _reset_fallback_warned_for_tests, acount_tokens

    _reset_fallback_warned_for_tests()
    n = await acount_tokens("hello world")
    assert n > 0


def test_heuristic_fallback_warns_once(caplog, monkeypatch):
    """RFC §3.6.6: when tiktoken can't load the encoding, we log a warning
    EXACTLY ONCE per process and then silently fall back to the heuristic
    counter on every subsequent call."""
    import utils.tokenize as tok

    tok._reset_fallback_warned_for_tests()

    # Force tiktoken.get_encoding to raise so _get_encoder hits the
    # heuristic path on every call.
    def _broken_get_encoding(*_args, **_kwargs):
        raise RuntimeError("encoding file missing — packaging bug simulation")

    fake_tiktoken = MagicMock()
    fake_tiktoken.get_encoding.side_effect = _broken_get_encoding
    monkeypatch.setitem(__import__('sys').modules, 'tiktoken', fake_tiktoken)

    with caplog.at_level(logging.WARNING, logger='utils.tokenize'):
        n1 = tok.count_tokens("测试 hello")
        n2 = tok.count_tokens("again 测试")
        n3 = tok.count_tokens("more 中文 tokens")

    assert n1 > 0 and n2 > 0 and n3 > 0
    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and 'tiktoken' in r.getMessage()
    ]
    assert len(warnings) == 1, (
        f"expected exactly one fallback warning, got {len(warnings)}"
    )

    # Reset for any downstream test in the same process.
    tok._reset_fallback_warned_for_tests()


# ── PersonaManager._score_trim_entries ─────────────────────────────


def _persona_manager():
    """Build a PersonaManager isolated from disk + config."""
    from memory.persona import PersonaManager

    cm = MagicMock()
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人"}, {}, {}, {}, {},
    ))
    with patch("memory.persona.get_config_manager", return_value=cm):
        pm = PersonaManager()
    pm._config_manager = cm
    return pm


def _entry(eid: str, text: str, *, rein: float = 0.0, disp: float = 0.0,
           importance: int = 0, protected: bool = False) -> dict:
    return {
        'id': eid, 'text': text,
        'reinforcement': rein, 'disputation': disp,
        'rein_last_signal_at': None, 'disp_last_signal_at': None,
        'sub_zero_days': 0, 'user_fact_reinforce_count': 0,
        'merged_from_ids': [],
        'importance': importance,
        'protected': protected,
        'suppress': False, 'suppressed_at': None,
        'recent_mentions': [],
        'source': 'manual', 'source_id': None,
    }


def test_score_trim_breaks_at_budget():
    """Sorted by (score, importance) DESC, stop at first entry whose
    cumulative tokens would exceed budget. Lower-score entries are
    silently dropped — they don't sneak in via fallback."""
    pm = _persona_manager()
    now = datetime.now()
    entries = [
        _entry('e1', 'A' * 40, rein=3.0),  # highest score
        _entry('e2', 'B' * 40, rein=2.0),
        _entry('e3', 'C' * 40, rein=1.0),
        _entry('e4', 'D' * 40, rein=0.0),
    ]
    # tiny budget that fits ~1.5 entries given the all-latin text
    kept = pm._score_trim_entries(entries, budget=15, now=now)

    # At minimum the highest-score entry survives; nothing past the budget
    assert kept, "expected at least one entry under non-zero budget"
    assert kept[0]['id'] == 'e1', (
        "score-trim must keep highest-score entry first"
    )
    assert all(k['id'] != 'e4' for k in kept), (
        "lowest-score entry must be dropped under tight budget"
    )


def test_score_trim_importance_breaks_score_ties():
    pm = _persona_manager()
    now = datetime.now()
    entries = [
        _entry('a', 'short', rein=2.0, importance=1),
        _entry('b', 'short', rein=2.0, importance=9),  # higher importance
        _entry('c', 'short', rein=2.0, importance=5),
    ]
    kept = pm._score_trim_entries(entries, budget=10**6, now=now)
    # All fit; ordering must be by importance DESC inside the same score
    assert [k['id'] for k in kept] == ['b', 'c', 'a']


def test_score_trim_protected_inf_score_always_kept():
    """Protected entries get evidence_score=inf via memory.evidence —
    when tossed into score-trim with non-protected siblings they always
    win the sort and consume budget first. Phase 1 (split) keeps them
    out of trim entirely; this test validates the math contract that
    backs that split."""
    from memory.evidence import evidence_score

    now = datetime.now()
    p = _entry('p1', 'protected', rein=0.0, protected=True)
    n = _entry('n1', 'normal', rein=10.0)
    assert evidence_score(p, now) == float('inf')
    assert evidence_score(n, now) == 10.0


@pytest.mark.asyncio
async def test_split_excludes_protected_from_trim_pool(tmp_path):
    """Phase 1 (RFC §3.6.2): protected entries route to the always-render
    list and never compete for the non-protected score-trim budget."""
    pm = _persona_manager()
    persona = {
        'master': {
            'facts': [
                _entry('card_1', 'master loves cats', protected=True),
                _entry('m1', 'extra observation 1', rein=1.0),
                _entry('m2', 'extra observation 2', rein=2.0),
            ],
        },
    }
    protected, by_entity = pm._split_persona_for_render(persona)
    assert [(ek, e['id']) for ek, e in protected] == [('master', 'card_1')]
    assert {e['id'] for e in by_entity['master']} == {'m1', 'm2'}


@pytest.mark.asyncio
async def test_render_persona_independent_from_reflection_budget(tmp_path):
    """S11: persona overflow must not crowd reflection rendering, and
    vice versa — they have separate budgets."""
    from memory.persona import PersonaManager

    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人"}, {}, {}, {}, {},
    ))
    cm.get_config_value.return_value = False
    with patch("memory.persona.get_config_manager", return_value=cm):
        pm = PersonaManager()
    pm._config_manager = cm

    # Stuff persona with 50 long entries so it WILL hit the 2000-token
    # default budget; reflections have 3 short entries that easily fit
    # the 1000-token budget. Both should render content.
    persona = {
        'master': {'facts': [
            _entry(f'm{i}', '这是一条很长的描述' * 20, rein=float(50 - i))
            for i in range(50)
        ]},
    }
    pm._personas['小天'] = persona
    # Skip suppressions update + character_card sync by stubbing
    # `aensure_persona` to return our prepared dict.
    async def _aensure(name):
        return persona
    pm.aensure_persona = _aensure  # type: ignore[assignment]
    pm.aupdate_suppressions = AsyncMock()

    pending = [
        {'id': 'r1', 'text': '小天觉得主人最近很开心',
         'reinforcement': 1.0, 'disputation': 0.0,
         'rein_last_signal_at': None, 'disp_last_signal_at': None,
         'sub_zero_days': 0, 'user_fact_reinforce_count': 0},
    ]
    confirmed = [
        {'id': 'r2', 'text': '小天比较确定主人喜欢辣条',
         'reinforcement': 2.0, 'disputation': 0.0,
         'rein_last_signal_at': None, 'disp_last_signal_at': None,
         'sub_zero_days': 0, 'user_fact_reinforce_count': 0},
    ]

    md = await pm.arender_persona_markdown('小天',
                                            pending_reflections=pending,
                                            confirmed_reflections=confirmed)

    # Persona section is present with at least the highest-score entries
    assert '关于主人' in md
    # Reflections survived the persona overflow — both sections render
    assert '小天最近的印象' in md
    assert '小天比较确定的印象' in md
    assert '主人最近很开心' in md
    assert '主人喜欢辣条' in md


@pytest.mark.asyncio
async def test_render_protected_always_emitted_under_tight_budget(tmp_path):
    """S12: even with budget = 1 token (effectively zero non-protected
    capacity), protected entries always render."""
    from memory.persona import PersonaManager

    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人"}, {}, {}, {}, {},
    ))
    with patch("memory.persona.get_config_manager", return_value=cm):
        pm = PersonaManager()
    pm._config_manager = cm

    persona = {
        'master': {'facts':
            [_entry('card_1', '主人是一只猫娘的主人', protected=True)]
            + [_entry(f'm{i}', '一些不重要的观察' * 10, rein=1.0)
               for i in range(20)]
        },
    }
    pm._personas['小天'] = persona
    async def _aensure(name):
        return persona
    pm.aensure_persona = _aensure  # type: ignore[assignment]
    pm.aupdate_suppressions = AsyncMock()

    # Force the persona budget to 1 so non-protected entries cannot fit.
    with patch('memory.persona.PERSONA_RENDER_TOKEN_BUDGET', 1):
        md = await pm.arender_persona_markdown('小天')

    assert '主人是一只猫娘的主人' in md, (
        "protected entries must render even when budget is exhausted"
    )
