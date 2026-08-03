"""繁体窗口标题的活动分类（#2500）。

活动追踪把窗口标题、进程名和浏览器域名往关键词表上撞。表是简体写的，
繁体系统上报的是繁体标题，于是**一条都撞不上**——不是分错类，是
`unknown`，整个活动感知对繁体用户静默失效。

修法不是给 490 条别名逐个补繁体孪生，而是在匹配的**两侧**都折叠到简体
（`fold_script`）。这里的测试盯的是那两侧都真的折了，以及折叠表没有
随着新别名加入而悄悄过期。
"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import astuple
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config import activity_keywords as ak  # noqa: E402

CJK = re.compile(r'[一-鿿]')

CLASSIFIERS = (
    ak.classify_window_title,
    ak.classify_process_name,
    ak.classify_browser_title,
)


def _table_texts() -> list[str]:
    texts = []
    for table in (ak._TITLE_TABLE, ak._PROCESS_TABLE, ak._DOMAIN_TABLE):
        for needle, _ in table:
            texts.append(needle if isinstance(needle, str) else needle.pattern)
    return texts


def _alias_chars() -> set[str]:
    chars: set[str] = set()
    for text in _table_texts():
        chars.update(CJK.findall(text))
    return chars


# The inverse of the fold map. A Simplified character can have several
# Traditional sources (发 -> 發/髮), so picking one deterministically may
# produce text no human would write. That is fine here: the point is to
# prove the *call sites* fold, and any of the sources folds back to the
# same Simplified character.
_UNFOLD = {}
for _t, _s in zip(ak._TRAD_FOLD_SOURCE, ak._SIMP_FOLD_TARGET):
    _UNFOLD.setdefault(_s, _t)


def _to_traditional(text: str) -> str:
    return ''.join(_UNFOLD.get(c, c) for c in text)


CJK_ALIASES = sorted({t for t in _table_texts() if CJK.search(t) and '\\' not in t})


def test_the_corpus_under_test_is_not_empty():
    """⚠️ 上面那些别名是从表里自动发现的。如果发现逻辑哪天失效，
    参数化就退化成空集，底下所有用例会「全绿」——绿在没跑上。
    """  # noqa: DOCSTRING_CJK
    assert len(CJK_ALIASES) > 250


@pytest.mark.parametrize('alias', CJK_ALIASES)
def test_a_traditional_title_classifies_the_same_as_its_simplified_twin(alias):
    """繁体标题必须和简体拿到同一个分类结果。

    这是 #2500 里「0 命中」那一类：软降级看不出来，因为返回的是
    `unknown`，跟「用户在用没收录的软件」长得一模一样。
    """  # noqa: DOCSTRING_CJK
    traditional = _to_traditional(alias)
    if traditional == alias:
        pytest.skip('script-neutral alias')
    for classify in CLASSIFIERS:
        assert astuple(classify(traditional)) == astuple(classify(alias)), (
            f'{classify.__name__}: {traditional!r} vs {alias!r}'
        )


def test_every_entry_point_folds_not_just_the_window_title():
    """⚠️ 三个入口各自 `.lower()` 一次，折叠也得各自加一次。

    只给 `classify_window_title` 加是最容易犯的错——浏览器标题走的是
    `classify_browser_title`，进程名走 `classify_process_name`，
    漏掉哪个哪个就继续 0 命中。所以这里逐个函数断言，而不是只测
    `fold_script` 本身。
    """  # noqa: DOCSTRING_CJK
    cases = {
        ak.classify_window_title: ('網易雲音樂', '网易云音乐'),
        ak.classify_process_name: ('嗶哩嗶哩.exe', '哔哩哔哩.exe'),
        ak.classify_browser_title: ('網易郵箱 收件匣', '网易邮箱 收件匣'),
    }
    for classify, (traditional, simplified) in cases.items():
        got = classify(traditional)
        assert got.category != 'unknown', f'{classify.__name__} still 0-hit'
        assert astuple(got) == astuple(classify(simplified))


def test_needles_are_stored_folded():
    """表里本来就混着 30 多条手写的繁体别名。

    如果只折输入不折别名，那些条目就变成了永远撞不上的死数据——输入被
    折成简体，别名还是繁体。折叠必须发生在 `_make_needle` 里。
    """  # noqa: DOCSTRING_CJK
    unfolded = [t for t in _table_texts() if set(t) & set(ak._TRAD_FOLD_SOURCE)]
    assert unfolded == [], f'needles kept Traditional characters: {unfolded[:5]}'


def test_simplified_and_ascii_text_is_untouched():
    """折叠对简体和非中文必须是恒等——不然就是拿繁体覆盖换简体回归。"""  # noqa: DOCSTRING_CJK
    for text in ('网易云音乐', 'visual studio code', 'モンスターハンター',
                 '배틀그라운드', 'Форза', ''):
        assert ak.fold_script(text) == text


def test_the_fold_map_has_not_gone_stale():
    """⚠️ 折叠表是**闭集**：只收「简体形已经出现在表里」的字，这样折叠
    永远不会把无关文本拽向一个它本来撞不上的关键词。

    代价是新增别名带进新字时它会过期，而且过期是静默的——那个字所在的
    繁体标题继续 0 命中，跟修之前一模一样。这里用字符集指纹兜住。
    """  # noqa: DOCSTRING_CJK
    actual = hashlib.sha256(
        ''.join(sorted(_alias_chars())).encode('utf-8')
    ).hexdigest()[:16]
    assert actual == ak._FOLD_ALIAS_CHAR_DIGEST, (
        'alias tables gained or lost CJK characters; regenerate the fold map:\n'
        '  uv run --with opencc-python-reimplemented '
        'python scripts/gen_activity_fold_map.py'
    )


def test_the_fold_map_stays_closed():
    """折叠目标必须全部是表里真在用的字。多出来的说明表不再是闭集，
    折叠就可能把无关文本拽进某个关键词。
    """  # noqa: DOCSTRING_CJK
    stray = set(ak._SIMP_FOLD_TARGET) - _alias_chars()
    assert stray == set(), f'fold targets outside the alias tables: {sorted(stray)}'


def test_the_fold_map_is_well_formed():
    assert len(ak._TRAD_FOLD_SOURCE) == len(ak._SIMP_FOLD_TARGET)
    assert len(set(ak._TRAD_FOLD_SOURCE)) == len(ak._TRAD_FOLD_SOURCE)
    assert not set(ak._TRAD_FOLD_SOURCE) & set(ak._SIMP_FOLD_TARGET), (
        'a character is both a fold source and a fold target, so folding '
        'would not be idempotent'
    )
