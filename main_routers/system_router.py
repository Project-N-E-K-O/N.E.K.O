# -*- coding: utf-8 -*-
"""
System Router

Handles system-related endpoints including:
- Server shutdown
- Emotion analysis
- Steam achievements
- File utilities (file-exists, find-first-image, proxy-image)
"""

import os
import sys
import asyncio
import base64
import difflib
import re
import time
from collections import deque
from io import BytesIO
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from openai import AsyncOpenAI
from openai import APIConnectionError, InternalServerError, RateLimitError
from utils.llm_client import SystemMessage, HumanMessage, create_chat_llm
import httpx
from cachetools import TTLCache

from .shared_state import get_steamworks, get_config_manager, get_sync_message_queue, get_session_manager
from config import get_extra_body, MEMORY_SERVER_PORT
from config.prompts_sys import _loc
from config.prompts_memory import emotion_analysis_prompt, PROACTIVE_FOLLOWUP_HEADER
from config.prompts_proactive import (
    get_proactive_screen_prompt, get_proactive_generate_prompt,
    get_proactive_music_playing_hint,
    get_proactive_music_unknown_track_name,
    get_proactive_music_failsafe_hint,
    get_proactive_music_strict_constraint,
    get_proactive_format_sections,
    RECENT_PROACTIVE_CHATS_HEADER, RECENT_PROACTIVE_CHATS_FOOTER,
    RECENT_PROACTIVE_TIME_LABELS, RECENT_PROACTIVE_CHANNEL_LABELS,
    BEGIN_GENERATE,
    SCREEN_SECTION_HEADER, SCREEN_SECTION_FOOTER,
    SCREEN_WINDOW_TITLE, SCREEN_IMG_HINT,
    EXTERNAL_TOPIC_HEADER, EXTERNAL_TOPIC_FOOTER,
    PROACTIVE_SOURCE_LABELS,
    PROACTIVE_MUSIC_TAG_INSTRUCTIONS,
    MUSIC_SEARCH_RESULT_TEXTS,
)
from utils.workshop_utils import get_workshop_path
from utils.screenshot_utils import compress_screenshot, COMPRESS_TARGET_HEIGHT, COMPRESS_JPEG_QUALITY
from utils.language_utils import detect_language, translate_text, normalize_language_code, get_global_language
from utils.web_scraper import (
    fetch_trending_content, format_trending_content,
    fetch_window_context_content, format_window_context_content,
    fetch_video_content, format_video_content,
    fetch_news_content, format_news_content,
    fetch_personal_dynamics, format_personal_dynamics,
)
from utils.music_crawlers import fetch_music_content
from utils.meme_fetcher import fetch_meme_content, MEME_ALLOWED_HOSTS
from utils.logger_config import get_module_logger

router = APIRouter(prefix="/api", tags=["system"])
logger = get_module_logger(__name__, "Main")

# 统一的表情包图源白名单由 utils.meme_fetcher 维护，本文件仅用于引入


@router.get("/token-usage")
async def get_token_usage(days: int = 7):
    """返回最近 N 天的 LLM token 用量统计。"""
    from utils.token_tracker import TokenTracker
    return TokenTracker.get_instance().get_stats(days=min(days, 90))


@router.get("/pending-notices")
async def get_pending_notices():
    """前端页面加载时拉取待弹通知（只读快照，不清空队列）。
    
    返回 {"notices": [...], "cursor": N}；前端确认后须将 cursor 回传给 ack 接口，
    确保只删除本次已展示的通知，不会误删两次请求之间新入队的条目。
    """
    from main_logic.core import peek_prominent_notices
    notices, cursor = peek_prominent_notices()
    return {"notices": notices, "cursor": cursor}


@router.post("/pending-notices/ack")
async def ack_pending_notices(request: Request):
    """前端展示完通知后调用，仅删除 cursor 以内的通知（游标确认，避免 TOCTOU）。"""
    from main_logic.core import drain_prominent_notices
    try:
        body = await request.json()
        cursor = int(body.get("cursor", 0))
    except Exception:
        cursor = 0
    drain_prominent_notices(cursor)
    return {"ok": True}


# --- 主动搭话近期记录暂存区 ---
# {lanlan_name: deque([(timestamp, message), ...], maxlen=10)}
_proactive_chat_history: dict[str, deque] = {}
_proactive_topic_history: dict[str, deque] = {}

_RECENT_CHAT_MAX_AGE_SECONDS = 3600  # 1小时内的搭话记录
_RECENT_TOPIC_MAX_AGE_SECONDS = 3600  # 1小时内避免重复外部话题
_PROACTIVE_SIMILARITY_THRESHOLD = 0.94  # 高阈值，尽量避免误杀
_PHASE1_FETCH_PER_SOURCE = 10  # Phase 1 每个信息源固定抓取条数
_PHASE1_TOTAL_TOPIC_TARGET = 20  # Phase 1 输入给筛选模型的总候选目标条数

# --- 来源动态权重系统 ---
_SOURCE_WEIGHT_DECAY_LAMBDA = 0.002   # 指数衰减系数，半衰期 ≈ 5.8 分钟
_SOURCE_WEIGHT_K = 0.30               # freshness 惩罚系数：freshness = 1 / (1 + k * raw_score)
_SOURCE_WEIGHT_FLOOR = 0.20           # 归一化权重绝对下限


def _extract_links_from_raw(mode: str, raw_data: dict) -> list[dict]:
    """
    从原始 web 数据中提取链接信息列表
    args:
    - mode: 数据模式，支持 'news', 'video', 'home', 'personal', 'music'
    - raw_data: 原始 web 数据
    returns:
    - list[dict]: 包含链接信息的列表，每个元素包含 'title', 'url', 'source' 字段
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
    解析 Phase 1 Web 筛选 LLM 的结构化结果。
    期望格式：
      序号：N / No: N
      话题：xxx / Topic: xxx
      来源：xxx / Source: xxx
      简述：xxx / Summary: xxx
    返回 dict(title, source, number) 或 None
    """
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


def _parse_unified_phase1_result(text: str) -> dict:
    """
    解析合并 Phase 1 LLM 输出。

    按 [WEB] / [MUSIC] / [MEME] 标记分段：
    - web 段: 复用现有正则提取 title/source/number/summary
    - music 段: 提取关键词（或识别 PASS）
    - meme 段: 同上

    Returns:
        {
            'web': {'title': ..., 'source': ..., 'number': ...} | None,
            'music_keyword': str | None,    # None 表示 PASS 或不存在
            'meme_keyword': str | None,     # None 表示 PASS 或不存在
        }
    """
    result: dict = {'web': None, 'music_keyword': None, 'meme_keyword': None}

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
        elif '[PASS]' in web_text.upper():
            pass  # 确实是 PASS，web 保持 None

    # --- 解析 music 段 ---
    music_text = sections.get('music', '')
    if music_text:
        music_text = music_text.strip()
        if '[PASS]' not in music_text.upper() and music_text:
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
        if '[PASS]' not in meme_text.upper() and meme_text:
            keyword = re.sub(
                r'(?i).*?(?:关键词|keyword|キーワード|키워드|ключевое\s*слово)[：:\s]+',
                '', meme_text, count=1
            )
            keyword = keyword.strip('\'"「」【】[]《》<> \n\r\t')
            keyword = keyword.splitlines()[0].strip() if keyword else ''
            if keyword and not re.fullmatch(r'\[?\s*pass\s*\]?', keyword, re.IGNORECASE):
                result['meme_keyword'] = keyword

    return result


def _lookup_link_by_title(title: str, all_links: list[dict]) -> dict | None:
    """
    根据 Phase 1 输出的标题在 all_web_links 中查找对应链接
    匹配逻辑：
    - 完全匹配（忽略大小写和前后空白）
    - 部分匹配（标题包含或被包含，忽略大小写和前后空白）
    """
    title_lower = title.lower().strip()
    for link in all_links:
        link_title = link.get('title', '').lower().strip()
        if not link_title:
            continue
        if link_title == title_lower or link_title in title_lower or title_lower in link_title:
            return link
    return None


def _format_recent_proactive_chats(lanlan_name: str, lang: str = 'zh') -> str:
    """
    将近期搭话记录格式化为可注入prompt的文本段（含相对时间和来源通道）
    逻辑：
    - 从 _proactive_chat_history 中获取指定模型的搭话记录
    - 过滤出最近 _RECENT_CHAT_MAX_AGE_SECONDS 秒内的记录
    - 根据 lang 格式化时间标签（'zh'、'en'、'ja'、'ko'）
    - 格式化来源通道标签（'vision'、'web'）
    """
    history = _proactive_chat_history.get(lanlan_name)
    if not history:
        return ""
    now = time.time()
    recent = [entry for entry in history if now - entry[0] < _RECENT_CHAT_MAX_AGE_SECONDS]
    if not recent:
        return ""

    tl = RECENT_PROACTIVE_TIME_LABELS.get(lang, RECENT_PROACTIVE_TIME_LABELS['zh'])
    cl = RECENT_PROACTIVE_CHANNEL_LABELS.get(lang, RECENT_PROACTIVE_CHANNEL_LABELS['zh'])

    def _rel(ts):
        """
        格式化时间标签
        args:
        - ts: 时间戳（秒）
        returns:
        - str: 格式化后的时间标签
        """
        d = int(now - ts)
        if d < 60:
            return tl[0]
        m = d // 60
        if m < 60:
            return tl['m'].format(m)
        return tl['h'].format(m // 60)

    header = _loc(RECENT_PROACTIVE_CHATS_HEADER, lang)
    footer = _loc(RECENT_PROACTIVE_CHATS_FOOTER, lang)
    lines = []
    for entry in recent:
        ts, msg = entry[0], entry[1]
        ch = entry[2] if len(entry) > 2 else ''
        # 过滤掉 vision 通道的记录，避免 AI 引用已过期的屏幕内容产生幻觉
        if ch == 'vision':
            continue
        tag = _rel(ts)
        if ch:
            tag += f"·{cl.get(ch, ch)}"
        lines.append(f"- [{tag}] {msg}")
    if not lines:
        return ""
    return f"\n{header}\n" + "\n".join(lines) + f"\n{footer}\n"


def _record_proactive_chat(lanlan_name: str, message: str, channel: str = ''):
    """
    记录一次成功的主动搭话（附带来源通道）
    逻辑：
    - 获取当前时间戳
    - 将搭话记录（时间戳、消息内容、通道）追加到 _proactive_chat_history 中指定模型的队列中
    - 若队列已满，自动弹出最早的记录,确保队列长度不超过 maxlen（默认 10）
    args:
    - lanlan_name: 模型名称
    - message: 搭话内容
    - channel: 来源通道（可选，默认 'vision'）
    """
    if lanlan_name not in _proactive_chat_history:
        _proactive_chat_history[lanlan_name] = deque(maxlen=10)
    _proactive_chat_history[lanlan_name].append((time.time(), message, channel))


def _normalize_text_for_similarity(text: str) -> str:
    """
    文本归一化（保守策略）：
    - 小写
    - 合并连续空白
    仅做轻量归一，避免因过度清洗导致误杀。
    """
    text = (text or "").strip().lower()
    return re.sub(r'\s+', ' ', text)


def _is_similar_to_recent_proactive_chat(lanlan_name: str, message: str) -> tuple[bool, float]:
    """
    判断 message 是否与近期主动搭话高度相似（高阈值防误杀）。
    返回 (is_duplicate, best_score)。
    """
    history = _proactive_chat_history.get(lanlan_name)
    if not history or not message.strip():
        return False, 0.0

    now = time.time()
    current = _normalize_text_for_similarity(message)
    if not current:
        return False, 0.0

    best = 0.0
    for entry in history:
        ts, old_msg = entry[0], entry[1]
        if now - ts >= _RECENT_CHAT_MAX_AGE_SECONDS:
            continue
        old_norm = _normalize_text_for_similarity(old_msg)
        if not old_norm:
            continue
        score = difflib.SequenceMatcher(None, current, old_norm).ratio()
        if score > best:
            best = score
        if score >= _PROACTIVE_SIMILARITY_THRESHOLD:
            return True, score
    return False, best


def _build_topic_dedup_key(topic_title: str = '', topic_source: str = '', topic_url: str = '') -> str:
    """
    构建话题去重键，优先使用 URL（更稳定）；没有 URL 时退化到 source+title。
    """
    url = (topic_url or '').strip().lower()
    if url:
        return f"url::{url}"
    source = re.sub(r'\s+', ' ', (topic_source or '').strip().lower())
    title = re.sub(r'\s+', ' ', (topic_title or '').strip().lower())
    if title:
        return f"st::{source}::{title}"
    return ''


def _is_recent_topic_used(lanlan_name: str, topic_key: str) -> bool:
    """
    判断某个话题 key 是否在近期已被使用。
    """
    if not topic_key:
        return False
    history = _proactive_topic_history.get(lanlan_name)
    if not history:
        return False
    now = time.time()
    for ts, old_key in history:
        if now - ts < _RECENT_TOPIC_MAX_AGE_SECONDS and old_key == topic_key:
            return True
    return False


def _record_topic_usage(lanlan_name: str, topic_key: str):
    """
    记录一次话题 key 使用。
    """
    if not topic_key:
        return
    if lanlan_name not in _proactive_topic_history:
        _proactive_topic_history[lanlan_name] = deque(maxlen=100)
    _proactive_topic_history[lanlan_name].append((time.time(), topic_key))


def _compute_source_weights(
    lanlan_name: str,
    candidate_channels: list[str],
) -> dict[str, float]:
    """
    计算各来源的归一化权重。

    算法：
    1. 从 _proactive_chat_history 取 1h 内记录
    2. raw_score[ch] = Σ exp(-λ·age)  (每次使用按时间衰减累加)
    3. freshness[ch] = 1 / (1 + k·raw_score[ch])
    4. 归一化 weight[ch] = freshness[ch] / Σ freshness

    无历史记录时返回均匀分布。

    Args:
        lanlan_name: 角色名
        candidate_channels: 参与权重计算的通道列表（不含 vision）

    Returns:
        {channel: normalized_weight}，weight 之和为 1.0
    """
    import math
    n = len(candidate_channels)
    if n == 0:
        return {}

    # 收集 1h 内历史
    history = _proactive_chat_history.get(lanlan_name)
    now = time.time()

    raw_scores: dict[str, float] = {ch: 0.0 for ch in candidate_channels}

    if history:
        for ts, _msg, ch in history:
            age = now - ts
            if age > _SOURCE_WEIGHT_WINDOW:
                continue
            if ch in raw_scores:
                raw_scores[ch] += math.exp(-_SOURCE_WEIGHT_DECAY_LAMBDA * age)

    # freshness: 使用越多 → raw 越高 → freshness 越低
    freshness: dict[str, float] = {}
    for ch in candidate_channels:
        freshness[ch] = 1.0 / (1.0 + _SOURCE_WEIGHT_K * raw_scores[ch])

    total = sum(freshness.values())
    if total <= 0:
        # 不可能发生，但做防御
        return {ch: 1.0 / n for ch in candidate_channels}

    return {ch: freshness[ch] / total for ch in candidate_channels}


def _filter_sources_by_weight(weights: dict[str, float]) -> set[str]:
    """
    返回应被剔除的 channel 集合。

    阈值 = min(_SOURCE_WEIGHT_FLOOR, 1 / N)
    - 4 通道时 threshold=0.20，2 次使用触发剔除
    - 6 通道时 threshold=0.167，竞争更激烈

    Args:
        weights: _compute_source_weights 返回的归一化权重

    Returns:
        应被剔除的 channel 名称集合
    """
    n = len(weights)
    if n <= 1:
        return set()  # 只剩 1 个来源时不剔除

    threshold = min(_SOURCE_WEIGHT_FLOOR, 1.0 / n)
    return {ch for ch, w in weights.items() if w < threshold}


# 复用 _RECENT_CHAT_MAX_AGE_SECONDS 作为权重窗口
_SOURCE_WEIGHT_WINDOW = _RECENT_CHAT_MAX_AGE_SECONDS


def _is_path_within_base(base_dir: str, candidate_path: str) -> bool:
    """
    
    安全检查 candidate_path 是否在 base_dir 内
    需要使用 os.path.commonpath 方法,防止路径遍历攻击
    调用该方法前，必须先将两个路径（candidate_path 和 base_dir）转换为绝对路径，
    并通过 os.path.realpath 解析（解析符号链接、./.. 等相对路径）
    args:
    - base_dir: 基础目录（绝对路径）
    - candidate_path: 候选路径（绝对路径）
    returns:
    - bool: True 如果 candidate_path 在 base_dir 内，False 否则
    """
    try:
        # Normalize both paths for case-insensitivity on Windows
        norm_base = os.path.normcase(os.path.realpath(base_dir))
        norm_candidate = os.path.normcase(os.path.realpath(candidate_path))
        
        # os.path.commonpath raises ValueError if paths are on different drives (Windows)
        common = os.path.commonpath([norm_base, norm_candidate])
        return common == norm_base
    except (ValueError, TypeError):
        # Different drives or invalid paths
        return False

def _get_app_root():
    """
    获取应用根目录，兼容开发环境和PyInstaller打包后的环境
    """
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS
        else:
            return os.path.dirname(sys.executable)
    else:
        return os.getcwd()


def _log_news_content(lanlan_name: str, news_content: dict):
    """
    记录新闻内容获取详情
    """
    region = news_content.get('region', 'china')
    news_data = news_content.get('news', {})
    if news_data.get('success'):
        trending_list = news_data.get('trending', [])
        words = [item.get('word', '') for item in trending_list[:5]]
        if words:
            source = "微博热议话题" if region == 'china' else "Twitter热门话题"
            print(f"[{lanlan_name}] 成功获取{source}:")
            for word in words:
                print(f"  - {word}")


def _log_video_content(lanlan_name: str, video_content: dict):
    """
    记录视频内容获取详情
    """
    region = video_content.get('region', 'china')
    video_data = video_content.get('video', {})
    if video_data.get('success'):
        if region == 'china':
            videos = video_data.get('videos', [])
            titles = [video.get('title', '') for video in videos[:5]]
            if titles:
                print(f"[{lanlan_name}] 成功获取B站视频:")
                for title in titles:
                    print(f"  - {title}")
        else:
            posts = video_data.get('posts', [])
            titles = [post.get('title', '') for post in posts[:5]]
            if titles:
                print(f"[{lanlan_name}] 成功获取Reddit热门帖子:")
                for title in titles:
                    print(f"  - {title}")


def _log_trending_content(lanlan_name: str, trending_content: dict):
    """
    记录首页推荐内容获取详情
    """
    content_details = []
    
    bilibili_data = trending_content.get('bilibili', {})
    if bilibili_data.get('success'):
        videos = bilibili_data.get('videos', [])
        titles = [video.get('title', '') for video in videos[:5]]
        if titles:
            content_details.append("B站视频:")
            for title in titles:
                content_details.append(f"  - {title}")
    
    weibo_data = trending_content.get('weibo', {})
    if weibo_data.get('success'):
        trending_list = weibo_data.get('trending', [])
        words = [item.get('word', '') for item in trending_list[:5]]
        if words:
            content_details.append("微博话题:")
            for word in words:
                content_details.append(f"  - {word}")
    
    reddit_data = trending_content.get('reddit', {})
    if reddit_data.get('success'):
        posts = reddit_data.get('posts', [])
        titles = [post.get('title', '') for post in posts[:5]]
        if titles:
            content_details.append("Reddit热门帖子:")
            for title in titles:
                content_details.append(f"  - {title}")
    
    twitter_data = trending_content.get('twitter', {})
    if twitter_data.get('success'):
        trending_list = twitter_data.get('trending', [])
        words = [item.get('word', '') for item in trending_list[:5]]
        if words:
            content_details.append("Twitter热门话题:")
            for word in words:
                content_details.append(f"  - {word}")
    
    if content_details:
        print(f"[{lanlan_name}] 成功获取首页推荐:")
        for detail in content_details:
            print(detail)
    else:
        print(f"[{lanlan_name}] 成功获取首页推荐 - 但未获取到具体内容")

def _log_music_content(lanlan_name: str, music_content: dict):
    """记录音乐内容获取详情"""
    if music_content.get('success'):
        tracks = music_content.get('data', [])
        titles = [f"{t.get('name', '')} - {t.get('artist', '')}" for t in tracks[:5]]
        if titles:
            logger.info(f"[{lanlan_name}] 成功获取音乐推荐:")
            for title in titles:
                logger.info(f"  - {title}")
    else:
        logger.warning(f"[{lanlan_name}] 音乐获取失败: {music_content.get('error', '未知错误')}")

def _format_music_content(music_content: dict, lang: str = 'zh') -> str:
    """Formats music content into a readable string with multi-language support."""
    if not music_content.get('success'):
        return ""
    
    t = MUSIC_SEARCH_RESULT_TEXTS.get(lang, MUSIC_SEARCH_RESULT_TEXTS['zh'])
    
    output_lines = [t['title']]
    tracks = music_content.get('data', [])
    for i, track in enumerate(tracks[:5], 1):
        # 使用多语言字典中的"未知"占位符，替代硬编码的中文
        name = track.get('name') or t['unknown_track']
        artist = track.get('artist') or t['unknown_artist']
        album = track.get('album', '')
        
        if album:
            output_lines.append(f"{i}. 《{name}》 - {artist}（{t['album']}：{album}）")
        else:
            output_lines.append(f"{i}. 《{name}》 - {artist}")
    
    # 如果除了标题没有抓到任何歌曲，则返回空
    if len(output_lines) == 1:
        return ""
        
    # 删除了原来的 desc 尾注，保持素材的客观中立
    return "\n".join(output_lines)


def _append_music_recommendations(
    source_links: list[dict],
    music_content: dict | None,
    limit: int = 3,
) -> int:
    """Deduplicate and append music tracks from *music_content* into *source_links*.

    Returns the number of tracks actually appended (0 when nothing new).
    """
    music_raw = music_content.get('raw_data', {}) if music_content else {}
    tracks = music_raw.get('data')
    if not tracks:
        return 0

    existing_signatures = {
        (
            (link.get('url') or '').strip(),
            (link.get('title') or '').strip(),
            (link.get('artist') or '').strip(),
        )
        for link in source_links
        if isinstance(link, dict) and link.get('source') == '音乐推荐'
    }

    appended = 0
    for track in tracks[:limit]:
        title = (track.get('name') or '未知曲目').strip()
        artist = (track.get('artist') or '未知艺术家').strip()
        url = (track.get('url') or '').strip()
        sig = (url, title, artist)
        if sig in existing_signatures:
            continue
        source_links.append({
            'title': title,
            'artist': artist,
            'url': url,
            'cover': track.get('cover', ''),
            'source': '音乐推荐',
        })
        existing_signatures.add(sig)
        appended += 1
    return appended


def _log_personal_dynamics(lanlan_name: str, personal_content: dict):
    """
    记录个人动态内容获取详情
    """
    content_details = []
    
    bilibili_dynamic = personal_content.get('bilibili_dynamic', {})
    if bilibili_dynamic.get('success'):
        dynamics = bilibili_dynamic.get('dynamics', [])
        bilibili_contents = [dynamic.get('content', dynamic.get('title', '')) for dynamic in dynamics[:5]]
        if bilibili_contents:
            content_details.append("B站动态:")
            for content in bilibili_contents:
                content_details.append(f"  - {content}")
    
    weibo_dynamic = personal_content.get('weibo_dynamic', {})
    if weibo_dynamic.get('success'):
        dynamics = weibo_dynamic.get('statuses', [])
        weibo_contents = [dynamic.get('content', '') for dynamic in dynamics[:5]]
        if weibo_contents:
            content_details.append("微博动态:")
            for content in weibo_contents:
                content_details.append(f"  - {content}")
                
    if content_details:
        print(f"[{lanlan_name}] 成功获取个人动态:")
        for detail in content_details:
            print(detail)
    else:
        print(f"[{lanlan_name}] 成功获取个人动态 - 但未获取到具体内容")

@router.post('/emotion/analysis')
async def emotion_analysis(request: Request):
    """
    表情分析接口
    func:
    - 接收文本输入，调用配置的情绪分析模型进行分析，返回情绪类别和置信度
    - 支持从请求参数覆盖默认配置的API密钥和模型名称，增强灵活性
    - 对模型响应进行智能解析，兼容不同格式（纯文本、markdown代码块、JSON字符串等），提高鲁棒性
    - 根据置信度自动调整情绪类别，当置信度较低时将情绪设置为 neutral，提升结果可靠性
    - 将分析结果推送到监控系统（如果提供了 lanlan_name），实现与前端的实时交互和展示
    """
    try:
        _config_manager = get_config_manager()
        data = await request.json()
        if not data or 'text' not in data:
            return {"error": "请求体中必须包含text字段"}
        
        text = data['text']
        api_key = data.get('api_key')
        model = data.get('model')
        
        # 使用参数或默认配置，使用 .get() 安全获取避免 KeyError
        emotion_config = _config_manager.get_model_api_config('emotion')
        emotion_api_key = emotion_config.get('api_key')
        emotion_model = emotion_config.get('model')
        emotion_base_url = emotion_config.get('base_url')
        
        # 优先使用请求参数，其次使用配置
        api_key = api_key or emotion_api_key
        model = model or emotion_model
        
        if not api_key:
            return {"error": "情绪分析模型配置缺失: API密钥未提供且配置中未设置默认密钥"}
        
        if not model:
            return {"error": "情绪分析模型配置缺失: 模型名称未提供且配置中未设置默认模型"}
        
        # 构建请求消息
        messages = [
            {
                "role": "system", 
                "content": emotion_analysis_prompt
            },
            {
                "role": "user", 
                "content": text
            }
        ]

        # 异步调用模型
        request_params = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            # Gemini 模型可能返回 markdown 格式，需要更多 token
            "max_completion_tokens": 40
        }
        
        # 只有在需要时才添加 extra_body
        extra_body = get_extra_body(model)
        if extra_body:
            request_params["extra_body"] = extra_body
        
        from utils.token_tracker import set_call_type
        set_call_type("emotion")
        
        # 使用异步上下文管理器确保 client 正确关闭
        async with AsyncOpenAI(api_key=api_key, base_url=emotion_base_url) as client:
            response = await client.chat.completions.create(**request_params)

        # 解析响应
        result_text = response.choices[0].message.content.strip()

        # 处理 markdown 代码块格式（Gemini 可能返回 ```json {...} ``` 格式）
        # 首先尝试使用正则表达式提取第一个代码块
        code_block_match = re.search(r"```(?:json)?\s*(.+?)\s*```", result_text, flags=re.S)
        if code_block_match:
            result_text = code_block_match.group(1).strip()
        elif result_text.startswith("```"):
            # 回退到原有的行分割逻辑
            lines = result_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]  # 移除第一行
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]  # 移除最后一行
            result_text = "\n".join(lines).strip()
        
        # 尝试解析JSON响应
        try:
            import json
            result = json.loads(result_text)
            # 获取emotion和confidence
            emotion = result.get("emotion", "neutral")
            confidence = result.get("confidence", 0.5)
            
            # 当confidence小于0.3时，自动将emotion设置为neutral
            if confidence < 0.3:
                emotion = "neutral"
            
            # 获取 lanlan_name 并推送到 monitor
            lanlan_name = data.get('lanlan_name')
            sync_message_queue = get_sync_message_queue()
            if lanlan_name and lanlan_name in sync_message_queue:
                sync_message_queue[lanlan_name].put({
                    "type": "json",
                    "data": {
                        "type": "emotion",
                        "emotion": emotion,
                        "confidence": confidence
                    }
                })
            
            return {
                "emotion": emotion,
                "confidence": confidence
            }
        except json.JSONDecodeError:
            # 如果JSON解析失败，返回简单的情感判断
            return {
                "emotion": "neutral",
                "confidence": 0.5
            }
            
    except Exception as e:
        logger.error(f"情感分析失败: {e}")
        return {
            "error": f"情感分析失败: {str(e)}",
            "emotion": "neutral",
            "confidence": 0.0
        }


@router.post('/steam/set-achievement-status/{name}')
async def set_achievement_status(name: str):
    """
    设置Steam成就状态接口
    func:
    - 接收成就名称作为路径参数，调用Steamworks API设置成就状态
    - 先请求当前统计数据并运行回调，确保数据已加载
    - 检查成就当前状态，若已解锁则直接返回成功
    - 若未解锁，尝试设置成就，若成功则返回成功，否则等待1秒后重试一次
    - 最多重试10次，若仍失败则返回错误，提示可能的配置问题
    """
    steamworks = get_steamworks()
    if steamworks is not None:
        try:
            # 先请求统计数据并运行回调，确保数据已加载
            steamworks.UserStats.RequestCurrentStats()
            # 运行回调等待数据加载（多次运行以确保接收到响应）
            for _ in range(10):
                steamworks.run_callbacks()
                await asyncio.sleep(0.1)
            
            achievement_status = steamworks.UserStats.GetAchievement(name)
            logger.info(f"Achievement status: {achievement_status}")
            if not achievement_status:
                result = steamworks.UserStats.SetAchievement(name)
                if result:
                    logger.info(f"成功设置成就: {name}")
                    steamworks.UserStats.StoreStats()
                    steamworks.run_callbacks()
                    return JSONResponse(content={"success": True, "message": f"成就 {name} 处理完成"})
                else:
                    # 第一次失败，等待后重试一次
                    logger.warning(f"设置成就首次尝试失败，正在重试: {name}")
                    await asyncio.sleep(0.5)
                    steamworks.run_callbacks()
                    result = steamworks.UserStats.SetAchievement(name)
                    if result:
                        logger.info(f"成功设置成就（重试后）: {name}")
                        steamworks.UserStats.StoreStats()
                        steamworks.run_callbacks()
                        return JSONResponse(content={"success": True, "message": f"成就 {name} 处理完成"})
                    else:
                        logger.error(f"设置成就失败: {name}，请确认成就ID在Steam后台已配置")
                        return JSONResponse(content={"success": False, "error": f"设置成就失败: {name}，请确认成就ID在Steam后台已配置"}, status_code=500)
            else:
                logger.info(f"成就已解锁，无需重复设置: {name}")
                return JSONResponse(content={"success": True, "message": f"成就 {name} 处理完成"})
        except Exception as e:
            logger.error(f"设置成就失败: {e}")
            return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)
    else:
        return JSONResponse(content={"success": False, "error": "Steamworks未初始化"}, status_code=503)


@router.post('/steam/update-playtime')
async def update_playtime(request: Request):
    """
    更新游戏时长统计（PLAY_TIME_SECONDS）
    """
    steamworks = get_steamworks()
    if steamworks is not None:
        try:
            data = await request.json()
            seconds_to_add = data.get('seconds', 10)

            # 验证 seconds 参数
            try:
                seconds_to_add = int(seconds_to_add)
                if seconds_to_add < 0:
                    return JSONResponse(
                        content={"success": False, "error": "seconds must be non-negative"},
                        status_code=400
                    )
            except (ValueError, TypeError):
                return JSONResponse(
                    content={"success": False, "error": "seconds must be a valid integer"},
                    status_code=400
                )

            # 注意:不需要每次都调用 RequestCurrentStats()
            # RequestCurrentStats() 应该只在应用启动时调用一次
            # 频繁调用可能导致性能问题和同步延迟
            # 这里直接获取和更新统计值即可

            # 获取当前游戏时长（如果统计不存在，从 0 开始）
            try:
                current_playtime = steamworks.UserStats.GetStatInt('PLAY_TIME_SECONDS')
            except Exception as e:
                logger.warning(f"获取 PLAY_TIME_SECONDS 失败，从 0 开始: {e}")
                current_playtime = 0

            # 增加时长
            new_playtime = current_playtime + seconds_to_add

            # 设置新的时长
            try:
                result = steamworks.UserStats.SetStat('PLAY_TIME_SECONDS', new_playtime)

                if result:
                    # 存储统计数据
                    steamworks.UserStats.StoreStats()
                    steamworks.run_callbacks()

                    logger.debug(f"游戏时长已更新: {current_playtime}s -> {new_playtime}s (+{seconds_to_add}s)")

                    return JSONResponse(content={
                        "success": True,
                        "totalPlayTime": new_playtime,
                        "added": seconds_to_add
                    })
                else:
                    logger.debug("SetStat 返回 False - PLAY_TIME_SECONDS 统计可能未在 Steamworks 后台配置")
                    # 即使失败也返回成功，避免前端报错
                    return JSONResponse(content={
                        "success": True,
                        "totalPlayTime": new_playtime,
                        "added": seconds_to_add,
                        "warning": "Steam stat not configured"
                    })
            except Exception as stat_error:
                logger.warning(f"设置 Steam 统计失败: {stat_error} - 统计可能未在 Steamworks 后台配置")
                # 即使失败也返回成功，避免前端报错
                return JSONResponse(content={
                    "success": True,
                    "totalPlayTime": new_playtime,
                    "added": seconds_to_add,
                    "warning": "Steam stat not configured"
                })

        except Exception as e:
            logger.error(f"更新游戏时长失败: {e}")
            return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)
    else:
        return JSONResponse(content={"success": False, "error": "Steamworks未初始化"}, status_code=503)


@router.get('/steam/list-achievements')
async def list_achievements():
    """
    列出Steam后台已配置的所有成就（调试用）
    """
    steamworks = get_steamworks()
    if steamworks is not None:
        try:
            steamworks.UserStats.RequestCurrentStats()
            for _ in range(10):
                steamworks.run_callbacks()
                await asyncio.sleep(0.1)
            
            num_achievements = steamworks.UserStats.GetNumAchievements()
            achievements = []
            for i in range(num_achievements):
                name = steamworks.UserStats.GetAchievementName(i)
                if name:
                    # 如果是bytes类型，解码为字符串
                    if isinstance(name, bytes):
                        name = name.decode('utf-8')
                    status = steamworks.UserStats.GetAchievement(name)
                    achievements.append({"name": name, "unlocked": status})
            
            logger.info(f"Steam后台已配置 {num_achievements} 个成就: {achievements}")
            return JSONResponse(content={"count": num_achievements, "achievements": achievements})
        except Exception as e:
            logger.error(f"获取成就列表失败: {e}")
            return JSONResponse(content={"error": str(e)}, status_code=500)
    else:
        return JSONResponse(content={"error": "Steamworks未初始化"}, status_code=500)


@router.get('/file-exists')
async def check_file_exists(path: str = None):
    """
    检查文件是否存在

    Security: Validates against path traversal attacks by:
    - URL-decoding the path
    - Normalizing the path (resolves . and ..)
    - Rejecting any path containing .. components (prevents escaping to parent dirs)
    - Using os.path.realpath to get the canonical path
    
    Note: This endpoint allows access to user Documents and Steam Workshop
    locations, so no whitelist restriction is applied.
    """
    try:
        if not path:
            return JSONResponse(content={"exists": False}, status_code=400)
        
        # 解码URL编码的路径
        decoded_path = unquote(path)
        
        # Windows路径处理 - normalize slashes
        if os.name == 'nt':
            decoded_path = decoded_path.replace('/', '\\')
        
        # Security: Reject path traversal attempts
        # Normalize first to catch encoded variants like %2e%2e
        normalized = os.path.normpath(decoded_path)
        
        # After normpath, check if path tries to escape via ..
        # Split and check each component to be thorough
        parts = normalized.split(os.sep)
        if '..' in parts:
            logger.warning(f"Rejected path traversal attempt in file-exists: {decoded_path}")
            return JSONResponse(content={"exists": False}, status_code=400)
        
        # Resolve to canonical absolute path
        real_path = os.path.realpath(normalized)
        
        # Check if the file exists
        exists = os.path.exists(real_path) and os.path.isfile(real_path)
        
        return JSONResponse(content={"exists": exists})
        
    except Exception as e:
        logger.error(f"检查文件存在失败: {e}")
        return JSONResponse(content={"exists": False}, status_code=500)


@router.get('/find-first-image')
async def find_first_image(folder: str = None):
    """
    查找指定文件夹中的预览图片 - 增强版，添加了严格的安全检查
    
    安全注意事项：
    1. 只允许访问项目内特定的安全目录
    2. 防止路径遍历攻击
    3. 限制返回信息，避免泄露文件系统信息
    4. 记录可疑访问尝试
    5. 只返回小于 1MB 的图片（Steam创意工坊预览图大小限制）
    """
    MAX_IMAGE_SIZE = 1 * 1024 * 1024  # 1MB
    
    try:
        # 检查参数有效性
        if not folder:
            logger.warning("收到空的文件夹路径请求")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # 安全警告日志记录
        logger.warning(f"预览图片查找请求: {folder}")
        
        # 获取基础目录和允许访问的目录列表
        base_dir = _get_app_root()
        allowed_dirs = [
            os.path.realpath(os.path.join(base_dir, 'static')),
            os.path.realpath(os.path.join(base_dir, 'assets'))
        ]
        
        # 添加"我的文档/Xiao8"目录到允许列表
        if os.name == 'nt':  # Windows系统
            documents_path = os.path.join(os.path.expanduser('~'), 'Documents', 'Xiao8')
            if os.path.exists(documents_path):
                real_doc_path = os.path.realpath(documents_path)
                allowed_dirs.append(real_doc_path)
                logger.info(f"find-first-image: 添加允许的文档目录: {real_doc_path}")
        
        # 解码URL编码的路径
        decoded_folder = unquote(folder)
        
        # Windows路径处理
        if os.name == 'nt':
            decoded_folder = decoded_folder.replace('/', '\\')
        
        # 额外的安全检查：拒绝包含路径遍历字符的请求
        if '..' in decoded_folder or '//' in decoded_folder:
            logger.warning(f"检测到潜在的路径遍历攻击: {decoded_folder}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=403)
        
        # 规范化路径以防止路径遍历攻击
        try:
            real_folder = os.path.realpath(decoded_folder)
        except Exception as e:
            logger.error(f"路径规范化失败: {e}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # 检查路径是否在允许的目录内 - 使用 commonpath 防止前缀攻击
        is_allowed = any(_is_path_within_base(allowed_dir, real_folder) for allowed_dir in allowed_dirs)
        
        if not is_allowed:
            logger.warning(f"访问被拒绝：路径不在允许的目录内 - {real_folder}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=403)
        
        # 检查文件夹是否存在
        if not os.path.exists(real_folder) or not os.path.isdir(real_folder):
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # 只查找指定的8个预览图片名称，按优先级顺序
        preview_image_names = [
            'preview.jpg', 'preview.png',
            'thumbnail.jpg', 'thumbnail.png',
            'icon.jpg', 'icon.png',
            'header.jpg', 'header.png'
        ]
        
        for image_name in preview_image_names:
            image_path = os.path.join(real_folder, image_name)
            try:
                # 检查文件是否存在
                if os.path.exists(image_path) and os.path.isfile(image_path):
                    # 检查文件大小是否小于 1MB
                    file_size = os.path.getsize(image_path)
                    if file_size >= MAX_IMAGE_SIZE:
                        logger.info(f"跳过大于1MB的图片: {image_name} ({file_size / 1024 / 1024:.2f}MB)")
                        continue
                    
                    # 再次验证图片文件路径是否在允许的目录内 - 使用 commonpath 防止前缀攻击
                    real_image_path = os.path.realpath(image_path)
                    if any(_is_path_within_base(allowed_dir, real_image_path) for allowed_dir in allowed_dirs):
                        # 只返回相对路径或文件名，不返回完整的文件系统路径，避免信息泄露
                        # 计算相对于base_dir的相对路径
                        try:
                            relative_path = os.path.relpath(real_image_path, base_dir)
                            return JSONResponse(content={"success": True, "imagePath": relative_path})
                        except ValueError:
                            # 如果无法计算相对路径（例如跨驱动器），只返回文件名
                            return JSONResponse(content={"success": True, "imagePath": image_name})
            except Exception as e:
                logger.error(f"检查图片文件 {image_name} 失败: {e}")
                continue
        
        return JSONResponse(content={"success": False, "error": "未找到小于1MB的预览图片文件"})
        
    except Exception as e:
        logger.error(f"查找预览图片文件失败: {e}")
        return JSONResponse(content={"success": False, "error": "服务器内部错误"}, status_code=500)

# 统一的表情包代理缓存，使用 byte-based 限制 (50MB)，防止 OOM
MEME_PROXY_CACHE = TTLCache(
    maxsize=50 * 1024 * 1024,  # 50MB 内存预算
    ttl=1800,
    getsizeof=lambda item: len(item.get('body', b''))
)

@router.get('/meme/proxy-image')
async def proxy_meme_image(url: str):
    """
    代理远程表情包图片，解决跨域问题，包含 SSRF 防护
    """
    import time
    
    # 检查缓存
    cache_key = url
    if cache_key in MEME_PROXY_CACHE:
        logger.info(f"[Meme Proxy] 命中缓存: {url[:60]}...")
        cached = MEME_PROXY_CACHE[cache_key]
        return Response(
            content=cached['body'],
            media_type=cached['content_type'],
            headers={
                'Cache-Control': 'public, max-age=86400',
                'X-Cache': 'HIT',
                'X-Content-Type-Options': 'nosniff'
            }
        )
    
    try:
        logger.info(f"[Meme Proxy] 收到代理请求, url: {url[:100] if url else 'None'}...")
        
        if not url:
            return JSONResponse(content={"success": False, "error": "缺少URL参数"}, status_code=400)
        
        decoded_url = unquote(url)
        if not decoded_url.startswith(('http://', 'https://')):
            return JSONResponse(content={"success": False, "error": "无效的URL"}, status_code=400)
        
        allowed_hosts = MEME_ALLOWED_HOSTS
        
        from urllib.parse import urlparse, urljoin
        parsed = urlparse(decoded_url)
        hostname = (parsed.hostname or '').lower()
        
        if not any(hostname == host or hostname.endswith('.' + host) for host in allowed_hosts):
            logger.warning(f"[Meme Proxy] 非法域名请求: {hostname}")
            return JSONResponse(content={"success": False, "error": f"不允许代理该域名: {hostname}"}, status_code=403)

        # 构建请求头
        # 【修复】完善所有域名的 Referer 映射，避免被反爬拦截
        referer_map = {
            'img.soutula.com': 'https://fabiaoqing.com/',
            'fabiaoqing.com': 'https://fabiaoqing.com/',
            'qn.doutub.com': 'https://www.doutub.com/',
            'doutub.com': 'https://www.doutub.com/',
            'i.imgflip.com': 'https://imgflip.com/',
            'imgflip.com': 'https://imgflip.com/',
            'soutula.com': 'https://fabiaoqing.com/',
        }
        referer = referer_map.get(hostname, f'{parsed.scheme}://{hostname}/')
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Referer': referer,
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8'
        }

        # 使用流式下载以严格控制资源大小，防止内存溢出或大文件攻击
        MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB 限制
        
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            current_url = decoded_url
            for _ in range(4):  # 最多跟随 3 次重定向 (4次请求)
                async with client.stream("GET", current_url, headers=headers) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            break
                        
                        new_url = urljoin(current_url, location)
                        new_parsed = urlparse(new_url)
                        new_hostname = (new_parsed.hostname or '').lower()
                        
                        if not any(new_hostname == host or new_hostname.endswith('.' + host) for host in allowed_hosts):
                            logger.warning(f"[Meme Proxy] 重定向到非法域名: {new_hostname}")
                            return JSONResponse(content={"success": False, "error": "非法重定向"}, status_code=403)
                        
                        current_url = new_url
                        continue
                    
                    resp.raise_for_status()
                    
                    # 校验 Content-Type (严格白名单，防 SVG XSS 注入)
                    raw_content_type = resp.headers.get('Content-Type', '').lower()
                    content_type = raw_content_type.split(';', 1)[0].strip()
                    allowed_content_types = {
                        'image/jpeg', 'image/png', 'image/gif', 
                        'image/webp', 'image/avif', 'image/bmp'
                    }
                    if content_type not in allowed_content_types:
                        logger.warning(f"[Meme Proxy] 拒绝非安全图片内容: {raw_content_type}")
                        return JSONResponse(content={"success": False, "error": "格式不支持或含有潜在风险"}, status_code=403)
                    
                    # 校验 Content-Length (如果存在)
                    content_length = resp.headers.get('Content-Length')
                    if content_length:
                        try:
                            declared_size = int(content_length)
                        except (ValueError, TypeError):
                            declared_size = None  # 解析失败就当未知长度，靠流式校验兜底
                        if declared_size is not None and declared_size > MAX_IMAGE_SIZE:
                            logger.warning(f"[Meme Proxy] 资源过大 (Content-Length): {content_length}")
                            return JSONResponse(content={"success": False, "error": "资源超过大小限制 (10MB)"}, status_code=413)

                    # 流式读取内容并累加大小校验
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_IMAGE_SIZE:
                            logger.warning(f"[Meme Proxy] 资源过大 (实际读取): {len(body)}")
                            return JSONResponse(content={"success": False, "error": "资源超过大小限制 (10MB)"}, status_code=413)

                    # 存入 TTLCache
                    MEME_PROXY_CACHE[cache_key] = {
                        'body': bytes(body),
                        'content_type': content_type
                    }
                    
                    return Response(
                        content=bytes(body),
                        media_type=content_type,
                        headers={
                            'Cache-Control': 'public, max-age=86400',
                            'X-Cache': 'MISS',
                            'X-Content-Type-Options': 'nosniff'
                        }
                    )
            
            return JSONResponse(content={"success": False, "error": "过多的重定向"}, status_code=400)

    except httpx.TimeoutException:
        return JSONResponse(content={"success": False, "error": "请求超时"}, status_code=504)
    except Exception as e:
        logger.error(f"[Meme Proxy] 代理失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)

# 辅助函数

@router.get('/steam/proxy-image')
async def proxy_image(image_path: str):
    """
    代理访问本地图片文件，支持绝对路径和相对路径，特别是Steam创意工坊目录
    """

    try:
        logger.info(f"代理图片请求，原始路径: {image_path}")
        
        # 解码URL编码的路径（处理双重编码情况）
        decoded_path = unquote(image_path)
        # 再次解码以处理可能的双重编码
        decoded_path = unquote(decoded_path)
        
        logger.info(f"解码后的路径: {decoded_path}")
        
        # 检查是否是远程URL，如果是则直接返回错误（目前只支持本地文件）
        if decoded_path.startswith(('http://', 'https://')):
            return JSONResponse(content={"success": False, "error": "暂不支持远程图片URL"}, status_code=400)
        
        # 获取基础目录和允许访问的目录列表
        base_dir = _get_app_root()
        allowed_dirs = [
            os.path.realpath(os.path.join(base_dir, 'static')),
            os.path.realpath(os.path.join(base_dir, 'assets'))
        ]
        
        
        # 添加get_workshop_path()返回的路径作为允许目录，支持相对路径解析
        try:
            workshop_base_dir = os.path.abspath(os.path.normpath(get_workshop_path()))
            if os.path.exists(workshop_base_dir):
                real_workshop_dir = os.path.realpath(workshop_base_dir)
                if real_workshop_dir not in allowed_dirs:
                    allowed_dirs.append(real_workshop_dir)
                    logger.info(f"添加允许的默认创意工坊目录: {real_workshop_dir}")
        except Exception as e:
            logger.warning(f"无法添加默认创意工坊目录: {str(e)}")
        
        # 动态添加路径到允许列表：如果请求的路径包含创意工坊相关标识，则允许访问
        try:
            # 检查解码后的路径是否包含创意工坊相关路径标识
            if ('steamapps\\workshop' in decoded_path.lower() or 
                'steamapps/workshop' in decoded_path.lower()):
                
                # 获取创意工坊父目录
                workshop_related_dir = None
                
                # 方法1：如果路径存在，获取文件所在目录或直接使用目录路径
                if os.path.exists(decoded_path):
                    if os.path.isfile(decoded_path):
                        workshop_related_dir = os.path.dirname(decoded_path)
                    else:
                        workshop_related_dir = decoded_path
                
                # 方法2：尝试从路径中提取创意工坊相关部分
                if not workshop_related_dir:
                    match = re.search(r'(.*?steamapps[/\\]workshop)', decoded_path, re.IGNORECASE)
                    if match:
                        workshop_related_dir = match.group(1)
                
                # 方法3：如果是Steam创意工坊内容路径，获取content目录
                if not workshop_related_dir:
                    content_match = re.search(r'(.*?steamapps[/\\]workshop[/\\]content)', decoded_path, re.IGNORECASE)
                    if content_match:
                        workshop_related_dir = content_match.group(1)
                
                # 方法4：如果是Steam创意工坊内容路径，添加整个steamapps/workshop目录
                if not workshop_related_dir:
                    steamapps_match = re.search(r'(.*?steamapps)', decoded_path, re.IGNORECASE)
                    if steamapps_match:
                        workshop_related_dir = os.path.join(steamapps_match.group(1), 'workshop')
                
                # 如果找到了相关目录，添加到允许列表
                if workshop_related_dir:
                    # 确保目录存在
                    if os.path.exists(workshop_related_dir):
                        real_workshop_dir = os.path.realpath(workshop_related_dir)
                        if real_workshop_dir not in allowed_dirs:
                            allowed_dirs.append(real_workshop_dir)
                            logger.info(f"动态添加允许的创意工坊相关目录: {real_workshop_dir}")
                    else:
                        # 如果目录不存在，尝试直接添加steamapps/workshop路径
                        workshop_match = re.search(r'(.*?steamapps[/\\]workshop)', decoded_path, re.IGNORECASE)
                        if workshop_match:
                            potential_dir = workshop_match.group(0)
                            if os.path.exists(potential_dir):
                                real_workshop_dir = os.path.realpath(potential_dir)
                                if real_workshop_dir not in allowed_dirs:
                                    allowed_dirs.append(real_workshop_dir)
                                    logger.info(f"动态添加允许的创意工坊目录: {real_workshop_dir}")
        except Exception as e:
            logger.warning(f"动态添加创意工坊路径失败: {str(e)}")
        
        logger.info(f"当前允许的目录列表: {allowed_dirs}")

        # Windows路径处理：确保路径分隔符正确
        if os.name == 'nt':  # Windows系统
            # 替换可能的斜杠为反斜杠，确保Windows路径格式正确
            decoded_path = decoded_path.replace('/', '\\')
            # 处理可能的双重编码问题
            if decoded_path.startswith('\\\\'):
                decoded_path = decoded_path[2:]  # 移除多余的反斜杠前缀
        
        # 尝试解析路径
        final_path = None
        
        # 特殊处理：如果路径包含steamapps/workshop，直接检查文件是否存在
        if ('steamapps\\workshop' in decoded_path.lower() or 'steamapps/workshop' in decoded_path.lower()):
            if os.path.exists(decoded_path) and os.path.isfile(decoded_path):
                final_path = decoded_path
                logger.info(f"直接允许访问创意工坊文件: {final_path}")
        
        # 尝试作为绝对路径
        if final_path is None:
            if os.path.exists(decoded_path) and os.path.isfile(decoded_path):
                # 规范化路径以防止路径遍历攻击
                real_path = os.path.realpath(decoded_path)
                # 检查路径是否在允许的目录内 - 使用 commonpath 防止前缀攻击
                if any(_is_path_within_base(allowed_dir, real_path) for allowed_dir in allowed_dirs):
                    final_path = real_path
        
        # 尝试备选路径格式
        if final_path is None:
            alt_path = decoded_path.replace('\\', '/')
            if os.path.exists(alt_path) and os.path.isfile(alt_path):
                real_path = os.path.realpath(alt_path)
                # 使用 commonpath 防止前缀攻击
                if any(_is_path_within_base(allowed_dir, real_path) for allowed_dir in allowed_dirs):
                    final_path = real_path
        
        # 尝试相对路径处理 - 相对于static目录
        if final_path is None:
            # 对于以../static开头的相对路径，尝试直接从static目录解析
            if decoded_path.startswith('..\\static') or decoded_path.startswith('../static'):
                # 提取static后面的部分
                relative_part = decoded_path.split('static')[1]
                if relative_part.startswith(('\\', '/')):
                    relative_part = relative_part[1:]
                # 构建完整路径
                relative_path = os.path.join(allowed_dirs[0], relative_part)  # static目录
                if os.path.exists(relative_path) and os.path.isfile(relative_path):
                    real_path = os.path.realpath(relative_path)
                    # 使用 commonpath 防止前缀攻击
                    if any(_is_path_within_base(allowed_dir, real_path) for allowed_dir in allowed_dirs):
                        final_path = real_path
        
        # 尝试相对于默认创意工坊目录的路径处理
        if final_path is None:
            try:
                workshop_base_dir = os.path.abspath(os.path.normpath(get_workshop_path()))
                
                # 尝试将解码路径作为相对于创意工坊目录的路径
                rel_workshop_path = os.path.join(workshop_base_dir, decoded_path)
                rel_workshop_path = os.path.normpath(rel_workshop_path)
                
                logger.info(f"尝试相对于创意工坊目录的路径: {rel_workshop_path}")
                
                if os.path.exists(rel_workshop_path) and os.path.isfile(rel_workshop_path):
                    real_path = os.path.realpath(rel_workshop_path)
                    # 确保路径在允许的目录内 - 使用 commonpath 防止前缀攻击
                    if _is_path_within_base(workshop_base_dir, real_path):
                        final_path = real_path
                        logger.info(f"找到相对于创意工坊目录的图片: {final_path}")
            except Exception as e:
                logger.warning(f"处理相对于创意工坊目录的路径失败: {str(e)}")
        
        # 如果仍未找到有效路径，返回错误
        if final_path is None:
            return JSONResponse(content={"success": False, "error": f"文件不存在或无访问权限: {decoded_path}"}, status_code=404)
        
        # 检查文件扩展名是否为图片
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        if os.path.splitext(final_path)[1].lower() not in image_extensions:
            return JSONResponse(content={"success": False, "error": "不是有效的图片文件"}, status_code=400)
        
        # 检查文件大小是否超过50MB限制
        MAX_IMAGE_SIZE = 50 * 1024 * 1024  # 50MB
        file_size = os.path.getsize(final_path)
        if file_size > MAX_IMAGE_SIZE:
            logger.warning(f"图片文件大小超过限制: {final_path} ({file_size / 1024 / 1024:.2f}MB > 50MB)")
            return JSONResponse(content={"success": False, "error": f"图片文件大小超过50MB限制 ({file_size / 1024 / 1024:.2f}MB)"}, status_code=413)
        
        # 读取图片文件
        with open(final_path, 'rb') as f:
            image_data = f.read()
        
        # 根据文件扩展名设置MIME类型
        ext = os.path.splitext(final_path)[1].lower()
        mime_type = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.webp': 'image/webp'
        }.get(ext, 'application/octet-stream')
        
        # 返回图片数据
        return Response(content=image_data, media_type=mime_type)
    except Exception as e:
        logger.error(f"代理图片访问失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": f"访问图片失败: {str(e)}"}, status_code=500)

@router.get('/get_window_title')
async def get_window_title_api():
    """
    获取当前活跃窗口标题（仅支持Windows）
    """
    try:
        from utils.web_scraper import get_active_window_title
        title = get_active_window_title()
        if title:
            return JSONResponse({"success": True, "window_title": title})
        return JSONResponse({"success": False, "window_title": None})
    except Exception as e:
        logger.error(f"获取窗口标题失败: {e}")
        return JSONResponse({"success": False, "window_title": None})


@router.get('/screenshot')
async def backend_screenshot(request: Request):
    """
    后端截图兜底：当前端所有屏幕捕获 API 都失败时，由后端用 pyautogui 截取本机屏幕。
    安全限制：仅允许来自 loopback 地址的请求。返回 JPEG base64 DataURL。
    """
    client_host = request.client.host if request.client else ''
    if client_host not in ('127.0.0.1', '::1', 'localhost'):
        return JSONResponse({"success": False, "error": "only available from localhost"}, status_code=403)

    try:
        import pyautogui
    except ImportError:
        return JSONResponse({"success": False, "error": "pyautogui not installed"}, status_code=501)

    try:
        shot = pyautogui.screenshot()
        if shot.mode in ('RGBA', 'LA', 'P'):
            shot = shot.convert('RGB')

        # macOS 黑屏检测：仅在 macOS 上执行——未授权 Screen Recording 时 pyautogui 返回全黑图片
        # 其他平台（Windows/Linux）全黑截图属正常内容，不应拦截
        if sys.platform == "darwin":
            # 低分辨率采样：把图缩到 16×16 后用 PIL extrema 检测，避免全量 numpy 数组的内存开销
            try:
                thumb = shot.resize((16, 16))
                extrema = thumb.getextrema()  # ((min_r, max_r), (min_g, max_g), (min_b, max_b))
                if all(mx <= 1 for _, mx in extrema):
                    logger.warning("后端截图检测到全黑图片，可能缺少 Screen Recording 权限")
                    return JSONResponse({"success": False, "error": "screenshot is blank (Screen Recording permission may be denied)"}, status_code=403)
            except Exception:
                logger.debug("macOS blank-screen detection failed, skipping check", exc_info=True)

        jpg_bytes = compress_screenshot(shot, target_h=COMPRESS_TARGET_HEIGHT, quality=COMPRESS_JPEG_QUALITY)
        b64 = base64.b64encode(jpg_bytes).decode('utf-8')
        data_url = f"data:image/jpeg;base64,{b64}"
        return JSONResponse({"success": True, "data": data_url, "size": len(jpg_bytes)})
    except Exception as e:
        logger.error(f"后端截图失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ================================================================
# 主动搭话响应构建 (Response builder pure function)
# ================================================================
def build_proactive_response(source_tag: str, ctx: dict) -> tuple[str, list]:
    primary_channel = 'unknown'
    source_links = []
    lan_name = ctx.get('lanlan_name', 'System')
    
    match source_tag:
        case 'CHAT':
            primary_channel = 'vision'
        case 'WEB':
            # 使用细粒度 web 子通道（news/video/home/personal），fallback 到 'web'
            web_link = ctx.get('selected_web_link')
            primary_channel = web_link.get('mode', 'web') if web_link else 'web'
            if web_link:
                source_links.append(web_link)
                logger.debug(f"[{lan_name}] Phase 2 确定选择 WEB (子通道: {primary_channel})，已添加链接")
        case 'MUSIC':
            primary_channel = 'music'
            if ctx.get('selected_music_link'):
                source_links.append(ctx['selected_music_link'])
                logger.debug(f"[{lan_name}] Phase 2 确定选择 MUSIC，已添加链接")
        case 'MEME':
            primary_channel = 'meme'
            if ctx.get('selected_meme_link'):
                source_links.append(ctx['selected_meme_link'])
                logger.debug(f"[{lan_name}] Phase 2 确定选择 MEME，已添加相关链接")
            else:
                logger.warning(f"[{lan_name}] Phase 2 AI 选择 MEME 但无可用表情包链接，回退处理")
                if ctx.get('selected_web_link'):
                    primary_channel = ctx['selected_web_link'].get('mode', 'web')
                    source_links.append(ctx['selected_web_link'])
                    logger.debug(f"[{lan_name}] Phase 2 回退到 WEB 通道 (子通道: {primary_channel})")
                elif ctx.get('vision_content'):
                    primary_channel = 'vision'
                    logger.debug(f"[{lan_name}] Phase 2 回退到 VISION 通道")
                else:
                    logger.debug(f"[{lan_name}] Phase 2 MEME 无表情包且无回退通道，将跳过链接展示")
    return primary_channel, source_links

@router.post('/proactive_chat')
async def proactive_chat(request: Request):
    """
    主动搭话：两阶段架构 — Phase 1 合并 LLM（web筛选+music/meme关键词，1次调用），Phase 2 结合人设生成搭话
    """
    try:
        _config_manager = get_config_manager()
        session_manager = get_session_manager()
        # 获取当前角色数据（包括完整人设）
        master_name_current, her_name_current, _, _, _, lanlan_prompt_map, _, _, _ = _config_manager.get_character_data()
        
        data = await request.json()
        lanlan_name = data.get('lanlan_name') or her_name_current
        is_playing_music = data.get('is_playing_music', False)
        current_track = data.get('current_track', None)
        
        # 获取session manager
        mgr = session_manager.get(lanlan_name)
        if not mgr:
            return JSONResponse({"success": False, "error": f"角色 {lanlan_name} 不存在"}, status_code=404)
        
        # 检查是否正在响应中（如果正在说话，不打断）
        if mgr.is_active and hasattr(mgr.session, '_is_responding') and mgr.session._is_responding:
            return JSONResponse({
                "success": False, 
                "error": "AI正在响应中，无法主动搭话",
                "message": "请等待当前响应完成"
            }, status_code=409)
        
        print(f"[{lanlan_name}] 开始主动搭话流程（两阶段架构）...")
        
        # ========== 解析 enabled_modes ==========
        enabled_modes = data.get('enabled_modes', [])
        # 兼容旧版前端
        if not enabled_modes:
            content_type = data.get('content_type', None)
            screenshot_data = data.get('screenshot_data')
            if screenshot_data and isinstance(screenshot_data, str):
                enabled_modes = ['vision']
            elif data.get('use_window_search', False):
                enabled_modes = ['window']
            elif content_type == 'news':
                enabled_modes = ['news']
            elif content_type == 'video':
                enabled_modes = ['video']
            elif data.get('use_personal_dynamic', False):
                enabled_modes = ['personal']
            else:
                enabled_modes = ['home']
        
        print(f"[{lanlan_name}] 启用的搭话模式: {enabled_modes}")
        
        # ========== 0. 并行获取所有信息源内容（无 LLM） ==========
        screenshot_data = data.get('screenshot_data')
        has_screenshot = bool(screenshot_data) and isinstance(screenshot_data, str)
        
        async def _fetch_source(mode: str) -> tuple:
            """
            获取单个信息源，返回 (mode, content_dict) 或抛出异常
            """
            if mode == 'vision':
                if not has_screenshot:
                    raise ValueError("无截图数据（screenshot_data 为空或类型不正确）")
                window_title = data.get('window_title', '')
                # ⚠️ Phase 1 不调用 vision_model 分析截图！
                # 截图将在 Phase 2 由 vision_model 直接读取原图，这里只做压缩。
                compressed_b64 = ''
                try:
                    from PIL import Image as _PILImage
                    b64_raw = screenshot_data.split(',', 1)[1] if ',' in screenshot_data else screenshot_data
                    img = _PILImage.open(BytesIO(base64.b64decode(b64_raw)))
                    if img.mode in ('RGBA', 'LA', 'P'):
                        img = img.convert('RGB')
                    jpg_bytes = compress_screenshot(img, target_h=COMPRESS_TARGET_HEIGHT, quality=COMPRESS_JPEG_QUALITY)
                    compressed_b64 = base64.b64encode(jpg_bytes).decode('utf-8')
                    print(f"[{lanlan_name}] Vision 通道: 截图压缩完成 {len(jpg_bytes)//1024}KB (Phase 2 将直接分析)")
                except Exception as compress_err:
                    logger.warning(f"[{lanlan_name}] 截图压缩失败（Phase 2 将无法使用截图）: {compress_err}")
                return (mode, {'window_title': window_title, 'screenshot_b64': compressed_b64})
            
            elif mode == 'news':
                news_content = await fetch_news_content(limit=_PHASE1_FETCH_PER_SOURCE)
                if not news_content['success']:
                    raise ValueError(f"获取新闻失败: {news_content.get('error')}")
                formatted = format_news_content(news_content)
                _log_news_content(lanlan_name, news_content)
                # 提取链接信息
                links = _extract_links_from_raw(mode, news_content)
                return (mode, {'formatted_content': formatted, 'raw_data': news_content, 'links': links})
            
            elif mode == 'video':
                video_content = await fetch_video_content(limit=_PHASE1_FETCH_PER_SOURCE)
                if not video_content['success']:
                    raise ValueError(f"获取视频失败: {video_content.get('error')}")
                formatted = format_video_content(video_content)
                _log_video_content(lanlan_name, video_content)
                links = _extract_links_from_raw(mode, video_content)
                return (mode, {'formatted_content': formatted, 'raw_data': video_content, 'links': links})
            
            elif mode == 'window':
                window_context_content = await fetch_window_context_content(limit=5)
                if not window_context_content['success']:
                    raise ValueError(f"获取窗口上下文失败: {window_context_content.get('error')}")
                formatted = format_window_context_content(window_context_content)
                raw_title = window_context_content.get('window_title', '')
                sanitized_title = raw_title[:30] + '...' if len(raw_title) > 30 else raw_title
                print(f"[{lanlan_name}] 成功获取窗口上下文: {sanitized_title}")
                return (mode, {'formatted_content': formatted, 'raw_data': window_context_content, 'links': []})
            
            elif mode == 'home':
                trending_content = await fetch_trending_content(
                    bilibili_limit=_PHASE1_FETCH_PER_SOURCE,
                    weibo_limit=_PHASE1_FETCH_PER_SOURCE
                )
                if not trending_content['success']:
                    raise ValueError(f"获取首页推荐失败: {trending_content.get('error')}")
                formatted = format_trending_content(trending_content)
                _log_trending_content(lanlan_name, trending_content)
                links = _extract_links_from_raw(mode, trending_content)
                return (mode, {'formatted_content': formatted, 'raw_data': trending_content, 'links': links})

            elif mode == 'personal':
                personal_dynamics = await fetch_personal_dynamics(limit=_PHASE1_FETCH_PER_SOURCE)
                if not personal_dynamics['success']:
                    raise ValueError(f"获取个人动态失败: {personal_dynamics.get('error')}")
                formatted = format_personal_dynamics(personal_dynamics)
                _log_personal_dynamics(lanlan_name, personal_dynamics)
                links = _extract_links_from_raw(mode, personal_dynamics)
                return (mode, {'formatted_content': formatted, 'raw_data': personal_dynamics, 'links': links})
            
            elif mode == 'music':
                return (mode, {'placeholder': True, 'note': '关键词将在 Phase 1 开始前生成'})
            
            elif mode == 'meme':
                # meme 关键词将由合并 LLM 调用生成，此处仅占位
                return (mode, {'placeholder': True, 'note': '关键词将由合并 Phase 1 LLM 生成'})

            else:
                raise ValueError(f"未知模式: {mode}")
        
        # 并行获取所有信息源
        fetch_tasks = [
            _fetch_source(m)
            for m in enabled_modes
        ]
        fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        
        # 收集成功的信息源
        sources: dict[str, dict] = {}
        for i, result in enumerate(fetch_results):
            if isinstance(result, Exception):
                failed_mode = enabled_modes[i]
                logger.warning(f"[{lanlan_name}] 信息源 [{failed_mode}] 获取失败: {result}")
                continue
            mode, content = result
            sources[mode] = content
        
        if not sources:
            return JSONResponse({
                "success": False,
                "error": "所有信息源获取失败",
                "action": "pass"
            }, status_code=500)
        
        print(f"[{lanlan_name}] 成功获取 {len(sources)} 个信息源: {list(sources.keys())}")

        # ========== 1. 获取记忆上下文 (New Dialog) ==========
        # new_dialog 返回格式：
        # ========以下是{name}的内心活动========
        # {内心活动/Settings}...
        # 现在时间...整理了近期发生的事情。
        # Name | Content
        # ...
        
        raw_memory_context = ""
        try:
            async with httpx.AsyncClient(proxy=None, trust_env=False) as client:
                resp = await client.get(f"http://127.0.0.1:{MEMORY_SERVER_PORT}/new_dialog/{lanlan_name}", timeout=5.0)
                resp.raise_for_status()  # Check for HTTP errors explicitly
                if resp.status_code == 200:
                    raw_memory_context = resp.text
                else:
                    logger.warning(f"[{lanlan_name}] 记忆服务返回非200状态: {resp.status_code}，使用空上下文")
        except Exception as e:
            logger.warning(f"[{lanlan_name}] 获取记忆上下文失败，使用空上下文: {e}")
        
        # 解析 new_dialog 响应
        def _parse_new_dialog(text: str) -> tuple[str, str]:
            """
            解析 new_dialog 的文本响应，尝试分离内心活动和对话历史。
             - 如果包含分割线 "整理了近期发生的事情"，则将其前部分作为内心活动，后部分作为对话历史。
             - 该函数的目的是为了在 Phase 1 后能够清晰地获取到内心活动和对话历史，以便在 Phase 2 中更好地生成搭话内容。
             - 内心活动通常包含角色的当前状态、情绪、想法等信息，而对话历史则是与用户的过去交流记录。
             - 通过这种方式，我们可以在 Phase 1 中分析内心活动来选择搭话话题，在 Phase 2 中结合对话历史生成更符合上下文的搭话内容。
            """
            if not text:
                return "", ""
            # 尝试找到分割线 "整理了近期发生的事情"
            split_keyword = "整理了近期发生的事情"
            if split_keyword in text:
                parts = text.split(split_keyword, 1)
                # part[0] 是内心活动+时间，part[1] 是对话历史
                # 提取内心活动 (去除首尾空白)
                inner_thoughts_part = parts[0].strip()
                # 提取对话历史 (去除首尾空白)
                history_part = parts[1].strip()
                return history_part, inner_thoughts_part
            return text, ""

        memory_context, inner_thoughts = _parse_new_dialog(raw_memory_context)
        
        # ========== 2. 选择语言 ==========
        try:
            request_lang = data.get('language') or data.get('lang') or data.get('i18n_language')
            if request_lang:
                proactive_lang = normalize_language_code(request_lang, format='short')
            else:
                proactive_lang = get_global_language()
        except Exception:
            proactive_lang = 'zh'
        
        # ========== 3. 注入近期搭话记录 ==========
        proactive_chat_history_prompt = _format_recent_proactive_chats(lanlan_name, proactive_lang)

        # ========== 3.5 反思 + 回调话题（融合主动搭话，几乎免费） ==========
        # 认知框架：Facts → Reflection(pending) → 主动搭话自然提及 → 用户反馈 → Persona
        followup_topics_prompt = ""
        _surfaced_reflection_ids = []  # 记录本次搭话提及了哪些 pending 反思
        _reflection_engine = None
        _persona_mgr = None
        try:
            from memory import ReflectionEngine, FactStore, PersonaManager
            _fact_store = FactStore()
            _persona_mgr = PersonaManager()
            _reflection_engine = ReflectionEngine(_fact_store, _persona_mgr)

            # 1. 自动确认 >=3天 无反对的 pending 反思 → 晋升 persona
            _auto_confirmed = _reflection_engine.auto_confirm_stale(lanlan_name)
            if _auto_confirmed:
                print(f"[{lanlan_name}] 自动确认 {_auto_confirmed} 条过期反思到 persona")

            # 2. 异步反思（>=3 条新事实才触发 LLM 调用）
            _reflection_result = await _reflection_engine.reflect(lanlan_name)
            if _reflection_result:
                print(f"[{lanlan_name}] 反思完成(pending): {_reflection_result['text'][:50]}...")

            # 3. 获取回调话题（list[dict]，带 reflection_id）
            _followup_topics = _reflection_engine.get_followup_topics(lanlan_name)
            if _followup_topics:
                followup_topics_prompt = _loc(PROACTIVE_FOLLOWUP_HEADER, proactive_lang)
                for topic in _followup_topics:
                    followup_topics_prompt += f"- {topic['text']}\n"
                    if topic.get('reflection_id'):
                        _surfaced_reflection_ids.append(topic['reflection_id'])
                print(f"[{lanlan_name}] 回调话题: {len(_followup_topics)} 条 (pending反思: {len(_surfaced_reflection_ids)})")
        except Exception as e:
            logger.debug(f"[{lanlan_name}] 反思/回调话题获取失败（不影响主流程）: {e}")

        # ========== 4. 获取 LLM 配置 ==========
        try:
            correction_config = _config_manager.get_model_api_config('correction')
            correction_model = correction_config.get('model')
            correction_base_url = correction_config.get('base_url')
            correction_api_key = correction_config.get('api_key')
            
            if not correction_model or not correction_api_key:
                logger.error("纠错模型配置缺失: model或api_key未设置")
                return JSONResponse({
                    "success": False,
                    "error": "纠错模型配置缺失",
                    "detail": "请在设置中配置纠错模型的model和api_key"
                }, status_code=500)
            
            vision_config = _config_manager.get_model_api_config('vision')
            vision_model_name = vision_config.get('model', '')
            vision_base_url = vision_config.get('base_url', '')
            vision_api_key = vision_config.get('api_key', '')
            has_vision_model = bool(vision_model_name and vision_api_key)
            if not has_vision_model:
                logger.info("Vision 模型未配置，Phase 2 将退回使用 correction 模型")
        except Exception as e:
            logger.error(f"获取模型配置失败: {e}")
            return JSONResponse({
                "success": False,
                "error": "模型配置异常",
                "detail": str(e)
            }, status_code=500)
        
        def _make_llm(temperature: float = 1.0, max_tokens: int = 1536,
                      use_vision: bool = False, disable_thinking: bool = True):
            """
            创建 LLM 实例。use_vision=True 时使用 vision 模型；disable_thinking=False 时不注入 extra_body。
            """
            if use_vision and has_vision_model:
                m, bu, ak = vision_model_name, vision_base_url, vision_api_key
            else:
                m, bu, ak = correction_model, correction_base_url, correction_api_key
            kw: dict = dict(
                temperature=temperature,
                max_completion_tokens=max_tokens,
                streaming=True,
            )
            if not disable_thinking:
                kw["extra_body"] = None  # skip auto-resolved extra_body
            return create_chat_llm(m, bu, ak, **kw)
        
        async def _llm_call_with_retry(
            system_prompt: str, label: str, *,
            temperature: float = 1.0, max_tokens: int = 1024, timeout: float = 16.0,
            use_vision: bool = False, disable_thinking: bool = True,
            image_b64: str = '',
            dynamic_context: str = '',
        ) -> str:
            """
            带重试的 LLM 调用。image_b64 非空时以多模态方式发送截图。
            dynamic_context: 动态上下文，注入到 HumanMessage 中使 SystemMessage 可被缓存。
            """
            actual_model = (vision_model_name if use_vision and has_vision_model else correction_model)
            begin_text = _loc(BEGIN_GENERATE, proactive_lang)
            human_text = f"{dynamic_context}\n\n{begin_text}" if dynamic_context else begin_text
            if image_b64:
                human_content = [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": human_text},
                ]
            else:
                human_content = human_text
            messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_content)]

            from utils.token_tracker import set_call_type
            set_call_type("proactive")
            max_retries = 3
            retry_delays = [1, 2]
            for attempt in range(max_retries):
                try:
                    # 使用 async with 确保 ChatOpenAI (AsyncOpenAI) 实例被正确关闭
                    async with _make_llm(temperature=temperature, max_tokens=max_tokens,
                                        use_vision=use_vision, disable_thinking=disable_thinking) as llm:
                        response = await asyncio.wait_for(
                            llm.ainvoke(messages),
                            timeout=timeout
                        )
                        # [临时调试]
                        print(f"\n[PROACTIVE-DEBUG] LLM output [{label}]: {response.content[:500]}...\n")
                        return response.content.strip()
                except (asyncio.TimeoutError, APIConnectionError, InternalServerError, RateLimitError) as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"[{lanlan_name}] LLM [{label}] 调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                        await asyncio.sleep(retry_delays[attempt])
                    else:
                        logger.error(f"[{lanlan_name}] LLM [{label}] 调用失败，已达最大重试: {e}")
                        raise
            raise RuntimeError("Unexpected")
        
        # ================================================================
        # Phase 1: 合并 LLM 调用（web 筛选 + music 关键词 + meme 关键词）
        # ⚠️ 一阶段一定不要分析屏幕！截图会在二阶段由 vision_model 直接 feed in。
        # - 所有文本源合并 → 1 次 LLM 同时完成 web 筛选、music/meme 关键词生成
        # - 来源动态权重系统在 LLM 调用前剔除低权重通道
        # 总计最多 1 次 LLM 调用
        # ================================================================
        
        vision_content = sources.get('vision')  # 仅保留给 Phase 2 使用，Phase 1 不处理
        music_content = sources.get('music')
        meme_content = sources.get('meme')
        logger.info(f"[{lanlan_name}] 主动搭话-音乐内容: type={type(music_content)}, success={music_content.get('success') if music_content else 'N/A'}")
        logger.info(f"[{lanlan_name}] 主动搭话-表情包内容: type={type(meme_content)}, success={meme_content.get('success') if meme_content else 'N/A'}")
        
        all_web_links: list[dict] = []
        
        # 收集音乐链接（在 Phase 1 Web 筛选完成后）
        # meme 也不经过 Phase 1 LLM 筛选，直接添加话题
        web_modes = [m for m in sources if m not in ('vision', 'music', 'meme')]
        
        merged_web_content = ""
        if web_modes:
            parts = []
            seen_topic_keys: set[str] = set()
            remaining_total = _PHASE1_TOTAL_TOPIC_TARGET
            for m in web_modes:
                if remaining_total <= 0:
                    break
                src = sources[m]
                label_map = PROACTIVE_SOURCE_LABELS.get(proactive_lang, PROACTIVE_SOURCE_LABELS['zh'])
                label = label_map.get(m, m)
                links = src.get('links', []) or []

                selected_links: list[dict] = []
                for link in links:
                    title = link.get('title', '')
                    source = link.get('source', '')
                    url = link.get('url', '')
                    key = _build_topic_dedup_key(topic_title=title, topic_source=source, topic_url=url)
                    if key:
                        if key in seen_topic_keys or _is_recent_topic_used(lanlan_name, key):
                            continue
                        seen_topic_keys.add(key)
                    # 给 link 打上来源 mode 标记，用于细粒度 channel 记录
                    if 'mode' not in link:
                        link['mode'] = m
                    selected_links.append(link)
                    if len(selected_links) >= remaining_total:
                        break

                if selected_links:
                    all_web_links.extend(selected_links)
                    remaining_total -= len(selected_links)
                    lines = []
                    for idx, item in enumerate(selected_links, start=1):
                        title = item.get('title', '').strip()
                        if not title:
                            continue
                        source = item.get('source', '').strip()
                        url = item.get('url', '').strip()
                        suffix = []
                        if source:
                            suffix.append(f"来源: {source}")
                        if url:
                            suffix.append(f"URL: {url}")
                        ext = (" | " + " | ".join(suffix)) if suffix else ""
                        lines.append(f"{idx}. {title}{ext}")
                    if lines:
                        parts.append(f"--- {label} ---\n" + "\n".join(lines))
                        continue

                content_text = src.get('formatted_content', '')
                if content_text:
                    compact_lines = [ln.strip() for ln in content_text.splitlines() if ln.strip()]
                    if compact_lines:
                        fallback_lines = compact_lines[:remaining_total]
                        if fallback_lines:
                            parts.append(f"--- {label} ---\n" + "\n".join(fallback_lines))
                            remaining_total -= len(fallback_lines)
            merged_web_content = "\n\n".join(parts)
        
        # Phase 1 结果收集
        phase1_topics: list[tuple[str, str]] = []  # [(channel, topic_summary), ...]
        source_links: list[dict] = []  # [{"title": ..., "url": ..., "source": ...}]
        selected_web_link = None
        selected_web_topic_key = None
        selected_music_link = None
        selected_music_topic_key = None
        selected_meme_link = None
        selected_meme_topic_key = None

        # 【加固】如果正在放歌，强制在此环节清空 music_content，彻底跳过 Phase 1 的所有搜歌逻辑
        if is_playing_music:
            if music_content:
                logger.info(f"[{lanlan_name}]-音乐正在播放，强制屏蔽 Phase 1 搜歌逻辑")
            music_content = None

        # ============================================================
        # 来源动态权重过滤（vision 不参与权重计算）
        # ============================================================
        non_vision_modes = [m for m in enabled_modes if m != 'vision']
        if non_vision_modes:
            source_weights = _compute_source_weights(lanlan_name, non_vision_modes)
            suppressed = _filter_sources_by_weight(source_weights)
            weight_str = ' '.join(f"{ch}={w:.3f}" for ch, w in source_weights.items())
            logger.info(f"[{lanlan_name}] 来源权重: {weight_str} | 剔除: {suppressed or '无'}")

            for ch in suppressed:
                sources.pop(ch, None)
            if 'music' in suppressed:
                music_content = None
            if 'meme' in suppressed:
                meme_content = None

            # 被剔除的 web 子通道不参与 merged_web_content（sources 已弹出，
            # 但 merged_web_content 已经构建完毕，需要重新构建）
            if suppressed & set(web_modes):
                # 重新构建 merged_web_content，排除被剔除的通道
                remaining_web_modes = [m for m in web_modes if m not in suppressed]
                if remaining_web_modes:
                    # 先从 all_web_links 中移除被剔除通道的链接
                    all_web_links = [lk for lk in all_web_links if lk.get('mode') not in suppressed]
                    parts = []
                    seen_topic_keys_2: set[str] = set()
                    remaining_total_2 = _PHASE1_TOTAL_TOPIC_TARGET
                    for m in remaining_web_modes:
                        if remaining_total_2 <= 0:
                            break
                        src = sources.get(m)
                        if not src:
                            continue
                        label_map = PROACTIVE_SOURCE_LABELS.get(proactive_lang, PROACTIVE_SOURCE_LABELS['zh'])
                        label = label_map.get(m, m)
                        links = src.get('links', []) or []
                        selected_links_2: list[dict] = []
                        for link in links:
                            title = link.get('title', '')
                            source_name = link.get('source', '')
                            url = link.get('url', '')
                            key = _build_topic_dedup_key(topic_title=title, topic_source=source_name, topic_url=url)
                            if key:
                                if key in seen_topic_keys_2 or _is_recent_topic_used(lanlan_name, key):
                                    continue
                                seen_topic_keys_2.add(key)
                            if 'mode' not in link:
                                link['mode'] = m
                            selected_links_2.append(link)
                            if len(selected_links_2) >= remaining_total_2:
                                break
                        if selected_links_2:
                            remaining_total_2 -= len(selected_links_2)
                            lines = []
                            for idx, item in enumerate(selected_links_2, start=1):
                                t = item.get('title', '').strip()
                                if not t:
                                    continue
                                s = item.get('source', '').strip()
                                u = item.get('url', '').strip()
                                suffix = []
                                if s:
                                    suffix.append(f"来源: {s}")
                                if u:
                                    suffix.append(f"URL: {u}")
                                ext = (" | " + " | ".join(suffix)) if suffix else ""
                                lines.append(f"{idx}. {t}{ext}")
                            if lines:
                                parts.append(f"--- {label} ---\n" + "\n".join(lines))
                    merged_web_content = "\n\n".join(parts)
                else:
                    merged_web_content = ""
                    all_web_links = []

        # ============================================================
        # 合并 Phase 1 LLM 调用：web 筛选 + music 关键词 + meme 关键词
        # 一次 LLM 调用完成所有任务，降低 RPM
        # ============================================================
        has_music_task = bool(music_content and music_content.get('placeholder'))
        has_meme_task = bool(meme_content and meme_content.get('placeholder'))
        has_web_task = bool(merged_web_content)

        # 只要有至少一个任务就发起 LLM 调用
        unified_parsed: dict = {'web': None, 'music_keyword': None, 'meme_keyword': None}
        if has_web_task or has_music_task or has_meme_task:
            try:
                from config.prompts_proactive import build_unified_phase1_prompt
                # 将回调话题追加到 memory_context 中供 Phase 1 参考
                enriched_memory_context = memory_context
                if followup_topics_prompt:
                    enriched_memory_context = memory_context + "\n" + followup_topics_prompt
                unified_prompt = build_unified_phase1_prompt(
                    proactive_lang,
                    merged_content=merged_web_content if has_web_task else None,
                    memory_context=enriched_memory_context,
                    recent_chats_section=proactive_chat_history_prompt,
                    music_ctx={'lanlan_name': lanlan_name, 'master_name': master_name_current} if has_music_task else None,
                    meme_enabled=has_meme_task,
                    lanlan_name=lanlan_name,
                    master_name=master_name_current,
                )
                unified_result_text = await _llm_call_with_retry(unified_prompt, "unified_phase1")
                print(f"[{lanlan_name}] Phase 1 合并 LLM 结果: {unified_result_text[:500]}")
                unified_parsed = _parse_unified_phase1_result(unified_result_text)
                logger.info(f"[{lanlan_name}] Phase 1 解析: web={'有' if unified_parsed.get('web') else '无'}, "
                           f"music_kw={unified_parsed.get('music_keyword', 'N/A')}, "
                           f"meme_kw={unified_parsed.get('meme_keyword', 'N/A')}")
            except Exception as e:
                logger.warning(f"[{lanlan_name}] Phase 1 合并 LLM 调用异常: {type(e).__name__}: {e}，降级处理")
                # LLM 失败：各通道降级
                unified_parsed = {'web': None, 'music_keyword': None, 'meme_keyword': None}

        # ============================================================
        # 解析 web 结果 → 链接匹配 → 去重
        # ============================================================
        web_parsed = unified_parsed.get('web')
        if web_parsed and web_parsed.get('title'):
            matched = _lookup_link_by_title(web_parsed.get('title', ''), all_web_links)
            topic_key = _build_topic_dedup_key(
                topic_title=web_parsed.get('title', ''),
                topic_source=web_parsed.get('source', ''),
                topic_url=(matched.get('url', '') if matched else ''),
            )
            if topic_key and _is_recent_topic_used(lanlan_name, topic_key):
                print(f"[{lanlan_name}] Phase 1 话题去重命中，跳过: {web_parsed.get('title','')[:60]}")
            else:
                if matched:
                    selected_web_link = {
                        'title': web_parsed.get('title', matched.get('title', '')),
                        'url': matched['url'],
                        'source': web_parsed.get('source', matched.get('source', '')),
                        'mode': matched.get('mode', 'web'),  # 保留细粒度 mode
                    }
                    selected_web_topic_key = topic_key
                    print(f"[{lanlan_name}] Phase 1 链接预匹配成功: {matched.get('title','')[:60]}")
                else:
                    print(f"[{lanlan_name}] Phase 1 未在 web_links 中匹配到标题: {web_parsed.get('title','')[:60]}")
                # 用 web_parsed 的 summary 或原始文本作为 topic
                web_topic_text = web_parsed.get('summary', web_parsed.get('title', ''))
                phase1_topics.append(('web', web_topic_text.strip()))

        # ============================================================
        # 并行后置 fetch：music + meme（使用 LLM 生成的关键词）
        # ============================================================
        music_keyword = unified_parsed.get('music_keyword')
        meme_keyword = unified_parsed.get('meme_keyword')

        async def _fetch_music_with_fallback(kw: str):
            """用 LLM 关键词搜索音乐，失败则随机推荐"""
            try:
                raw = await fetch_music_content(keyword=kw, limit=5)
                if raw and raw.get('success'):
                    return raw
            except Exception as e:
                logger.warning(f"[{lanlan_name}] 音乐关键词 '{kw}' 搜索异常: {e}")
            logger.warning(f"[{lanlan_name}] 音乐关键词 '{kw}' 搜索失败，尝试随机推荐")
            try:
                return await fetch_music_content(keyword="", limit=5)
            except Exception:
                return None

        async def _fetch_meme_with_fallback(kw: str):
            """用 LLM 关键词搜索表情包，失败则随机热词"""
            try:
                raw = await asyncio.wait_for(
                    fetch_meme_content(keyword=kw, limit=_PHASE1_FETCH_PER_SOURCE),
                    timeout=12.0
                )
                if raw and raw.get('success'):
                    return raw
            except Exception as e:
                logger.warning(f"[{lanlan_name}] 表情包关键词 '{kw}' 搜索异常: {e}")
            logger.warning(f"[{lanlan_name}] 表情包关键词 '{kw}' 搜索失败，尝试随机热词")
            try:
                return await asyncio.wait_for(
                    fetch_meme_content(keyword="", limit=_PHASE1_FETCH_PER_SOURCE),
                    timeout=12.0
                )
            except Exception:
                return None

        fetch_tasks_p1: list = []
        fetch_labels: list[str] = []

        if has_music_task:
            kw = music_keyword or ""
            fetch_tasks_p1.append(_fetch_music_with_fallback(kw))
            fetch_labels.append('music')
        if has_meme_task:
            kw = meme_keyword or ""
            fetch_tasks_p1.append(_fetch_meme_with_fallback(kw))
            fetch_labels.append('meme')

        if fetch_tasks_p1:
            fetch_results_p1 = await asyncio.gather(*fetch_tasks_p1, return_exceptions=True)
            for label_p1, result_p1 in zip(fetch_labels, fetch_results_p1):
                if isinstance(result_p1, Exception):
                    logger.warning(f"[{lanlan_name}] Phase 1 后置 fetch [{label_p1}] 异常: {result_p1}")
                    continue
                if label_p1 == 'music' and result_p1 and result_p1.get('success'):
                    _log_music_content(lanlan_name, result_p1)
                    music_content = {
                        'formatted_content': _format_music_content(result_p1, proactive_lang),
                        'raw_data': result_p1,
                    }
                elif label_p1 == 'meme' and result_p1 and result_p1.get('success'):
                    meme_content = {
                        'success': True,
                        'data': result_p1.get('data', []),
                        'raw_data': result_p1,
                        'source': result_p1.get('source', '表情包'),
                    }
                    print(f"[{lanlan_name}] 成功获取 {len(result_p1.get('data', []))} 个表情包 (来源: {result_p1.get('source', '?')})")

        # ============================================================
        # 音乐话题组装（去重 + 暂存链接）
        # ============================================================
        if music_content and music_content.get('formatted_content'):
            music_topic = music_content['formatted_content']
            if music_topic:
                music_tracks = music_content.get('raw_data', {}).get('data', [])
                if music_tracks:
                    first_track = music_tracks[0]
                    track_name = first_track.get('name', '')
                    track_artist = first_track.get('artist', '')
                    track_url = first_track.get('url', '')
                    track_cover = first_track.get('cover', '')
                    music_topic_key = _build_topic_dedup_key(
                        topic_title=f"{track_name} - {track_artist}",
                        topic_source='music',
                        topic_url=track_url
                    )
                    if _is_recent_topic_used(lanlan_name, music_topic_key):
                        print(f"[{lanlan_name}]- Phase 1 音乐话题去重命中，跳过: {track_name}")
                    else:
                        logger.info(f"[{lanlan_name}]- Phase 1 音乐话题已添加: {music_topic[:100]}...")
                        selected_music_link = {
                            'title': track_name,
                            'artist': track_artist,
                            'url': track_url,
                            'cover': track_cover,
                            'source': '音乐推荐',
                            'type': 'music'
                        }
                        selected_music_topic_key = music_topic_key
                        phase1_topics.append(('music', music_topic))
                else:
                    logger.info(f"[{lanlan_name}] Phase 1 音乐话题已添加: {music_topic[:100]}...")
                    phase1_topics.append(('music', music_topic))

        # ============================================================
        # 表情包话题组装（遍历候选 → 去重 → 限1张）
        # ============================================================
        if meme_content and meme_content.get('success') and meme_content.get('data'):
            meme_data = meme_content.get('data', [])
            if meme_data:
                for candidate_meme in meme_data:
                    meme_title = candidate_meme.get('title', '')
                    meme_url = candidate_meme.get('url', '')
                    if not meme_url:
                        continue  # 跳过无 URL 的候选
                    meme_source = candidate_meme.get('source', '表情包')
                    meme_topic_key = _build_topic_dedup_key(
                        topic_title=meme_title,
                        topic_source=meme_source,
                        topic_url=meme_url
                    )
                    if meme_topic_key and _is_recent_topic_used(lanlan_name, meme_topic_key):
                        logger.info(f"[{lanlan_name}]- Phase 1 表情包话题去重命中，跳过: {meme_title[:30]}")
                        continue
                    single_meme_topic = f"发现一个很有意思的[表情包]：'{meme_title}' (来自 {meme_source})"
                    logger.info(f"[{lanlan_name}]- Phase 1 表情包话题已添加 (限额1张): {single_meme_topic}")
                    phase1_topics.append(('meme', single_meme_topic))
                    selected_meme_link = {
                        'title': meme_title,
                        'url': meme_url,
                        'source': meme_source,
                        'type': candidate_meme.get('type', 'meme')
                    }
                    selected_meme_topic_key = meme_topic_key
                    logger.info(f"[{lanlan_name}] 预选表情包话题: {meme_title[:30]}")
                    break
                else:
                    logger.info(f"[{lanlan_name}]- Phase 1 所有表情包候选均被去重，跳过表情包话题")
            else:
                logger.warning(f"[{lanlan_name}] Phase 1 表情包数据为空，跳过表情包话题")
        
        if not phase1_topics and not vision_content:
            print(f"[{lanlan_name}] Phase 1 所有通道均无可用话题")
            return JSONResponse({
                "success": True,
                "action": "pass",
                "message": "所有信息源筛选后均不值得搭话"
            })
        
        # 收集各通道结果
        active_channels = [ch for ch, _ in phase1_topics]
        print(f"[{lanlan_name}] Phase 1 结果: phase1_topics={phase1_topics}, vision_content={'有' if vision_content else '无'}")
        web_topic = None
        music_topic = None
        for channel, topic in phase1_topics:
            if channel == 'web':
                web_topic = topic
            elif channel == 'music':
                music_topic = topic
        if vision_content:
            active_channels.append('vision')
        primary_channel = 'vision' if vision_content else (active_channels[0] if active_channels else 'unknown')
        print(f"[{lanlan_name}] Phase 1 可用通道: {active_channels}，主通道: {primary_channel}")
        
        # ================================================================
        # Phase 2: 结合人设 + 双通道信息 → 流式生成搭话
        # ⚠️ 二阶段一定要用 vision_model，在调用前使用最新截图。
        #    只有这样才能减少 vision_model 读屏幕的延迟。
        # ⚠️ 二阶段一定不要打开思考 (disable_thinking 必须为 True)，
        #    否则 vision_model + thinking 一定会超时。
        # ⚠️ 不重试、不改写。流式拦截到异常直接 abort，失败即 pass 等下一次。
        # 流程：tokens → TTS 即时生成 → 全文完成后一次性投递文本 → abort 时中断两端
        # ================================================================
        
        # 获取角色完整人设，替换模板变量
        character_prompt = lanlan_prompt_map.get(lanlan_name, '')
        if not character_prompt:
            logger.warning(f"[{lanlan_name}] 未找到角色人设，使用空字符串")
        character_prompt = character_prompt.replace('{LANLAN_NAME}', lanlan_name).replace('{MASTER_NAME}', master_name_current)
        
        # --- 向前端请求最新截图，替换 Phase 1 时拿到的旧截图 ---
        screenshot_b64_for_phase2 = ''
        if vision_content and has_vision_model:
            fresh_b64 = await mgr.request_fresh_screenshot(timeout=3.0)
            if fresh_b64:
                screenshot_b64_for_phase2 = fresh_b64
                print(f"[{lanlan_name}] Phase 2 获取到最新截图 ({len(fresh_b64)//1024}KB)")
            else:
                screenshot_b64_for_phase2 = vision_content.get('screenshot_b64', '')
                if screenshot_b64_for_phase2:
                    print(f"[{lanlan_name}] Phase 2 刷新截图失败，退回使用 Phase 1 旧截图")
        
        # 构建屏幕内容段（vision 通道）
        screen_section = ""
        if screenshot_b64_for_phase2:
            sl = _loc(SCREEN_SECTION_HEADER, proactive_lang)
            sf = _loc(SCREEN_SECTION_FOOTER, proactive_lang)
            vision_window = vision_content.get('window_title', '') if vision_content else ''
            window_line = _loc(SCREEN_WINDOW_TITLE, proactive_lang).format(window=vision_window) if vision_window else ""
            hint = _loc(SCREEN_IMG_HINT, proactive_lang)
            screen_section = f"{sl}\n{window_line}{hint}\n{sf}"
            print(f"[{lanlan_name}] Phase 2 将使用 vision 模型直接看截图")
        else:
            print(f"[{lanlan_name}] Phase 2 无截图或无 vision 模型，跳过屏幕分析")
        
        # 构建外部话题段（web 通道）
        external_section = ""
        if web_topic:
            el = _loc(EXTERNAL_TOPIC_HEADER, proactive_lang)
            ef = _loc(EXTERNAL_TOPIC_FOOTER, proactive_lang)
            external_section = f"{el}\n{web_topic}\n{ef}"
        
        music_section = ""
        # 如果正在放歌，强行屏蔽音乐素材推荐，避免 AI 误触
        if music_topic and not is_playing_music:
            # 【优化】使用独立的标识符，防止模型将音乐素材误认为普通的外部 WEB 话题
            music_section = f"======音乐推荐素材======\n{music_topic}\n======音乐素材结束======"
        elif is_playing_music:
            print(f"[{lanlan_name}] 正在播放音乐，已屏蔽音乐推荐素材（仅保留 playing_hint）")
            music_section = ""
        
        # 构建表情包段（meme 通道）
        meme_section = ""
        meme_topic = None
        for channel, topic in phase1_topics:
            if channel == 'meme':
                meme_topic = topic
                break
        if meme_topic:
            meme_section = f"======表情包素材======\n{meme_topic}\n======表情包素材结束======"
        
        source_instruction, output_format_section = get_proactive_format_sections(
            has_screen=bool(screen_section),
            has_web=bool(external_section),
            has_music=bool(music_section),
            has_meme=bool(meme_section),
            lang=proactive_lang,
        )
        music_playing_hint = ""
        if is_playing_music and current_track:
            track_name = current_track.get('name') or get_proactive_music_unknown_track_name(proactive_lang)
            music_playing_hint = get_proactive_music_playing_hint(track_name, proactive_lang)

        # 静动分离：generate_prompt 作为静态 SystemMessage（可被缓存），
        # 追加的音乐/表情包指令作为动态上下文注入 HumanMessage
        generate_prompt = get_proactive_generate_prompt(
            proactive_lang, music_playing_hint,
            has_music=bool(music_section), has_meme=bool(meme_section),
        ).format(
            character_prompt=character_prompt,
            inner_thoughts=inner_thoughts,
            memory_context=memory_context,
            recent_chats_section=proactive_chat_history_prompt,
            screen_section=screen_section,
            external_section=external_section,
            music_section=music_section,
            meme_section=meme_section,
            master_name=master_name_current,
            source_instruction=source_instruction,
            output_format_section=output_format_section,
        )
        dynamic_context_for_phase2 = ""
        if music_topic:
            dynamic_context_for_phase2 += PROACTIVE_MUSIC_TAG_INSTRUCTIONS.get(
                proactive_lang,
                PROACTIVE_MUSIC_TAG_INSTRUCTIONS.get('en', PROACTIVE_MUSIC_TAG_INSTRUCTIONS['zh']),
            )
            raw_data = music_content.get('raw_data', {}) if music_content else {}
            if raw_data.get('best_match', {}).get('status') == 'fuzzy':
                dynamic_context_for_phase2 += get_proactive_music_failsafe_hint(proactive_lang)

        if is_playing_music:
            dynamic_context_for_phase2 += get_proactive_music_strict_constraint(proactive_lang)
        print(f"[{lanlan_name}] Phase 2 prompt 长度: {len(generate_prompt)}, 动态上下文: {len(dynamic_context_for_phase2)} 字符")

        # --- 前置检查：用户是否空闲、WebSocket 是否在线、session 是否可用 ---
        if not await mgr.prepare_proactive_delivery(min_idle_secs=30.0):
            return JSONResponse({
                "success": True,
                "action": "pass",
                "message": "主动搭话条件未满足（用户近期活跃或语音会话正在进行）"
            })

        # --- 构建 LLM + messages (static/dynamic 分离) ---
        phase2_use_vision = bool(screenshot_b64_for_phase2 and has_vision_model)

        begin_text = _loc(BEGIN_GENERATE, proactive_lang)
        human_text = f"{dynamic_context_for_phase2}\n\n{begin_text}" if dynamic_context_for_phase2 else begin_text
        if phase2_use_vision:
            human_content = [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64_for_phase2}"}},
                {"type": "text", "text": human_text},
            ]
        else:
            human_content = human_text
        messages = [SystemMessage(content=generate_prompt), HumanMessage(content=human_content)]

        actual_model = (vision_model_name if phase2_use_vision else correction_model)
        print(f"\n{'='*60}\n[PROACTIVE-DEBUG] Phase 2 STREAM: model={actual_model} | vision={phase2_use_vision} | img={'yes' if phase2_use_vision else 'no'}\n{'='*60}\n{generate_prompt}\n{'='*60}\n")

        # --- 流式调用 + 在线拦截 ---
        from utils.token_tracker import set_call_type
        set_call_type("proactive")
        buffer = ""
        tag_parsed = False
        source_tag = ""
        full_text = ""
        pipe_count = 0
        aborted = False
        
        try:
            async with asyncio.timeout(25.0):
                # 使用 async with 确保 ChatOpenAI 正确关闭
                async with _make_llm(temperature=1.0, max_tokens=1536,
                                    use_vision=phase2_use_vision, disable_thinking=True) as llm:
                    async for chunk in llm.astream(messages):
                        content = chunk.content if hasattr(chunk, 'content') else ''
                        if not content:
                            continue
                        
                        if not tag_parsed:
                            buffer += content
                            # 缓冲前 ~80 字符，解析 "主动搭话" 前缀和来源标签
                            if len(buffer) < 80 and '\n' not in buffer[min(len(buffer)-1, 10):]:
                                continue
                            # 清理 "主动搭话" 前缀
                            cleaned = buffer
                            m = re.search(r'主动搭话\s*\n', cleaned)
                            if m:
                                cleaned = cleaned[m.end():]
                            # 解析 [PASS] / [CHAT] / [WEB] / [MUSIC] / [MEME]
                            tag_match = re.match(r'^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*', cleaned, re.IGNORECASE)
                            if tag_match:
                                source_tag = tag_match.group(1).upper()
                                cleaned = cleaned[tag_match.end():]
                            tag_parsed = True
                            
                            if source_tag == 'PASS' or '[PASS]' in cleaned.upper():
                                print(f"[{lanlan_name}] Phase 2 流式检测到 [PASS]，abort")
                                aborted = True
                                break
                            
                            # 缓冲中剩余的文本作为首批内容
                            if cleaned.strip():
                                full_text += cleaned
                                await mgr.feed_tts_chunk(cleaned)
                            continue
                        
                        # --- 在线拦截: fence ---
                        fence_hit = False
                        for ch in content:
                            if ch in ('|', '｜'):
                                pipe_count += 1
                                if pipe_count >= 2:
                                    fence_hit = True
                                    break
                        if fence_hit:
                            print(f"[{lanlan_name}] Phase 2 流式 fence 触发 (pipe_count={pipe_count})，abort")
                            aborted = True
                            break
                        
                        # --- 在线拦截: 长度 ---
                        if len(full_text) + len(content) > 400:
                            print(f"[{lanlan_name}] Phase 2 流式长度超限 ({len(full_text)+len(content)} > 400)，abort")
                            aborted = True
                            break
                        
                        full_text += content
                        await mgr.feed_tts_chunk(content)
        
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[{lanlan_name}] Phase 2 流式调用异常: {type(e).__name__}: {e}")
            aborted = True
        
        # --- 流结束后 buffer 未 flush 的兜底处理 ---
        if not tag_parsed and buffer and not aborted:
            cleaned = buffer
            m = re.search(r'主动搭话\s*\n', cleaned)
            if m:
                cleaned = cleaned[m.end():]
            tag_match = re.match(r'^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*', cleaned, re.IGNORECASE)
            if tag_match:
                source_tag = tag_match.group(1).upper()
                cleaned = cleaned[tag_match.end():]
            if source_tag == 'PASS' or '[PASS]' in cleaned.upper():
                aborted = True
            elif cleaned.strip():
                full_text += cleaned
                await mgr.feed_tts_chunk(cleaned)
        
        # --- 结果处理 ---
        print(f"\n[PROACTIVE-DEBUG] Phase 2 STREAM output (aborted={aborted}, tag={source_tag}): {(buffer + full_text)[:300]}\n")
        if aborted or not full_text.strip():
            await mgr.handle_new_message()
            logger.info(f"[{lanlan_name}] Phase 2 abort，已中断 TTS + 前端音频")
            return JSONResponse({
                "success": True,
                "action": "pass",
                "message": "Phase 2 流式输出被拦截或为空"
            })
        
        response_text = full_text.strip()
        logger.info(f"[{lanlan_name}] Phase 2 流式完成 (vision={phase2_use_vision}): {response_text[:120]}...")
        print(f"\n[PROACTIVE-DEBUG] Phase 2 STREAM output: {response_text[:200]}...\n")

        has_music_topic = 'music' in active_channels

        # 【加固】数据级锁：如果正在同步播放音乐，哪怕 AI 产生了音乐标签，也强制降级/忽略
        is_music_used = has_music_topic and source_tag == 'MUSIC'
        ai_wants_music = source_tag == 'MUSIC'

        if is_playing_music and ai_wants_music:
            print(f"[{lanlan_name}] 数据级锁触发：AI 在播放中尝试推荐新歌，已强制拦截并清空曲目列表")
            is_music_used = False
            music_content = None
            source_tag = 'PASS'
            aborted = True
        
        # 【加固补齐】如果触发了降级拦截（aborted），立即返回
        if aborted:
            await mgr.handle_new_message()
            return JSONResponse({
                "success": True,
                "action": "pass",
                "message": f"[{lanlan_name}] 播放中推荐拦截触发，动作已取消"
            })

        # 使用纯函数构建响应
        primary_channel, source_links = build_proactive_response(source_tag, {
            'lanlan_name': lanlan_name,
            'is_music_used': is_music_used,
            'selected_web_link': selected_web_link,
            'selected_music_link': selected_music_link,
            'selected_meme_link': selected_meme_link,
            'vision_content': vision_content
        })

        # 兜底：当最终主通道已经落到 music，或当前实际上只剩音乐通道时，
        # 【逻辑加固】如果 active_channels 里包含 meme 且 primary_channel 是 meme，不触发 fallback
        should_try_music_fallback = not is_playing_music and (
            primary_channel == 'music'
            or (has_music_topic and not any(ch in ('vision', 'web', 'meme') for ch in active_channels))
        )
        if should_try_music_fallback:
            if source_links is None:
                source_links = []
            if _append_music_recommendations(source_links, music_content) > 0:
                is_music_used = True

        if is_music_used:
            # 此处不再二次调用，因为 should_try_music_fallback 已经处理了 append
            # 或者如果 is_music_used 为 True 但 haven't appended yet, do it.
            # 实际上 supports_music_fallback 已经 append 了。
            # 为了稳妥，我们只在尚未 append 时调用。
            music_already_appended = any(link.get('source') == '音乐推荐' for link in source_links)
            if not music_already_appended:
                _append_music_recommendations(source_links, music_content)
        
        # 一次性投递完整文本 + 记录历史 + TTS end + turn end
        await mgr.finish_proactive_delivery(response_text)

        # 记录主动搭话
        _record_proactive_chat(lanlan_name, response_text, primary_channel)

        # 后台长期记忆维护（同步，轻量操作）
        try:
            # 事实衰减 GC
            if _fact_store is not None:
                archived = _fact_store.run_decay(lanlan_name)
                if archived:
                    print(f"[{lanlan_name}] 事实衰减: 归档 {archived} 条")

            # 保存本次搭话提及的 pending 反思 ID（供下次 /process 做反馈检查）
            if _reflection_engine is not None and _surfaced_reflection_ids:
                _reflection_engine.record_surfaced(lanlan_name, _surfaced_reflection_ids)
                print(f"[{lanlan_name}] 记录 surfaced 反思: {len(_surfaced_reflection_ids)} 条")

            # 记录 persona 提及次数（疲劳跟踪）
            if _persona_mgr is not None:
                _persona_mgr.record_mentions(lanlan_name, response_text)
        except Exception as e:
            logger.debug(f"[{lanlan_name}] 长期记忆后处理失败（不影响主流程）: {e}")

        # 【逻辑优化】精准的话题去重记录：仅当链接真正被加入 source_links 时才记录已使用
        def _is_link_selected(selected_link):
            if not selected_link:
                return False

            target_url = (selected_link.get('url') or '').strip()
            if target_url:
                # 存在有效 URL 时，按 URL 对比
                return any((link.get('url') or '').strip() == target_url for link in source_links if link)

            # URL 为空（如音乐降级记录），按元数据签名对比
            target_sig = (
                (selected_link.get('title') or '').strip(),
                (selected_link.get('artist') or '').strip(),
                (selected_link.get('source') or '').strip(),
            )
            return any(
                (
                    (link.get('title') or '').strip(),
                    (link.get('artist') or '').strip(),
                    (link.get('source') or '').strip(),
                ) == target_sig
                for link in source_links if link
            )

        if selected_web_topic_key and _is_link_selected(selected_web_link):
            _record_topic_usage(lanlan_name, selected_web_topic_key)
            print(f"[{lanlan_name}] 已记录 Web 话题去重: {selected_web_topic_key[:60]}")
            
        if selected_music_topic_key and (is_music_used or _is_link_selected(selected_music_link)):
            _record_topic_usage(lanlan_name, selected_music_topic_key)
            print(f"[{lanlan_name}] 已记录音乐话题去重: {selected_music_topic_key}")
            
        if selected_meme_topic_key and _is_link_selected(selected_meme_link):
            _record_topic_usage(lanlan_name, selected_meme_topic_key)
            print(f"[{lanlan_name}] 已记录表情包话题去重: {selected_meme_topic_key[:60]}")

        return JSONResponse({
            "success": True,
            "action": "chat",
            "message": "主动搭话已发送",
            "lanlan_name": lanlan_name,
            "source_mode": primary_channel.lower(),
            "source_tag": source_tag or "unknown",
            "active_channels": active_channels,
            "source_links": source_links,
            "turn_id": mgr.current_speech_id
        })
        
    except asyncio.TimeoutError:
        logger.error("主动搭话超时")
        return JSONResponse({
            "success": False,
            "error": "AI处理超时"
        }, status_code=504)
    except Exception as e:
        logger.error(f"主动搭话接口异常: {e}")
        return JSONResponse({
            "success": False,
            "error": "服务器内部错误",
            "detail": str(e)
        }, status_code=500)





@router.post('/translate')
async def translate_text_api(request: Request):
    """
    翻译文本API（供前端字幕模块使用）
    
    请求格式:
    {
        "text": "要翻译的文本",
        "target_lang": "目标语言代码 ('zh', 'en', 'ja', 'ko')",
        "source_lang": "源语言代码 (可选，为null时自动检测)"
    }
    
    响应格式:
    {
        "success": true/false,
        "translated_text": "翻译后的文本",
        "source_lang": "检测到的源语言代码",
        "target_lang": "目标语言代码"
    }
    """
    try:
        data = await request.json()
        text = data.get('text', '').strip()
        target_lang = data.get('target_lang', 'zh')
        source_lang = data.get('source_lang')
        
        if not text:
            return {
                "success": False,
                "error": "文本不能为空",
                "translated_text": "",
                "source_lang": "unknown",
                "target_lang": target_lang
            }
        
        # 归一化目标语言代码（复用公共函数）
        target_lang_normalized = normalize_language_code(target_lang, format='short')
        
        # 检测源语言（如果未提供）
        if source_lang is None:
            detected_source_lang = detect_language(text)
        else:
            # 归一化源语言代码（复用公共函数）
            detected_source_lang = normalize_language_code(source_lang, format='short')
        
        # 如果源语言和目标语言相同，不需要翻译
        if detected_source_lang == target_lang_normalized or detected_source_lang == 'unknown':
            return {
                "success": True,
                "translated_text": text,
                "source_lang": detected_source_lang,
                "target_lang": target_lang_normalized
            }
        
        # 检查是否跳过 Google 翻译（前端传递的会话级失败标记）
        skip_google = data.get('skip_google', False)
        
        # 调用翻译服务
        try:
            translated, google_failed = await translate_text(
                text, 
                target_lang_normalized, 
                detected_source_lang,
                skip_google=skip_google
            )
            return {
                "success": True,
                "translated_text": translated,
                "source_lang": detected_source_lang,
                "target_lang": target_lang_normalized,
                "google_failed": google_failed  # 告诉前端 Google 翻译是否失败
            }
        except Exception as e:
            logger.error(f"翻译失败: {e}")
            # 翻译失败时返回原文
            return {
                "success": False,
                "error": str(e),
                "translated_text": text,
                "source_lang": detected_source_lang,
                "target_lang": target_lang_normalized
            }
            
    except Exception as e:
        logger.error(f"翻译API处理失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "translated_text": "",
            "source_lang": "unknown",
            "target_lang": "zh"
        }

# ========== 个性化内容接口 ==========

@router.post('/personal_dynamics')
async def get_personal_dynamics(request: Request):
    """
    获取个性化内容数据
    """
    from utils.web_scraper import fetch_personal_dynamics, format_personal_dynamics
    try:
        
        data = await request.json()
        limit = data.get('limit', 10)
        
        # 获取个性化内容
        personal_content = await fetch_personal_dynamics(limit=limit)
        
        if not personal_content['success']:
            return JSONResponse({
                "success": False,
                "error": "无法获取个性化内容",
                "detail": personal_content.get('error', '未知错误')
            }, status_code=500)
        
        # 格式化内容用于前端显示
        formatted_content = format_personal_dynamics(personal_content)
        
        return JSONResponse({
            "success": True,
            "data": {
                "raw": personal_content,
                "formatted": formatted_content,
                "platforms": [k for k in personal_content.keys() if k not in ('success', 'error', 'region')]
            }
        })
        
    except Exception as e:
        logger.error(f"获取个性化内容失败: {e}")
        return JSONResponse({
            "success": False,
            "error": "服务器内部错误",
            "detail": str(e)
        }, status_code=500)