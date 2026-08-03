"""Regenerate the Traditional->Simplified fold map in activity_keywords.

``config/activity_keywords.py`` matches window titles against Simplified
aliases. Traditional-locale machines report Traditional titles, so both
sides are folded to Simplified before matching (see ``fold_script``).

The fold map is a **closed set**: it holds only characters whose
Simplified form actually occurs in the alias tables. That keeps the fold
from pulling unrelated text toward a keyword it could not otherwise
reach -- but it also means the map goes stale when an alias introduces a
CJK character that was not there before.
``tests/unit/test_activity_keywords_script_fold.py`` detects that and
points here.

Run::

    uv run --with opencc-python-reimplemented python scripts/gen_activity_fold_map.py

OpenCC is a build-time tool only; nothing at runtime imports it.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / 'config' / 'activity_keywords.py'
CJK = re.compile(r'[一-鿿]')


def alias_chars() -> set[str]:
    """Every CJK character reachable through the built needle tables."""
    sys.path.insert(0, str(REPO))
    from config import activity_keywords as ak

    chars: set[str] = set()
    for table in (ak._TITLE_TABLE, ak._PROCESS_TABLE, ak._DOMAIN_TABLE):
        for needle, _ in table:
            text = needle if isinstance(needle, str) else needle.pattern
            chars.update(CJK.findall(text))
    return chars


def digest(chars: set[str]) -> str:
    return hashlib.sha256(''.join(sorted(chars)).encode('utf-8')).hexdigest()[:16]


def build_map(chars: set[str]) -> dict[str, str]:
    import opencc

    t2s = opencc.OpenCC('t2s')
    mapping: dict[str, str] = {}
    for cp in range(0x4E00, 0xA000):
        ch = chr(cp)
        folded = t2s.convert(ch)
        if len(folded) == 1 and folded != ch and folded in chars:
            mapping[ch] = folded
    return mapping


def main() -> int:
    chars = alias_chars()
    mapping = build_map(chars)
    trad = ''.join(sorted(mapping))
    simp = ''.join(mapping[c] for c in sorted(mapping))

    def wrap(s: str, per: int = 48) -> str:
        return '\n'.join(f"    '{s[i:i + per]}'" for i in range(0, len(s), per))

    src = TARGET.read_text(encoding='utf-8')
    for name, value in (
        ('_TRAD_FOLD_SOURCE', wrap(trad)),
        ('_SIMP_FOLD_TARGET', wrap(simp)),
    ):
        # ⚠️ re.sub 在不匹配时**静默**返回原文本。这里必须卡住替换次数：常量的
        # 书写格式一变，生成器就会保留旧映射、却照常更新指纹并打印成功——指纹
        # 测试于是变绿，而新字符对应的繁体标题仍然一条都撞不上。
        # 这个脚本存在的唯一意义就是防静默过期，它自己不能有静默失败。
        src, replaced = re.subn(
            rf'{name} = \(\n(?:.*\n)*?\)',
            f'{name} = (\n{value}\n)',
            src,
            count=1,
        )
        if replaced != 1:
            raise RuntimeError(f'{TARGET} 里找不到 {name} 的定义块，无法更新')
    src, replaced = re.subn(
        r"_FOLD_ALIAS_CHAR_DIGEST = '[^']*'",
        f"_FOLD_ALIAS_CHAR_DIGEST = '{digest(chars)}'",
        src,
        count=1,
    )
    if replaced != 1:
        raise RuntimeError(
            f'{TARGET} 里找不到 _FOLD_ALIAS_CHAR_DIGEST，无法更新'
        )
    TARGET.write_text(src, encoding='utf-8')
    print(f'{len(mapping)} fold pairs over {len(chars)} alias characters')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
