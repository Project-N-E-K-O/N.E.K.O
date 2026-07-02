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

"""Emotion analysis: label normalization tables, keyword heuristics and
the /emotion/analysis endpoint.

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from ._shared import _validate_local_mutation_request, logger, router
import difflib
import math
import re
from fastapi import Request
from utils.llm_client import (
    create_chat_llm_async,
)
from ..shared_state import get_config_manager, get_sync_message_queue
from config import (
    EMOTION_ANALYSIS_MAX_TOKENS,
)
from config.prompts.prompts_emotion import (
    get_outward_emotion_analysis_prompt,
    get_emotion_keywords_flat,
    get_angry_attack_patterns_flat,
    get_sad_vulnerable_patterns_flat,
    get_happy_playful_patterns_flat,
    get_heuristic_negation_tokens_flat,
    get_heuristic_tight_negation_tokens_flat,
    get_heuristic_negation_blocklist_flat,
    get_heuristic_contrast_conjunctions_flat,
    get_emotion_label_aliases_flat,
)
from utils.language_utils import detect_language, normalize_language_code


# 统一的表情包图源白名单由 utils.meme_fetcher 维护，本文件仅用于引入

# 多语言关键词/别名表统一在 config/prompts/prompts_emotion.py 维护，此处只做扁平索引。
_EMOTION_LABEL_ALIASES = get_emotion_label_aliases_flat()


_EMOTION_CANONICAL_LABELS = ("happy", "sad", "angry", "surprised", "neutral")


_EMOTION_NORMALIZED_ALIAS_LOOKUP = {}


_EMOTION_COMPACT_ALIAS_LOOKUP = {}


for _alias, _canonical in _EMOTION_LABEL_ALIASES.items():
    _normalized_alias = re.sub(r"[\s\-_]+", " ", str(_alias).strip().lower())
    if not _normalized_alias:
        continue
    _EMOTION_NORMALIZED_ALIAS_LOOKUP[_normalized_alias] = _canonical
    _compact_alias = re.sub(r"[\W_]+", "", _normalized_alias, flags=re.UNICODE)
    if _compact_alias and _compact_alias not in _EMOTION_COMPACT_ALIAS_LOOKUP:
        _EMOTION_COMPACT_ALIAS_LOOKUP[_compact_alias] = _canonical


_EMOTION_FUZZY_ALIAS_KEYS = tuple(_EMOTION_NORMALIZED_ALIAS_LOOKUP.keys())


_EMOTION_FUZZY_COMPACT_KEYS = tuple(_EMOTION_COMPACT_ALIAS_LOOKUP.keys())


_ASCII_EMOTION_ALIAS_RE = re.compile(r"^[a-z0-9]+(?:\s+[a-z0-9]+)*$")


_EMOTION_NEGATION_WORDS = frozenset((
    "not", "no", "never", "without",
    "안", "아니", "못", "않", "아니다", "아닌", "아님",
    "не", "нет", "никогда",
))


_EMOTION_NEGATION_PREFIXES = (
    "不是", "并不", "并非", "不太", "没那么", "没有", "并没有",
    "不", "没", "無", "无", "非", "别", "別",
    "안", "아니", "못",
    "не", "нет", "никогда",
)


_EMOTION_NEGATION_SUFFIXES = (
    "지 않", "지않", "지 않아", "지않아", "지 않다", "지않다", "지 않음", "지않음",
    "지 못", "지못", "지 못해", "지못해", "지 못하다", "지못하다",
    "않", "않아", "않다", "않음", "아냐", "아니야", "아니다", "아닌", "아님",
)


_EMOTION_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)


_EMOTION_NEGATION_COMPACT_PREFIXES = tuple(sorted({
    re.sub(r"[\W_]+", "", str(negation).strip().lower(), flags=re.UNICODE)
    for negation in (*_EMOTION_NEGATION_PREFIXES, *_EMOTION_NEGATION_WORDS)
    if str(negation).strip()
}, key=len, reverse=True))


_EMOTION_NEGATION_COMPACT_SUFFIXES = tuple(sorted({
    re.sub(r"[\W_]+", "", str(negation).strip().lower(), flags=re.UNICODE)
    for negation in _EMOTION_NEGATION_SUFFIXES
    if str(negation).strip()
}, key=len, reverse=True))


_EMOTION_NEGATION_CONTEXT_WINDOW = max(
    (len(negation) for negation in _EMOTION_NEGATION_COMPACT_PREFIXES),
    default=6,
)


def _looks_like_emotion_compact_candidate(candidate, cutoff):
    if not candidate:
        return False
    if candidate in _EMOTION_COMPACT_ALIAS_LOOKUP:
        return True
    return bool(difflib.get_close_matches(
        candidate,
        _EMOTION_FUZZY_COMPACT_KEYS,
        n=1,
        cutoff=cutoff,
    ))


def _has_negated_emotion_phrase(normalized_text, compact_text, fuzzy_compact_cutoff):
    tokens = [token for token in _EMOTION_TOKEN_RE.findall(normalized_text) if token]
    if tokens and any(token in _EMOTION_NEGATION_WORDS for token in tokens):
        remaining_compact = re.sub(
            r"[\W_]+",
            "",
            "".join(token for token in tokens if token not in _EMOTION_NEGATION_WORDS),
            flags=re.UNICODE,
        )
        if _looks_like_emotion_compact_candidate(remaining_compact, fuzzy_compact_cutoff):
            return True

    for negation in _EMOTION_NEGATION_COMPACT_PREFIXES:
        if not compact_text.startswith(negation):
            continue
        if _looks_like_emotion_compact_candidate(compact_text[len(negation):], fuzzy_compact_cutoff):
            return True

    for negation in _EMOTION_NEGATION_COMPACT_SUFFIXES:
        marker_index = compact_text.find(negation)
        if marker_index <= 0:
            continue
        if _looks_like_emotion_compact_candidate(compact_text[:marker_index], fuzzy_compact_cutoff):
            return True

    return False


# 启发式关键词/patterns 全部在 config/prompts/prompts_emotion.py 按语种维护，此处只做扁平化。
_EMOTION_KEYWORDS = get_emotion_keywords_flat()


_SAD_VULNERABLE_PATTERNS = get_sad_vulnerable_patterns_flat()


_ANGRY_ATTACK_PATTERNS = get_angry_attack_patterns_flat()


_HAPPY_PLAYFUL_PATTERNS = get_happy_playful_patterns_flat()


def _normalize_emotion_label(raw_emotion, raw_confidence=None):
    emotion_text = str(raw_emotion or "").strip().lower()
    if not emotion_text:
        return "neutral"
    normalized_text = re.sub(r"[\s\-_]+", " ", emotion_text)
    if normalized_text in _EMOTION_NORMALIZED_ALIAS_LOOKUP:
        return _EMOTION_NORMALIZED_ALIAS_LOOKUP[normalized_text]

    compact_text = re.sub(r"[\W_]+", "", emotion_text, flags=re.UNICODE)
    if compact_text in _EMOTION_COMPACT_ALIAS_LOOKUP:
        return _EMOTION_COMPACT_ALIAS_LOOKUP[compact_text]

    high_confidence = raw_confidence is not None and _coerce_emotion_confidence(raw_confidence, 0.0) >= 0.72
    fuzzy_alias_cutoff = 0.74 if high_confidence else 0.9
    fuzzy_compact_cutoff = 0.72 if high_confidence else 0.88

    if _has_negated_emotion_phrase(normalized_text, compact_text, fuzzy_compact_cutoff):
        return "neutral"

    def _is_negated_ascii_match(match_start):
        prefix_tokens = _EMOTION_TOKEN_RE.findall(normalized_text[:match_start])
        return any(token in _EMOTION_NEGATION_WORDS for token in prefix_tokens[-3:])

    def _is_negated_compact_match(match_start):
        prefix = compact_text[max(0, match_start - _EMOTION_NEGATION_CONTEXT_WINDOW):match_start]
        return any(prefix.endswith(negation) for negation in _EMOTION_NEGATION_COMPACT_PREFIXES)

    alias_items = sorted(
        _EMOTION_NORMALIZED_ALIAS_LOOKUP.items(),
        key=lambda item: len(item[0]),
        reverse=True
    )
    for alias, canonical in alias_items:
        if not alias:
            continue
        if _ASCII_EMOTION_ALIAS_RE.match(alias):
            pattern = r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
            for match in re.finditer(pattern, normalized_text):
                if not _is_negated_ascii_match(match.start()):
                    return canonical
            continue

        compact_alias = re.sub(r"[\W_]+", "", alias, flags=re.UNICODE)
        if not compact_alias:
            continue
        search_start = 0
        while True:
            match_start = compact_text.find(compact_alias, search_start)
            if match_start < 0:
                break
            if not _is_negated_compact_match(match_start):
                return canonical
            search_start = match_start + len(compact_alias)

    fuzzy_alias_match = difflib.get_close_matches(
        normalized_text,
        _EMOTION_FUZZY_ALIAS_KEYS,
        n=1,
        cutoff=fuzzy_alias_cutoff
    )
    if fuzzy_alias_match:
        return _EMOTION_NORMALIZED_ALIAS_LOOKUP[fuzzy_alias_match[0]]

    if compact_text:
        fuzzy_compact_match = difflib.get_close_matches(
            compact_text,
            _EMOTION_FUZZY_COMPACT_KEYS,
            n=1,
            cutoff=fuzzy_compact_cutoff
        )
        if fuzzy_compact_match:
            return _EMOTION_COMPACT_ALIAS_LOOKUP[fuzzy_compact_match[0]]

    if high_confidence:
        fuzzy_canonical = difflib.get_close_matches(
            normalized_text,
            _EMOTION_CANONICAL_LABELS,
            n=1,
            cutoff=0.55
        )
        if fuzzy_canonical:
            return fuzzy_canonical[0]

    return "neutral"


def _push_emotion_update(lanlan_name, emotion, confidence):
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


def _emotion_response(emotion, confidence):
    return {
        "emotion": emotion,
        "confidence": confidence
    }


def _coerce_emotion_confidence(raw_confidence, default=0.5):
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        confidence = float(default)
    if not math.isfinite(confidence):
        confidence = float(default)
    return max(0.0, min(1.0, confidence))


# 启发式打分时的否定回看 token / 转折连词表统一在 config/prompts/prompts_emotion.py 按语种维护。
_HEURISTIC_NEGATION_TOKENS = get_heuristic_negation_tokens_flat()


_HEURISTIC_TIGHT_NEGATION_TOKENS = get_heuristic_tight_negation_tokens_flat()


_HEURISTIC_NEGATION_BLOCKLIST = get_heuristic_negation_blocklist_flat()


_HEURISTIC_CONTRAST_CONJUNCTIONS = get_heuristic_contrast_conjunctions_flat()


_HEURISTIC_NEGATION_LOOKBACK = 14


# zh 单字否定（`不/没/别/未` 等）假阳率高，必须紧邻情绪词才算真否定，
# 避免 `不错/不思议/不具合` 等非否定词组里的单字误触发。
_HEURISTIC_TIGHT_NEGATION_LOOKBACK = 2


# 子句分隔符：回看窗口越过分隔符后的内容视为另一小句，不再修饰本次命中。
# 避免 "我不是难过，我是生气" 中 `生气` 的回看抓到前一小句的 `不` 而被误判否定。
_HEURISTIC_CLAUSE_DELIMITERS = (
    '.', ',', ';', '!', '?', '\n',
    '，', '。', '；', '！', '？', '、', '：', ':',
)


def _has_heuristic_negation_before(text_value, position):
    if position <= 0:
        return False
    start = max(0, position - _HEURISTIC_NEGATION_LOOKBACK)
    window = text_value[start:position]
    # 1) 窗口越过子句分隔符（标点）的部分丢掉，只看与命中关键词同小句的前文
    last_delim = -1
    for delim in _HEURISTIC_CLAUSE_DELIMITERS:
        idx = window.rfind(delim)
        if idx > last_delim:
            last_delim = idx
    if last_delim >= 0:
        window = window[last_delim + 1:]
    # 2) 句首场景补一个前导空格，统一处理带前导空格的 token（否定 ` no `、连词 ` but `）
    window = ' ' + window
    # 3) 让步/转折连词同样切断否定范围：处理 "not X but Y / 不是 X 而是 Y" 对比句，
    #    避免前半的否定被错误带到后半的情绪关键词。
    last_conj = -1
    for conj in _HEURISTIC_CONTRAST_CONJUNCTIONS:
        idx = window.rfind(conj)
        if idx >= 0:
            end_pos = idx + len(conj)
            if end_pos > last_conj:
                last_conj = end_pos
    if last_conj >= 0:
        window = window[last_conj:]
    # 4) 排除非否定固定搭配（`not only / 不仅 / не только` 等肯定结构里的 not/不/не
    #    并不是真否定）：把这些短语从 window 里替换成等长空白后再做 token 匹配。
    sanitized = window
    for phrase in _HEURISTIC_NEGATION_BLOCKLIST:
        if phrase and phrase in sanitized:
            sanitized = sanitized.replace(phrase, ' ' * len(phrase))
    # 5) 多字否定 token（宽 lookback）
    if any(token in sanitized for token in _HEURISTIC_NEGATION_TOKENS):
        return True
    # 6) zh 单字否定 token：仅在紧邻命中关键词的尾部窗口里才算真否定，
    #    避免 `不错/不思议/不具合` 等非否定词组里的单字误触发整个否定。
    if _HEURISTIC_TIGHT_NEGATION_TOKENS:
        tight_window = sanitized[-_HEURISTIC_TIGHT_NEGATION_LOOKBACK:]
        if any(token in tight_window for token in _HEURISTIC_TIGHT_NEGATION_TOKENS):
            return True
    return False


# 英文 keyword 用 ASCII-only 词边界匹配，避免 `happy` 命中 `unhappy`、`surprised`
# 命中 `unsurprised` 这类反向情绪嵌入。
# 注意：不能用 `\b`，因为 Python regex 默认 Unicode 模式下 CJK 也算 \w，
# 在 mixed-script 文本（如 `好happy啊 / 超annoyed欸`）里 `好` 和 `h` 之间没有
# word boundary，导致英文 keyword 完全失配。改用前后 ASCII 字母断言：
# `(?<![a-zA-Z])keyword(?![a-zA-Z])`，CJK / 标点 / 空白都允许作为边界。
_ASCII_WORD_KEYWORD_RE_CACHE = {}


def _is_ascii_word_keyword(keyword):
    if not keyword:
        return False
    return all(c.isascii() and (c.isalpha() or c in " '") for c in keyword)


def _count_keyword_hits(text_value, keyword):
    if not keyword or not text_value:
        return 0
    if _is_ascii_word_keyword(keyword):
        pattern = _ASCII_WORD_KEYWORD_RE_CACHE.get(keyword)
        if pattern is None:
            pattern = re.compile(r'(?<![a-zA-Z])' + re.escape(keyword) + r'(?![a-zA-Z])')
            _ASCII_WORD_KEYWORD_RE_CACHE[keyword] = pattern
        hits = 0
        for match in pattern.finditer(text_value):
            if not _has_heuristic_negation_before(text_value, match.start()):
                hits += 1
        return hits
    hits = 0
    search_start = 0
    while True:
        pos = text_value.find(keyword, search_start)
        if pos < 0:
            break
        if not _has_heuristic_negation_before(text_value, pos):
            hits += 1
        search_start = pos + len(keyword)
    return hits


def _infer_emotion_from_text(text):
    text_value = str(text or "").lower()
    if not text_value:
        return None, 0

    scores = {key: 0 for key in _EMOTION_KEYWORDS}
    for emotion, keywords in _EMOTION_KEYWORDS.items():
        for keyword in keywords:
            scores[emotion] += _count_keyword_hits(text_value, keyword)

    if "!!" in text_value or "！？" in text_value or "!?" in text_value or "??" in text_value:
        scores["surprised"] += 1

    sad_vulnerable_hits = sum(_count_keyword_hits(text_value, p) for p in _SAD_VULNERABLE_PATTERNS)
    angry_attack_hits = sum(_count_keyword_hits(text_value, p) for p in _ANGRY_ATTACK_PATTERNS)
    happy_playful_hits = sum(_count_keyword_hits(text_value, p) for p in _HAPPY_PLAYFUL_PATTERNS)

    if sad_vulnerable_hits:
        scores["sad"] += sad_vulnerable_hits * 2
    if angry_attack_hits:
        scores["angry"] += angry_attack_hits * 2
    if happy_playful_hits and not sad_vulnerable_hits and not angry_attack_hits:
        # playful patterns（哈哈/嘿嘿/嘻嘻/可爱/好耶 等）大量与 happy keyword 重叠，
        # 重复出现时 keyword 那边已经按命中数累加分数；这里只额外 +1 作为信号 boost，
        # 避免 `haha haha haha / 哈哈哈哈哈` 类 filler 文本被双倍放大触发 override。
        scores["happy"] += 1
    if sad_vulnerable_hits and happy_playful_hits:
        # 撒娇外壳下的委屈/想哭，优先视为 sad 而不是 happy
        scores["sad"] += 1

    best_emotion = None
    best_score = 0
    for emotion, score in scores.items():
        if score > best_score:
            best_emotion = emotion
            best_score = score

    if best_score <= 0:
        return None, 0
    return best_emotion, best_score


def _resolve_emotion_prompt_language(text):
    try:
        detected_lang = detect_language(str(text or ""))
        return normalize_language_code(detected_lang, format='short')
    except Exception:
        return 'zh'


@router.post('/emotion/analysis')
async def emotion_analysis(request: Request):
    """
    Emotion analysis endpoint.
    func:
    - receives text input, calls the configured emotion analysis model, and returns the emotion class and confidence
    - supports overriding the default API key and model name from request parameters for flexibility
    - parses the model response intelligently, tolerating different formats (plain text, markdown code blocks, JSON strings, etc.) for robustness
    - adjusts the emotion class by confidence, setting it to neutral when confidence is low, improving result reliability
    - pushes the result to the monitor system (when lanlan_name is provided) for realtime interaction and display with the frontend
    """
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    try:
        _config_manager = get_config_manager()
        data = await request.json()
        if not data or 'text' not in data:
            return {"error": "请求体中必须包含text字段"}
        
        text = data['text']
        lanlan_name = data.get('lanlan_name')
        if text is None or str(text).strip() == "":
            emotion = "neutral"
            confidence = 0.5
            _push_emotion_update(lanlan_name, emotion, confidence)
            return _emotion_response(emotion, confidence)

        api_key = data.get('api_key')
        model = data.get('model')
        
        # 使用参数或默认配置，使用 .get() 安全获取避免 KeyError
        emotion_config = _config_manager.get_model_api_config('emotion')
        emotion_api_key = emotion_config.get('api_key')
        emotion_model = emotion_config.get('model')
        emotion_base_url = emotion_config.get('base_url')
        emotion_provider_type = emotion_config.get('provider_type')
        
        # 优先使用请求参数，其次使用配置
        api_key = api_key or emotion_api_key
        model = model or emotion_model
        
        if not api_key:
            return {"error": "情绪分析模型配置缺失: API密钥未提供且配置中未设置默认密钥"}
        
        if not model:
            return {"error": "情绪分析模型配置缺失: 模型名称未提供且配置中未设置默认模型"}
       
        prompt_lang = _resolve_emotion_prompt_language(text)

        # 构建请求消息
        messages = [
            {
                "role": "system", 
                "content": get_outward_emotion_analysis_prompt(prompt_lang)
            },
            {
                "role": "user", 
                "content": text
            }
        ]

        from utils.token_tracker import set_call_type
        set_call_type("emotion")

        # 异步调用模型（使用统一工厂，自动处理 extra_body / provider 兼容）
        llm = await create_chat_llm_async(
            model,
            emotion_base_url,
            api_key,
            provider_type=emotion_provider_type,
            temperature=0.3,
            # Gemini 模型可能返回 markdown 格式，需要更多 token
            max_completion_tokens=EMOTION_ANALYSIS_MAX_TOKENS,
            timeout=30,
        )
        async with llm:
            result = await llm.ainvoke(messages)

        # 解析响应
        result_text = result.content.strip()

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
        emotion = "neutral"
        confidence = 0.5

        def _apply_degraded_emotion_fallback():
            heuristic_emotion, heuristic_score = _infer_emotion_from_text(text)
            if heuristic_emotion:
                return heuristic_emotion, min(0.62, 0.34 + heuristic_score * 0.1)
            # 当模型结果不可用或缺少足够关键词线索时，回退到 neutral。
            return "neutral", 0.5

        try:
            from utils.file_utils import robust_json_loads
            result = robust_json_loads(result_text)
            if not isinstance(result, dict):
                # 有效 JSON 也可能是 null/[]/"text"，此时复用降级启发式处理。
                emotion, confidence = _apply_degraded_emotion_fallback()
            else:
                # 获取emotion和confidence
                raw_emotion = result.get("emotion", "neutral")
                raw_confidence = result.get("confidence", 0.5)
                emotion = _normalize_emotion_label(raw_emotion, raw_confidence)
                confidence = _coerce_emotion_confidence(raw_confidence)
                decision_source = "model"

                heuristic_emotion, heuristic_score = _infer_emotion_from_text(text)
                if heuristic_emotion:
                    # 强 override：启发式分数较高（≥4）且模型置信度不算很高（<0.8）时
                    # 才推翻模型判断；避免单个吐槽词把模型 happy/neutral 翻成 angry。
                    if heuristic_emotion != emotion and heuristic_score >= 4 and confidence < 0.8:
                        emotion = heuristic_emotion
                        confidence = max(confidence, min(0.86, 0.44 + heuristic_score * 0.07))
                        decision_source = "heuristic_strong_override"
                    elif heuristic_emotion == "sad" and emotion == "happy" and heuristic_score >= 2:
                        emotion = heuristic_emotion
                        confidence = max(confidence, min(0.84, 0.5 + heuristic_score * 0.08))
                        decision_source = "heuristic_sad_override"
                    elif emotion == "neutral" and confidence < 0.6:
                        emotion = heuristic_emotion
                        confidence = max(confidence, min(0.78, 0.42 + heuristic_score * 0.12))
                        decision_source = "heuristic_from_neutral"
                    elif confidence < 0.25:
                        emotion = heuristic_emotion
                        confidence = max(confidence, min(0.65, 0.35 + heuristic_score * 0.1))
                        decision_source = "heuristic_from_low_confidence"

                # 当confidence很低时，自动将emotion设置为neutral，避免误报
                if confidence < 0.2:
                    emotion = "neutral"
                    decision_source = "neutral_fallback"
        except ValueError:
            emotion, confidence = _apply_degraded_emotion_fallback()

        _push_emotion_update(lanlan_name, emotion, confidence)
        return _emotion_response(emotion, confidence)
            
    except Exception as e:
        logger.error(f"情感分析失败: {e}")
        return {
            "error": f"情感分析失败: {str(e)}",
            "emotion": "neutral",
            "confidence": 0.0
        }
