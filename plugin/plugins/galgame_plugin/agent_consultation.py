"""Cat consultation decision + prompt assembly for GameLLMAgent.

GameLLM consults the catgirl at narrative decision points (visible choices,
scene transitions, dialogue accumulation). The catgirl reply is **reference
information**, not a directive — GameLLM keeps full decision authority.

The plan-level integration is fire-and-forget: the agent sends a consultation
prompt through ``AgentMessageRouter`` and continues immediately. Replies arrive
through the existing inbound message channel and are merged into
``shared["cat_opinions"]`` by :func:`inject_cat_opinion`. ``cat_opinions`` is
capped at ``MAX_CAT_OPINIONS`` so the strategy context cannot grow unbounded.

This module is intentionally pure (no I/O, no agent reference) so it is easy to
unit-test. ``GameLLMAgent`` is expected to call ``decide_consultation`` from
its ``tick`` loop, build the prompt with ``build_consult_prompt``, and dispatch
the message via its existing outbound channel.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .llm_prompts import (
    CONSULT_CAT_CHOICE_QUESTION_TEMPLATE,
    CONSULT_CAT_PROMPT_TEMPLATE,
    CONSULT_CAT_SCENE_CHANGE_QUESTION_TEMPLATE,
    CONSULT_CAT_STORY_PROGRESS_QUESTION_TEMPLATE,
)


CONSULT_COOLDOWN_SECONDS: float = 30.0
"""Lower bound on time between consultations (any reason)."""

CONSULT_PROGRESS_LINE_THRESHOLD: int = 8
"""Dialogue lines since last consult before the progress reason fires."""

MAX_CAT_OPINIONS: int = 5
"""Cap on retained ``shared['cat_opinions']`` entries — older entries drop."""

MAX_RECENT_LINES_IN_PROMPT: int = 5
"""When building a progress prompt, fold at most this many recent lines."""

CONSULT_REASON_CHOICE: str = "choice"
CONSULT_REASON_SCENE_CHANGE: str = "scene_change"
CONSULT_REASON_STORY_PROGRESS: str = "story_progress"

_REASON_PRIORITY: tuple[str, ...] = (
    CONSULT_REASON_CHOICE,
    CONSULT_REASON_SCENE_CHANGE,
    CONSULT_REASON_STORY_PROGRESS,
)


@dataclass(frozen=True, slots=True)
class ConsultInputs:
    """Snapshot of the data needed to make a consultation decision."""

    character_mode: str = "off"
    character_fixed_name: str = ""
    scene_id: str = ""
    visible_choices: tuple[str, ...] = ()
    scene_changed: bool = False
    lines_since_last_consult: int = 0
    now: float = 0.0
    last_consult_ts: float = 0.0
    profile_known: bool = False


@dataclass(frozen=True, slots=True)
class ConsultDecision:
    """Outcome of :func:`decide_consultation`."""

    should_consult: bool
    reason: str = ""
    skip_reason: str = ""
    character_name: str = ""

    @property
    def consulted(self) -> bool:
        return self.should_consult


@dataclass(frozen=True, slots=True)
class CatOpinion:
    """A single recorded catgirl reply, returned by :func:`inject_cat_opinion`."""

    opinion: str
    scene_id: str
    reason: str
    ts: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "opinion": self.opinion,
            "scene_id": self.scene_id,
            "reason": self.reason,
            "ts": self.ts,
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def decide_consultation(inputs: ConsultInputs) -> ConsultDecision:
    """Decide whether to consult the catgirl, and why.

    Order of checks (highest priority first):

    1. Mode gate — only ``fixed`` mode consults; ``off`` never does.
    2. Profile gate — refuse when the fixed character is not in the profile
       set (the consultation prompt depends on its voice block).
    3. Cooldown — refuse when the last consult was < ``CONSULT_COOLDOWN_SECONDS``
       ago. Cooldown wins over every trigger to prevent fast-forward flooding.
    4. Trigger — first matching reason among choice / scene change / progress.
    """

    if (inputs.character_mode or "").strip().lower() != "fixed":
        return ConsultDecision(False, skip_reason="mode_off")
    if not (inputs.character_fixed_name or "").strip():
        return ConsultDecision(False, skip_reason="no_fixed_character")
    if not inputs.profile_known:
        return ConsultDecision(
            False,
            skip_reason="profile_missing",
            character_name=inputs.character_fixed_name,
        )

    elapsed = max(0.0, float(inputs.now) - float(inputs.last_consult_ts))
    if inputs.last_consult_ts > 0 and elapsed < CONSULT_COOLDOWN_SECONDS:
        return ConsultDecision(
            False,
            skip_reason="cooldown",
            character_name=inputs.character_fixed_name,
        )

    reason = _select_reason(inputs)
    if not reason:
        return ConsultDecision(
            False,
            skip_reason="no_trigger",
            character_name=inputs.character_fixed_name,
        )
    return ConsultDecision(
        True,
        reason=reason,
        character_name=inputs.character_fixed_name,
    )


def _select_reason(inputs: ConsultInputs) -> str:
    for candidate in _REASON_PRIORITY:
        if _reason_active(candidate, inputs):
            return candidate
    return ""


def _reason_active(reason: str, inputs: ConsultInputs) -> bool:
    if reason == CONSULT_REASON_CHOICE:
        return bool(inputs.visible_choices)
    if reason == CONSULT_REASON_SCENE_CHANGE:
        return bool(inputs.scene_changed)
    if reason == CONSULT_REASON_STORY_PROGRESS:
        return inputs.lines_since_last_consult >= CONSULT_PROGRESS_LINE_THRESHOLD
    return False


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def build_consult_prompt(
    *,
    reason: str,
    character_name: str,
    character_voice_summary: str,
    scene_summary: str,
    visible_choices: tuple[str, ...] = (),
    recent_lines: tuple[str, ...] = (),
) -> str:
    """Render the consultation prompt using the shared templates."""

    question = _build_consult_question(
        reason=reason,
        character_name=character_name,
        visible_choices=visible_choices,
        recent_lines=recent_lines,
    )
    return CONSULT_CAT_PROMPT_TEMPLATE.format(
        scene_summary=(scene_summary or "").strip() or "（暂无场景摘要）",
        consult_question=question,
        character_name=(character_name or "").strip() or "（未指定角色）",
        character_voice_summary=(character_voice_summary or "").strip()
        or "（角色说话方式未提供）",
    )


def _build_consult_question(
    *,
    reason: str,
    character_name: str,
    visible_choices: tuple[str, ...],
    recent_lines: tuple[str, ...],
) -> str:
    name = (character_name or "").strip() or "（未指定角色）"
    if reason == CONSULT_REASON_CHOICE:
        return CONSULT_CAT_CHOICE_QUESTION_TEMPLATE.format(
            choices="；".join(choice for choice in visible_choices if choice)
            or "（未提供选项）",
            character_name=name,
        )
    if reason == CONSULT_REASON_SCENE_CHANGE:
        return CONSULT_CAT_SCENE_CHANGE_QUESTION_TEMPLATE.format(
            character_name=name
        )
    if reason == CONSULT_REASON_STORY_PROGRESS:
        recent_text = "\n".join(
            line.strip()
            for line in recent_lines[-MAX_RECENT_LINES_IN_PROMPT:]
            if (line or "").strip()
        ) or "（暂无最近台词）"
        return CONSULT_CAT_STORY_PROGRESS_QUESTION_TEMPLATE.format(
            recent_lines=recent_text
        )
    return f"作为 {name}，你有什么想说的？"


def summarize_character_voice(profile: dict[str, Any] | None) -> str:
    """Compact voice summary suitable for the consultation prompt header."""
    if not isinstance(profile, dict):
        return ""
    voice = profile.get("character_voice")
    if not isinstance(voice, dict):
        return ""
    traits = voice.get("core_traits")
    if not isinstance(traits, list):
        return ""
    parts: list[str] = []
    for trait_obj in traits[:2]:
        if not isinstance(trait_obj, dict):
            continue
        trait = str(trait_obj.get("trait") or "").strip()
        speech = str(trait_obj.get("speech_effect") or "").strip()
        if trait and speech:
            parts.append(f"{trait}→{speech}")
    pronoun = str(voice.get("first_person_pronoun") or "").strip()
    if pronoun:
        parts.append(f"自称「{pronoun}」")
    return "；".join(parts)


# ---------------------------------------------------------------------------
# Opinion injection
# ---------------------------------------------------------------------------


def inject_cat_opinion(
    shared: dict[str, Any],
    opinion: str,
    *,
    scene_id: str = "",
    reason: str = "",
    ts: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> CatOpinion | None:
    """Append a catgirl opinion to ``shared['cat_opinions']`` (capped)."""
    text = (opinion or "").strip()
    if not text:
        return None
    record = CatOpinion(
        opinion=text,
        scene_id=(scene_id or "").strip(),
        reason=(reason or "").strip(),
        ts=float(ts if ts is not None else time.time()),
        metadata=dict(metadata or {}),
    )
    queue = shared.get("cat_opinions")
    if not isinstance(queue, list):
        queue = []
    queue = list(queue)
    queue.append(record.to_dict())
    if len(queue) > MAX_CAT_OPINIONS:
        queue = queue[-MAX_CAT_OPINIONS:]
    shared["cat_opinions"] = queue
    return record


def get_recent_cat_opinions(
    shared: dict[str, Any], n: int = MAX_CAT_OPINIONS
) -> list[dict[str, Any]]:
    if n <= 0:
        return []
    queue = shared.get("cat_opinions")
    if not isinstance(queue, list):
        return []
    return [dict(entry) for entry in queue[-n:]]


def render_cat_opinions_for_strategy(
    shared: dict[str, Any], n: int = MAX_CAT_OPINIONS
) -> str:
    """Render recent opinions as a strategy-context block.

    The header is intentional: cat replies are high-priority fixed-character
    POV advice, but GameLLM still keeps final decision authority.
    """
    recent = get_recent_cat_opinions(shared, n)
    if not recent:
        return ""
    lines = ["[Fixed-character POV advice: high-priority reference, not a command]"]
    for entry in recent:
        opinion = str(entry.get("opinion") or "").strip()
        if not opinion:
            continue
        reason = str(entry.get("reason") or "").strip()
        tag = f"（{reason}）" if reason else ""
        lines.append(f"· {opinion}{tag}")
    return "\n".join(lines) if len(lines) > 1 else ""


__all__ = [
    "ConsultInputs",
    "ConsultDecision",
    "CatOpinion",
    "decide_consultation",
    "build_consult_prompt",
    "summarize_character_voice",
    "inject_cat_opinion",
    "get_recent_cat_opinions",
    "render_cat_opinions_for_strategy",
    "CONSULT_COOLDOWN_SECONDS",
    "CONSULT_PROGRESS_LINE_THRESHOLD",
    "MAX_CAT_OPINIONS",
    "MAX_RECENT_LINES_IN_PROMPT",
    "CONSULT_REASON_CHOICE",
    "CONSULT_REASON_SCENE_CHANGE",
    "CONSULT_REASON_STORY_PROGRESS",
]
