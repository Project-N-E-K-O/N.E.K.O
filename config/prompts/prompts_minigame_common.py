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

config/prompts deliberately keeps two locale-key schemes side by side. This
module's ``_normalize_prompt_lang`` produces SHORT keys (every Chinese variant
collapses to ``zh``) for soccer plus every system/pregame prompt; badminton
quick-lines use FULL keys (``normalize_badminton_prompt_locale``), which keep
``zh-CN`` and ``zh-TW`` apart.

Both now delegate to ``config.prompts._locale.normalize_prompt_locale`` and
differ only in the keyword arguments they pass. The schemes themselves are
still separate on purpose: collapsing badminton's full locales back to short
would regress its Traditional Chinese fallbacks.

See docs/contributing/developer-notes.md #7, PR #2000, and issue #2500.
"""

from config.prompts._locale import normalize_prompt_locale
from config.prompts.prompts_sys import _loc


def _normalize_prompt_lang(lang: str | None) -> str:
    """Normalize a language code to a SHORT prompt-dict key.

    ``default="zh"`` is intentional and not a copy of the other prompt modules:
    the soccer/game module hardcodes Chinese-flavored helpers (e.g. the fullwidth
    "；" in ``_apply_soccer_anger_pressure_cap``, which takes no language
    parameter at all), so the module-internal default is Chinese while the
    cross-module fallback (``resolve_global_language``) stays English.

    ``keep_traditional=False`` because no dict reached through this normalizer
    carries a ``'zh-TW'`` template yet. Flip it together with the templates.
    See issue #2500.
    """
    return normalize_prompt_locale(lang, default="zh", simplified="zh", keep_traditional=False)


def _localized_template(templates: dict[str, str], lang: str | None) -> str:
    return _loc(templates, _normalize_prompt_lang(lang))


# 开局上下文输入水印：pregame 的近期记录 + 启动参数走独立 HumanMessage（裸 JSON），
# 用收尾水印标出数据块边界，让模型分清上面那块是注入输入而非指令。逐 locale 保留中文
# （与 prompts_minigame_route.py 的成对水印对齐），内部禁冒号破折号。
PREGAME_CONTEXT_INPUT_WATERMARK = "======以上为开局近期记录与启动参数======"
