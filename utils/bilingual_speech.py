# -*- coding: utf-8 -*-
"""Dual-channel speech: Japanese for TTS, Chinese for chat display.

Protocol (model must emit both, same meaning, JA first)::

    <ja>こんにちは。</ja><zh>你好。</zh>

Streaming-safe for tags. Channel bodies are released on close-tag (or flush)
so script guards can drop Chinese stuffed into ``<ja>`` without chopping
Japanese kanji mid-stream.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Prefer explicit channel tags. Aliases accepted for model drift.
_OPEN_JA = re.compile(r"<\s*(?:ja|jp|japanese)\s*>", re.IGNORECASE)
_CLOSE_JA = re.compile(r"<\s*/\s*(?:ja|jp|japanese)\s*>", re.IGNORECASE)
_OPEN_ZH = re.compile(r"<\s*(?:zh|cn|chinese)\s*>", re.IGNORECASE)
_CLOSE_ZH = re.compile(r"<\s*/\s*(?:zh|cn|chinese)\s*>", re.IGNORECASE)

_ANY_TAG = re.compile(
    r"<\s*/?\s*(?:ja|jp|japanese|zh|cn|chinese)\s*>",
    re.IGNORECASE,
)

# Hiragana + Katakana: reliable signal that a clause is meant to be spoken JP.
_KANA_RE = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")
_HAN_RE = re.compile(r"[\u4E00-\u9FFF]")
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[。！？!?．\n])")


@dataclass(frozen=True)
class BilingualSpeechChunk:
    """One feed()/flush() result."""

    display: str = ""
    tts: str = ""


def _has_kana(text: str) -> bool:
    return bool(_KANA_RE.search(text or ""))


def _has_han(text: str) -> bool:
    return bool(_HAN_RE.search(text or ""))


def japanese_only_for_tts(text: str) -> str:
    """Keep only clauses that contain kana; drop Chinese-only clauses.

    Pure Chinese (no kana) must never reach the Japanese TTS voice.
    Mixed ``こんにちは。你好。`` keeps only the Japanese clause.
    """
    raw = str(text or "")
    if not raw.strip():
        return ""
    if not _has_kana(raw):
        return ""
    parts = _CLAUSE_SPLIT_RE.split(raw)
    if len(parts) <= 1:
        return raw
    kept: list[str] = []
    for part in parts:
        if not part:
            continue
        if _has_kana(part):
            kept.append(part)
    return "".join(kept)


def chinese_only_for_display(text: str) -> str:
    """Prefer Chinese clauses for the chat box; drop kana-bearing JA slips."""
    raw = str(text or "")
    if not raw.strip():
        return ""
    if not _has_kana(raw):
        return raw
    parts = _CLAUSE_SPLIT_RE.split(raw)
    kept: list[str] = []
    for part in parts:
        if not part:
            continue
        if _has_kana(part):
            continue
        kept.append(part)
    cleaned = "".join(kept).strip()
    # If filtering wiped everything (model put JA into <zh>), keep original.
    return cleaned if cleaned else raw


def split_untagged_dual_text(plain: str) -> BilingualSpeechChunk:
    """Best-effort JA/ZH split when the model ignored channel tags."""
    text = str(plain or "")
    if not text.strip():
        return BilingualSpeechChunk()
    tts = japanese_only_for_tts(text)
    display = chinese_only_for_display(text)
    if tts and display == text and _has_kana(text) and _has_han(text):
        parts = _CLAUSE_SPLIT_RE.split(text)
        zh_parts = [p for p in parts if p and not _has_kana(p)]
        display = "".join(zh_parts).strip() or display
    if not display and not tts:
        display = text
    elif not display and tts:
        display = ""
    return BilingualSpeechChunk(display=display, tts=tts)


class BilingualSpeechSplitter:
    """Stream splitter for ``<ja>`` / ``<zh>`` dual-language replies."""

    __slots__ = ("_buf", "_mode", "_saw_channel_tag", "_pending_plain", "_channel_buf")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._buf = ""
        self._mode: str | None = None  # 'ja' | 'zh' | None
        self._saw_channel_tag = False
        self._pending_plain = ""
        self._channel_buf = ""

    def feed(self, text: str) -> BilingualSpeechChunk:
        if not text:
            return BilingualSpeechChunk()
        self._buf += text
        return self._drain(final=False)

    def flush(self) -> BilingualSpeechChunk:
        chunk = self._drain(final=True)
        # Untagged turns → heuristic JA/ZH script split.
        if not self._saw_channel_tag and self._pending_plain:
            plain = self._pending_plain
            self._pending_plain = ""
            self._buf = ""
            self._mode = None
            self._channel_buf = ""
            heuristic = split_untagged_dual_text(plain)
            return BilingualSpeechChunk(
                display=chunk.display + heuristic.display,
                tts=chunk.tts + heuristic.tts,
            )
        self._pending_plain = ""
        self._buf = ""
        self._mode = None
        self._channel_buf = ""
        return chunk

    def _release_channel(self) -> BilingualSpeechChunk:
        body = self._channel_buf
        self._channel_buf = ""
        if not body:
            return BilingualSpeechChunk()
        if self._mode == "ja":
            return BilingualSpeechChunk(tts=japanese_only_for_tts(body))
        if self._mode == "zh":
            return BilingualSpeechChunk(display=chinese_only_for_display(body))
        return BilingualSpeechChunk()

    def _drain(self, *, final: bool) -> BilingualSpeechChunk:
        display_parts: list[str] = []
        tts_parts: list[str] = []

        def _take(emitted: BilingualSpeechChunk) -> None:
            if emitted.display:
                display_parts.append(emitted.display)
            if emitted.tts:
                tts_parts.append(emitted.tts)

        while self._buf:
            if self._mode is None:
                m = _ANY_TAG.search(self._buf)
                if m is None:
                    if final:
                        self._pending_plain += self._buf
                        self._buf = ""
                    else:
                        keep = min(len(self._buf), 24)
                        if len(self._buf) > keep:
                            self._pending_plain += self._buf[:-keep]
                            self._buf = self._buf[-keep:]
                    break

                prefix = self._buf[: m.start()]
                if prefix:
                    self._pending_plain += prefix
                tag = self._buf[m.start() : m.end()]
                self._buf = self._buf[m.end() :]
                self._saw_channel_tag = True
                self._pending_plain = ""
                if _OPEN_JA.match(tag):
                    self._mode = "ja"
                    self._channel_buf = ""
                elif _OPEN_ZH.match(tag):
                    self._mode = "zh"
                    self._channel_buf = ""
                else:
                    self._mode = None
                continue

            close = _CLOSE_JA if self._mode == "ja" else _CLOSE_ZH
            m = close.search(self._buf)
            if m is None:
                if final:
                    self._channel_buf += self._buf
                    self._buf = ""
                    _take(self._release_channel())
                    self._mode = None
                else:
                    keep = min(len(self._buf), 12)
                    if len(self._buf) <= keep:
                        break
                    self._channel_buf += self._buf[:-keep]
                    self._buf = self._buf[-keep:]
                    # Hold until close-tag so script filters see a full clause.
                break

            self._channel_buf += self._buf[: m.start()]
            self._buf = self._buf[m.end() :]
            _take(self._release_channel())
            self._mode = None

        return BilingualSpeechChunk(
            display="".join(display_parts),
            tts="".join(tts_parts),
        )


def strip_bilingual_tags_for_history(text: str) -> str:
    """Keep Chinese display text for memory/history; drop Japanese + tags."""
    if not text:
        return text
    if not _ANY_TAG.search(text):
        return split_untagged_dual_text(text).display or text
    splitter = BilingualSpeechSplitter()
    chunk = splitter.feed(text)
    chunk2 = splitter.flush()
    display = (chunk.display + chunk2.display).strip()
    if display:
        return display
    return _ANY_TAG.sub("", text).strip()


def dual_language_speech_prompt_block() -> str:
    """System-prompt addendum when dual-language speech is enabled."""
    return (
        "<Dual Language Speech>\n"
        "Every reply MUST contain BOTH channels with the SAME meaning:\n"
        "1) First emit Japanese for voice only: <ja>...</ja>\n"
        "2) Then emit Chinese for the chat box only: <zh>...</zh>\n"
        "Example (copy this shape exactly):\n"
        "<ja>今日はいい天気だね。</ja><zh>今天天气不错呢。</zh>\n"
        "Rules:\n"
        "- <ja> and <zh> must express the same intent/content; do not invent different facts.\n"
        "- Put <ja> before <zh>. Keep both concise spoken language.\n"
        "- <ja> MUST be Japanese only (include kana). NEVER put Chinese-only sentences in <ja>.\n"
        "- <zh> MUST be Chinese only. NEVER put Japanese kana in <zh>.\n"
        "- Do NOT output any text outside these two tags.\n"
        "- Never explain the tags. Never mention this dual-language rule.\n"
        "- Never output the same sentence twice outside the tags.\n"
        "</Dual Language Speech>"
    )
