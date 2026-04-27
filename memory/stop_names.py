# -*- coding: utf-8 -*-
"""
Stop-name helpers for the memory module's keyword / BM25 / extraction layer.

Why this exists: ``master_name``、``lanlan_name`` 以及它们各自的 ``昵称``
几乎在每一轮对话里都会出现——一旦把它们也喂给 ``_extract_keywords`` /
``_is_mentioned`` / FTS5 BM25，这些 token 会主导关键词重叠或检索得分，
触发大量误命中（无关 fact 被判定 "mentioned"、dedup 误判相似、矛盾检测
误报）。统一在调用关键词层之前剥离这些 stop-name，避免无效匹配。

Design notes:
- 入口集中在 ``collect_stop_names`` / ``acollect_stop_names``，从
  ``ConfigManager.get_character_data`` 取 ``主人.档案名`` + ``主人.昵称``
  与给定 ``lanlan_name`` 自身 + 该角色的 ``昵称``。``lanlan_name`` 缺省
  退回 "当前猫娘"，因为部分调用点只关心当前活跃角色。
- ``昵称`` 字段是逗号分隔字符串（中英文标点皆可），统一拆成单条别名。
- 列表按长度倒序去重——substring replace 时长 alias 优先匹配，避免
  ``T酱`` 先剥离时把 ``小T酱`` 截断成 ``小``。
- ``strip_stop_names`` 是 substring replace；CJK / 短拉丁名足够用，
  长拉丁名想做 word-boundary 留给后续按需扩展。
"""
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
      * len < 2 → 跳过。单字符 alias（``T`` 或 ``天``）做全文 substring
        replace 会把所有命中字符抹掉，对 ``_extract_keywords`` 的 n-gram
        切分是毁灭性的；漏剥一个真单字别名远比悄无声息腐蚀全量 fact 文本
        要轻。
      * 纯拉丁 alias (``Tony`` / ``Al``) → word-boundary 替换，否则
        ``Al`` 会把 ``Algorithm`` 截成 `` gorithm``。boundary 用显式
        ``[A-Za-z0-9_]`` 前后查 ascii，避免 ``\\b`` 在 Python 默认
        Unicode 模式下把 CJK 算成 word-char 而失效（``\\bTony\\b`` 在
        ``今天Tony来了`` 里不命中）。
      * CJK / 混合脚本 alias (``T酱`` / ``小天``) → 仍走 substring
        replace。CJK 没有 word boundary 概念，且 ≥2 字符的 CJK 串足够
        specific，substring 误伤 vanishingly rare。
    """
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
