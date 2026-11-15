"""Memory recall for equivalent traditional/simplified Chinese text (#2584).

`hybrid_recall` uses character 2/3-grams, leaving the same sentence's simplified/traditional versions with almost
no token overlap: traditional-Chinese questions cannot recall earlier simplified facts, or vice versa.
The issue's five measured pairs averaged Jaccard 0.103; the worst scored 0.00, meaning
not low ranking but BM25 zero scores discarded directly by `score > 0`.

Fix `_tokenize` by folding **both sides** to simplified Chinese. Tests guard three properties: a well-formed
folding table, actual `_tokenize` folding including fallback/stop_names, and changed results for the
issue's examples after folding.
"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from memory import script_fold as sf  # noqa: E402
from memory.hybrid_recall import _bm25_rank, _tokenize  # noqa: E402


# ── Folding table itself ────────────────────────────────────────────────────────
#
# This table is an **open set** of all OpenCC 1:1 traditional/simplified pairs in URO, unlike
# config/activity_keywords.py's closed set, which becomes stale when aliases are added. Nothing in the repository
# feeds this table, so enforce structural invariants rather than fingerprints. Manual corruption is
# its only failure mode, targeted by the tests below.


def test_the_fold_map_is_well_formed():
    assert len(sf._TRAD_FOLD_SOURCE) == len(sf._SIMP_FOLD_TARGET)
    assert len(set(sf._TRAD_FOLD_SOURCE)) == len(sf._TRAD_FOLD_SOURCE)


def test_the_fold_map_is_not_truncated():
    """⚠️ Equivalence tests below **derive** traditional samples from this table. Truncating it to
    a few dozen entries would still pass, because uncovered characters never enter the corpus.
    """  # noqa: DOCSTRING_CJK
    assert len(sf._TRAD_FOLD_SOURCE) > 2500


def test_no_character_is_both_a_fold_source_and_a_fold_target():
    """Collapse folding chains (`薴` → `苧` → `苎`) to fixed points during generation.

    `str.translate` is single-pass: retaining chains maps `薴` and `苧` to two
    different simplified characters, recreating the very cross-script mismatch the table should eliminate,
    only in a more hidden location.
    """  # noqa: DOCSTRING_CJK
    both = set(sf._TRAD_FOLD_SOURCE) & set(sf._SIMP_FOLD_TARGET)
    assert both == set(), f'fold sources that are also targets: {sorted(both)}'


def test_folding_never_moves_a_character_out_of_the_tokenizer_cjk_range():
    """⚠️ `_tokenize` counts only U+4E00–9FFF when determining CJK proportion.

    OpenCC has 710 pairs mapping URO characters into Ext-A/Ext-B (`俓` → `𠇹`,
    `倲` → `㑈`). Including them changes folded text classification to Latin, producing
    one token rather than 2/3-grams: **folding would disable substring recall**, worse than
    no folding. Generation excludes these pairs; keep them excluded.

    The alternative, broadening `_tokenize` CJK detection, was not taken: documentation requires
    exact agreement with `persona._extract_keywords`, and unilateral expansion would split their rules.
    """  # noqa: DOCSTRING_CJK
    for ch in sf._TRAD_FOLD_SOURCE + sf._SIMP_FOLD_TARGET:
        assert '一' <= ch <= '鿿', (
            f'{ch!r} (U+{ord(ch):04X}) is outside the tokenizer CJK range'
        )


def test_a_folded_cjk_segment_still_tokenizes_as_ngrams():
    """Behavioral counterpart: folded text must still take n-gram tokenization, not one whole-text token.

    Checking only table code-point ranges misses corruption in classification logic itself, so inspect
    `_tokenize` output directly. `俓倲偑` comes from the excluded 710 pairs (formerly mapped to
    `𠇹㑈㐽`); including these mappings again immediately reduces this example to one token.
    """  # noqa: DOCSTRING_CJK
    for text in ('俓倲偑', '這臺機器'):
        tokens = _tokenize(text, None)
        assert len(tokens) > 1, f'{text}: degraded to one token: {tokens}'
        assert all(2 <= len(t) <= 3 for t in tokens), tokens


def test_folding_is_idempotent_and_length_preserving():
    """Fixed length is required for fold-before-split in `_tokenize`: folding must not move punctuation positions."""  # noqa: DOCSTRING_CJK
    for src, dst in zip(sf._TRAD_FOLD_SOURCE, sf._SIMP_FOLD_TARGET):
        assert sf.fold_script(src) == dst
        assert sf.fold_script(dst) == dst
        assert len(sf.fold_script(src)) == len(src)


def test_simplified_and_non_chinese_text_is_untouched():
    """Folding must be identity for simplified Chinese and non-Chinese, avoiding simplified-text regressions for traditional coverage."""  # noqa: DOCSTRING_CJK
    for text in ('用户在写软件程序', 'visual studio code',
                 'モンスターハンター', '배틀그라운드', 'Форза', ''):
        assert sf.fold_script(text) == text


# ── _tokenize actually folds ────────────────────────────────────────────────

# Reverse mapping: simplified characters can have multiple traditional sources (发 ← 發/髮); choose any,
# since the goal is the same sentence in traditional glyphs, and every source folds to the same simplified character.
_UNFOLD: dict[str, str] = {}
for _t, _s in zip(sf._TRAD_FOLD_SOURCE, sf._SIMP_FOLD_TARGET):
    _UNFOLD.setdefault(_s, _t)


def _to_traditional(text: str) -> str:
    return ''.join(_UNFOLD.get(c, c) for c in text)


# Sentences differ only in glyph forms, without Taiwanese vocabulary substitutions; folded results must be **exactly** equivalent.
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
    """Sentences differing only in glyph forms must produce identical tokens, not merely greater overlap.

    Include multiplicity: BM25 consumes TF, so set equality alone misses changes that fold
    but discard duplicates.
    """  # noqa: DOCSTRING_CJK
    traditional = _to_traditional(simplified)
    assert traditional != simplified, 'sample has no Traditional form to test'
    assert _tokenize(traditional, None) == _tokenize(simplified, None)


def test_the_issue_zero_overlap_case_now_shares_tokens():
    """The issue table's final row differs in both glyphs and Taiwanese vocabulary, with zero token overlap before the fix.

    Folding cannot solve vocabulary (用户/使用者, 软件/軟體), so equality is inappropriate here;
    overlap must nevertheless increase from zero, or BM25 still cannot see this fact.
    """  # noqa: DOCSTRING_CJK
    a = set(_tokenize('用户在写软件程序', None))
    b = set(_tokenize('使用者在寫軟體程式', None))
    assert a & b, 'still zero overlap across scripts'


def test_the_fallback_tokenizer_folds_too(monkeypatch):
    """⚠️ `_tokenize` falls back to whitespace splitting when persona import fails.

    Folding only the primary path would **silently** restore #2584 behavior on fallback; fallback
    itself raises no error, concealing the loss of traditional/simplified recall.
    """  # noqa: DOCSTRING_CJK
    real_import = __builtins__['__import__'] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def boom(name, *args, **kwargs):
        if name == 'memory.persona':
            raise ImportError('simulated: persona not loaded')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr('builtins.__import__', boom)
    # Assert the fallback result (whitespace split + len>=2, discarding `和`),
    # not merely equality: both sides can match without actually taking fallback, making an unexercised test pass.
    assert _tokenize('機器學習 和 軟體', None) == ['机器学习', '软体']


def test_stop_names_strip_across_scripts():
    """Fold stop_names together with text so simplified-form names strip from traditional-form facts.

    Reversing the order (strip before fold) fails because both sides still use different writing systems during stripping.
    """  # noqa: DOCSTRING_CJK
    # Simplified-form name with traditional-form facts: reversing order fails because stripping still sees different
    # writing systems).
    stripped = _tokenize('主人跟蘭蘭去看電影了', ['兰兰'])
    assert not any('兰' in t or '蘭' in t for t in stripped)
    # Reverse case: traditional-form name with simplified-form facts; failing to fold stop_names must fail.
    stripped_trad_name = _tokenize('主人跟兰兰去看电影了', ['蘭蘭'])
    assert not any('兰' in t or '蘭' in t for t in stripped_trad_name)


# ── End-to-end BM25 ranking ─────────────────────────────────────────────────

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
    # Glyphs + Taiwanese vocabulary: before the fix, BM25 scored 0 and `score > 0` discarded the fact,
    # so a traditional-Chinese user asking '我在寫什麼軟體' ('what software am I writing?') never got it as a candidate.
    ('使用者在寫軟體程式', 'dev'),
])
def test_a_traditional_query_ranks_the_simplified_fact_first(query, expected):
    ranked = _bm25_rank(query, POOL, stop_names=None)
    assert ranked, f'{query!r} matched nothing'
    assert ranked[0][0]['id'] == expected


def test_a_traditional_query_scores_like_its_simplified_twin():
    """After folding, traditional/simplified questions must receive identical scores, not merely both match.

    Scores determine entry into `HYBRID_RECALL_BUDGET_EACH` top-N and ranking in
    RRF fusion; matching-only assertions miss successful matches with persistently lower scores.
    """  # noqa: DOCSTRING_CJK
    trad = _bm25_rank('他對機器學習很感興趣', POOL, stop_names=None)
    simp = _bm25_rank('他对机器学习很感兴趣', POOL, stop_names=None)
    assert [(d['id'], round(s, 6)) for d, s in trad] == \
           [(d['id'], round(s, 6)) for d, s in simp]
