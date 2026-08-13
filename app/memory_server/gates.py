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
  - persisted maintenance state (``_maint_view`` to read,
    ``_amutate_maint_state`` to write, ``_aload_maint_state`` at startup)
  - hot-reloadable feature switches (``_ais_review_enabled`` /
    ``_ais_powerful_memory_enabled``)
  - idle detection (``_touch_activity`` / ``_is_idle``) and the loop
    scheduling constants (poll intervals, staggered initial delays)

``idle_maintenance_state.json`` is the one process-wide (not per-character)
file this package owns, and roughly ten call sites across request handlers and
background tasks update it. ``_amutate_maint_state`` is the ONLY write entry
point: it runs read → mutate → persist as a single critical section. Reads go
through ``_maint_view``, which hands back a read-only snapshot, so ``_maint_state``
itself must not be named outside this module (there is a guard test for that) —
see the block comment above the lock for the full contract.

``_maint_state`` and ``_last_activity_time`` are REBOUND in place (not just
mutated), so in-module code must access them as module globals rather than via a
from-import snapshot, which goes stale after the first ``_aload_maint_state`` /
``_touch_activity`` call. For the same reason tests must monkeypatch these names
(and the switch helpers) on THIS module, not on the package facade.
"""

import asyncio
import json
import os
import threading
from datetime import datetime
from types import MappingProxyType
from typing import Any, Callable, Mapping

from utils.config_manager import get_config_manager
# 模块级 import（本文件其他 file_utils 用法是函数内 import）：atomic_write_json 会在
# _maint_state_lock 临界区内被调，函数内 import 会把一次 import machinery 关进临界区。
# utils/file_utils 只依赖标准库，且 utils.config_manager 已经在模块级导入它，没有环。
from utils.file_utils import atomic_write_json

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
_INITIAL_DELAY_SCOPED_REFINE = 600   # SCOPED_REFINE 首次（群记忆轻量 refine，续接错峰梯队）
_INITIAL_DELAY_REFLECTION_SYNTHESIS = 200  # REFLECTION_SYNTHESIS 首次（错过 AUTO_PROMOTE 150 与 ARCHIVE 250，给 SignalLoop 60s + 一两次实际 fact 产出留余地）

# ── 持久化维护状态（跨重启保留 review_clean 标记） ──────────────────
_maint_state: dict[str, dict] = {}   # {角色名: {"review_clean": bool, "last_review_ts": str}}

# ── 维护状态的唯一加锁写入口 ────────────────────────────────────────
# 这个文件是本包唯一一个不分角色的进程级文件，写点横跨 /cache /process /renew
# /settle 四个 handler、review 后台 task 和 compress 兜底 task。重构前每个写点都是
# 「裸读模块级 dict → 改字段 → 整体覆盖落盘」且全程无锁，两层后果：
#   (a) 两个协程各自 to_thread → 两个 worker 线程同时 os.replace 同一个文件，
#       Windows 上直接 PermissionError(WinError 5)；
#   (b) 更重的一层：各自的 json.dumps 发生在不同时刻，而 os.replace 的落地顺序可以
#       和 dumps 顺序反过来 → 后落地的内容更旧，把对方刚写的退避计数 /
#       review_clean 标记抹掉。
#
# 为什么是 threading.Lock + 整段临界区丢进一个 to_thread，而不是 asyncio.Lock：
# 1) 这是本仓库写盘的既有正确范式 —— memory/event_log.py 的 record_and_save 明写
#    「五步放进一个 per-character threading.Lock，整体包进一个 asyncio.to_thread，
#    绝不跨 await 持锁」。本包里那些 asyncio.Lock（_settle_locks /
#    _review_spawn_locks）保的是 loop 内的编排不变量，不是文件。
# 2) 模块级 asyncio.Lock 一旦真发生争用就绑定当时的 event loop；本仓库 pytest 是
#    asyncio_mode=auto + function-scope loop，第二个有争用的用例会直接
#    RuntimeError（绑到了别的 loop），而且失败后锁的内部状态还残留成已持有。
# 3) threading.Lock 把 json.dumps 也关进临界区（atomic_write_json 是先 dumps 再
#    写）。asyncio.Lock 的版本 dumps 跑在 worker 线程里，任何残留的锁外 mutation
#    都可能撞出 "dictionary changed size during iteration"。
#
# 取消安全：整段临界区在**一个** asyncio.to_thread 里跑完，锁的 acquire/release 都
# 发生在 worker 线程内部。asyncio.to_thread 交出去就取消不掉——等它的协程被 cancel
# 时线程照样跑完、照样释放锁，所以既不会漏掉落盘、也不会把锁泄漏成永久持有。正因如此
# 这里不需要 shield + 等收尾：本模块任何地方都没有「持锁跨 await」。
_maint_state_lock = threading.Lock()

# mutator 契约：**同步**函数，入参是本角色的可变 sub-dict，返回 (dirty, value)。
#   - dirty=False → 跳过落盘（调用方本来就不该无脑写；_aclear_review_clean 每轮
#     /cache、/process 都调，标记本来是 False 时不该白付一次 fsync+replace）。
#   - value 原样回传给调用方，用来把「锁内做出的判定」带出来（如 dead-letter）。
# 故意要求同步：这样「锁内 await」「锁内再取别的锁」在语法上就写不出来，
# 临界区结构性地只剩纯 CPU + 一次落盘。
# 故意不用 RLock：RLock 会让嵌套 RMW 的内层落盘把外层改了一半的状态写进磁盘。
MaintStateMutator = Callable[[dict], tuple[bool, Any]]

_EMPTY_MAINT_VIEW: Mapping[str, Any] = MappingProxyType({})


def _maint_state_path() -> str:
    return os.path.join(str(_config_manager.memory_dir), 'idle_maintenance_state.json')


def _maint_view(lanlan_name: str) -> Mapping[str, Any]:
    """Read-only SNAPSHOT of one character's maintenance state.

    The proxy wraps a copy, not the live sub-dict, so the result is safe to
    iterate and to hold across an ``await``: a worker thread running a mutator
    can ``setdefault`` new keys at any moment, and iterating a live mapping
    while that happens raises "dictionary changed size during iteration".
    Copying is one C-level ``dict()`` call over a handful of scalars, so it is
    cheaper than the alternative of taking the lock. The per-character sub-dict
    only ever holds scalars, hence a shallow copy is a genuine seal: callers
    cannot reach a mutable handle and must go through ``_amutate_maint_state``.

    Deliberately lock-free. Every field read is advisory — the mutator
    re-checks whatever it depends on inside the critical section — and blocking
    the event loop on another writer's fsync would cost far more than reading a
    value that is a few milliseconds stale.
    """
    entry = _maint_state.get(lanlan_name)
    if entry is None:
        return _EMPTY_MAINT_VIEW
    return MappingProxyType(dict(entry))


def _persist_maint_state_locked() -> None:
    """Write the whole maintenance map to disk. Caller MUST hold ``_maint_state_lock``."""
    atomic_write_json(_maint_state_path(), _maint_state,
                      indent=2, ensure_ascii=False)


def _rebind_maint_state_locked(data: dict[str, dict]) -> None:
    """Replace the whole maintenance map. Acquires ``_maint_state_lock`` itself."""
    global _maint_state
    with _maint_state_lock:
        _maint_state = data


def _mutate_maint_state_locked(lanlan_name: str, mutator: MaintStateMutator) -> Any:
    """Read → mutate → persist as one critical section. Run me inside a worker thread."""
    with _maint_state_lock:
        # sub-dict 必须在锁内取：_aload_maint_state 会 global 重绑定 _maint_state，
        # 锁外取到的 sub-dict 可能已经从新 dict 上掉下来变成孤儿——改了它既不会被
        # 落盘、也不会被后续的读看到。
        state = _maint_state.setdefault(lanlan_name, {})
        dirty, value = mutator(state)
        if not dirty:
            return value
        try:
            _persist_maint_state_locked()
        except Exception as e:
            # 与重构前的 _asave_maint_state 一致：落盘失败只告警，内存态保留、不往
            # 上抛。/cache /process /renew /settle 四个 handler 都在 try 里调
            # _aclear_review_clean，让写盘抖动冒出去会把整轮请求变成
            # {"status": "error"}，进而卡住 cross_server 的 last_synced_index。
            logger.warning(f"[IdleMaint] 维护状态保存失败: {e}")
        return value


async def _amutate_maint_state(lanlan_name: str, mutator: MaintStateMutator) -> Any:
    """The single write entry point for ``idle_maintenance_state.json``.

    Hands the whole read-mutate-persist sequence to one worker thread so that
    concurrent writers cannot interleave (see the block comment above
    ``_maint_state_lock`` for why the lock is a ``threading.Lock`` and why the
    mutator must be synchronous).
    """
    return await asyncio.to_thread(_mutate_maint_state_locked, lanlan_name, mutator)


async def _aload_maint_state() -> None:
    """Load maintenance state from disk at startup."""
    from utils.file_utils import read_json_async
    path = _maint_state_path()
    if not await asyncio.to_thread(os.path.exists, path):
        await asyncio.to_thread(_rebind_maint_state_locked, {})
        return
    try:
        data = await read_json_async(path)
        if isinstance(data, dict):
            await asyncio.to_thread(_rebind_maint_state_locked, data)
            logger.debug(f"[IdleMaint] 已加载维护状态: {len(data)} 个角色")
            return
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        # UnicodeDecodeError 必须单列：read_json 是 open(encoding='utf-8') + json.load，
        # 文件被外部工具按 GBK/UTF-16 写过、或断电写了半截多字节序列时抛的是它，
        # 而它是 ValueError 的子类——既不是 JSONDecodeError 也不是 OSError。漏掉就会
        # 从这里冒到唯一的调用点 ensure_memory_server_runtime_initialized（那次 await
        # 没有 try），把整个 memory_server 的 runtime 初始化打断。
        logger.warning(f"[IdleMaint] 维护状态文件加载失败: {e}")
    await asyncio.to_thread(_rebind_maint_state_locked, {})


def _is_review_clean(lanlan_name: str) -> bool:
    """Check whether the character is in the review_clean state (reviewed and no new conversation)."""
    return _maint_view(lanlan_name).get('review_clean', False)


def _mutate_clear_review_clean(state: dict) -> tuple[bool, None]:
    """Mutator: drop the review_clean flag, no-op when it is already clear."""
    if not state.get('review_clean'):
        return False, None
    state['review_clean'] = False
    return True, None


async def _aclear_review_clean(lanlan_name: str) -> None:
    """Clear the review_clean flag when a new human message arrives."""
    # 锁外快筛：/cache 和 /process 每轮都调这里，而标记绝大多数时候本来就是 False。
    # 没有这一层，热路径上每条 user 消息都要白切一次线程。快筛读到的值可能过期，
    # 但无害——真正的判定在 _mutate_clear_review_clean 里锁内又做了一次，所以
    # 「假阴性」只会让本轮少写一次（下一条消息立刻会补上），不会写坏状态。
    if not _is_review_clean(lanlan_name):
        return
    await _amutate_maint_state(lanlan_name, _mutate_clear_review_clean)


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
