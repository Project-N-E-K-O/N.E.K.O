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

"""Parsers and output guards for proactive Phase 1/2 generation."""

import asyncio
from contextlib import suppress
import logging
import unicodedata
import re
from dataclasses import dataclass
from functools import partial
from itertools import zip_longest
from typing import Any, Callable

from config import (
    ANTI_REPEAT_DROP_THRESHOLD,
    ANTI_REPEAT_EXEMPT_SOURCE_TAGS,
    ANTI_REPEAT_INJECT_TOP_K,
    ANTI_REPEAT_VERBATIM_GUARD_SOURCE_TAGS,
    ANTI_REPEAT_REGEN_THRESHOLD,
    PROACTIVE_PHASE1_FETCH_PER_SOURCE,
    PROACTIVE_PHASE1_UNIFIED_MAX_TOKENS,
    PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
    PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS,
    focus_extra_body,
    leaks_thinking_in_content,
)
from config.prompts.prompts_directives import (
    is_semantically_empty_term,
    term_needs_case_sensitive_match,
    render_format_fix_instruction,
    render_regen_avoid_instruction,
)
from config.prompts.prompts_proactive import (
    BEGIN_GENERATE,
    build_unified_phase1_prompt,
    get_proactive_generate_prompt,
)
from config.prompts.prompts_sys import _loc
from memory.anti_repeat_effects import (
    KEEP_INITIAL_SIGNATURE,
    AntiRepeatDecision,
    build_repeat_signature,
    record_anti_repeat_decision,
)
from utils.llm_client import (
    HumanMessage,
    SystemMessage,
    ThinkingStreamStripper,
    chat_retry_error_types,
    create_chat_llm_async,
)
from utils.logger_config import get_module_logger
from utils.meme_fetcher import fetch_meme_content
from utils.tokenize import count_tokens

from .contracts import (
    PROACTIVE_REASON_DELIVERY_PREEMPTED,
    PROACTIVE_REASON_PASS_DUPLICATE,
    PROACTIVE_REASON_PASS_GENERATION_EMPTY,
    PROACTIVE_REASON_PASS_MODEL_PASS,
    PROACTIVE_REASON_PASS_USER_DIRECTIVE,
    ProactiveChatResult,
    _proactive_pass_body,
)
from .music_recommendation import (
    _fetch_music_with_fallback,
    _format_music_content,
    _log_music_content,
)
from .state import (
    _PROACTIVE_SIMILARITY_THRESHOLD,
    ProactiveSimilarityMatch,
    _find_similar_recent_proactive_chat,
    _find_verbatim_recent_proactive_chat,
    _is_recent_proactive_material,
    _proactive_material_key,
    _proactive_turn_still_owned,
)

logger = get_module_logger(__name__, "Main")


_PROACTIVE_LLM_RETRY_ERROR_TYPES: tuple[type[BaseException], ...] | None = None


def _proactive_silence_since(mgr: Any) -> float | None:
    """Return the trustworthy start of the current no-response streak."""
    timestamps = (
        getattr(mgr, "proactive_engagement_observation_started_at", None),
        getattr(mgr, "last_user_message_time", None),
        getattr(mgr, "last_user_engagement_time", None),
    )
    valid = [float(value) for value in timestamps if value is not None]
    return max(valid) if valid else None


def _append_directives_section(
    memory_context: str, lanlan_name: str, lang: str,
) -> str:
    """Append the active ban-topic block to a Phase 2 memory context.

    Phase 2 assembles its own system prompt and never goes through
    ``_build_initial_prompt``, so without this the one path where the character
    speaks unprompted is also the one path blind to "stop bringing X up".

    Soft constraint only — the output-side gate is ``_proactive_directive_hits``.
    Never raises: on failure the caller keeps the unmodified context.
    """
    try:
        from memory.user_directives import get_user_directives_manager
        block = get_user_directives_manager().render_prompt_block(
            lanlan_name or "default", lang,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[UserDirectives] proactive prompt injection skipped: %s", exc)
        return memory_context
    if not block:
        return memory_context
    return (memory_context or "") + block


# Scripts with/without word boundaries: CJK/kana/Hangul concatenate without spaces, requiring substrings; Latin,
# Cyrillic/Greek use spaces, so substrings collide inside longer words. Banning ``it``
# matches ``favorite`` / ``waiting``, silencing all proactive speech (adversarial review
# dropped 3/3 drafts). If term contains any unspaced-script character, fall back to substrings.
_BOUNDARYLESS_SCRIPT_RE = re.compile(
    r"[⺀-鿿぀-ヿ가-힯豈-﫿ｦ-ﾟ]"
)

# Word-delimited alphabet: ASCII + extended Latin (Spanish/Portuguese accents) + Greek + Cyrillic.
# ⚠️ Never use ``\b``: Python ``\w`` includes Han, so ``\b`` never fires between
# letters and Han; ``今天work很累`` has ``\w`` on both sides of ``work``,
# and ``\bwork\b`` misses entirely. Mixed-language speech (user says "stop talking about
# work", character says "今天work怎么样") is explicitly supported repeatedly by
# prompts_directives. Blanket \b misses the main scenario (CodeRabbit caught;
# 3/3 missed). Check neighboring word characters from same script family, not generic \w.
_LATIN_WORD_CHAR = r"0-9A-Za-zÀ-ɏͰ-ϿЀ-ӿ"
_LATIN_EDGE_RE = re.compile(rf"[{_LATIN_WORD_CHAR}]")


def _normalize_for_match(text: str) -> str:
    """Unicode-normalize so composed and decomposed accents compare equal.

    A term captured from decomposed input (``e`` + combining acute) and a draft
    written in composed form (``é``) are different strings byte-for-byte, so an
    accented Spanish/Portuguese ban would silently never match.
    """
    return unicodedata.normalize("NFC", text)


def _directive_term_in_draft(
    term: str, draft_folded: str, *, fold_case: bool = True,
) -> bool:
    """Whether ``term`` occurs in a draft the caller already normalized.

    Latin/Cyrillic/Greek terms match only when not glued to another letter of
    the same family; CJK/kana/hangul are written without spaces and can only
    be matched as substrings.

    ⚠️ ``fold_case`` must agree with how the caller prepared ``draft_folded``:
    True expects a casefolded draft, False expects one that kept its case. A
    capitalized term (``US``, ``Us``, ``Her``) is a proper name, and matching it
    case-insensitively would fire on every ordinary ``us`` / ``her`` in the
    draft — the pronoun-silencing P1 all over again. See
    ``term_needs_case_sensitive_match``.
    """
    normalized_term = _normalize_for_match(term)
    folded_term = normalized_term.casefold() if fold_case else normalized_term
    if not folded_term:
        return False
    if _BOUNDARYLESS_SCRIPT_RE.search(folded_term):
        return folded_term in draft_folded
    # ⚠️ Boundaries matter only when both term ends are word-delimited characters. ``my ex`` ends
    # with letters, fine; punctuation-ended terms from permissive extraction place lookarounds
    # outside punctuation, nearly always missing. Fall back to substrings, favoring extra blocking.
    if not (_LATIN_EDGE_RE.match(folded_term[0])
            and _LATIN_EDGE_RE.match(folded_term[-1])):
        return folded_term in draft_folded
    # re already caches compiled patterns; no extra lru_cache.
    return re.search(
        rf"(?<![{_LATIN_WORD_CHAR}]){re.escape(folded_term)}"
        rf"(?![{_LATIN_WORD_CHAR}])",
        draft_folded,
    ) is not None


def _proactive_directive_hits(lanlan_name: str, draft: str) -> list[str]:
    """Active ban-topic terms that literally appear in a proactive draft.

    Matching is case-insensitive and script-folded on both sides, on word
    boundaries for Latin-script terms and as substrings for the boundaryless
    scripts (see ``_directive_term_in_draft``). Extraction upstream deliberately
    over-kills, on the grounds that a false positive costs one skipped proactive
    message while a miss costs the user being annoyed again by the exact thing
    they just asked about — and this stays consistent with that.

    Bare referents (the demonstratives, "this", "it") are skipped: matching on
    the most common word in the language would silence proactive chat wholesale
    for the directive's whole lifetime. They stay in the soft prompt block,
    where the model has the context needed to interpret them.

    Never raises: recall failures degrade to "no hit" so the anti-repeat gates
    downstream keep their existing behaviour.
    """
    if not draft or not draft.strip():
        return []
    try:
        from memory.user_directives import get_user_directives_manager
        terms = get_user_directives_manager().get_active_terms(
            lanlan_name or "default",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[UserDirectives] proactive gate skipped: %s", exc)
        return []
    if not terms:
        return []
    # Lazy import: narrow entrypoints may not load memory layer, matching this function's
    # other memory access patterns.
    # ⚠️ Both import and invocation belong in try. Docstring promises never raises, while
    # lazy import specifically accounts for missing memory layers, exactly the raising case.
    # Outside try breaks contract in its sole relevant scenario (get_active_terms above
    # is guarded, only this site missed it).
    def _no_fold(text: str) -> str:
        return text

    # ⚠️ Fold Traditional/Simplified on both sides. Input-method switch gives "别再提遊戲", storing Traditional term,
    # while locale outputs Simplified "游戏"; literal mismatch misses, pushing explicitly banned
    # topics at users. memory.script_fold already serves this purpose
    # (#2584 same recall-side issue); reuse, avoiding anti_repeat's duplicate
    # tokenization implementations diverging independently.
    # ⚠️ Normalize Unicode too: Spanish/Portuguese accents may persist decomposed (e + combining accent),
    # byte-distinct from composed é, silently never matching accented terms (Codex).
    try:
        from memory.script_fold import fold_script
        folded = _normalize_for_match(fold_script(draft)).casefold()
    except Exception as exc:  # pragma: no cover - defensive
        # ⚠️ Fallback continues matching without folding, never abandons entire gate. Folding handles cross-script
        # variants (遊戲/游戏); losing it still blocks most same-script matches. Immediate
        # return [] instead pushes explicitly banned topics at users; degraded matching is
        # never worse than abandonment on any input.
        logger.debug("[UserDirectives] script fold unavailable: %s", exc)
        fold_script = _no_fold
        folded = _normalize_for_match(draft).casefold()
    # ⚠️ Pure demonstratives (``这个`` / ``this`` / ``それ``) use soft constraints only, never hard blocks.
    # Extraction stores them ("别再讲这个了"), already pinned by existing tests and
    # outside this change. But substring matching ubiquitous Chinese words silences all proactive
    # speech throughout directive lifetime (escalating TTL up to 30 days), with no current
    # UI to inspect/delete it. Criteria/harm are consumer-side, so resolve there.
    # ⚠️ Retain original-case draft for proper-name terms (``US`` / ``Us`` / ``Her``).
    # Lowercase forms are table pronouns; casefold would match every ordinary draft
    # ``us`` / ``her``. Treating them as exempt pronouns instead permits explicitly banned
    # topics. Both are P1; decide from term's own case, consistently on both sides.
    cased = _normalize_for_match(fold_script(draft))
    hits: list[str] = []
    for t in terms:
        if not t or is_semantically_empty_term(t):
            continue
        folded_t = fold_script(t)
        case_sensitive = term_needs_case_sensitive_match(folded_t)
        if _directive_term_in_draft(
            folded_t,
            cased if case_sensitive else folded,
            fold_case=not case_sensitive,
        ):
            hits.append(t)
    return hits


def _merge_regen_avoid_terms(*term_groups: Any) -> list[str]:
    """Interleave repeat signals while keeping the prompt injection bounded."""
    interleaved = (
        term for row in zip_longest(*term_groups) for term in row if term is not None
    )
    return list(dict.fromkeys(interleaved))[:ANTI_REPEAT_INJECT_TOP_K]


def _score_regenerated_draft(
    anti_repeat_corpus: Any,
    lanlan_name: str,
    text: str,
    *,
    exempt: bool,
) -> tuple[float | None, dict]:
    """Return a measured BM25 score and its terms, never a synthetic zero.

    The terms come back because an outcome produced by scoring the REGENERATED
    draft has to be attributed to that draft's phrases, not to the initial
    draft's.
    """
    if exempt or anti_repeat_corpus is None:
        return None, {}
    try:
        total, terms = anti_repeat_corpus.score_draft(lanlan_name, text)
        return float(total), dict(terms or {})
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[AntiRepeat] proactive regen score skipped: %s", exc)
        return None, {}


@dataclass(frozen=True, slots=True)
class ProactiveModelConfig:
    """Resolved conversation and optional vision model settings for one turn."""

    conversation_model: str
    conversation_base_url: str | None
    conversation_api_key: str
    conversation_provider_type: str | None
    vision_model: str = ""
    vision_base_url: str | None = ""
    vision_api_key: str = ""
    vision_provider_type: str | None = None
    vision_is_custom: bool = False

    @property
    def has_vision_model(self) -> bool:
        # Custom vision endpoints (local Ollama/LAN OpenAI-compatible) often omit keys;
        # match screenshot_utils: empty key with custom config still counts configured.
        return bool(
            self.vision_model and (self.vision_api_key or self.vision_is_custom)
        )


@dataclass(frozen=True, slots=True)
class Phase2PromptContext:
    """Bounded business context used to render the existing Phase 2 prompt."""

    music_playing_hint: str
    character_prompt: str
    inner_thoughts: str
    state_section: str
    memory_context: str
    recent_chats_section: str
    screen_section: str
    external_section: str
    music_section: str
    meme_section: str
    source_instruction: str
    output_format_section: str

    def render(self, *, proactive_lang: str, master_name: str) -> str:
        return get_proactive_generate_prompt(
            proactive_lang,
            self.music_playing_hint,
            has_music=bool(self.music_section),
            has_meme=bool(self.meme_section),
            master_name=master_name,
        ).format(
            character_prompt=self.character_prompt,
            inner_thoughts=self.inner_thoughts,
            state_section=self.state_section,
            memory_context=self.memory_context,
            recent_chats_section=self.recent_chats_section,
            screen_section=self.screen_section,
            external_section=self.external_section,
            music_section=self.music_section,
            meme_section=self.meme_section,
            master_name=master_name,
            source_instruction=self.source_instruction,
            output_format_section=self.output_format_section,
        )


def _proactive_llm_retry_error_types() -> tuple[type[BaseException], ...]:
    """Lazily resolve optional provider exception types."""
    global _PROACTIVE_LLM_RETRY_ERROR_TYPES
    if _PROACTIVE_LLM_RETRY_ERROR_TYPES is None:
        _PROACTIVE_LLM_RETRY_ERROR_TYPES = (
            asyncio.TimeoutError,
            *chat_retry_error_types(),
        )
    return _PROACTIVE_LLM_RETRY_ERROR_TYPES


def _proactive_vision_consent_checker(
    mgr: Any, vision_consent_epoch: int,
) -> Callable[[], bool]:
    """Live consent for one round, judged against the epoch it started under."""
    def live_consent_check() -> bool:
        # A quick off/on must not restore vision input held by the revoked round.
        return (
            getattr(mgr, "_proactive_vision_enabled", None) is not False
            and getattr(mgr, "_proactive_vision_revocation_epoch", 0) == vision_consent_epoch
        )

    return live_consent_check


async def _check_proactive_vision_consent(
    live_consent_check: Callable[[], bool] | None = None,
) -> None:
    """Require current consent before exposing proactive images to a model."""
    from utils.preferences import aload_global_conversation_settings

    if live_consent_check is not None and not live_consent_check():
        raise PermissionError("Proactive screenshot use declined by live consent")
    settings = await aload_global_conversation_settings(strict=True)
    if (
        settings.get("proactiveVisionEnabled", True) is not True
        or (live_consent_check is not None and not live_consent_check())
    ):
        raise PermissionError("Proactive screenshot use declined by privacy mode")


async def _make_proactive_llm(
    model_config: ProactiveModelConfig,
    *,
    temperature: float = 1.0,
    max_completion_tokens: int = PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
    use_vision: bool = False,
    disable_thinking: bool = True,
    live_consent_check: Callable[[], bool] | None = None,
    round_is_current: Callable[[], bool] | None = None,
    vision_consent_required: bool = False,
):
    """Create the configured proactive-chat model without owning lifecycle state.

    ``vision_consent_required`` gates a text-only call whose prompt carries
    screen- or window-derived material the same way as an image call.
    """
    needs_vision_consent = use_vision or vision_consent_required

    def require_current_round() -> None:
        if round_is_current is not None and not round_is_current():
            raise PermissionError("Proactive round retired")

    require_current_round()
    if needs_vision_consent:
        await _check_proactive_vision_consent(live_consent_check)
    require_current_round()
    if use_vision and model_config.has_vision_model:
        model = model_config.vision_model
        base_url = model_config.vision_base_url
        api_key = model_config.vision_api_key
        provider_type = model_config.vision_provider_type
    else:
        model = model_config.conversation_model
        base_url = model_config.conversation_base_url
        api_key = model_config.conversation_api_key
        provider_type = model_config.conversation_provider_type

    from config import DIALOG_LLM_STREAM_TIMEOUT_SECONDS

    kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_completion_tokens": max_completion_tokens,
        "streaming": True,
        "timeout": DIALOG_LLM_STREAM_TIMEOUT_SECONDS,
        "provider_type": provider_type,
    }
    if not disable_thinking:
        kwargs["extra_body"] = focus_extra_body(model)
    llm = await create_chat_llm_async(  # noqa: LLM_OUTPUT_BUDGET
        model,
        base_url,
        api_key,
        **kwargs,
    )
    if needs_vision_consent or round_is_current is not None:
        # Client creation awaits a worker thread; consent may change while it runs.
        # The caller has not entered the client context yet, so denial owns cleanup.
        try:
            if needs_vision_consent:
                await _check_proactive_vision_consent(live_consent_check)
            require_current_round()
        except BaseException:
            with suppress(Exception):
                await llm.aclose()
            raise
    return llm


async def _llm_call_with_retry(
    *,
    model_config: ProactiveModelConfig,
    proactive_lang: str,
    lanlan_name: str,
    system_prompt: str,
    label: str,
    temperature: float = 1.0,
    max_completion_tokens: int = PROACTIVE_PHASE1_UNIFIED_MAX_TOKENS,
    timeout: float = 16.0,
    use_vision: bool = False,
    disable_thinking: bool = True,
    image_b64: str = "",
    dynamic_context: str = "",
    log: logging.Logger | None = None,
    live_consent_check: Callable[[], bool] | None = None,
    vision_consent_required: bool = False,
) -> str:
    """Call the proactive model with the established retry and message shape."""
    active_logger = log or logger
    begin_text = _loc(BEGIN_GENERATE, proactive_lang)
    human_text = f"{dynamic_context}\n\n{begin_text}" if dynamic_context else begin_text
    if image_b64:
        human_content: Any = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
            },
            {"type": "text", "text": human_text},
        ]
    else:
        human_content = human_text
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_content),
    ]

    from utils.token_tracker import set_call_type

    set_call_type("proactive")
    retry_delays = [1, 2]
    for attempt in range(3):
        try:
            async with await _make_proactive_llm(
                model_config,
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                use_vision=use_vision,
                disable_thinking=disable_thinking,
                live_consent_check=live_consent_check,
                vision_consent_required=vision_consent_required,
            ) as llm:
                response = await asyncio.wait_for(
                    llm.ainvoke(messages),
                    timeout=timeout,
                )
                print(
                    f"\n[PROACTIVE-DEBUG] LLM output [{label}]: "
                    f"{response.content[:500]}...\n"
                )
                return response.content.strip()
        except _proactive_llm_retry_error_types() as exc:
            if attempt < 2:
                active_logger.warning(
                    f"[{lanlan_name}] LLM [{label}] 调用失败 "
                    f"(尝试 {attempt + 1}/3): {exc}"
                )
                await asyncio.sleep(retry_delays[attempt])
            else:
                active_logger.error(
                    f"[{lanlan_name}] LLM [{label}] 调用失败，已达最大重试: {exc}"
                )
                raise
    raise RuntimeError("Unexpected")


@dataclass(frozen=True, slots=True)
class Phase1Decision:
    """The channel decision handed from Phase 1 to Phase 2.

    ``result`` is populated only when Phase 1 terminates the proactive turn.
    A text-only unfinished-thread continuation is therefore represented by an
    empty channel list and ``primary_channel='unknown'``, matching the legacy
    Router flow.
    """

    result: ProactiveChatResult | None
    active_channels: list[str]
    primary_channel: str
    web_topic: str | None
    music_topic: str | None


@dataclass(frozen=True, slots=True)
class Phase2Generation:
    """Framework-independent outcome of the Phase 2 model stream."""

    result: ProactiveChatResult | None
    full_text: str = ""
    response_text: str = ""
    source_tag: str = ""


@dataclass(frozen=True, slots=True)
class Phase2GuardedOutput:
    """Phase 2 text after literal, BM25, and music-data guards."""

    result: ProactiveChatResult | None
    full_text: str = ""
    response_text: str = ""
    source_tag: str = ""
    selected_music_link: dict[str, Any] | None = None
    music_content: dict[str, Any] | None = None
    is_music_used: bool = False


async def _run_unified_phase1(
    *,
    model_config: ProactiveModelConfig,
    proactive_lang: str,
    lanlan_name: str,
    master_name: str,
    merged_web_content: str,
    memory_context: str,
    recent_chats_section: str,
    has_music_task: bool,
    has_meme_task: bool,
    log: logging.Logger | None = None,
    live_consent_check: Callable[[], bool] | None = None,
    vision_consent_required: bool = False,
) -> dict[str, Any]:
    """Build, call, and parse the unified Phase 1 model request."""
    active_logger = log or logger
    empty_result = {
        "web": None,
        "music_keyword": None,
        "meme_keyword": None,
    }
    has_web_task = bool(merged_web_content)
    if not (has_web_task or has_music_task or has_meme_task):
        return empty_result

    try:
        prompt = build_unified_phase1_prompt(
            proactive_lang,
            merged_content=merged_web_content if has_web_task else None,
            memory_context=memory_context,
            recent_chats_section=recent_chats_section,
            music_ctx={
                "lanlan_name": lanlan_name,
                "master_name": master_name,
            }
            if has_music_task
            else None,
            meme_enabled=has_meme_task,
            lanlan_name=lanlan_name,
            master_name=master_name,
        )
        result_text = await _llm_call_with_retry(
            model_config=model_config,
            proactive_lang=proactive_lang,
            lanlan_name=lanlan_name,
            system_prompt=prompt,
            label="unified_phase1",
            log=active_logger,
            live_consent_check=live_consent_check,
            vision_consent_required=vision_consent_required,
        )
        print(f"[{lanlan_name}] Phase 1 合并 LLM 结果: {result_text[:500]}")
        parsed = _parse_unified_phase1_result(result_text)
        active_logger.debug(
            f"[{lanlan_name}] Phase 1 解析: "
            f"web={'有' if parsed.get('web') else '无'}, "
            f"music_kw={parsed.get('music_keyword', 'N/A')}, "
            f"meme_kw={parsed.get('meme_keyword', 'N/A')}"
        )
        return parsed
    except Exception as exc:
        active_logger.warning(
            f"[{lanlan_name}] Phase 1 合并 LLM 调用异常: "
            f"{type(exc).__name__}: {exc}，降级处理"
        )
        return empty_result


async def _fetch_meme_with_fallback(
    keyword: str,
    *,
    lanlan_name: str,
    log: logging.Logger | None = None,
) -> dict[str, Any] | None:
    """Fetch a keyword meme, then preserve the legacy random fallback."""
    active_logger = log or logger
    try:
        raw = await asyncio.wait_for(
            fetch_meme_content(
                keyword=keyword,
                limit=PROACTIVE_PHASE1_FETCH_PER_SOURCE,
            ),
            timeout=12.0,
        )
        if raw and raw.get("success"):
            raw["effective_keyword"] = keyword
            return raw
    except Exception as exc:
        active_logger.warning(
            f"[{lanlan_name}] 表情包关键词 '{keyword}' 搜索异常: {exc}"
        )
    active_logger.warning(
        f"[{lanlan_name}] 表情包关键词 '{keyword}' 搜索失败，尝试随机热词"
    )
    try:
        raw = await asyncio.wait_for(
            fetch_meme_content(
                keyword="",
                limit=PROACTIVE_PHASE1_FETCH_PER_SOURCE,
            ),
            timeout=12.0,
        )
        if raw:
            raw["effective_keyword"] = ""
        return raw
    except Exception:
        return None


async def _fetch_phase1_followups(
    *,
    parsed: dict[str, Any],
    has_music_task: bool,
    has_meme_task: bool,
    music_content: dict[str, Any] | None,
    meme_content: dict[str, Any] | None,
    proactive_lang: str,
    lanlan_name: str,
    log: logging.Logger | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Run the Phase 1 keyword-dependent music and meme fetches concurrently."""
    active_logger = log or logger
    fetch_tasks: list[Any] = []
    fetch_labels: list[str] = []

    if has_music_task and not parsed.get("music_pass"):
        fetch_tasks.append(
            _fetch_music_with_fallback(
                parsed.get("music_keyword") or "",
                lanlan_name=lanlan_name,
            )
        )
        fetch_labels.append("music")

    if has_meme_task and not parsed.get("meme_pass"):
        fetch_tasks.append(
            _fetch_meme_with_fallback(
                parsed.get("meme_keyword") or "",
                lanlan_name=lanlan_name,
                log=active_logger,
            )
        )
        fetch_labels.append("meme")

    if not fetch_tasks:
        return music_content, meme_content

    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
    for label, result in zip(fetch_labels, results):
        if isinstance(result, Exception):
            active_logger.warning(
                f"[{lanlan_name}] Phase 1 后置 fetch [{label}] 异常: {result}"
            )
            continue
        if label == "music" and result and result.get("success"):
            _log_music_content(lanlan_name, result)
            music_content = {
                "formatted_content": _format_music_content(
                    result,
                    proactive_lang,
                ),
                "raw_data": result,
            }
        elif label == "meme" and result and result.get("success"):
            meme_content = {
                "success": True,
                "data": result.get("data", []),
                "raw_data": result,
                "source": result.get("source", "表情包"),
                "keyword": result.get("effective_keyword", ""),
            }
            print(
                f"[{lanlan_name}] 成功获取 {len(result.get('data', []))} 个表情包 "
                f"(来源: {result.get('source', '?')})"
            )
    return music_content, meme_content


async def _run_phase2_generation(
    *,
    mgr: Any,
    proactive_sid: Any,
    model_config: ProactiveModelConfig,
    lanlan_name: str,
    proactive_lang: str,
    master_name: str,
    system_prompt: str,
    dynamic_context: str,
    screenshot_b64: str | None,
    focus_thinking: bool,
    expects_source_tag: bool,
    active_channels: list[str],
    selected_music_link: dict[str, Any] | None,
    selected_meme_link: dict[str, Any] | None,
    music_content: dict[str, Any] | None,
    meme_content: dict[str, Any] | None,
    is_playing_music: bool,
    music_cooldown: bool,
    log: logging.Logger | None = None,
    vision_consent_epoch: int | None = None,
    round_is_current: Callable[[], bool] | None = None,
    vision_consent_required: bool = False,
) -> Phase2GuardedOutput:
    """Own Phase 2 messages, model stream, and output guards."""
    active_logger = log or logger
    use_vision = bool(screenshot_b64 and model_config.has_vision_model)
    disable_thinking = use_vision or not focus_thinking
    begin_text = _loc(BEGIN_GENERATE, proactive_lang)
    human_text = f"{dynamic_context}\n\n{begin_text}" if dynamic_context else begin_text
    if use_vision:
        human_content: Any = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"},
            },
            {"type": "text", "text": human_text},
        ]
    else:
        human_content = human_text
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_content),
    ]
    actual_model = (
        model_config.vision_model if use_vision else model_config.conversation_model
    )
    # ⚠️ Never print prompt bodies. Existing debug print began leaking user ban-topic directives
    # when this PR injected them into Phase 2 system prompt,
    # sending explicit do-not-mention content (ex-partners/illness/deceased names) verbatim to stdout.
    # Same issue already fixed twice here (proactive output gate/_report_if_kept
    # now counts only), but indirectly introduced through prompts; initial self-check
    # searched direct variables only and missed it (CodeRabbit outside diff).
    # Prompts already contain memory/activity context, also unsuitable for unconditional stdout;
    # retain debugging model/modality/size, remove bodies.
    print(
        f"\n{'=' * 60}\n[PROACTIVE-DEBUG] Phase 2 STREAM: "
        f"model={actual_model} | vision={use_vision} | "
        f"img={'yes' if use_vision else 'no'} | "
        f"prompt_chars={len(system_prompt)}\n{'=' * 60}\n"
    )

    if vision_consent_epoch is None:
        vision_consent_epoch = getattr(mgr, "_proactive_vision_revocation_epoch", 0)
    live_consent_check = _proactive_vision_consent_checker(mgr, vision_consent_epoch)

    make_llm = partial(
        _make_proactive_llm, model_config, live_consent_check=live_consent_check,
        round_is_current=round_is_current,
        vision_consent_required=vision_consent_required,
    )
    silence_since_before_generation = _proactive_silence_since(mgr)
    generated = await _generate_phase2_stream(
        mgr=mgr,
        proactive_sid=proactive_sid,
        lanlan_name=lanlan_name,
        messages=messages,
        make_llm=make_llm,
        phase2_use_vision=use_vision,
        phase2_disable_thinking=disable_thinking,
        conversation_model=model_config.conversation_model,
        expects_source_tag=expects_source_tag,
        proactive_lang=proactive_lang,
        master_name=master_name,
        human_text=human_text,
        screenshot_b64=screenshot_b64,
        log=active_logger,
    )
    if generated.result is not None:
        return Phase2GuardedOutput(result=generated.result)

    silence_since_after_generation = _proactive_silence_since(mgr)
    if silence_since_after_generation is not None and (
        silence_since_before_generation is None
        or silence_since_after_generation > silence_since_before_generation
    ):
        active_logger.info(
            "[%s] proactive Phase 2 abandoned after user interaction during generation",
            lanlan_name,
        )
        if _proactive_turn_still_owned(mgr, proactive_sid):
            await mgr.handle_new_message()
        return Phase2GuardedOutput(
            result=ProactiveChatResult(
                body=_proactive_pass_body(
                    PROACTIVE_REASON_DELIVERY_PREEMPTED,
                    message="用户已在主动搭话生成期间恢复互动",
                )
            )
        )

    return await _guard_phase2_output(
        mgr=mgr,
        proactive_sid=proactive_sid,
        lanlan_name=lanlan_name,
        response_text=generated.response_text,
        full_text=generated.full_text,
        source_tag=generated.source_tag,
        active_channels=active_channels,
        selected_music_link=selected_music_link,
        selected_meme_link=selected_meme_link,
        music_content=music_content,
        meme_content=meme_content,
        is_playing_music=is_playing_music,
        music_cooldown=music_cooldown,
        expects_source_tag=expects_source_tag,
        make_llm=make_llm,
        messages=messages,
        human_text=human_text,
        screenshot_b64=screenshot_b64,
        phase2_use_vision=use_vision,
        phase2_disable_thinking=disable_thinking,
        proactive_lang=proactive_lang,
        master_name=master_name,
        log=active_logger,
    )


def _decide_phase1_channels(
    phase1_topics: list[tuple[str, str]],
    vision_content: str | None,
    *,
    has_unfinished_thread: bool,
) -> Phase1Decision:
    """Finalize Phase 1 without changing source order or continuation rules."""
    if not phase1_topics and not vision_content and not has_unfinished_thread:
        return Phase1Decision(
            result=ProactiveChatResult(
                body=_proactive_pass_body(
                    PROACTIVE_REASON_PASS_MODEL_PASS,
                    message="所有信息源筛选后均不值得搭话",
                )
            ),
            active_channels=[],
            primary_channel="unknown",
            web_topic=None,
            music_topic=None,
        )

    active_channels = [channel for channel, _topic in phase1_topics]
    web_topic = None
    music_topic = None
    for channel, topic in phase1_topics:
        if channel == "web":
            web_topic = topic
        elif channel == "music":
            music_topic = topic
    if vision_content:
        active_channels.append("vision")
    primary_channel = (
        "vision"
        if vision_content
        else (active_channels[0] if active_channels else "unknown")
    )
    return Phase1Decision(
        result=None,
        active_channels=active_channels,
        primary_channel=primary_channel,
        web_topic=web_topic,
        music_topic=music_topic,
    )


async def _generate_phase2_stream(
    *,
    mgr: Any,
    proactive_sid: Any,
    lanlan_name: str,
    messages: list[Any],
    make_llm: Any,
    phase2_use_vision: bool,
    phase2_disable_thinking: bool,
    conversation_model: str,
    expects_source_tag: bool,
    proactive_lang: str,
    master_name: str,
    human_text: str,
    screenshot_b64: str | None,
    log: logging.Logger | None = None,
) -> Phase2Generation:
    """Run the guarded Phase 2 stream and preserve its legacy abort semantics."""
    active_logger = log or logger
    from utils.token_tracker import set_call_type

    set_call_type("proactive")
    buffer = ""
    tag_parsed = False
    source_tag = ""
    full_text = ""
    pipe_count = 0
    aborted = False
    abort_reason_code: str | None = None
    pass_probe = ""
    pass_probe_len = 5  # len("[PASS]") - 1

    def _abort(reason_code: str) -> None:
        nonlocal aborted, abort_reason_code
        aborted = True
        if (
            abort_reason_code is None
            or reason_code == PROACTIVE_REASON_DELIVERY_PREEMPTED
        ):
            abort_reason_code = reason_code

    async def _emit_safe(text: str) -> bool:
        nonlocal pipe_count, full_text
        if not text:
            return False
        if mgr.state.is_proactive_preempted(proactive_sid):
            print(f"[{lanlan_name}] Phase 2 检测到用户接管（state 抢占），abort")
            _abort(PROACTIVE_REASON_DELIVERY_PREEMPTED)
            return True
        for char in text:
            if char in ("|", "｜"):
                pipe_count += 1
                if pipe_count >= 2:
                    print(
                        f"[{lanlan_name}] Phase 2 fence 触发 "
                        f"(pipe_count={pipe_count})，abort"
                    )
                    _abort(PROACTIVE_REASON_PASS_GENERATION_EMPTY)
                    return True
        token_count = count_tokens(full_text + text)
        if token_count > PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS:
            print(
                f"[{lanlan_name}] Phase 2 长度超限 "
                f"({token_count} > {PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS} tokens)，abort"
            )
            _abort(PROACTIVE_REASON_PASS_GENERATION_EMPTY)
            return True
        full_text += text
        return False

    thinking_stripper = (
        ThinkingStreamStripper()
        if not phase2_disable_thinking and leaks_thinking_in_content(conversation_model)
        else None
    )
    try:
        async with asyncio.timeout(25.0):
            async with await make_llm(
                temperature=1.0,
                max_completion_tokens=PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                use_vision=phase2_use_vision,
                disable_thinking=phase2_disable_thinking,
            ) as llm:
                async for chunk in llm.astream(messages):
                    if mgr.state.is_proactive_preempted(proactive_sid):
                        print(
                            f"[{lanlan_name}] Phase 2 astream chunk 前检测到抢占，abort"
                        )
                        _abort(PROACTIVE_REASON_DELIVERY_PREEMPTED)
                        break
                    content = chunk.content if hasattr(chunk, "content") else ""
                    if thinking_stripper is not None and content:
                        content = thinking_stripper.feed(content)
                    if not content:
                        continue

                    if not tag_parsed:
                        buffer += content
                        if "[PASS]" in buffer.upper():
                            _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
                            break
                        parsed = _parse_proactive_phase2_prefix(buffer)
                        if parsed is None:
                            # Recovery needs the complete output: a chunk may
                            # end inside a label, separator, or ordinary path.
                            # Bound pending data by the provider's generation
                            # budget; the cleaned body keeps its stricter limit.
                            if (
                                sum(buffer.count(mark) for mark in ("|", "｜")) >= 2
                                or count_tokens(buffer)
                                > PROACTIVE_PHASE2_GENERATE_MAX_TOKENS
                            ):
                                _abort(PROACTIVE_REASON_PASS_GENERATION_EMPTY)
                                break
                            continue
                        cleaned, source_tag = parsed
                        tag_parsed = True
                        buffer = ""

                        if (
                            source_tag == "PASS"
                            or "[PASS]" in cleaned.upper()
                            or _text_is_pass_sentinel(cleaned)
                        ):
                            print(f"[{lanlan_name}] Phase 2 流式检测到 PASS，abort")
                            _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
                            break

                        if cleaned.strip():
                            combined = pass_probe + cleaned
                            if "[PASS]" in combined.upper():
                                print(
                                    f"[{lanlan_name}] Phase 2 流式检测到 [PASS]，abort"
                                )
                                _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
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
                            if await _emit_safe(safe_text):
                                break
                        continue

                    combined = pass_probe + content
                    if "[PASS]" in combined.upper():
                        print(f"[{lanlan_name}] Phase 2 流式检测到内嵌 [PASS]，abort")
                        _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
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
                    if safe_text and await _emit_safe(safe_text):
                        break
    except (asyncio.TimeoutError, Exception) as exc:
        active_logger.warning(
            "[%s] Phase 2 流式调用异常: %s: %s",
            lanlan_name,
            type(exc).__name__,
            exc,
        )
        _abort(PROACTIVE_REASON_PASS_GENERATION_EMPTY)

    if pass_probe and not aborted:
        if "[PASS]" in pass_probe.upper():
            _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
        else:
            await _emit_safe(pass_probe)
    pass_probe = ""

    if thinking_stripper is not None and not aborted:
        residual = thinking_stripper.flush()
        if residual:
            buffer += residual

    if not tag_parsed and buffer and not aborted:
        cleaned, source_tag = _parse_proactive_phase2_prefix(buffer, final=True)
        if (
            source_tag == "PASS"
            or "[PASS]" in cleaned.upper()
            or _text_is_pass_sentinel(cleaned)
        ):
            _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
        elif cleaned.strip():
            await _emit_safe(cleaned)

    if not aborted and full_text.strip() and not source_tag and expects_source_tag:
        print(f"[{lanlan_name}] Phase 2 输出无合法来源标签，尝试格式自救 regen")
        if mgr.state.is_proactive_preempted(proactive_sid):
            _abort(PROACTIVE_REASON_DELIVERY_PREEMPTED)
        else:
            fix_human_text = (
                f"{render_format_fix_instruction(proactive_lang, master_name)}"
                f"\n\n{human_text}"
            )
            if phase2_use_vision:
                fix_human_content: Any = [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{screenshot_b64}"
                        },
                    },
                    {"type": "text", "text": fix_human_text},
                ]
            else:
                fix_human_content = fix_human_text
            fix_text = ""
            try:
                async with asyncio.timeout(20.0):
                    async with await make_llm(
                        temperature=1.0,
                        max_completion_tokens=PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                        use_vision=phase2_use_vision,
                        disable_thinking=phase2_disable_thinking,
                    ) as fix_llm:
                        fix_response = await fix_llm.ainvoke(
                            [messages[0], HumanMessage(content=fix_human_content)]
                        )
                        fix_text = (
                            fix_response.content
                            if hasattr(fix_response, "content")
                            else ""
                        ) or ""
            except Exception as exc:
                active_logger.warning(
                    "[%s] Phase 2 格式自救 regen 失败: %s",
                    lanlan_name,
                    exc,
                )
                fix_text = ""
            fixed = (fix_text or "").strip()
            fixed, fix_tag = _parse_proactive_phase2_prefix(fixed, final=True)
            if (
                fix_tag
                and fix_tag != "PASS"
                and fixed.strip()
                and "[PASS]" not in fixed.upper()
            ):
                source_tag = fix_tag
                full_text = fixed.strip()
                print(f"[{lanlan_name}] Phase 2 格式自救成功 tag={source_tag}")
            else:
                print(f"[{lanlan_name}] Phase 2 格式自救仍无合法 tag，drop")
                if (
                    fix_tag == "PASS"
                    or "[PASS]" in fixed.upper()
                    or _text_is_pass_sentinel(fixed)
                ):
                    _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
                else:
                    _abort(PROACTIVE_REASON_PASS_GENERATION_EMPTY)

    # ⚠️ Never print draft bodies. These two sites and response_text below run before ban gates:
    # matched banned terms reach stdout before drafts are discarded, blocking
    # delivery but not leakage. Apply same criterion as output-gate logs/prompt bodies,
    # avoiding inconsistent no-prompt-logging-but-draft-logging behavior.
    print(
        "\n[PROACTIVE-DEBUG] Phase 2 STREAM output "
        f"(aborted={aborted}, tag={source_tag}, chars={len(full_text)})\n"
    )
    if aborted or not full_text.strip():
        final_reason = abort_reason_code or PROACTIVE_REASON_PASS_GENERATION_EMPTY
        if _proactive_turn_still_owned(mgr, proactive_sid):
            await mgr.handle_new_message()
            active_logger.debug(
                "[%s] Phase 2 abort，已中断 TTS + 前端音频", lanlan_name
            )
        else:
            active_logger.info(
                "[%s] Phase 2 abort 但用户已接管 (state preempted)，"
                "跳过 TTS 清理避免误伤正常回复",
                lanlan_name,
            )
        return Phase2Generation(
            result=ProactiveChatResult(
                body=_proactive_pass_body(
                    final_reason,
                    message="Phase 2 流式输出被拦截或为空",
                )
            )
        )

    full_text, leak_tag = _strip_proactive_screen_tag_leak(full_text)
    if leak_tag and not source_tag:
        source_tag = leak_tag
    response_text = _strip_proactive_intent_label_leak(full_text.strip())
    if not response_text:
        if _proactive_turn_still_owned(mgr, proactive_sid):
            await mgr.handle_new_message()
        else:
            active_logger.info(
                "[%s] cleaned proactive output is empty but user already "
                "took over; skip TTS cleanup",
                lanlan_name,
            )
        return Phase2Generation(
            result=ProactiveChatResult(
                body=_proactive_pass_body(
                    PROACTIVE_REASON_PASS_GENERATION_EMPTY,
                    message="Phase 2 清理后输出为空",
                )
            )
        )
    active_logger.debug(
        "[%s] Phase 2 流式完成 (vision=%s, len=%s chars)",
        lanlan_name,
        phase2_use_vision,
        len(response_text),
    )
    print(
        "\n[PROACTIVE-DEBUG] Phase 2 STREAM output "
        f"chars={len(response_text)}\n"
    )
    return Phase2Generation(
        result=None,
        full_text=full_text,
        response_text=response_text,
        source_tag=source_tag,
    )


async def _guard_phase2_output(
    *,
    mgr: Any,
    proactive_sid: Any,
    lanlan_name: str,
    response_text: str,
    full_text: str,
    source_tag: str,
    active_channels: list[str],
    selected_music_link: dict[str, Any] | None,
    selected_meme_link: dict[str, Any] | None,
    music_content: dict[str, Any] | None,
    meme_content: dict[str, Any] | None,
    is_playing_music: bool,
    music_cooldown: bool,
    expects_source_tag: bool,
    make_llm: Any,
    messages: list[Any],
    human_text: str,
    screenshot_b64: str | None,
    phase2_use_vision: bool,
    phase2_disable_thinking: bool,
    proactive_lang: str,
    master_name: str,
    log: logging.Logger | None = None,
) -> Phase2GuardedOutput:
    """Apply Phase 2 dedup and data guards in their established order."""
    active_logger = log or logger

    def _output(
        *,
        result: ProactiveChatResult | None = None,
        is_music_used: bool = False,
    ) -> Phase2GuardedOutput:
        return Phase2GuardedOutput(
            result=result,
            full_text=full_text,
            response_text=response_text,
            source_tag=source_tag,
            selected_music_link=selected_music_link,
            music_content=music_content,
            is_music_used=is_music_used,
        )

    music_only_pending = (
        "music" in active_channels
        and selected_music_link is not None
        and not is_playing_music
        and not music_cooldown
        and not any(channel in ("vision", "web", "meme") for channel in active_channels)
    )
    if music_only_pending and source_tag != "MUSIC":
        dedup_tag = "MUSIC"
    elif (source_tag == "MEME" and selected_meme_link is None) or (
        source_tag == "MUSIC" and selected_music_link is None
    ):
        dedup_tag = "CHAT"
    else:
        dedup_tag = source_tag
    material_key = _proactive_material_key(
        dedup_tag,
        selected_music_link,
        meme_content,
    )
    exempt_text_dedup = (
        dedup_tag in ANTI_REPEAT_EXEMPT_SOURCE_TAGS
        and not _is_recent_proactive_material(
            lanlan_name,
            dedup_tag,
            material_key,
        )
    )
    # See ANTI_REPEAT_VERBATIM_GUARD_SOURCE_TAGS: fresh MEME material keeps the
    # text-dedup exemption except for a pure verbatim repeat, and a hit rewrites
    # once instead of dropping the meme outright.
    literal_regen_only = (
        exempt_text_dedup and dedup_tag in ANTI_REPEAT_VERBATIM_GUARD_SOURCE_TAGS
    )
    if exempt_text_dedup:
        active_logger.info(
            "[%s] proactive text-dedup exempt: tag=%s (model_tag=%s) "
            "material=%r (fresh material, skip %s)",
            lanlan_name,
            dedup_tag,
            source_tag,
            material_key or "(none)",
            "similarity+BM25 except verbatim" if literal_regen_only
            else "similarity+BM25",
        )

    # ── Explicit user ban-topic hard gate ────────────────────────────────────
    # Phase 2 prompt has soft constraints injected by service.py; this output fallback
    # drops drafts still containing banned topics verbatim.
    #
    # ⚠️ Drop rather than regenerate: regeneration bills another LLM call after model
    # already ignored known bans, making another paid gamble unwarranted.
    # Drop costs nothing and respects users explicitly declining the topic.
    #
    # ⚠️ exempt_text_dedup never exempts this. That exception avoids BM25 false positives
    # on formulaic openings with fresh material, while topic bans ignore freshness:
    # music recommendations mentioning forbidden subjects still must be blocked.
    #
    # ⚠️ Must precede all repetition checks (literal/BM25/unanswered long window).
    # Their drops call ``record_anti_repeat_decision``, whose
    # ``RepeatSignature`` includes ``phrase`` / ``normalized_phrase``, original draft
    # fragments persisted in anti_repeat_effects.json. If draft hits both bans
    # and repetition, a later ban gate blocks delivery only after sensitive snippets
    # persisted, worse than stdout because they survive sessions.
    # Also prerequisite for claim below that anti-repeat made no decision; later placement
    # makes that false because repetition checks already ran/recorded (CodeRabbit).
    directive_hits = _proactive_directive_hits(lanlan_name, response_text)
    if directive_hits:
        # ⚠️ Never print term text. Banned topics are explicitly unwanted sensitive content:
        # ex-partners/illness/layoffs/deceased names. Logs persist under logs/,
        # potentially bundled in bug reports, and these terms are among the most sensitive text.
        # Counts alone identify ban-gate blocking without reconstructing user statements.
        active_logger.info(
            "[%s] proactive blocked by user ban-topic directive (%d term(s) matched)",
            lanlan_name,
            len(directive_hits),
        )
        print(
            f"[{lanlan_name}] 主动搭话命中用户明确要求回避的话题，已拦截 "
            f"({len(directive_hits)} 项)"
        )
        if _proactive_turn_still_owned(mgr, proactive_sid):
            await mgr.handle_new_message()
        return _output(
            result=ProactiveChatResult(
                body=_proactive_pass_body(
                    PROACTIVE_REASON_PASS_USER_DIRECTIVE,
                    message="主动搭话命中用户明确要求回避的话题，已拦截",
                    # Likewise response includes counts only, no original text; body reaches frontend
                    # via /api/proactive_chat.
                    directive_match_count=len(directive_hits),
                )
            )
        )

    # One scan, not two: the guard verdict and the evidence fragment used by the
    # effect record come from the same match. ``_is_similar_to_recent_proactive_chat``
    # is only the historical (bool, score) wrapper around this call, so calling it
    # first and then re-deriving the match ran the whole SequenceMatcher sweep over
    # the recent-chat window twice on the block path. Both guard sites in this
    # module go through the match object directly; the tuple wrapper stays in
    # ``state`` for its other callers.
    if literal_regen_only:
        literal_match = _find_verbatim_recent_proactive_chat(
            lanlan_name, response_text
        )
    elif exempt_text_dedup:
        literal_match = ProactiveSimilarityMatch()
    else:
        literal_match = _find_similar_recent_proactive_chat(
            lanlan_name, response_text
        )
    literal_regen_triggered = literal_regen_only and literal_match.is_duplicate
    is_duplicate = literal_match.is_duplicate and not literal_regen_only
    similarity_score = literal_match.best_score
    if is_duplicate:
        record_anti_repeat_decision(
            lanlan_name,
            AntiRepeatDecision(
                source="proactive",
                reasons=("literal_similarity",),
                action="block",
                outcome="blocked_initial",
                signature=build_repeat_signature(
                    response_text,
                    language=proactive_lang,
                    fallback_fragment=literal_match.common_fragment,
                ),
                response_id=str(proactive_sid),
            ),
        )
        active_logger.info(
            "[%s] proactive repeat guard blocked Phase 2 output "
            "(similarity=%.3f threshold=%.2f)",
            lanlan_name,
            similarity_score,
            _PROACTIVE_SIMILARITY_THRESHOLD,
        )
        print(
            f"[{lanlan_name}] 主动搭话重复度过高，已拦截 "
            f"(similarity={similarity_score:.3f}, "
            f"threshold={_PROACTIVE_SIMILARITY_THRESHOLD:.2f})"
        )
        if _proactive_turn_still_owned(mgr, proactive_sid):
            await mgr.handle_new_message()
        else:
            active_logger.info(
                "[%s] repeat guard hit but user already took over; skip TTS cleanup",
                lanlan_name,
            )
        return _output(
            result=ProactiveChatResult(
                body=_proactive_pass_body(
                    PROACTIVE_REASON_PASS_DUPLICATE,
                    message="主动搭话重复度过高，已拦截",
                    similarity=similarity_score,
                    threshold=_PROACTIVE_SIMILARITY_THRESHOLD,
                )
            )
        )

    anti_repeat_corpus = None
    unanswered_repeat_signal = None
    silence_since = _proactive_silence_since(mgr)
    try:
        from memory.anti_repeat import get_anti_repeat_corpus

        anti_repeat_corpus = get_anti_repeat_corpus()
        await anti_repeat_corpus.apreload(lanlan_name)
    except Exception as exc:  # pragma: no cover - defensive
        active_logger.debug("[AntiRepeat] corpus unavailable: %s", exc)
        anti_repeat_corpus = None

    if anti_repeat_corpus is not None and not exempt_text_dedup:
        try:
            unanswered_repeat_signal = (
                anti_repeat_corpus.score_unanswered_proactive_draft(
                    lanlan_name,
                    response_text,
                    silence_since=silence_since,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            active_logger.debug(
                "[AntiRepeat] unanswered proactive score skipped: %s",
                exc,
            )

    unanswered_repeat_triggered = bool(
        unanswered_repeat_signal is not None and unanswered_repeat_signal.triggered
    )
    if unanswered_repeat_triggered:
        active_logger.info(
            "[%s] proactive unanswered-repeat regen "
            "(matches=%d considered=%d best_similarity=%.3f)",
            lanlan_name,
            unanswered_repeat_signal.match_count,
            unanswered_repeat_signal.considered_count,
            unanswered_repeat_signal.best_similarity,
        )
        print(
            f"[{lanlan_name}] 用户持续未互动且主动搭话内容反复，触发改写 "
            f"(matches={unanswered_repeat_signal.match_count}, "
            f"similarity={unanswered_repeat_signal.best_similarity:.3f})"
        )

    if exempt_text_dedup:
        bm25_total, bm25_terms = 0.0, {}
    elif anti_repeat_corpus is not None:
        try:
            bm25_total, bm25_terms = anti_repeat_corpus.score_draft(
                lanlan_name,
                response_text,
            )
        except Exception as exc:  # pragma: no cover - defensive
            active_logger.debug("[AntiRepeat] BM25 score skipped: %s", exc)
            bm25_total, bm25_terms = 0.0, {}
    else:
        bm25_total, bm25_terms = 0.0, {}

    if (
        unanswered_repeat_triggered
        or bm25_total >= ANTI_REPEAT_REGEN_THRESHOLD
        or literal_regen_triggered
    ):
        initial_source_tag = source_tag
        if unanswered_repeat_triggered:
            avoid_terms = _merge_regen_avoid_terms(
                (
                    bm25_terms.keys()
                    if bm25_total >= ANTI_REPEAT_REGEN_THRESHOLD
                    else ()
                ),
                unanswered_repeat_signal.repeated_terms,
            )
        elif literal_regen_triggered:
            # Only reachable on the exempt path, where BM25/unanswered never
            # score, so the repeated fragment is the whole avoidance signal.
            avoid_terms = (
                [literal_match.common_fragment]
                if literal_match.common_fragment
                else []
            )
        else:
            avoid_terms = list(bm25_terms.keys())[:ANTI_REPEAT_INJECT_TOP_K]
        repeat_reasons = tuple(
            reason
            for reason, triggered in (
                ("bm25", bm25_total >= ANTI_REPEAT_REGEN_THRESHOLD),
                ("unanswered_repeat", unanswered_repeat_triggered),
                ("literal_similarity", literal_regen_triggered),
            )
            if triggered
        )
        repeat_signature = build_repeat_signature(
            response_text,
            avoid_terms,
            language=proactive_lang,
            fallback_fragment=(
                literal_match.common_fragment if literal_regen_triggered else ""
            ),
        )

        def record_regen_effect(
            outcome: str,
            *,
            score_after: float | None = None,
            extra_reasons: tuple[str, ...] = (),
            signature: Any = KEEP_INITIAL_SIGNATURE,
        ) -> None:
            # ``repeat_reasons`` / ``repeat_signature`` describe the INITIAL
            # draft. An outcome produced by a different detector on the
            # regenerated draft has to say so, otherwise reason totals and the
            # per-candidate handling record attribute it to the wrong detector
            # and the wrong fragment.
            record_anti_repeat_decision(
                lanlan_name,
                AntiRepeatDecision(
                    source="proactive",
                    reasons=tuple(dict.fromkeys(repeat_reasons + extra_reasons)),
                    action="regenerate",
                    outcome=outcome,
                    signature=(
                        repeat_signature
                        if signature is KEEP_INITIAL_SIGNATURE
                        else signature
                    ),
                    score_before=bm25_total if bm25_total > 0 else None,
                    score_after=score_after,
                    response_id=str(proactive_sid),
                ),
            )

        active_logger.info(
            "[%s] proactive regen (bm25_score=%.2f threshold=%.2f "
            "unanswered_repeat=%s literal_similarity=%s)",
            lanlan_name,
            bm25_total,
            ANTI_REPEAT_REGEN_THRESHOLD,
            unanswered_repeat_triggered,
            f"{similarity_score:.3f}" if literal_regen_triggered else "-",
        )
        print(
            f"[{lanlan_name}] 主动搭话触发 regen "
            f"(bm25={bm25_total:.2f}, unanswered_repeat="
            f"{unanswered_repeat_triggered}, literal_repeat="
            f"{literal_regen_triggered}, "
            # A verbatim hit's avoid term is the whole caption; keep it out of
            # the persisted stdout log like the other literal-guard paths do.
            f"避开={'(整句配文)' if literal_regen_triggered else avoid_terms})"
        )
        avoid_message = render_regen_avoid_instruction(
            avoid_terms,
            proactive_lang,
            master_name,
        )
        regen_human_text = f"{avoid_message}\n\n{human_text}"
        if phase2_use_vision:
            regen_human_content: Any = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"},
                },
                {"type": "text", "text": regen_human_text},
            ]
        else:
            regen_human_content = regen_human_text
        regen_messages = [
            messages[0],
            HumanMessage(content=regen_human_content),
        ]
        regen_text = ""
        if mgr.state.is_proactive_preempted(proactive_sid):
            record_regen_effect("abandoned_user_interaction")
            active_logger.info(
                "[%s] proactive BM25 regen aborted: user preempted before ainvoke",
                lanlan_name,
            )
            return _output(
                result=ProactiveChatResult(
                    body=_proactive_pass_body(
                        PROACTIVE_REASON_DELIVERY_PREEMPTED,
                        message="BM25 regen 前用户已接管",
                    )
                )
            )
        silence_since_before_regen = _proactive_silence_since(mgr)
        try:
            async with asyncio.timeout(20.0):
                async with await make_llm(
                    temperature=1.0,
                    max_completion_tokens=PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                    use_vision=phase2_use_vision,
                    disable_thinking=phase2_disable_thinking,
                ) as regen_llm:
                    regen_response = await regen_llm.ainvoke(regen_messages)
                    regen_text = (
                        regen_response.content
                        if hasattr(regen_response, "content")
                        else ""
                    ) or ""
        except Exception as exc:
            active_logger.warning(
                "[%s] proactive BM25 regen LLM call failed: %s",
                lanlan_name,
                exc,
            )
            regen_text = ""

        silence_since_after_regen = _proactive_silence_since(mgr)
        if silence_since_after_regen is not None and (
            silence_since_before_regen is None
            or silence_since_after_regen > silence_since_before_regen
        ):
            record_regen_effect("abandoned_user_interaction")
            active_logger.info(
                "[%s] proactive regen abandoned after user interaction",
                lanlan_name,
            )
            if _proactive_turn_still_owned(mgr, proactive_sid):
                await mgr.handle_new_message()
            return _output(
                result=ProactiveChatResult(
                    body=_proactive_pass_body(
                        PROACTIVE_REASON_DELIVERY_PREEMPTED,
                        message="主动搭话改写期间用户已互动，取消投递",
                    )
                )
            )

        cleaned = (regen_text or "").strip()
        cleaned, regen_source_tag = _parse_proactive_phase2_prefix(cleaned, final=True)
        cleaned = _strip_proactive_intent_label_leak(cleaned)
        if (
            regen_source_tag == "PASS"
            or (expects_source_tag and not regen_source_tag)
            or not cleaned.strip()
            or "[PASS]" in cleaned.upper()
        ):
            record_regen_effect("regen_failed")
            active_logger.info(
                "[%s] proactive BM25 regen returned empty/PASS/untagged, drop",
                lanlan_name,
            )
            if _proactive_turn_still_owned(mgr, proactive_sid):
                await mgr.handle_new_message()
            return _output(
                result=ProactiveChatResult(
                    body=_proactive_pass_body(
                        PROACTIVE_REASON_PASS_DUPLICATE,
                        message="BM25 regen 失败，已 drop",
                    )
                )
            )

        regen_dedup_tag = (
            "CHAT"
            if (
                (regen_source_tag == "MEME" and selected_meme_link is None)
                or (regen_source_tag == "MUSIC" and selected_music_link is None)
            )
            else regen_source_tag
        )
        regen_material_key = _proactive_material_key(
            regen_dedup_tag,
            selected_music_link,
            meme_content,
        )
        regen_exempt_text_dedup = (
            regen_dedup_tag in ANTI_REPEAT_EXEMPT_SOURCE_TAGS
            and not _is_recent_proactive_material(
                lanlan_name,
                regen_dedup_tag,
                regen_material_key,
            )
        )

        # Symmetric with initial ban gate: drafts entering regeneration were clean, but rewriting
        # can introduce bans (avoidance prompts concern BM25 terms, not user
        # prohibitions). Again pure string matching, no extra LLM calls.
        #
        # ⚠️ Precede all regeneration repetition checks (BM25/unanswered long-window/
        # literal dedup), not merely ``record_regen_effect("regen_guard_passed")``.
        # Same rationale as initial output: those drops record
        # ``record_anti_repeat_decision`` with original draft fragments persisted in
        # anti_repeat_effects.json; later placement leaks banned snippets to disk
        # while only preventing delivery.
        # Passing recorded only after all gates also follows from this placement.
        regen_directive_hits = _proactive_directive_hits(lanlan_name, cleaned)
        if regen_directive_hits:
            # ⚠️ Deliberately omit record_regen_effect here. It writes
            # ``record_anti_repeat_decision`` (#2876 local repeated-expression insights), whereas
            # ban-topic is user prohibition, a separate dimension. Recording a nonrepetition
            # outcome there attributes regeneration to the wrong detector,
            # falsely claiming repetition. Anti-repeat made no decision:
            # another system terminated draft early; absence from that panel is correct.
            active_logger.info(
                "[%s] proactive regen hit user ban-topic directive "
                "(%d term(s) matched), drop",
                lanlan_name,
                len(regen_directive_hits),
            )
            if _proactive_turn_still_owned(mgr, proactive_sid):
                await mgr.handle_new_message()
            return _output(
                result=ProactiveChatResult(
                    body=_proactive_pass_body(
                        PROACTIVE_REASON_PASS_USER_DIRECTIVE,
                        message="改写后仍命中用户明确要求回避的话题，已 drop",
                        directive_match_count=len(regen_directive_hits),
                    )
                )
            )

        regen_total, regen_bm25_terms = _score_regenerated_draft(
            anti_repeat_corpus,
            lanlan_name,
            cleaned,
            exempt=regen_exempt_text_dedup,
        )
        if (
            regen_total is not None
            and regen_total >= ANTI_REPEAT_DROP_THRESHOLD
        ):
            record_regen_effect(
                "blocked_after_regen_bm25",
                score_after=regen_total,
                # Scored against the REGENERATED draft, so it carries that
                # draft's reason and phrases. Without this the block was filed
                # under whatever detector triggered the rewrite and pointed at
                # a fragment from a draft that had already been discarded.
                extra_reasons=("bm25",),
                signature=build_repeat_signature(
                    cleaned,
                    list(regen_bm25_terms)[:ANTI_REPEAT_INJECT_TOP_K],
                    language=proactive_lang,
                ),
            )
            active_logger.info(
                "[%s] proactive BM25 regen still over drop (score=%.2f)",
                lanlan_name,
                regen_total,
            )
            if _proactive_turn_still_owned(mgr, proactive_sid):
                await mgr.handle_new_message()
            return _output(
                result=ProactiveChatResult(
                    body=_proactive_pass_body(
                        PROACTIVE_REASON_PASS_DUPLICATE,
                        message="BM25 regen 后仍超阈值，已 drop",
                        bm25_score=regen_total,
                    )
                )
            )
        regen_unanswered_repeat_signal = None
        if not regen_exempt_text_dedup and anti_repeat_corpus is not None:
            try:
                regen_unanswered_repeat_signal = (
                    anti_repeat_corpus.score_unanswered_proactive_draft(
                        lanlan_name,
                        cleaned,
                        silence_since=_proactive_silence_since(mgr),
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive
                active_logger.debug(
                    "[AntiRepeat] unanswered proactive regen score skipped: %s",
                    exc,
                )
        if (
            regen_unanswered_repeat_signal is not None
            and regen_unanswered_repeat_signal.triggered
        ):
            record_regen_effect(
                "blocked_after_regen_unanswered",
                score_after=regen_total,
                # The REGENERATED draft is what tripped the unanswered-repeat
                # detector; the closure's reasons/signature describe the initial
                # one. Same correction as the literal branch below.
                extra_reasons=("unanswered_repeat",),
                signature=build_repeat_signature(
                    cleaned,
                    regen_unanswered_repeat_signal.repeated_terms,
                    language=proactive_lang,
                ),
            )
            active_logger.info(
                "[%s] proactive regen still repeats unanswered content "
                "(matches=%d considered=%d best_similarity=%.3f), drop",
                lanlan_name,
                regen_unanswered_repeat_signal.match_count,
                regen_unanswered_repeat_signal.considered_count,
                regen_unanswered_repeat_signal.best_similarity,
            )
            if _proactive_turn_still_owned(mgr, proactive_sid):
                await mgr.handle_new_message()
            return _output(
                result=ProactiveChatResult(
                    body=_proactive_pass_body(
                        PROACTIVE_REASON_PASS_DUPLICATE,
                        message="改写后仍与用户持续未回应的主动搭话内容雷同，已 drop",
                        unanswered_repeat_matches=(
                            regen_unanswered_repeat_signal.match_count
                        ),
                        unanswered_repeat_similarity=(
                            regen_unanswered_repeat_signal.best_similarity
                        ),
                    )
                )
            )
        if not regen_exempt_text_dedup:
            regen_match = _find_similar_recent_proactive_chat(lanlan_name, cleaned)
        elif regen_dedup_tag in ANTI_REPEAT_VERBATIM_GUARD_SOURCE_TAGS:
            regen_match = _find_verbatim_recent_proactive_chat(lanlan_name, cleaned)
        else:
            regen_match = ProactiveSimilarityMatch()
        regen_duplicate = regen_match.is_duplicate
        regen_similarity = regen_match.best_score
        if regen_duplicate:
            record_regen_effect(
                "blocked_after_regen_literal",
                score_after=regen_total,
                extra_reasons=("literal_similarity",),
                # Derive the fragment from the regenerated match. Passing None
                # when nothing safe can be derived is correct: an unattributed
                # record beats one pointing at the initial draft's fragment.
                signature=build_repeat_signature(
                    cleaned,
                    language=proactive_lang,
                    fallback_fragment=regen_match.common_fragment,
                ),
            )
            active_logger.info(
                "[%s] proactive BM25 regen still literal-dup (similarity=%.3f)",
                lanlan_name,
                regen_similarity,
            )
            if _proactive_turn_still_owned(mgr, proactive_sid):
                await mgr.handle_new_message()
            return _output(
                result=ProactiveChatResult(
                    body=_proactive_pass_body(
                        PROACTIVE_REASON_PASS_DUPLICATE,
                        message="BM25 regen 后字面相似度仍超阈值，已 drop",
                        similarity=regen_similarity,
                        threshold=_PROACTIVE_SIMILARITY_THRESHOLD,
                    )
                )
            )
        record_regen_effect("regen_guard_passed", score_after=regen_total)
        source_tag = regen_source_tag
        if source_tag != "MUSIC":
            if selected_music_link is not None or music_content is not None:
                active_logger.info(
                    "[%s] proactive BM25 regen final tag=%s (initial=%s); "
                    "cleared music candidate",
                    lanlan_name,
                    source_tag,
                    initial_source_tag or "(none)",
                )
            selected_music_link = None
            music_content = None
        response_text = cleaned
        full_text = cleaned

    has_music_topic = "music" in active_channels
    is_music_used = has_music_topic and source_tag == "MUSIC"
    ai_wants_music = source_tag == "MUSIC"

    if is_playing_music and ai_wants_music:
        print(
            f"[{lanlan_name}] 数据级锁触发：播放中尝试推荐新歌，"
            "已强制拦截并清空曲目列表"
        )
        is_music_used = False
        music_content = None
        source_tag = "PASS"
        if _proactive_turn_still_owned(mgr, proactive_sid):
            await mgr.handle_new_message()
        else:
            active_logger.info(
                "[%s] 降级拦截 abort 但用户已接管 (state preempted)，跳过 TTS 清理",
                lanlan_name,
            )
        return _output(
            result=ProactiveChatResult(
                body=_proactive_pass_body(
                    PROACTIVE_REASON_PASS_MODEL_PASS,
                    message=f"[{lanlan_name}] 播放中推荐拦截触发，动作已取消",
                )
            ),
            is_music_used=False,
        )
    if music_cooldown and ai_wants_music:
        print(f"[{lanlan_name}] 音乐冷却期模型输出 [MUSIC]，降级为 CHAT（不中止搭话）")
        is_music_used = False
        music_content = None
        source_tag = "CHAT"

    if not source_tag and full_text.strip():
        source_tag = "CHAT"

    return _output(is_music_used=is_music_used)


def _parse_web_screening_result(text: str) -> dict | None:
    """
    Parse the structured result of the Phase 1 web-screening LLM.
    Expected format (localized labels):
      序号：N / No: N
      话题：xxx / Topic: xxx
      来源：xxx / Source: xxx
      简述：xxx / Summary: xxx
    Returns dict(title, source, number) or None
    """  # noqa: DOCSTRING_CJK
    result = {}
    # ^ + re.MULTILINE anchors line starts, avoiding prefixed "有值得分享的话题：" matches.
    # [ \t]* replaces \s*, horizontal whitespace only, preventing next-line captures.
    patterns = {
        "title": r"^[ \t]*(?:话题|标题|Topic|Title|話題|주제|\u0422\u0435\u043c\u0430)[ \t]*[：:][ \t]*(.+)",
        "source": r"^[ \t]*(?:来源|來源|Source|出典|출처|\u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a)[ \t]*[：:][ \t]*(.+)",
        "number": r"^[ \t]*(?:序号|序號|No|番号|번호|\u041d\u043e\u043c\u0435\u0440)\.?[ \t]*[：:][ \t]*(\d+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            result[key] = match.group(1).strip()

    if result.get("title"):
        return result
    return None


def _text_is_pass_sentinel(text: str) -> bool:
    """Return True when ``text`` as a whole is the PASS skip sentinel.

    Brackets are optional: matches both "[PASS]" (the prompted form) and a
    bare "PASS" the model occasionally emits. Phase-agnostic — used by both
    the Phase 1 section parser and the Phase 2 stream guards.
    """
    return bool(re.fullmatch(r"\s*\[?\s*PASS\s*\]?\s*", text or "", re.IGNORECASE))


def _parse_unified_phase1_result(text: str) -> dict:
    """
    Parse the merged Phase 1 LLM output.

    Split into sections by the [WEB] / [MUSIC] / [MEME] markers:
    - web section: reuse the existing regexes to extract title/source/number/summary
    - music section: extract the keyword (or recognize PASS)
    - meme section: same as above

    Returns:
        {
            'web': {'title': ..., 'source': ..., 'number': ...} | None,
            'music_keyword': str | None,    # None means no keyword
            'meme_keyword': str | None,     # None means no keyword
            'web_pass': bool,               # True means this channel explicitly passed
            'music_pass': bool,
            'meme_pass': bool,
        }
    """
    result: dict = {
        "web": None,
        "music_keyword": None,
        "meme_keyword": None,
        "web_pass": False,
        "music_pass": False,
        "meme_pass": False,
    }

    # Split into [WEB] / [MUSIC] / [MEME] sections.
    # Regex split retaining labels.
    sections: dict[str, str] = {}
    current_tag = None
    current_lines: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        # Detect section labels.
        if upper.startswith("[WEB]"):
            if current_tag:
                sections[current_tag] = "\n".join(current_lines)
            current_tag = "web"
            # Label lines may contain trailing content, e.g. [WEB] [PASS].
            remainder = stripped[5:].strip()
            current_lines = [remainder] if remainder else []
        elif upper.startswith("[MUSIC]"):
            if current_tag:
                sections[current_tag] = "\n".join(current_lines)
            current_tag = "music"
            remainder = stripped[7:].strip()
            current_lines = [remainder] if remainder else []
        elif upper.startswith("[MEME]"):
            if current_tag:
                sections[current_tag] = "\n".join(current_lines)
            current_tag = "meme"
            remainder = stripped[6:].strip()
            current_lines = [remainder] if remainder else []
        else:
            current_lines.append(line)

    if current_tag:
        sections[current_tag] = "\n".join(current_lines)

    # Missing section labels: try plain-web parsing fallback.
    if not sections:
        web_parsed = _parse_web_screening_result(text)
        if web_parsed:
            result["web"] = web_parsed
        return result

    # --- Parse web section ---
    # Extract structured fields first: models often output topic details alongside template's
    # "If nothing is worth sharing: [WEB] [PASS]", making [PASS]
    # wrongly discard completed topics. Prefer parsed results.
    web_text = sections.get("web", "")
    if web_text:
        parsed_web = _parse_web_screening_result(web_text)
        if parsed_web:
            result["web"] = parsed_web
        elif _text_is_pass_sentinel(web_text):
            result["web_pass"] = True  # Actual PASS: leave web None.

    # --- Parse music section ---
    music_text = sections.get("music", "")
    if music_text:
        music_text = music_text.strip()
        if _text_is_pass_sentinel(music_text):
            result["music_pass"] = True
        elif music_text:
            # Strip prefix labels such as "关键词：" / "keyword:".
            keyword = re.sub(
                r"(?i).*?(?:关键词|搜索(?:关键词)?|keyword|search|キーワード|検索|키워드|검색|ключевое\s*слово|поиск)[：:\s]+",
                "",
                music_text,
                count=1,
            )
            keyword = keyword.strip("'\"「」【】[]《》<> \n\r\t")
            # First nonempty line.
            keyword = keyword.splitlines()[0].strip() if keyword else ""
            if keyword and not re.fullmatch(
                r"\[?\s*pass\s*\]?", keyword, re.IGNORECASE
            ):
                result["music_keyword"] = keyword

    # --- Parse meme section ---
    meme_text = sections.get("meme", "")
    if meme_text:
        meme_text = meme_text.strip()
        if _text_is_pass_sentinel(meme_text):
            result["meme_pass"] = True
        elif meme_text:
            keyword = re.sub(
                r"(?i).*?(?:关键词|keyword|キーワード|키워드|ключевое\s*слово)[：:\s]+",
                "",
                meme_text,
                count=1,
            )
            keyword = keyword.strip("'\"「」【】[]《》<> \n\r\t")
            keyword = keyword.splitlines()[0].strip() if keyword else ""
            if keyword and not re.fullmatch(
                r"\[?\s*pass\s*\]?", keyword, re.IGNORECASE
            ):
                result["meme_keyword"] = keyword

    return result


_PROACTIVE_LEGAL_SOURCE_TAGS = frozenset({"CHAT", "WEB", "PASS", "MUSIC", "MEME"})


_PROACTIVE_SCREEN_TAG_LEAKS = frozenset({"SCREEN", "SCREENSHOT", "VISION", "WINDOW"})


_PROACTIVE_BRACKET_TAG_RE = re.compile(r"^\[([A-Za-z][A-Za-z0-9_-]{0,31})\]\s*")


_PROACTIVE_LEGAL_TAG_RE = re.compile(
    r"^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*", re.IGNORECASE
)


def _parse_proactive_phase2_prefix(
    text: str, *, final: bool = False,
) -> tuple[str, str] | None:
    """Return body/source, or None while nonstandard framing is incomplete.

    A complete leading legal tag commits once its body starts (PASS aborts
    immediately); waiting through whitespace keeps split separators intact.
    Recovery of leaked labels and the legacy heading waits for EOF so arbitrary provider
    chunk boundaries cannot change path preservation or source selection.
    At EOF an unrecognized prefix is returned as ordinary text with no source.
    """
    cleaned = text.lstrip()
    if final:
        heading_text = _PROACTIVE_LEADING_IGNORABLE_RE.sub("", cleaned, count=1)
        heading = re.match(r"主动搭话\s*\n", heading_text)
        if heading:
            cleaned = heading_text[heading.end() :].lstrip()
    match = _PROACTIVE_LEGAL_TAG_RE.match(cleaned)
    if match:
        source = match.group(1).upper()
        if not final and match.end() == len(cleaned) and source != "PASS":
            return None
        return cleaned[match.end() :], source
    if not final:
        return None
    return _strip_proactive_screen_tag_leak(cleaned)


_PROACTIVE_SLASHES = "/／"


# Unicode White_Space characters that are horizontal layout only. Keep CR/LF,
# vertical tab, form feed, NEL and Unicode line/paragraph separators excluded
# so a separator on the reply's next line can never be consumed here.
_PROACTIVE_HORIZONTAL_SPACES = (
    " \t\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u202f\u205f\u3000"
)
_PROACTIVE_HSPACE_PATTERN = rf"[{re.escape(_PROACTIVE_HORIZONTAL_SPACES)}]"


# Source-ish prefixes observed from weaker models. Matching is longest-first in
# ``_strip_proactive_source_prefix`` so ``屏幕`` can never partially consume
# ``屏幕观察`` / ``当前屏幕观察``.
_PROACTIVE_SOURCE_PREFIX_LABELS = (
    ("current screen observation", "CHAT"),
    ("screen observation", "CHAT"),
    ("current screen content", "CHAT"),
    ("current screen display", "CHAT"),
    ("current screenshot", "CHAT"),
    ("current screen", "CHAT"),
    ("screen content", "CHAT"),
    ("screen display", "CHAT"),
    ("screen image", "CHAT"),
    ("active window", "CHAT"),
    ("当前屏幕观察", "CHAT"),
    ("当前活跃窗口", "CHAT"),
    ("当前屏幕内容", "CHAT"),
    ("当前屏幕显示", "CHAT"),
    ("当前屏幕画面", "CHAT"),
    ("当前屏幕截图", "CHAT"),
    ("屏幕观察", "CHAT"),
    ("当前屏幕", "CHAT"),
    ("屏幕内容", "CHAT"),
    ("屏幕显示", "CHAT"),
    ("屏幕画面", "CHAT"),
    ("屏幕截图", "CHAT"),
    ("当前画面", "CHAT"),
    ("画面内容", "CHAT"),
    ("当前窗口", "CHAT"),
    ("窗口内容", "CHAT"),
    ("窗口标题", "CHAT"),
    ("当前界面", "CHAT"),
    ("屏幕", "CHAT"),
    ("screenshot", "CHAT"),
    ("screen", "CHAT"),
    ("vision", "CHAT"),
    ("window", "CHAT"),
    ("聊天中", "CHAT"),
    ("music", "MUSIC"),
    ("chat", "CHAT"),
)


_PROACTIVE_BARE_PREFIX_LABELS = frozenset(
    label
    for label, source_tag in _PROACTIVE_SOURCE_PREFIX_LABELS
    if source_tag == "CHAT"
    and label not in {"screen", "screenshot", "vision", "window"}
)


_PROACTIVE_AMBIGUOUS_ASCII_PREFIX_LABELS = frozenset(
    label.casefold()
    for label, _source_tag in _PROACTIVE_SOURCE_PREFIX_LABELS
    if label.isascii() and " " not in label
)


_PROACTIVE_SPACED_BARE_PREFIX_LABELS = frozenset({"聊天中"})


_PROACTIVE_LEADING_INVISIBLES = "\ufeff\u200b\u200c\u200d\u2060"


_PROACTIVE_LEADING_IGNORABLE_RE = re.compile(
    rf"^[\s{re.escape(_PROACTIVE_LEADING_INVISIBLES)}]*"
)


_PROACTIVE_OBSERVED_CONTEXT_PREFIX_LABELS = frozenset(
    {
        "QQ",
        "当前界面",
        "用户当前操作",
        "上轮未收尾话题",
        "屏幕内容",
        "屏幕显示",
        "当前屏幕内容",
        "当前屏幕显示",
        "当前屏幕画面",
        "当前屏幕截图",
        "屏幕截图",
        "当前画面",
        "画面内容",
        "当前窗口",
        "窗口内容",
        "窗口标题",
    }
)


_PROACTIVE_OBSERVED_SLASH_PREFIX_LABELS = frozenset(
    {
        "聊天中",
    }
)


def _get_proactive_context_leak_labels() -> frozenset[str]:
    from config.prompts.prompts_activity import get_proactive_intent_leak_labels

    return get_proactive_intent_leak_labels() | frozenset(
        label.casefold() for label in _PROACTIVE_OBSERVED_CONTEXT_PREFIX_LABELS
    )


def _get_proactive_context_slash_leak_labels() -> frozenset[str]:
    return _get_proactive_context_leak_labels() | frozenset(
        label.casefold() for label in _PROACTIVE_OBSERVED_SLASH_PREFIX_LABELS
    )


def _label_prefix_boundary_ok(label: str, rest: str) -> bool:
    if not rest:
        return True
    ch = rest[0]
    if ch.isspace() or ch in _PROACTIVE_SLASHES + "：:":
        return True
    return (not label.isascii()) and (not ch.isascii())


def _normalize_proactive_label_gap(rest: str) -> str:
    """Drop invisibles in a known label's same-line horizontal gap."""
    gap_end = 0
    horizontal: list[str] = []
    for char in rest:
        if char in _PROACTIVE_HORIZONTAL_SPACES:
            horizontal.append(char)
            gap_end += 1
            continue
        if char in _PROACTIVE_LEADING_INVISIBLES:
            gap_end += 1
            continue
        break
    if not gap_end:
        return rest
    return "".join(horizontal) + rest[gap_end:]


def _starts_with_proactive_source_tag(text: str) -> bool:
    """Return whether text begins with a legal or known screen source tag."""
    match = _PROACTIVE_BRACKET_TAG_RE.match(text)
    if not match:
        return False
    tag = match.group(1).upper()
    return tag in _PROACTIVE_LEGAL_SOURCE_TAGS or tag in _PROACTIVE_SCREEN_TAG_LEAKS


def _strip_proactive_label_tail(rest: str) -> str:
    """Strip label punctuation without consuming a spaced reply path."""
    body = rest
    first_separator = True
    # Bounded consumption handles malformed combinations such as ``/ :``
    # without turning arbitrary reply punctuation into an unbounded rewrite.
    for _ in range(4):
        same_line = body.lstrip(_PROACTIVE_HORIZONTAL_SPACES)
        had_spacing = same_line != body
        marker = same_line[:1]
        if marker in "：:":
            body = same_line[1:]
            first_separator = False
            continue
        if marker not in _PROACTIVE_SLASHES:
            break
        slash_tail = same_line[1:2]
        slash_is_separator = (
            (first_separator and not had_spacing)
            or not slash_tail
            or slash_tail.isspace()
            or not slash_tail.isascii()
            or _starts_with_proactive_source_tag(
                _PROACTIVE_LEADING_IGNORABLE_RE.sub("", same_line[1:], count=1)
            )
        )
        if not slash_is_separator:
            break
        body = same_line[1:]
        first_separator = False
    return body.lstrip()


def _strip_proactive_label_slash_prefix(
    body: str,
    labels: frozenset[str],
) -> str | None:
    """Strip a known label using an ASCII or full-width slash separator."""
    if not body:
        return None
    folded = body.casefold()
    for label in sorted(labels, key=len, reverse=True):
        if not label:
            continue
        if folded.startswith(label):
            rest = _normalize_proactive_label_gap(body[len(label) :])
            sep = re.match(
                rf"{_PROACTIVE_HSPACE_PATTERN}*"
                rf"[{re.escape(_PROACTIVE_SLASHES)}]",
                rest,
            )
            if sep:
                return _strip_proactive_label_tail(rest)
        if body.startswith(tuple(_PROACTIVE_SLASHES)) and folded[1:].startswith(
            label
        ):
            rest = _normalize_proactive_label_gap(body[1 + len(label) :])
            if _label_prefix_boundary_ok(label, rest):
                return _strip_proactive_label_tail(rest)
    return None


def _strip_proactive_orphan_slash_prefix(body: str) -> str | None:
    """Strip a lone leading slash separator left after a leaked label."""
    if not body:
        return None
    match = re.match(
        rf"^[{re.escape(_PROACTIVE_SLASHES)}]"
        rf"(?:{_PROACTIVE_HSPACE_PATTERN}+|"
        rf"\r?\n{_PROACTIVE_HSPACE_PATTERN}*|$)",
        body,
    )
    if not match:
        return None
    rest = body[match.end() :].lstrip()
    if rest or match.end() == len(body):
        return rest
    return None


def _strip_proactive_source_prefix(body: str) -> tuple[str, str] | None:
    """Peel one known source label without guessing inside normal prose."""
    if not body:
        return None
    folded = body.casefold()
    for label, source_tag in sorted(
        _PROACTIVE_SOURCE_PREFIX_LABELS,
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        folded_label = label.casefold()

        # ``label/正文`` / ``label／正文``.
        if folded.startswith(folded_label):
            rest = _normalize_proactive_label_gap(body[len(label) :])
            slash = re.match(
                rf"{_PROACTIVE_HSPACE_PATTERN}*"
                rf"[{re.escape(_PROACTIVE_SLASHES)}]",
                rest,
            )
            if slash:
                after_slash = rest[slash.end() :]
                after = after_slash.lstrip()
                slash_is_adjacent = rest[:1] in _PROACTIVE_SLASHES
                # A single-word ASCII label may also be a route segment. Keep
                # ``screen/share`` intact, but treat ``screen/ share`` as an
                # explicitly separated leaked label.
                if (
                    folded_label in _PROACTIVE_AMBIGUOUS_ASCII_PREFIX_LABELS
                    and slash_is_adjacent
                    and after_slash == after
                    and after
                    and after[0].isascii()
                    and not after[0].isupper()
                    and not _starts_with_proactive_source_tag(after)
                ):
                    continue
                return _strip_proactive_label_tail(rest), source_tag

            # A colon is a strong separator for lowercase / all-caps internal
            # source labels. Preserve title-cased English prose such as
            # ``Current screen: the colors look unusual``.
            colon = re.match(
                rf"^{_PROACTIVE_HSPACE_PATTERN}*[：:]",
                rest,
            )
            matched_label = body[: len(label)]
            if colon and (
                not label.isascii()
                or matched_label == label
                or matched_label.isupper()
            ):
                return rest[colon.end() :].lstrip(), source_tag

            # Only the conservative bare-label subset may stand alone or as
            # an entire line. A glued phrase such as ``当前屏幕观察到……``
            # deliberately does not match this branch.
            if folded_label in _PROACTIVE_BARE_PREFIX_LABELS:
                if not rest.strip(_PROACTIVE_HORIZONTAL_SPACES):
                    return "", source_tag
                newline = re.match(
                    rf"^{_PROACTIVE_HSPACE_PATTERN}*\r?\n"
                    rf"{_PROACTIVE_HSPACE_PATTERN}*",
                    rest,
                )
                if newline:
                    return rest[newline.end() :].lstrip(), source_tag
                if folded_label in _PROACTIVE_SPACED_BARE_PREFIX_LABELS:
                    space = re.match(
                        rf"^{_PROACTIVE_HSPACE_PATTERN}+",
                        rest,
                    )
                    if space:
                        return rest[space.end() :].lstrip(), source_tag

        # ``/label正文`` / ``／label正文``. The leading slash is a strong
        # marker, so preserve the existing glued-CJK recovery while rejecting
        # ordinary words such as ``/chatbot``.
        if body.startswith(tuple(_PROACTIVE_SLASHES)) and folded[1:].startswith(
            folded_label
        ):
            rest = _normalize_proactive_label_gap(body[1 + len(label) :])
            if (
                folded_label in _PROACTIVE_AMBIGUOUS_ASCII_PREFIX_LABELS
                and rest[:1] in _PROACTIVE_SLASHES
            ):
                route_tail = rest[1:]
                if (
                    route_tail
                    and not route_tail[0].isspace()
                    and route_tail[0].isascii()
                    and not route_tail[0].isupper()
                    and not _starts_with_proactive_source_tag(route_tail)
                ):
                    continue
            if not (
                not rest
                or rest[0].isspace()
                or rest[0] in _PROACTIVE_SLASHES + "：:"
                or rest[0].isupper()
                or not rest[0].isascii()
            ):
                continue
            return _strip_proactive_label_tail(rest), source_tag
    return None


def _strip_proactive_known_prefix_tag_leak(text: str) -> tuple[str, str]:
    """Strip known leading source-label leaks such as ``/chat`` from Phase 2 text."""
    if not text:
        return "", ""
    body = _PROACTIVE_LEADING_IGNORABLE_RE.sub("", text, count=1)
    recovered_tag = ""
    context_labels = _get_proactive_context_leak_labels()

    # Bounded peeling handles stacked new-line prefixes without allowing a
    # malformed response to turn this guard into an unbounded rewrite loop.
    for _ in range(4):
        cleaned = _strip_proactive_label_slash_prefix(
            body,
            context_labels,
        )
        source_tag = "CHAT"
        if cleaned is None:
            # A whole context-label line is framing, independently of whether
            # the next line starts with prose, a route or another source tag.
            first, newline, rest = body.partition("\n")
            if newline and first.strip(
                _PROACTIVE_HORIZONTAL_SPACES + "\r" + _PROACTIVE_LEADING_INVISIBLES
            ).casefold() in context_labels:
                cleaned = rest
        if cleaned is None:
            source_prefix = _strip_proactive_source_prefix(body)
            if source_prefix is not None:
                cleaned, source_tag = source_prefix
        if cleaned is None:
            cleaned = _strip_proactive_orphan_slash_prefix(body)
            source_tag = "CHAT"
        if cleaned is None:
            break
        body = _PROACTIVE_LEADING_IGNORABLE_RE.sub("", cleaned, count=1)
        if not recovered_tag or source_tag != "CHAT":
            recovered_tag = source_tag

    if not recovered_tag:
        return text, ""
    return body, recovered_tag


def _strip_proactive_screen_tag_leak(text: str) -> tuple[str, str]:
    """Strip mistakenly emitted screen-source tags (e.g. ``[Screen]``) from Phase 2 text.

    In proactive screen-only scenarios the model occasionally spits the screen
    source out as a leading tag. Semantically such a tag just means "chatting
    about something seen on screen" = an ordinary chat, so it is normalized to
    ``CHAT``.

    Returns ``(cleaned_text, recovered_source_tag)``:
    - On hitting a known screen-leak tag → strip it. If a legal source tag
      immediately follows (combinations like ``[Screen][CHAT]``), strip that too
      and adopt the real tag; otherwise fall back to ``CHAT``.
    - No hit (no tag / legal tag / unknown tag) → returned unchanged with an
      empty recovered tag, handing back to the caller's existing no-tag handling
      (format-rescue regen / drop).

    Tag matching is case-insensitive.
    """
    if not text:
        return "", ""
    text, prefix_tag = _strip_proactive_known_prefix_tag_leak(text)
    leading_len = len(text) - len(text.lstrip())
    leading = text[:leading_len]
    body = text[leading_len:]
    match = _PROACTIVE_BRACKET_TAG_RE.match(body)
    normalized_leading = False
    if not match:
        # ``str.lstrip`` does not remove BOM / zero-width characters. Only
        # adopt the broader normalization after it exposes a recognized tag;
        # otherwise normal prose and unknown bracket text remain byte-for-byte
        # unchanged below.
        normalized_body = _PROACTIVE_LEADING_IGNORABLE_RE.sub("", text, count=1)
        normalized_match = _PROACTIVE_BRACKET_TAG_RE.match(normalized_body)
        if normalized_match:
            normalized_tag = normalized_match.group(1).upper()
            if (
                normalized_tag in _PROACTIVE_LEGAL_SOURCE_TAGS
                or normalized_tag in _PROACTIVE_SCREEN_TAG_LEAKS
            ):
                leading = ""
                body = normalized_body
                match = normalized_match
                normalized_leading = True
    if not match:
        return text, prefix_tag
    tag = match.group(1).upper()
    if tag in _PROACTIVE_LEGAL_SOURCE_TAGS:
        if prefix_tag or normalized_leading:
            return leading + body[match.end() :].lstrip(), tag
        return text, ""
    if tag not in _PROACTIVE_SCREEN_TAG_LEAKS:
        return text, prefix_tag
    rest = body[match.end() :].lstrip()
    rest, nested_prefix_tag = _strip_proactive_known_prefix_tag_leak(rest)
    normalized_rest = _PROACTIVE_LEADING_IGNORABLE_RE.sub("", rest, count=1)
    # Support [Screen][CHAT]: remove leaked label then adopt following valid source tag,
    # or literal [CHAT] leaks to TTS; default CHAT if absent.
    legal = _PROACTIVE_LEGAL_TAG_RE.match(normalized_rest)
    if legal:
        return (
            leading + normalized_rest[legal.end() :].lstrip(),
            legal.group(1).upper(),
        )
    return leading + rest, nested_prefix_tag or prefix_tag or "CHAT"


# Decoration a model may wrap a leaked label in (markdown bold/heading/bullet,
# CJK + ASCII brackets). Stripped from both ends before matching so e.g.
# "**屏幕细节轻问**" / "【回忆线索】" still resolve to the bare label.
_INTENT_LABEL_DECOR = "*-•◦·#`_~【】「」[]《》（）() \t"


def _strip_proactive_intent_label_leak(text: str) -> str:
    """Strip an internal guidance label echoed as a leading heading.

    Weak models sometimes copy a tone-angle seed or memory-cue label from
    the proactive Phase 2 prompt and emit the bare label as the first line
    of the reply; the client then splits it into its own chat bubble. Such
    labels are pure scaffolding and must never be spoken.

    Removes, from the START of ``text`` only and repeating to peel stacked
    labels:
    - a standalone first line that exactly matches a known label (optional
      decoration / trailing colon), when real content follows on a later
      line;
    - a leading ``<label>:`` / ``<label>：`` prefix on the first line,
      keeping the rest of that line as content;
    - a leading ``<label>/`` / ``/<label>`` prefix, also keeping the content.

    Exact (decoration-trimmed, casefolded) matching against the derived
    label set keeps generic words from being scrubbed out of normal speech.
    Returns ``text`` unchanged when the leading segment is not a known label.
    """
    if not text:
        return text
    labels = _get_proactive_context_leak_labels()
    if not labels:
        return text
    slash_labels = _get_proactive_context_slash_leak_labels()

    def _norm(segment: str) -> str:
        out = segment.strip().strip(_INTENT_LABEL_DECOR)
        out = out.rstrip("：:").strip(_INTENT_LABEL_DECOR)
        return out.strip()

    # Bounded peel — a handful of stacked labels at most; never loop the body.
    for _ in range(4):
        body = text.lstrip()
        if not body:
            break
        slash_cleaned = _strip_proactive_label_slash_prefix(body, slash_labels)
        if slash_cleaned is not None:
            text = slash_cleaned
            continue
        nl = body.find("\n")
        first = body if nl == -1 else body[:nl]
        rest = "" if nl == -1 else body[nl + 1 :]

        # Case 1: the whole first line is a label, with real content after it.
        if rest.strip() and _norm(first).casefold() in labels:
            text = rest
            continue

        # Case 2: "<label>：<content>" sharing one line. Take the EARLIEST
        # colon (full- or half-width), not full-width-first — otherwise a
        # half-width separator followed by a full-width colon in the body
        # (e.g. "Memory cues: ...：...") would split on the wrong colon and
        # leave the leading label unstripped.
        sep_idx = -1
        for sep in ("：", ":"):
            idx = first.find(sep)
            if idx > 0 and (sep_idx == -1 or idx < sep_idx):
                sep_idx = idx
        if sep_idx > 0:
            cand = _norm(first[:sep_idx]).casefold()
            after = first[sep_idx + 1 :].strip()
            if cand in labels and (after or rest.strip()):
                if after:
                    text = after + ("\n" + rest if rest else "")
                else:
                    text = rest
                continue
        break
    return text


def _link_matches_phase1_title(title: str, link: dict) -> bool:
    """Match a model-returned title against canonical or prompt-safe text."""

    title_lower = title.lower().strip()
    if not title_lower:
        return False
    for field in ("title", "phase1_title", "phase1_rendered_title"):
        link_title = str(link.get(field) or "").lower().strip()
        if link_title and (
            link_title == title_lower
            or link_title in title_lower
            or title_lower in link_title
        ):
            return True
    return False


_NEKO_COMMUNITY_PHASE1_SOURCE_ALIASES = {
    "喵宇宙社区",
    "喵宇宙社群",
    "neko community",
    "n.e.k.o community",
    "n.e.k.o \u30b3\u30df\u30e5\u30cb\u30c6\u30a3",
    "n.e.k.o \ucee4\ubba4\ub2c8\ud2f0",
    "\u0441\u043e\u043e\u0431\u0449\u0435\u0441\u0442\u0432\u043e n.e.k.o",
    "comunidad n.e.k.o",
    "comunidade n.e.k.o",
}


def _is_neko_community_phase1_source(source: Any) -> bool:
    """Recognize the stable community source name and common localized aliases."""

    normalized = " ".join(str(source or "").split()).casefold()
    return normalized in _NEKO_COMMUNITY_PHASE1_SOURCE_ALIASES


def _link_matches_phase1_source(source: str, link: dict[str, Any]) -> bool:
    """Match the model-returned source without losing community aliases."""

    if link.get("mode") == "community" and _is_neko_community_phase1_source(source):
        return True
    return str(link.get("source") or "").strip().casefold() == source.casefold()


def _link_has_exact_phase1_title(title: str, link: dict) -> bool:
    """Return whether a returned title exactly identifies this candidate."""

    title_lower = title.lower().strip()
    return bool(title_lower) and any(
        str(link.get(field) or "").lower().strip() == title_lower
        for field in ("title", "phase1_title", "phase1_rendered_title")
    )


def _lookup_link_by_title(title: str, all_links: list[dict]) -> dict | None:
    """Return a uniquely identified Phase 1 candidate by exact, then partial title."""

    for matcher in (_link_has_exact_phase1_title, _link_matches_phase1_title):
        matches = [link for link in all_links if matcher(title, link)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            return None
    return None


def _lookup_link_by_phase1_selection(
    selection: dict[str, Any], all_links: list[dict]
) -> dict | None:
    """Resolve a Phase 1 pick by its source-global number before title fallback."""

    source = str(selection.get("source") or "").strip()
    try:
        number = int(selection.get("number"))
    except (TypeError, ValueError):
        number = 0
    source_links = [
        link
        for link in all_links
        if str(link.get("title") or "").strip()
        and source
        and _link_matches_phase1_source(source, link)
    ]
    if number > 0 and number <= len(source_links):
        candidate = source_links[number - 1]
        if _link_has_exact_phase1_title(str(selection.get("title") or ""), candidate):
            return candidate
    return _lookup_link_by_title(
        str(selection.get("title") or ""), source_links if source_links else all_links
    )
