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

"""The single locale normalizer for every prompt module.

Prompt modules used to carry six hand-rolled normalizers that disagreed on
empty-input defaults, Steam alias handling, whitespace, and whether the
Traditional Chinese branch survived. This module owns the one implementation;
the differences that were deliberate are now explicit keyword arguments.

Three axes cover every prompt module's needs:

``default``
    What an empty / missing language resolves to. Most prompt modules want
    ``"en"``; the minigame modules intentionally default to Chinese because
    their helper text is hardcoded Chinese-flavored.

``simplified``
    The dict key Simplified Chinese is stored under. Most prompt dicts use the
    short ``"zh"``; the badminton quick-lines table keys Simplified Chinese as
    the full ``"zh-CN"``.

``keep_traditional``
    Whether ``zh-TW`` survives as its own key. Pass ``False`` for prompt
    modules whose dicts have no ``'zh-TW'`` templates yet — collapsing to
    Simplified there keeps the module's intended locale key explicit. The
    shared ``_loc`` resolver independently uses Simplified Chinese as its
    safety fallback when a Chinese variant key is absent.
    Flip a module to ``True`` in the same change that adds its ``'zh-TW'``
    templates, never before. See issue #2500.

Unlike ``config._runtime.normalize_language_code``, this normalizer is
self-contained: it never routes through the runtime-injected forwarder, so a
bare ``import`` in a test resolves Steam codes exactly the way the running app
does.
"""

from typing import Any

# The runtime locale set, matching static/locales/ and the eight-locale i18n
# rule in docs/contributing/developer-notes.md.
NEKO_CORE_LOCALES = ("zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")

# Non-Chinese locales, matched exactly or as a `<locale>-<region>` prefix.
# Exact matching is what keeps "esperanto" from being read as Spanish.
_SIMPLE_LOCALES = ("en", "ja", "ko", "ru", "es", "pt")

# Markers that pick the Traditional branch out of a zh-* tag.
_TRADITIONAL_MARKERS = ("tw", "hk", "hant")

_LANGUAGE_ALIASES = {
    # Steam store language codes.
    # https://partner.steamgames.com/doc/store/localization/languages
    "schinese": "zh-CN",
    "tchinese": "zh-TW",
    "english": "en",
    "japanese": "ja",
    "koreana": "ko",
    "korean": "ko",
    "russian": "ru",
    "spanish": "es",
    "latam": "es",
    "portuguese": "pt",
    "brazilian": "pt",
    # Chinese spellings.
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-CN",
    "zh-tw": "zh-TW",
    "zh-hk": "zh-TW",
    "zh-hant": "zh-TW",
    # Region spellings of the non-Chinese locales.
    "en-us": "en",
    "ja-jp": "ja",
    "ko-kr": "ko",
    "ru-ru": "ru",
    "es-es": "es",
    "pt-br": "pt",
    "pt-pt": "pt",
}


def normalize_prompt_locale(
    language: Any,
    *,
    default: str = "en",
    simplified: str = "zh",
    keep_traditional: bool = True,
) -> str:
    """Resolve any language input to a prompt-dict key.

    Args:
        language: A BCP-47 tag, a Steam store language code, or anything else.
            ``None``, empty, and whitespace-only values resolve to ``default``.
        default: Returned for empty input. Unrecognized *non-empty* input
            resolves to ``"en"`` instead, which is a separate case: a garbage
            tag is not the same signal as no tag at all.
        simplified: The key Simplified Chinese resolves to.
        keep_traditional: When ``False``, Traditional Chinese resolves to
            ``simplified`` rather than ``"zh-TW"``.

    Returns:
        A prompt-dict key. Chinese resolves to ``simplified`` or ``"zh-TW"``;
        everything else to one of ``_SIMPLE_LOCALES``.
    """
    raw = str(language or "").strip().lower().replace("_", "-")
    if not raw:
        return default

    resolved = _LANGUAGE_ALIASES.get(raw)
    if resolved is None:
        if raw.startswith("zh"):
            # Note: this also catches unrelated tags that merely begin with
            # "zh" (e.g. "zhuang"). Harmless for now — the runtime language set
            # is the eight NEKO_CORE_LOCALES, none of which collide.
            resolved = (
                "zh-TW"
                if any(marker in raw for marker in _TRADITIONAL_MARKERS)
                else "zh-CN"
            )
        else:
            for locale in _SIMPLE_LOCALES:
                if raw == locale or raw.startswith(f"{locale}-"):
                    resolved = locale
                    break
            else:
                return "en"

    if resolved == "zh-CN":
        return simplified
    if resolved == "zh-TW":
        return "zh-TW" if keep_traditional else simplified
    return resolved


def prompt_locale_fallback_key(language: Any) -> str:
    """Return the generic fallback family for a prompt locale.

    Chinese locale tags and aliases fall back to the Simplified Chinese
    family. Missing non-Chinese variants and unknown locales fall back to
    English. The caller remains responsible for selecting the concrete
    Simplified key used by its table (usually ``zh``, occasionally ``zh-CN``).
    """
    normalized = normalize_prompt_locale(
        language,
        default="en",
        simplified="zh",
        keep_traditional=True,
    )
    return "zh" if normalized in {"zh", "zh-TW"} else "en"
