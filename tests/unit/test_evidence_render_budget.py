# -*- coding: utf-8 -*-
"""
Unit tests for the P-D render budget pipeline (memory-evidence-rfc §3.6).

Covers:
  - utils.tokenize: tiktoken happy path + heuristic fallback (one-shot warn)
  - PersonaManager._score_trim_entries / _ascore_trim_entries:
      * preserves protected entries regardless of budget (S12)
      * sorts by (evidence_score, importance) DESC
      * keeps the token sum within budget, skipping what does not fit
        (the skip-don't-stop rule itself lives in
        tests/unit/test_memory_render_token_budget.py)
  - 3-phase render: persona budget independent from reflection budget (S11)
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import PERSONA_RENDER_ENCODING
from utils.tokenize import count_tokens


# ── utils.tokenize ──────────────────────────────────────────────────


def test_count_tokens_uses_tiktoken_for_chinese():
    from utils.tokenize import _reset_fallback_warned_for_tests, count_tokens

    _reset_fallback_warned_for_tests()
    n = count_tokens("测试")
    assert n > 0, "tiktoken should produce non-zero tokens for non-empty text"
    # Empty string short-circuits to 0 — independent of encoder.
    assert count_tokens("") == 0


def test_token_helpers_treat_special_token_strings_as_literal_text():
    from utils.tokenize import (
        _reset_fallback_warned_for_tests,
        count_tokens,
        truncate_to_tokens,
    )

    _reset_fallback_warned_for_tests()
    text = "user note contains <|endoftext|> literally"
    special = "<|endoftext|>"

    assert count_tokens(text) > 0
    assert count_tokens(special) > 0
    # The special-token string must be treated as literal text (not a tokenizer
    # sentinel that raises/strips): it round-trips, and a high budget leaves the
    # full text untouched.
    assert truncate_to_tokens(special, count_tokens(special)) == special
    assert truncate_to_tokens(text, 10_000) == text


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
    from utils.tokenize import (
        _reset_fallback_warned_for_tests,
        count_tokens,
    )

    _reset_fallback_warned_for_tests()

    # Force tiktoken.get_encoding to raise so _get_encoder hits the
    # heuristic path on every call.
    def _broken_get_encoding(*_args, **_kwargs):
        raise RuntimeError("encoding file missing — packaging bug simulation")

    fake_tiktoken = MagicMock()
    fake_tiktoken.get_encoding.side_effect = _broken_get_encoding
    monkeypatch.setitem(__import__('sys').modules, 'tiktoken', fake_tiktoken)

    with caplog.at_level(logging.WARNING, logger='utils.tokenize'):
        n1 = count_tokens("测试 hello")
        n2 = count_tokens("again 测试")
        n3 = count_tokens("more 中文 tokens")

    assert n1 > 0 and n2 > 0 and n3 > 0
    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and 'tiktoken' in r.getMessage()
    ]
    assert len(warnings) == 1, (
        f"expected exactly one fallback warning, got {len(warnings)}"
    )

    # Reset for any downstream test in the same process.
    _reset_fallback_warned_for_tests()


def test_heuristic_floor_for_short_non_empty_text(monkeypatch):
    """Coderabbit Major: int() truncated short non-empty strings to 0
    (e.g. "ok" → int(0.5) → 0). Score-trim treats a 0-token entry as
    free and bypasses the budget. The fix is a max(1, ...) clamp on
    non-empty input. Empty stays 0 — short-circuited at the caller.
    """
    from utils.tokenize import (
        _count_tokens_heuristic,
        _reset_fallback_warned_for_tests,
        count_tokens,
    )

    _reset_fallback_warned_for_tests()

    # Direct heuristic call (bypasses tiktoken entirely)
    assert _count_tokens_heuristic("ok") >= 1, (
        "short latin text must count as at least 1 token, never 0"
    )
    assert _count_tokens_heuristic("a") >= 1
    assert _count_tokens_heuristic("") == 0, (
        "empty string is the only legitimate 0 — caller short-circuits"
    )

    # End-to-end via count_tokens with tiktoken forced unavailable.
    def _broken(*_a, **_kw):
        raise RuntimeError("force heuristic")

    fake_tiktoken = MagicMock()
    fake_tiktoken.get_encoding.side_effect = _broken
    monkeypatch.setitem(__import__('sys').modules, 'tiktoken', fake_tiktoken)
    _reset_fallback_warned_for_tests()

    assert count_tokens("ok") >= 1
    assert count_tokens("") == 0
    _reset_fallback_warned_for_tests()


# ── 降级计数是上界，不是估算（#2574） ────────────────────────────────
#
# #2574 报的是老启发式（非 CJK 约 0.25 token/char）在打包形态下系统性
# 低估：URL / base64 / 代码片段这类高熵文本实测真实 token 能到预算的
# 2.5–2.7 倍，而且整条链路没有任何告警。修法是把权重换成 UTF-8 字节
# 长度（#2626）——BPE 的每个 token 至少映射 1 字节，merge 只会让 token
# 更少，所以字节数是真实 token 数的**严格上界**。
#
# 这条上界性质是整个修复的立论基础，但此前没有测试直接钉住它：现有用
# 例只覆盖了 warning 一次性、空串和短串下限。下面三条按「计数 → 单条
# 截断 → 整段预算」把这条不变量在每一层各钉一遍，用的就是 issue 点名
# 的那几类高熵样本。

# issue 点名的三类高熵文本，加上 CJK / 混合 / 组合字符等边界形态。
_HIGH_ENTROPY_SAMPLES = [
    # URL：老启发式最惨的一类，标点和路径段几乎每个字符都自成 token
    "https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/utils/tokenize.py#L112",
    # base64：没有任何可 merge 的自然语言词块
    "aGVsbG8gd29ybGQgdGhpcyBpcyBiYXNlNjQgcGFkZGluZyBkYXRhIQ==",
    # 代码片段
    "def _heuristic_char_weight(c): return float(len(c.encode('utf-8')))",
    # 纯 CJK：字节权重在这里最保守（3 字节/字 vs 真实约 1 token/字）
    "主人今天心情不错，说想吃辣条",
    # 中英混排 + URL
    "主人发了个链接 https://example.com/a?b=1&c=2 说让我看看",
    # emoji（4 字节）与组合字符
    "辣条好吃🌶️🔥 café é",
    # 短串：max(1, ...) 下限的那一头
    "ok",
    "a",
]


def _real_encoder():
    """真实 tiktoken encoder；数据文件取不到就跳过——这几条断言的参照系
    就是它，没有它测的就不是同一件事。必须在打 fallback 补丁**之前**调，
    否则 `import tiktoken` 拿到的是 MagicMock。"""
    try:
        import tiktoken
        return tiktoken.get_encoding(PERSONA_RENDER_ENCODING)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"tiktoken encoding data unavailable: {e}")


@pytest.fixture
def force_heuristic(monkeypatch):
    """让 `_get_encoder` 必然失败，把 utils.tokenize 整个模块压到降级分支，
    并把打补丁前抓到的真实 encoder yield 出来做参照系。

    退出时清 `_ENCODERS`：该缓存把失败也缓存下来（正是它让降级不会每次
    重试磁盘 IO），不清的话同进程后续用例全被钉死在降级形态。"""
    real_enc = _real_encoder()

    from utils.tokenize import _reset_fallback_warned_for_tests

    fake_tiktoken = MagicMock()
    fake_tiktoken.get_encoding.side_effect = RuntimeError(
        "encoding file missing — packaging bug simulation"
    )
    monkeypatch.setitem(sys.modules, 'tiktoken', fake_tiktoken)
    _reset_fallback_warned_for_tests()
    yield real_enc
    _reset_fallback_warned_for_tests()


@pytest.mark.parametrize("text", _HIGH_ENTROPY_SAMPLES)
def test_heuristic_never_undercounts_real_tokens(text):
    """降级计数必须 ≥ 真实 tiktoken 计数——#2574 的核心不变量。

    直接调 `_count_tokens_heuristic`，不需要打 fallback 补丁，所以同一个
    用例里两边都能拿到真值。反过来（高估多少）不设上限：字节权重对 CJK
    约 3 倍、对英文约 4 倍，那是降级形态下**少渲染**，是有意选择的方向
    （见 utils/tokenize.py 模块 docstring），不是回归。
    """
    from utils.tokenize import _count_tokens_heuristic

    enc = _real_encoder()
    real = len(enc.encode(text, disallowed_special=()))
    heuristic = _count_tokens_heuristic(text)
    assert heuristic >= real, (
        f"降级计数低估了真实 token：{text!r} 启发式 {heuristic} < 真实 "
        f"{real}——#2574 的预算突破就是这么来的"
    )


@pytest.mark.parametrize("text", _HIGH_ENTROPY_SAMPLES)
@pytest.mark.parametrize("budget", [1, 5, 20])
def test_heuristic_truncate_output_fits_real_budget(force_heuristic, text, budget):
    """降级形态下 `truncate_to_tokens` 的输出，用真实 tokenizer 量也必须
    在预算内。单条召回（L21 每条 400）靠的就是这一层。"""
    from utils.tokenize import truncate_to_tokens

    enc = force_heuristic
    out = truncate_to_tokens(text, budget)
    real = len(enc.encode(out, disallowed_special=())) if out else 0
    assert real <= budget, (
        f"降级截断的输出超预算：{text!r} → {out!r} 真实 {real} > {budget}"
    )


@pytest.mark.parametrize("budget", [40, 120, 2200])
def test_heuristic_line_budget_holds_against_real_tokenizer(force_heuristic, budget):
    """降级形态下 `take_lines_within_token_budget` 的产物（含 join 用的
    分隔符），用真实 tokenizer 量也必须在预算内。

    2200 是 #2563 给 L21 召回整段定的预算，issue 实测在旧启发式下真实
    token 能到它的 2.5–2.7 倍；40 / 120 是让裁剪真的发生的紧预算。

    唯一豁免是「首行必留」——调用方按相关性排过序，宁可超一点也要出一
    条，见 `take_lines_within_token_budget` 的 docstring。"""
    from utils.tokenize import take_lines_within_token_budget

    enc = force_heuristic
    separator = "\n"
    kept, dropped = take_lines_within_token_budget(
        _HIGH_ENTROPY_SAMPLES, budget, separator=separator,
    )
    assert kept, "非零预算下至少要留一行"
    assert dropped == len(_HIGH_ENTROPY_SAMPLES) - len(kept)

    real = len(enc.encode(separator.join(kept), disallowed_special=()))
    if len(kept) == 1:
        return  # 首行必留，本来就允许单条超预算
    assert real <= budget, (
        f"降级整段预算被突破：留了 {len(kept)} 行，真实 {real} > {budget}"
    )


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
    with patch("memory.persona.manager.get_config_manager", return_value=cm):
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


def test_score_trim_stays_within_budget():
    """Sorted by (score, importance) DESC; the accumulated token count
    never crosses the cap. Lower-score entries that don't fit are dropped
    — they don't sneak in via fallback.

    (An entry that doesn't fit is skipped rather than ending the loop; the
    same-size fixture here can't tell the two apart, so that rule has its
    own guard in `test_memory_render_token_budget.py`.)"""
    pm = _persona_manager()
    now = datetime.now()
    entries = [
        _entry('e1', 'A' * 40, rein=3.0),  # highest score
        _entry('e2', 'B' * 40, rein=2.0),
        _entry('e3', 'C' * 40, rein=1.0),
        _entry('e4', 'D' * 40, rein=0.0),
    ]
    # tiny budget that fits ~1.5 entries given the all-latin text
    kept, used = pm._score_trim_entries(entries, budget=15, now=now)

    # At minimum the highest-score entry survives; nothing past the budget
    assert kept, "expected at least one entry under non-zero budget"
    assert kept[0]['id'] == 'e1', (
        "score-trim must keep highest-score entry first"
    )
    assert all(k['id'] != 'e4' for k in kept), (
        "lowest-score entry must be dropped under tight budget"
    )
    assert used <= 15, "kept token sum must respect the cap it was given"
    assert used == sum(count_tokens(k['text']) for k in kept), (
        "reported usage must match what the kept entries actually cost — "
        "the per-subject allocator hands the remainder to the next subject"
    )


def test_score_trim_importance_breaks_score_ties():
    pm = _persona_manager()
    now = datetime.now()
    entries = [
        _entry('a', 'short', rein=2.0, importance=1),
        _entry('b', 'short', rein=2.0, importance=9),  # higher importance
        _entry('c', 'short', rein=2.0, importance=5),
    ]
    kept, _used = pm._score_trim_entries(entries, budget=10**6, now=now)
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


def test_split_promotes_legacy_string_facts(tmp_path):
    """Codex P1: pre-PR-1 persona files sometimes stored facts as bare
    strings. The pre-PR-3 render path emitted them via
    `_render_fact_entries`'s `elif entry: lines.append(...)` branch.
    PR-3's `_split_persona_for_render` would silently drop them; we
    normalize ad-hoc here so legacy memories still appear in prompts.
    """
    pm = _persona_manager()
    persona = {
        'master': {'facts': [
            _entry('m1', 'normal dict entry', rein=1.0),
            "legacy string fact about master",  # bare string, no schema
        ]},
    }
    protected, by_entity = pm._split_persona_for_render(persona)
    assert protected == []
    texts = {e.get('text', '') for e in by_entity.get('master', [])}
    assert 'normal dict entry' in texts
    assert 'legacy string fact about master' in texts, (
        "string facts must be promoted to ad-hoc dicts so they keep "
        "rendering — pre-PR-3 behaviour"
    )


def test_split_drops_whitespace_only_string_facts(tmp_path):
    """裸字符串半边的空白闸（#2578 只修了 dict entry 那半）：'   ' 对
    truthiness 为真，promote 之后跳过了 dict 路径的 _renderable_text
    检查，一路渲成空 bullet。判据必须与 dict 路径同口径（strip）。"""  # noqa: DOCSTRING_CJK
    pm = _persona_manager()
    persona = {
        'master': {'facts': [
            '   ',
            '  \t ',
            # 非 str 假值：裸 str() 判空会把它们变成 "None"/"False"/"0"
            # 文本渲染出来（review 抓的）——legacy 形态只有裸字符串，
            # 其余一律丢弃。
            None,
            False,
            0,
            _entry('m1', 'normal dict entry', rein=1.0),
            'legacy string fact about master',
        ]},
    }
    protected, by_entity = pm._split_persona_for_render(persona)
    assert protected == []
    texts = [e.get('text', '') for e in by_entity.get('master', [])]
    assert texts == ['normal dict entry', 'legacy string fact about master'], (
        f"纯空格裸字符串 / 非 str 假值不得被 promote 成可渲染条目: {texts!r}"
    )


def test_compose_skips_blank_dict_entries_even_if_split_missed_them():
    """compose 半边的独立护栏：split 侧已挡住空白条目，但 compose 的判据
    必须自成一道（走 _renderable_text 而非 truthiness）——否则上游不变量
    一松动，'- ' 空 bullet 就直接进 prompt。绕过 split 直调 compose。"""  # noqa: DOCSTRING_CJK
    pm = _persona_manager()
    blank = _entry('b1', '   ')
    good = _entry('g1', '主人喜欢辣条', rein=1.0)
    persona = {'master': {'facts': [blank, good]}}
    md = pm._compose_markdown_from_trimmed(
        '小天', persona, {'human': '主人'},
        [], [blank, good],
        {id(blank): 'master', id(good): 'master'},
        [], [],
    )
    assert '主人喜欢辣条' in md
    blank_bullets = [
        line for line in md.splitlines()
        if line.startswith('- ') and not line[2:].strip()
    ]
    assert blank_bullets == [], (
        f"compose 渲染出了空 bullet 行: {blank_bullets!r}"
    )


@pytest.mark.asyncio
async def test_render_emits_no_blank_bullets_for_legacy_blank_strings(tmp_path):
    """端到端：facts 列表混入纯空格裸字符串（pre-PR-1 遗留数据形态）时，
    渲染出的 markdown 不得含空 bullet 行。compose 侧的判据同样要走
    _renderable_text，不能靠 truthiness。"""  # noqa: DOCSTRING_CJK
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
    with patch("memory.persona.manager.get_config_manager", return_value=cm):
        pm = PersonaManager()
    pm._config_manager = cm

    persona = {
        'master': {'facts': [
            '   ',
            '  \t ',
            _entry('m1', '主人喜欢辣条', rein=1.0),
        ]},
    }
    pm._personas['小天'] = persona

    async def _aensure(name):
        return persona
    pm.aensure_persona = _aensure  # type: ignore[assignment]
    pm.aupdate_suppressions = AsyncMock()

    md = await pm.arender_persona_markdown('小天')

    assert '主人喜欢辣条' in md
    blank_bullets = [
        line for line in md.splitlines()
        if line.startswith('- ') and not line[2:].strip()
    ]
    assert blank_bullets == [], (
        f"渲染出了空 bullet 行: {blank_bullets!r}"
    )


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
    with patch("memory.persona.manager.get_config_manager", return_value=cm):
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
    with patch("memory.persona.manager.get_config_manager", return_value=cm):
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
    with patch('memory.persona.rendering.PERSONA_RENDER_MAX_TOKENS', 1):
        md = await pm.arender_persona_markdown('小天')

    assert '主人是一只猫娘的主人' in md, (
        "protected entries must render even when budget is exhausted"
    )


# ── Reflection render preserves score-DESC order ──────────────────────


def _reflection_dict(rid: str, text: str, *, rein: float = 0.0,
                     disp: float = 0.0) -> dict:
    """Minimal reflection shape understood by `_score_trim_entries` and
    `_partition_trimmed_reflections`. Matches the runtime shape that
    `ReflectionEngine` persists (see `tests/unit/test_evidence_promote_merge
    ._reflection`)."""
    return {
        'id': rid, 'text': text,
        'reinforcement': rein, 'disputation': disp,
        'rein_last_signal_at': None, 'disp_last_signal_at': None,
        'sub_zero_days': 0, 'user_fact_reinforce_count': 0,
        'importance': 0,
    }


@pytest.mark.asyncio
async def test_arender_preserves_reflection_score_order(tmp_path):
    """Regression for CodeRabbit PR #936 round-4 Minor (line 1872): the
    score-trim output was being converted to a `kept_ids` set and then
    re-filtered by iterating the ORIGINAL `pending_reflections` /
    `confirmed_reflections` lists, which lost the score-DESC order from
    `_ascore_trim_entries`. Fix iterates the sorted combined list and
    partitions back to pending/confirmed while preserving order.

    This test: 3 non-protected reflections with varying evidence scores,
    all fit the budget; assert the rendered markdown emits them in
    score-DESC order within their respective sections.
    """
    from memory.persona import PersonaManager

    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人"}, {}, {}, {}, {},
    ))
    with patch("memory.persona.manager.get_config_manager", return_value=cm):
        pm = PersonaManager()
    pm._config_manager = cm

    persona = {'master': {'facts': []}}
    pm._personas['小天'] = persona

    async def _aensure(name):
        return persona
    pm.aensure_persona = _aensure  # type: ignore[assignment]
    pm.aupdate_suppressions = AsyncMock()

    # Caller-supplied lists in DELIBERATELY non-score order so a
    # score-order-preserving render is distinguishable from a
    # source-order-preserving render.
    pending = [
        _reflection_dict('p_low', '低分pending', rein=0.5),
        _reflection_dict('p_high', '高分pending', rein=5.0),
    ]
    confirmed = [
        _reflection_dict('c_mid', '中分confirmed', rein=2.0),
        _reflection_dict('c_top', '最高confirmed', rein=9.0),
    ]

    md = await pm.arender_persona_markdown(
        '小天', pending_reflections=pending, confirmed_reflections=confirmed,
    )

    # Locate the two reflection sections in the output.
    pending_header = '### 小天最近的印象（还不太确定）'
    confirmed_header = '### 小天比较确定的印象'
    assert pending_header in md
    assert confirmed_header in md

    # Within pending section: 高分pending (rein=5.0) must appear before
    # 低分pending (rein=0.5).
    pending_section = md.split(pending_header, 1)[1]
    # Next section starts with '\n\n### ' — cut there.
    pending_body = pending_section.split('\n\n### ', 1)[0]
    high_pos = pending_body.find('高分pending')
    low_pos = pending_body.find('低分pending')
    assert high_pos >= 0 and low_pos >= 0, (
        f"both pending entries must render, got body:\n{pending_body!r}"
    )
    assert high_pos < low_pos, (
        f"pending must be score-DESC; got 高分@{high_pos} vs 低分@{low_pos}"
    )

    # Within confirmed section: 最高confirmed (rein=9.0) must appear
    # before 中分confirmed (rein=2.0).
    confirmed_section = md.split(confirmed_header, 1)[1]
    confirmed_body = confirmed_section.split('\n\n### ', 1)[0]
    top_pos = confirmed_body.find('最高confirmed')
    mid_pos = confirmed_body.find('中分confirmed')
    assert top_pos >= 0 and mid_pos >= 0, (
        f"both confirmed entries must render, got body:\n{confirmed_body!r}"
    )
    assert top_pos < mid_pos, (
        f"confirmed must be score-DESC; got 最高@{top_pos} vs 中分@{mid_pos}"
    )


def test_partition_trimmed_reflections_preserves_order():
    """Unit-level regression for the new helper: iterating the
    score-sorted input and partitioning back to pending/confirmed must
    preserve the input order within each bucket, and must drop
    suppressed text."""
    pm = _persona_manager()

    pending_source = [
        {'id': 'p1', 'text': 'alpha'},
        {'id': 'p2', 'text': 'gamma'},
    ]
    # The combined list is in DELIBERATELY different order from the
    # source lists — it simulates what _score_trim_entries emits.
    trimmed_combined = [
        pending_source[1],                    # gamma (pending, rank 1)
        {'id': 'c1', 'text': 'delta'},        # confirmed, rank 2
        pending_source[0],                    # alpha (pending, rank 3)
        {'id': 'c2', 'text': 'epsilon'},      # confirmed, rank 4 —
                                              # but suppressed below
    ]
    suppressed = {'epsilon'}

    pend, conf = pm._partition_trimmed_reflections(
        trimmed_combined, pending_source, suppressed,
    )
    assert [r['id'] for r in pend] == ['p2', 'p1'], (
        "pending must preserve the input (score-sorted) order"
    )
    assert [r['id'] for r in conf] == ['c1'], (
        "confirmed must preserve input order AND drop suppressed text"
    )
