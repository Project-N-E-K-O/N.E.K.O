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
from typing import Any

from config import (
    PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
    PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS,
    leaks_thinking_in_content,
)
from config.prompts.prompts_directives import render_format_fix_instruction
from utils.llm_client import HumanMessage, ThinkingStreamStripper
from utils.tokenize import count_tokens

from .contracts import (
    PROACTIVE_REASON_DELIVERY_PREEMPTED,
    PROACTIVE_REASON_PASS_GENERATION_EMPTY,
    PROACTIVE_REASON_PASS_MODEL_PASS,
    ProactiveChatResult,
    _proactive_pass_body,
)


logger = logging.getLogger(__name__)


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


def _extract_links_from_raw(mode: str, raw_data: dict) -> list[dict]:
    """
    Extract a list of link info entries from raw web data.
    args:
    - mode: data mode; supports 'news', 'video', 'home', 'personal', 'music'
    - raw_data: raw web data
    returns:
    - list[dict]: list of link info entries, each containing 'title', 'url' and 'source' fields
    """
    links = []
    try:
        if mode == 'news':
            news = raw_data.get('news', {})
            items = news.get('trending', [])
            for item in items:
                title = item.get('word', '') or item.get('name', '')
                url = item.get('url', '')
                if title and url:
                    links.append({'title': title, 'url': url, 'source': '微博' if raw_data.get('region', 'china') == 'china' else 'Twitter'})

        elif mode == 'video':
            video = raw_data.get('video', {})
            items = video.get('videos', []) or video.get('posts', [])
            for item in items:
                title = item.get('title', '')
                url = item.get('url', '')
                if title and url:
                    links.append({'title': title, 'url': url, 'source': 'B站' if raw_data.get('region', 'china') == 'china' else 'Reddit'})

        elif mode == 'home':
            bilibili = raw_data.get('bilibili', {})
            for v in (bilibili.get('videos', []) or []):
                if v.get('title') and v.get('url'):
                    links.append({'title': v['title'], 'url': v['url'], 'source': 'B站'})

            weibo = raw_data.get('weibo', {})
            for w in (weibo.get('trending', []) or []):
                if w.get('word') and w.get('url'):
                    links.append({'title': w['word'], 'url': w['url'], 'source': '微博'})

            reddit = raw_data.get('reddit', {})
            for r in (reddit.get('posts', []) or []):
                if r.get('title') and r.get('url'):
                    links.append({'title': r['title'], 'url': r['url'], 'source': 'Reddit'})

            twitter = raw_data.get('twitter', {})
            for t in (twitter.get('trending', []) or []):
                title = t.get('name', '') or t.get('word', '')
                if title and t.get('url'):
                    links.append({'title': title, 'url': t['url'], 'source': 'Twitter'})

        elif mode == 'personal':
            region = raw_data.get('region', 'china')
            if region == 'china':

                b_dyn = raw_data.get('bilibili_dynamic', {})
                for d in (b_dyn.get('dynamics', []) or []):
                    title = d.get('content', '')
                    url = d.get('url', '')
                    if title and url:
                        links.append({'title': title, 'url': url, 'source': 'B站'})

                w_dyn = raw_data.get('weibo_dynamic', {})
                for d in (w_dyn.get('statuses', []) or []):
                    title = d.get('content', '')
                    url = d.get('url', '')
                    if title and url:
                        links.append({'title': title, 'url': url, 'source': '微博'})

                d_dyn = raw_data.get('douyin_dynamic', {})
                for d in (d_dyn.get('dynamics', []) or []):
                    title = d.get('content', '')
                    url = d.get('url', '')
                    if title and url:
                        links.append({'title': title, 'url': url, 'source': '抖音'})

                k_dyn = raw_data.get('kuaishou_dynamic', {})
                for d in (k_dyn.get('dynamics', []) or []):
                    title = d.get('content', '')
                    url = d.get('url', '')
                    if title and url:
                        links.append({'title': title, 'url': url, 'source': '快手'})
            else:
                r_dyn = raw_data.get('reddit_dynamic', {})
                for d in (r_dyn.get('posts', []) or []):
                    title = d.get('title', '') or d.get('content', '')
                    url = d.get('url', '')
                    if title and url:
                        links.append({'title': title, 'url': url, 'source': 'Reddit'})

                t_dyn = raw_data.get('twitter_dynamic', {})
                for d in (t_dyn.get('tweets', []) or []):
                    title = d.get('content', '')
                    url = d.get('url', '')
                    if title and url:
                        links.append({'title': title, 'url': url, 'source': 'Twitter'})

        elif mode == 'music':
            items = raw_data.get('data', [])
            for item in items:
                title = item.get('name', '')
                artist = item.get('artist', '')
                url = item.get('url', '')
                if title and url:
                    links.append({'title': f"{title} - {artist}", 'url': url, 'source': '音乐推荐'})

    except Exception as e:
        logger.warning(f"提取链接失败 [{mode}]: {e}")
    return links


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
        'title': r'^[ \t]*(?:话题|Topic|話題|주제)[ \t]*[：:][ \t]*(.+)',
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
      keeping the rest of that line as content.

    Exact (decoration-trimmed, casefolded) matching against the derived
    label set keeps generic words from being scrubbed out of normal speech.
    Returns ``text`` unchanged when the leading segment is not a known label.
    """
    if not text:
        return text
    from config.prompts.prompts_activity import get_proactive_intent_leak_labels
    labels = get_proactive_intent_leak_labels()
    if not labels:
        return text

    def _norm(segment: str) -> str:
        out = segment.strip().strip(_INTENT_LABEL_DECOR)
        out = out.rstrip('：:').strip(_INTENT_LABEL_DECOR)
        return out.strip()

    # Bounded peel — a handful of stacked labels at most; never loop the body.
    for _ in range(4):
        body = text.lstrip()
        if not body:
            break
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
