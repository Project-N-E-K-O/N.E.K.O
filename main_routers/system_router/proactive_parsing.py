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

"""Parsers for proactive phase 1/2 LLM output: link extraction,
screening result parsing and tag/intent-label leak stripping.

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from typing import Any

from ._shared import logger
import re


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
            weibo_or_twitter: list[dict] = []
            for item in (news.get('trending', []) or []):
                title = item.get('word', '') or item.get('name', '')
                url = item.get('url', '')
                if title and url:
                    weibo_or_twitter.append({
                        'title': title,
                        'url': url,
                        'source': '微博' if raw_data.get('region', 'china') == 'china' else 'Twitter',
                    })
            xhh_links: list[dict] = []
            for post in (raw_data.get('xhh', {}).get('posts', []) or []):
                title = post.get('title', '')
                url = post.get('url', '')
                if title and url:
                    xhh_links.append({'title': title, 'url': url, 'source': '小黑盒'})

            # news 共用一个 Phase 1 候选额度；交错合并避免微博先占满 10 条后
            # 小黑盒只能拿到尾部少量名额。
            row = 0
            groups = [group for group in (weibo_or_twitter, xhh_links) if group]
            while any(row < len(group) for group in groups):
                for group in groups:
                    if row < len(group):
                        links.append(group[row])
                row += 1
        
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
            # 每个平台先规范化为独立队列，再轮询交错。Phase 1 总候选只有
            # 12 条，若按平台整段追加，排在末尾的小黑盒等来源会长期进不了候选池。
            platform_specs = (
                [
                    ('bilibili_dynamic', 'dynamics', ('content',), 'B站'),
                    ('weibo_dynamic', 'statuses', ('content',), '微博'),
                    ('douyin_dynamic', 'dynamics', ('content',), '抖音'),
                    ('kuaishou_dynamic', 'dynamics', ('content',), '快手'),
                ]
                if region == 'china'
                else [
                    ('reddit_dynamic', 'posts', ('title', 'content'), 'Reddit'),
                    ('twitter_dynamic', 'tweets', ('content',), 'Twitter'),
                ]
            )
            platform_links: list[list[dict]] = []
            for data_key, items_key, title_keys, source_name in platform_specs:
                group: list[dict] = []
                for item in (raw_data.get(data_key, {}).get(items_key, []) or []):
                    title = next((item.get(key, '') for key in title_keys if item.get(key)), '')
                    url = item.get('url', '')
                    if title and url:
                        group.append({'title': title, 'url': url, 'source': source_name})
                if group:
                    platform_links.append(group)

            row = 0
            while any(row < len(group) for group in platform_links):
                for group in platform_links:
                    if row < len(group):
                        links.append(group[row])
                row += 1

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


PROACTIVE_REASON_CHAT_DELIVERED = "CHAT_DELIVERED"
PROACTIVE_REASON_PASS_BUSY = "PASS_BUSY"
PROACTIVE_REASON_PASS_ACTIVITY_BUSY = "PASS_ACTIVITY_BUSY"
PROACTIVE_REASON_PASS_DELIVERY_BUSY = "PASS_DELIVERY_BUSY"
PROACTIVE_REASON_PASS_DISABLED = "PASS_DISABLED"
PROACTIVE_REASON_PASS_ROUTE_ACTIVE = "PASS_ROUTE_ACTIVE"
PROACTIVE_REASON_PASS_PRIVACY = "PASS_PRIVACY"
PROACTIVE_REASON_PASS_RESTRICTED_SCREEN_ONLY = "PASS_RESTRICTED_SCREEN_ONLY"
PROACTIVE_REASON_PASS_THROTTLED = "PASS_THROTTLED"
PROACTIVE_REASON_PASS_SOURCE_EMPTY = "PASS_SOURCE_EMPTY"
PROACTIVE_REASON_PASS_MODEL_PASS = "PASS_MODEL_PASS"
PROACTIVE_REASON_PASS_GENERATION_EMPTY = "PASS_GENERATION_EMPTY"
PROACTIVE_REASON_PASS_DUPLICATE = "PASS_DUPLICATE"
PROACTIVE_REASON_DELIVERY_PREEMPTED = "DELIVERY_PREEMPTED"
PROACTIVE_REASON_DELIVERY_FAILED = "DELIVERY_FAILED"
PROACTIVE_REASON_ERROR_TIMEOUT = "ERROR_TIMEOUT"
PROACTIVE_REASON_ERROR_INTERNAL = "ERROR_INTERNAL"
PROACTIVE_REASON_ERROR_CHARACTER_NOT_FOUND = "ERROR_CHARACTER_NOT_FOUND"
PROACTIVE_REASON_ERROR_SOURCE_FETCH_FAILED = "ERROR_SOURCE_FETCH_FAILED"
PROACTIVE_REASON_PASS_UNSPECIFIED = "PASS_UNSPECIFIED"

PROACTIVE_STAGE_ENTRY_GUARD = "entry_guard"
PROACTIVE_STAGE_ACTIVITY_GATE = "activity_gate"
PROACTIVE_STAGE_SOURCE_SELECTION = "source_selection"
PROACTIVE_STAGE_MODEL_DECISION = "model_decision"
PROACTIVE_STAGE_GENERATION = "generation"
PROACTIVE_STAGE_DEDUP = "dedup"
PROACTIVE_STAGE_DELIVERY = "delivery"
PROACTIVE_STAGE_RUNTIME_ERROR = "runtime_error"
PROACTIVE_STAGE_UNKNOWN = "unknown"

_PROACTIVE_REASON_STAGE: dict[str, str] = {
    PROACTIVE_REASON_CHAT_DELIVERED: PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_REASON_PASS_BUSY: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_PASS_ACTIVITY_BUSY: PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_REASON_PASS_DELIVERY_BUSY: PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_REASON_PASS_DISABLED: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_PASS_ROUTE_ACTIVE: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_PASS_PRIVACY: PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_REASON_PASS_RESTRICTED_SCREEN_ONLY: PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_REASON_PASS_THROTTLED: PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_REASON_PASS_SOURCE_EMPTY: PROACTIVE_STAGE_SOURCE_SELECTION,
    PROACTIVE_REASON_PASS_MODEL_PASS: PROACTIVE_STAGE_MODEL_DECISION,
    PROACTIVE_REASON_PASS_GENERATION_EMPTY: PROACTIVE_STAGE_GENERATION,
    PROACTIVE_REASON_PASS_DUPLICATE: PROACTIVE_STAGE_DEDUP,
    PROACTIVE_REASON_DELIVERY_PREEMPTED: PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_REASON_DELIVERY_FAILED: PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_REASON_ERROR_TIMEOUT: PROACTIVE_STAGE_RUNTIME_ERROR,
    PROACTIVE_REASON_ERROR_INTERNAL: PROACTIVE_STAGE_RUNTIME_ERROR,
    PROACTIVE_REASON_ERROR_CHARACTER_NOT_FOUND: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_ERROR_SOURCE_FETCH_FAILED: PROACTIVE_STAGE_SOURCE_SELECTION,
    PROACTIVE_REASON_PASS_UNSPECIFIED: PROACTIVE_STAGE_UNKNOWN,
}


def _proactive_stage_for_reason(reason_code: str | None) -> str:
    if not reason_code:
        return PROACTIVE_STAGE_UNKNOWN
    return _PROACTIVE_REASON_STAGE.get(reason_code, PROACTIVE_STAGE_UNKNOWN)


def _proactive_response_body(
    action: str | None,
    reason_code: str,
    *,
    success: bool,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {"success": success, "reason_code": reason_code}
    if action is not None:
        body["action"] = action
    body.update(extra)
    body["reason_code"] = reason_code
    if not body.get("stage"):
        body["stage"] = _proactive_stage_for_reason(reason_code)
    if action is not None:
        body["action"] = action
    return body


def _proactive_pass_body(reason_code: str, **extra: Any) -> dict[str, Any]:
    success = bool(extra.pop("success", True))
    return _proactive_response_body("pass", reason_code, success=success, **extra)


def _proactive_chat_body(
    reason_code: str = PROACTIVE_REASON_CHAT_DELIVERED,
    **extra: Any,
) -> dict[str, Any]:
    success = bool(extra.pop("success", True))
    return _proactive_response_body("chat", reason_code, success=success, **extra)


def _proactive_error_body(reason_code: str, **extra: Any) -> dict[str, Any]:
    success = bool(extra.pop("success", False))
    return _proactive_response_body(None, reason_code, success=success, **extra)


def _ensure_proactive_reason_code(
    body: dict[str, Any],
    *,
    default_reason_code: str | None = None,
) -> dict[str, Any]:
    existing_reason_code = body.get("reason_code")
    if existing_reason_code:
        if not body.get("stage"):
            body["stage"] = _proactive_stage_for_reason(str(existing_reason_code))
        return body
    action = body.get("action")
    if default_reason_code is None:
        if action == "chat":
            default_reason_code = PROACTIVE_REASON_CHAT_DELIVERED
        elif action == "pass":
            default_reason_code = PROACTIVE_REASON_PASS_UNSPECIFIED
        else:
            default_reason_code = PROACTIVE_REASON_ERROR_INTERNAL
    body["reason_code"] = default_reason_code
    if not body.get("stage"):
        body["stage"] = _proactive_stage_for_reason(default_reason_code)
    return body
