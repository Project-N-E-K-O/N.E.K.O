"""Shared helpers for the background topic-hook package."""
from __future__ import annotations

import re
from typing import Any


# ⚠️ 简繁都要列。这两张表是从话题文本里剔虚词、再切单字 + bigram 算相似度用的。
# 缺繁体不会 crash、也不是 0 命中，而是**安静的偏移**：所有繁中话题都会共享
# 「這 / 個 / 還 / 嗎 / 與」这几个高频单元，两两之间的相似度被系统性抬高，
# 反复读检测因此过度激进。实测 topic_units('這個遊戲還在更新嗎') 会多出
# 這 / 個 / 嗎 / 還 / 這個 / 新嗎 / 還更 等纯虚词单元（9 → 15 个）。
#
# ⚠️⚠️ **「著」故意不收**，尽管它是简体体标记「着」的繁体对应。原因是两种字形
# 里的分工不同：简体用「着」当体标记、「著」当词汇字（著名 / 著作），所以基线
# 只收了「着」；繁体的「著」身兼两职，字符级分不开。收了它会把「著名 / 著作」
# 的真实内容一起削掉——实测「著名景點推薦」vs「著名景點清單」的 jaccard 从
# 0.47 掉到 0.38，跨过 0.6 去重阈值的判断随之变化，而且**简体侧同样受损**
# （「著名景点推荐」也含「著」），等于把改动溢出到 zh-TW 范围之外（Codex P2）。
# 取舍：宁可漏掉一个虚词（繁体的体标记「著」留在单元里），也不要削掉真实内容。
#
# ZH_LINK_STOP_CHARS 按设计不含指示代词（這/那/我/你/他/她/它），只补虚词侧。
ZH_TOPIC_STOP_CHARS = set("的一是在不了和就都而及与與着或吗嗎呢啊吧呀也很还還再又这這那我你他她它")
ZH_LINK_STOP_CHARS = set("的一是在不了和就都而及与與着或吗嗎呢啊吧呀也很还還再又")


def clean_text(value: Any, *, limit: int | None = 120) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    if limit is not None and len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def is_zh_lang(lang: str | None) -> bool:
    return str(lang or "").strip().lower().startswith("zh")


def topic_units(
    text: str,
    *,
    limit: int = 120,
    stop_chars: set[str] | None = None,
    include_cjk_bigrams: bool = True,
) -> set[str]:
    cleaned = clean_text(text, limit=limit).lower()
    effective_stop_chars = ZH_TOPIC_STOP_CHARS if stop_chars is None else stop_chars
    units = {
        token
        for token in re.findall(
            r"[a-z0-9]{3,}|[\u0400-\u04ff]{3,}|[\uac00-\ud7af]{2,}|[\u3040-\u30ffー]{2,}",
            cleaned,
        )
        if token
    }
    chars = [
        char
        for char in cleaned
        if "\u4e00" <= char <= "\u9fff" and char not in effective_stop_chars
    ]
    units.update(chars)
    if include_cjk_bigrams:
        for idx in range(len(chars) - 1):
            units.add(chars[idx] + chars[idx + 1])
    return units
