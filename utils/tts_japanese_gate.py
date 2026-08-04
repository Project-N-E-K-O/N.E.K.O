# -*- coding: utf-8 -*-
"""Ensure TTS speaks Japanese for JA voices / dual-language mode.

Chat-box feature paths (news, proactive, mirror, poke leftovers) often produce
Chinese. Translate to Japanese immediately before enqueueing TTS — without
changing the main LLM dual-tag protocol.
"""
from __future__ import annotations

from utils.bilingual_speech import _has_kana, japanese_only_for_tts
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")


def tts_text_looks_japanese(text: str) -> bool:
    """True when the string already has kana (safe for JA neural voices)."""
    return _has_kana(text or "")


async def ensure_japanese_tts_text(text: str) -> str:
    """Return Japanese suitable for TTS; translate when the text has no kana.

    If the string already mixes JA/ZH clauses, keep only kana-bearing clauses.
    Pure Chinese / English → machine-translate to ``ja``.
    """
    raw = str(text or "").strip()
    if not raw:
        return ""

    ja_only = japanese_only_for_tts(raw)
    if ja_only.strip():
        return ja_only

    try:
        from utils.language_utils import translate_text

        translated, _ = await translate_text(raw, "ja", source_lang=None)
        out = str(translated or "").strip()
        if out and tts_text_looks_japanese(out):
            return out
        if out and out != raw:
            # Translator returned something without kana (e.g. katakana-less
            # English loanwords only) — still prefer it over Chinese.
            return out
    except Exception as exc:
        logger.warning("ensure_japanese_tts_text: translate failed: %s", exc)
    return ""


async def ensure_chinese_display_text(text: str) -> str:
    """Translate speech text to Chinese for chat when the bubble would be empty."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    from utils.bilingual_speech import chinese_only_for_display

    zh = chinese_only_for_display(raw)
    if zh.strip() and not _has_kana(zh):
        return zh.strip()
    try:
        from utils.language_utils import translate_text

        translated, _ = await translate_text(raw, "zh", source_lang=None)
        out = str(translated or "").strip()
        return out or raw
    except Exception as exc:
        logger.warning("ensure_chinese_display_text: translate failed: %s", exc)
        return raw
