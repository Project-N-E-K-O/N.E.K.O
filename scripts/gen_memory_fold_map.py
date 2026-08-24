"""Regenerate the Traditional->Simplified fold map in ``memory/script_fold.py``.

Unlike ``scripts/gen_activity_fold_map.py``, this map is **open**: it
holds every 1:1 Traditional->Simplified character pair OpenCC knows in
the URO block, not just the characters some table happens to use. The
memory layer folds arbitrary user-written fact text, so there is no
closed alias set to derive from -- and a partial map would silently
leave whichever characters this user happens to type unfolded.

That also means this map does not go stale from repo edits (nothing in
the repo feeds it), so there is no fingerprint guard here. Regenerate
only when bumping OpenCC's dictionaries.

Run::

    uv run --with opencc-python-reimplemented python scripts/gen_memory_fold_map.py

OpenCC is a build-time tool only; nothing at runtime imports it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / 'memory' / 'script_fold.py'

# CJK Unified Ideographs (URO). Matches gen_activity_fold_map.py. Ext-A/B
# and the compatibility block are left out: OpenCC's Traditional->Simplified
# pairs there are vanishingly rare in chat text, and every character added
# widens the fold's blast radius for zero measured recall.
_URO = range(0x4E00, 0xA000)


def fixpoint(t2s, ch: str) -> str:
    """Fold until stable.

    OpenCC has a few chains (``薴`` -> ``苧`` -> ``苎``). ``str.translate``
    is single-pass, so a chain baked in verbatim would land ``薴`` and
    ``苧`` on *different* Simplified characters -- the exact cross-script
    mismatch this map exists to remove. Folding to the fixpoint here also
    keeps sources and targets disjoint, which the guard test asserts.
    """  # noqa: DOCSTRING_CJK  # the chain example needs the actual characters
    seen = {ch}
    cur = ch
    for _ in range(8):
        nxt = t2s.convert(cur)
        if nxt == cur:
            return cur
        if nxt in seen:
            raise RuntimeError(f'fold cycle through {ch!r}: {seen}')
        seen.add(nxt)
        cur = nxt
    raise RuntimeError(f'fold did not converge for {ch!r}')


def build_map() -> dict[str, str]:
    import opencc

    t2s = opencc.OpenCC('t2s')
    mapping: dict[str, str] = {}
    for cp in _URO:
        ch = chr(cp)
        folded = fixpoint(t2s, ch)
        # len != 1 would break str.maketrans' two-string form (and the
        # length-preserving property _tokenize relies on).
        if len(folded) != 1 or folded == ch:
            continue
        # The target must stay inside URO too. 710 pairs fold a URO
        # character onto an Ext-A/Ext-B ideograph (俓 -> 𠇹, 倲 -> 㑈), and
        # ``_tokenize``'s CJK predicate only counts U+4E00-9FFF: a segment
        # that folds out of that range flips from "CJK, emit 2/3-grams" to
        # "Latin, emit one whole-segment token", which *disables* substring
        # recall for exactly the text the fold was supposed to help.
        # Widening the predicate instead would diverge from
        # ``persona._extract_keywords``, which it is documented to mirror.
        # Both sides of these pairs are rare enough that dropping them just
        # leaves the pre-fold behaviour in place.
        if ord(folded) not in _URO:
            continue
        mapping[ch] = folded
    return mapping


def main() -> int:
    mapping = build_map()
    trad = ''.join(sorted(mapping))
    simp = ''.join(mapping[c] for c in sorted(mapping))
    overlap = set(trad) & set(simp)
    if overlap:
        raise RuntimeError(
            f'fold sources that are also targets (fixpoint bug): {sorted(overlap)}'
        )

    def wrap(s: str, per: int = 48) -> str:
        return '\n'.join(f"    '{s[i:i + per]}'" for i in range(0, len(s), per))

    src = TARGET.read_text(encoding='utf-8')
    for name, value in (
        ('_TRAD_FOLD_SOURCE', wrap(trad)),
        ('_SIMP_FOLD_TARGET', wrap(simp)),
    ):
        # ⚠️ re.sub 不匹配时**静默**返回原文本：常量的书写格式一变，生成器就会
        # 保留旧表却照常打印成功。同 gen_activity_fold_map.py，卡死替换次数。
        src, replaced = re.subn(
            rf'{name} = \(\n(?:.*\n)*?\)',
            f'{name} = (\n{value}\n)',
            src,
            count=1,
        )
        if replaced != 1:
            raise RuntimeError(f'{TARGET} 里找不到 {name} 的定义块，无法更新')
    TARGET.write_text(src, encoding='utf-8')
    print(f'{len(mapping)} fold pairs')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
