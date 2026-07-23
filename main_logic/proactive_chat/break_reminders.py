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

"""Work-break / anti-slack reminder prompts and LLM delivery."""

import asyncio

from config import (
    PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
)
from config import (
    PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS as PHASE2_OUTPUT_MAX_TOKENS,
)
from config.prompts.prompts_proactive import BEGIN_GENERATE
from config.prompts.prompts_sys import _loc
from main_logic.proactive_chat.state import _record_proactive_chat
from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm_async
from utils.logger_config import get_module_logger
from utils.tokenize import count_tokens

logger = get_module_logger(__name__, "Main")


# ---------- Break-reminder rendering + minimal-Phase-2 delivery ----------
# Two reminder paths emitted by ``main_logic/activity/tracker.py``:
#   * Anti-slack — fired when state transitions focused_work → leisure
#     after a real focus session. Higher priority (transition is more
#     time-sensitive than the cumulative water-break trigger).
#   * Water-break — fired when focused_work accumulator crosses
#     ``work_break_minutes``. 50% of the time, branches into a
#     "rest + game-invite" combo (LLM-generated) that shares the
#     mini-game cooldown so the two channels don't double-deliver.
#
# Both deliveries skip Phase 1 entirely (no source fetching, no
# enabled_modes parsing, no propensity gating). Phase 2 runs with a
# minimal SystemMessage (character_prompt + the env-notice template)
# so the model focuses on the single nudge instead of juggling sources.
# Mirrors ``_maybe_deliver_mini_game_invite`` in shape: try → fall
# through OR skip; never falls through to normal proactive flow when
# a pending exists (must-fire semantics).


def _compose_break_system_prompt(
    character_prompt: str,
    env_notice: str,
) -> str:
    """Prepend the resolved character prompt to a break reminder."""
    if not character_prompt:
        return env_notice
    return f"{character_prompt}\n\n{env_notice}"


def _resolve_break_reminder_label(
    canonical: str | None,
    lang: str,
    fallback_table: dict[str, str],
) -> str:
    """Pick a renderable app label, falling back to a localized generic."""
    label = (canonical or "").strip()
    if label:
        return label
    return fallback_table.get(lang, fallback_table.get("en", ""))


def _render_work_break_prompt(
    *,
    pending,  # WorkBreakPending
    master_name: str,
    lang: str,
) -> tuple[str, str]:
    """Pick a seed + render the regular drink/stretch nudge prompt.

    Returns ``(system_prompt_text, seed)`` so the caller can log / record which
    seed was used. Seed is picked at delivery time (not pinned to the snapshot)
    so consecutive failed-then-retried deliveries naturally rotate the
    suggested action.
    """
    import random as _random

    from config.prompts.prompts_activity import (
        WORK_BREAK_GENERIC_WORK_LABEL,
        WORK_BREAK_REMINDER_PROMPT,
        WORK_BREAK_SEED_HINTS,
    )

    template = WORK_BREAK_REMINDER_PROMPT.get(
        lang,
        WORK_BREAK_REMINDER_PROMPT.get("en", WORK_BREAK_REMINDER_PROMPT["zh"]),
    )
    seeds = WORK_BREAK_SEED_HINTS.get(
        lang,
        WORK_BREAK_SEED_HINTS.get("en", WORK_BREAK_SEED_HINTS["zh"]),
    ) or [""]
    seed = _random.choice(seeds)
    app_label = _resolve_break_reminder_label(
        pending.app,
        lang,
        WORK_BREAK_GENERIC_WORK_LABEL,
    )
    rendered = template.format(
        master=master_name or "",
        app=app_label,
        minutes=pending.minutes,
        seed=seed,
    )
    return rendered, seed


def _render_anti_slack_prompt(
    *,
    pending,  # AntiSlackPending
    master_name: str,
    lang: str,
) -> str:
    """Render the focused→leisure 'back to work' nudge prompt.

    No seed slot — single behaviour, variation comes from prev/new app names +
    minute count + AI persona. Returns the system prompt text.
    """
    from config.prompts.prompts_activity import (
        ANTI_SLACK_REMINDER_PROMPT,
        WORK_BREAK_GENERIC_LEISURE_LABEL,
        WORK_BREAK_GENERIC_WORK_LABEL,
    )

    template = ANTI_SLACK_REMINDER_PROMPT.get(
        lang,
        ANTI_SLACK_REMINDER_PROMPT.get("en", ANTI_SLACK_REMINDER_PROMPT["zh"]),
    )
    prev_app_label = _resolve_break_reminder_label(
        pending.prev_app,
        lang,
        WORK_BREAK_GENERIC_WORK_LABEL,
    )
    new_app_label = _resolve_break_reminder_label(
        pending.new_app,
        lang,
        WORK_BREAK_GENERIC_LEISURE_LABEL,
    )
    return template.format(
        master=master_name or "",
        prev_app=prev_app_label,
        new_app=new_app_label,
        minutes=pending.minutes,
    )


def _render_work_break_game_invite_prompt(
    *,
    pending,  # WorkBreakPending
    game_type: str,
    master_name: str,
    lang: str,
) -> str | None:
    """Render the rest+game-invite combo prompt (50% branch).

    Returns the system prompt text, or None when no template exists for the
    given game_type (caller falls back to the regular water-break branch).
    """
    from config.prompts.prompts_activity import (
        WORK_BREAK_GAME_INVITE_PROMPTS_BY_GAME,
        WORK_BREAK_GENERIC_WORK_LABEL,
    )

    per_lang = WORK_BREAK_GAME_INVITE_PROMPTS_BY_GAME.get(game_type)
    if not per_lang:
        return None
    template = per_lang.get(lang, per_lang.get("en", per_lang.get("zh")))
    if not template:
        return None
    app_label = _resolve_break_reminder_label(
        pending.app,
        lang,
        WORK_BREAK_GENERIC_WORK_LABEL,
    )
    return template.format(
        master=master_name or "",
        app=app_label,
        minutes=pending.minutes,
    )


async def _deliver_break_reminder_via_llm(
    *,
    lanlan_name: str,
    mgr,
    config_manager,
    system_prompt: str,
    channel: str,  # 'work_break' | 'anti_slack' | 'work_break_game_invite'
    lang: str,
    timeout_seconds: float = 25.0,
) -> tuple[str | None, str | None]:
    """Minimal Phase 2 LLM stream delivery for break reminders.

    No Phase 1, no sources, no full activity_state_section in the prompt — just
    ``character_prompt`` (already baked into ``system_prompt`` by the caller) +
    the env-notice block, so the model puts all attention on the single nudge.

    Returns ``(delivered_text, proactive_sid)`` on success.
    Returns ``(None, None)`` on:
      * ``prepare_proactive_delivery`` rejection (user just spoke / WS offline /
        etc — leave the source pending alone, next round can retry)
      * LLM error / timeout / preempt
      * Empty output / [PASS] emission (defensive)

    Caller is responsible for ``mark_*_used`` on success and for any follow-up
    UI push (e.g. the mini-game options popup in the
    work_break_game_invite branch).
    """
    # Model configuration is an explicit orchestration dependency. Returns None
    # on any misconfiguration: a working break reminder is strictly better than
    # crashing the whole proactive_chat round, and the source pending stays
    # armed for the next attempt once config is fixed.
    try:
        correction_config = config_manager.get_model_api_config("correction")
        correction_model = correction_config.get("model")
        correction_base_url = correction_config.get("base_url")
        correction_api_key = correction_config.get("api_key")
        correction_provider_type = correction_config.get("provider_type")
        if not correction_model or not correction_api_key:
            logger.warning(
                "[%s] break reminder skipped: correction model misconfigured",
                lanlan_name,
            )
            return None, None
    except Exception as cfg_err:
        logger.warning(
            "[%s] break reminder skipped: model config fetch failed: %s",
            lanlan_name,
            cfg_err,
        )
        return None, None

    # Idle gate (10s) — same threshold mini-game invite uses. If the user just
    # typed/spoke, don't interrupt.
    if not await mgr.prepare_proactive_delivery(min_idle_secs=10.0):
        return None, None

    proactive_sid = mgr.current_speech_id
    from main_logic.session_state import SessionEvent as _SE

    await mgr.state.fire(_SE.PROACTIVE_PHASE2)

    # Minimal HumanMessage — just ask the model to begin. The localized
    # ``BEGIN_GENERATE`` matches what normal Phase 2 uses, so the model
    # interprets the cue identically.
    begin_text = _loc(BEGIN_GENERATE, lang)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=begin_text),
    ]

    print(
        f"\n{'=' * 60}\n[BREAK-REMINDER] channel={channel} lang={lang} "
        f"model={correction_model}\n{'=' * 60}\n{system_prompt}\n{'=' * 60}\n"
    )

    from utils.token_tracker import set_call_type

    set_call_type("proactive")
    full_text = ""
    aborted = False
    pass_probe = ""
    pass_probe_len = 5  # len("[PASS]") - 1

    try:
        async with asyncio.timeout(timeout_seconds):
            async with await create_chat_llm_async(
                correction_model,
                correction_base_url,
                correction_api_key,
                provider_type=correction_provider_type,
                temperature=1.0,
                max_completion_tokens=PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                streaming=True,
                timeout=timeout_seconds,
            ) as llm:
                async for chunk in llm.astream(messages):
                    if mgr.state.is_proactive_preempted(proactive_sid):
                        aborted = True
                        break
                    content = chunk.content if hasattr(chunk, "content") else ""
                    if not content:
                        continue
                    combined = pass_probe + content
                    if "[PASS]" in combined.upper():
                        aborted = True
                        break
                    safe_text = (
                        combined[:-pass_probe_len]
                        if len(combined) > pass_probe_len
                        else ""
                    )
                    pass_probe = (
                        combined[-pass_probe_len:]
                        if len(combined) >= pass_probe_len
                        else combined
                    )
                    if safe_text:
                        # Token-budget cap mirrors the normal Phase 2 path —
                        # break-reminder output should be short in any case,
                        # but defensive.
                        n_tokens = count_tokens(full_text + safe_text)
                        if n_tokens > PHASE2_OUTPUT_MAX_TOKENS:
                            aborted = True
                            break
                        full_text += safe_text
                        await mgr.feed_tts_chunk(
                            safe_text,
                            expected_speech_id=proactive_sid,
                        )
        # Flush remaining pass_probe (if it doesn't itself contain [PASS]).
        if not aborted and pass_probe and "[PASS]" not in pass_probe.upper():
            full_text += pass_probe
            await mgr.feed_tts_chunk(
                pass_probe,
                expected_speech_id=proactive_sid,
            )
    except (asyncio.TimeoutError, Exception) as exc:
        logger.warning(
            "[%s] break reminder LLM stream failed (channel=%s): %s: %s",
            lanlan_name,
            channel,
            type(exc).__name__,
            exc,
        )
        aborted = True

    if aborted or not full_text.strip():
        if not mgr.state.is_proactive_preempted(proactive_sid):
            await mgr.handle_new_message()
        return None, None

    text = full_text.strip()
    committed = await mgr.finish_proactive_delivery(
        text,
        expected_speech_id=proactive_sid,
    )
    if not committed:
        return None, None

    _record_proactive_chat(lanlan_name, text, channel=channel)
    print(f"[{lanlan_name}] break reminder delivered (channel={channel}): {text[:80]}…")
    return text, proactive_sid


__all__ = (
    "_deliver_break_reminder_via_llm",
    "_render_anti_slack_prompt",
    "_render_work_break_game_invite_prompt",
    "_render_work_break_prompt",
    "_resolve_break_reminder_label",
)
