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
import logging
import re
from dataclasses import dataclass
from functools import partial
from typing import Any

from config import (
    ANTI_REPEAT_DROP_THRESHOLD,
    ANTI_REPEAT_EXEMPT_SOURCE_TAGS,
    ANTI_REPEAT_INJECT_TOP_K,
    ANTI_REPEAT_REGEN_THRESHOLD,
    PROACTIVE_PHASE1_FETCH_PER_SOURCE,
    PROACTIVE_PHASE1_UNIFIED_MAX_TOKENS,
    PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
    PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS,
    focus_extra_body,
    leaks_thinking_in_content,
)
from config.prompts.prompts_directives import (
    render_format_fix_instruction,
    render_regen_avoid_instruction,
)
from config.prompts.prompts_proactive import (
    BEGIN_GENERATE,
    build_unified_phase1_prompt,
    get_proactive_generate_prompt,
)
from config.prompts.prompts_sys import _loc
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
    _is_recent_proactive_material,
    _is_similar_to_recent_proactive_chat,
    _proactive_material_key,
)

logger = get_module_logger(__name__, "Main")


_PROACTIVE_LLM_RETRY_ERROR_TYPES: tuple[type[BaseException], ...] | None = None


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

    @property
    def has_vision_model(self) -> bool:
        return bool(self.vision_model and self.vision_api_key)


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


async def _make_proactive_llm(
    model_config: ProactiveModelConfig,
    *,
    temperature: float = 1.0,
    max_completion_tokens: int = PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
    use_vision: bool = False,
    disable_thinking: bool = True,
):
    """Create the configured proactive-chat model without owning lifecycle state."""
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
    return await create_chat_llm_async(  # noqa: LLM_OUTPUT_BUDGET
        model,
        base_url,
        api_key,
        **kwargs,
    )


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
) -> Phase2GuardedOutput:
    """Own Phase 2 messages, model stream, and output guards."""
    active_logger = log or logger
    use_vision = bool(screenshot_b64 and model_config.has_vision_model)
    disable_thinking = use_vision or not focus_thinking
    begin_text = _loc(BEGIN_GENERATE, proactive_lang)
    human_text = (
        f"{dynamic_context}\n\n{begin_text}" if dynamic_context else begin_text
    )
    if use_vision:
        human_content: Any = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{screenshot_b64}"
                },
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
    print(
        f"\n{'=' * 60}\n[PROACTIVE-DEBUG] Phase 2 STREAM: "
        f"model={actual_model} | vision={use_vision} | "
        f"img={'yes' if use_vision else 'no'}\n{'=' * 60}\n"
        f"{system_prompt}\n{'=' * 60}\n"
    )

    make_llm = partial(_make_proactive_llm, model_config)
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
        if not phase2_disable_thinking
        and leaks_thinking_in_content(conversation_model)
        else None
    )
    try:
        async with asyncio.timeout(25.0):
            async with (
                await make_llm(
                    temperature=1.0,
                    max_completion_tokens=PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                    use_vision=phase2_use_vision,
                    disable_thinking=phase2_disable_thinking,
                )
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
                        if (
                            len(buffer) < 80
                            and "\n" not in buffer[min(len(buffer) - 1, 10) :]
                        ):
                            continue
                        cleaned = buffer
                        prefix_match = re.search(r"主动搭话\s*\n", cleaned)
                        if prefix_match:
                            cleaned = cleaned[prefix_match.end() :]
                        cleaned = cleaned.lstrip()
                        tag_match = re.match(
                            r"^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*",
                            cleaned,
                            re.IGNORECASE,
                        )
                        if tag_match:
                            source_tag = tag_match.group(1).upper()
                            cleaned = cleaned[tag_match.end() :]
                        else:
                            cleaned, leak_tag = _strip_proactive_screen_tag_leak(
                                cleaned
                            )
                            if leak_tag:
                                source_tag = leak_tag
                        tag_parsed = True

                        if (
                            source_tag == "PASS"
                            or "[PASS]" in cleaned.upper()
                            or _text_is_pass_sentinel(cleaned)
                        ):
                            print(
                                f"[{lanlan_name}] Phase 2 流式检测到 PASS，abort"
                            )
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
                        print(
                            f"[{lanlan_name}] Phase 2 流式检测到内嵌 [PASS]，abort"
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
        cleaned = buffer
        prefix_match = re.search(r"主动搭话\s*\n", cleaned)
        if prefix_match:
            cleaned = cleaned[prefix_match.end() :]
        cleaned = cleaned.lstrip()
        tag_match = re.match(
            r"^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*",
            cleaned,
            re.IGNORECASE,
        )
        if tag_match:
            source_tag = tag_match.group(1).upper()
            cleaned = cleaned[tag_match.end() :]
        else:
            cleaned, leak_tag = _strip_proactive_screen_tag_leak(cleaned)
            if leak_tag:
                source_tag = leak_tag
        if (
            source_tag == "PASS"
            or "[PASS]" in cleaned.upper()
            or _text_is_pass_sentinel(cleaned)
        ):
            _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
        elif cleaned.strip():
            await _emit_safe(cleaned)

    if not aborted and full_text.strip() and not source_tag and expects_source_tag:
        print(
            f"[{lanlan_name}] Phase 2 输出无合法来源标签，尝试格式自救 regen"
        )
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
                    async with (
                        await make_llm(
                            temperature=1.0,
                            max_completion_tokens=PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                            use_vision=phase2_use_vision,
                            disable_thinking=phase2_disable_thinking,
                        )
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
            prefix_match = re.search(r"主动搭话\s*\n", fixed)
            if prefix_match:
                fixed = fixed[prefix_match.end() :]
            fixed = fixed.lstrip()
            fix_tag = ""
            tag_match = re.match(
                r"^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*",
                fixed,
                re.IGNORECASE,
            )
            if tag_match:
                fix_tag = tag_match.group(1).upper()
                fixed = fixed[tag_match.end() :]
            else:
                fixed, leak_tag = _strip_proactive_screen_tag_leak(fixed)
                if leak_tag:
                    fix_tag = leak_tag
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

    print(
        "\n[PROACTIVE-DEBUG] Phase 2 STREAM output "
        f"(aborted={aborted}, tag={source_tag}): {full_text[:300]}\n"
    )
    if aborted or not full_text.strip():
        final_reason = abort_reason_code or PROACTIVE_REASON_PASS_GENERATION_EMPTY
        if not mgr.state.is_proactive_preempted(proactive_sid):
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
        if not mgr.state.is_proactive_preempted(proactive_sid):
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
    print(f"\n[PROACTIVE-DEBUG] Phase 2 STREAM output: {response_text[:200]}...\n")
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
        and not any(
            channel in ("vision", "web", "meme")
            for channel in active_channels
        )
    )
    if music_only_pending and source_tag != "MUSIC":
        dedup_tag = "MUSIC"
    elif source_tag == "MEME" and selected_meme_link is None:
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
    if exempt_text_dedup:
        active_logger.info(
            "[%s] proactive text-dedup exempt: tag=%s (model_tag=%s) "
            "material=%r (fresh material, skip similarity+BM25)",
            lanlan_name,
            dedup_tag,
            source_tag,
            material_key or "(none)",
        )

    is_duplicate, similarity_score = False, 0.0
    if not exempt_text_dedup:
        is_duplicate, similarity_score = _is_similar_to_recent_proactive_chat(
            lanlan_name,
            response_text,
        )
    if is_duplicate:
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
        if not mgr.state.is_proactive_preempted(proactive_sid):
            await mgr.handle_new_message()
        else:
            active_logger.info(
                "[%s] repeat guard hit but user already took over; "
                "skip TTS cleanup",
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

    if exempt_text_dedup:
        bm25_total, bm25_terms = 0.0, {}
        anti_repeat_corpus = None
    else:
        try:
            from memory.anti_repeat import get_anti_repeat_corpus

            anti_repeat_corpus = get_anti_repeat_corpus()
            bm25_total, bm25_terms = anti_repeat_corpus.score_draft(
                lanlan_name,
                response_text,
            )
        except Exception as exc:  # pragma: no cover - defensive
            active_logger.debug("[AntiRepeat] BM25 score skipped: %s", exc)
            bm25_total, bm25_terms = 0.0, {}
            anti_repeat_corpus = None

    if bm25_total >= ANTI_REPEAT_REGEN_THRESHOLD:
        initial_source_tag = source_tag
        avoid_terms = list(bm25_terms.keys())[:ANTI_REPEAT_INJECT_TOP_K]
        active_logger.info(
            "[%s] proactive BM25 regen (score=%.2f threshold=%.2f avoid=%s)",
            lanlan_name,
            bm25_total,
            ANTI_REPEAT_REGEN_THRESHOLD,
            avoid_terms,
        )
        print(
            f"[{lanlan_name}] 主动搭话 BM25 触发 regen "
            f"(score={bm25_total:.2f} >= {ANTI_REPEAT_REGEN_THRESHOLD}, "
            f"避开={avoid_terms})"
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
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{screenshot_b64}"
                    },
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
        try:
            async with asyncio.timeout(20.0):
                async with (
                    await make_llm(
                        temperature=1.0,
                        max_completion_tokens=PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                        use_vision=phase2_use_vision,
                        disable_thinking=phase2_disable_thinking,
                    )
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

        cleaned = (regen_text or "").strip()
        regen_source_tag = ""
        prefix_match = re.search(r"主动搭话\s*\n", cleaned)
        if prefix_match:
            cleaned = cleaned[prefix_match.end() :]
        tag_match = re.match(
            r"^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*",
            cleaned,
            re.IGNORECASE,
        )
        if tag_match:
            regen_source_tag = tag_match.group(1).upper()
            cleaned = cleaned[tag_match.end() :]
        else:
            cleaned, leak_tag = _strip_proactive_screen_tag_leak(cleaned)
            if leak_tag:
                regen_source_tag = leak_tag
        cleaned = _strip_proactive_intent_label_leak(cleaned)
        if (
            regen_source_tag == "PASS"
            or (expects_source_tag and not regen_source_tag)
            or not cleaned.strip()
            or "[PASS]" in cleaned.upper()
        ):
            active_logger.info(
                "[%s] proactive BM25 regen returned empty/PASS/untagged, drop",
                lanlan_name,
            )
            if not mgr.state.is_proactive_preempted(proactive_sid):
                await mgr.handle_new_message()
            return _output(
                result=ProactiveChatResult(
                    body=_proactive_pass_body(
                        PROACTIVE_REASON_PASS_DUPLICATE,
                        message="BM25 regen 失败，已 drop",
                    )
                )
            )

        try:
            regen_total, _ = anti_repeat_corpus.score_draft(
                lanlan_name,
                cleaned,
            )
        except Exception:
            regen_total = 0.0
        if regen_total >= ANTI_REPEAT_DROP_THRESHOLD:
            active_logger.info(
                "[%s] proactive BM25 regen still over drop (score=%.2f)",
                lanlan_name,
                regen_total,
            )
            if not mgr.state.is_proactive_preempted(proactive_sid):
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
        regen_duplicate, regen_similarity = (
            _is_similar_to_recent_proactive_chat(lanlan_name, cleaned)
        )
        if regen_duplicate:
            active_logger.info(
                "[%s] proactive BM25 regen still literal-dup "
                "(similarity=%.3f)",
                lanlan_name,
                regen_similarity,
            )
            if not mgr.state.is_proactive_preempted(proactive_sid):
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
        if not mgr.state.is_proactive_preempted(proactive_sid):
            await mgr.handle_new_message()
        else:
            active_logger.info(
                "[%s] 降级拦截 abort 但用户已接管 "
                "(state preempted)，跳过 TTS 清理",
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
        print(
            f"[{lanlan_name}] 音乐冷却期模型输出 [MUSIC]，"
            "降级为 CHAT（不中止搭话）"
        )
        is_music_used = False
        music_content = None
        source_tag = "CHAT"

    if not source_tag and full_text.strip():
        source_tag = "CHAT"

    return _output(is_music_used=is_music_used)


def _parse_web_screening_result(text: str) -> dict | None:
    """
    Parse the structured result of the Phase 1 web-screening LLM.
    Expected format (Chinese or English labels):
      序号：N / No: N
      话题：xxx / Topic: xxx
      来源：xxx / Source: xxx
      简述：xxx / Summary: xxx
    Returns dict(title, source, number) or None
    """  # noqa: DOCSTRING_CJK
    result = {}
    # ^ + re.MULTILINE 锚定行首，防止匹配到 "有值得分享的话题：" 等前缀行
    # [ \t]* 替代 \s*，只吃水平空白，避免跨行捕获到下一行内容
    patterns = {
        'title': r'^[ \t]*(?:话题|标题|Topic|Title|話題|주제)[ \t]*[：:][ \t]*(.+)',
        'source': r'^[ \t]*(?:来源|Source|出典|출처)[ \t]*[：:][ \t]*(.+)',
        'number': r'^[ \t]*(?:序号|No|番号|번호)\.?[ \t]*[：:][ \t]*(\d+)',
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            result[key] = match.group(1).strip()

    if result.get('title'):
        return result
    return None

def _text_is_pass_sentinel(text: str) -> bool:
    """Return True when ``text`` as a whole is the PASS skip sentinel.

    Brackets are optional: matches both "[PASS]" (the prompted form) and a
    bare "PASS" the model occasionally emits. Phase-agnostic — used by both
    the Phase 1 section parser and the Phase 2 stream guards.
    """
    return bool(re.fullmatch(r'\s*\[?\s*PASS\s*\]?\s*', text or '', re.IGNORECASE))


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
        'web': None,
        'music_keyword': None,
        'meme_keyword': None,
        'web_pass': False,
        'music_pass': False,
        'meme_pass': False,
    }

    # 按 [WEB] / [MUSIC] / [MEME] 分段
    # 使用正则切分，保留标签
    sections: dict[str, str] = {}
    current_tag = None
    current_lines: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        # 检测段标签
        if upper.startswith('[WEB]'):
            if current_tag:
                sections[current_tag] = '\n'.join(current_lines)
            current_tag = 'web'
            # 标签行后面可能有内容（如 [WEB] [PASS]）
            remainder = stripped[5:].strip()
            current_lines = [remainder] if remainder else []
        elif upper.startswith('[MUSIC]'):
            if current_tag:
                sections[current_tag] = '\n'.join(current_lines)
            current_tag = 'music'
            remainder = stripped[7:].strip()
            current_lines = [remainder] if remainder else []
        elif upper.startswith('[MEME]'):
            if current_tag:
                sections[current_tag] = '\n'.join(current_lines)
            current_tag = 'meme'
            remainder = stripped[6:].strip()
            current_lines = [remainder] if remainder else []
        else:
            current_lines.append(line)

    if current_tag:
        sections[current_tag] = '\n'.join(current_lines)

    # 如果 LLM 没有输出段标签（fallback：尝试当作纯 web 输出解析）
    if not sections:
        web_parsed = _parse_web_screening_result(text)
        if web_parsed:
            result['web'] = web_parsed
        return result

    # --- 解析 web 段 ---
    # 先尝试提取结构化字段；LLM 经常同时输出话题详情和模板里的
    # "If nothing is worth sharing: [WEB] [PASS]" 行，导致 [PASS]
    # 误杀已填好的话题。因此优先以 parse 结果为准。
    web_text = sections.get('web', '')
    if web_text:
        parsed_web = _parse_web_screening_result(web_text)
        if parsed_web:
            result['web'] = parsed_web
        elif _text_is_pass_sentinel(web_text):
            result['web_pass'] = True  # 确实是 PASS，web 保持 None

    # --- 解析 music 段 ---
    music_text = sections.get('music', '')
    if music_text:
        music_text = music_text.strip()
        if _text_is_pass_sentinel(music_text):
            result['music_pass'] = True
        elif music_text:
            # 去掉前缀标签（如"关键词：" "keyword:" 等）
            keyword = re.sub(
                r'(?i).*?(?:关键词|搜索(?:关键词)?|keyword|search|キーワード|検索|키워드|검색|ключевое\s*слово|поиск)[：:\s]+',
                '', music_text, count=1
            )
            keyword = keyword.strip('\'"「」【】[]《》<> \n\r\t')
            # 取第一行非空内容
            keyword = keyword.splitlines()[0].strip() if keyword else ''
            if keyword and not re.fullmatch(r'\[?\s*pass\s*\]?', keyword, re.IGNORECASE):
                result['music_keyword'] = keyword

    # --- 解析 meme 段 ---
    meme_text = sections.get('meme', '')
    if meme_text:
        meme_text = meme_text.strip()
        if _text_is_pass_sentinel(meme_text):
            result['meme_pass'] = True
        elif meme_text:
            keyword = re.sub(
                r'(?i).*?(?:关键词|keyword|キーワード|키워드|ключевое\s*слово)[：:\s]+',
                '', meme_text, count=1
            )
            keyword = keyword.strip('\'"「」【】[]《》<> \n\r\t')
            keyword = keyword.splitlines()[0].strip() if keyword else ''
            if keyword and not re.fullmatch(r'\[?\s*pass\s*\]?', keyword, re.IGNORECASE):
                result['meme_keyword'] = keyword

    return result


_PROACTIVE_LEGAL_SOURCE_TAGS = frozenset({"CHAT", "WEB", "PASS", "MUSIC", "MEME"})


_PROACTIVE_SCREEN_TAG_LEAKS = frozenset({"SCREEN", "SCREENSHOT", "VISION", "WINDOW"})


_PROACTIVE_BRACKET_TAG_RE = re.compile(r"^\[([A-Za-z][A-Za-z0-9_-]{0,31})\]\s*")


_PROACTIVE_LEGAL_TAG_RE = re.compile(r"^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*", re.IGNORECASE)


_PROACTIVE_KNOWN_PREFIX_TAG_LEAKS = (
    (re.compile(r"^/(?i:chat)(?=\s|$|[A-Z]|[^\x00-\x7f])\s*"), "CHAT"),
    (re.compile(r"^(?i:chat)/(?=\s|$|[A-Z]|[^\x00-\x7f])\s*"), "CHAT"),
    (re.compile(r"^chat[ \t]*(?:\r?\n|$)\s*", re.IGNORECASE), "CHAT"),
    (re.compile(r"^/(?i:music)(?=\s|$|[A-Z]|[^\x00-\x7f])\s*"), "MUSIC"),
    (re.compile(r"^(?i:music)/(?=\s|$|[A-Z]|[^\x00-\x7f])\s*"), "MUSIC"),
    (re.compile(r"^/聊天中(?=\s|$|[A-Z]|[^\x00-\x7f])\s*"), "CHAT"),
    (re.compile(r"^/?聊天中\s*/(?=\s|$|[A-Z]|[^\x00-\x7f])\s*"), "CHAT"),
    (re.compile(r"^聊天中(?=\s|$)\s*"), "CHAT"),
    (re.compile(r"^/屏幕观察(?=\s|$|[A-Z]|[^\x00-\x7f])\s*"), "CHAT"),
    (re.compile(r"^屏幕观察/(?=\s|$|[A-Z]|[^\x00-\x7f])\s*"), "CHAT"),
    (re.compile(r"^/屏幕(?=\s|$|[A-Z]|[^\x00-\x7f])\s*"), "CHAT"),
    (re.compile(r"^屏幕\s*/(?=\s|$|[A-Z]|[^\x00-\x7f])\s*"), "CHAT"),
)


_PROACTIVE_OBSERVED_CONTEXT_PREFIX_LABELS = frozenset({
    "QQ",
    "当前界面",
    "用户当前操作",
    "上轮未收尾话题",
    "屏幕内容",
    "屏幕显示",
})


_PROACTIVE_OBSERVED_SLASH_PREFIX_LABELS = frozenset({
    "聊天中",
})


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
    if ch.isspace() or ch in "/：:":
        return True
    return (not label.isascii()) and (not ch.isascii())


def _strip_proactive_label_slash_prefix(
    body: str,
    labels: frozenset[str],
) -> str | None:
    """Strip a known leading internal label written as ``label/`` or ``/label``."""
    if not body:
        return None
    folded = body.casefold()
    for label in sorted(labels, key=len, reverse=True):
        if not label:
            continue
        if folded.startswith(label):
            rest = body[len(label):]
            sep = re.match(r"\s*/", rest)
            if sep:
                return rest[sep.end():].lstrip()
        if body.startswith("/") and folded[1:].startswith(label):
            rest = body[1 + len(label):]
            if _label_prefix_boundary_ok(label, rest):
                rest = rest.lstrip()
                if rest[:1] in "/：:":
                    rest = rest[1:]
                return rest.lstrip()
    return None


def _strip_proactive_orphan_slash_prefix(body: str) -> str | None:
    """Strip a lone leading slash separator left after a leaked label."""
    if not body:
        return None
    match = re.match(r"^/(?:[ \t]+|\r?\n[ \t]*|$)", body)
    if not match:
        return None
    rest = body[match.end():].lstrip()
    if rest or match.end() == len(body):
        return rest
    return None


def _strip_proactive_known_prefix_tag_leak(text: str) -> tuple[str, str]:
    """Strip known leading source-label leaks such as ``/chat`` from Phase 2 text."""
    if not text:
        return "", ""
    leading_len = len(text) - len(text.lstrip())
    leading = text[:leading_len]
    body = text[leading_len:]
    cleaned = _strip_proactive_label_slash_prefix(
        body,
        _get_proactive_context_leak_labels(),
    )
    if cleaned is not None:
        return leading + cleaned, "CHAT"
    cleaned = _strip_proactive_orphan_slash_prefix(body)
    if cleaned is not None:
        return leading + cleaned, "CHAT"
    for pattern, source_tag in _PROACTIVE_KNOWN_PREFIX_TAG_LEAKS:
        match = pattern.match(body)
        if match:
            rest = body[match.end():].lstrip()
            cleaned_rest = _strip_proactive_orphan_slash_prefix(rest)
            if cleaned_rest is not None:
                rest = cleaned_rest
            return leading + rest, source_tag
    return text, ""


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
    if prefix_tag:
        return text, prefix_tag
    leading_len = len(text) - len(text.lstrip())
    leading = text[:leading_len]
    body = text[leading_len:]
    match = _PROACTIVE_BRACKET_TAG_RE.match(body)
    if not match:
        return text, ""
    tag = match.group(1).upper()
    if tag in _PROACTIVE_LEGAL_SOURCE_TAGS or tag not in _PROACTIVE_SCREEN_TAG_LEAKS:
        return text, ""
    rest = body[match.end():].lstrip()
    # 兼容 [Screen][CHAT] 组合：泄漏标签后若紧跟合法来源标签，剥掉并采用真实 tag
    # （否则该 [CHAT] 字面会作为正文漏给 TTS）；没有则按 CHAT 兜底。
    legal = _PROACTIVE_LEGAL_TAG_RE.match(rest)
    if legal:
        return leading + rest[legal.end():].lstrip(), legal.group(1).upper()
    return leading + rest, "CHAT"


# Decoration a model may wrap a leaked label in (markdown bold/heading/bullet,
# CJK + ASCII brackets). Stripped from both ends before matching so e.g.
# "**屏幕细节轻问**" / "【回忆线索】" still resolve to the bare label.
_INTENT_LABEL_DECOR = '*-•◦·#`_~【】「」[]《》（）() \t'


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
        out = out.rstrip('：:').strip(_INTENT_LABEL_DECOR)
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
        nl = body.find('\n')
        first = body if nl == -1 else body[:nl]
        rest = '' if nl == -1 else body[nl + 1:]

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
        for sep in ('：', ':'):
            idx = first.find(sep)
            if idx > 0 and (sep_idx == -1 or idx < sep_idx):
                sep_idx = idx
        if sep_idx > 0:
            cand = _norm(first[:sep_idx]).casefold()
            after = first[sep_idx + 1:].strip()
            if cand in labels and (after or rest.strip()):
                if after:
                    text = after + ('\n' + rest if rest else '')
                else:
                    text = rest
                continue
        break
    return text


def _lookup_link_by_title(title: str, all_links: list[dict]) -> dict | None:
    """
    Look up the link matching a Phase 1 output title in all_web_links.
    Matching logic:
    - exact match (ignoring case and surrounding whitespace)
    - partial match (title contains or is contained, ignoring case and surrounding whitespace)
    """
    title_lower = title.lower().strip()
    for link in all_links:
        link_title = link.get('title', '').lower().strip()
        if not link_title:
            continue
        if link_title == title_lower or link_title in title_lower or title_lower in link_title:
            return link
    return None
