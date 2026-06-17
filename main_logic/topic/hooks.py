"""Topic-hook prompt helpers for proactive chat.

This module intentionally does not schedule, persist, or deliver anything.
It only turns already-approved proactive candidates into a compact prompt
section that the existing /api/proactive_chat Phase 2 path can consume.

Prompt placement contract:
* reflection follow-up topics render here and are appended to memory_context,
  next to the long-term conversation history they extend.
* open_threads stay in the activity-state section, close to live state/tone and
  the decision rules. Do not merge them back into this memory-cue section: they
  are recent unfinished semantic threads, not old reminiscence.
* background deep-topic hooks are delivered through build_topic_hook_callback
  in delivery.py, not through this helper.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from main_logic.topic.common import clean_text


# Deliberately encouraging, not discouraging: paired with Phase 2's repeated
# anti-repeat warnings, weak/negative wording made the model treat callbacks as
# "high repeat risk" and skip them. Frame old topics as welcome, natural memory
# cues. Keep the rendering intentionally quieter than major ====== sections:
# memory cues should be available near conversation history, not compete with
# recent-chat dedup or activity-state decision blocks.
_INTRO_ZH = "可自然想起的旧话题：\n\n以下旧话题距今较久，适合自然回忆与跟进；只在能顺手接、像随口想起时轻轻带出，别硬聊。"
_INTRO_ZH_TW = "可自然想起的舊話題：\n\n以下舊話題距今較久，適合自然回憶與跟進；只在能順手接、像隨口想起時輕輕帶出，別硬聊。"
_INTRO_EN = "Older topics that may come to mind:\n\nThe older topics below are far enough back for natural reminiscence or follow-up; use one only when it flows easily, as if it just came to mind."
_INTRO_JA = "自然に思い出せる古い話題：\n\n以下は以前の会話で出た古い話題です。自然に流れるときだけ、ふと思い出したように軽く切り出してください。"
_INTRO_KO = "자연스럽게 떠올릴 오래된 화제:\n\n아래는 이전 대화에서 나온 오래된 화제입니다. 자연스럽게 이어질 때만 문득 떠올린 듯 가볍게 꺼내세요."
_INTRO_ES = "Temas antiguos que pueden surgir:\n\nLos temas antiguos de abajo vienen de conversaciones previas; usa uno solo si fluye con naturalidad, como si acabara de ocurrírsete."
_INTRO_PT = "Temas antigos que podem voltar:\n\nOs temas antigos abaixo vêm de conversas anteriores; use apenas um quando fluir naturalmente, como se tivesse acabado de lembrar."
_INTRO_RU = "Старые темы, которые можно вспомнить:\n\nСтарые темы ниже достаточно давние для естественного возврата; используй одну только если она легко ложится в разговор."

_INTROS = {
    "zh": _INTRO_ZH,
    "zh-CN": _INTRO_ZH,
    "zh-TW": _INTRO_ZH_TW,
    "en": _INTRO_EN,
    "ja": _INTRO_JA,
    "ko": _INTRO_KO,
    "es": _INTRO_ES,
    "pt": _INTRO_PT,
    "ru": _INTRO_RU,
}

_LABELS = {
    "zh": "较久前的回忆线索",
    "zh-CN": "较久前的回忆线索",
    "zh-TW": "較久前的回憶線索",
    "en": "Older memory cue",
    "ja": "古い記憶の手がかり",
    "ko": "오래된 기억 단서",
    "es": "Pista de memoria antigua",
    "pt": "Pista de memória antiga",
    "ru": "Давняя подсказка памяти",
}

def _lang_key(lang: str) -> str:
    raw = (lang or "").strip()
    if raw in _LABELS:
        return raw
    if raw.lower().startswith("zh"):
        return "zh"
    short = raw.split("-", 1)[0].lower()
    if short in _LABELS:
        return short
    return "en"


def _iter_followup_texts(followup_topics: Iterable[Mapping[str, Any]] | None) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()
    for topic in followup_topics or []:
        if not isinstance(topic, Mapping):
            continue
        text = clean_text(topic.get("text"))
        if not text or text in seen:
            continue
        seen.add(text)
        texts.append(text)
    return texts


def build_topic_hook_prompt(
    lang: str,
    *,
    followup_topics: Iterable[Mapping[str, Any]] | None = None,
    max_items: int = 3,
) -> str:
    """Render old reflection follow-up topics for the proactive prompt.

    The output is deliberately a prompt section, not final copy. Phase 2 still
    owns character voice, timing, and whether to pass. This helper renders only
    old reflection follow-ups; open_threads stay in the activity-state section,
    and the background topic pool delivers through build_topic_hook_callback.
    """
    key = _lang_key(lang)
    label = _LABELS.get(key, _LABELS["en"])
    intro = _INTROS.get(key, _INTROS["en"])

    memory_texts = _iter_followup_texts(followup_topics)[:max_items]
    if not memory_texts:
        return ""

    lines = [intro]
    for text in memory_texts:
        lines.append(f"- {label}: {text}")
    return "\n".join(lines) + "\n"
