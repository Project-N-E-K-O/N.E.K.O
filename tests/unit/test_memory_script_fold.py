"""繁简同义文本的记忆召回（#2584）。

`hybrid_recall` 用字符 2/3-gram 分词，于是同一句话的简体版和繁体版几乎
零 token 重叠：繁中用户提问命中不了自己早期用简体记下的 fact，反之亦然。
issue 里那五组实测的平均 Jaccard 是 0.103，最狠的一组是 0.00 —— 不是
排名靠后，是 BM25 打 0 分直接被 `score > 0` 丢掉。

修法是在 `_tokenize` 里把**两侧**都折到简体。这里的测试盯三件事：折叠表
本身是良构的、`_tokenize` 真的折了（含降级路径和 stop_names），以及折完
之后 issue 那几组确实换了结果。
"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from memory import script_fold as sf  # noqa: E402
from memory.hybrid_recall import _bm25_rank, _tokenize  # noqa: E402


# ── 折叠表本身 ────────────────────────────────────────────────────────
#
# 这张表是**开集**（OpenCC 在 URO 里全部 1:1 的繁简对），不像
# `config/activity_keywords.py` 那张闭集会随着别名新增而过期——仓库里没有
# 任何东西喂它，所以这里没有指纹测试，只钉结构不变量。手改表把它改坏是
# 唯一的失效路径，下面几条就是冲它去的。


def test_the_fold_map_is_well_formed():
    assert len(sf._TRAD_FOLD_SOURCE) == len(sf._SIMP_FOLD_TARGET)
    assert len(set(sf._TRAD_FOLD_SOURCE)) == len(sf._TRAD_FOLD_SOURCE)


def test_the_fold_map_is_not_truncated():
    """⚠️ 下面的等价性用例是拿这张表**派生**出繁体样本的。表要是被截短成
    几十条，那些用例照样全绿——绿在没折到的字根本没进语料。
    """  # noqa: DOCSTRING_CJK
    assert len(sf._TRAD_FOLD_SOURCE) > 2500


def test_no_character_is_both_a_fold_source_and_a_fold_target():
    """折叠链（`薴` → `苧` → `苎`）必须在生成时就压到不动点。

    `str.translate` 是单趟的：链条原样烤进表里，`薴` 和 `苧` 会落到两个
    不同的简体字上——正是这张表要消灭的那种跨脚本失配，只不过换了个更
    隐蔽的地方发生。
    """  # noqa: DOCSTRING_CJK
    both = set(sf._TRAD_FOLD_SOURCE) & set(sf._SIMP_FOLD_TARGET)
    assert both == set(), f'fold sources that are also targets: {sorted(both)}'


def test_folding_never_moves_a_character_out_of_the_tokenizer_cjk_range():
    """⚠️ `_tokenize` 的 CJK 占比判定只数 U+4E00-9FFF。

    OpenCC 有 710 对是把 URO 里的字折到 Ext-A/Ext-B 上的（`俓` → `𠇹`、
    `倲` → `㑈`）。收进表就等于：那段文本折完之后判定翻成「拉丁」，整段吐
    一个 token 而不是 2/3-gram——**折叠反而把 substring 召回关掉了**，比不折
    还糟。生成脚本筛掉了这类，这里钉住它别回来。

    另一条路（放宽 `_tokenize` 的 CJK 判定）没走：那个判定被文档写明与
    `persona._extract_keywords` 严格一致，单边放宽就是把两处规则拆散。
    """  # noqa: DOCSTRING_CJK
    for ch in sf._TRAD_FOLD_SOURCE + sf._SIMP_FOLD_TARGET:
        assert '一' <= ch <= '鿿', (
            f'{ch!r} (U+{ord(ch):04X}) is outside the tokenizer CJK range'
        )


def test_a_folded_cjk_segment_still_tokenizes_as_ngrams():
    """上一条的行为面：折完仍要走 n-gram 分支，而不是退化成整段一个 token。

    只断言表里的码位范围会漏掉「判定逻辑本身被改坏」这一路，所以这里直接
    看 `_tokenize` 的产物。`俓倲偑` 是筛掉的那 710 对里的字（曾折成
    `𠇹㑈㐽`），拿它当样本：表要是把这类收回去，这条立刻退化成单 token。
    """  # noqa: DOCSTRING_CJK
    for text in ('俓倲偑', '這臺機器'):
        tokens = _tokenize(text, None)
        assert len(tokens) > 1, f'{text}: degraded to one token: {tokens}'
        assert all(2 <= len(t) <= 3 for t in tokens), tokens


def test_folding_is_idempotent_and_length_preserving():
    """定长是 `_tokenize` 先折后切的前提：折叠不能挪动任何标点的位置。"""  # noqa: DOCSTRING_CJK
    for src, dst in zip(sf._TRAD_FOLD_SOURCE, sf._SIMP_FOLD_TARGET):
        assert sf.fold_script(src) == dst
        assert sf.fold_script(dst) == dst
        assert len(sf.fold_script(src)) == len(src)


def test_simplified_and_non_chinese_text_is_untouched():
    """折叠对简体和非中文必须是恒等——否则就是拿繁体覆盖换简体回归。"""  # noqa: DOCSTRING_CJK
    for text in ('用户在写软件程序', 'visual studio code',
                 'モンスターハンター', '배틀그라운드', 'Форза', ''):
        assert sf.fold_script(text) == text


# ── _tokenize 真的折了 ────────────────────────────────────────────────

# 逆映射：一个简体字可能有多个繁体来源（发 ← 發/髮），随便取一个即可——
# 目的只是造出「用繁体字形写的同一句话」，任何一个来源都折回同一个简体字。
_UNFOLD: dict[str, str] = {}
for _t, _s in zip(sf._TRAD_FOLD_SOURCE, sf._SIMP_FOLD_TARGET):
    _UNFOLD.setdefault(_s, _t)


def _to_traditional(text: str) -> str:
    return ''.join(_UNFOLD.get(c, c) for c in text)


# 只含字形差异的句子（不含台湾用语替换）。它们折叠后应当**完全**等价。
GLYPH_ONLY = [
    '主人喜欢猫',
    '最近养了只猫',
    '他对机器学习很感兴趣',
    '她说话的时候总是带着笑',
    '她喜欢在周末看电影和听音乐',
    '主人的生日是十二月三号',
]


@pytest.mark.parametrize('simplified', GLYPH_ONLY)
def test_a_traditional_sentence_tokenizes_exactly_like_its_simplified_twin(
    simplified,
):
    """纯字形差异的两句话，token 必须一模一样（不是「重叠变多」，是相等）。

    包含次数：BM25 吃的是 TF，只断言集合相等会放过「折了但把重复项吃掉」
    这类改动。
    """  # noqa: DOCSTRING_CJK
    traditional = _to_traditional(simplified)
    assert traditional != simplified, 'sample has no Traditional form to test'
    assert _tokenize(traditional, None) == _tokenize(simplified, None)


def test_the_issue_zero_overlap_case_now_shares_tokens():
    """issue 表格最后一行：字形 + 台湾用语双重差异，修前重叠 0 个 token。

    折叠解决不了用词（用户/使用者、软件/軟體），所以这里不能断言相等——
    但重叠必须从 0 变成非 0，否则这条 fact 在 BM25 侧依旧完全不可见。
    """  # noqa: DOCSTRING_CJK
    a = set(_tokenize('用户在写软件程序', None))
    b = set(_tokenize('使用者在寫軟體程式', None))
    assert a & b, 'still zero overlap across scripts'


def test_the_fallback_tokenizer_folds_too(monkeypatch):
    """⚠️ `_tokenize` 有一条 persona 导入失败的降级分支（whitespace split）。

    只给主路径加折叠的话，降级时会**静默**退回 #2584 的老行为——而降级
    本身是不报错的，没人会发现召回悄悄跨不过繁简了。
    """  # noqa: DOCSTRING_CJK
    real_import = __builtins__['__import__'] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def boom(name, *args, **kwargs):
        if name == 'memory.persona':
            raise ImportError('simulated: persona not loaded')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr('builtins.__import__', boom)
    # 断言等于降级路径的产物（whitespace split + len>=2，`和` 被丢掉），
    # 而不只是「两边相等」——后者在没真降级时也成立，测试会绿在没跑上。
    assert _tokenize('機器學習 和 軟體', None) == ['机器学习', '软体']


def test_stop_names_strip_across_scripts():
    """stop_names 和文本一起折，所以简体配置的名字能从繁体 fact 里剥掉。

    顺序反了（先 strip 再 fold）这条就红：strip 时两边还在不同脚本里。
    """  # noqa: DOCSTRING_CJK
    # 名字配成简体、fact 是繁体写的（顺序反了就红：strip 时两边还在不同
    # 脚本里）。
    stripped = _tokenize('主人跟蘭蘭去看電影了', ['兰兰'])
    assert not any('兰' in t or '蘭' in t for t in stripped)
    # 反向：名字配成繁体、fact 是简体写的（stop_names 不跟着折就红）。
    stripped_trad_name = _tokenize('主人跟兰兰去看电影了', ['蘭蘭'])
    assert not any('兰' in t or '蘭' in t for t in stripped_trad_name)


# ── 端到端：BM25 排序 ─────────────────────────────────────────────────

POOL = [
    {'id': 'cat', 'text': '主人最近养了只猫，很喜欢'},
    {'id': 'dev', 'text': '主人在写软件程序，用的是 Python'},
    {'id': 'coffee', 'text': '主人喜欢喝咖啡'},
    {'id': 'movie', 'text': '主人昨天去看了电影'},
    {'id': 'ml', 'text': '主人对机器学习很感兴趣'},
]


@pytest.mark.parametrize('query,expected', [
    ('最近養了隻貓', 'cat'),
    ('他對機器學習很感興趣', 'ml'),
    ('昨天去看了電影', 'movie'),
    # 字形 + 台湾用语：修前 BM25 打 0 分，整条被 `score > 0` 丢掉，
    # 也就是繁中用户问「我在寫什麼軟體」时这条 fact 根本不进候选。
    ('使用者在寫軟體程式', 'dev'),
])
def test_a_traditional_query_ranks_the_simplified_fact_first(query, expected):
    ranked = _bm25_rank(query, POOL, stop_names=None)
    assert ranked, f'{query!r} matched nothing'
    assert ranked[0][0]['id'] == expected


def test_a_traditional_query_scores_like_its_simplified_twin():
    """折叠之后繁简两种问法应当拿到同一个分数，而不只是「都能命中」。

    分数决定它能不能挤进 `HYBRID_RECALL_BUDGET_EACH` 的 top-N，也决定
    RRF 融合里的名次——只断言命中会放过「命中了但分数依然低一截」。
    """  # noqa: DOCSTRING_CJK
    trad = _bm25_rank('他對機器學習很感興趣', POOL, stop_names=None)
    simp = _bm25_rank('他对机器学习很感兴趣', POOL, stop_names=None)
    assert [(d['id'], round(s, 6)) for d, s in trad] == \
           [(d['id'], round(s, 6)) for d, s in simp]
