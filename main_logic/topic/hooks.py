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
    TOPIC_COMMITMENT_CUE_INTROS,
    TOPIC_COMMITMENT_CUE_LABELS,
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


def commitment_cue_text(commitment: Mapping[str, Any] | None) -> str:
    """The text of a due promise as the cue renders it; "" when there is none."""
    if not isinstance(commitment, Mapping):
        return ""
    return clean_text(commitment.get("text"))


def build_topic_hook_prompt(
    lang: str,
    *,
    followup_topics: Iterable[Mapping[str, Any]] | None = None,
    commitment: Mapping[str, Any] | None = None,
    max_items: int = 3,
) -> str:
    """Render old reflection follow-up topics for the proactive prompt.

    The output is deliberately a prompt section, not final copy. Phase 2 still
    owns character voice, timing, and whether to pass. This helper renders only
    old reflection follow-ups; open_threads stay in the activity-state section,
    and the background topic pool delivers through build_topic_hook_callback.

    ``commitment`` (``{text, due}``: a promise of the character's that comes
    due soon) renders first, under its own intro: it is worth leading with,
    where the old topics are only there to pick up.
    """
    key = _lang_key(lang)
    blocks: list[list[str]] = []

    promise = commitment_cue_text(commitment)
    if promise:
        label = TOPIC_COMMITMENT_CUE_LABELS.get(key, TOPIC_COMMITMENT_CUE_LABELS["en"])
        intro = TOPIC_COMMITMENT_CUE_INTROS.get(key, TOPIC_COMMITMENT_CUE_INTROS["en"])
        due = clean_text(commitment.get("due"), limit=None)
        blocks.append([intro, f"- {label.format(due=due)}: {promise}"])

    memory_texts = _iter_followup_texts(followup_topics)[:max_items]
    if memory_texts:
        label = TOPIC_MEMORY_CUE_LABELS.get(key, TOPIC_MEMORY_CUE_LABELS["en"])
        intro = TOPIC_MEMORY_CUE_INTROS.get(key, TOPIC_MEMORY_CUE_INTROS["en"])
        blocks.append([intro] + [f"- {label}: {text}" for text in memory_texts])

    if not blocks:
        return ""
    return "\n".join(line for block in blocks for line in block) + "\n"
