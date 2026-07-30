"""Guards for the L10 core-memory render budget.

- ``_score_trim_entries`` skips an entry it cannot afford instead of
  treating it as a stop sign; one over-long top-ranked entry used to make
  the whole persona / reflection section vanish.
- A scoped (group) render gives every subject its own persona and
  reflection budget, bounded overall by ``SCOPED_RENDER_TOTAL_MAX_TOKENS``,
  with a slice reserved for a group subject that is queued behind members.
  Allocation follows the caller's subject order, never one invented here.
- The legacy (private / main-app) path keeps its single shared pool.
- ``protected`` and ``suppressed`` entries stay exempt from the token
  budget but are capped by count, loudly.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.tokenize import count_tokens


def _entry(eid: str, text: str, *, rein: float = 0.0, importance: int = 0,
           protected: bool = False, suppress: bool = False,
           subject=None) -> dict:
    entry = {
        'id': eid, 'text': text,
        'reinforcement': rein, 'disputation': 0.0,
        'rein_last_signal_at': None, 'disp_last_signal_at': None,
        'sub_zero_days': 0, 'user_fact_reinforce_count': 0,
        'merged_from_ids': [],
        'importance': importance,
        'protected': protected,
        'suppress': suppress, 'suppressed_at': None,
        'recent_mentions': [],
        'source': 'manual', 'source_id': None,
    }
    if subject is not None:
        entry.update(subject.as_entry_fields())
    return entry


def _reflection(rid: str, text: str, *, rein: float = 0.0, subject=None) -> dict:
    entry = {
        'id': rid, 'text': text, 'entity': 'master', 'status': 'confirmed',
        'reinforcement': rein, 'disputation': 0.0,
        'rein_last_signal_at': None, 'disp_last_signal_at': None,
        'sub_zero_days': 0, 'user_fact_reinforce_count': 0,
        'temporal_scope': 'pattern',
        'created_at': datetime.now().isoformat(),
    }
    if subject is not None:
        entry.update(subject.as_entry_fields())
    return entry


def _scoped_section(subject, facts: list[dict]) -> dict:
    return {**subject.as_entry_fields(), 'facts': facts}


class _RenderHarness:
    """Runs the real RenderingMixin, stubbing only persona/config IO."""

    def __init__(self, persona: dict):
        from memory.persona.mentions import MentionsMixin
        from memory.persona.rendering import RenderingMixin

        self.__class__ = type(
            "_Harness", (_RenderHarness, RenderingMixin, MentionsMixin), {},
        )
        self._persona = persona
        character_data = ("主人", "小天", {}, {}, {"human": "主人"}, {}, {}, {}, {})
        self._config_manager = SimpleNamespace(
            get_character_data=lambda: character_data,
            aget_character_data=AsyncMock(return_value=character_data),
        )

    def ensure_persona(self, name):
        return self._persona

    async def aensure_persona(self, name):
        return self._persona

    def update_suppressions(self, name):
        return None

    async def aupdate_suppressions(self, name):
        return None


def _group_and_member():
    from memory.scopes import MemorySubject

    return (
        MemorySubject.group_chat("qq", "7788"),
        MemorySubject.group_participant("qq", "7788", "2046"),
    )


# ── the break cliff: an unaffordable entry is skipped, not fatal ──────


def test_score_trim_skips_oversized_entry_instead_of_stopping():
    """A rank-1 entry bigger than the whole budget used to `break` the
    loop, so `kept` came back empty and the section disappeared outright —
    not a shortened persona, an absent one. The affordable lower-ranked
    entries were right there behind it.
    """
    from memory.persona.rendering import RenderingMixin

    now = datetime.now()
    huge = _entry('big', '这是一条被合并出来的超长记忆条目' * 40, rein=9.0)
    small_a = _entry('a', '主人喜欢辣条', rein=5.0)
    small_b = _entry('b', '主人怕冷', rein=4.0)
    budget = count_tokens(small_a['text']) + count_tokens(small_b['text'])
    assert count_tokens(huge['text']) > budget, "夹具失效：超长条目并未超预算"

    kept, used = RenderingMixin._score_trim_entries(
        [huge, small_a, small_b], budget, now,
    )

    assert [e['id'] for e in kept] == ['a', 'b'], (
        "排第一的条目放不下时应跳过它继续往下取，而不是终止整轮"
    )
    assert used <= budget


@pytest.mark.asyncio
async def test_ascore_trim_skips_oversized_entry_instead_of_stopping():
    """Async twin — the sync-only fix is the classic miss in this repo."""
    from memory.persona.rendering import RenderingMixin

    now = datetime.now()
    huge = _entry('big', '这是一条被合并出来的超长记忆条目' * 40, rein=9.0)
    small_a = _entry('a', '主人喜欢辣条', rein=5.0)
    small_b = _entry('b', '主人怕冷', rein=4.0)
    budget = count_tokens(small_a['text']) + count_tokens(small_b['text'])

    kept, used = await RenderingMixin._ascore_trim_entries(
        [huge, small_a, small_b], budget, now,
    )

    assert [e['id'] for e in kept] == ['a', 'b']
    assert used <= budget


@pytest.mark.asyncio
async def test_render_keeps_persona_section_despite_oversized_top_entry():
    """The call-site version of the same defect: one giant entry at the
    top of the persona pool used to empty the rendered section."""
    persona = {
        'master': {'facts': [
            _entry('big', '一段很长的合并结果' * 60, rein=9.0),
            _entry('a', '主人喜欢辣条', rein=5.0),
            _entry('b', '主人怕冷', rein=4.0),
        ]},
    }
    harness = _RenderHarness(persona)
    budget = count_tokens('主人喜欢辣条') + count_tokens('主人怕冷')

    with patch('memory.persona.rendering.PERSONA_RENDER_MAX_TOKENS', budget):
        rendered = await harness.arender_persona_markdown('小天')

    assert '主人喜欢辣条' in rendered
    assert '主人怕冷' in rendered


# ── per-subject budgets under one overall gate ───────────────────────


@pytest.mark.asyncio
async def test_each_subject_gets_its_own_persona_budget():
    """Group and member subjects used to fight over one 2000-token pool,
    so a talkative member could starve the group's own persona (or the
    other way round) purely by sort order."""
    group, member = _group_and_member()
    group_facts = [
        _entry('g1', '群规是不许剧透', rein=9.0, subject=group),
        _entry('g2', '群里在筹划露营', rein=8.0, subject=group),
    ]
    member_facts = [
        _entry('m1', '阿离在准备考试', rein=3.0, subject=member),
        _entry('m2', '阿离养了一只橘猫', rein=2.0, subject=member),
    ]
    persona = {
        group.persona_section_key: _scoped_section(group, group_facts),
        member.persona_section_key: _scoped_section(member, member_facts),
    }
    harness = _RenderHarness(persona)
    # Exactly what the group's two entries cost: under one shared pool the
    # member's entries (lower score) get nothing at all.
    shared_pool = sum(count_tokens(e['text']) for e in group_facts)

    with patch('memory.persona.rendering.PERSONA_RENDER_MAX_TOKENS', shared_pool):
        rendered = await harness.arender_persona_markdown(
            '小天', subjects=[group, member], include_legacy_private=False,
        )

    for text in ('群规是不许剧透', '群里在筹划露营',
                 '阿离在准备考试', '阿离养了一只橘猫'):
        assert text in rendered, f"{text} 被另一个 subject 抢走了预算"


@pytest.mark.asyncio
async def test_each_subject_gets_its_own_reflection_budget():
    """Same split for reflections — they have their own per-subject pool,
    not a share of the persona one."""
    group, member = _group_and_member()
    persona = {
        group.persona_section_key: _scoped_section(group, []),
        member.persona_section_key: _scoped_section(member, []),
    }
    group_reflections = [
        _reflection('rg1', '小天觉得这个群很热闹', rein=9.0, subject=group),
        _reflection('rg2', '小天觉得群里爱聊吃的', rein=8.0, subject=group),
    ]
    member_reflections = [
        _reflection('rm1', '小天觉得阿离最近很忙', rein=3.0, subject=member),
    ]
    harness = _RenderHarness(persona)
    shared_pool = sum(count_tokens(r['text']) for r in group_reflections)

    with patch('memory.persona.rendering.REFLECTION_RENDER_MAX_TOKENS', shared_pool):
        rendered = await harness.arender_persona_markdown(
            '小天', None, group_reflections + member_reflections,
            subjects=[group, member], include_legacy_private=False,
        )

    assert '小天觉得这个群很热闹' in rendered
    assert '小天觉得阿离最近很忙' in rendered


@pytest.mark.asyncio
async def test_total_gate_drops_a_trailing_subject_whole():
    """When the overall gate runs out, the remaining subject renders
    nothing — a two-line persona reads to the model as that person's
    complete profile, which is worse than an honest absence."""
    from memory.scopes import MemorySubject

    subjects = [
        MemorySubject.group_participant("qq", "7788", str(2000 + i))
        for i in range(3)
    ]
    facts = {
        s.subject_id: [
            _entry(f'p{i}a', f'成员{i}最近在学做菜和爬山还在写小说', rein=5.0, subject=s),
            _entry(f'p{i}b', f'成员{i}周末常去看展顺便逛书店', rein=4.0, subject=s),
        ]
        for i, s in enumerate(subjects)
    }
    # The last subject's entries are deliberately TINY: a budget check that
    # only asked "is anything left?" would happily emit them.
    facts[subjects[2].subject_id] = [
        _entry('p2a', '短', rein=5.0, subject=subjects[2]),
        _entry('p2b', '也短', rein=4.0, subject=subjects[2]),
    ]
    persona = {
        s.persona_section_key: _scoped_section(s, facts[s.subject_id])
        for s in subjects
    }
    harness = _RenderHarness(persona)

    from config import SCOPED_RENDER_SUBJECT_MIN_TOKENS

    spent = sum(
        count_tokens(e['text'])
        for s in subjects[:2] for e in facts[s.subject_id]
    )
    total = spent + SCOPED_RENDER_SUBJECT_MIN_TOKENS - 1

    with patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', total):
        rendered = await harness.arender_persona_markdown(
            '小天', subjects=subjects, include_legacy_private=False,
        )

    assert '成员0最近在学做菜和爬山还在写小说' in rendered
    assert '成员1周末常去看展顺便逛书店' in rendered
    assert '短' not in rendered, (
        "总闸剩量低于单 subject 下限时该整段跳过，而不是塞进能放下的碎片"
    )


@pytest.mark.asyncio
async def test_group_subject_keeps_a_reserve_against_earlier_members():
    """A group listed behind members must not be eaten by them. The group's
    persona is the context every participant shares, so it is the worst
    thing to lose to whoever happened to be enumerated first."""
    group, member = _group_and_member()
    bulk = '阿离说过的一大段话要占掉相当多的预算才行' * 3
    # One big entry plus a long tail of 1-token crumbs. The crumbs are the
    # point: without a reserve the member mops up every last token that the
    # big entry leaves behind, and the group gets nothing.
    member_facts = [_entry('m-bulk', bulk, rein=9.0, subject=member)] + [
        _entry(f'm{i}', 'x', rein=8.0 - i * 0.01, subject=member)
        for i in range(60)
    ]
    group_facts = [_entry('g1', '群规是不许剧透', rein=1.0, subject=group)]
    persona = {
        member.persona_section_key: _scoped_section(member, member_facts),
        group.persona_section_key: _scoped_section(group, group_facts),
    }
    harness = _RenderHarness(persona)

    reserve = count_tokens(group_facts[0]['text'])
    total = reserve + count_tokens(bulk)
    crumbs = sum(count_tokens(e['text']) for e in member_facts[1:])
    assert crumbs >= reserve, "夹具失效：碎屑不够多，吃不光保底额度"

    with patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', total), \
            patch('memory.persona.rendering.SCOPED_RENDER_GROUP_RESERVED_TOKENS', reserve), \
            patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS', 1):
        rendered = await harness.arender_persona_markdown(
            '小天', subjects=[member, group], include_legacy_private=False,
        )

    assert '群规是不许剧透' in rendered, "群 subject 的保底额度被前面的成员吃光了"
    assert bulk in rendered, "保底额度不该把前面成员的正常配额也扣没"
    assert '- x' not in rendered, (
        "成员只该拿到 total - reserve；碎屑挤进来说明保底额度没起作用"
    )


@pytest.mark.asyncio
async def test_allocation_follows_the_caller_subject_order():
    """Order is the caller's call. It sends [group, current speaker] today
    and grows to [group, speaker, three recent speakers] next; ranking the
    subjects here would silently override the only layer that knows who
    matters this turn."""
    from memory.scopes import MemorySubject

    first = MemorySubject.group_participant("qq", "7788", "2046")
    second = MemorySubject.group_participant("qq", "7788", "3057")
    persona = {
        first.persona_section_key: _scoped_section(first, [
            _entry('a', '阿离在准备考试而且最近睡得很晚', rein=1.0, subject=first),
        ]),
        second.persona_section_key: _scoped_section(second, [
            _entry('b', '小北在学吉他而且刚买了新琴弦', rein=9.0, subject=second),
        ]),
    }
    # Funds whichever subject comes first and nothing after it, regardless
    # of which of the two that is.
    total = max(
        count_tokens('阿离在准备考试而且最近睡得很晚'),
        count_tokens('小北在学吉他而且刚买了新琴弦'),
    )

    async def _render(order):
        with patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', total), \
                patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS', 1):
            return await _RenderHarness(persona).arender_persona_markdown(
                '小天', subjects=order, include_legacy_private=False,
            )

    first_wins = await _render([first, second])
    second_wins = await _render([second, first])

    assert '阿离在准备考试而且最近睡得很晚' in first_wins
    assert '小北在学吉他而且刚买了新琴弦' not in first_wins
    # Reversed order flips the winner even though `second` scores higher —
    # proof the allocator is not sorting by score, key or anything else.
    assert '小北在学吉他而且刚买了新琴弦' in second_wins
    assert '阿离在准备考试而且最近睡得很晚' not in second_wins


@pytest.mark.asyncio
async def test_legacy_render_still_uses_one_shared_pool():
    """No subjects means private chat / the main app: unchanged behaviour,
    one pool shared by every entity section. Per-entity budgets here would
    quietly multiply what the desktop app puts in its system prompt."""
    persona = {
        'master': {'facts': [
            _entry('m1', '主人喜欢辣条和麻辣烫还有火锅', rein=9.0),
            _entry('m2', '主人怕冷所以冬天不出门', rein=8.0),
        ]},
        'neko': {'facts': [
            _entry('n1', '小天喜欢晒太阳还爱打盹', rein=7.0),
            _entry('n2', '小天讨厌洗澡也讨厌吹风机', rein=6.0),
        ]},
    }
    harness = _RenderHarness(persona)
    pool = (count_tokens('主人喜欢辣条和麻辣烫还有火锅')
            + count_tokens('主人怕冷所以冬天不出门'))

    with patch('memory.persona.rendering.PERSONA_RENDER_MAX_TOKENS', pool):
        rendered = await harness.arender_persona_markdown('小天')

    assert '主人喜欢辣条和麻辣烫还有火锅' in rendered
    assert '主人怕冷所以冬天不出门' in rendered
    assert '小天喜欢晒太阳还爱打盹' not in rendered, (
        "legacy 路径必须保持单池；按 entity 分池会让主程序 prompt 成倍膨胀"
    )
    assert '小天讨厌洗澡也讨厌吹风机' not in rendered


@pytest.mark.asyncio
async def test_legacy_render_ignores_the_scoped_total_gate():
    """The overall scoped gate must not reach the legacy pool: the two
    have different sizes and the private corpus predates subjects."""
    persona = {
        'master': {'facts': [
            _entry('m1', '主人喜欢辣条', rein=9.0),
        ]},
    }
    harness = _RenderHarness(persona)

    with patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', 0):
        rendered = await harness.arender_persona_markdown('小天')

    assert '主人喜欢辣条' in rendered


@pytest.mark.asyncio
async def test_sync_and_async_scoped_renders_agree():
    """The two render paths differ only in how they count tokens. This is
    the behavioural version of "fix both twins" — it stays true however
    the budget code is later restructured."""
    group, member = _group_and_member()
    persona = {
        group.persona_section_key: _scoped_section(group, [
            _entry('g1', '群规是不许剧透', rein=9.0, subject=group),
            _entry('g2', '群里在筹划露营', rein=8.0, subject=group),
        ]),
        member.persona_section_key: _scoped_section(member, [
            _entry('m1', '阿离在准备考试', rein=3.0, subject=member),
            _entry('m2', '阿离养了一只橘猫', rein=2.0, subject=member),
        ]),
    }
    reflections = [
        _reflection('rg1', '小天觉得这个群很热闹', rein=5.0, subject=group),
        _reflection('rm1', '小天觉得阿离最近很忙', rein=4.0, subject=member),
    ]

    with patch('memory.persona.rendering.PERSONA_RENDER_MAX_TOKENS', 12), \
            patch('memory.persona.rendering.REFLECTION_RENDER_MAX_TOKENS', 10), \
            patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS', 1), \
            patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', 40):
        sync_out = _RenderHarness(persona).render_persona_markdown(
            '小天', None, reflections,
            subjects=[group, member], include_legacy_private=False,
        )
        async_out = await _RenderHarness(persona).arender_persona_markdown(
            '小天', None, reflections,
            subjects=[group, member], include_legacy_private=False,
        )

    assert sync_out == async_out
    assert sync_out, "夹具失效：预算太紧，两边都渲染出空串就证明不了什么"


@pytest.mark.asyncio
async def test_scoped_render_keeps_legacy_rows_when_the_caller_opts_in():
    """`subjects` plus `include_legacy_private=True` is legal input. The
    per-subject allocator must give those rows a slot instead of filtering
    them into the view and then dropping them on the floor."""
    group, _member = _group_and_member()
    persona = {
        group.persona_section_key: _scoped_section(group, [
            _entry('g1', '群规是不许剧透', rein=9.0, subject=group),
        ]),
        'master': {'facts': [_entry('m1', '主人喜欢辣条', rein=8.0)]},
    }
    harness = _RenderHarness(persona)

    rendered = await harness.arender_persona_markdown(
        '小天', subjects=[group], include_legacy_private=True,
    )

    assert '群规是不许剧透' in rendered
    assert '主人喜欢辣条' in rendered


# ── protected / suppressed: privileged, but not unbounded ────────────


@pytest.mark.asyncio
async def test_protected_entries_are_capped_by_count_with_a_warning():
    """Protected entries stay out of the token budget on purpose (cutting
    a character-card line is a personality break). Being exempt from the
    budget must not mean unbounded — a bulk card import would otherwise
    own the whole system prompt."""
    persona = {
        'master': {'facts': [
            _entry(f'card{i}', f'角色卡第{i}条设定', protected=True)
            for i in range(5)
        ]},
    }
    harness = _RenderHarness(persona)
    logger = MagicMock()

    with patch('memory.persona.rendering.PERSONA_RENDER_PROTECTED_MAX_ENTRIES', 2), \
            patch('memory.persona.rendering.logger', logger):
        rendered = await harness.arender_persona_markdown('小天')

    assert '角色卡第0条设定' in rendered
    assert '角色卡第1条设定' in rendered
    assert '角色卡第2条设定' not in rendered
    assert '角色卡第4条设定' not in rendered
    assert [c for c in logger.warning.call_args_list if 'protected' in str(c)], (
        "超过 protected 条数上限必须留下 warning，否则膨胀是静默的"
    )


@pytest.mark.asyncio
async def test_suppressed_entries_are_capped_by_count_with_a_warning():
    """Same rule for the "remembers but won't volunteer it" section."""
    persona = {
        'master': {'facts': [
            _entry(f's{i}', f'不要主动提第{i}件事', suppress=True)
            for i in range(5)
        ]},
    }
    harness = _RenderHarness(persona)
    logger = MagicMock()

    with patch('memory.persona.rendering.PERSONA_RENDER_SUPPRESSED_MAX_ENTRIES', 2), \
            patch('memory.persona.rendering.logger', logger):
        rendered = await harness.arender_persona_markdown('小天')

    assert '不要主动提第0件事' in rendered
    assert '不要主动提第1件事' in rendered
    assert '不要主动提第2件事' not in rendered
    assert [c for c in logger.warning.call_args_list if 'suppressed' in str(c)], (
        "超过 suppressed 条数上限必须留下 warning"
    )


@pytest.mark.asyncio
async def test_protected_entries_still_bypass_the_token_budget():
    """The count cap must not turn into a token cap by accident — the
    exemption is the whole reason the split exists."""
    persona = {
        'master': {'facts': [
            _entry('card', '主人是一只猫娘的主人' * 30, protected=True),
        ]},
    }
    harness = _RenderHarness(persona)

    with patch('memory.persona.rendering.PERSONA_RENDER_MAX_TOKENS', 1):
        rendered = await harness.arender_persona_markdown('小天')

    assert '主人是一只猫娘的主人' in rendered
