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

from config.prompts.prompts_activity import (
    TOPIC_MEMORY_CUE_INTROS,
    TOPIC_MEMORY_CUE_LABELS,
)
from main_logic.topic.common import clean_text


# Deliberately encouraging, not discouraging: paired with Phase 2's repeated
# anti-repeat warnings, weak/negative wording made the model treat callbacks as
# "high repeat risk" and skip them. Frame old topics as welcome, natural memory
# cues. Keep the rendering intentionally quieter than major ====== sections:
# memory cues should be available near conversation history, not compete with
# recent-chat dedup or activity-state decision blocks.

def _lang_key(lang: str) -> str:
    raw = (lang or "").strip()
    if raw in TOPIC_MEMORY_CUE_LABELS:
        return raw
    if raw.lower().startswith("zh"):
        return "zh"
    short = raw.split("-", 1)[0].lower()
    if short in TOPIC_MEMORY_CUE_LABELS:
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
    label = TOPIC_MEMORY_CUE_LABELS.get(key, TOPIC_MEMORY_CUE_LABELS["en"])
    intro = TOPIC_MEMORY_CUE_INTROS.get(key, TOPIC_MEMORY_CUE_INTROS["en"])

    memory_texts = _iter_followup_texts(followup_topics)[:max_items]
    if not memory_texts:
        return ""

    lines = [intro]
    for text in memory_texts:
        lines.append(f"- {label}: {text}")
    return "\n".join(lines) + "\n"
