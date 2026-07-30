"""Guards for the L10 core-memory render budget.

- ``_score_trim_entries`` skips an entry it cannot afford instead of
  treating it as a stop sign; one over-long top-ranked entry used to make
  the whole persona / reflection section vanish.
- A scoped (group) render gives every subject its own persona and
  reflection budget, bounded overall by ``SCOPED_RENDER_TOTAL_MAX_TOKENS``.
  Allocation follows the caller's subject order, never one invented here —
  unconditionally, with no reserved slice for any subject kind. (An earlier
  draft of this file described a group reserve two lines above that
  sentence; the reserve was deleted before merge and the two halves of the
  docstring had been contradicting each other ever since.)
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
    """A per-subject pool sized to hold exactly these entries.

    Pools are denominated in entry TEXT, in every mode — that is the whole
    point of keeping one meaning per constant.
    """
    return sum(count_tokens(t) for t in texts)


def _gate(*texts: str) -> int:
    """Overall-gate capacity for exactly these entries.

    The gate is denominated in RENDERED tokens, so each entry costs its
    text plus the bullet and newline composition adds. A fixture that
    sizes the gate with bare ``count_tokens`` comes out one markup short
    per entry and silently drops the last one.
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


async def _render_either(harness, twin: str, *args, **kwargs) -> str:
    """Drive whichever of the two render twins `twin` names.

    Budget guards that only ever call the async path leave the sync twin
    free to drift: `test_sync_and_async_scoped_renders_agree` catches a
    divergence only on the knobs its scenarios actually bind, and a
    one-sided edit to an accounting line it does not exercise sails
    through — which is how the reflection charge on the overall gate came
    to be uncovered on the sync side.

    Used by the three guards whose invariant is easiest to break one-sided
    (the gate's running total, cross-subject reflection order, caller
    order). The other allocator guards in this file still drive the async
    path only and lean on the parity test; widening them is worthwhile but
    not done here, so do not read this helper's existence as "every
    allocator guard covers both twins".
    """
    if twin == 'sync':
        return harness.render_persona_markdown(*args, **kwargs)
    return await harness.arender_persona_markdown(*args, **kwargs)


_TWINS = ('sync', 'async')


@pytest.mark.parametrize('twin', _TWINS)
@pytest.mark.asyncio
async def test_total_gate_drops_a_trailing_subject_whole(twin):
    """When the overall gate runs out, the remaining subject loses its
    whole budgeted section — a two-line persona reads to the model as that
    person's complete profile, which is worse than an honest absence.

    "Whole section", not "nothing": a subject whose only content is
    budget-exempt (protected / suppressed) costs the gate nothing and
    still renders, which is what
    `test_a_group_holding_only_suppressed_facts_still_renders_them` pins.
    The subject here has budgeted facts it cannot afford, so it goes.

    The earlier subjects carry reflections as well as facts, so the gate
    has to account for BOTH. Fact-only fixtures leave the reflection half
    of the accounting untested: drop `remaining -= reflection_used` and
    a fact-only test never notices.

    Carrying reflections is necessary but NOT sufficient, which is how this
    guard spent a release doing nothing. Sizing the gate in bare
    ``count_tokens`` left the leftover at 127 tok with the reflection
    charge and 179 without — both under the 200 tok floor, so the third
    subject was skipped either way and the assertion below could not tell
    the two apart. The fixture has to land the leftover on OPPOSITE sides
    of the floor, which means denominating the gate the way the gate is
    denominated: rendered tokens, via ``_gate``. Correct → exactly
    ``MIN - 1`` left, one token under. Mutated → that plus whatever the
    reflections cost, comfortably over, and the third subject appears.
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
    logger = MagicMock()

    from config import SCOPED_RENDER_SUBJECT_MIN_TOKENS

    # Charged cost — text PLUS markup — for everything the first two
    # subjects render, so the leftover is exactly one token under the floor.
    spent = _gate(
        *[e['text'] for s in subjects[:2] for e in facts[s.subject_id]],
        *[r['text'] for r in reflections],
    )
    reflection_charge = _gate(*[r['text'] for r in reflections])
    total = spent + SCOPED_RENDER_SUBJECT_MIN_TOKENS - 1
    assert reflection_charge >= 1, "夹具失效：reflection 一点额度都没花"

    with patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', total), \
            patch('memory.persona.rendering.logger', logger):
        rendered = await _render_either(
            harness, twin, '小天', None, reflections,
            subjects=subjects, include_legacy_private=False,
        )

    assert '成员0最近在学做菜和爬山还在写小说' in rendered
    assert '成员1周末常去看展顺便逛书店' in rendered
    assert '小天觉得成员0最近状态还不错也挺好聊的' in rendered
    assert '小天觉得成员1最近状态还不错也挺好聊的' in rendered
    assert '短' not in rendered, (
        "总闸剩量低于单 subject 下限时该整段跳过，而不是塞进能放下的碎片"
    )
    # Assert the LEFTOVER itself, not just which side of the floor it fell
    # on: the skip log is the only place the allocator's running total is
    # observable, and a number is the one assertion an "equivalent but
    # wrong" accounting cannot satisfy by accident. Dropping the reflection
    # charge shows up here as `MIN - 1 + reflection_charge`.
    skips = [
        c for c in logger.warning.call_args_list if '整段跳过' in str(c)
    ]
    assert len(skips) == 1, f"夹具失效：期望恰好一次整段跳过，实际 {len(skips)} 次"
    assert f"剩余 {SCOPED_RENDER_SUBJECT_MIN_TOKENS - 1} tok" in str(skips[0]), (
        f"总闸剩量不是 {SCOPED_RENDER_SUBJECT_MIN_TOKENS - 1}——reflection 的用量"
        f"没有从 remaining 里扣掉（漏扣会多出 {reflection_charge} tok）："
        f"{skips[0]}"
    )


@pytest.mark.parametrize('twin', _TWINS)
@pytest.mark.asyncio
async def test_five_subjects_all_over_budget_skip_the_tail_whole(twin):
    """读侧扩到 [群, 当前发言人, 最近 3 人] = 5 个 subject 后总闸真正开始
    咬的形态：每个 subject 的语料都超出自己的 per-subject 池（persona 与
    reflection 双双超额），总闸只够前 4 个拿满配额，第 5 个必须整段跳过
    ——绝不是每个 subject 各渲染半截。分配是 caller order 先到先得，群排
    最前，这是调用方唯一的优先级旋钮；按比例摊薄的"等价"实现会让第 5
    个 subject 渲染出内容、且前 4 个渲染不满，两头都会红。"""
    from memory.scopes import MemorySubject

    subjects = [MemorySubject.group_chat("qq", "7788")] + [
        MemorySubject.group_participant("qq", "7788", str(2000 + i))
        for i in range(4)
    ]

    def _texts(i: int) -> tuple[list[str], list[str]]:
        return (
            [
                f'主体{i}最近在研究烘焙面包和手冲咖啡',
                f'主体{i}周末喜欢去美术馆看展览散心',
                f'主体{i}养了一只很黏人的橘猫叫毛毛',
            ],
            [
                f'小天觉得主体{i}最近的状态松弛了不少',
                f'小天猜主体{i}可能换了一份新的工作',
            ],
        )

    persona: dict = {}
    reflections: list[dict] = []
    for i, subject in enumerate(subjects):
        fact_texts, reflection_texts = _texts(i)
        persona[subject.persona_section_key] = _scoped_section(subject, [
            _entry(f's{i}f{j}', text, rein=float(5 - j), subject=subject)
            for j, text in enumerate(fact_texts)
        ])
        reflections.extend(
            _reflection(f's{i}r{j}', text, rein=float(5 - j), subject=subject)
            for j, text in enumerate(reflection_texts)
        )

    # 每个 subject 的池：恰好装下前 2 条 facts / 前 1 条 reflection —— 第
    # 3 条 fact 与第 2 条 reflection 双双超额。文本只差一个数字，token 数
    # 必须全体一致，否则"恰好装下"对某些 subject 不成立。
    fact_texts0, reflection_texts0 = _texts(0)
    persona_pool = _pool(*fact_texts0[:2])
    reflection_pool = _pool(reflection_texts0[0])
    for i in range(len(subjects)):
        fact_texts, reflection_texts = _texts(i)
        assert _pool(*fact_texts[:2]) == persona_pool, "夹具失效：token 数不齐"
        assert _pool(reflection_texts[0]) == reflection_pool
        assert _pool(fact_texts[2]) > 0

    from config import SCOPED_RENDER_SUBJECT_MIN_TOKENS

    per_subject_gate_spend = (
        _gate(*fact_texts0[:2]) + _gate(reflection_texts0[0])
    )
    total = per_subject_gate_spend * 4 + SCOPED_RENDER_SUBJECT_MIN_TOKENS - 1

    harness = _RenderHarness(persona)
    logger = MagicMock()
    with patch('memory.persona.rendering.PERSONA_RENDER_MAX_TOKENS', persona_pool), \
            patch('memory.persona.rendering.REFLECTION_RENDER_MAX_TOKENS', reflection_pool), \
            patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', total), \
            patch('memory.persona.rendering.logger', logger):
        rendered = await _render_either(
            harness, twin, '小天', None, reflections,
            subjects=subjects, include_legacy_private=False,
        )

    for i in range(4):
        fact_texts, reflection_texts = _texts(i)
        # 前 4 个 subject 拿满自己的配额：per-subject 池内的条目一条不少。
        assert fact_texts[0] in rendered and fact_texts[1] in rendered, (
            f"subject {i} 没拿满配额——按比例摊薄的分配会走到这里"
        )
        assert reflection_texts[0] in rendered
        # 超出 per-subject 池的部分照常被池裁掉。
        assert fact_texts[2] not in rendered
        assert reflection_texts[1] not in rendered
    # 第 5 个 subject（最近发言人名单的最后一位）整段消失：facts 与
    # reflections 一条都不得渲染——半截 persona 比缺席更糟。
    tail_fact_texts, tail_reflection_texts = _texts(4)
    for text in tail_fact_texts + tail_reflection_texts:
        assert text not in rendered, f"尾部 subject 渲染了半截: {text!r}"

    skips = [
        c for c in logger.warning.call_args_list if '整段跳过' in str(c)
    ]
    assert len(skips) == 1, f"期望恰好一次整段跳过，实际 {len(skips)} 次"
    assert f"剩余 {SCOPED_RENDER_SUBJECT_MIN_TOKENS - 1} tok" in str(skips[0])


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
    gate = _gate('群规是不许剧透而且要按时报名参加活动')

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
    gate = _gate(*['x'] * 6)

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
    total = _gate('阿离说过的一件事情要占掉不少预算才行')

    with patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS',
               _gate('x')), \
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
async def test_the_pool_ceiling_stays_denominated_in_entry_text():
    """One constant, one meaning.

    `PERSONA_RENDER_MAX_TOKENS` bounds entry text in legacy AND scoped
    mode. Charging the rendered markup against it too (an earlier shape of
    this code) quietly gave a scoped subject less than the number
    advertises — worst with short facts, where the markup is most of the
    line. The gate is the one measured in rendered tokens.
    """
    group, _member = _group_and_member()
    facts = [
        _entry(f'g{j}', 'x', rein=float(20 - j), subject=group)
        for j in range(20)
    ]
    persona = {group.persona_section_key: _scoped_section(group, facts)}
    harness = _RenderHarness(persona)
    pool = 10  # ten one-token facts

    with patch('memory.persona.rendering.PERSONA_RENDER_MAX_TOKENS', pool), \
            patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS', 1):
        rendered = await harness.arender_persona_markdown(
            '小天', subjects=[group], include_legacy_private=False,
        )

    assert rendered.count('- x') == pool, (
        f"文本预算 {pool} 只渲染了 {rendered.count('- x')} 条——markup 被算进了"
        f"per-subject 池，池的含义和常量名对不上了"
    )


@pytest.mark.asyncio
async def test_protected_cap_counts_only_entries_that_actually_render():
    """The count cap is spent on what reaches the prompt.

    Capping at split time spends the allowance in persona-file order,
    including on a subject the allocator later drops — and the entries it
    displaced cannot be brought back. A bulk card import on a subject that
    never renders would silently take a visible subject's character-card
    lines with it.
    """
    group, member = _group_and_member()
    persona = {
        # File order puts the doomed subject's cards first.
        member.persona_section_key: _scoped_section(member, [
            _entry(f'm-card{j}', f'阿离的角色卡第{j}条', protected=True,
                   subject=member)
            for j in range(3)
        ] + [
            _entry('m1', '阿离在准备考试而且最近睡得很晚', rein=1.0, subject=member),
        ]),
        group.persona_section_key: _scoped_section(group, [
            _entry('g-card', '群的角色卡设定', protected=True, subject=group),
            _entry('g1', '群规是不许剧透', rein=9.0, subject=group),
        ]),
    }
    harness = _RenderHarness(persona)
    gate = _gate('群规是不许剧透')

    with patch('memory.persona.rendering.PERSONA_RENDER_PROTECTED_MAX_ENTRIES', 3), \
            patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS', 1), \
            patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', gate):
        rendered = await harness.arender_persona_markdown(
            '小天', subjects=[group, member], include_legacy_private=False,
        )

    assert '群规是不许剧透' in rendered, "夹具失效：群没渲染出来"
    assert '阿离在准备考试而且最近睡得很晚' not in rendered, (
        "夹具失效：member 没被跳过，这条用例什么都没测到"
    )
    assert '群的角色卡设定' in rendered, (
        "配额被一个根本不渲染的 subject 的角色卡吃光了，可见 subject 的角色卡"
        "反而消失"
    )


def test_markup_allowance_covers_the_worst_rendered_decoration():
    """The gate's per-entry allowance is a worst case, not a typical one.

    Composition adds ``- `` and a newline to every entry, and a localized
    ``[13 months ago] `` prefix to stale reflections on top. An allowance
    sized for the common case lets a block of short stale reflections slip
    past the gate and then add several uncounted tokens apiece.
    """
    from datetime import timedelta

    from config import SCOPED_RENDER_ENTRY_MARKUP_TOKENS as MARKUP
    from memory.temporal import time_since_label

    now = datetime.now()
    worst = count_tokens("- ") + count_tokens("\n")
    for lang in ("zh", "en", "ja", "ko", "ru", "es", "pt"):
        for days in (1, 10, 45, 400):
            label = time_since_label(
                (now - timedelta(days=days)).isoformat(), now=now, lang=lang,
            )
            worst = max(worst, count_tokens("- ") + count_tokens("\n")
                        + count_tokens(f"[{label}] "))
    assert MARKUP >= worst, (
        f"实测最坏单条装饰 {worst} tok（含过时 reflection 的时间标签前缀），"
        f"预留只有 {MARKUP} tok——短条目多时整块会越过总闸"
    )


@pytest.mark.asyncio
async def test_a_suppressed_fact_under_many_subjects_spends_one_slot():
    """The do-not-mention section has one unscoped heading, so the same
    fact under several subjects renders once and costs one slot.

    Counting every copy lets duplicates fill the cap and push a genuinely
    different suppression out of the prompt — and the model then
    volunteers exactly that topic.
    """
    group, member = _group_and_member()
    shared = '不要主动提起的那件事'
    unique = '另一件也不要主动提起的事'
    persona = {
        group.persona_section_key: _scoped_section(group, [
            _entry('g-h1', shared, suppress=True, subject=group),
        ]),
        member.persona_section_key: _scoped_section(member, [
            _entry('m-h1', shared, suppress=True, subject=member),
            _entry('m-h2', unique, suppress=True, subject=member),
        ]),
    }
    harness = _RenderHarness(persona)

    with patch('memory.persona.rendering.PERSONA_RENDER_SUPPRESSED_MAX_ENTRIES', 2):
        rendered = await harness.arender_persona_markdown(
            '小天', subjects=[group, member], include_legacy_private=False,
        )

    assert rendered.count(f'- {shared}') == 1, (
        f"同一条 suppressed 事实渲染了 {rendered.count(f'- {shared}')} 次"
    )
    assert unique in rendered, (
        "重复项占满了条数上限，把另一条真正不同的「别主动提」挤出了 prompt"
    )


@pytest.mark.asyncio
async def test_allocation_follows_the_caller_subject_order():
    """Order is the caller's call, unconditionally.

    It sends [group, current speaker] today and grows to [group, speaker,
    three recent speakers] next; ranking the subjects here would silently
    override the only layer that knows who matters this turn. No subject
    kind is exempt — the group reserve that used to be that exemption is
    gone, and with it every way it had of inverting the order it was
    supposed to protect.
    """
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
        _gate('阿离在准备考试而且最近睡得很晚'),
        _gate('小北在学吉他而且刚买了新琴弦'),
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


@pytest.mark.parametrize('twin', _TWINS)
@pytest.mark.asyncio
async def test_reflections_render_in_global_score_order_across_subjects(twin):
    """Allocation order is the caller's; READING order is confidence.

    The two reflection sections are flat bullet lists under a single
    heading — nothing in them marks where one subject's impressions end and
    the next one's begin. Per-subject allocation hands back one trimmed
    list per slot and the allocator concatenates them, so without a final
    sort the flat list comes out in caller-subject order: a barely-held
    impression about the group sits above a well-evidenced one about the
    person actually being replied to, and position is the only confidence
    signal the model has. The pre-split code trimmed one global pool and
    was sorted by construction; nothing in the requirement asked for that
    to change, and nothing in the PR recorded that it had.
    """
    group, member = _group_and_member()
    persona = {
        group.persona_section_key: _scoped_section(group, []),
        member.persona_section_key: _scoped_section(member, []),
    }
    low_first = '小天觉得群里最近挺热闹的'      # listed first, scores LOWER
    high_second = '小天觉得阿离最近很努力'       # listed second, scores HIGHER
    reflections = [
        _reflection('rg', low_first, rein=1.0, subject=group),
        _reflection('rm', high_second, rein=9.0, subject=member),
    ]

    rendered = await _render_either(
        _RenderHarness(persona), twin, '小天', None, reflections,
        subjects=[group, member], include_legacy_private=False,
    )

    bullets = [line for line in rendered.split('\n') if line.startswith('- ')]
    assert set(bullets) == {f'- {low_first}', f'- {high_second}'}, (
        f"夹具失效：两条 reflection 没有都渲染出来，{bullets!r}"
    )
    assert bullets[0] == f'- {high_second}', (
        "低分的群印象排在了高分的成员印象前面——分配顺序（调用方的）泄漏成了"
        "渲染顺序（该按置信度）"
    )


@pytest.mark.parametrize('twin', _TWINS)
@pytest.mark.asyncio
async def test_a_group_queued_behind_a_member_gets_no_reserved_slice(twin):
    """The unconditional half of the caller-order contract, on the one
    subject kind that used to be exempt from it.

    ``test_allocation_follows_the_caller_subject_order`` proves the
    allocator does not rank by score — but it runs two
    ``group_participant`` subjects, and every reserve this code has ever
    carried keyed on ``group_chat``, so the reserve predicate is constant
    across that fixture and it cannot see one. The only other place a
    ``group_chat`` sits behind a member is the exempt-only-group case,
    whose group has nothing billable — precisely the shape a
    "reserve only for a group that has billable content" variant exempts
    itself from. That variant (the deleted ``_slot_has_entries``, which
    took five review rounds to kill) passes this whole file today.

    So: a group with real billable content, listed SECOND, must lose. Any
    reserve at all — conditional or not — starves the member ahead of it
    and hands the slot to the group, which is the order inversion the
    reserve was supposedly there to prevent.
    """
    group, member = _group_and_member()
    member_fact = '阿离在准备考试而且最近睡得很晚'
    group_fact = '群规是不许剧透大家都要遵守'
    persona = {
        member.persona_section_key: _scoped_section(member, [
            _entry('m1', member_fact, rein=1.0, subject=member),
        ]),
        group.persona_section_key: _scoped_section(group, [
            # Higher score AND billable, so nothing but caller order can
            # explain the member winning.
            _entry('g1', group_fact, rein=9.0, subject=group),
        ]),
    }
    # Funds exactly one of the two, whichever the allocator reaches first.
    total = max(_gate(member_fact), _gate(group_fact))

    with patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', total), \
            patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS', 1):
        rendered = await _render_either(
            _RenderHarness(persona), twin, '小天',
            subjects=[member, group], include_legacy_private=False,
        )

    assert member_fact in rendered, (
        "调用方把成员排在群前面，成员却被饿死了——群拿到了预留额度或插队权，"
        "而这正是删掉保底要根除的顺序倒置"
    )
    assert group_fact not in rendered, (
        "总闸只够一个 subject，排在后面的群却也渲染出来了——夹具失效或额度超发"
    )


@pytest.mark.asyncio
async def test_the_scoped_context_caller_ranks_the_group_first():
    """The other end of the caller-order contract.

    The allocator refuses to rank subjects, which only produces a sane
    render because the caller does. That makes "group first" a contract
    between two files with nothing between them enforcing it — `/scoped_
    context` accepts 1..8 subjects in any order and deliberately does not
    validate one (a caller with a legitimately different ranking should
    not get a 422). It is documented on the route; this is the executable
    half.

    Send members first and the group's own persona is what falls off the
    end of the gate — silently, as a group that has "no personality" this
    turn rather than as an error.
    """
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    bridge = MagicMock()
    bridge.group_subject.side_effect = QQMemoryBridge.group_subject
    bridge.group_participant_subject.side_effect = (
        QQMemoryBridge.group_participant_subject
    )
    bridge.fetch_scoped_bootstrap_memory = AsyncMock(return_value='群聊长期记忆')
    plugin = SimpleNamespace(
        memory_bridge=bridge, logger=MagicMock(),
        i18n=SimpleNamespace(t=lambda key, **kw: key),
        _qq_settings={
            'group_memory_enabled': True,
            'group_member_memory_enabled': True,
        },
    )

    await QQSessionInstructionService(plugin)._build_core_memory_section(
        should_use_memory_context=True,
        her_name='Neko', master_name='Master',
        context_ready_template='{name}/{master}',
        is_group=True, group_id='7788', sender_id='2046',
    )

    sent = bridge.fetch_scoped_bootstrap_memory.await_args.kwargs['subjects']
    assert len(sent) > 1, (
        "夹具失效：只发了一个 subject，顺序契约在这条路径上没有可测内容"
    )
    assert sent[0]['subject_kind'] == 'group_chat', (
        f"/scoped_context 的调用方没有把群排在第一位（实际首位 "
        f"{sent[0]['subject_kind']}）——总闸是先到先得，排在成员后面的群"
        f"会在额度耗尽后整段消失"
    )


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


# NOTE: these knob values are hand-derived from the fixture's token counts
# and SCOPED_RENDER_ENTRY_MARKUP_TOKENS; changing either means re-deriving.
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
        {'SCOPED_RENDER_TOTAL_MAX_TOKENS': 50,
         'REFLECTION_RENDER_MAX_TOKENS': 13,
         'SCOPED_RENDER_SUBJECT_MIN_TOKENS': 1},
        {'present': ['群规是不许剧透', '- x'], 'absent': ['阿离在准备考试']},
    ),
    (
        # Same gate, floor raised above the sliver: now the 2nd subject
        # renders NOTHING. Contrasting with the row above is what proves
        # the floor does its own work and isn't just the gate again.
        'min-floor',
        {'SCOPED_RENDER_TOTAL_MAX_TOKENS': 50,
         'REFLECTION_RENDER_MAX_TOKENS': 13,
         'SCOPED_RENDER_SUBJECT_MIN_TOKENS': 14},
        {'present': ['群规是不许剧透'], 'absent': ['阿离在准备考试', '- x']},
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
    order = [group, member]
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
async def test_a_malformed_text_value_does_not_bring_the_render_down():
    """`text` is a string in principle — JSON round-trip — but manual
    edits, pre-PR-1 leftovers and migration bugs all produce truthy
    non-strings, an epoch int being the classic. Compose has always
    formatted those fine, so a blank-filter that calls `.strip()` on them
    would take `/scoped_context` and `/new_dialog` down with it.
    """
    persona = {
        'master': {'facts': [
            _entry('int', 0, protected=True),
            _entry('epoch', 1735689600),
            _entry('listy', ['a', 'b']),
            _entry('ok', '主人喜欢辣条', rein=9.0),
        ]},
    }
    harness = _RenderHarness(persona)

    rendered = await harness.arender_persona_markdown('小天')

    assert '主人喜欢辣条' in rendered
    assert '1735689600' in rendered, "非字符串 text 应照常渲染，不该被当空条目丢掉"


@pytest.mark.parametrize('blank_carrier', ('facts', 'reflections'))
@pytest.mark.asyncio
async def test_blank_entries_do_not_spend_the_scoped_gate(blank_carrier):
    """A blank entry emits no line, so it must not be charged either.

    With the per-entry markup charge it costs a full allowance against the
    overall gate while producing nothing — enough blanks in front and the
    next subject drops under the floor and vanishes for no output at all.

    Both carriers, because "does this entry produce a line" is supposed to
    have exactly ONE answer (`_renderable_text`) and the reflection branch
    was not using it: it ran its own ``if not text``, and a whitespace-only
    string is truthy. So the reflection entered the bucket, paid text +
    markup against the gate, and composed into an empty ``- `` bullet. A
    fact-only fixture left this guard's name (blank entries do not spend
    the gate) claiming more than it proved.
    """
    group, member = _group_and_member()
    blanks = [
        _entry(f'blank{j}', '   ', rein=float(9 - j), subject=group)
        for j in range(6)
    ] if blank_carrier == 'facts' else []
    blank_reflections = [
        _reflection(f'rblank{j}', '   ', rein=float(9 - j), subject=group)
        for j in range(6)
    ] if blank_carrier == 'reflections' else []
    persona = {
        group.persona_section_key: _scoped_section(group, blanks + [
            _entry('g1', '群规是不许剧透', rein=1.0, subject=group),
        ]),
        member.persona_section_key: _scoped_section(member, [
            _entry('m1', '阿离在准备考试', rein=5.0, subject=member),
        ]),
    }
    harness = _RenderHarness(persona)
    gate = _gate('群规是不许剧透', '阿离在准备考试')

    with patch('memory.persona.rendering.SCOPED_RENDER_TOTAL_MAX_TOKENS', gate), \
            patch('memory.persona.rendering.SCOPED_RENDER_SUBJECT_MIN_TOKENS', 1):
        rendered = await harness.arender_persona_markdown(
            '小天', None, blank_reflections,
            subjects=[group, member], include_legacy_private=False,
        )

    assert '群规是不许剧透' in rendered
    assert '阿离在准备考试' in rendered, (
        f"空白{blank_carrier}在总闸上收了费，把后面的 subject 挤掉了"
        f"——而它们一行都没渲染"
    )
    # The other half of "emits no line": no empty bullet reaches the prompt.
    empty_bullets = [
        line for line in rendered.split('\n')
        if line.startswith('- ') and not line[2:].strip()
    ]
    assert empty_bullets == [], (
        f"空白条目渲染成了空 bullet：{empty_bullets!r}"
    )


@pytest.mark.asyncio
async def test_whitespace_only_suppressions_do_not_spend_the_count_cap():
    """Same rule for the do-not-mention cap: `if text:` accepted
    whitespace-only strings, which each took a slot and rendered as an
    empty bullet, pushing a real suppression out of the prompt."""
    persona = {
        'master': {'facts': [
            _entry(f'ws{j}', ' ' * (j + 1), suppress=True) for j in range(3)
        ] + [
            _entry('real', '不要主动提起的那件事', suppress=True),
        ]},
    }
    harness = _RenderHarness(persona)

    with patch('memory.persona.rendering.PERSONA_RENDER_SUPPRESSED_MAX_ENTRIES', 3):
        rendered = await harness.arender_persona_markdown('小天')

    assert '不要主动提起的那件事' in rendered, (
        "只有空白的 suppressed 条目占满了配额，真正的「别主动提」被挤出 prompt"
    )


@pytest.mark.asyncio
async def test_blank_protected_entries_do_not_spend_the_count_cap():
    """The cap counts lines that reach the prompt, not dict entries.

    Compose skips an entry with no text, so letting one occupy a slot
    spends the allowance on nothing — enough blanks in front and every
    real character-card line disappears while the section renders empty.
    Hand-edited or half-migrated persona.json is where blanks come from.

    The enforcing layer is `_split_persona_for_render`, and it is asserted
    on directly below. `_cap_protected_entries` used to repeat the same
    filter, which read like a second line of defence but was unreachable —
    its only caller is fed by the split, which has already dropped every
    blank. A guard aimed at the copy would have gone on passing with the
    real filter deleted.
    """
    persona = {
        'master': {'facts': [
            _entry(f'blank{j}', '   ', protected=True) for j in range(4)
        ] + [
            _entry('card', '主人是一只猫娘的主人', protected=True),
        ]},
    }
    harness = _RenderHarness(persona)

    protected, _by_entity = harness._split_persona_for_render(persona)
    assert [entry['id'] for _ek, entry in protected] == ['card'], (
        "空白 protected 条目穿过了 _split_persona_for_render——条数上限"
        "下游只能看到它已经放行的东西"
    )

    with patch('memory.persona.rendering.PERSONA_RENDER_PROTECTED_MAX_ENTRIES', 4):
        rendered = await harness.arender_persona_markdown('小天')

    assert '主人是一只猫娘的主人' in rendered, (
        "空白 protected 条目占满了条数上限，真正的角色卡反而没渲染"
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
