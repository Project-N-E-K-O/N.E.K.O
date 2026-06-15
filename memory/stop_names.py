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

"""
Stop-name helpers for the memory module's keyword / BM25 / extraction layer.

Why this exists: ``master_name``, ``lanlan_name`` and their respective ``昵称``
(nicknames) appear in nearly every conversation turn — once fed into
``_extract_keywords`` / ``_is_mentioned`` / FTS5 BM25, these tokens dominate
keyword overlap and retrieval scores, triggering massive false hits (unrelated
facts judged "mentioned", dedup misjudging similarity, contradiction-detection
false positives). Stripping these stop-names uniformly before the keyword
layer avoids the junk matches.

Design notes:
- Entry points are centralized in ``collect_stop_names`` /
  ``acollect_stop_names``, which read ``主人.档案名`` + ``主人.昵称`` from
  ``ConfigManager.get_character_data`` plus the given ``lanlan_name`` itself +
  that character's ``昵称``. ``lanlan_name`` defaults to the currently active
  catgirl, since some call sites only care about the active character.
- The ``昵称`` field is a comma-separated string (CJK or ASCII punctuation
  both fine); it is split into individual aliases.
- The list is deduped and ordered longest-first — in substring replacement the
  longer alias must match first, so stripping ``T酱`` first can't truncate
  ``小T酱`` into ``小``.
- ``strip_stop_names`` is a substring replace; good enough for CJK / short
  Latin names. Word-boundary handling for long Latin names is left for future
  need.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import re

# Comma / 中文逗号 / 顿号 / 分号 / 空白都视为昵称字段分隔符。
_NICKNAME_SPLIT_RE = re.compile(r"[,，;；、\s]+")

# 纯拉丁/数字字母的别名（"Tony"、"al"、"T-酱" 也算，只要不含 CJK / 其他脚本）。
# 这类别名走 word-boundary 替换，避免 ``Al`` 把 ``Algorithm`` 截掉一截。
_LATIN_ALIAS_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

# 别名最短长度。Codex PR-971 P2: 单字符 alias（``T`` / ``天``）做全文 substring
# replace 会把所有命中字符抹掉——``今天天气好`` + stop=``天`` 会留下
# ``今  气好``，``_extract_keywords`` 抽到的 n-gram 只剩 ``气好`` 一个，悄无声
# 息地把 BM25/记忆召回的 recall 砍光。漏掉一个真单字别名是次要损失，比起
# 把每条 fact 都腌一遍完全可接受。
_MIN_STOP_NAME_LEN = 2


def split_nickname_aliases(raw) -> list[str]:
    """Split a ``昵称`` field (comma/space-separated) into individual aliases.

    Empty / whitespace tokens are dropped. Always returns a list (never None).
    """
    if not raw:
        return []
    return [s.strip() for s in _NICKNAME_SPLIT_RE.split(str(raw)) if s.strip()]


def _assemble_stop_names(
    master_name: str | None,
    her_name: str | None,
    master_basic: dict | None,
    catgirl_data: dict | None,
    lanlan_name: str | None,
) -> list[str]:
    target = lanlan_name or her_name
    names: list[str] = []
    if master_name:
        names.append(str(master_name))
    if target:
        names.append(str(target))
    if isinstance(master_basic, dict):
        names.extend(split_nickname_aliases(master_basic.get('昵称', '')))
    if target and isinstance(catgirl_data, dict):
        char_cfg = catgirl_data.get(target)
        if isinstance(char_cfg, dict):
            names.extend(split_nickname_aliases(char_cfg.get('昵称', '')))
    seen: set[str] = set()
    unique: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            unique.append(n)
    # Longest-first so substring replace doesn't leave fragments of longer aliases.
    unique.sort(key=len, reverse=True)
    return unique


def collect_stop_names(config_manager, lanlan_name: str | None = None) -> list[str]:
    """Sync: master + master_nicknames + lanlan + lanlan_nicknames.

    ``lanlan_name`` defaults to the current catgirl when ``None``.
    Failures (config corruption, etc.) degrade silently to ``[]`` so the
    caller's keyword layer keeps working — losing stop-name stripping is
    strictly less harmful than crashing the recall path.
    """
    try:
        master_name, her_name, master_basic, catgirl_data, *_ = (
            config_manager.get_character_data()
        )
    except Exception:
        return []
    return _assemble_stop_names(
        master_name, her_name, master_basic, catgirl_data, lanlan_name,
    )


async def acollect_stop_names(
    config_manager, lanlan_name: str | None = None,
) -> list[str]:
    """Async twin of :func:`collect_stop_names`."""
    try:
        master_name, her_name, master_basic, catgirl_data, *_ = (
            await config_manager.aget_character_data()
        )
    except Exception:
        return []
    return _assemble_stop_names(
        master_name, her_name, master_basic, catgirl_data, lanlan_name,
    )


def strip_stop_names(text: str, stop_names: list[str] | None) -> str:
    """Remove every ``stop_name`` occurrence from ``text``.

    Names are replaced with a single space (not empty) so that
    ``_extract_keywords`` ' tokenizer sees a clean separator instead of
    merging the surrounding characters into a fake n-gram. Caller is
    expected to pass ``stop_names`` ordered longest-first
    (``collect_stop_names`` already guarantees this).

    Per-alias strategy (Codex PR-971 P2):
      * len < 2 → skip. A single-character alias (``T`` or ``天``) under
        full-text substring replace would wipe every matching character —
        devastating for ``_extract_keywords``' n-gram splitting; failing to
        strip one genuine single-char alias is far milder than silently
        corroding the entire fact corpus.
      * Pure-Latin aliases (``Tony`` / ``Al``) → word-boundary replace,
        otherwise ``Al`` would cut ``Algorithm`` into `` gorithm``. The
        boundary checks ascii explicitly with ``[A-Za-z0-9_]`` look-around,
        because ``\b`` in Python's default Unicode mode counts CJK as
        word-chars and fails (``\bTony\b`` doesn't match inside
        ``今天Tony来了``).
      * CJK / mixed-script aliases (``T酱`` / ``小天``) → still substring
        replace. CJK has no word-boundary concept, and a CJK string of >= 2
        chars is specific enough that substring collateral damage is
        vanishingly rare.
    """  # noqa: DOCSTRING_CJK
    if not text or not stop_names:
        return text
    out = text
    for n in stop_names:
        if not n or len(n) < _MIN_STOP_NAME_LEN:
            continue
        if _LATIN_ALIAS_RE.fullmatch(n):
            pattern = (
                r"(?<![A-Za-z0-9_])"
                + re.escape(n)
                + r"(?![A-Za-z0-9_])"
            )
            out = re.sub(pattern, ' ', out, flags=re.IGNORECASE)
        else:
            out = out.replace(n, ' ')
    return out
