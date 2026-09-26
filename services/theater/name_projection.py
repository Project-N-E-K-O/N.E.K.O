"""把作者姓名安全地单次投影为运行时姓名。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import re
from typing import Any, Iterable


def _source_pattern(source: str) -> str:
    """为容易嵌入普通词的姓名增加字符边界。"""  # noqa: DOCSTRING_CJK

    escaped = re.escape(source)
    if len(source) == 1 and (source.isalnum() or source == "_"):
        # 单字姓名只允许独立出现；否则“林”会改坏“森林”等任意复合词。
        return rf"(?<!\w){escaped}(?!\w)"
    if source.isascii():
        # 拉丁姓名允许空格和连字符，但不能把 May 从 Maybe 中截出来。
        left = r"(?<![A-Za-z0-9_])" if source[0].isalnum() or source[0] == "_" else ""
        right = r"(?![A-Za-z0-9_])" if source[-1].isalnum() or source[-1] == "_" else ""
        return f"{left}{escaped}{right}"
    return escaped


def replace_names(value: Any, replacements: Iterable[tuple[Any, Any]]) -> str:
    """按最长姓名优先，一次替换全部非空来源姓名。"""  # noqa: DOCSTRING_CJK

    text = str(value or "")
    mapping: dict[str, str] = {}
    for source, target in replacements:
        source_text = str(source or "")
        target_text = str(target or "")
        # 未改名的较长姓名也必须参与最长匹配，否则“小明→阿晨”会误改“小明月”。
        if source_text:
            if source_text in mapping and mapping[source_text] != target_text:
                raise ValueError("conflicting_name_replacement")
            mapping[source_text] = target_text
    if not mapping:
        return text
    pattern = re.compile("|".join(
        _source_pattern(source)
        for source in sorted(mapping, key=len, reverse=True)
    ))
    return pattern.sub(lambda match: mapping[match.group(0)], text)


__all__ = ["replace_names"]
