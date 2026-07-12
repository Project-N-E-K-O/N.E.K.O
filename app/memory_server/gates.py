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

"""Cross-loop gating state for the memory_server package.

Dependency leaf shared by every background loop and every session endpoint:
  - persisted maintenance state (``_maint_state`` + load/save/clear helpers)
  - hot-reloadable feature switches (``_ais_review_enabled`` /
    ``_ais_powerful_memory_enabled``)
  - idle detection (``_touch_activity`` / ``_is_idle``) and the loop
    scheduling constants (poll intervals, staggered initial delays)

``_maint_state`` and ``_last_activity_time`` are REBOUND in place (not just
mutated), so consumers must access them as ``gates._maint_state`` /
``gates._last_activity_time`` module attributes — a from-import snapshot goes
stale after the first ``_aload_maint_state`` / ``_touch_activity`` call.
For the same reason tests must monkeypatch these names (and the switch
helpers) on THIS module, not on the package facade.
"""

import asyncio
import json
import os
from datetime import datetime

from utils.config_manager import get_config_manager

from ._shared import logger

_config_manager = get_config_manager()

# ── 空闲维护相关 ────────────────────────────────────────────────────
_last_activity_time: datetime = datetime.now()            # 最后一次对话活动时间
IDLE_CHECK_INTERVAL = 40             # 空闲检查轮询间隔（秒）
IDLE_THRESHOLD = 10                  # 多少秒无活动视为空闲（匹配最低 proactive 间隔）
REVIEW_MIN_INTERVAL = 60             # review 最短间隔（秒）。配合消息门双重限流
REVIEW_SKIP_HISTORY_LEN = 8          # 历史不足此数的角色跳过 review
MIN_NEW_MSGS_FOR_REVIEW = 5          # 自上次 review cutoff 起累积 ≥ N 条 user msg 才允许触发新一轮
LONG_IDLE_REVIEW_BYPASS_SECONDS = 1800  # 距上次活动 ≥ 30 min 且有未 review 的新消息 → 绕过新消息门，
                                        # 把"差几条不够批量"的尾巴也整理掉

# ── 启动错峰 initial_delay（避免首轮全部撞 startup + interval 同一时刻） ──
# 每个循环首次执行时间 = startup + 该 delay；之后按各自 INTERVAL 周期跑。
# 设计原则：archive sweep 用最长 INTERVAL (3600s) 但很多用户不到 1h 就退出，
# 必须显著前移；rebuttal/auto_promote 同 300s 间隔但不能同时跑，错开 60s；
# IdleMaint/Signal 已经间隔短，仅给 startup tasks (cloudsave / outbox replay /
# migration) 一点喘息空间。EmbeddingWarmupWorker 自带 30s warmup gate，不在此处。
_INITIAL_DELAY_IDLE_MAINT = 20       # IdleMaint 首次 (原 10s startup 高频已废)
_INITIAL_DELAY_SIGNAL = 60           # Signal extraction 首次 (原 40s)
_INITIAL_DELAY_REBUTTAL = 100        # Rebuttal 首次 (原 300s)
_INITIAL_DELAY_AUTO_PROMOTE = 150    # Auto-promote 首次 (原 300s, 错开 rebuttal 50s)
_INITIAL_DELAY_ARCHIVE = 250         # Archive sweep 首次 (原 3600s, 大幅前移确保短会话用户也能跑到)
_INITIAL_DELAY_PERSONA_REFINE = 400  # PERSONA_REFINE 首次（与 reflection refine 错峰 100s）
_INITIAL_DELAY_REFLECTION_REFINE = 500  # REFLECTION_REFINE 首次
_INITIAL_DELAY_REFLECTION_SYNTHESIS = 200  # REFLECTION_SYNTHESIS 首次（错过 AUTO_PROMOTE 150 与 ARCHIVE 250，给 SignalLoop 60s + 一两次实际 fact 产出留余地）

# ── 持久化维护状态（跨重启保留 review_clean 标记） ──────────────────
_maint_state: dict[str, dict] = {}   # {角色名: {"review_clean": bool, "last_review_ts": str}}


def _maint_state_path() -> str:
    return os.path.join(str(_config_manager.memory_dir), 'idle_maintenance_state.json')


async def _aload_maint_state() -> None:
    """Load maintenance state from disk at startup."""
    from utils.file_utils import read_json_async
    global _maint_state
    path = _maint_state_path()
    if not await asyncio.to_thread(os.path.exists, path):
        _maint_state = {}
        return
    try:
        data = await read_json_async(path)
        if isinstance(data, dict):
            _maint_state = data
            logger.debug(f"[IdleMaint] 已加载维护状态: {len(_maint_state)} 个角色")
            return
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[IdleMaint] 维护状态文件加载失败: {e}")
    _maint_state = {}


async def _asave_maint_state() -> None:
    """Persist maintenance state to disk."""
    from utils.file_utils import atomic_write_json_async
    try:
        await atomic_write_json_async(_maint_state_path(), _maint_state,
                                      indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[IdleMaint] 维护状态保存失败: {e}")


def _is_review_clean(lanlan_name: str) -> bool:
    """Check whether the character is in the review_clean state (reviewed and no new conversation)."""
    return _maint_state.get(lanlan_name, {}).get('review_clean', False)


async def _aclear_review_clean(lanlan_name: str) -> None:
    """Clear the review_clean flag when a new human message arrives."""
    state = _maint_state.get(lanlan_name, {})
    if state.get('review_clean'):
        state['review_clean'] = False
        await _asave_maint_state()


async def _ais_review_enabled() -> bool:
    """Check whether correction/review is enabled in config (async IO)."""
    from utils.file_utils import read_json_async
    try:
        config_path = str(_config_manager.get_runtime_config_path('core_config.json'))
        if not await asyncio.to_thread(os.path.exists, config_path):
            return True
        config_data = await read_json_async(config_path)
        if isinstance(config_data, dict) and not config_data.get('recent_memory_auto_review', True):
            return False
    except Exception as e:
        logger.debug(f"[IdleMaint] 读取 review 开关配置失败，默认启用: {e}")
    return True


async def _ais_powerful_memory_enabled() -> bool:
    """Check whether "powerful memory" is enabled — controls all the new LLM paths introduced by the evidence RFC.

    When off, only the pre-RFC base pipeline remains (Stage-1 fact extraction /
    reflection synthesize / recent compress+review / recall reranker /
    check_feedback for proactive-chat responses) + the time-driven promote
    fallback. Turning it off saves ~40-50% tokens.

    Persisted as the ``powerful_memory_enabled`` field in ``core_config.json``;
    missing defaults to True (for compatibility). Re-opens read_json_async on each
    use, no caching — same hot-reload as ``_ais_review_enabled``, takes effect
    without a restart.
    """
    from utils.file_utils import read_json_async
    try:
        config_path = str(_config_manager.get_runtime_config_path('core_config.json'))
        if not await asyncio.to_thread(os.path.exists, config_path):
            return True
        config_data = await read_json_async(config_path)
        if isinstance(config_data, dict) and not config_data.get('powerful_memory_enabled', True):
            return False
    except Exception as e:
        logger.debug(f"[Memory] 读取强力记忆开关配置失败，默认启用: {e}")
    return True


def _touch_activity() -> None:
    """Record one conversation activity, refreshing the idle timer."""
    global _last_activity_time
    _last_activity_time = datetime.now()


def _is_idle() -> bool:
    """Whether the system is currently idle (more than the threshold since the last activity)."""
    return (datetime.now() - _last_activity_time).total_seconds() >= IDLE_THRESHOLD
