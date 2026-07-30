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

from contextlib import ExitStack
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


def _pool(*texts: str) -> int:
    """A scoped pool sized to hold exactly these entries.

    Scoped budgets are charged per rendered line, not per raw text, so a
    fixture that adds up bare ``count_tokens`` comes out one markup short
    per entry and silently drops the last one. Derived from the constant
    rather than written down, so changing it retunes every fixture here.
    """
    from config import SCOPED_RENDER_ENTRY_MARKUP_TOKENS as MARKUP

    return sum(count_tokens(t) + MARKUP for t in texts)


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
    other way round) purely by sort order.

    Two claims in one, and both matter: the second subject reaches its
    OWN pool (lower bound), and the first cannot spend past its pool
    (upper bound). Drop the ceiling and the original defect comes right
    back with the sign flipped — whoever is listed first eats the gate.
    """
    group, member = _group_and_member()
    group_facts = [
        _entry('g1', '群规是不许剧透', rein=9.0, subject=group),
        _entry('g2', '群里在筹划露营', rein=8.0, subject=group),
        # Well past the group's own pool: must not be funded out of the
        # gate's remainder just because the group is enumerated first.
        _entry('g3', '群里还聊过一堆别的事情要占掉很多预算', rein=7.0, subject=group),
        _entry('g4', '群里又聊过另外一堆事情同样很占预算', rein=6.0, subject=group),
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
    # Exactly what the group's first two entries cost: under one shared
    # pool the member's entries (lower score) get nothing at all.
    pool = _pool(*[e['text'] for e in group_facts[:2]])

    with patch('memory.persona.rendering.PERSONA_RENDER_MAX_TOKENS', pool):
        rendered = await harness.arender_persona_markdown(
            '小天', subjects=[group, member], include_legacy_private=False,
        )

    for text in ('群规是不许剧透', '群里在筹划露营',
                 '阿离在准备考试', '阿离养了一只橘猫'):
        assert text in rendered, f"{text} 被另一个 subject 抢走了预算"
    for text in ('群里还聊过一堆别的事情要占掉很多预算',
                 '群里又聊过另外一堆事情同样很占预算'):
        assert text not in rendered, (
            "排在第一位的 subject 越过了自己的 per-subject 上限，"
            "只剩总闸约束它——原缺陷换了个方向又回来了"
        )


@pytest.mark.asyncio
async def test_each_subject_gets_its_own_reflection_budget():
    """Same split for reflections — their own per-subject pool, with the
    same lower and upper bound as persona."""
    group, member = _group_and_member()
    persona = {
        group.persona_section_key: _scoped_section(group, []),
        member.persona_section_key: _scoped_section(member, []),
    }
    group_reflections = [
        _reflection('rg1', '小天觉得这个群很热闹', rein=9.0, subject=group),
        _reflection('rg2', '小天觉得群里爱聊吃的', rein=8.0, subject=group),
        _reflection('rg3', '小天觉得群里的人都特别爱开玩笑而且很热心',
                    rein=7.0, subject=group),
    ]
    member_reflections = [
        _reflection('rm1', '小天觉得阿离最近很忙', rein=3.0, subject=member),
    ]
    harness = _RenderHarness(persona)
    pool = _pool(*[r['text'] for r in group_reflections[:2]])

    with patch('memory.persona.rendering.REFLECTION_RENDER_MAX_TOKENS', pool):
        rendered = await harness.arender_persona_markdown(
            '小天', None, group_reflections + member_reflections,
            subjects=[group, member], include_legacy_private=False,
        )

    assert '小天觉得这个群很热闹' in rendered
    assert '小天觉得群里爱聊吃的' in rendered
    assert '小天觉得阿离最近很忙' in rendered
    assert '小天觉得群里的人都特别爱开玩笑而且很热心' not in rendered, (
        "第一个 subject 的 reflection 越过了自己的 per-subject 上限"
    )


@pytest.mark.asyncio
async def test_total_gate_drops_a_trailing_subject_whole():
    """When the overall gate runs out, the remaining subject renders
    nothing — a two-line persona reads to the model as that person's
    complete profile, which is worse than an honest absence.

    The earlier subjects carry reflections as well as facts, so the gate
    has to account for BOTH. Fact-only fixtures leave the reflection half
    of the accounting untested: drop `remaining -= reflection_used` and
    a fact-only test never notices.
    """
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
    reflections = [
        _reflection(f'r{i}', f'小天觉得成员{i}最近状态还不错也挺好聊的',
                    rein=5.0, subject=s)
        for i, s in enumerate(subjects[:2])
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
    ) + sum(count_tokens(r['text']) for r in reflections)
    total = spent + SCOPED_RENDER_SUBJECT_MIN_TOKENS - 1

    with patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', total):
        rendered = await harness.arender_persona_markdown(
            '小天', None, reflections,
            subjects=subjects, include_legacy_private=False,
        )

    assert '成员0最近在学做菜和爬山还在写小说' in rendered
    assert '成员1周末常去看展顺便逛书店' in rendered
    assert '小天觉得成员0最近状态还不错也挺好聊的' in rendered
    assert '小天觉得成员1最近状态还不错也挺好聊的' in rendered
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

    # Derived, not hand-tuned: the gate also charges per-entry markup, so
    # the reserve has to cover the group's line as rendered.
    reserve = _pool(group_facts[0]['text'])
    total = _pool(bulk) + reserve
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
async def test_reserve_covers_every_group_still_queued_not_just_one():
    """Two groups in one render need two reserves.

    A single flat reserve covers only the last group in the list: the
    earlier one deducts a slice for the later one, gets nothing back for
    itself, and is the subject that disappears — while every member ahead
    of it renders in full. That is exactly the outcome the reserve exists
    to prevent, so it has to scale with how many groups are still queued.
    """
    from memory.scopes import MemorySubject

    members = [
        MemorySubject.group_participant("qq", "7788", str(2000 + i))
        for i in range(2)
    ]
    group_a = MemorySubject.group_chat("qq", "7788")
    group_b = MemorySubject.group_chat("qq", "9900")
    order = members + [group_a, group_b]

    def _facts(subject, label):
        return [
            _entry(f'{label}-{i}', f'{label}说过的第{i}件事情要占掉不少预算才行',
                   rein=9.0 - i, subject=subject)
            for i in range(4)
        ]

    persona = {
        s.persona_section_key: _scoped_section(s, _facts(s, name))
        for s, name in zip(order, ('成员甲', '成员乙', '群A', '群B'))
    }
    harness = _RenderHarness(persona)
    # One entry's worth per pool, and one pool's worth per reserve: with a
    # FLAT reserve the two members then eat enough of the gate that the
    # second group falls off. A roomier gate hides the difference.
    per_subject = _pool('成员甲说过的第0件事情要占掉不少预算才行')

    with patch('memory.persona.rendering.PERSONA_RENDER_MAX_TOKENS', per_subject), \
            patch('memory.persona.rendering.SCOPED_RENDER_GROUP_RESERVED_TOKENS',
                  per_subject), \
            patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS', 1), \
            patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS',
                  per_subject * 3):
        rendered = await harness.arender_persona_markdown(
            '小天', subjects=order, include_legacy_private=False,
        )

    assert '群A说过的第0件事情要占掉不少预算才行' in rendered, (
        "排在前面的那个群替后面的群留了额度，把自己饿死了"
    )
    assert '群B说过的第0件事情要占掉不少预算才行' in rendered
    # The gate funds three subjects' worth; two reserves mean the members
    # split what is left, so at least one of them gives way — never a group.
    assert '成员甲说过的第0件事情要占掉不少预算才行' in rendered


@pytest.mark.asyncio
async def test_a_group_never_reserves_against_another_group():
    """Groups are peers; between peers the caller's order decides.

    The reserve exists to stop MEMBERS from eating a group queued behind
    them. Charging a group for the groups after it inverts what it
    protects: with five groups the first owes four reserves, comes out
    with nothing, and the request renders its LAST four subjects instead
    of its first four — caller order backwards.
    """
    from memory.scopes import MemorySubject

    groups = [MemorySubject.group_chat("qq", str(7000 + i)) for i in range(5)]
    persona = {
        s.persona_section_key: _scoped_section(s, [
            _entry(f'g{i}', f'群{i}的群规是不许剧透而且要按时报名', rein=5.0, subject=s),
        ])
        for i, s in enumerate(groups)
    }
    harness = _RenderHarness(persona)
    from config import SCOPED_RENDER_ENTRY_MARKUP_TOKENS as MARKUP

    per_group = count_tokens('群0的群规是不许剧透而且要按时报名')

    with patch('memory.persona.rendering.SCOPED_RENDER_GROUP_RESERVED_TOKENS',
               per_group), \
            patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS', 1), \
            patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS',
                  (per_group + MARKUP) * 3):
        rendered = await harness.arender_persona_markdown(
            '小天', subjects=groups, include_legacy_private=False,
        )

    for i in range(3):
        assert f'群{i}的群规是不许剧透而且要按时报名' in rendered, (
            f"群{i} 排在前面却被跳过了——保底额度把 caller order 倒了过来"
        )
    for i in (3, 4):
        assert f'群{i}的群规是不许剧透而且要按时报名' not in rendered, (
            "总闸打满时该丢队尾，不是队首"
        )


@pytest.mark.asyncio
async def test_an_empty_group_does_not_hold_a_reserve_nobody_can_spend():
    """A brand-new group has nothing to render. Reserving for it anyway
    costs the member ahead of it its whole slot for no benefit — and the
    slice then falls to whoever is listed AFTER the empty group, which
    inverts the caller order the allocator is supposed to honour.
    """
    from memory.scopes import MemorySubject

    early = MemorySubject.group_participant("qq", "7788", "2046")
    empty_group = MemorySubject.group_chat("qq", "7788")
    late = MemorySubject.group_participant("qq", "7788", "3057")
    persona = {
        early.persona_section_key: _scoped_section(early, [
            _entry('e1', '阿离在准备考试而且最近睡得很晚', rein=5.0, subject=early),
        ]),
        # The group section exists but holds nothing renderable.
        empty_group.persona_section_key: _scoped_section(empty_group, []),
        late.persona_section_key: _scoped_section(late, [
            _entry('l1', '小北在学吉他而且刚买了新琴弦', rein=4.0, subject=late),
        ]),
    }
    harness = _RenderHarness(persona)
    reserve = count_tokens('阿离在准备考试而且最近睡得很晚') * 4

    with patch('memory.persona.rendering.SCOPED_RENDER_GROUP_RESERVED_TOKENS',
               reserve), \
            patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS', 1), \
            patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS',
                  reserve):
        rendered = await harness.arender_persona_markdown(
            '小天', subjects=[early, empty_group, late],
            include_legacy_private=False,
        )

    assert '阿离在准备考试而且最近睡得很晚' in rendered, (
        "空群占住了没人能花的保底额度，把排在它前面的成员挤掉了"
    )


@pytest.mark.asyncio
async def test_a_skipped_subject_drops_its_budget_exempt_sections_too():
    """Dropping a subject has to take its protected and suppressed
    entries with it.

    Those never pass through the trim — they are exempt by design — so a
    subject the floor "dropped whole" would still emit its character-card
    lines and its do-not-mention list. That is precisely the partial
    profile `SCOPED_RENDER_SUBJECT_MIN_TOKENS` exists to prevent: the
    model reads two stray card lines as the whole person.
    """
    group, member = _group_and_member()
    persona = {
        group.persona_section_key: _scoped_section(group, [
            _entry('g1', '群规是不许剧透而且要按时报名参加活动', rein=9.0, subject=group),
        ]),
        member.persona_section_key: _scoped_section(member, [
            _entry('m-card', '阿离的角色卡设定', protected=True, subject=member),
            _entry('m-hush', '阿离不想被主动提起的事', suppress=True, subject=member),
            _entry('m1', '阿离在准备考试', rein=1.0, subject=member),
        ]),
    }
    harness = _RenderHarness(persona)
    logger = MagicMock()
    gate = _pool('群规是不许剧透而且要按时报名参加活动')

    with patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', gate), \
            patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS', 1), \
            patch('memory.persona.rendering.logger', logger):
        rendered = await harness.arender_persona_markdown(
            '小天', subjects=[group, member], include_legacy_private=False,
        )

    assert '群规是不许剧透而且要按时报名参加活动' in rendered
    assert [c for c in logger.warning.call_args_list if '整段跳过' in str(c)], (
        "夹具失效：member 根本没被跳过，这条用例什么都没测到"
    )
    assert '阿离在准备考试' not in rendered
    assert '阿离的角色卡设定' not in rendered, (
        "被整段跳过的 subject 的 protected 条目还是渲染出来了——正是这条"
        "下限要防的「半截人设」"
    )
    assert '阿离不想被主动提起的事' not in rendered, (
        "被整段跳过的 subject 的 suppressed 条目还是渲染出来了"
    )


@pytest.mark.asyncio
async def test_gate_charges_the_markup_composition_adds_to_every_entry():
    """The gate advertises a bound on the RENDERED block, and compose adds
    a ``- `` bullet plus a newline to each entry. With short facts that
    markup is most of the line: counting only entry text lets a workload
    that "fills" the gate emit a block well past it.
    """
    from memory.scopes import MemorySubject

    first = MemorySubject.group_participant("qq", "7788", "2046")
    second = MemorySubject.group_participant("qq", "7788", "3057")
    # One-token facts: text is a small fraction of the rendered line, so
    # text-only accounting reports the first subject as far cheaper than
    # the block it actually produced.
    persona = {
        first.persona_section_key: _scoped_section(first, [
            _entry(f'a{j}', 'x', rein=float(9 - j), subject=first)
            for j in range(40)
        ]),
        second.persona_section_key: _scoped_section(second, [
            _entry(f'b{j}', 'y', rein=float(9 - j), subject=second)
            for j in range(40)
        ]),
    }
    harness = _RenderHarness(persona)
    from config import SCOPED_RENDER_ENTRY_MARKUP_TOKENS as MARKUP

    gate = 40

    with patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', gate), \
            patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS', 1):
        rendered = await harness.arender_persona_markdown(
            '小天', subjects=[first, second], include_legacy_private=False,
        )

    bullets = rendered.count('- x') + rendered.count('- y')
    assert bullets, "夹具失效：一条都没渲染出来"
    # Every entry is one token of text, so once the bullet and its newline
    # are charged the gate can fund at most this many of them. Counting
    # text alone funds five times as many and emits a block far past the
    # cap the constant advertises.
    affordable = gate // (1 + MARKUP)
    assert bullets <= affordable, (
        f"总闸 {gate} tok 渲染了 {bullets} 条（含 markup 最多只装得下 "
        f"{affordable} 条）——markup 没计进预算"
    )


@pytest.mark.asyncio
async def test_the_gate_is_never_overspent_within_one_subject():
    """A subject's reflection pool cannot spend what its persona pool
    already used up.

    Selecting on text while charging markup afterwards left the two
    counters disagreeing: the gate could be exhausted (or negative) while
    the same subject went on funding reflections out of an ``available``
    that had only been debited the raw text.
    """
    group, member = _group_and_member()
    persona = {
        group.persona_section_key: _scoped_section(group, [
            _entry(f'g{j}', 'x', rein=float(40 - j), subject=group)
            for j in range(40)
        ]),
        member.persona_section_key: _scoped_section(member, []),
    }
    reflections = [
        _reflection('rg1', '小天觉得这个群很热闹', rein=5.0, subject=group),
        _reflection('rm1', '小天觉得阿离最近很忙', rein=4.0, subject=member),
    ]
    harness = _RenderHarness(persona)
    # Exactly enough for the persona side and nothing more.
    gate = _pool(*['x'] * 6)

    with patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', gate), \
            patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS', 1):
        rendered = await harness.arender_persona_markdown(
            '小天', None, reflections,
            subjects=[group, member], include_legacy_private=False,
        )

    assert '- x' in rendered, "夹具失效：persona 一条都没渲染出来"
    assert '小天觉得这个群很热闹' not in rendered, (
        "persona 已经吃光总闸，同一个 subject 的 reflection 还是拿到了额度"
    )
    assert '小天觉得阿离最近很忙' not in rendered


@pytest.mark.asyncio
async def test_a_group_holding_only_suppressed_facts_still_renders_them():
    """The floor drops fragments, not slots that cost nothing.

    A group whose facts are all suppressed has empty persona and
    reflection buckets, so it always falls under the minimum once earlier
    subjects have spent the gate. Dropping it there would take its
    do-not-mention list with it — and the character would start
    volunteering exactly what it was told to sit on. There is no fragment
    to avoid here: the slot has nothing budgeted, so nothing is being
    half-rendered.
    """
    group, member = _group_and_member()
    persona = {
        member.persona_section_key: _scoped_section(member, [
            _entry('m-big', '阿离说过的一件事情要占掉不少预算才行',
                   rein=9.0, subject=member),
        ] + [
            # Crumbs, so that without a reserve the member mops the gate
            # down past the floor and the group really does get dropped.
            _entry(f'm{j}', 'x', rein=8.0 - j * 0.01, subject=member)
            for j in range(20)
        ]),
        group.persona_section_key: _scoped_section(group, [
            _entry('g-hush', '群里不要主动提起的那件事', suppress=True,
                   subject=group),
        ]),
    }
    harness = _RenderHarness(persona)
    reserve = _pool('群里不要主动提起的那件事')
    total = reserve + _pool('阿离说过的一件事情要占掉不少预算才行')

    with patch('memory.persona.rendering.SCOPED_RENDER_GROUP_RESERVED_TOKENS',
               reserve), \
            patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS',
                  _pool('x')), \
            patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', total):
        rendered = await harness.arender_persona_markdown(
            '小天', subjects=[member, group], include_legacy_private=False,
        )

    assert '阿离说过的一件事情要占掉不少预算才行' in rendered, (
        "夹具失效：成员一条都没渲染出来"
    )
    assert '群里不要主动提起的那件事' in rendered, (
        "只有免预算内容的 subject 被下限当成「半截人设」丢掉了，"
        "「别主动提」清单跟着一起没了"
    )


@pytest.mark.asyncio
async def test_an_exempt_only_group_reserves_nothing_for_itself():
    """Rendering for free must not park capacity nobody spends.

    An exempt-only group debits the gate nothing, so a reserve held on its
    behalf is released untouched to whatever slot follows it. The member
    ahead pays for it and gets skipped; the member behind inherits it and
    renders — the caller's order, inverted across the group boundary.

    Both halves are asserted: the group's own exempt content still shows
    up (that is the round-5 property this must not regress), and the
    earlier member wins over the later one.
    """
    from memory.scopes import MemorySubject

    early = MemorySubject.group_participant("qq", "7788", "2046")
    exempt_group = MemorySubject.group_chat("qq", "7788")
    late = MemorySubject.group_participant("qq", "7788", "3057")
    persona = {
        early.persona_section_key: _scoped_section(early, [
            _entry('e1', '阿离在准备考试而且最近睡得很晚', rein=5.0, subject=early),
        ]),
        exempt_group.persona_section_key: _scoped_section(exempt_group, [
            _entry('g-hush', '群里不要主动提起的那件事', suppress=True,
                   subject=exempt_group),
        ]),
        late.persona_section_key: _scoped_section(late, [
            _entry('l1', '小北在学吉他而且刚买了新琴弦', rein=4.0, subject=late),
        ]),
    }
    harness = _RenderHarness(persona)
    gate = _pool('阿离在准备考试而且最近睡得很晚')

    with patch('memory.persona.rendering.SCOPED_RENDER_GROUP_RESERVED_TOKENS',
               gate), \
            patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS', 1), \
            patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', gate):
        rendered = await harness.arender_persona_markdown(
            '小天', subjects=[early, exempt_group, late],
            include_legacy_private=False,
        )

    assert '群里不要主动提起的那件事' in rendered, (
        "只有免预算内容的群没能渲染出它的「别主动提」清单"
    )
    assert '阿离在准备考试而且最近睡得很晚' in rendered, (
        "排在前面的成员替一个根本不花钱的群让了位"
    )
    assert '小北在学吉他而且刚买了新琴弦' not in rendered, (
        "排在后面的成员捡走了那份没人花的保底额度——caller order 被跨过群边界倒了"
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
        _pool('阿离在准备考试而且最近睡得很晚'),
        _pool('小北在学吉他而且刚买了新琴弦'),
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
async def test_two_custom_scopes_of_one_subject_id_get_separate_budgets():
    """Bucketing is by (key, scope), not by persona section.

    A section key is only ``kind:subject_id``, so the same kind/id under
    two custom scopes shares one section. Bucket by section and their
    budgets merge back together — precisely what this split exists to
    stop. Every other fixture here uses the factory helpers, whose scope
    defaults to ``kind:subject_id``, so key and scope move together and
    the distinction is invisible.
    """
    from memory.scopes import MemorySubject

    domain_a = MemorySubject.create(
        'group_participant', 'qq:7788:2046', scope='domain-a',
    )
    domain_b = MemorySubject.create(
        'group_participant', 'qq:7788:2046', scope='domain-b',
    )
    assert domain_a.key == domain_b.key, "夹具失效：两个 subject 的 key 应当相同"
    assert domain_a.persona_section_key == domain_b.persona_section_key

    a_facts = [_entry('a1', '阿离在 A 域说过的事情', rein=9.0, subject=domain_a)]
    b_facts = [_entry('b1', '阿离在 B 域说过的事情', rein=1.0, subject=domain_b)]
    persona = {
        domain_a.persona_section_key: {
            **domain_a.as_entry_fields(), 'facts': a_facts + b_facts,
        },
    }
    harness = _RenderHarness(persona)
    # Only enough for ONE entry if the two scopes share a pool.
    pool = _pool(a_facts[0]['text'])

    with patch('memory.persona.rendering.PERSONA_RENDER_MAX_TOKENS', pool):
        rendered = await harness.arender_persona_markdown(
            '小天', subjects=[domain_a, domain_b], include_legacy_private=False,
        )

    assert '阿离在 A 域说过的事情' in rendered
    assert '阿离在 B 域说过的事情' in rendered, (
        "两个自定义 scope 被并回一个预算池，低分那份被挤掉了"
    )


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


# Each scenario pins a different knob AT its boundary. A loose fixture
# (every budget comfortably larger than the content) makes the parity
# check vacuous: both twins render everything and agree for the wrong
# reason. `expect` is what the async path must produce, so a scenario that
# stops exercising its knob fails here instead of quietly going slack.
_PARITY_SCENARIOS = [
    (
        'persona-pool',            # per-subject persona ceiling binds
        {'PERSONA_RENDER_MAX_TOKENS': 10, 'SCOPED_RENDER_SUBJECT_MIN_TOKENS': 1},
        {'present': ['群规是不许剧透', '阿离在准备考试'],
         'absent': ['群里在筹划露营', '阿离养了一只橘猫']},
    ),
    (
        'reflection-pool',         # per-subject reflection ceiling binds
        {'REFLECTION_RENDER_MAX_TOKENS': 13, 'SCOPED_RENDER_SUBJECT_MIN_TOKENS': 1},
        {'present': ['小天觉得这个群很热闹', '小天觉得阿离最近很忙'],
         'absent': ['小天觉得群里爱聊吃的']},
    ),
    (
        # Gate nearly spent; the floor is low, so the 2nd subject still
        # gets its sliver — only the entries that don't fit drop out.
        'total-gate',
        {'SCOPED_RENDER_TOTAL_MAX_TOKENS': 40,
         'REFLECTION_RENDER_MAX_TOKENS': 13,
         'SCOPED_RENDER_SUBJECT_MIN_TOKENS': 1},
        {'present': ['群规是不许剧透', '- x'], 'absent': ['阿离在准备考试']},
    ),
    (
        # Same gate, floor raised above the sliver: now the 2nd subject
        # renders NOTHING. Contrasting with the row above is what proves
        # the floor does its own work and isn't just the gate again.
        'min-floor',
        {'SCOPED_RENDER_TOTAL_MAX_TOKENS': 40,
         'REFLECTION_RENDER_MAX_TOKENS': 13,
         'SCOPED_RENDER_SUBJECT_MIN_TOKENS': 7},
        {'present': ['群规是不许剧透'], 'absent': ['阿离在准备考试', '- x']},
    ),
    (
        'group-reserve',           # group queued last keeps its slice
        {'SCOPED_RENDER_TOTAL_MAX_TOKENS': 26,
         'SCOPED_RENDER_GROUP_RESERVED_TOKENS': 17,
         'SCOPED_RENDER_SUBJECT_MIN_TOKENS': 1,
         'reversed_order': True},
        {'present': ['群规是不许剧透'], 'absent': ['阿离养了一只橘猫']},
    ),
]


def _parity_fixture():
    group, member = _group_and_member()
    persona = {
        group.persona_section_key: _scoped_section(group, [
            _entry('g1', '群规是不许剧透', rein=9.0, subject=group),
            _entry('g2', '群里在筹划露营', rein=8.0, subject=group),
        ]),
        member.persona_section_key: _scoped_section(member, [
            _entry('m1', '阿离在准备考试', rein=3.0, subject=member),
            _entry('m2', '阿离养了一只橘猫', rein=2.0, subject=member),
            # 1-token crumb: it is what distinguishes "the gate left a
            # sliver" from "the floor refused to render a sliver".
            _entry('m3', 'x', rein=1.0, subject=member),
        ]),
    }
    reflections = [
        _reflection('rg1', '小天觉得这个群很热闹', rein=5.0, subject=group),
        _reflection('rg2', '小天觉得群里爱聊吃的', rein=4.5, subject=group),
        _reflection('rm1', '小天觉得阿离最近很忙', rein=4.0, subject=member),
    ]
    return group, member, persona, reflections


@pytest.mark.parametrize(
    'name,knobs,expect', _PARITY_SCENARIOS, ids=[s[0] for s in _PARITY_SCENARIOS],
)
@pytest.mark.asyncio
async def test_sync_and_async_scoped_renders_agree(name, knobs, expect):
    """The two paths differ only in how they count tokens.

    This is the behavioural version of "fix both twins": it holds however
    the budget code is later restructured, and unlike a source-shape check
    it cannot be satisfied by a cosmetic edit. Every knob gets a scenario
    where it actually binds — a twin that diverges on exactly one knob has
    nowhere to hide.
    """
    group, member, persona, reflections = _parity_fixture()
    knobs = dict(knobs)
    order = ([member, group] if knobs.pop('reversed_order', False)
             else [group, member])
    patches = [
        patch(f'memory.persona.rendering.{key}', value)
        for key, value in knobs.items()
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        sync_out = _RenderHarness(persona).render_persona_markdown(
            '小天', None, reflections,
            subjects=order, include_legacy_private=False,
        )
        async_out = await _RenderHarness(persona).arender_persona_markdown(
            '小天', None, reflections,
            subjects=order, include_legacy_private=False,
        )

    assert sync_out == async_out, f"sync/async 在 {name} 这档上分叉了"
    for text in expect['present']:
        assert text in async_out, f"[{name}] 夹具失效：{text} 本该渲染出来"
    for text in expect['absent']:
        assert text not in async_out, (
            f"[{name}] 夹具失效：{text} 还在，这一档的预算根本没绑定，"
            f"相等性断言就成了两边都全渲染的空话"
        )


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


@pytest.mark.asyncio
async def test_suppressed_entries_still_bypass_the_token_budget():
    """Strict dual of the protected case. A half-listed do-not-mention
    list is worse than none: the character confidently volunteers the
    entries that fell off the end."""
    persona = {
        'master': {'facts': [
            _entry('s1', '不要主动提这件很长很长的事情' * 30, suppress=True),
        ]},
    }
    harness = _RenderHarness(persona)

    with patch('memory.persona.rendering.PERSONA_RENDER_MAX_TOKENS', 1), \
            patch('memory.persona.rendering.REFLECTION_RENDER_MAX_TOKENS', 1):
        rendered = await harness.arender_persona_markdown('小天')

    assert '不要主动提这件很长很长的事情' in rendered
