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

"""Shared helpers for minigame prompt modules (soccer, badminton).

config/prompts deliberately keeps two locale-normalization schemes side by side.
This module owns the SHORT-locale normalizer (``_normalize_prompt_lang``), which
collapses every Chinese variant to ``zh`` and is used by soccer plus every
system/pregame prompt. Badminton quick-lines instead use the FULL-locale
normalizer (``normalize_badminton_prompt_locale`` in prompts_badminton), which
keeps ``zh-CN`` and ``zh-TW`` apart. Do not merge the two schemes: collapsing
full locales back to short would regress the Traditional Chinese fallbacks.

See docs/contributing/developer-notes.md #7 and PR #2000.
"""

from config.prompts.prompts_sys import _loc


# SHORT-locale normalizer: collapses every Chinese variant to zh (soccer + all
# system/pregame prompts). Deliberately NOT the same as prompts_badminton.
# normalize_badminton_prompt_locale, which keeps zh-CN and zh-TW apart for the
# badminton quick-lines. See docs/contributing/developer-notes.md #7 and PR #2000.
def _normalize_prompt_lang(lang: str | None) -> str:
    value = str(lang or "").strip().lower().replace("_", "-")
    if not value:
        # Stays "zh" intentionally: the soccer/game module hardcodes
        # Chinese-flavored helpers (e.g. fullwidth "；" in
        # ``_apply_soccer_anger_pressure_cap``) and helpers such as
        # ``_apply_soccer_anger_pressure_cap`` don't accept a language
        # parameter at all. Module-internal default is Chinese; cross-module
        # fallback (resolve_global_language) is English.
        return "zh"
    if value.startswith("zh") or value in {"schinese", "tchinese"}:
        return "zh"
    if value.startswith("ja") or value == "japanese":
        return "ja"
    if value.startswith("ko") or value in {"korean", "koreana"}:
        return "ko"
    if value.startswith("ru") or value == "russian":
        return "ru"
    if value.startswith("es") or value in {"spanish", "latam"}:
        return "es"
    if value.startswith("pt") or value in {"portuguese", "brazilian"}:
        return "pt"
    if value.startswith("en") or value == "english":
        return "en"
    return "en"


def _localized_template(templates: dict[str, str], lang: str | None) -> str:
    return _loc(templates, _normalize_prompt_lang(lang))


# 开局上下文输入水印：pregame 的近期记录 + 启动参数走独立 HumanMessage（裸 JSON），
# 用收尾水印标出数据块边界，让模型分清上面那块是注入输入而非指令。逐 locale 保留中文
# （与 prompts_minigame_route.py 的成对水印对齐），内部禁冒号破折号。
PREGAME_CONTEXT_INPUT_WATERMARK = "======以上为开局近期记录与启动参数======"
