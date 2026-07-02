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

"""Proactive source decay history (persistent) and source weight
computation for proactive phase 1 source selection.

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from ._shared import logger
from .proactive_history import _RECENT_CHAT_MAX_AGE_SECONDS, _proactive_chat_history, _reminiscence_usage_history
import asyncio
import hashlib
import random
import re
import time
from pathlib import Path
from typing import Any
from ..shared_state import get_config_manager
from config import (
    PROACTIVE_SOURCE_HARD_SKIP_SECONDS,
    PROACTIVE_SOURCE_HALF_LIFE_BY_KIND,
    PROACTIVE_SOURCE_HALF_LIFE_DEFAULT,
    PROACTIVE_SOURCE_FORGET_P,
)
from utils.file_utils import atomic_write_json_async, read_json


# --- 全局来源衰减历史（跨角色 / 持久化）---
# 主动搭话消费过的 web / music / image 链接进入这里，按 URL hash 索引。
# 5h 内硬 skip（p_skip=1），其后按 kind 各自半衰期指数衰减；p_skip 低于阈值
# 时直接遗忘。所有 IO 走 asyncio.to_thread / atomic_write_json_async，过滤
# 路径只读 dict + RNG，不阻塞 event loop。
# （衰减参数定义在 config/__init__.py 与项目其他 budget 常量统一维护）
_SOURCE_HISTORY_FILENAME = "proactive_source_history.json"


_SOURCE_HISTORY_SCHEMA_VERSION = 1


_source_history: dict[str, dict[str, Any]] = {}


_source_history_lock = asyncio.Lock()


_source_history_loaded = False


def _source_history_path() -> Path:
    return Path(get_config_manager().memory_dir) / _SOURCE_HISTORY_FILENAME


def _source_hash(url: str = '', fallback_title: str = '') -> str:
    """URL first, otherwise the normalized title as fallback. An empty string means "cannot be identified stably"."""
    norm = (url or '').strip().lower().rstrip('/')
    if norm:
        return hashlib.sha256(norm.encode('utf-8')).hexdigest()
    title_norm = re.sub(r'\s+', ' ', (fallback_title or '').strip().lower())
    if title_norm:
        return hashlib.sha256(('t::' + title_norm).encode('utf-8')).hexdigest()
    return ''


def _half_life_for(kind: str) -> float:
    return PROACTIVE_SOURCE_HALF_LIFE_BY_KIND.get(kind, PROACTIVE_SOURCE_HALF_LIFE_DEFAULT)


def _source_skip_probability(age: float, half_life: float) -> float:
    if age < PROACTIVE_SOURCE_HARD_SKIP_SECONDS:
        return 1.0
    decay_age = age - PROACTIVE_SOURCE_HARD_SKIP_SECONDS
    return 0.5 ** (decay_age / half_life)


def _should_skip_source(url_hash: str) -> bool:
    """Synchronous, purely in-memory check, O(1); callable directly inside the synchronous picking loop."""
    if not url_hash:
        return False
    entry = _source_history.get(url_hash)
    if not entry:
        return False
    age = time.time() - entry.get('ts', 0.0)
    p = _source_skip_probability(age, _half_life_for(entry.get('kind', 'web')))
    if p >= 1.0:
        return True
    if p <= 0.0:
        return False
    return random.random() < p


async def _ensure_source_history_loaded() -> None:
    """Lazy loading, idempotent. The file read goes to the thread pool and does not block the event loop."""
    global _source_history_loaded
    if _source_history_loaded:
        return
    async with _source_history_lock:
        if _source_history_loaded:
            return
        path = _source_history_path()
        try:
            data = await asyncio.to_thread(read_json, path)
            entries = data.get('entries') if isinstance(data, dict) else None
            if isinstance(entries, dict):
                # 加载时顺便丢掉早已遗忘阈值之下的条目
                now = time.time()
                for h, entry in entries.items():
                    if not isinstance(entry, dict):
                        continue
                    age = now - float(entry.get('ts', 0.0) or 0.0)
                    p = _source_skip_probability(
                        age, _half_life_for(entry.get('kind', 'web'))
                    )
                    if p >= PROACTIVE_SOURCE_FORGET_P:
                        _source_history[h] = entry
        except FileNotFoundError:
            # 首次运行 / 全新机器：尚无历史文件，按空历史继续
            pass
        except Exception as e:
            logger.warning(
                f"加载 {_SOURCE_HISTORY_FILENAME} 失败，按空历史处理: {type(e).__name__}: {e}"
            )
        _source_history_loaded = True


async def _record_source_used(
    *,
    url: str,
    kind: str,
    title: str = '',
) -> None:
    """Called after a source is consumed or deliberately suppressed: update memory → prune → persist.

    Concurrent records are serialized by an asyncio.Lock; persistence goes through
    atomic_write_json_async (fsync + os.replace in the thread pool), so the main
    coroutine is never stalled by disk IO.
    """
    h = _source_hash(url, title)
    if not h:
        return
    snapshot: dict[str, Any] | None = None
    async with _source_history_lock:
        _source_history[h] = {
            "ts": time.time(),
            "kind": kind,
            "title": (title or '')[:80],
        }
        # 顺手 prune：写盘前剔除已遗忘条目，文件体积自然有界
        now = time.time()
        forget = [
            hh for hh, entry in _source_history.items()
            if _source_skip_probability(
                now - float(entry.get('ts', 0.0) or 0.0),
                _half_life_for(entry.get('kind', 'web'))
            ) < PROACTIVE_SOURCE_FORGET_P
        ]
        for hh in forget:
            _source_history.pop(hh, None)
        snapshot = {
            "v": _SOURCE_HISTORY_SCHEMA_VERSION,
            "entries": dict(_source_history),
        }
    try:
        await atomic_write_json_async(_source_history_path(), snapshot)
    except Exception as e:
        # 写盘失败不影响主流程：下一次 record 会整文件重写覆盖
        logger.warning(
            f"落盘 {_SOURCE_HISTORY_FILENAME} 失败: {type(e).__name__}: {e}"
        )


# --- 来源动态权重系统 ---
_SOURCE_WEIGHT_DECAY_LAMBDA = 0.002   # 指数衰减系数，半衰期 ≈ 5.8 分钟


_SOURCE_WEIGHT_K = 0.30               # freshness 惩罚系数：freshness = 1 / (1 + k * raw_score)


_SOURCE_WEIGHT_FLOOR = 0.20           # 归一化权重绝对下限


def _compute_source_weights(
    lanlan_name: str,
    candidate_channels: list[str],
) -> dict[str, float]:
    """
    Compute normalized weights for each source.

    Algorithm:
    1. take records within 1h from _proactive_chat_history
    2. raw_score[ch] = Σ exp(-λ·age)  (each use accumulates with time decay)
    3. freshness[ch] = 1 / (1 + k·raw_score[ch])
    4. normalize: weight[ch] = freshness[ch] / Σ freshness

    With no history, returns a uniform distribution.

    Args:
        lanlan_name: character name
        candidate_channels: channels participating in the weighting (excluding vision)

    Returns:
        {channel: normalized_weight}, with weights summing to 1.0
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

    # Reminiscence usage lives in a separate buffer (kept out of
    # _proactive_chat_history to avoid polluting dedup / similarity
    # checks). Inject its decayed-frequency contribution here so the
    # weight calculation treats it on the same footing as web/news/etc.
    if 'reminiscence' in raw_scores:
        rem_buf = _reminiscence_usage_history.get(lanlan_name)
        if rem_buf:
            for ts in rem_buf:
                age = now - ts
                if age > _SOURCE_WEIGHT_WINDOW:
                    continue
                raw_scores['reminiscence'] += math.exp(-_SOURCE_WEIGHT_DECAY_LAMBDA * age)

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
    Return the set of channels that should be culled.

    Threshold = min(_SOURCE_WEIGHT_FLOOR, 1 / N)
    - with 4 channels, threshold=0.20; 2 uses trigger culling
    - with 6 channels, threshold=0.167; competition is fiercer

    Args:
        weights: normalized weights returned by _compute_source_weights

    Returns:
        set of channel names to cull
    """
    n = len(weights)
    if n <= 1:
        return set()  # 只剩 1 个来源时不剔除

    threshold = min(_SOURCE_WEIGHT_FLOOR, 1.0 / n)
    return {ch for ch, w in weights.items() if w < threshold}


# 复用 _RECENT_CHAT_MAX_AGE_SECONDS 作为权重窗口
_SOURCE_WEIGHT_WINDOW = _RECENT_CHAT_MAX_AGE_SECONDS
