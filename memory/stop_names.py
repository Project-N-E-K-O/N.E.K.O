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
    """Remove every ``stop_name`` occurrence from ``text`` (substring replace).

    Names are replaced with a single space rather than empty string so
    that ``_extract_keywords`` ' tokenizer sees a clean word boundary
    instead of merging the surrounding characters into a fake n-gram.
    Caller is expected to pass ``stop_names`` ordered longest-first
    (``collect_stop_names`` already guarantees this).
    """
    if not text or not stop_names:
        return text
    out = text
    for n in stop_names:
        if not n:
            continue
        out = out.replace(n, ' ')
    return out
