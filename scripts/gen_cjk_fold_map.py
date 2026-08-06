# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Regenerate the Traditional->Simplified fold map in ``utils/cjk_fold.py``.

Unlike ``scripts/gen_activity_fold_map.py`` this map is **open**: every
character in the BMP CJK block whose ``t2s`` form is a different single
character goes in, because the caller (memory near-duplicate scoring)
folds arbitrary user text and has no alias table to bound it by.

The map therefore does not go stale when other tables change — rerun
this only after an OpenCC upgrade.

Run::

    uv run --with opencc-python-reimplemented python scripts/gen_cjk_fold_map.py

OpenCC is a build-time tool only; nothing at runtime imports it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / 'utils' / 'cjk_fold.py'


def build_map() -> dict[str, str]:
    import opencc

    t2s = opencc.OpenCC('t2s')
    mapping: dict[str, str] = {}
    for cp in range(0x4E00, 0xA000):
        ch = chr(cp)
        folded = t2s.convert(ch)
        if len(folded) == 1 and folded != ch:
            mapping[ch] = folded
    return mapping


def main() -> int:
    mapping = build_map()
    # 折叠必须幂等：t2s 里存在链式项（实测 薴→苧、而 苧→苎），而 translate 只
    # 走一趟——留着链的话，同一个字按输入形态会落到不同结果，两侧对齐就失效。
    # 先把每条映射推到不动点，再断言真的没有链剩下。
    for ch in list(mapping):
        target = mapping[ch]
        seen = {ch}
        while target in mapping and target not in seen:
            seen.add(target)
            target = mapping[target]
        mapping[ch] = target
    chained = {c: t for c, t in mapping.items() if t in mapping}
    if chained:
        raise RuntimeError(f'折叠映射不是幂等的，存在环: {chained}')
    trad = ''.join(sorted(mapping))
    simp = ''.join(mapping[c] for c in sorted(mapping))

    def wrap(s: str, per: int = 48) -> str:
        return '\n'.join(f"    '{s[i:i + per]}'" for i in range(0, len(s), per))

    src = TARGET.read_text(encoding='utf-8')
    for name, value in (
        ('_TRAD_FOLD_SOURCE', wrap(trad)),
        ('_SIMP_FOLD_TARGET', wrap(simp)),
    ):
        # ⚠️ re.sub 不匹配时**静默**返回原文本——常量的书写格式一变，生成器
        # 就会保留旧映射却照常打印成功。卡死替换次数（同 gen_activity_fold_map）。
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
