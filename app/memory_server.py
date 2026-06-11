# -*- coding: utf-8 -*-
import sys
import os
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# Wire DI bindings explicitly — direct script invocation
# (``python app/memory_server.py``) doesn't run app/__init__.py.
# Idempotent under launcher's ``from app import memory_server`` path too.
from app.runtime_bindings import install_runtime_bindings as _install_runtime_bindings
_install_runtime_bindings()

from memory import (
    CompressedRecentHistoryManager, ImportantSettingsManager, TimeIndexedMemory,
    FactStore, PersonaManager, ReflectionEngine,
)
from memory.cursors import CursorStore, CURSOR_REBUTTAL_CHECKED_UNTIL
from memory.facts import FactExtractionFailed
from memory.event_log import (
    EventLog, Reconciler,
    EVIDENCE_SOURCE_USER_CONFIRM,
    EVIDENCE_SOURCE_USER_FACT,
    EVIDENCE_SOURCE_USER_IGNORE,
    EVIDENCE_SOURCE_USER_KEYWORD_REBUT,
    EVIDENCE_SOURCE_USER_REBUT,
    EVIDENCE_SOURCE_MIGRATION_SEED,
)
from memory.evidence_handlers import register_evidence_handlers as _register_evidence_handlers
from memory.outbox import Outbox, OP_POST_TURN_SIGNALS
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, JSONResponse
import json
import uvicorn
from utils.llm_client import convert_to_messages
from uuid import uuid4
from config import (
    EVIDENCE_ARCHIVE_DAYS,
    EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS,
    EVIDENCE_NEGATIVE_TARGET_MODEL_TIER,
    EVIDENCE_SIGNAL_CHECK_ENABLED,
    EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS,
    EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES,
    EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS,
    EVIDENCE_AI_AWARE_EVERY_N_A_TICKS,
    MAX_AI_AWARE_WINDOW_MSGS,
    MAX_KNOWN_POOL_FACTS,
    MEMORY_REFLECTION_SYNTHESIS_INTERVAL_SECONDS,
    IGNORED_REINFORCEMENT_DELTA,
    MEMORY_RECHECK_ENABLED,
    MEMORY_RECHECK_INITIAL_DELAY_SECONDS,
    MEMORY_REFINE_CRON_INTERVAL_SECONDS,
    MEMORY_RECHECK_INTERVAL_SECONDS,
    MEMORY_SERVER_PORT,
    USER_CONFIRM_DELTA,
    USER_FACT_NEGATE_DELTA,
    USER_FACT_REINFORCE_DELTA,
    USER_KEYWORD_REBUT_DELTA,
    USER_REBUT_DELTA,
)
from config.prompts.prompts_sys import _loc
from config.prompts.prompts_memory import (
    INNER_THOUGHTS_HEADER, INNER_THOUGHTS_BODY,
    CHAT_GAP_NOTICE, CHAT_GAP_LONG_HINT, CHAT_GAP_CURRENT_TIME,
    CHAT_HOLIDAY_CONTEXT,
    MEMORY_RECALL_HEADER, MEMORY_RESULTS_HEADER,
    PERSONA_HEADER, INNER_THOUGHTS_DYNAMIC,
    RECENT_HISTORY_INTRO, NO_RECENT_HISTORY,
)
# Negative-intent prompts/scanner 已迁到 ``prompts_directives``（与 ban-topic
# regex 同源——同是"用户负面 / 回避指令"的语义层）。``prompts_memory`` 保留
# fact/persona/reflection/summary 等纯 memory-业务 prompt。
from config.prompts.prompts_directives import (
    get_negative_target_check_prompt,
    scan_negative_keywords,
)
from utils.language_utils import get_global_language
from utils.character_name import validate_character_name
from utils.cloudsave_runtime import (
    MaintenanceModeError,
    ROOT_MODE_NORMAL,
    bootstrap_local_cloudsave_environment,
    is_cloudsave_disabled,
    maintenance_error_payload,
    set_root_mode,
    should_write_root_mode_normal_after_startup,
)
from utils.config_manager import get_config_manager
from utils.storage_location_bootstrap import get_storage_startup_blocking_reason
from pydantic import BaseModel
import re
import asyncio
import logging
import argparse
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable
from utils.frontend_utils import get_timestamp

# 配置日志
from utils.logger_config import setup_logging
logger, log_config = setup_logging(service_name="Memory", log_level=logging.INFO)

from utils.time_format import format_elapsed as _format_elapsed


class HistoryRequest(BaseModel):
    input_history: str


class ContinueStorageStartupRequest(BaseModel):
    reason: str = ""

app = FastAPI()
_STORAGE_LIMITED_MODE_ALLOWED_PATHS = {
    "/health",
    "/shutdown",
    "/internal/storage/startup/continue",
    "/internal/storage/startup/block",
}


@app.middleware("http")
async def storage_limited_mode_guard(request: Request, call_next):
    if _memory_runtime_init_completed and not _memory_storage_blocked_after_init:
        return await call_next(request)

    if request.url.path in _STORAGE_LIMITED_MODE_ALLOWED_PATHS:
        return await call_next(request)

    blocking_reason = get_storage_startup_blocking_reason(_config_manager)
    if blocking_reason or _memory_storage_blocked_after_init:
        blocking_reason = blocking_reason or "storage_startup_blocked_after_init"
        logger.info(
            "[Memory] limited-mode blocks request path=%s reason=%s",
            request.url.path,
            blocking_reason,
        )
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error_code": "storage_startup_blocked",
                "blocking_reason": blocking_reason,
                "limited_mode": True,
                "error": "Memory server 正处于存储受限启动状态，请等待存储位置选择、迁移或恢复完成。",
            },
        )
    runtime_blocking_reason = "runtime_initializing"
    logger.info(
        "[Memory] limited-mode blocks request path=%s reason=%s",
        request.url.path,
        runtime_blocking_reason,
    )
    return JSONResponse(
        status_code=409,
        content={
            "ok": False,
            "error_code": "storage_startup_blocked",
            "blocking_reason": runtime_blocking_reason,
            "limited_mode": True,
            "error": "Memory server 正处于存储受限启动状态，请等待存储位置选择、迁移或恢复完成。",
        },
    )


@app.exception_handler(MaintenanceModeError)
async def handle_maintenance_mode_error(_request, exc: MaintenanceModeError):
    return JSONResponse(status_code=409, content=maintenance_error_payload(exc))


# ── 健康检查 / 指纹端点 ──────────────────────────────────────────
@app.get("/health")
async def health():
    """返回带 N.E.K.O 签名的健康响应，供 launcher/前端识别，
    以区分当前服务与随机占用该端口的其他进程。"""
    from utils.port_utils import build_health_response
    from config import INSTANCE_ID
    return build_health_response("memory", instance_id=INSTANCE_ID)


def validate_lanlan_name(name: str) -> str:
    result = validate_character_name(name, allow_dots=True, max_length=50)
    if result.code in {"empty", "too_long_length"}:
        raise HTTPException(status_code=400, detail="Invalid lanlan_name length")
    if result.code is not None:
        raise HTTPException(status_code=400, detail="Invalid characters in lanlan_name")
    return result.normalized

# 所有依赖 cloudsave 目录结构的初始化都推迟到 startup 钩子（见 startup_event_handler）：
#   1. bootstrap_local_cloudsave_environment 在磁盘满/只读 FS 等场景会 raise OSError，
#      裸调会让 module import 阶段就崩，FastAPI 根本起不来；
#   2. bootstrap 内部的 import_legacy_runtime_root_if_needed 可能把 legacy 扁平布局的
#      memory/{type}_{name}.ext 文件带进 target root，必须在 migrate_to_character_dirs
#      之前跑（不然 legacy 数据留在扁平布局、components 只认 per-character 布局，数据不可达）；
#   3. 因此 bootstrap → migrate → 组件实例化 三步必须保持顺序且都放在 startup 里。
# Components 先声明为 None，startup hook 赋值。FastAPI 在 startup 钩子 await 完成后
# 才开始接请求，所以 route handler 不会看到 None。
_config_manager = get_config_manager()

recent_history_manager: CompressedRecentHistoryManager | None = None
settings_manager: ImportantSettingsManager | None = None
time_manager: TimeIndexedMemory | None = None
fact_store: FactStore | None = None
persona_manager: PersonaManager | None = None
reflection_engine: ReflectionEngine | None = None
cursor_store: CursorStore | None = None
outbox: Outbox | None = None
# memory-evidence-rfc §3.3 基础设施：EventLog + Reconciler 单例。
# 初始化时机同 persona_manager 等——startup hook 里建，reload 时重建。
event_log: EventLog | None = None
reconciler: Reconciler | None = None

# memory-enhancements P2: vector embedding warmup + backfill worker.
# Lazily constructed in startup hook; held at module scope so
# /process / /renew handlers can call notify_first_process() to
# unblock the warmup wait early. None when vectors are disabled or
# the worker bootstrap raised.
embedding_warmup_worker = None
# memory-enhancements P2: fact vector dedup resolver. Shares the
# FactStore with the embedding worker (worker enqueues candidates,
# the idle-maintenance loop resolves them). None when bootstrap
# fails or the embedding service is permanently disabled.
fact_dedup_resolver = None

# 用于保护重新加载操作的锁
_reload_lock = asyncio.Lock()
_deferred_time_managers: list[TimeIndexedMemory] = []
_memory_runtime_init_lock = asyncio.Lock()
_memory_runtime_init_completed = False
_memory_storage_blocked_after_init = False
_memory_background_tasks_started = False


def _defer_time_manager_cleanup(manager: TimeIndexedMemory | None) -> None:
    """将旧的 TimeIndexedMemory 延迟到进程关闭时再清理，避免切换窗口内并发请求触发已释放句柄。"""
    if manager is None:
        return
    if any(existing is manager for existing in _deferred_time_managers):
        return
    _deferred_time_managers.append(manager)
    logger.info("[MemoryServer] 旧的 TimeIndexedMemory 已加入延迟清理队列")

async def reload_memory_components():
    """重新加载记忆组件配置（用于新角色创建后）

    使用锁保护重新加载操作，确保原子性交换，避免竞态条件。
    先创建所有新实例，然后原子性地交换引用。

    注意：reload 期间旧 cursor_store 已启动的 async 任务可能与新实例并发
    读写同一份 cursors.json。整个架构假设"per-character 单写者"，重载是
    管理员操作（角色新增），不会与后台 rebuttal_loop 高频冲突；
    atomic_write_json 保证单次写原子，极端 last-writer-wins 场景下最多
    损失一次 cursor 推进——下一轮 tick 即恢复。
    """
    global recent_history_manager, settings_manager, time_manager, fact_store, persona_manager, reflection_engine, cursor_store, outbox, event_log, reconciler, fact_dedup_resolver
    async with _reload_lock:
        logger.info("[MemoryServer] 开始重新加载记忆组件配置...")
        old_time_manager = time_manager
        try:
            # 先创建所有新实例
            new_recent = CompressedRecentHistoryManager()
            new_settings = ImportantSettingsManager()
            new_time = TimeIndexedMemory(new_recent)
            new_facts = FactStore(time_indexed_memory=new_time)
            # EventLog 复用（per-character lock dict 没有必要跨 reload 丢弃），
            # 但每次 reload 重建 Reconciler 以便 handlers 指向新 manager 实例。
            new_event_log = event_log if event_log is not None else EventLog()
            new_persona = PersonaManager(event_log=new_event_log)
            new_reflection = ReflectionEngine(new_facts, new_persona, event_log=new_event_log)
            new_cursor_store = CursorStore()
            new_outbox = Outbox()
            new_reconciler = Reconciler(new_event_log)
            _register_evidence_handlers(new_reconciler, new_persona, new_reflection)
            # P2 step 2: rebind the existing fact_dedup_resolver to the
            # NEW FactStore in place rather than constructing a new
            # resolver. Going via rebind_fact_store preserves the
            # per-character ``_alocks`` dict, so a mid-reload
            # ``aresolve`` still in flight on the old instance and a
            # fresh ``aenqueue_candidates`` arriving on the new
            # instance serialise on the same asyncio.Lock (CodeRabbit
            # PR-956 Major; Codex PR-957 P2). Falls back to fresh
            # construction only if there was no prior resolver
            # (extremely cold-path during reload — startup never ran).
            try:
                from memory.fact_dedup import FactDedupResolver
                if fact_dedup_resolver is not None:
                    fact_dedup_resolver.rebind_fact_store(new_facts)
                    new_fact_dedup_resolver = fact_dedup_resolver
                else:
                    new_fact_dedup_resolver = FactDedupResolver(new_facts)
            except Exception as e:
                logger.warning(f"[MemoryServer] reload: fact_dedup_resolver 重建失败: {e}")
                new_fact_dedup_resolver = None

            # 然后原子性地交换引用
            recent_history_manager = new_recent
            settings_manager = new_settings
            time_manager = new_time
            fact_store = new_facts
            persona_manager = new_persona
            reflection_engine = new_reflection
            cursor_store = new_cursor_store
            outbox = new_outbox
            event_log = new_event_log
            reconciler = new_reconciler
            fact_dedup_resolver = new_fact_dedup_resolver

            if old_time_manager is not None and old_time_manager is not new_time:
                _defer_time_manager_cleanup(old_time_manager)
            
            logger.info("[MemoryServer] ✅ 记忆组件配置重新加载完成")
            return True
        except Exception as e:
            logger.error(f"[MemoryServer] ❌ 重新加载记忆组件配置失败: {e}", exc_info=True)
            return False


@app.post("/release_character/{lanlan_name}")
async def release_character_resources(lanlan_name: str):
    """在角色重命名/删除前主动释放对应 SQLite 句柄。"""
    try:
        lanlan_name = validate_lanlan_name(lanlan_name)
    except HTTPException as exc:
        logger.warning("[MemoryServer] 拒绝释放非法角色名的 SQLite 引擎: %s", lanlan_name)
        return JSONResponse(
            {"status": "error", "character_name": lanlan_name, "message": str(exc.detail)},
            status_code=exc.status_code,
        )

    async with _reload_lock:
        try:
            time_manager.dispose_engine(lanlan_name)
            logger.info("[MemoryServer] 已主动释放角色 %s 的 SQLite 引擎", lanlan_name)
            return {"status": "success", "character_name": lanlan_name}
        except Exception as exc:
            logger.warning("[MemoryServer] 释放角色 %s 的 SQLite 引擎失败: %s", lanlan_name, exc)
            return JSONResponse(
                {"status": "error", "character_name": lanlan_name, "message": str(exc)},
                status_code=500,
            )

# 全局变量用于控制服务器关闭
shutdown_event = asyncio.Event()
# 全局变量控制是否响应退出请求
enable_shutdown = False
# 全局变量用于管理correction任务
correction_tasks = {}  # {lanlan_name: asyncio.Task}
correction_cancel_flags = {}  # {lanlan_name: asyncio.Event}
# Phase C: 防 spawn 竞态——/process /renew /settle / IdleMaint 都共用 maybe_spawn_review，
# 多入口同时进 gate 检查会有 in-flight check → spawn 之间的 await 窗口；用 per-name lock
# 串行化 gate+spawn 这一段，确保同名角色至多一个 review 在跑。
_review_spawn_locks: dict[str, asyncio.Lock] = {}
# 每角色结算锁：首轮摘要期间阻塞 /new_dialog，确保热切换后读到最新数据
_settle_locks: dict[str, asyncio.Lock] = {}
# 强引用注册表：防止 fire-and-forget task 被 GC
_BACKGROUND_TASKS: set[asyncio.Task] = set()

# /new_dialog QPS 观测：每角色累计调用次数，由 _periodic_new_dialog_qps_log_loop
# 每 NEW_DIALOG_QPS_FLUSH_INTERVAL 秒打一行 INFO 日志后清零。用于 A 之后观测
# proactive_chat 路径是否成为 memory_server 真正的负载来源；如不是，则不必再
# 上 main_server 端缓存（C+ 方案）。
_new_dialog_qps_counter: dict[str, int] = {}
NEW_DIALOG_QPS_FLUSH_INTERVAL = 60

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
    """启动时从磁盘加载维护状态。"""
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
    """将维护状态持久化到磁盘。"""
    from utils.file_utils import atomic_write_json_async
    try:
        await atomic_write_json_async(_maint_state_path(), _maint_state,
                                      indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[IdleMaint] 维护状态保存失败: {e}")


def _is_review_clean(lanlan_name: str) -> bool:
    """检查角色是否处于 review_clean 状态（已 review 且无新对话）。"""
    return _maint_state.get(lanlan_name, {}).get('review_clean', False)


async def _aclear_review_clean(lanlan_name: str) -> None:
    """新 human 消息到达时清除 review_clean 标记。"""
    state = _maint_state.get(lanlan_name, {})
    if state.get('review_clean'):
        state['review_clean'] = False
        await _asave_maint_state()


def _has_human_messages(messages) -> bool:
    """检查消息列表中是否包含用户（human）消息。"""
    for m in messages:
        if getattr(m, 'type', '') == 'human':
            return True
    return False


async def _ais_review_enabled() -> bool:
    """检查配置中 correction/review 是否启用（走异步 IO）。"""
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
    """检查"强力记忆"是否启用——controls evidence-RFC 引入的全部新 LLM 路径。

    关闭时只保留 RFC 之前的基础流水线（Stage-1 fact 抽取 / reflection synthesize
    / recent compress+review / recall reranker / 主动搭话回应的 check_feedback）
    + time-driven promote fallback。关后可省 ~40-50% token。

    持久化到 ``core_config.json`` 的 ``powerful_memory_enabled`` 字段，缺失默
    认 True（保兼容）。每次需要时再开 read_json_async，不缓存——和
    ``_ais_review_enabled`` 同款热加载，无需重启即生效。
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


async def _reset_confirmed_at_for_all_characters() -> int:
    """开→关 migration：所有角色的 confirmed reflection 重置 confirmed_at 锚点。

    被 main_routers/memory_router.py 的 update_powerful_memory_config 调用——
    只在 prev=True, new=False 切换时跑。让 time-driven fallback 走完整 14 天
    计时，避免"刚关就立刻批量 promote 旧 confirmed"的体验断层。

    返回真实迁移条目数。**对不可恢复失败（reflection_engine 未初始化 / 角色
    列表加载失败）一律 raise**，让 caller endpoint 区分"真实 0 条"（角色都
    loaded 但没需要重置的）vs"根本没跑"（早期失败）。CodeRabbit PR #997
    feedback：之前两条早期失败路径都返回 0 → endpoint 包装成 ok=true,
    count=0 → 上游 memory_router 误判成功 → 落盘 powerful_memory_enabled=False
    → 旧 confirmed_at 永久漏迁移。
    """
    if reflection_engine is None:
        raise RuntimeError(
            "reflection_engine 未初始化（memory_server limited-mode 或 startup 未完成）"
        )
    character_data = await _config_manager.aload_characters()
    catgirl_names = list(character_data.get('猫娘', {}).keys())
    # 角色列表为空（没配过猫娘）是合法的"0 条要迁移" case，正常返回 0。
    total = 0
    for name in catgirl_names:
        try:
            count = await reflection_engine.areset_confirmed_at_to_now(name)
            total += count
        except Exception as e:
            # 单角色失败不致命——记录后继续。最终 count 反映成功的 N 条。
            logger.warning(f"[Memory] migration {name} 重置失败（其他角色继续）: {e}")
    return total


def _touch_activity() -> None:
    """记录一次对话活动，刷新空闲计时器。"""
    global _last_activity_time
    _last_activity_time = datetime.now()


def _is_idle() -> bool:
    """判断当前是否空闲（距上次活动超过阈值）。"""
    return (datetime.now() - _last_activity_time).total_seconds() >= IDLE_THRESHOLD


def _get_settle_lock(lanlan_name: str) -> asyncio.Lock:
    """获取指定角色的结算锁（懒创建）"""
    if lanlan_name not in _settle_locks:
        _settle_locks[lanlan_name] = asyncio.Lock()
    return _settle_locks[lanlan_name]


def _format_legacy_settings_as_text(settings: dict, lanlan_name: str) -> str:
    """将旧版 settings JSON 转为自然语言格式，替代原始 json.dumps 输出。"""
    if not settings:
        return f"{lanlan_name}记得：（暂无记录）"

    sections = []
    for name, data in settings.items():
        if not isinstance(data, dict) or not data:
            continue
        lines = []
        for key, value in data.items():
            if value is None or value == '' or value == []:
                continue
            if isinstance(value, list):
                value_str = '、'.join(str(v) for v in value)
            elif isinstance(value, dict):
                parts = [f"{k}: {v}" for k, v in value.items() if v is not None and v != '']
                value_str = '、'.join(parts) if parts else str(value)
            else:
                value_str = str(value)
            lines.append(f"- {key}：{value_str}")
        if lines:
            sections.append(f"关于{name}：\n" + "\n".join(lines))

    if not sections:
        return f"{lanlan_name}记得：（暂无记录）"
    return f"{lanlan_name}记得：\n" + "\n".join(sections)


def _spawn_background_task(coro) -> asyncio.Task:
    """Create a background task with strong reference + exception logging."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)

    def _on_done(t: asyncio.Task):
        _BACKGROUND_TASKS.discard(t)
        if not t.cancelled():
            exc = t.exception()
            if exc:
                logger.warning(f"[MemoryServer] 后台任务异常: {exc}")

    task.add_done_callback(_on_done)
    return task


# ── Outbox handler registry + replay (P1.c) ────────────────────────

# op_type → async handler(name: str, payload: dict) -> None. Handler 必须幂等。
OutboxHandler = Callable[[str, dict], Awaitable[None]]
_OUTBOX_HANDLERS: dict[str, OutboxHandler] = {}

# 启动期补跑 fan-out 并发上限：防止 24h 停机后的 outbox 洪水冲击 LLM 后端。
_REPLAY_CONCURRENCY = 2
_replay_semaphore: asyncio.Semaphore | None = None  # 懒构造（event loop-bound）


def register_outbox_handler(op_type: str, handler: OutboxHandler) -> None:
    _OUTBOX_HANDLERS[op_type] = handler


async def _run_outbox_op(name: str, op: dict, sem: asyncio.Semaphore | None = None) -> None:
    """跑单条 outbox op 并在成功后 append_done。失败保持 pending 等下次启动补跑。

    `sem`：startup replay 路径传入共享 Semaphore 限制 LLM fan-out；日常单次
    spawn 路径传 None 即不限流。

    Liveness 兜底（Site 7）：handler 失败时 append_attempt 一行记录失败。
    若同 op_id 累计 attempt 数（含本次） ≥ ``MEMORY_LIVENESS_MAX_ATTEMPTS``
    则 append_done 当 dead-letter 放弃该 op + WARN。否则毒 op（payload 触
    发 handler 永久 raise，例如 LLM safety filter / parse 永久失败）每次重启
    都重跑且永远不出 pending → ``compact`` 永久阻塞 → outbox.ndjson 线性增
    长。``op.get('_attempt_count', 0)`` 来自 ``pending_ops`` scan 时的累计，
    日常 spawn 路径调 _run_outbox_op 时 op 是临时构造的不带这个字段，按 0
    起算（首次失败 → attempt=1，远 < N，正常 pending 等重启重放）。
    """
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    op_id = op.get('op_id')
    op_type = op.get('type')
    payload = op.get('payload') or {}
    from memory.facts import safe_int_field
    prior_attempts = safe_int_field(op, '_attempt_count')
    handler = _OUTBOX_HANDLERS.get(op_type)
    if handler is None:
        logger.warning(f"[Outbox] {name}: 未注册的 op type {op_type}, 跳过 {op_id}")
        return

    # CodeRabbit: 已达 dead-letter 阈值的 op 直接补写 done，不要再跑 handler。
    # 边缘 case：上一轮 ``aappend_attempt`` 成功把 _attempt_count 推到 N，但
    # 紧接着 ``aappend_done`` 写盘失败（IO transient）→ op 留在 pending →
    # 重启 replay 看到 ``_attempt_count=N`` 又进 handler 再失败再尝试 done。
    # 对幂等 handler 只是浪费一次调用；对非幂等 handler（outbox 契约要求幂等
    # 但不保证）就是真重复副作用。进门先短路保证"达阈值后绝不再执行"。
    if prior_attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS:
        logger.warning(
            f"[Outbox] {name}/{op_type}/{op_id}: 进入时已达 dead-letter 阈值 "
            f"({prior_attempts}/{MEMORY_LIVENESS_MAX_ATTEMPTS})，跳过 handler "
            f"直接补写 done。Why: 上一轮 append_done 可能 IO 失败留 pending，"
            f"避免毒 op 重复执行 + 副作用重放。"
        )
        try:
            await outbox.aappend_done(name, op_id)
        except Exception as de:
            logger.warning(
                f"[Outbox] {name}/{op_type}/{op_id}: dead-letter "
                f"append_done 仍失败（保持 pending 等下次重放再补 done）: {de}"
            )
        return

    acquired = False
    if sem is not None:
        await sem.acquire()
        acquired = True
    try:
        try:
            await handler(name, payload)
        except Exception as e:
            try:
                await outbox.aappend_attempt(name, op_id)
                attempt_persisted = True
            except Exception as ae:
                attempt_persisted = False
                logger.warning(
                    f"[Outbox] {name}/{op_type}/{op_id}: append_attempt 失败: {ae}"
                )

            # Codex P1：不能基于"未落盘的 +1"触发 dead-letter。
            # 如果本次 aappend_attempt 失败 + 接着 aappend_done 成功 →
            # 重启后只看到磁盘上 prior_attempts 个 attempt 行 + 1 个 done →
            # op 永久丢失而磁盘记录看起来"只失败了 N-1 次就 done"，违背 "≥ N
            # 次失败才放弃" 的契约。Attempt 没落盘 → 本次失败按 transient 处理
            # （保留 pending，下次重试自然再走一次 attempt），不进 dead-letter
            # 判定。
            if not attempt_persisted:
                logger.warning(
                    f"[Outbox] {name}/{op_type}/{op_id} 执行失败（attempt 持久化"
                    f"失败，按 transient 保留 pending 等下次重放）: {e}"
                )
                return

            total_attempts = prior_attempts + 1
            if total_attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS:
                logger.warning(
                    f"[Outbox] {name}/{op_type}/{op_id}: handler 累计失败 "
                    f"{total_attempts} 次 ≥ {MEMORY_LIVENESS_MAX_ATTEMPTS}，"
                    f"dead-letter 放弃该 op（最近一次失败: {e}）。"
                    f"Why: liveness 兜底，避免毒 payload 让重启 replay 永远卡住 + "
                    f"compact 永久阻塞。"
                )
                try:
                    await outbox.aappend_done(name, op_id)
                except Exception as de:
                    logger.warning(
                        f"[Outbox] {name}/{op_type}/{op_id}: dead-letter "
                        f"append_done 失败: {de}"
                    )
            else:
                logger.warning(
                    f"[Outbox] {name}/{op_type}/{op_id} 执行失败（保持 pending，"
                    f"attempts={total_attempts}/{MEMORY_LIVENESS_MAX_ATTEMPTS}）: {e}"
                )
            return
        try:
            await outbox.aappend_done(name, op_id)
        except Exception as e:
            # append_done 失败不致命：下次启动重放这个 op，handler 幂等。
            logger.warning(f"[Outbox] {name}/{op_type}/{op_id}: append_done 失败: {e}")
    finally:
        if acquired and sem is not None:
            sem.release()


async def _spawn_outbox_post_turn_signals(lanlan_name: str, messages: list) -> asyncio.Task:
    """把 per-turn signals 背景任务登记到 outbox 并 spawn。

    "per-turn signals" = counter bump（给 batch loop 计数）+ 复读嗅探 +
    check_feedback + OFF-mode Stage-1 fallback，见 ``_run_post_turn_signals``。
    登记的 payload 包含 messages_to_dict 序列化后的整轮对话，重启时可重放。
    """
    from utils.llm_client import messages_to_dict

    payload = {'messages': messages_to_dict(messages)}
    try:
        op_id = await outbox.aappend_pending(lanlan_name, OP_POST_TURN_SIGNALS, payload)
    except Exception as e:
        # Outbox 写失败不能阻塞主流程，降级为一次性任务（与重构前行为一致）
        logger.warning(
            f"[Outbox] {lanlan_name}: append_pending 失败，降级为内存任务: "
            f"{type(e).__name__}: {e}"
        )
        return _spawn_background_task(
            _run_post_turn_signals(messages, lanlan_name)
        )
    op = {'op_id': op_id, 'type': OP_POST_TURN_SIGNALS, 'payload': payload}
    return _spawn_background_task(_run_outbox_op(lanlan_name, op))


async def _replay_pending_outbox() -> list[asyncio.Task]:
    """启动期扫描 outbox，补跑未完成 op。返回 spawn 出的 Task 列表。

    返回值方便调用方（或测试）await 所有任务跑完，而不是靠
    `_BACKGROUND_TASKS` 快照 + `asyncio.sleep(0)` 这种弱保证等法。

    扫描范围 = 当前 config 的角色名 ∪ memory_dir 下有 `outbox.ndjson` 的
    子目录。仅扫 config 会漏掉"曾经在用、后来被移出 config 但仍有 pending
    op 的角色"，导致那些 op 永远不会被补跑。
    """
    global _replay_semaphore
    spawned: list[asyncio.Task] = []
    names: set[str] = set()
    try:
        character_data = await _config_manager.aload_characters()
        names.update(character_data.get('猫娘', {}).keys())
    except Exception as e:
        logger.warning(f"[Outbox] 启动补跑：加载角色列表失败: {e}")
        # 即便 config 加载失败，仍允许走磁盘扫描兜底——这正是 config
        # 变化后仍需保证 crash-recovery 的场景。

    try:
        memory_dir = _config_manager.memory_dir
        if memory_dir and os.path.isdir(memory_dir):
            for entry in os.listdir(memory_dir):
                candidate = os.path.join(memory_dir, entry, 'outbox.ndjson')
                if os.path.isfile(candidate):
                    names.add(entry)
    except Exception as e:
        logger.warning(f"[Outbox] 启动补跑：扫描 memory_dir 失败: {e}")

    if not names:
        return spawned

    # Semaphore 在 event loop 里构造（不能在模块级构造）
    if _replay_semaphore is None:
        _replay_semaphore = asyncio.Semaphore(_REPLAY_CONCURRENCY)

    for name in sorted(names):
        try:
            pending = await outbox.apending_ops(name)
        except Exception as e:
            logger.warning(f"[Outbox] {name}: 读取 pending ops 失败: {e}")
            continue
        if not pending:
            # 机会性 compact：文件可能累积了很多 done 行。失败不影响主流程
            # （compact 仅是空间回收），debug 级别记录便于观测。
            try:
                dropped = await outbox.amaybe_compact(name)
                if dropped:
                    logger.info(f"[Outbox] {name}: compact 丢弃 {dropped} 行")
            except Exception as e:
                logger.debug(f"[Outbox] {name}: 机会性 compact 失败（可忽略）: {e}")
            continue
        logger.info(f"[Outbox] {name}: 补跑 {len(pending)} 条未完成 op")
        for op in pending:
            spawned.append(
                _spawn_background_task(_run_outbox_op(name, op, _replay_semaphore))
            )
    return spawned

@app.post("/shutdown")
async def shutdown_memory_server():
    """接收来自main_server的关闭信号"""
    global enable_shutdown
    if not enable_shutdown:
        logger.warning("收到关闭信号，但当前模式不允许响应退出请求")
        return {"status": "shutdown_disabled", "message": "当前模式不允许响应退出请求"}
    
    try:
        logger.info("收到来自main_server的关闭信号")
        shutdown_event.set()
        return {"status": "shutdown_signal_received"}
    except Exception as e:
        logger.error(f"处理关闭信号时出错: {e}")
        return {"status": "error", "message": str(e)}

REBUTTAL_CHECK_INTERVAL = 180  # 3 分钟
REBUTTAL_FIRST_RUN_LOOKBACK_HOURS = 1  # 首次启动 / 时钟回拨兜底回扫窗口
# Drain pattern: 一次最多处理 N 条 user 消息，避免高频用户场景下 prompt 爆炸。
# 多余的留到下一轮（cursor 推进到第 N 条的 timestamp，不丢消息）。
REBUTTAL_DRAIN_BATCH_LIMIT = 20
# 读 SQL 时的硬上限——bound memory，防止 1h fallback 把整张表拉进来。
# 200 行通常包含 50-100 条 user 消息，足以喂多次 drain。
REBUTTAL_SQL_ROW_LIMIT = 200


def _coerce_db_ts(ts) -> datetime | None:
    """归一化 SQL 行里的 timestamp 字段为 **naive** datetime。

    SQLAlchemy + SQLite 在某些 driver 配置下返回字符串而非 datetime；与
    memory/timeindex.py:get_last_conversation_time 同款归一化。返回 None
    表示无法解析（caller 应跳过此行而不是把 None 写进 cursor）。

    若解析出 TZ-aware datetime（import / migration 路径写入 "...+00:00"
    之类），强制 `replace(tzinfo=None)` 转 naive——本文件所有 cursor /
    比较都按 naive 语义工作（last_b_check_ts / last_a_msg_ts / facts.json
    `created_at` 全是 naive `datetime.now().isoformat()`），aware 跟 naive
    比较会抛 TypeError 让 caller 永久哑火（Codex P1+P2 round-7/8 on PR
    #1408 双侧 case）。
    """
    if isinstance(ts, datetime):
        result = ts
    elif isinstance(ts, str):
        try:
            result = datetime.fromisoformat(ts)
        except ValueError:
            try:
                result = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                return None
    else:
        return None
    if result.tzinfo is not None:
        result = result.replace(tzinfo=None)
    return result


def _extract_user_messages_with_ts_from_rows(rows: list) -> list[tuple[str, datetime]]:
    """从 time_indexed SQL 查询结果中提取 (用户消息文本, timestamp) 元组。

    rows: [(timestamp, session_id, message_json), ...] (ASC ordered by ts)
    message_json 是 langchain SQLChatMessageHistory 存储的 JSON 字符串。
    content 可能是 str 或 list[{type, text}]。

    返回的 list 按 ts ASC 排序，caller 可基于 last item 的 ts 推 cursor。
    timestamp 通过 _coerce_db_ts 归一化为 datetime 对象（SQL driver 可能
    返回 str）；解析失败的行会被跳过。
    """
    out: list[tuple[str, datetime]] = []
    for ts_raw, _, msg_json in rows:
        ts = _coerce_db_ts(ts_raw)
        if ts is None:
            continue
        try:
            msg = json.loads(msg_json) if isinstance(msg_json, str) else msg_json
            if isinstance(msg, dict) and msg.get('type') == 'human':
                content = msg.get('data', {}).get('content', '')
                if isinstance(content, str):
                    if content.strip():
                        out.append((content, ts))
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get('type') == 'text':
                            text_val = part.get('text', '')
                            if text_val.strip():
                                out.append((text_val, ts))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def _extract_user_messages_from_rows(rows: list) -> list[str]:
    """从 time_indexed SQL 查询结果中提取用户消息文本（legacy text-only 视图）。

    rows: [(timestamp, session_id, message_json), ...]
    """
    user_msgs = []
    for _, _, msg_json in rows:
        try:
            msg = json.loads(msg_json) if isinstance(msg_json, str) else msg_json
            if isinstance(msg, dict) and msg.get('type') == 'human':
                content = msg.get('data', {}).get('content', '')
                if isinstance(content, str):
                    if content.strip():
                        user_msgs.append(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get('type') == 'text':
                            text = part.get('text', '')
                            if text.strip():
                                user_msgs.append(text)
        except (json.JSONDecodeError, TypeError):
            continue
    return user_msgs


def _extract_role_tagged_messages_from_rows(rows: list) -> list[dict]:
    """Path B 用的全消息提取——保留 user + ai 两种 type，输出 message_dict
    list 直接喂 ``convert_to_messages``。

    跟 ``_extract_user_messages_from_rows`` 的区别：
    - 收 type ∈ {'human', 'ai'} 两种（不再仅 human）
    - 返回 [{'type': 'human'|'ai', 'data': {'content': str}}, ...] 而不是
      纯 str list，让下游 ``convert_to_messages`` 还原成 HumanMessage/AIMessage
      让 ``FactStore._format_conversation`` 渲染时按 type → name_mapping 出
      "{MASTER_NAME} | xxx" / "{LANLAN_NAME} | xxx" 形式，path B prompt 据此
      判每条 fact 的 source 归属（user_observation / ai_disclosure）

    PR #1399 的教训：这里返回 list[dict] 让 caller 拼 message_dicts 后用
    ``convert_to_messages(message_dicts)`` 直接转——**不要** ``json.dumps``
    包一层（convert_to_messages 只接 list，str 会被静默吞成 []）。
    """
    out: list[dict] = []
    for _, _, msg_json in rows:
        try:
            msg = json.loads(msg_json) if isinstance(msg_json, str) else msg_json
            if not isinstance(msg, dict):
                continue
            msg_type = msg.get('type')
            if msg_type not in ('human', 'ai'):
                continue
            content = msg.get('data', {}).get('content', '')
            # content 归一化：内部可能是 str 或 [{type:'text', text:'...'}, ...]
            # 后者拼回单个 str（path B prompt 不需要细粒度 part 结构，
            # FactStore._format_conversation 把 list content 拼成 ''.join 也是
            # 同样语义）。
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = [
                    p.get('text', '')
                    for p in content
                    if isinstance(p, dict) and p.get('type') == 'text'
                ]
                text = ''.join(parts)
            else:
                continue
            if not text.strip():
                continue
            out.append({'type': msg_type, 'data': {'content': text}})
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def _trim_to_user_msg_bracket(message_dicts: list[dict]) -> list[dict]:
    """只保留首条 human msg 到末条 human msg 之间（含两端）的消息。

    Product thesis 防廉价层污染：AI 在首条 user msg **之前**的内容是 user
    还没印证的 proactive 试探，AI 在末条 user msg **之后**的内容是 user
    还没回应过的独白——两段都是廉价层，不该当 fact 沉淀。只有夹在两条
    user msg 中间的 AI 内容才意味着 "user 看到了 / 认可了这段对话上下
    文"，才有资格被 path B 拣回当 ai_disclosure fact。

    完全无 human msg → 返 []（caller 视作 AI-only 窗口跳过）。
    只有一条 human msg → 返该条（bracket 退化为单点，仍合法：那条本身
    就是 user 发声，path B 可借 known_pool 看相邻 AI 上下文）。
    """
    human_indices = [
        i for i, m in enumerate(message_dicts) if m.get('type') == 'human'
    ]
    if not human_indices:
        return []
    return message_dicts[human_indices[0]:human_indices[-1] + 1]


async def _resolve_rebuttal_start_time(name: str, now: datetime):
    """决定 rebuttal_loop 本轮查询的起始时间。

    优先级：
      1. 持久化的 CURSOR_REBUTTAL_CHECKED_UNTIL
      2. 兜底回扫窗口（首次启动 / cursor 文件缺失）
      3. 时钟回拨保护：cursor > now 视为脏数据，走兜底并**立刻重写**游标

    rollback 分支立即覆写游标的原因：若只在主循环 success branch 才覆写，
    遇上 LLM 持续失败 + 时钟回拨，主循环每轮都会命中 fallback 并告警，
    但游标永远停留在未来时间，无法自愈；这里直接写回 fallback 打破死循环。

    写 fallback 而非 now：若写 now，本 tick 的 LLM 调用若失败，
    窗口 `[fallback, now]` 的消息会因下轮 cursor 已推进到 now 而被跳过；
    写 fallback 则保持重试语义——主循环 success branch 再把 cursor 推进到 now。

    独立成函数便于单测验证。
    """
    cursor = await cursor_store.aget_cursor(name, CURSOR_REBUTTAL_CHECKED_UNTIL)
    fallback = now - timedelta(hours=REBUTTAL_FIRST_RUN_LOOKBACK_HOURS)
    if cursor is None:
        # 首次启动：把 fallback 落盘锚定。否则 LLM 连续失败时，下轮
        # cursor 仍为 None，新的 fallback 会基于新的 now 重新计算并前移
        # （滑动 1h 窗口），首轮窗口最早段消息会被永久跳过。
        try:
            await cursor_store.aset_cursor(
                name, CURSOR_REBUTTAL_CHECKED_UNTIL, fallback,
            )
        except Exception as e:
            logger.debug(f"[Rebuttal] {name}: 首次 fallback 锚定写入失败（将在下轮重试）: {e}")
        return fallback
    if cursor > now:
        logger.warning(
            f"[Rebuttal] {name}: 游标 {cursor.isoformat()} 晚于当前时间 "
            f"{now.isoformat()}（时钟回拨?），回退到 {fallback.isoformat()} 并覆写"
        )
        # 自愈：把游标拉回 fallback（而非 now），使后续 tick 不再命中 rollback
        # 分支，同时保留本轮窗口 [fallback, now] 的重试能力（若 LLM 失败）
        try:
            await cursor_store.aset_cursor(
                name, CURSOR_REBUTTAL_CHECKED_UNTIL, fallback,
            )
        except Exception as e:
            logger.debug(f"[Rebuttal] {name}: rollback 自愈写入失败（将在下轮重试）: {e}")
        return fallback
    return cursor


_rebuttal_failures: dict[str, dict[str, int]] = {}
"""Per-character rebuttal LLM 失败计数：``{name: {cursor_iso: count}}``。

In-memory only（cursor 本身落盘到 cursors.json，但 counter 重启清零）。
Why in-memory: 重启后再试 ``MEMORY_LIVENESS_MAX_ATTEMPTS`` 次再 dead-letter，
避免内存 counter 错把短暂 transient 失败永久放弃；user-visible 代价 = 重启
后多卡 N × REBUTTAL_CHECK_INTERVAL 一段时间，可接受。

Liveness 兜底原因：``check_feedback_for_confirmed`` 返 None 时原代码直接
``return`` 不动 cursor → 下轮重读相同 [cursor, now] 窗口含同样的毒 user
msg → 仍失败 → 永久卡死 rebuttal 链路（毒窗口让 user 反驳信号永远进不来
evidence loop）。"""


def _rebuttal_bump_failure(name: str, cursor_key: str) -> int:
    """Bump 失败计数，返回当前累计次数。Caller 自行判 ≥ MEMORY_LIVENESS_MAX_ATTEMPTS。"""
    fails = _rebuttal_failures.setdefault(name, {})
    fails[cursor_key] = int(fails.get(cursor_key, 0) or 0) + 1
    return fails[cursor_key]


def _rebuttal_clear_failures(name: str) -> None:
    """Cursor 推进（成功）或 dead-letter 强推后清零 counter。"""
    _rebuttal_failures.pop(name, None)


async def _periodic_rebuttal_loop():
    """每 5 分钟检查 confirmed reflections 是否被近期对话反驳。

    通过 time_indexed SQL 查询上次检查之后的所有新对话消息，
    确保不遗漏任何未消费的用户回复。

    游标持久化（P0 修复）：`CURSOR_REBUTTAL_CHECKED_UNTIL` 写入 cursors.json，
    关机→重启后从磁盘读取，消灭"默认只回扫 1 小时导致关机期间反驳丢失"的缺陷。

    首轮启动延迟 _INITIAL_DELAY_REBUTTAL 秒（与其他后台循环错峰）。
    """
    await asyncio.sleep(_INITIAL_DELAY_REBUTTAL)
    while True:
        # 强力记忆关 → rebuttal LLM 整段停（这是 evidence-RFC 引入的最贵
        # 周期 LLM 之一，每 180s 一次开 thinking 跑 drain）。关闭后用户的
        # 反驳信号经由 per-turn check_feedback (主动搭话回应) 仍能进 evidence。
        #
        # 关态推进 cursor 到 now：否则重新开启时 _resolve_rebuttal_start_time
        # 拿到的是关闭前的旧 cursor，下一轮会把关闭期间积攒的所有 user msg
        # 整段补处理（极大 prompt + 大量 LLM 调用）。"关时不跑" 应等价于
        # "关时已 noop 处理完"——重开后从 now 重新累积，不回补。
        if not await _ais_powerful_memory_enabled():
            try:
                character_data = await _config_manager.aload_characters()
                catgirl_names = list(character_data.get('猫娘', {}).keys())
                cursor_now = datetime.now()
                for name in catgirl_names:
                    try:
                        await cursor_store.aset_cursor(
                            name, CURSOR_REBUTTAL_CHECKED_UNTIL, cursor_now,
                        )
                    except Exception as cursor_e:
                        # 单角色 cursor 推进失败不致命——下一轮再试，最坏
                        # 是该角色重开时多扫一段窗口，不影响其他角色。
                        logger.debug(
                            f"[Rebuttal] {name}: 关态 cursor 推进失败: {cursor_e}"
                        )
            except Exception as e:
                logger.debug(f"[Rebuttal] 关态 cursor 推进 batch 失败: {e}")
            await asyncio.sleep(REBUTTAL_CHECK_INTERVAL)
            continue

        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[Rebuttal] 加载角色列表失败: {e}")
            await asyncio.sleep(REBUTTAL_CHECK_INTERVAL)
            continue

        now = datetime.now()

        async def _check_one_rebuttal(name: str):
            """单个 catgirl 的反驳检查。各角色互相独立，外层 gather 并行。
            内部对 feedbacks 仍串行 areject_promotion（同 reflection 不能并发处理）。

            Drain 模式：每轮最多处理 ``REBUTTAL_DRAIN_BATCH_LIMIT`` (=20) 条
            user 消息，cursor 推进到第 N 条的 timestamp。背压期（高频对话用户
            或 1h fallback）下分多个 tick 排干，每次 LLM prompt 大小受控；
            消息不丢（cursor 严格按已处理位置推进）。
            """
            try:
                confirmed = await reflection_engine.aget_confirmed_reflections(name)
                if not confirmed:
                    # 无 confirmed 时仍需推进游标：否则等到有新 confirmed reflection
                    # 出现后，首轮会把 cursor-now 之间积攒的全部用户消息喂给
                    # check_feedback_for_confirmed，容易把无关历史回复误判为反驳。
                    await cursor_store.aset_cursor(
                        name, CURSOR_REBUTTAL_CHECKED_UNTIL, now,
                    )
                    _rebuttal_clear_failures(name)
                    return

                start_time = await _resolve_rebuttal_start_time(name, now)
                rows = await time_manager.aretrieve_original_by_timeframe(
                    name, start_time, now,
                    limit_rows=REBUTTAL_SQL_ROW_LIMIT,
                )
                if not rows:
                    await cursor_store.aset_cursor(
                        name, CURSOR_REBUTTAL_CHECKED_UNTIL, now,
                    )
                    _rebuttal_clear_failures(name)
                    return

                # 提取 (msg, ts) 元组（ASC by ts；ts 已归一化为 datetime）
                user_msgs_with_ts = _extract_user_messages_with_ts_from_rows(rows)
                if not user_msgs_with_ts:
                    # 窗口里只有 AI 消息或无 user 内容 → 推进 cursor 到 SQL 截
                    # 取的最后一行 ts（如果命中 LIMIT 还有更多行）或 now（清空了）
                    last_row_ts = _coerce_db_ts(rows[-1][0])
                    if len(rows) >= REBUTTAL_SQL_ROW_LIMIT and last_row_ts is not None:
                        await cursor_store.aset_cursor(
                            name, CURSOR_REBUTTAL_CHECKED_UNTIL, last_row_ts,
                        )
                    else:
                        # 既然没命中 LIMIT，窗口已经全部扫过；直接推到 now。
                        # last_row_ts 解析失败也走这条（保守 fallback）。
                        await cursor_store.aset_cursor(
                            name, CURSOR_REBUTTAL_CHECKED_UNTIL, now,
                        )
                    _rebuttal_clear_failures(name)
                    return

                # Drain 取前 N 条 user msg。然后扩展 batch 把和 batch 末位
                # 共享同 ts 的后续 user msg 也吸收进来——因为 SQL 用
                # ``timestamp BETWEEN`` (inclusive)，cursor 推进到 batch[-1].ts
                # 后下一轮会把同 ts 的行原样重读。如果不扩展，多条同 ts 的
                # user msg 在 batch 边界被切，会出现"只处理一部分，剩下的下
                # 轮当 batch 边界又被切"的死循环（``store_conversation`` 一
                # 批 message 共享 timestamp，所以同 ts 多条很常见）。
                # 扩展受 SQL 行 LIMIT 兜底，不会无界增长。
                batch = user_msgs_with_ts[:REBUTTAL_DRAIN_BATCH_LIMIT]
                if len(user_msgs_with_ts) > len(batch):
                    boundary_ts = batch[-1][1]
                    extend_idx = len(batch)
                    while (
                        extend_idx < len(user_msgs_with_ts)
                        and user_msgs_with_ts[extend_idx][1] == boundary_ts
                    ):
                        extend_idx += 1
                    if extend_idx > len(batch):
                        batch = user_msgs_with_ts[:extend_idx]
                user_msgs = [m for m, _ in batch]

                # 复用 check_feedback 判断反驳
                feedbacks = await reflection_engine.check_feedback_for_confirmed(
                    name, confirmed, user_msgs,
                )
                if feedbacks is None:
                    # LLM 调用失败 → 不推进游标，下次重试这批消息。
                    # Liveness 兜底：同一 cursor 反复失败 ≥
                    # MEMORY_LIVENESS_MAX_ATTEMPTS 时强推 cursor 到 now 放弃这段
                    # 窗口（dead-letter），避免毒 user msg 让 rebuttal 链路永久
                    # 卡死。cursor 落盘到 cursors.json，stuck cursor 重启都不
                    # 复活，比 in-memory 的 signal extraction cursor 更顽固。
                    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
                    cursor_key = (
                        start_time.isoformat(timespec='microseconds')
                        if start_time else 'cold'
                    )
                    attempts = _rebuttal_bump_failure(name, cursor_key)
                    if attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS:
                        logger.warning(
                            f"[Rebuttal] {name}: 反驳检查在 cursor {cursor_key!r} "
                            f"累计失败 {attempts} 次 ≥ {MEMORY_LIVENESS_MAX_ATTEMPTS}，"
                            f"强推 cursor 到 {now.isoformat(timespec='seconds')} "
                            f"放弃该窗口（dead-letter）。Why: 毒窗口 liveness 兜底。"
                        )
                        await cursor_store.aset_cursor(
                            name, CURSOR_REBUTTAL_CHECKED_UNTIL, now,
                        )
                        _rebuttal_clear_failures(name)
                    else:
                        logger.warning(
                            f"[Rebuttal] {name}: 反驳检查失败，保留游标待重试 "
                            f"({attempts}/{MEMORY_LIVENESS_MAX_ATTEMPTS})"
                        )
                    return

                # 成功才推进游标并持久化。Drain 推进规则：
                # - 还有 user msgs 在本次 read 内未处理（batch 已扩展含所有
                #   同 ts，所以剩余的 ts 一定 > batch[-1].ts）
                #   → cursor 推到第一个未处理 user msg 的 ts（next read 的
                #     BETWEEN 起点，包含该行不会重处理因为它本来就 unprocessed）
                # - SQL 命中 LIMIT 但 user msgs 全处理 → cursor 推到最后一行 ts
                #   (next read 会重读 same-ts cluster 但 LLM 调用幂等无害)
                # - 全干净 → cursor 推到 now
                more_user_msgs = len(user_msgs_with_ts) > len(batch)
                hit_sql_limit = len(rows) >= REBUTTAL_SQL_ROW_LIMIT
                if more_user_msgs:
                    new_cursor = user_msgs_with_ts[len(batch)][1]
                    logger.info(
                        f"[Rebuttal] {name}: drain 处理 {len(batch)} 条，"
                        f"cursor 推进到下一未处理 user msg ts，下轮续"
                    )
                elif hit_sql_limit:
                    last_row_ts = _coerce_db_ts(rows[-1][0])
                    new_cursor = last_row_ts if last_row_ts is not None else now
                    logger.info(
                        f"[Rebuttal] {name}: drain 处理 {len(batch)} 条 user msg，"
                        f"SQL 命中 LIMIT，cursor 推进到最后一行 ts，下轮续"
                    )
                else:
                    new_cursor = now
                await cursor_store.aset_cursor(
                    name, CURSOR_REBUTTAL_CHECKED_UNTIL, new_cursor,
                )
                # Cursor 推进 → 旧 cursor key 永远不会再被命中，清空 counter
                # 避免内存 dict 随 cursor 历史无限增长（对偶 Site 0a/0b）。
                _rebuttal_clear_failures(name)
                for fb in feedbacks:
                    if isinstance(fb, dict) and fb.get('feedback') == 'denied':
                        rid = fb.get('reflection_id')
                        if rid:
                            await reflection_engine.areject_promotion(name, rid)
                            logger.info(f"[Rebuttal] {name}: confirmed 反思被反驳: {rid}")
            except Exception as e:
                logger.debug(f"[Rebuttal] {name}: 处理失败，跳过: {e}")

        if catgirl_names:
            await asyncio.gather(
                *(_check_one_rebuttal(name) for name in catgirl_names),
                return_exceptions=True,
            )

        await asyncio.sleep(REBUTTAL_CHECK_INTERVAL)


AUTO_PROMOTE_CHECK_INTERVAL = 180  # 3 分钟（与 rebuttal 同步，覆盖同样级别的状态变化）

async def _periodic_auto_promote_loop():
    """定期执行 auto_promote_stale：pending→confirmed→promoted 状态迁移。

    PR-3 (RFC §3.9.1)：`aauto_promote_stale` 现在包含两段：
      1. 锁内 pending → confirmed (score driven)
      2. 锁外 confirmed → promoted via `_apromote_with_merge`（LLM 决策
         合并 / 独立晋升 / 拒绝；带节流防 LLM 失败 DOS）

    Per-character 用 asyncio.gather 并行——每个角色内部仍是顺序操作
    （锁串行），但跨角色可以打满。

    首轮启动延迟 _INITIAL_DELAY_AUTO_PROMOTE 秒（与其他后台循环错峰）。
    """
    await asyncio.sleep(_INITIAL_DELAY_AUTO_PROMOTE)
    while True:
        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[AutoPromote] 加载角色列表失败: {e}")
            await asyncio.sleep(AUTO_PROMOTE_CHECK_INTERVAL)
            continue

        powerful = await _ais_powerful_memory_enabled()

        async def _promote_one(name: str):
            try:
                if powerful:
                    # score-driven + merge LLM (current evidence-RFC 路径)
                    transitions = await reflection_engine.aauto_promote_stale(name)
                else:
                    # 强力记忆关：time-driven 直接 aadd_fact，零 LLM
                    transitions = await reflection_engine.aauto_promote_time_driven(name)
                if transitions:
                    logger.info(
                        f"[AutoPromote] {name}: {transitions} 条状态迁移"
                        f"({'score+merge' if powerful else 'time-driven'})"
                    )
            except Exception as e:
                logger.debug(f"[AutoPromote] {name}: 处理失败: {e}")

        if catgirl_names:
            await asyncio.gather(
                *(_promote_one(name) for name in catgirl_names),
                return_exceptions=True,
            )

        await asyncio.sleep(AUTO_PROMOTE_CHECK_INTERVAL)


async def _periodic_idle_maintenance_loop():
    """定期检查系统是否空闲，空闲时自动执行记忆维护任务。

    首次执行延迟 _INITIAL_DELAY_IDLE_MAINT 秒（让 startup 期 cloudsave / outbox
    replay / migration 任务先消化），之后每 IDLE_CHECK_INTERVAL 秒轮询一次。

    每轮为每个角色依次执行：
    1. 历史记录压缩 — 有需要就跑（history > compress_threshold）
    1b. Fact 向量去重 — 有需要就跑（vectors 启用且 pending dedup 队列非空）
    2. Persona 矛盾审视 — 有需要就跑（pending corrections 非空）；不受 recent_memory_auto_review
       开关或 REVIEW_SKIP_HISTORY_LEN 影响：persona corrections 不读 recent history，是独立的
       矛盾消解管线，不应被 review 开关一刀切。
    3. 记忆整理 review — review_clean 则跳过；受 REVIEW_MIN_INTERVAL 最短间隔；
       history < REVIEW_SKIP_HISTORY_LEN 或 review_enabled 关闭则跳过。
    """
    await asyncio.sleep(_INITIAL_DELAY_IDLE_MAINT)
    while True:
        try:
            if not _is_idle():
                continue

            try:
                character_data = await _config_manager.aload_characters()
                catgirl_names = list(character_data.get('猫娘', {}).keys())
            except Exception as e:
                logger.debug(f"[IdleMaint] 加载角色列表失败: {e}")
                continue

            # 强力记忆开关 → 控制 1b (fact_dedup) 和 2 (persona corrections)
            # 是否跑。子任务 1 (history 压缩) 和 3 (recent.review) 是 RFC 之
            # 前的基础设施，永远跑。本轮快照一次，跨角色复用。
            powerful_enabled = await _ais_powerful_memory_enabled()

            for name in catgirl_names:
                # 每处理一个角色前重新检查空闲，一旦变忙立即退出
                if not _is_idle():
                    logger.debug("[IdleMaint] 检测到新活动，中断本轮维护")
                    break

                try:
                    history = await recent_history_manager.aget_recent_history(name)
                    history_len = len(history)

                    # ── 子任务1: 历史记录压缩（有需要就跑，不受全局开关控制） ──
                    # 门槛对齐 update_history 内部的真实触发条件 `len > compress_threshold`
                    # （默认 20）。用 max_history_length（默认 10，压缩后保留条数）会让
                    # 11~20 区间持续触发 IdleMaint 但 update_history 实际不压缩，形成
                    # 每 IDLE_CHECK_INTERVAL 一次的空转日志。
                    if history_len > recent_history_manager.compress_threshold:
                        logger.info(
                            f"[IdleMaint] {name}: 历史记录过长 ({history_len} > "
                            f"{recent_history_manager.compress_threshold})，触发压缩"
                        )
                        try:
                            # 传空消息列表仅触发压缩逻辑
                            await recent_history_manager.update_history([], name, detailed=True)
                            logger.info(f"[IdleMaint] {name}: 历史记录压缩完成")
                        except Exception as e:
                            logger.warning(f"[IdleMaint] {name}: 历史记录压缩失败: {e}")

                    # ── 子任务1b: Fact 向量去重（P2 step 2） ──
                    # Runs *before* the review-gate so a character with
                    # short history still gets paraphrase consolidation
                    # (Codex PR-957 P2). The embedding worker enqueued
                    # candidate paraphrase pairs after the last fact-sweep;
                    # resolve them here via a single LLM call.
                    # fact_dedup_resolver is None when vectors are disabled
                    # or bootstrap failed — legacy hash + FTS5 dedup
                    # remains the entire dedup pipeline in that case.
                    # 强力记忆关 → 整段跳过（向量去重是 evidence-RFC 后期引入的）
                    if powerful_enabled and fact_dedup_resolver is not None:
                        if not _is_idle():
                            break
                        try:
                            pending_dedup = await fact_dedup_resolver.aload_pending(name)
                            if pending_dedup:
                                logger.info(
                                    f"[IdleMaint] {name}: 发现 {len(pending_dedup)} 对未处理的 fact 候选去重，触发 LLM 审视"
                                )
                                resolved = await fact_dedup_resolver.aresolve(name)
                                if resolved:
                                    logger.info(
                                        f"[IdleMaint] {name}: 完成 {resolved} 对 fact 去重决策"
                                    )
                        except Exception as e:
                            logger.warning(f"[IdleMaint] {name}: fact 向量去重失败: {e}")

                    # ── 子任务2: Persona 矛盾审视（强力记忆关时跳过） ──
                    # resolve_corrections 由 evidence-RFC 引入；矛盾队列的产生路
                    # 径（aadd_fact 的 keyword overlap heuristic 触发 _aqueue_correction）
                    # 在强力记忆关时仍可能产生（time-driven aadd_fact 也走启发式检查），
                    # 但消化路径 LLM 整批审视成本高，关时不跑。queue 会累积，
                    # 等用户重开强力记忆时一次性消化。
                    if powerful_enabled:
                        if not _is_idle():
                            break
                        try:
                            pending_corrections = await persona_manager.aload_pending_corrections(name)
                            if pending_corrections:
                                logger.info(
                                    f"[IdleMaint] {name}: 发现 {len(pending_corrections)} 条未处理的 persona 矛盾，触发审视"
                                )
                                resolved = await persona_manager.resolve_corrections(name)
                                if resolved:
                                    logger.info(f"[IdleMaint] {name}: 审视了 {resolved} 条 persona 矛盾")
                        except Exception as e:
                            logger.warning(f"[IdleMaint] {name}: persona 矛盾审视失败: {e}")

                    # ── 子任务3: 记忆整理 review ──
                    # Phase C: gate 逻辑全部集中到 maybe_spawn_review，IdleMaint
                    # 不再做单点门禁。spawn 函数内部自查 review_enabled / 历史长度
                    # / min_interval / 新消息门 / in-flight，不过门就 skip。
                    if not _is_idle():
                        break
                    try:
                        await maybe_spawn_review(name)
                    except Exception as e:
                        logger.warning(f"[IdleMaint] {name}: 记忆整理启动失败: {e}")

                except Exception as e:
                    logger.debug(f"[IdleMaint] {name}: 处理失败，跳过: {e}")
        finally:
            await asyncio.sleep(IDLE_CHECK_INTERVAL)


async def _periodic_new_dialog_qps_log_loop():
    """每 NEW_DIALOG_QPS_FLUSH_INTERVAL 秒输出一次 /new_dialog 调用计数并清零。

    无流量时也打 total=0 心跳——避免静默时无法区分'真零流量'与'loop 已挂'。
    """
    while True:
        await asyncio.sleep(NEW_DIALOG_QPS_FLUSH_INTERVAL)
        snapshot = dict(_new_dialog_qps_counter)
        _new_dialog_qps_counter.clear()
        total = sum(snapshot.values())
        logger.debug(
            f"[QPS] /new_dialog last {NEW_DIALOG_QPS_FLUSH_INTERVAL}s: "
            f"total={total} per_char={snapshot}"
        )


# memory-evidence-rfc §3.3.6 Reconciler handlers live in
# memory/evidence_handlers.py — imported at module top as
# `_register_evidence_handlers`. Keeping the handlers in their own module
# lets unit tests exercise the production apply path without booting FastAPI.


# ── memory-evidence-rfc §5: one-shot migration ──────────────────────

_MIGRATION_MARKER_ENTITY = '__meta__'
_MIGRATION_MARKER_ENTRY = '__evidence_migration_v1__'


def _migration_seed_from_reflection_status(status: str) -> tuple[float, float]:
    if status == 'promoted':
        return 2.0, 0.0
    if status == 'confirmed':
        return 1.0, 0.0
    if status == 'denied':
        return 0.0, 2.0
    return 0.0, 0.0


async def _aone_shot_migration_if_needed(lanlan_name: str) -> None:
    """Seed evidence fields on legacy reflection / persona entries.

    Marker-based guard: we inject a synthetic `__meta__.__evidence_migration_v1__`
    entry into persona (idempotent — `_find_entry_in_section` returns None if
    missing). Subsequent boots see the marker and skip.

    Reconciler-safe: all seed mutations go through `aapply_signal` which is
    event-sourced. A half-run migration is fully resumable: already-seeded
    entries have non-None `rein_last_signal_at`/`disp_last_signal_at` (set by
    the first seed event) and are skipped on resume.
    """
    try:
        persona = await persona_manager.aensure_persona(lanlan_name)
    except Exception as e:
        logger.debug(f"[Migration] {lanlan_name}: 读取 persona 失败: {e}")
        return

    marker_section = persona.get(_MIGRATION_MARKER_ENTITY)
    if isinstance(marker_section, dict):
        for entry in marker_section.get('facts', []):
            if isinstance(entry, dict) and entry.get('id') == _MIGRATION_MARKER_ENTRY:
                return  # Already migrated on a prior boot

    logger.info(f"[Migration] {lanlan_name}: 触发 evidence 字段一次性种子迁移")

    # Seed reflections
    try:
        reflections = await reflection_engine._aload_reflections_full(lanlan_name)
    except Exception as e:
        logger.warning(f"[Migration] {lanlan_name}: 读取 reflections 失败: {e}")
        reflections = []

    seeded_reflection = 0
    seed_failures = 0  # 只要有一条失败就不写 marker，保证下轮可补
    for r in reflections:
        if not isinstance(r, dict):
            continue
        rid = r.get('id')
        if not rid:
            continue
        # Skip already-seeded
        if r.get('rein_last_signal_at') or r.get('disp_last_signal_at'):
            continue
        rein, disp = _migration_seed_from_reflection_status(r.get('status', 'pending'))
        if rein == 0.0 and disp == 0.0:
            continue  # pending → no seed needed (defaults already 0)
        delta = {'reinforcement': rein, 'disputation': disp}
        try:
            ok = await reflection_engine.aapply_signal(
                lanlan_name, rid, delta, source=EVIDENCE_SOURCE_MIGRATION_SEED,
            )
            if ok:
                seeded_reflection += 1
        except Exception as e:
            seed_failures += 1
            logger.warning(f"[Migration] {lanlan_name}: seed reflection {rid} 失败: {e}")

    # Persona entries: non-protected with no prior signal timestamps get a
    # zero-seed event so they carry the evidence schema keys consistently
    # on disk even before the first real signal arrives. Protected entries
    # are exempt (their evidence_score is always inf anyway).
    seeded_persona = 0
    for entity_key, section in list(persona.items()):
        if entity_key == _MIGRATION_MARKER_ENTITY or not isinstance(section, dict):
            continue
        for entry in section.get('facts', []):
            if not isinstance(entry, dict):
                continue
            if entry.get('protected'):
                continue
            if entry.get('rein_last_signal_at') or entry.get('disp_last_signal_at'):
                continue
            if entry.get('reinforcement') or entry.get('disputation'):
                continue
            entry_id = entry.get('id')
            if not entry_id:
                continue
            # 零 delta 等效 "no-op + 字段 normalize"；不推进 last_signal_at，
            # 但走完一次 record_and_save 保证 view 里 schema 完整。
            try:
                ok = await persona_manager.aapply_signal(
                    lanlan_name, entity_key, entry_id,
                    delta={'reinforcement': 0.0, 'disputation': 0.0},
                    source=EVIDENCE_SOURCE_MIGRATION_SEED,
                )
                if ok:
                    seeded_persona += 1
            except Exception as e:
                seed_failures += 1
                logger.warning(
                    f"[Migration] {lanlan_name}: seed persona {entity_key}/{entry_id} 失败: {e}"
                )

    # CodeRabbit PR #929 fix: 如果本轮有任何 seed 失败，marker 不写入——
    # 下次启动继续从断点补（已 seed 过的字段检查会跳过）。避免瞬时 IO
    # 抖动导致某些 entry 永远漏种。
    if seed_failures > 0:
        logger.warning(
            f"[Migration] {lanlan_name}: 本轮 {seed_failures} 条 seed 失败 "
            f"（reflection={seeded_reflection} persona={seeded_persona}），"
            f"marker 暂不写入，下次启动继续补"
        )
        return

    # Drop the marker entry so we don't re-run next boot. Marker is a
    # synthetic "fact" under a synthetic entity — it never surfaces in
    # render (protected-free path for it is also skipped; render loops
    # over the known entity keys and the sync_character_card path).
    async with persona_manager._get_alock(lanlan_name):
        persona = await persona_manager._aensure_persona_locked(lanlan_name)
        marker_section = persona.setdefault(_MIGRATION_MARKER_ENTITY, {})
        facts = marker_section.setdefault('facts', [])
        if not any(
            isinstance(e, dict) and e.get('id') == _MIGRATION_MARKER_ENTRY
            for e in facts
        ):
            facts.append({
                'id': _MIGRATION_MARKER_ENTRY,
                'text': '',
                'source': EVIDENCE_SOURCE_MIGRATION_SEED,
                'source_id': None,
                'protected': True,  # 豁免 render/archive 任意扫描
                'migrated_at': datetime.now().isoformat(),
            })
            await persona_manager.asave_persona(lanlan_name, persona)

    logger.info(
        f"[Migration] {lanlan_name}: seed 完成 "
        f"reflection={seeded_reflection} persona={seeded_persona}"
    )


# ── memory-evidence-rfc §3.5.5: one-shot archive migration ──────────


async def _aone_shot_archive_migration_if_needed(lanlan_name: str) -> None:
    """Migrate legacy flat ``reflections_archive.json`` → sharded directory.

    Idempotent: a sentinel file inside the new dir guards re-runs.
    Persona had no flat archive predecessor, so only reflection needs
    migration here.
    """
    try:
        await reflection_engine.aone_shot_archive_migration(lanlan_name)
    except Exception as e:
        # NEVER let archive migration block boot — RFC §3.5.5 explicitly
        # allows the legacy file to remain as fallback if migration fails.
        logger.warning(
            f"[Migration] {lanlan_name}: 旧 reflections_archive 分片迁移失败 (非致命): {e}"
        )


# ── memory-evidence-rfc §3.5: periodic archive sweep ────────────────


# Round-robin 起点游标：每轮 +1。避免每次都从 catgirl_names[0] 开始扫描
# + 命中即 break 造成首角色独占（CodeRabbit review on PR #1316 catch）。
# 模块级状态可接受：循环单实例、单事件循环、无并发。
_RECHECK_RR_CURSOR: int = 0


async def _periodic_slow_memory_recheck_loop():
    """Schema v1 → v2 慢速记忆重判循环。

    每 MEMORY_RECHECK_INTERVAL_SECONDS 秒重判 1 条 reflection / fact。优
    先级：所有角色的 v1 reflection 先跑完，再跑 fact。每轮只处理 1 条，
    控速避免 LLM 抢占工作模型 quota（参考 archive_sweep 的 background-tier
    设计）。

    多角色公平性：用 `_RECHECK_RR_CURSOR` 做 round-robin 起点轮转——每轮
    从 cursor 开始扫描，命中即 break + 推进 cursor。catgirl A 有 100 条
    v1 数据、catgirl B 只有 1 条时，B 仍能在 N 轮内拿到调度名额，不被
    A 长尾独占。

    LLM 输出：
    - reflection: temporal_scope (pattern/state/episode) + event_when (相对偏移)
    - fact:       event_when 单字段
    系统按 created_at 当锚点解算 event_start_at / event_end_at 写回。

    Skip 条件（在 store 层做）：
    - schema_version >= CURRENT
    - reflection status in REFLECTION_TERMINAL_STATUSES（archived 等）
    - 已 archive 的 reflection / fact 在 shard 文件里，主路径不加载，
      自然不会被选中

    首轮启动延迟 MEMORY_RECHECK_INITIAL_DELAY_SECONDS 秒（与其他后台循环
    错峰）。`MEMORY_RECHECK_ENABLED=False` 时整个循环不启动。
    """
    global _RECHECK_RR_CURSOR
    if not MEMORY_RECHECK_ENABLED:
        logger.info("[MemoryRecheck] 重判循环未启用 (MEMORY_RECHECK_ENABLED=False)")
        return
    await asyncio.sleep(MEMORY_RECHECK_INITIAL_DELAY_SECONDS)
    logger.info("[MemoryRecheck] 慢速 schema v1→v2 重判循环启动")
    while True:
        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[MemoryRecheck] 加载角色列表失败: {e}")
            await asyncio.sleep(MEMORY_RECHECK_INTERVAL_SECONDS)
            continue

        # Round-robin: 每轮起点比上轮 +1，保证 N 角色在 N 轮内都被尝试到
        n = len(catgirl_names)
        if n == 0:
            await asyncio.sleep(MEMORY_RECHECK_INTERVAL_SECONDS)
            continue
        start = _RECHECK_RR_CURSOR % n
        ordered = catgirl_names[start:] + catgirl_names[:start]
        _RECHECK_RR_CURSOR = (start + 1) % n

        # 阶段 1：reflection 优先（数据少、影响 prompt 直接、价值高）
        # 阶段 2：所有 reflection 跑完后才轮到 fact（数据多、影响间接）
        # 每次外循环只动 1 条，避免单角色 reflection 长时间独占
        did_one = False
        for name in ordered:
            try:
                if await reflection_engine.arecheck_one_legacy_reflection(name):
                    did_one = True
                    break
            except Exception as e:
                logger.debug(f"[MemoryRecheck] {name} reflection recheck 异常: {e}")
        if not did_one:
            for name in ordered:
                try:
                    if await fact_store.arecheck_one_legacy_fact(name):
                        did_one = True
                        break
                except Exception as e:
                    logger.debug(f"[MemoryRecheck] {name} fact recheck 异常: {e}")

        await asyncio.sleep(MEMORY_RECHECK_INTERVAL_SECONDS)


async def _periodic_archive_sweep_loop():
    """Periodically scan all non-protected reflection / persona entries
    and (a) bump `sub_zero_days` for those with `evidence_score < 0`
    today, (b) move entries with `sub_zero_days >= EVIDENCE_ARCHIVE_DAYS`
    into a sharded archive file.

    Runs every `EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS`. The
    `maybe_mark_sub_zero` helper has its own day-based debounce so a
    sub-day cadence does not over-count (RFC §3.5.3).

    Per-character iteration is parallel (`asyncio.gather`) — each
    character has independent files + locks; one slow char must not
    block another.

    首轮启动延迟 _INITIAL_DELAY_ARCHIVE 秒（远小于 INTERVAL=3600s，确保
    短会话用户也能跑到一次归档；之后按 INTERVAL 周期跑）。
    """
    from memory.evidence import maybe_mark_sub_zero
    await asyncio.sleep(_INITIAL_DELAY_ARCHIVE)
    while True:
        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[ArchiveSweep] 加载角色列表失败: {e}")
            await asyncio.sleep(EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS)
            continue

        now = datetime.now()

        async def _sweep_one(name: str):
            """Scan one character's reflections + persona entries.

            For each non-protected entry:
              1. Snapshot-test `maybe_mark_sub_zero` (mutates a COPY so
                 we don't dirty the cache; the real increment + event
                 happen inside `aincrement_sub_zero` under the per-char
                 lock).
              2. Call `aincrement_sub_zero` if needed → returns the new
                 count or None (no-op).
              3. Determine the effective `sub_zero_days` for the archive
                 check:
                    - If we just incremented → use the returned count
                    - Else → use the on-disk count from step 1's read
                 Same-tick archival saves an extra sweep cycle for
                 entries that were already at threshold but missed the
                 last increment due to debounce.
              4. If `effective_sz >= EVIDENCE_ARCHIVE_DAYS` → archive.

            All three operations (increment / archive / their event
            writes) re-read the view under the per-char lock, so this
            outer scan can use a stale snapshot safely.
            """
            try:
                # ── reflections ──
                refls = await reflection_engine._aload_reflections_full(name)
                for r in refls:
                    if not isinstance(r, dict):
                        continue
                    if r.get('protected'):
                        continue
                    rid = r.get('id')
                    if not rid:
                        continue
                    pre_sz = int(r.get('sub_zero_days', 0) or 0)
                    will_increment = maybe_mark_sub_zero(dict(r), now)
                    new_count: int | None = None
                    if will_increment:
                        try:
                            new_count = await reflection_engine.aincrement_sub_zero(
                                name, rid, now,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[ArchiveSweep] {name}: reflection {rid} "
                                f"sub_zero 增量失败: {e}"
                            )
                    effective_sz = new_count if new_count is not None else pre_sz
                    if effective_sz >= EVIDENCE_ARCHIVE_DAYS:
                        try:
                            await reflection_engine.aarchive_reflection(name, rid)
                        except Exception as e:
                            logger.warning(
                                f"[ArchiveSweep] {name}: reflection {rid} 归档失败: {e}"
                            )

                # ── persona entries ──
                persona = await persona_manager.aensure_persona(name)
                # Snapshot (entity_key, entry_id, pre_sz) tuples; mutations
                # go through aincrement / aarchive which re-load.
                snapshots: list[tuple[str, str, int, bool]] = []
                for entity_key, section in list(persona.items()):
                    if not isinstance(section, dict):
                        continue
                    for entry in section.get('facts', []):
                        if not isinstance(entry, dict):
                            continue
                        if entry.get('protected'):
                            continue
                        eid = entry.get('id')
                        if not eid:
                            continue
                        pre_sz = int(entry.get('sub_zero_days', 0) or 0)
                        will_inc = maybe_mark_sub_zero(dict(entry), now)
                        snapshots.append((entity_key, eid, pre_sz, will_inc))

                for entity_key, eid, pre_sz, will_inc in snapshots:
                    new_count = None
                    if will_inc:
                        try:
                            new_count = await persona_manager.aincrement_sub_zero(
                                name, entity_key, eid, now,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[ArchiveSweep] {name}: persona {entity_key}/{eid} "
                                f"sub_zero 增量失败: {e}"
                            )
                    effective_sz = new_count if new_count is not None else pre_sz
                    if effective_sz >= EVIDENCE_ARCHIVE_DAYS:
                        try:
                            await persona_manager.aarchive_persona_entry(
                                name, entity_key, eid,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[ArchiveSweep] {name}: persona {entity_key}/{eid} 归档失败: {e}"
                            )
            except Exception as e:
                logger.debug(f"[ArchiveSweep] {name}: 扫描失败，跳过: {e}")

        if catgirl_names:
            await asyncio.gather(
                *(_sweep_one(name) for name in catgirl_names),
                return_exceptions=True,
            )

        await asyncio.sleep(EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS)


# ── memory-evidence-rfc §3.4.3: background signal extraction loop ───

_signal_check_state: dict[str, dict] = {}
"""Per-character signal extraction state.

Schema:
  {
    'turns_since': int,           # turn counter since last successful check
    'last_check_ts': str | None,  # ISO cursor for path A window start
    'last_a_msg_ts': datetime,    # path A 实际处理过的最晚 msg ts (path B 上游边界)
    'last_b_check_ts': datetime,  # ISO cursor for path B window start
    'b_tick_counter': int,        # ticks since last path B trigger
    # Liveness counters (in-memory only)：cursor key → 连续失败次数。
    # 成功 mark_done 时清空对应 path 的 counter。重启清零是有意为之的"软兜底"
    # ——重启后再试 MEMORY_LIVENESS_MAX_ATTEMPTS 次再 dead-letter，避免内存
    # counter 错误地把短暂 transient 失败永久放弃。
    'a_extract_failures': dict[str, int],  # path A cursor (last_check_ts) → fail count
    'b_extract_failures': dict[str, int],  # path B cursor (last_b_check_ts) → fail count
  }
"""


def _signal_check_should_run(name: str, now: datetime) -> bool:
    state = _signal_check_state.setdefault(name, {'turns_since': 0, 'last_check_ts': None})
    if state['turns_since'] >= EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS:
        return True
    last = state.get('last_check_ts')
    if last is None:
        # 未 check 过 → 走空闲分支（需要 idle）
        return _is_idle() and state['turns_since'] > 0
    try:
        last_dt = datetime.fromisoformat(last)
    except (ValueError, TypeError):
        return True
    if (now - last_dt).total_seconds() >= EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES * 60:
        return state['turns_since'] > 0
    return False


def _signal_check_record_turn(name: str) -> None:
    state = _signal_check_state.setdefault(name, {'turns_since': 0, 'last_check_ts': None})
    state['turns_since'] = int(state.get('turns_since', 0) or 0) + 1


def _signal_check_mark_done(name: str, now: datetime) -> None:
    state = _signal_check_state.setdefault(name, {'turns_since': 0, 'last_check_ts': None})
    state['turns_since'] = 0
    state['last_check_ts'] = now.isoformat()
    # Cursor 推进 → path A 的旧 cursor key 永远不会再被命中，清空 counter
    # 避免内存 dict 随 cursor 历史无限增长。同时把"曾经失败但靠新数据冲过去
    # 了"的窗口归零，下次毒窗口出现按 fresh attempt 计算。
    state['a_extract_failures'] = {}


def _stage1_path_a_bump_failure(
    name: str, state: dict, cursor_key: str, now: datetime,
) -> bool:
    """Path A Stage-1 LLM 终态失败的 liveness 兜底。

    给 (cursor_key) 当前窗口 bump 失败计数；达 ``MEMORY_LIVENESS_MAX_ATTEMPTS``
    时强推 cursor 到 now（视为放弃该窗口的 fact 抽取），返回 True；未达上限
    返回 False（caller 走原有"保留 cursor 下轮重试"路径）。

    Why: 毒 msg（safety filter / content policy / 永远 parse 不出来的畸形
    输出）让 ``_allm_call_with_retries`` 永久耗尽，原代码捕获后直接 return
    不动 cursor → 下轮重读同窗口 → 永远卡死该角色的 fact pipeline（类似
    PR #1399 "26 天 0 fact" 事故的 liveness 缺口）。强推 cursor 等于
    放弃这段窗口的 fact 抽取，代价上限 = N × interval (≈ 3 分钟)，远比
    "永久 0 fact" 划算。
    """
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    fails = state.setdefault('a_extract_failures', {})
    fails[cursor_key] = int(fails.get(cursor_key, 0) or 0) + 1
    if fails[cursor_key] < MEMORY_LIVENESS_MAX_ATTEMPTS:
        return False
    logger.warning(
        f"[SignalLoop] {name}: Stage-1 path A 在 cursor {cursor_key!r} "
        f"累计失败 {fails[cursor_key]} 次 ≥ {MEMORY_LIVENESS_MAX_ATTEMPTS}，"
        f"强推 cursor 到 {now.isoformat(timespec='seconds')} "
        f"放弃该窗口（dead-letter）。Why: 毒窗口 liveness 兜底。"
    )
    _signal_check_mark_done(name, now)  # 会顺带把 a_extract_failures 清空
    return True


def _stage1_path_b_bump_failure(
    name: str, state: dict, cursor_key: str, force_to: datetime,
) -> bool:
    """Path B Stage-1 LLM 终态失败的 liveness 兜底（path A 的对偶）。

    给 (cursor_key) 当前 B 窗口 bump 失败计数；达
    ``MEMORY_LIVENESS_MAX_ATTEMPTS`` 时强推 ``state['last_b_check_ts']``
    到 ``force_to`` (= last_fetched_ts)，返回 True；未达上限返回 False
    （caller 走原有"保留 cursor 下次 trigger 重试"路径）。

    Why: 跟 path A 同源问题——B 的 ``persisted is None`` 分支原代码直接
    return 不动 ``last_b_check_ts`` → 下次 B trigger 重读 [last_b_check_ts,
    last_a_msg_ts] 同窗口 → 仍卡。强推 cursor 到 last_fetched_ts 等于
    放弃 AI-aware 视角下的这段窗口，代价上限 = N × B trigger 间隔。
    """
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    fails = state.setdefault('b_extract_failures', {})
    fails[cursor_key] = int(fails.get(cursor_key, 0) or 0) + 1
    if fails[cursor_key] < MEMORY_LIVENESS_MAX_ATTEMPTS:
        return False
    logger.warning(
        f"[PathB] {name}: Stage-1 path B 在 cursor {cursor_key!r} "
        f"累计失败 {fails[cursor_key]} 次 ≥ {MEMORY_LIVENESS_MAX_ATTEMPTS}，"
        f"强推 last_b_check_ts 到 {force_to.isoformat(timespec='seconds')} "
        f"放弃该窗口（dead-letter）。Why: 毒窗口 liveness 兜底。"
    )
    state['last_b_check_ts'] = force_to
    state['b_extract_failures'] = {}
    return True


def _signal_check_window_start(name: str, now: datetime) -> datetime:
    """Compute the start of the SQL window for the signal-extraction cycle.

    Use the previous successful `last_check_ts` when available so long
    active sessions do not silently drop messages older than the fallback
    window. Cold-start (first run or after corrupt state) falls back to
    `now - EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES * 2` — wider than a single
    idle trigger window but bounded so the initial scan is not unbounded.
    """
    state = _signal_check_state.get(name, {})
    last = state.get('last_check_ts')
    if last:
        try:
            ts = datetime.fromisoformat(last)
            # Clock-skew safety: never let cursor land in the future
            if ts <= now:
                return ts
        except (ValueError, TypeError) as e:
            # Corrupt cursor value in in-memory state (shouldn't happen —
            # we always write ISO-8601 — but stay defensive so one bad
            # character doesn't stall the signal loop). Fall through to
            # the bounded fallback window below.
            logger.debug(
                f"[SignalLoop] {name}: last_check_ts {last!r} 解析失败 ({e}), 用 fallback 窗口"
            )
    return now - timedelta(minutes=EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES * 2)


async def _adispatch_evidence_signals(
    lanlan_name: str, signals: list[dict], source: str,
) -> bool:
    """Apply each signal through ReflectionEngine / PersonaManager aapply_signal.

    Delta mapping (§3.4.1 v1.2.1 weight scheme):
      source='user_fact' + reinforces → USER_FACT_REINFORCE_DELTA (indirect,
        silver; combo bonus handled inside compute_evidence_snapshot)
      source='user_fact' + negates    → USER_FACT_NEGATE_DELTA
      source='user_keyword_rebut'     → USER_KEYWORD_REBUT_DELTA (always negates)

    Defensive: unknown target_type / missing manager refs are skipped.

    Returns True if ALL signals applied successfully; False if any raised
    (`aapply_signal` raises for critical IO / event-log errors, but returns
    False silently for unknown target_id). Caller can use the return value
    to decide whether to advance its cursor (CodeRabbit PR #929 major).
    """
    all_ok = True
    for s in signals:
        if not isinstance(s, dict):
            continue
        signal_kind = s.get('signal')
        if signal_kind == 'reinforces':
            # Indirect inference (Stage-2) gets half weight; combo logic in
            # `compute_evidence_snapshot` re-inflates it past the threshold.
            delta = {'reinforcement': USER_FACT_REINFORCE_DELTA}
        elif signal_kind == 'negates':
            # keyword_rebut uses a different constant from fact-derived negates
            # only in name — both currently 1.0. Pick by source for clarity.
            if source == EVIDENCE_SOURCE_USER_KEYWORD_REBUT:
                delta = {'disputation': USER_KEYWORD_REBUT_DELTA}
            else:
                delta = {'disputation': USER_FACT_NEGATE_DELTA}
        else:
            continue

        target_type = s.get('target_type')
        target_id = s.get('target_id')
        if not target_id:
            continue

        try:
            if target_type == 'reflection':
                await reflection_engine.aapply_signal(
                    lanlan_name, target_id, delta, source=source,
                )
            elif target_type == 'persona':
                entity_key = s.get('entity_key')
                if not entity_key:
                    logger.warning(
                        f"[Signal] {lanlan_name}: persona signal 缺 entity_key，丢弃"
                    )
                    continue
                await persona_manager.aapply_signal(
                    lanlan_name, entity_key, target_id, delta, source=source,
                )
            else:
                logger.warning(f"[Signal] {lanlan_name}: 未知 target_type={target_type}")
        except Exception as e:
            # Critical failure (event_log fsync / atomic_write_json fail,
            # etc.) — flag so caller can preserve the cursor; subsequent
            # signals in this batch still attempted (best-effort).
            all_ok = False
            logger.warning(
                f"[Signal] {lanlan_name}: aapply_signal 失败 ({target_type}/{target_id}): {e}"
            )
    return all_ok


async def _run_path_b(name: str, state: dict) -> None:
    """Path B: AI-aware Stage-1 only（不进 Stage-2 evidence loop）。

    Piggyback 在 path A 循环里，每 ``EVIDENCE_AI_AWARE_EVERY_N_A_TICKS`` 次 A
    tick 触发一次。窗口下游边界用 path A 实际处理过的最晚 msg ts，保证 B
    看到的消息严格被 A 看过——避免"A scan SQL 完成那一刻之后才入 SQLite 的
    msg 被 B 抢先处理"的 race。

    设计要点：
      1. 窗口 = [last_b_check_ts, last_a_msg_ts]。cold start last_b 推算 =
         last_a_msg_ts - max(N_TICKS, N_TURNS) × IDLE_MINUTES（取两种 A 触发
         节律的较保守值，cover sparse turn 场景）
      2. SQL 层 LIMIT MAX_AI_AWARE_WINDOW_MSGS 防极端长窗口爆 prompt
      3. 已知 fact 池：从 facts.json 拉 created_at ≥ last_b 的 fact（不设
         上界——A idle delay 让最新一批 A facts 的 created_at 略晚于
         last_a_msg_ts，设上界会把它们整批漏掉），按 importance DESC 取
         前 MAX_KNOWN_POOL_FACTS 塞 prompt，让 LLM 输出层主动去重 path A
         已抽内容
      4. 落盘 default_source='ai_disclosure'；LLM 显式 source 字段优先
         注：喂给 Stage-1 的消息会先 trim 到 user-msg-bracket（首条 user msg
         到末条 user msg 之间，含两端）——product thesis 防廉价层污染，
         首尾的 AI 残段不该被 path B 当 fact 沉淀
      5. Cursor 推进规则：
         - SQL 返 0 rows → 推到 last_a_msg_ts（窗口确实空）
         - SQL 返 N rows 但全 system/空 msg → 推到 last fetched row ts
           （未取尾巴可能有内容）
         - Stage-1 LLM 终态失败（aextract_facts_with_known_pool 返 None）
           → cursor 不推进，下次 trigger 重试同窗口（fact dedup 防双写）
         - 其它正常路径 → 推到 last fetched row ts（截断时 < last_a_msg_ts）

    与 path A 区别：
    - 不进 Stage-2 evidence loop（_apersist_new_facts 写 signal_processed=True
      + aextract_facts_and_detect_signals 内部 source filter 双重防御）
    - Stage-1 失败 swallow 不抛（path A 自己的 FactExtractionFailed 有独立
      retry 路径，B 是补抓性质不该阻塞），但 cursor 必须保留——失败窗口
      下次 trigger 重试，不能折叠成"成功 0 抽"静默 skip
    """
    last_a_msg_ts = state.get('last_a_msg_ts')
    if last_a_msg_ts is None:
        # A 还没成功处理过任何 batch，B 无源可看
        return
    # 防御性 TZ normalize：`_coerce_db_ts` 已经在写入 state 时归一化成 naive
    # 是主路径保护，但外部 state injection / 升级前残留的 aware 值仍可能漏进
    # 来——下面所有 cursor 比较 + known_pool created_at 比较都按 naive 工作，
    # 这里再 strip 一遍把整个 _run_path_b 变成自包含 naive-only 域（Codex P2
    # round-8 on PR #1408 双侧 case）。
    if last_a_msg_ts.tzinfo is not None:
        last_a_msg_ts = last_a_msg_ts.replace(tzinfo=None)
        state['last_a_msg_ts'] = last_a_msg_ts

    last_b = state.get('last_b_check_ts')
    if last_b is not None and last_b.tzinfo is not None:
        last_b = last_b.replace(tzinfo=None)
        state['last_b_check_ts'] = last_b
    if last_b is None:
        # Cold start lookback：B 第一次 trigger 时 last_b 无值，需要估个起点。
        # A tick 不一定按 IDLE gate 节律走——也可能被 turn-count gate
        # (EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS 累积) 触发，或在 sparse turn
        # 场景（user 间歇性发声、turn 间隔 >> IDLE_MIN）下两 tick 之间跨度
        # 远超 IDLE_MIN。只按 piggyback 估算 (N_TICKS × IDLE_MIN) 会让 cold
        # start 起点落在 A 真正处理过的范围之内，B 永久 skip 那段之前的
        # AI-only msg（Codex P2 round-6 on PR #1408）。
        # 修法：取 max(piggyback 节律, turn-count 节律) × IDLE_MIN 当估算
        # 上限。默认下 max(3, 10) × 10min = 100min。LIMIT 兜底防爆 prompt，
        # Stage-1 dedup hash 防双写——overshoot 是安全的。
        cold_start_ticks_estimate = max(
            EVIDENCE_AI_AWARE_EVERY_N_A_TICKS,
            EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS,
        )
        estimated_a_coverage = timedelta(
            minutes=cold_start_ticks_estimate * EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES
        )
        last_b = last_a_msg_ts - estimated_a_coverage

    if last_b >= last_a_msg_ts:
        # 窗口为空（B 已追上 A）
        return

    try:
        rows = await time_manager.aretrieve_original_by_timeframe(
            name, last_b, last_a_msg_ts,
            limit_rows=MAX_AI_AWARE_WINDOW_MSGS,
        )
    except Exception as e:
        logger.warning(f"[PathB] {name}: 读取窗口失败: {e}")
        return
    if not rows:
        # `aretrieve_original_by_timeframe` 在 SQL exception / engine init 失败
        # / 维护态等情况下都 swallow + 返 []（见 timeindex.py 实现），从 caller
        # 端无法区分"真空窗口"vs"transient 读失败"。保守起见 cursor 不推：
        # - 真空窗口：A 刚成功处理了同段范围，B 这里几乎不可能真空（除非
        #   A 的 SQL 看到 row 但 B 的 SQL 同段读不到——意味着 SQL 层异常）。
        #   下次 B trigger 再 query 一次 0 rows 也是常数代价（SQLite 空范围
        #   scan 极快）。
        # - Transient 失败：保留 cursor 让下次 trigger 重试该窗口，避免把整段
        #   [last_b, last_a_msg_ts] 永久 skip（Codex P1 round-5 on PR #1408）。
        logger.debug(
            f"[PathB] {name}: 窗口 {last_b.isoformat(timespec='seconds')} → "
            f"{last_a_msg_ts.isoformat(timespec='seconds')} 取回 0 rows "
            f"(可能 SQL transient 失败 swallow 成 []), 保留 cursor 下次 trigger 复查"
        )
        return

    # 解析 SQL 实际取到的最后一行 ts —— 后续所有 cursor 推进点都用这个值，
    # 不能用 last_a_msg_ts。差别只在窗口被 MAX_AI_AWARE_WINDOW_MSGS LIMIT
    # 截断时显现：截断时 last_fetched_ts < last_a_msg_ts，未取到的尾巴留
    # 给下次 B trigger 继续处理；若推到 last_a_msg_ts 会让尾巴永久 skip
    # （Codex P1 round-1 on PR #1408, P2 round-2 covers filtered-empty case）。
    last_fetched_ts = _coerce_db_ts(rows[-1][0])
    if last_fetched_ts is None:
        # 防御：_coerce_db_ts 解析失败退回 last_a_msg_ts（避免 cursor 不动
        # 死循环）。正常路径不触发——rows[-1][0] 是 SQLite 返回的 ts 字符串。
        last_fetched_ts = last_a_msg_ts

    # 同 ts 簇 LIMIT 截断死循环防御（Codex P2 round-3 on PR #1408）：
    # aretrieve_original_by_timeframe 用 inclusive `BETWEEN`，若窗口里
    # > MAX_AI_AWARE_WINDOW_MSGS 行共享同一 ts（极端情况：bulk import 或
    # store_conversation 给一次请求里所有 row 写同 ts），那么 LIMIT 切出
    # 的最早 N 行全在同 ts，cursor 推到 last_fetched_ts 后下次 BETWEEN
    # 仍把这批 row 全部捞回来 → 无限循环、该 ts 簇后面的 row 永远 skip。
    # 检测：LIMIT 拉满 AND 所有 fetched row 同 ts → cursor +1μs 越过该 ts。
    # 代价：该 ts 簇 LIMIT 之后的 tail row 被 skip（罕见——一次正常对话
    # turn 写 2~5 行，远 < MAX_AI_AWARE_WINDOW_MSGS=200）。无更便宜的修法
    # 除非把 cursor 改成 (ts, rowid) 复合键、改写 SQL，太重不划算。
    first_fetched_ts = _coerce_db_ts(rows[0][0])
    if (
        len(rows) >= MAX_AI_AWARE_WINDOW_MSGS
        and first_fetched_ts is not None
        and last_fetched_ts == first_fetched_ts
    ):
        logger.warning(
            f"[PathB] {name}: 同 ts 簇 {first_fetched_ts.isoformat(timespec='microseconds')} "
            f"行数 ≥ LIMIT ({MAX_AI_AWARE_WINDOW_MSGS})，cursor +1μs 越过避免死循环；"
            f"该 ts 簇 LIMIT 之后的 tail row 会被 skip"
        )
        last_fetched_ts = last_fetched_ts + timedelta(microseconds=1)

    message_dicts = _extract_role_tagged_messages_from_rows(rows)
    if not message_dicts:
        # 全是 system msg / 空内容。cursor 推到 last fetched（不是 last_a_msg_ts），
        # 截断时未取尾巴可能含有效 msg。
        state['last_b_check_ts'] = last_fetched_ts
        state['b_extract_failures'] = {}
        return

    # 截到 user msg bracket：首条 user msg 到末条 user msg 之间（含两端）。
    # Product thesis 防廉价层污染——首尾的 AI 残段（user 没印证过的试探 /
    # user 没回应过的独白）不该当 fact 沉淀。
    message_dicts = _trim_to_user_msg_bracket(message_dicts)
    if not message_dicts:
        # 窗口内完全无 user msg → AI-only 廉价层，故意 skip。cursor 照常推
        # 进，下次 B trigger 不会再来覆盖这段。
        logger.debug(
            f"[PathB] {name}: 窗口 {last_b.isoformat(timespec='seconds')} → "
            f"{last_fetched_ts.isoformat(timespec='seconds')} 无 user msg bracket "
            f"(纯 AI-only 内容，product thesis 跳过)"
        )
        state['last_b_check_ts'] = last_fetched_ts
        state['b_extract_failures'] = {}
        return

    from utils.llm_client import convert_to_messages
    messages = convert_to_messages(message_dicts)

    # 已知 fact 池：用 path A 在本 B 窗口内 / 之后写的 fact 当 do-not-repeat 提示。
    # 只设下界 ``created_at >= last_b``、不设上界（CodeRabbit on PR #1408）：
    # A 的 idle/polling 延迟让"刚扫完本 B 窗口"那批 fact 的 created_at 普遍
    # 略晚于 last_a_msg_ts，若用 created_at <= last_a_msg_ts 过滤会把最新一
    # 批 A facts 整批排除——known_pool 对"刚被 A 抽过的内容"失效，path B 更
    # 容易和 A 重复抽同一窗口。多包含一些"窗口后"的 A fact 是安全的：known
    # _pool 只是 LLM 的提示，多余条目至多让 B 多抑制少量新 fact，且 Stage-1
    # dedup hash 仍是兜底。按 importance DESC 取前 MAX_KNOWN_POOL_FACTS。
    try:
        all_facts = await fact_store.aload_facts(name)
    except Exception as e:
        logger.debug(f"[PathB] {name}: aload_facts 失败，known pool 留空: {e}")
        all_facts = []

    # Importance 用 safe_importance 兜底——legacy/手改 facts.json 里可能
    # 有 'importance': "high" / None / list 等脏值，raw int(...) cast 会
    # ValueError 把整个 B 跑挂、下次 trigger 又同样脏值同样挂，path B 对该
    # 角色永久哑火（Codex P2 round-1 on PR #1408）。
    from memory.facts import safe_importance

    known_pool: list[dict] = []
    for f in all_facts:
        if not isinstance(f, dict):
            continue
        created_at_raw = f.get('created_at') or ''
        try:
            # 完整 ISO 解析（含微秒）—— `created_at` 是 datetime.now().isoformat()
            # 写盘的，截到 [:19] 会丢微秒，让 created_at == last_b + 0.x 秒的
            # fact 在 `>= last_b` 比较里被误判出窗口（CodeRabbit on PR #1408）。
            created_at = datetime.fromisoformat(created_at_raw)
        except (ValueError, TypeError):
            continue
        # 防御：本仓库 `_apersist_new_facts` 写的 `created_at` 都是 naive
        # datetime.now().isoformat()，但若 import/migration 路径写入了 TZ-aware
        # 值（如 "...+00:00"），跟 naive 的 last_b 比较会抛 TypeError 让
        # `_run_path_b` 一直 fail，path B 对该角色永久哑火（Codex P1 round-7
        # on PR #1408）。比较口径上把 aware 当 naive 处理——绝大多数场景就是
        # 同一 wall-clock 时间，时区差异不应让 fact 抽取整段挂掉。
        if created_at.tzinfo is not None:
            created_at = created_at.replace(tzinfo=None)
        if created_at >= last_b:
            known_pool.append(f)
    known_pool.sort(key=lambda f: -safe_importance(f))
    known_pool = known_pool[:MAX_KNOWN_POOL_FACTS]

    persisted = await fact_store.aextract_facts_with_known_pool(
        name, messages, known_pool,
    )
    if persisted is None:
        # Stage-1 LLM 终态失败（重试耗尽）。cursor 保留不推进，下次 B trigger
        # 重试同窗口（fact dedup hash 防双写）。区分 None vs [] 至关重要：
        # 若把失败折叠成"成功 0 抽"，失败窗口会被永久 skip（CodeRabbit / Codex
        # P1 round-2 on PR #1408）。
        #
        # Liveness 兜底（path A 的对偶）：同一 last_b_check_ts cursor 反复
        # 失败 ≥ MEMORY_LIVENESS_MAX_ATTEMPTS 时强推 cursor 到 last_fetched_ts，
        # 避免毒窗口让 B pipeline 永久卡死该角色的 AI-aware fact 抽取。
        cursor_key = (
            last_b.isoformat(timespec='microseconds') if last_b else 'cold'
        )
        if not _stage1_path_b_bump_failure(name, state, cursor_key, last_fetched_ts):
            logger.warning(
                f"[PathB] {name}: Stage-1 终态失败，保留 cursor 下次 trigger 重试 "
                f"(window={last_b.isoformat(timespec='seconds')} → "
                f"{last_fetched_ts.isoformat(timespec='seconds')})"
            )
        return
    if persisted:
        logger.info(
            f"[PathB] {name}: AI-aware Stage-1 抽出 {len(persisted)} 条新 fact "
            f"(window={last_b.isoformat(timespec='seconds')} → "
            f"{last_fetched_ts.isoformat(timespec='seconds')}, "
            f"known_pool={len(known_pool)})"
        )

    state['last_b_check_ts'] = last_fetched_ts
    # Cursor 推进 → 旧 cursor key 永远不会再被命中，清空 path-B counter
    # 避免内存 dict 随 cursor 历史无限增长（对偶 _signal_check_mark_done
    # 在 path A 成功路径上清 a_extract_failures）。
    state['b_extract_failures'] = {}


async def _periodic_signal_extraction_loop():
    """每 EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS 轮询，满足触发条件时对每个
    catgirl 跑 Stage-1 + Stage-2 + signal dispatch（RFC §3.4.3）。

    首轮启动延迟 _INITIAL_DELAY_SIGNAL 秒（与其他后台循环错峰）。
    """
    await asyncio.sleep(_INITIAL_DELAY_SIGNAL)
    while True:
        # 强力记忆关 → Stage-1 + Stage-2 evidence 抽取整段停。这是 evidence-RFC
        # 引入的 token 大头（每 40s 轮询一次，trigger 时跑 Stage-1 + Stage-2 两
        # 个 LLM 调用，Stage-2 还开 thinking）。关闭后 evidence_score 不再变化，
        # confirmed/promoted 走 time-driven fallback。
        #
        # 关态推进 last_check_ts 到 now（同 rebuttal 处的理由）：避免重开后
        # 把关闭期间的所有 user msg 当成"积压"一次性塞进 Stage-1+Stage-2 prompt。
        if not await _ais_powerful_memory_enabled():
            try:
                character_data = await _config_manager.aload_characters()
                catgirl_names = list(character_data.get('猫娘', {}).keys())
                cursor_now = datetime.now()
                for name in catgirl_names:
                    try:
                        _signal_check_mark_done(name, cursor_now)
                    except Exception as cursor_e:
                        # 单角色 last_check_ts 推进失败不致命——同 rebuttal
                        # 处的理由，下一轮再试。
                        logger.debug(
                            f"[SignalLoop] {name}: 关态 cursor 推进失败: {cursor_e}"
                        )
            except Exception as e:
                logger.debug(f"[SignalLoop] 关态 cursor 推进 batch 失败: {e}")
            await asyncio.sleep(EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS)
            continue

        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[SignalLoop] 加载角色列表失败: {e}")
            await asyncio.sleep(EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS)
            continue

        now = datetime.now()

        async def _signal_check_one(name: str):
            """单角色的 Stage-1 + Stage-2 + signal dispatch。各角色互相
            独立（per-char event_log 锁 / 文件），外层 gather 并行。失败
            不阻塞其他角色，cursor 只在完整成功路径上推进。"""
            try:
                if not _signal_check_should_run(name, now):
                    return
                # 窗口起点：优先用上次成功 check 时戳（cursor 语义），避免
                # 长对话期间 >10 分钟的消息被永远 skip（§3.4.3 游标推进）。
                # 冷启动 / cursor 缺失时回退到 IDLE_MINUTES*2。
                start_time = _signal_check_window_start(name, now)
                rows = await time_manager.aretrieve_original_by_timeframe(
                    name, start_time, now,
                )
                if not rows:
                    _signal_check_mark_done(name, now)
                    return
                user_msgs_text = _extract_user_messages_from_rows(rows)
                if not user_msgs_text:
                    # 窗口里没 user msg —— 纯 proactive / AI 自言自语 / tool
                    # turn。这种内容**故意**不进 memory：
                    # 1. Path A 抽 user_observation fact 需要 user 发声当源
                    # 2. Path B 拣 AI 自我披露**也**只在 user 有 engagement
                    #    的窗口里跑（B 是 piggyback A，不是独立路径）
                    # 设计原则：用户不搭理 = 内容廉价层 ("90% 没心没肺"
                    # product thesis)，不该被自动当 fact 沉淀污染 memory。
                    # cursor 照常推进、计数清零，让下次有 user msg 的窗口
                    # 直接进入正常 A+B 流程。
                    _signal_check_mark_done(name, now)
                    return

                # 组装成 BaseMessage-like 结构给 extract_facts 使用
                from utils.llm_client import convert_to_messages
                message_dicts = [
                    {'type': 'human', 'data': {'content': m}}
                    for m in user_msgs_text
                ]
                # convert_to_messages 只接 list，不再解 JSON 字符串（PR #547 以来的契约）；
                # 这里之前的 json.dumps 让函数走 isinstance(data, list)==False 分支直接返回 []，
                # → messages=[] → _format_conversation render 出空字符串 → Stage-1 prompt
                # 里 ======以下为对话====== 跟 ======以上为对话====== 之间为空 → LLM 合理
                # 返回 []，整套 fact 抽取 + 后续 Stage-2 evidence 都被静默跳过。
                messages = convert_to_messages(message_dicts)

                try:
                    persisted, signals, batch_fact_ids = await fact_store.aextract_facts_and_detect_signals(
                        name, messages,
                        reflection_engine=reflection_engine,
                        persona_manager=persona_manager,
                    )
                except FactExtractionFailed as e:
                    # Stage-1 terminal failure — cursor NOT advanced, next
                    # cycle retries the same message window (§3.4.3)。
                    # Liveness 兜底：同一窗口反复失败 ≥ MEMORY_LIVENESS
                    # _MAX_ATTEMPTS 强推 cursor 到 now，避免毒窗口让
                    # fact pipeline 永久卡死。
                    state = _signal_check_state.setdefault(
                        name, {'turns_since': 0, 'last_check_ts': None},
                    )
                    # CodeRabbit: 用 start_time 当 key，不要字面 'cold'。
                    # 字面 'cold' 把所有冷启动多轮失败聚合到同一桶，
                    # 第 N 次会强推 cursor 到当时的 now，把那段时间内进来的
                    # 正常 msg 也跟着 dead-letter。改用 start_time（每轮
                    # window 起点）：有稳定 cursor 时 start_time == cursor
                    # （`_signal_check_window_start` 直接返 cursor），冷启动
                    # 时 start_time 是 `now - IDLE_MINUTES*2`，每轮不同 →
                    # 冷启动阶段不会错误聚合 dead-letter。
                    cursor_key = start_time.isoformat(timespec='microseconds')
                    if not _stage1_path_a_bump_failure(name, state, cursor_key, now):
                        logger.warning(
                            f"[SignalLoop] {name}: Stage-1 失败保留 cursor 重试: {e}"
                        )
                    return

                # 先 dispatch 再 mark_done：dispatch 中途有任何 aapply 失败
                # cursor 不推进，下轮 Stage-1 在同一窗口重新抽取（Stage-1
                # dedup 保证 fact 不会翻倍写入，Stage-2 会重新生成 signal
                # 再试一次）。CodeRabbit PR #929 fix：之前 dispatch 吞异常
                # 后 mark_done 仍跑，单次 aapply 失败会永久丢一条 evidence。
                dispatch_ok = True
                if signals:
                    dispatch_ok = await _adispatch_evidence_signals(
                        name, signals, source=EVIDENCE_SOURCE_USER_FACT,
                    )
                    logger.info(
                        f"[SignalLoop] {name}: dispatch {len(signals)} 个 evidence 信号"
                    )

                # Drain checkpoint：dispatch 全部成功（含 signals=[] 即 LLM
                # 看过没关联）才 mark batch processed。任何 aapply 失败保留
                # signal_processed=False 让下轮 idle 重试这批 fact，避免
                # 把没落地的 signal 永久跳过（CodeRabbit fingerprint c755101c）。
                if dispatch_ok and batch_fact_ids:
                    await fact_store.amark_signal_processed(name, batch_fact_ids)

                if not dispatch_ok:
                    logger.warning(
                        f"[SignalLoop] {name}: dispatch 有失败，保留 cursor 下轮重试"
                    )
                    return  # 保留 cursor（不调 _signal_check_mark_done）

                # 信号写完后触发一次 score-driven pending→confirmed 扫描；
                # 独立 try/except：本步失败不应阻止 cursor 推进（score 下
                # 轮会自然重算）。
                try:
                    await reflection_engine.aauto_promote_stale(name)
                except Exception as e:
                    logger.debug(f"[SignalLoop] {name}: auto_promote_stale 失败: {e}")

                # Stage-1 + dispatch 都跨过了，cursor 推进。
                _signal_check_mark_done(name, now)

                # 记录 A 实际处理过的最晚 msg ts，给 path B 当下游边界用
                # （rows 已 ORDER BY ts ASC，最后一行就是 window 内最晚 msg）。
                # 用真实 msg ts 而不是 wall-clock now：保证 path B 看到的
                # 消息严格被 path A 看过，避免"A scan SQL 完成那一刻之后才入
                # SQLite 的 msg 被 B 抢先处理"的 race。
                state = _signal_check_state.setdefault(
                    name, {'turns_since': 0, 'last_check_ts': None},
                )
                last_msg_ts = _coerce_db_ts(rows[-1][0])
                if last_msg_ts is not None:
                    state['last_a_msg_ts'] = last_msg_ts

                # Path B trigger：A 成功跑完后 bump counter；达 N 触发
                # _run_path_b（AI-aware Stage-1 only，详见函数 docstring）。
                state['b_tick_counter'] = state.get('b_tick_counter', 0) + 1
                if state['b_tick_counter'] >= EVIDENCE_AI_AWARE_EVERY_N_A_TICKS:
                    state['b_tick_counter'] = 0
                    try:
                        await _run_path_b(name, state)
                    except Exception as e:
                        # B 失败完全不应该影响 A 路径（A 已经在 mark_done 之
                        # 后了）；只 log warning。下次 b_tick_counter 又满 N
                        # 时 B 自动重试，cursor 是 last_b_check_ts 推进的，
                        # 失败时不推 cursor → 下次 B 重新覆盖同窗口。
                        logger.warning(
                            f"[PathB] {name}: AI-aware Stage-1 失败 (skip 本轮，下次 B trigger 重试): {e}"
                        )
            except Exception as e:
                logger.debug(f"[SignalLoop] {name}: 处理失败: {e}")

        if catgirl_names:
            await asyncio.gather(
                *(_signal_check_one(name) for name in catgirl_names),
                return_exceptions=True,
            )

        await asyncio.sleep(EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS)


# ── memory-evidence-rfc §3.4.5: negative-keyword hook helpers ───────

async def _amaybe_trigger_negative_keyword_hook(
    lanlan_name: str, user_messages: list[str], lang: str,
) -> None:
    """If any user message hits NEGATIVE_KEYWORDS_I18N, fire the async LLM
    target-check and dispatch disputation signals. Non-blocking for the
    calling conversation path."""
    if not user_messages:
        return
    hit = any(scan_negative_keywords(m, lang) for m in user_messages)
    if not hit:
        return

    # Assemble observation pool (§3.4.5 prompt inputs)
    try:
        observations = await fact_store._aload_signal_targets(
            lanlan_name,
            reflection_engine=reflection_engine,
            persona_manager=persona_manager,
        )
    except Exception as e:
        logger.debug(f"[NegKW] {lanlan_name}: 观察集加载失败: {e}")
        return
    if not observations:
        return

    from config import (
        NEGATIVE_KEYWORD_CHECK_CONTEXT_ITEMS,
        EVIDENCE_PER_OBSERVATION_MAX_TOKENS,
        EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS,
    )
    from utils.tokenize import truncate_to_tokens
    user_msg_text = "\n".join(user_messages[-NEGATIVE_KEYWORD_CHECK_CONTEXT_ITEMS:])
    obs_text = "\n".join(
        f"[{o['id']}] {truncate_to_tokens(o.get('text', '') or '', EVIDENCE_PER_OBSERVATION_MAX_TOKENS)}"
        for o in observations
    )
    obs_text = truncate_to_tokens(obs_text, EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS)
    prompt = get_negative_target_check_prompt(lang) \
        .replace('{USER_MESSAGES}', user_msg_text) \
        .replace('{OBSERVATIONS}', obs_text)

    parsed = await fact_store._allm_call_with_retries(
        prompt, lanlan_name,
        tier=EVIDENCE_NEGATIVE_TARGET_MODEL_TIER,
        call_type="memory_negative_target_check",
        max_retries=2,
    )
    if parsed is None or not isinstance(parsed, dict):
        return
    targets = parsed.get('targets', [])
    if not isinstance(targets, list) or not targets:
        return

    # Validate + dispatch (same defensive filter as Stage-2)
    valid_ids = {o['id']: o for o in observations}
    signals: list[dict] = []
    for t in targets:
        if not isinstance(t, dict):
            continue
        tid = t.get('target_id')
        if not tid:
            continue
        # Accept raw or prefixed id
        full_id = tid if tid in valid_ids else next(
            (vid for vid in valid_ids if vid.endswith(f".{tid}")), None,
        )
        if full_id is None:
            logger.warning(f"[NegKW] {lanlan_name}: 未知 target_id={tid}, 丢弃")
            continue
        obs = valid_ids[full_id]
        signals.append({
            'signal': 'negates',
            'target_type': obs['target_type'],
            'target_id': obs['raw_id'],
            'entity_key': obs.get('entity_key'),
        })

    if signals:
        # Negative-keyword hook is inline with conversation turn — no cursor
        # to preserve on dispatch failure; best-effort fire-and-forget.
        await _adispatch_evidence_signals(
            lanlan_name, signals, source=EVIDENCE_SOURCE_USER_KEYWORD_REBUT,
        )
        logger.info(
            f"[NegKW] {lanlan_name}: 关键词触发 {len(signals)} 个 disputation 信号"
        )


# ── Phase A-4 / A-5: MemoryRefineEngine 接 cron ─────────────────────


async def _run_persona_refine_for_character(character: str) -> None:
    """单角色 persona refine pass。embedding 不可用 / cluster_hash 全
    skip / 候选不足 → 整 pass no-op。"""
    from config import (
        MEMORY_LIVENESS_MAX_ATTEMPTS,
        MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
    )
    from memory.facts import safe_int_field
    from memory.temporal import cooldown_elapsed
    from memory.refine import (
        MemoryRefineEngine,
        REFINE_ENTITY_KEY,
        annotate_entry,
    )

    pm = persona_manager
    if pm is None:
        return
    persona = await pm.aensure_persona(character)
    candidates_by_entity: dict[str, list[dict]] = {}
    for entity in ('master', 'neko', 'relationship'):
        section = pm._get_section_facts(persona, entity)
        # Liveness 过滤：refine_attempts ≥ MEMORY_LIVENESS_MAX_ATTEMPTS 的
        # entry 不再进 cluster gather。Site 4 dead-letter——同 entry 在多
        # cluster 反复 LLM 失败后被 frozen，避免持续占用 starvation-first
        # ordering 名额空跑 LLM。recovery 路径：apply_refine_actions 在
        # stamp 成功时会清回 0；或人工编辑 persona.json；或时间自愈——
        # 冻结后过 MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS 放行一次 probe，让
        # 一次性 correction 模型宕机恢复后自愈（不再永久冻死无辜 entry）。
        entries = [
            annotate_entry(e, type_='persona', entity=entity)
            for e in section
            if isinstance(e, dict)
            and not e.get('protected')
            and e.get('id')
            and (
                safe_int_field(e, 'refine_attempts') < MEMORY_LIVENESS_MAX_ATTEMPTS
                or cooldown_elapsed(
                    e.get('last_refine_attempt_at'),
                    MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
                )
            )
        ]
        if entries:
            candidates_by_entity[entity] = entries
    if not candidates_by_entity:
        return

    engine = MemoryRefineEngine(_config_manager)

    async def _apply(cluster, actions, cluster_hash):
        # cluster 内成员同 entity（engine 强制），从第一个非空成员读
        ent = next(
            (e.get(REFINE_ENTITY_KEY) for e in cluster
             if isinstance(e, dict) and e.get(REFINE_ENTITY_KEY)),
            'master',
        )
        await pm.apply_refine_actions(character, ent, cluster, actions, cluster_hash)

    async def _failure(cluster, cluster_hash):
        await pm._abump_refine_attempts(character, cluster, cluster_hash)

    result = await engine.refine_pass(
        candidates_by_entity,
        apply_fn=_apply,
        scope_label=f"persona/{character}",
        failure_fn=_failure,
    )
    if result['clusters_resolved'] or result['clusters_failed']:
        logger.info(
            f"[PersonaRefine] {character}: seen={result['clusters_seen']}, "
            f"skipped={result['clusters_skipped']}, "
            f"resolved={result['clusters_resolved']}, "
            f"failed={result['clusters_failed']}"
        )


async def _periodic_persona_refine_loop():
    """每 N 秒对每个角色跑一轮 PERSONA_REFINE。

    embedding 服务关 / powerful memory 关 → no-op；engine 内 cluster_hash
    skip 让"刚审过"的 cluster 零成本跳过，所以高频触发不会浪费 LLM
    token。初始 delay 错峰 reflection refine 100s。"""
    await asyncio.sleep(_INITIAL_DELAY_PERSONA_REFINE)
    interval = MEMORY_REFINE_CRON_INTERVAL_SECONDS
    while True:
        if not await _ais_powerful_memory_enabled():
            await asyncio.sleep(interval)
            continue
        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[PersonaRefine] 加载角色列表失败: {e}")
            await asyncio.sleep(interval)
            continue
        for name in catgirl_names:
            try:
                await _run_persona_refine_for_character(name)
            except Exception as e:
                logger.warning(f"[PersonaRefine] {name} cron 异常: {e}")
        await asyncio.sleep(interval)


async def _run_reflection_refine_for_character(character: str) -> None:
    """单角色 reflection refine pass。cluster 内可混入同 entity 的
    absorbed fact 作只读信息源（fact 不可被 split/discard/modify，apply
    层代码兜底）。"""
    from config import (
        MEMORY_LIVENESS_MAX_ATTEMPTS,
        MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
    )
    from memory.facts import safe_int_field
    from memory.temporal import cooldown_elapsed
    from memory.refine import (
        MemoryRefineEngine,
        REFINE_ENTITY_KEY,
        annotate_entry,
    )

    # 用 `engine_ref` 而不是 `re` —— 后者遮蔽 Python 内置 `re` 模块
    # （CodeRabbit nitpick #1392）。
    engine_ref = reflection_engine
    fs = fact_store
    if engine_ref is None or fs is None:
        return

    refls = await engine_ref.aload_reflections(character, include_archived=False)
    if not refls:
        return
    facts = await fs.aload_facts(character)

    candidates_by_entity: dict[str, list[dict]] = {}
    for entity in ('master', 'neko', 'relationship'):
        # Liveness 过滤：refine_attempts ≥ MEMORY_LIVENESS_MAX_ATTEMPTS 的
        # reflection 不再进 cluster gather（同 persona refine）。fact 不算
        # ——fact 是 readonly 信息源，不会被 refine 改，自然不会 bump
        # attempts。时间自愈：冻结后过 MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS
        # 放行一次 probe，让一次性宕机恢复后自愈。
        entity_refls = [
            annotate_entry(r, type_='reflection', entity=entity)
            for r in refls
            if isinstance(r, dict)
            and r.get('entity') == entity
            and r.get('id')
            and (
                safe_int_field(r, 'refine_attempts') < MEMORY_LIVENESS_MAX_ATTEMPTS
                or cooldown_elapsed(
                    r.get('last_refine_attempt_at'),
                    MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
                )
            )
        ]
        entity_facts = [
            annotate_entry(f, type_='fact', entity=entity)
            for f in facts
            if isinstance(f, dict) and f.get('entity') == entity
            and f.get('absorbed') and f.get('id')
        ]
        if entity_refls:  # 至少要有 reflection；fact 是只读补料
            candidates_by_entity[entity] = entity_refls + entity_facts
    if not candidates_by_entity:
        return

    engine = MemoryRefineEngine(_config_manager)

    async def _apply(cluster, actions, cluster_hash):
        ent = next(
            (e.get(REFINE_ENTITY_KEY) for e in cluster
             if isinstance(e, dict) and e.get(REFINE_ENTITY_KEY)),
            'master',
        )
        await engine_ref.apply_refine_actions(character, ent, cluster, actions, cluster_hash)

    async def _failure(cluster, cluster_hash):
        await engine_ref._abump_refine_attempts(character, cluster, cluster_hash)

    result = await engine.refine_pass(
        candidates_by_entity,
        apply_fn=_apply,
        scope_label=f"reflection/{character}",
        failure_fn=_failure,
    )
    if result['clusters_resolved'] or result['clusters_failed']:
        logger.info(
            f"[ReflectionRefine] {character}: seen={result['clusters_seen']}, "
            f"skipped={result['clusters_skipped']}, "
            f"resolved={result['clusters_resolved']}, "
            f"failed={result['clusters_failed']}"
        )


async def _periodic_reflection_refine_loop():
    """每 N 秒对每个角色跑一轮 REFLECTION_REFINE。candidate pool 包含
    active reflection + 同 entity 的 absorbed fact（fact 只读）。"""
    await asyncio.sleep(_INITIAL_DELAY_REFLECTION_REFINE)
    interval = MEMORY_REFINE_CRON_INTERVAL_SECONDS
    while True:
        if not await _ais_powerful_memory_enabled():
            await asyncio.sleep(interval)
            continue
        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[ReflectionRefine] 加载角色列表失败: {e}")
            await asyncio.sleep(interval)
            continue
        for name in catgirl_names:
            try:
                await _run_reflection_refine_for_character(name)
            except Exception as e:
                logger.warning(f"[ReflectionRefine] {name} cron 异常: {e}")
        await asyncio.sleep(interval)


async def _periodic_reflection_synthesis_loop():
    """每 N 秒对每个角色跑一轮 reflection 合成。

    与其他 9 条 ``_periodic_*_loop`` 对偶——signal_extraction 把对话抽成 fact，
    本循环把 unabsorbed fact 综合成 pending reflection，auto_promote_loop 再把
    pending 推到 confirmed/promoted。整条链路全部在 memory_server 进程内长跑，
    不依赖 ``/api/proactive_chat`` HTTP trigger（也就不再依赖前端浏览器开着）。

    历史背景（为啥要这条循环）：reflection 合成原本只挂在
    ``main_routers/system_router.py`` 的 proactive_chat handler 里
    （``_mem_client.post('/reflect/{name}')``，PR #1015 顺手塞的），导致：
      - 前端关 / proactive 不触发 / 任一 frontend gate false → ``/reflect``
        永不被调 → ``reflections.json`` 永不增长
      - reflection 生命周期实际上跟前端 setTimeout 强耦合，违背"长跑后端服务
        自己保证记忆生态"的设计意图

    Gate 全靠 ``reflection_engine.synthesize_reflections`` 内置：
      - ``len(unabsorbed) < MIN_FACTS_FOR_REFLECTION (=5)`` → 直接返回 []
      - 同批 source_fact_ids → 同 rid 幂等 short-circuit，无新 fact 时 LLM 不调
      - ``REFLECTION_SYNTHESIS_FACTS_MAX (=20)`` cap 单次输入规模
    所以本循环只做调度，不重复加 gate；间隔常量
    ``MEMORY_REFLECTION_SYNTHESIS_INTERVAL_SECONDS`` 控制最大调用频率。

    与 powerful_memory 开关：synthesize_reflections 不在 evidence-RFC 引入的
    新 LLM 路径里——它是 RFC 之前就存在的合成机制（pending reflection 是 RFC
    前就有的，evidence 只是给它做了状态推进），所以**不**受 powerful_memory 关
    闭影响。这跟 refine / signal_extraction 不一样，对齐 idle_maintenance 里
    "history 压缩 / review" 那两个子任务的处理。
    """
    await asyncio.sleep(_INITIAL_DELAY_REFLECTION_SYNTHESIS)
    interval = MEMORY_REFLECTION_SYNTHESIS_INTERVAL_SECONDS
    while True:
        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[ReflectionSynth] 加载角色列表失败: {e}")
            await asyncio.sleep(interval)
            continue
        for name in catgirl_names:
            try:
                results = await reflection_engine.synthesize_reflections(name)
                if results:
                    logger.info(
                        f"[ReflectionSynth] {name}: 合成 {len(results)} 条新 pending reflection"
                    )
            except Exception as e:
                # 单角色合成失败不阻塞其他角色 / 下轮重试
                logger.warning(f"[ReflectionSynth] {name} 合成异常: {e}")
        await asyncio.sleep(interval)


async def _bootstrap_embedding_worker() -> None:
    """ready 后在后台 bootstrap 向量预热 / 去重 worker。

    重 import（``memory.embedding_worker`` 拉起 embedding 栈 ~0.6s）和服务构造
    （``get_embedding_service()`` 可能 probe/load 模型）全程在 ``to_thread`` 里跑，
    不阻塞 event loop；``start()`` 是轻量的（只 ``create_task``），回到 loop 上调。
    worker 自带 warmup 延迟、向量不可用也会降级，所以从 memory 启动关键路径移出来
    对 greeting 零影响。

    ⚠️ 故意**不**把 manager 作为参数传入：worker 的 getter（``lambda: persona_manager``
    等）必须解析到模块全局，这样 /reload 重绑全局后下一轮 sweep 能看到新实例。
    传参会让闭包捕获启动期的旧实例，绕过 worker 设计的 reload-staleness 防护。
    """
    global embedding_warmup_worker, fact_dedup_resolver
    try:
        def _build():
            from memory.embedding_worker import EmbeddingWarmupWorker
            from memory.fact_dedup import FactDedupResolver
            from config import VECTORS_WARMUP_DELAY_SECONDS

            def _current_catgirl_names() -> list[str]:
                try:
                    data = _config_manager.load_characters()
                    return list((data or {}).get('猫娘', {}).keys())
                except Exception:
                    return []

            bound_fact_store = fact_store
            resolver = FactDedupResolver(bound_fact_store)
            worker = EmbeddingWarmupWorker(
                get_persona_manager=lambda: persona_manager,
                get_reflection_engine=lambda: reflection_engine,
                get_fact_store=lambda: fact_store,
                get_character_names=_current_catgirl_names,
                warmup_delay_seconds=VECTORS_WARMUP_DELAY_SECONDS,
                get_dedup_resolver=lambda: fact_dedup_resolver,
            )
            return worker, resolver, bound_fact_store

        worker, resolver, bound_fact_store = await asyncio.to_thread(_build)
        # worker 用 getter 读全局，天然 reload-safe，直接发布。
        embedding_warmup_worker = worker
        # 但 resolver 是绑定到具体 fact_store 的实例：若 await（重 import + 构造）期间
        # reload_memory_components() 换了 fact_store 并重绑了 fact_dedup_resolver，
        # 这里再无条件赋值会用绑旧 store 的 resolver 覆盖掉 reload 的新 resolver，
        # 导致 worker 的 get_fact_store 读新 store、get_dedup_resolver 读旧 resolver 错配。
        # 因此只在当前全局 fact_store 仍是 resolver 绑定的那个时才发布。
        if fact_store is bound_fact_store:
            fact_dedup_resolver = resolver
        else:
            logger.info("[Memory] embedding worker bootstrap 与 reload 竞争，沿用 reload 已重绑的 fact_dedup_resolver")
        embedding_warmup_worker.start()
    except Exception as e:
        logger.warning(f"[Memory] embedding worker bootstrap failed: {e}")
        embedding_warmup_worker = None
        # 不清 fact_dedup_resolver：若 await 期间 reload 已重绑了一个绑定新 store 的
        # resolver，这里清成 None 会把 reload 的成果抹掉。bootstrap 失败本就只代表
        # "没有 warmup worker"，resolver 该保留（None 维持原样，reload 设的则保留）。


async def ensure_memory_server_runtime_initialized(*, reason: str = "") -> bool:
    global recent_history_manager, settings_manager, time_manager, fact_store
    global persona_manager, reflection_engine, cursor_store, outbox, event_log, reconciler
    global embedding_warmup_worker, fact_dedup_resolver
    global _memory_runtime_init_completed, _memory_background_tasks_started

    if _memory_runtime_init_completed:
        return False

    async with _memory_runtime_init_lock:
        if _memory_runtime_init_completed:
            return False

        bootstrap_ok = False
        if is_cloudsave_disabled():
            logger.warning("[Memory] 跳过 cloudsave 环境 bootstrap：cloudsave 已为本次会话禁用")
        else:
            try:
                bootstrap_local_cloudsave_environment(_config_manager)
                bootstrap_ok = True
            except Exception as e:
                logger.warning(f"[Memory] cloudsave 环境 bootstrap 失败，后续 cloudsave 相关操作可能降级: {e}")

        try:
            from memory import migrate_to_character_dirs

            _config_manager.ensure_memory_directory()
            _char_data = await _config_manager.aload_characters()
            _catgirl_names = list(_char_data.get('猫娘', {}).keys())
            await asyncio.to_thread(migrate_to_character_dirs, _config_manager.memory_dir, _catgirl_names)
        except Exception as _e:
            logger.warning(f"[Memory] 目录迁移失败: {_e}")

        recent_history_manager = CompressedRecentHistoryManager()
        settings_manager = ImportantSettingsManager()
        time_manager = TimeIndexedMemory(recent_history_manager)
        fact_store = FactStore(time_indexed_memory=time_manager)
        event_log = EventLog()
        persona_manager = PersonaManager(event_log=event_log)
        reflection_engine = ReflectionEngine(fact_store, persona_manager, event_log=event_log)
        cursor_store = CursorStore()
        outbox = Outbox()
        reconciler = Reconciler(event_log)
        _register_evidence_handlers(reconciler, persona_manager, reflection_engine)

        try:
            from utils.token_tracker import TokenTracker, install_hooks

            install_hooks()
            TokenTracker.get_instance().start_periodic_save()
            # process 字段进 session_start / session_end 维度，跨进程诊断必须区分
            TokenTracker.get_instance().record_app_start(process="memory_server")
        except Exception as e:
            logger.warning(f"[Memory] Token tracker init failed: {e}")

        await _aload_maint_state()

        catgirl_names: list[str] = []
        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
            if catgirl_names:
                results = await asyncio.gather(
                    *(persona_manager.aensure_persona(n) for n in catgirl_names),
                    return_exceptions=True,
                )
                for name, result in zip(catgirl_names, results):
                    if isinstance(result, Exception):
                        logger.warning(
                            f"[Memory] Persona 迁移检查失败: {name}: {result}",
                            exc_info=result,
                        )
            logger.info(f"[Memory] Persona 迁移检查完成，角色数: {len(catgirl_names)}")
        except Exception as e:
            logger.warning(f"[Memory] Persona 迁移检查失败: {e}")

        try:
            await _replay_pending_outbox()
        except Exception as e:
            logger.warning(f"[Outbox] 启动补跑顶层失败: {e}")

        async def _reconcile_one(n: str):
            try:
                applied = await reconciler.areconcile(n)
                if applied:
                    logger.info(f"[Memory] reconciler {n}: 重放 {applied} 条事件")
            except Exception as e:
                logger.warning(f"[Memory] reconciler {n} replay 失败: {e}")

        if catgirl_names:
            await asyncio.gather(
                *(_reconcile_one(n) for n in catgirl_names),
                return_exceptions=True,
            )

        async def _migrate_one(n: str):
            try:
                await _aone_shot_migration_if_needed(n)
            except Exception as e:
                logger.warning(f"[Memory] {n} evidence 迁移失败: {e}")
            try:
                await _aone_shot_archive_migration_if_needed(n)
            except Exception as e:
                logger.warning(f"[Memory] {n} archive 迁移失败: {e}")

        if catgirl_names:
            await asyncio.gather(
                *(_migrate_one(n) for n in catgirl_names),
                return_exceptions=True,
            )

        if bootstrap_ok:
            current_root_state = _config_manager.load_root_state()
            if should_write_root_mode_normal_after_startup(current_root_state):
                try:
                    set_root_mode(
                        _config_manager,
                        ROOT_MODE_NORMAL,
                        current_root=str(_config_manager.app_docs_dir),
                        last_known_good_root=str(_config_manager.app_docs_dir),
                        last_successful_boot_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    )
                except Exception as e:
                    logger.warning(f"[Memory] 写入启动成功标记失败: {e}")
            else:
                logger.info(
                    "[Memory] 跳过 ROOT_MODE_NORMAL 写入，当前仍处于阻断态: %s",
                    current_root_state.get("mode") or ROOT_MODE_NORMAL,
                )
        else:
            logger.warning("[Memory] 跳过 ROOT_MODE_NORMAL 写入：cloudsave bootstrap 未成功")

        if not _memory_background_tasks_started:
            _spawn_background_task(_periodic_rebuttal_loop())
            _spawn_background_task(_periodic_auto_promote_loop())
            _spawn_background_task(_periodic_idle_maintenance_loop())
            if EVIDENCE_SIGNAL_CHECK_ENABLED:
                _spawn_background_task(_periodic_signal_extraction_loop())
            _spawn_background_task(_periodic_archive_sweep_loop())
            _spawn_background_task(_periodic_new_dialog_qps_log_loop())
            if MEMORY_RECHECK_ENABLED:
                _spawn_background_task(_periodic_slow_memory_recheck_loop())
            # Phase A-4 / A-5: MemoryRefineEngine cron 接入
            _spawn_background_task(_periodic_persona_refine_loop())
            _spawn_background_task(_periodic_reflection_refine_loop())
            _spawn_background_task(_periodic_reflection_synthesis_loop())
            _memory_background_tasks_started = True

        # memory-enhancements P2: vector embedding warmup + backfill worker.
        # 这块的 import（embedding 栈 ~0.6s）+ 服务构造原本同步跑在 startup
        # handler 里，uvicorn 要等 handler 返回才开端口，于是把 memory 端口
        # 就绪足足推后 ~1.3s（合并单进程下又被串行放大）。worker 本身是可选的、
        # 自带 warmup 延迟，greeting 不依赖向量——所以挪到后台 task，重活全程
        # 在 to_thread 里跑，绝不阻塞 event loop / 拖慢端口就绪。
        _spawn_background_task(_bootstrap_embedding_worker())

        _memory_runtime_init_completed = True
        logger.info("[Memory] 运行态初始化完成 (reason=%s)", reason or "manual")
        return True


@app.on_event("startup")
async def startup_event_handler():
    """应用启动时初始化"""
    blocking_reason = get_storage_startup_blocking_reason(_config_manager)
    if blocking_reason:
        logger.info(
            "[Memory] 检测到存储启动阻断态，先保持 limited-mode，等待网页端放行: %s",
            blocking_reason,
        )
        return

    await ensure_memory_server_runtime_initialized(reason="startup")


@app.post("/internal/storage/startup/continue")
async def continue_storage_startup(payload: ContinueStorageStartupRequest | None = None):
    global _memory_storage_blocked_after_init
    blocking_reason = get_storage_startup_blocking_reason(_config_manager)
    if blocking_reason:
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error_code": "storage_startup_blocked",
                "blocking_reason": blocking_reason,
                "error": "当前存储状态仍需选择、迁移或恢复，暂时不能释放 memory server 启动闸门。",
            },
        )

    try:
        initialized = await ensure_memory_server_runtime_initialized(
            reason=str(getattr(payload, "reason", "") or "storage_selection_continue_current_session"),
        )
        _memory_storage_blocked_after_init = False
        return {
            "ok": True,
            "initialized": bool(initialized),
        }
    except Exception as e:
        logger.error(f"[Memory] 释放 limited-mode 启动失败: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(e),
            },
        )


@app.post("/internal/storage/startup/block")
async def block_storage_startup(payload: ContinueStorageStartupRequest | None = None):
    global _memory_storage_blocked_after_init
    reason = str(getattr(payload, "reason", "") or "").strip()
    _memory_storage_blocked_after_init = True
    logger.warning("[Memory] limited-mode restored after main_server startup failure: %s", reason or "-")
    return {
        "ok": True,
        "limited_mode": True,
        "reason": reason,
    }


@app.post("/internal/memory/reset_confirmed_at")
async def internal_reset_confirmed_at():
    """强力记忆 ON→OFF migration：重置所有角色 confirmed reflection 的
    confirmed_at 锚点到 now。

    main_routers/memory_router.py 通过 HTTP 触发本端点——helper
    ``_reset_confirmed_at_for_all_characters`` 依赖本进程内的
    ``reflection_engine`` 全局，必须在 memory_server 进程跑才能拿到正确的
    实例（main_server 进程虽然能 import memory_server 模块，但那是个 fresh
    副本，``reflection_engine`` 是 None，调用会成 no-op）。
    """
    try:
        count = await _reset_confirmed_at_for_all_characters()
        return {"ok": True, "count": count}
    except Exception as e:
        logger.warning(f"[Memory] reset_confirmed_at migration 失败: {e}")
        return {"ok": False, "error": str(e), "count": 0}


@app.on_event("shutdown")
async def shutdown_event_handler():
    """应用关闭时执行清理工作"""
    logger.info("Memory server正在关闭...")
    try:
        from utils.token_tracker import TokenTracker
        TokenTracker.get_instance().save()
    except Exception:
        pass
    # P2 vector worker: kick off stop() as a task before we touch the
    # reload lock so its bounded 2s wait overlaps with manager cleanup
    # below instead of serializing in front of it.
    worker_stop_task: asyncio.Task | None = None
    if embedding_warmup_worker is not None:
        worker_stop_task = asyncio.create_task(embedding_warmup_worker.stop())

    managers_to_cleanup: list[TimeIndexedMemory] = []
    async with _reload_lock:
        managers_to_cleanup.extend(_deferred_time_managers)
        _deferred_time_managers.clear()
        # time_manager 在 startup 钩子里才实例化；若启动过程中就触发 shutdown 可能为 None
        if time_manager is not None and all(existing is not time_manager for existing in managers_to_cleanup):
            managers_to_cleanup.append(time_manager)

    async def _cleanup_one(m: TimeIndexedMemory) -> None:
        try:
            await asyncio.to_thread(m.cleanup)
        except Exception as cleanup_exc:
            logger.warning("[MemoryServer] 延迟释放 SQLite 引擎失败: %s", cleanup_exc)

    async def _await_worker_stop() -> None:
        try:
            await worker_stop_task  # type: ignore[arg-type]
        except Exception as e:
            logger.warning(f"[Memory] embedding worker stop 失败: {e}")

    shutdown_coros: list = [_cleanup_one(m) for m in managers_to_cleanup]
    if worker_stop_task is not None:
        shutdown_coros.append(_await_worker_stop())
    if shutdown_coros:
        await asyncio.gather(*shutdown_coros)
    logger.info("Memory server已关闭")


def _get_review_spawn_lock(name: str) -> asyncio.Lock:
    """惰性 per-name asyncio.Lock，串行化 gate+spawn 检查。"""
    lock = _review_spawn_locks.get(name)
    if lock is None:
        lock = asyncio.Lock()
        _review_spawn_locks[name] = lock
    return lock


def _count_new_user_msgs_since_last_review(name: str, current_history: list) -> float:
    """数自上次 review cutoff 起 history 里的 user msg 数。

    白 review（fingerprint=None）→ 视为足够多放行。
    fingerprint 在 current 里找不到（被压缩 / 清空）→ 同样视为足够多放行
    （应当尽快重 review 重建 fingerprint）。
    """
    from memory.recent import _find_fingerprint_position
    fp = _maint_state.get(name, {}).get('last_reviewed_cutoff_tail')
    if not fp:
        return float('inf')
    cutoff_idx = _find_fingerprint_position(current_history, fp)
    if cutoff_idx is None:
        return float('inf')
    return sum(
        1 for m in current_history[cutoff_idx + 1:]
        if getattr(m, 'type', '') == 'human'
    )


async def maybe_spawn_review(name: str) -> None:
    """统一 review 触发入口（Phase C）。

    /process /renew /settle / IdleMaint 都调这一个函数。本身**不**取消任何
    在跑的 review——看到 in-flight 直接 skip 本次 spawn。由 spawn 锁串行化
    gate+spawn 防多入口竞态。

    Gates（任一不过都 skip）：
    1. 已有 review 在跑（in-flight）
    2. ``review_enabled``（``recent_memory_auto_review`` flag）
    3. 历史长度 < ``REVIEW_SKIP_HISTORY_LEN``
    4. 距上次 review 完成 < ``REVIEW_MIN_INTERVAL``
    5. 自上次 review cutoff 起累积 user msg < ``MIN_NEW_MSGS_FOR_REVIEW``
    """
    async with _get_review_spawn_lock(name):
        # Gate 1: in-flight
        existing = correction_tasks.get(name)
        if existing is not None and not existing.done():
            return
        # Gate 2: review_enabled
        if not await _ais_review_enabled():
            return
        # 拉 history（gate 3/5 + 后续做 snapshot 都需要）
        try:
            history = await recent_history_manager.aget_recent_history(name)
        except Exception as e:
            logger.debug(f"[Review/spawn] {name}: 拉 history 失败: {e}")
            return
        # Gate 3: history 长度
        if len(history) < REVIEW_SKIP_HISTORY_LEN:
            return
        # Gate 4: min interval
        last_review = _maint_state.get(name, {}).get('last_review_ts')
        if last_review:
            try:
                elapsed = (datetime.now() - datetime.fromisoformat(last_review)).total_seconds()
                effective_min = REVIEW_MIN_INTERVAL
                if elapsed < effective_min:
                    return
            except (ValueError, TypeError):
                # last_review_ts 格式损坏（旧版本字段 / 手改文件 / 编码错误）→
                # 视为"从未 review 过"，不阻塞触发；继续走 gate 5（新消息门）。
                # 下次 review 成功后会用合法 ISO 字符串覆写。
                pass
        # Gate 5: 够多新 user 消息（含长挂机 bypass）
        new_msg_count = _count_new_user_msgs_since_last_review(name, history)
        if new_msg_count < MIN_NEW_MSGS_FOR_REVIEW:
            # 长挂机 bypass：≥1 条未 review 的新消息且全局静默 ≥ 30 min →
            # 允许凑不够批量的尾巴也跑一次 review。否则用户挂机一夜回来发现
            # console 里前一晚的零散对话永远停在"差几条不够触发"。
            idle_secs = (datetime.now() - _last_activity_time).total_seconds()
            if not (new_msg_count >= 1 and idle_secs >= LONG_IDLE_REVIEW_BYPASS_SECONDS):
                return
            logger.info(
                f"[Review/spawn] {name}: 长挂机 bypass MIN_NEW_MSGS_FOR_REVIEW "
                f"(new_msgs={new_msg_count}, idle={idle_secs:.0f}s)"
            )
        # Gate 6: 失败退避（dead-letter）。review 连续失败 ≥
        # MEMORY_LIVENESS_MAX_ATTEMPTS 次且**输入未变**（当前 history 末尾 K 条
        # fingerprint == 上次失败时记下的）→ 跳过本次 spawn，不再每轮空烧
        # 3×110s 超时。输入一变（master 发了新消息，尾部 fingerprint 变）→ 视为
        # 新输入，清掉失败计数放行重试。
        # 必须放在 Gate 5 之后：长挂机 bypass 在 correction 模型持续超时时会
        # 主动给死循环续命，本闸门要能压过它（用户审计 #1：实锤的整夜无限重烧）。
        from config import MEMORY_LIVENESS_MAX_ATTEMPTS
        from memory.recent import build_review_fingerprint
        state = _maint_state.setdefault(name, {})
        fail_attempts = state.get('review_fail_attempts', 0) or 0
        if fail_attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS:
            cur_fp = build_review_fingerprint(history)
            if state.get('review_fail_fp') == cur_fp:
                logger.debug(
                    f"[Review/spawn] {name}: 失败退避 dead-letter "
                    f"(连续失败 {fail_attempts} 次 ≥ {MEMORY_LIVENESS_MAX_ATTEMPTS} "
                    f"且输入未变)，跳过本轮"
                )
                return
            # 输入已变 → 旧失败计数过期，复位后放行重试
            state['review_fail_attempts'] = 0
            state['review_fail_fp'] = None
            await _asave_maint_state()
        # 全过 → spawn
        logger.info(f"[Review/spawn] {name}: 触发 review (history_len={len(history)})")
        cancel_event = asyncio.Event()
        correction_cancel_flags[name] = cancel_event
        snapshot = list(history)  # 浅拷贝即可，消息对象不可变
        # 把 cancel_event 显式传给后台 task（不再依靠 finally 时再从 dict 拿），
        # 这样 task 自己持有的 event 引用不会被并发的新 spawn 覆盖。
        task = asyncio.create_task(_run_review_in_background(name, snapshot, cancel_event))
        correction_tasks[name] = task


async def _record_review_failure(lanlan_name: str, snapshot: list) -> int:
    """记一次 review 失败到失败退避计数（Gate 6 用），返回累计次数。

    输入 fingerprint 与上次失败记录不同 → 先把预算归零再 +1，让每段
    history tail 各享独立的 N 次预算，不跨输入累积（Codex P2）。'failed'
    返回分支和 except 异常分支共用本函数，避免两处逻辑漂移。
    """
    from memory.recent import build_review_fingerprint
    state = _maint_state.setdefault(lanlan_name, {})
    cur_fp = build_review_fingerprint(snapshot)
    if state.get('review_fail_fp') != cur_fp:
        state['review_fail_attempts'] = 0
    state['review_fail_attempts'] = (state.get('review_fail_attempts', 0) or 0) + 1
    state['review_fail_fp'] = cur_fp
    await _asave_maint_state()
    return state['review_fail_attempts']


async def _run_review_in_background(
    lanlan_name: str, snapshot: list, cancel_event: asyncio.Event,
):
    """在后台运行 review_history，支持取消。

    Phase C 改动：
    - snapshot + cancel_event 由 caller 拍下传入（task 自己持有引用）
    - review_history 返回 (status, fingerprint) tuple：
        ('patched', new_fp) → 成功 patch；new_fp 是 patch 后 new_history 末尾
                              的 K 条 fingerprint，**必须**用这个新 fingerprint
                              （review 可能改写过末尾 K 条里的任一条，
                              ``build_review_fingerprint(snapshot)`` 是旧的）
        ('white', None)    → cutoff 失配 / 整段丢弃
        ('failed', None)   → LLM 失败 / 被取消 / 格式错误

    白 review 处理（CodeRabbit Issue #1 修复）：
    - **不**更新 last_review_ts → 下轮 gate 4 视为"距上次 review 时间已久"
      → 配合 fingerprint=None → MIN_NEW_MSGS gate 视为 ∞ → 下次 /process
      立即重 review，重建锚点。这才符合"白 review = 锚点丢失，应尽快重建"
      的用户原意。

    清理（CodeRabbit Issue #2 修复）：
    - finally 按 task/event 身份比对再 pop/clear，避免并发新 spawn 写入的
      条目被误删。理论上 spawn lock + asyncio finally 同步语义已经排除了
      race，但身份检查是廉价的防御。
    """
    try:
        # 只把 review_history 调用本身包进内层 try：它抛异常才算"review 失败"，
        # 收口成 ('failed', None) 走下面统一的失败分支记一次退避。成功后的 result
        # 处理 / state 落盘异常**不**能被当成 review 失败（否则 patched/white 的
        # save 抖动会误判成失败、误触 Gate 6 dead-letter；'failed' 分支自己 save
        # 抛异常也会被重复记一次）——那类异常交给外层 except 纯兜底、不 bump。
        # 注：asyncio.CancelledError 是 BaseException，不被 except Exception 捕获，
        # 会正常冒泡到外层 CancelledError 分支。
        try:
            result = await recent_history_manager.review_history(
                lanlan_name, snapshot, cancel_event=cancel_event,
            )
        except Exception as e:
            logger.error(f"❌ {lanlan_name} 的 review_history 抛异常，按失败处理: {e}")
            result = ('failed', None)
        # 兼容意外的返回类型，统一解包
        if isinstance(result, tuple) and len(result) == 2:
            status, fingerprint = result
        else:
            status, fingerprint = ('failed', None)

        state = _maint_state.setdefault(lanlan_name, {})
        if status == 'patched':
            logger.info(f"✅ {lanlan_name} 的记忆整理任务完成")
            state['review_clean'] = True
            state['last_review_ts'] = datetime.now().isoformat()
            state['last_reviewed_cutoff_tail'] = fingerprint
            # 成功 → 清掉失败退避计数（Gate 6）
            state['review_fail_attempts'] = 0
            state['review_fail_fp'] = None
            await _asave_maint_state()
        elif status == 'white':
            logger.info(
                f"⚠️ {lanlan_name} 白 review（cutoff 失配），fingerprint 清空、不刷 ts，允许立即重试"
            )
            state['last_reviewed_cutoff_tail'] = None
            # 故意不更新 last_review_ts：让下轮 gate 4 用旧 ts（通常已过 30/60s）
            # 直接放行，配合 fingerprint=None 触发 gate 5 的 ∞ 通行 → 立即重 review。
            # 白 review 是 cutoff 失配（输入实际已变）而非失败，清退避计数允许立即重建锚点。
            state['review_fail_attempts'] = 0
            state['review_fail_fp'] = None
            await _asave_maint_state()
        elif cancel_event.is_set():
            # review_history 在 cancel_event 置位时也返回 ('failed', None)，但这是
            # 主动取消（cancel_correction：记忆编辑后立即生效）而非失败，不能计入
            # 失败退避——否则用户频繁编辑记忆会被误判成 poison。
            logger.info(f"ℹ️ {lanlan_name} 的记忆整理被取消（不计入失败退避）")
        else:
            # 'failed'：LLM 持续失败 / 超时 / 格式错误。bump 失败退避计数 + 记下
            # 本次失败的输入 fingerprint，供 Gate 6 在输入不变时 dead-letter，避免
            # correction 模型一直超时 + 长挂机 bypass 续命导致整夜空烧（用户审计 #1）。
            attempts = await _record_review_failure(lanlan_name, snapshot)
            logger.info(
                f"ℹ️ {lanlan_name} 的记忆整理未执行（被跳过或失败），"
                f"失败退避计数 → {attempts}"
            )
    except asyncio.CancelledError:
        logger.info(f"⚠️ {lanlan_name} 的记忆整理任务被取消")
    except Exception as e:
        # 纯兜底：能到这里的只剩 result 处理 / state 持久化等"非 review 失败"
        # 的异常（review_history 自身抛已在内层收口成 'failed'）。这类异常**不**
        # 计入失败退避——否则成功 review 的 save 抖动会被误判成失败、误触
        # Gate 6 dead-letter 压住后续 review（Codex P2）。
        logger.error(f"❌ {lanlan_name} 的记忆整理后处理出错（不计入失败退避）: {e}")
    finally:
        # 按 task/event 身份比对再清理：如果并发的新 spawn 已经写入了新 task /
        # 新 event，本 task 不应该把它们清掉。
        current_task = asyncio.current_task()
        if correction_tasks.get(lanlan_name) is current_task:
            correction_tasks.pop(lanlan_name, None)
        if correction_cancel_flags.get(lanlan_name) is cancel_event:
            correction_cancel_flags.pop(lanlan_name, None)

def _extract_ai_response(messages: list) -> str:
    """从消息列表中提取最后一条 AI 回复的文本。"""
    for m in reversed(messages):
        if getattr(m, 'type', '') == 'ai':
            content = getattr(m, 'content', '')
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text']
                return ''.join(parts)
    return ''


def _extract_user_messages(messages: list) -> list[str]:
    """从消息列表中提取用户消息文本（跳过空白）。"""
    user_msgs = []
    for m in messages:
        if getattr(m, 'type', '') == 'human':
            content = getattr(m, 'content', '')
            if isinstance(content, str):
                if content.strip():
                    user_msgs.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get('type') == 'text':
                        text = part.get('text', '').strip()
                        if text:
                            user_msgs.append(text)
    return user_msgs


# --- Reflection API（供 main_server/system_router 通过 HTTP 调用） ---

@app.post("/reflect/{lanlan_name}")
async def api_reflect(lanlan_name: str):
    """合成反思 + 自动状态迁移，返回结果。

    集中在 memory_server 进程内执行，避免 main_server 本地实例化导致的
    absorbed 标记竞态问题。
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    reflection_result = None
    # auto_promote_stale 改 fire-and-forget：开 thinking 后 promote_merge 单
    # 调用可能 30-90s，串行多个 confirmed reflection 累计能超 client 15s
    # timeout。periodic auto_promote loop 每 180s 跑一次会兜底，本端点不
    # 等也安全。caller (system_router) 仅用 auto_transitions 打 log，丢失
    # 计数无功能影响。
    _spawn_background_task(_safe_auto_promote(lanlan_name))
    try:
        reflection_result = await reflection_engine.reflect(lanlan_name)
    except Exception as e:
        logger.debug(f"[ReflectAPI] {lanlan_name}: reflect 失败: {e}")
    return {
        "reflection": reflection_result,
        "auto_transitions": 0,  # fire-and-forget，本调用不返回真实计数
    }


async def _safe_auto_promote(lanlan_name: str) -> None:
    """fire-and-forget 包装，吞 reflection_engine.aauto_promote_* 的异常。

    根据强力记忆开关二选一：开 → score-driven + merge LLM；关 → time-driven。
    """
    try:
        if await _ais_powerful_memory_enabled():
            await reflection_engine.aauto_promote_stale(lanlan_name)
        else:
            await reflection_engine.aauto_promote_time_driven(lanlan_name)
    except Exception as e:
        logger.debug(f"[ReflectAPI] {lanlan_name}: 后台 auto_promote 失败: {e}")


@app.get("/followup_topics/{lanlan_name}")
async def api_followup_topics(lanlan_name: str):
    """获取回调话题候选（不标记 surfaced，调用方需后续调 /record_surfaced）。"""
    lanlan_name = validate_lanlan_name(lanlan_name)
    try:
        topics = await reflection_engine.aget_followup_topics(lanlan_name)
    except Exception as e:
        logger.debug(f"[ReflectAPI] {lanlan_name}: get_followup_topics 失败: {e}")
        topics = []
    return {"topics": topics}


@app.post("/record_surfaced/{lanlan_name}")
async def api_record_surfaced(request: Request, lanlan_name: str):
    """记录本次主动搭话提及了哪些反思，刷新 cooldown。"""
    lanlan_name = validate_lanlan_name(lanlan_name)
    body = await request.json()
    reflection_ids = body.get("reflection_ids", [])
    if not reflection_ids:
        return {"ok": True}
    try:
        await reflection_engine.arecord_surfaced(lanlan_name, reflection_ids)
    except Exception as e:
        logger.debug(f"[ReflectAPI] {lanlan_name}: record_surfaced 失败: {e}")
    return {"ok": True}


async def _run_post_turn_signals(messages: list, lanlan_name: str):
    """后台异步：每轮 turn end 的 per-turn signals。失败静默跳过。

    职责（按 step 顺序）：
      0. counter bump —— 给 ``_periodic_signal_extraction_loop`` 的 turn
         counter +1，让 batch loop 在累积 10 turn 时触发 Stage-1+Stage-2
      1. OFF-mode Stage-1 fallback —— powerful_memory 关闭时 batch loop 整段
         停，per-turn ``fact_store.extract_facts`` 是 fact extraction 唯一
         兜底（ON-mode 不跑，交给 batch loop）
      2. 复读嗅探 —— 本地 BM25，§2.6 5h 窗口 suppress
      3. check_feedback —— 用户对 surfaced reflection 的反馈检测（LLM 仅在
         surfaced 有 pending 时跑）+ NEGATIVE_KEYWORDS 命中触发 LLM target check

    命名史 — 本函数 PR-1 (RFC #928) 引入时叫 ``_extract_facts_and_check_feedback``，
    当时 step 1 还是无条件每轮跑 ``fact_store.extract_facts`` (Stage-1)。
    RFC §3.4.3 原话："**不**在对话主路径上每轮运行 extract_facts——太贵。
    改为背景调度"——PR #1346 把 ON-mode Stage-1 剥离到
    ``_periodic_signal_extraction_loop``，step 1 退化为 OFF-mode fallback，
    本 follow-up 把符号名（含 outbox spawn helper / handler / op 常量）统一
    改成 ``post_turn_signals`` 以匹配实际语义。``OP_POST_TURN_SIGNALS`` 的
    **字符串值**仍是 ``"extract_facts"``（outbox.ndjson wire-format 不可变）。
    """
    user_msgs = _extract_user_messages(messages)

    # 本轮算入 signal-extraction 触发计数器（RFC §3.4.3）—— batch loop
    # 靠这个 counter 在累积 N 轮时触发 _signal_check_one。
    # 只在 user 有发声时 bump，**故意不**算 AI-only / proactive turn：
    # path A 抽的是 user_observation fact，没 user 发声就抽不出料；
    # path B 是 piggyback A 的 trigger 跑（不独立调度），也跟着只在 user
    # 有 engagement 的窗口里跑。这是 product thesis 的"90% 没心没肺"——
    # AI 自言自语 + user 不搭理的内容是廉价层，不该自动当 fact 沉淀污染
    # memory；只有 user 印证过的才升级到神明降临层。
    try:
        if user_msgs:
            _signal_check_record_turn(lanlan_name)
    except Exception as e:
        # Best-effort counter bump; a failure here only delays the next
        # signal-extraction cycle — not worth interrupting conversation flow.
        logger.debug(f"[MemoryServer] signal-check turn counter 更新失败: {e}")

    # 强力记忆开关——本轮 evidence-related 路径的 gate（promote/negative-keyword/
    # corrections）。check_feedback 自身仍跑（主动搭话回应是核心 channel）。
    powerful_enabled = await _ais_powerful_memory_enabled()

    # Step 1 — per-turn Stage-1 fact extraction：只在 powerful_memory **关闭**
    # 时跑（OFF-mode baseline fallback）。ON-mode 下 fact extraction 完全交给
    # ``_periodic_signal_extraction_loop`` 跑 batch Stage-1+Stage-2（RFC §3.4.3
    # 设计意图："不在对话主路径上每轮运行 extract_facts——太贵。改为背景调度"，
    # batch 路径带上下文、质量更高、cost 更低）。
    #
    # OFF-mode 下 batch loop 整段停（见 _periodic_signal_extraction_loop 的
    # `if not powerful_enabled: continue` 分支），如果这里也跳过，facts.json
    # 就完全无路径更新——这是 chatgpt-codex-connector PR #1346 抓到的 regression。
    # OFF-mode 保留 legacy per-turn Stage-1，let user 仍能拿到基础 fact 累积。
    if not powerful_enabled:
        try:
            await fact_store.extract_facts(messages, lanlan_name)
        except Exception as e:
            logger.warning(f"[MemoryServer] OFF-mode 事实提取失败: {e}")

    try:
        # 2. 全局复读嗅探：扫描 AI 回复中是否重复提及 persona 条目 +
        #    confirmed reflection（§2.6 5h 窗口 suppress 机制，两者正交）。
        #    本地 BM25，无 LLM 调用，per-turn 跑是必要的——5h 窗口逻辑
        #    依赖即时更新。
        ai_response = _extract_ai_response(messages)
        if ai_response:
            await persona_manager.arecord_mentions(lanlan_name, ai_response)
            await reflection_engine.arecord_mentions(lanlan_name, ai_response)
    except Exception as e:
        logger.warning(f"[MemoryServer] 复读嗅探失败: {e}")

    try:
        # 3. 检查用户对之前 surfaced 反思的反馈 + 派 evidence 信号
        surfaced = await reflection_engine.aload_surfaced(lanlan_name)
        pending_surfaced = [s for s in surfaced if s.get('feedback') is None]
        if pending_surfaced and user_msgs:
            feedbacks = await reflection_engine.check_feedback(lanlan_name, user_msgs)
            if feedbacks is not None:
                # Build id→feedback map for quick lookup
                fb_map: dict[str, str] = {}
                for fb in feedbacks:
                    if not isinstance(fb, dict):
                        continue
                    rid = fb.get('reflection_id')
                    kind = fb.get('feedback')
                    if rid and kind in ('confirmed', 'denied', 'ignored'):
                        fb_map[rid] = kind

                # RFC §3.1.5: confirmed → reinforcement += 1; denied →
                # disputation += 1; ignored → reinforcement += -0.2.
                # pending→confirmed/denied state transitions happen in the
                # score-driven auto_promote_stale path (not here).
                #
                # Retry semantics caveat: `check_feedback` above already
                # persisted the feedback decision into `surfaced.json`, so
                # a downstream aapply_signal / areject_promotion failure
                # here won't be re-tried next cycle (surfaced.feedback !=
                # None skips the row). PR-1 accepts best-effort with WARN
                # logs; a follow-up would move these side-effects behind an
                # outbox op so they survive transient failures. Tracked for
                # PR-2+ decay/archive work.
                for rid, kind in fb_map.items():
                    if kind == 'confirmed':
                        delta = {'reinforcement': USER_CONFIRM_DELTA}
                        source = EVIDENCE_SOURCE_USER_CONFIRM
                    elif kind == 'denied':
                        delta = {'disputation': USER_REBUT_DELTA}
                        source = EVIDENCE_SOURCE_USER_REBUT
                    else:  # ignored
                        delta = {'reinforcement': IGNORED_REINFORCEMENT_DELTA}
                        source = EVIDENCE_SOURCE_USER_IGNORE
                    try:
                        await reflection_engine.aapply_signal(
                            lanlan_name, rid, delta, source=source,
                        )
                    except Exception as e:
                        # Signal lost this turn (see caveat above). Warn so
                        # operators can spot transient LLM / disk issues.
                        logger.warning(
                            f"[MemoryServer] {lanlan_name}: aapply_signal "
                            f"({rid}, {kind}) 失败，此次反馈 signal 已丢失: {e}"
                        )

                # denied 仍然走 areject_promotion 做 status transition（保留
                # 既有 surfaced 登记 + reflection status='denied' 行为）
                for rid, kind in fb_map.items():
                    if kind == 'denied':
                        try:
                            await reflection_engine.areject_promotion(lanlan_name, rid)
                        except Exception as e:
                            logger.warning(
                                f"[MemoryServer] areject_promotion 失败 "
                                f"{rid}，此次 denial 未转入 status: {e}"
                            )

                # 让后续扫描把 pending→confirmed 推进。强力记忆决定走哪条：
                #   开 → score-driven + merge LLM
                #   关 → time-driven (14 天 confirm + 14 天 promote, 零 LLM)
                try:
                    if powerful_enabled:
                        await reflection_engine.aauto_promote_stale(lanlan_name)
                    else:
                        await reflection_engine.aauto_promote_time_driven(lanlan_name)
                except Exception as e:
                    logger.debug(
                        f"[MemoryServer] {lanlan_name}: auto_promote 失败: {e}"
                    )
    except Exception as e:
        logger.warning(f"[MemoryServer] 反馈检查失败: {e}")

    if powerful_enabled:
        try:
            # 3.5 负面关键词 hook（§3.4.5）——命中就派个异步小 LLM 任务
            # 强力记忆关 → 整段不跑（这是 evidence-RFC 引入的额外 LLM 路径）
            if user_msgs:
                from utils.language_utils import get_global_language
                _spawn_background_task(
                    _amaybe_trigger_negative_keyword_hook(
                        lanlan_name, user_msgs, get_global_language(),
                    )
                )
        except Exception as e:
            logger.debug(f"[MemoryServer] 负面关键词 hook 派发失败: {e}")

        try:
            # 4. 审视矛盾队列（如果有 pending corrections）
            # 强力记忆关 → 不跑 LLM 批量审视（corrections queue 累积，等重开消化）
            resolved = await persona_manager.resolve_corrections(lanlan_name)
            if resolved:
                logger.info(f"[MemoryServer] {lanlan_name}: 审视了 {resolved} 条 persona 矛盾")
        except Exception as e:
            logger.warning(f"[MemoryServer] 矛盾审视失败: {e}")


async def _outbox_post_turn_signals_handler(lanlan_name: str, payload: dict) -> None:
    """OP_POST_TURN_SIGNALS 的 outbox handler：从 payload 还原 messages 再跑
    ``_run_post_turn_signals``。

    幂等性来源：
      - fact_store.extract_facts（OFF-mode fallback）内部靠 SHA-256 对事实
        去重，重复提取不会产生重复 fact。
      - arecord_mentions 是单调累加计数，重放会小幅抬高提及次数（可接受的
        at-least-once 语义）。
      - check_feedback 下次自然回补——reflection 的 surfaced/feedback
        列表是持久化的。
      - resolve_corrections 内部用 processed_indices 保护幂等。
    """
    from utils.llm_client import messages_from_dict

    raw = payload.get('messages') or []
    if not raw:
        return
    messages = messages_from_dict(raw)
    if not messages:
        return
    await _run_post_turn_signals(messages, lanlan_name)


register_outbox_handler(OP_POST_TURN_SIGNALS, _outbox_post_turn_signals_handler)


@app.post("/cache/{lanlan_name}")
async def cache_conversation(request: HistoryRequest, lanlan_name: str):
    """每轮 turn end 的"轻量持久化"端点：写 recent.json + 落 time_indexed.db
    + 登记 per-turn signals outbox op（counter bump + 本地复读嗅探 +
    check_feedback）。**不**跑 Stage-1 fact_extract LLM——RFC §3.4.3
    明确"per-turn extract_facts 太贵，改为背景调度"，batch 抽取由
    ``_periodic_signal_extraction_loop`` 在累积 10 turn 或 5 min idle 时
    从 ``time_indexed.db`` 拉窗口跑 Stage-1+Stage-2；也**不**跑 review LLM
    重写历史（那一类仍由 /settle 在 renew session 时跑）。

    历史 — commit cba377c5（"Fix/memory hotswap timing"，2026-03-29）引入
    /settle 时把"补完 cache 留下的 LLM 后续操作"全 gate 在 ``if input_history``
    后面，但 cross_server 的标准节奏是"turn end /cache → renew session
    /settle(msgs=0)"，settle 永远收 msgs=0，于是 ``store_conversation`` 和
    outbox extract 都被静默跳过：``time_indexed.db`` 永不创建（time
    perception 失效）+ ``outbox.ndjson`` / ``events.ndjson`` / ``facts.json``
    全部不建（长期记忆 + evidence-RFC 链路完全空转），**且 batch loop 依赖
    db 拉历史也一并瘫痪**。

    修法把 store + post-turn signals 搬回 cache 端点；同时 PR-1 当时为
    "短期行为不变"暂留的 Stage-1 per-turn fact_extract（``legacy flow``）
    一并迁完——RFC 原本就计划只让 ``_periodic_signal_extraction_loop`` 跑
    fact extraction。``astore_conversation`` 是 SQLite INSERT（~ms 量级），
    ``_spawn_outbox_post_turn_signals`` 现内部只跑 counter bump + 本地复读嗅探
    + check_feedback（LLM 仅在 surfaced 有 pending 时才跑），是 ndjson
    append + spawn background task（不阻塞响应）。``cache`` 保持"前台无
    LLM 延迟"的轻量语义，**且比 PR-1 实现更轻**——单 turn fact_extract
    LLM 浪费已彻底去除。
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    _touch_activity()
    try:
        input_history = convert_to_messages(json.loads(request.input_history))
        if not input_history:
            return {"status": "cached", "count": 0}
        if _has_human_messages(input_history):
            await _aclear_review_clean(lanlan_name)
        logger.info(f"[MemoryServer] cache: {lanlan_name} +{len(input_history)} 条消息")
        uid = str(uuid4())
        async with _get_settle_lock(lanlan_name):
            await recent_history_manager.update_history(input_history, lanlan_name, compress=False)
            # store_conversation 必须在 lock 内、与 update_history 串行：和
            # /process / /renew 路径对偶，确保单角色 db 写顺序一致。
            await time_manager.astore_conversation(uid, input_history, lanlan_name)
        # outbox 登记走锁外——它会 spawn background task 跑 LLM，长持锁会
        # 阻塞下一轮 /cache 写盘。
        await _spawn_outbox_post_turn_signals(lanlan_name, input_history)
        return {"status": "cached", "count": len(input_history)}
    except Exception as e:
        logger.error(f"[MemoryServer] cache 失败: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@app.post("/process/{lanlan_name}")
async def process_conversation(request: HistoryRequest, lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    _touch_activity()
    # P2 vector warmup: first /process is the cheapest "frontend ready"
    # signal we have — by the time the user sends a real conversation
    # turn, greeting and prominent drain are over. notify_first_process
    # is a setflag, not async, so it doesn't add latency to /process.
    if embedding_warmup_worker is not None:
        embedding_warmup_worker.notify_first_process()
    global correction_tasks
    try:
        # 检查角色是否存在于配置中，如果不存在则记录信息但继续处理（允许新角色）
        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
            if lanlan_name not in catgirl_names:
                logger.info(f"[MemoryServer] 角色 '{lanlan_name}' 不在配置中，但继续处理（可能是新创建的角色）")
        except Exception as e:
            logger.warning(f"检查角色配置失败: {e}，继续处理")

        uid = str(uuid4())
        input_history = convert_to_messages(json.loads(request.input_history))
        if _has_human_messages(input_history):
            await _aclear_review_clean(lanlan_name)
        logger.info(f"[MemoryServer] 收到 {lanlan_name} 的对话历史处理请求，消息数: {len(input_history)}")
        await recent_history_manager.update_history(input_history, lanlan_name)
        # 旧模块已禁用（性能不足）：
        # await settings_manager.extract_and_update_settings(input_history, lanlan_name)
        # await semantic_manager.store_conversation(uid, input_history, lanlan_name)
        await time_manager.astore_conversation(uid, input_history, lanlan_name)

        # 异步事实提取（不阻塞返回，失败静默跳过）
        await _spawn_outbox_post_turn_signals(lanlan_name, input_history)

        # Phase C: 不再 cancel-and-restart review；让 maybe_spawn_review 在新消息
        # 门 + min_interval + in-flight 多重 gate 后决定起或不起。在跑的 review
        # 跑完会自行 patch 当前 history 末尾的可改区，新消息保留不动。
        await maybe_spawn_review(lanlan_name)

        return {"status": "processed"}
    except Exception as e:
        logger.error(f"处理对话历史失败: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/renew/{lanlan_name}")
async def process_conversation_for_renew(request: HistoryRequest, lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    _touch_activity()
    # Same warmup hint as /process: /renew is also a "user actively
    # using the app" signal, so it counts as the unblock event.
    if embedding_warmup_worker is not None:
        embedding_warmup_worker.notify_first_process()
    global correction_tasks
    try:
        # 检查角色是否存在于配置中，如果不存在则记录信息但继续处理（允许新角色）
        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
            if lanlan_name not in catgirl_names:
                logger.info(f"[MemoryServer] renew: 角色 '{lanlan_name}' 不在配置中，但继续处理（可能是新创建的角色）")
        except Exception as e:
            logger.warning(f"检查角色配置失败: {e}，继续处理")

        uid = str(uuid4())
        input_history = convert_to_messages(json.loads(request.input_history))
        if _has_human_messages(input_history):
            await _aclear_review_clean(lanlan_name)
        logger.info(f"[MemoryServer] renew: 收到 {lanlan_name} 的对话历史处理请求，消息数: {len(input_history)}")
        # 首轮摘要带锁：阻塞 /new_dialog 直到摘要+时间戳写入完成
        async with _get_settle_lock(lanlan_name):
            await recent_history_manager.update_history(input_history, lanlan_name, detailed=True)
            await time_manager.astore_conversation(uid, input_history, lanlan_name)

        # 以下操作在锁外执行，不阻塞 /new_dialog
        # 异步事实提取
        await _spawn_outbox_post_turn_signals(lanlan_name, input_history)

        # Phase C: 见 /process 的注释——不再 cancel-and-restart。
        await maybe_spawn_review(lanlan_name)

        return {"status": "processed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/settle/{lanlan_name}")
async def settle_conversation(request: HistoryRequest, lanlan_name: str):
    """结算已通过 /cache 缓存的对话：触发摘要压缩 + 时间戳写入 + 事实提取。

    当 cross_server 的 renew session 发现增量为 0（所有消息已 /cache 过）时调用此端点。
    /cache 只做 update_history(compress=False)，不触发 LLM 摘要和 time_manager 写入，
    本端点补全这些操作。
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    _touch_activity()
    global correction_tasks
    try:
        uid = str(uuid4())
        input_history = convert_to_messages(json.loads(request.input_history))
        if _has_human_messages(input_history):
            await _aclear_review_clean(lanlan_name)
        logger.info(f"[MemoryServer] settle: 收到 {lanlan_name} 的结算请求，消息数: {len(input_history)}")

        async with _get_settle_lock(lanlan_name):
            if input_history:
                await time_manager.astore_conversation(uid, input_history, lanlan_name)
            await recent_history_manager.update_history([], lanlan_name, detailed=True)

        if input_history:
            await _spawn_outbox_post_turn_signals(lanlan_name, input_history)

        # Phase C: 见 /process 的注释——不再 cancel-and-restart。
        await maybe_spawn_review(lanlan_name)

        return {"status": "settled"}
    except Exception as e:
        logger.error(f"[MemoryServer] settle 失败: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@app.get("/get_recent_history/{lanlan_name}")
async def get_recent_history(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    _lang = get_global_language()
    # 检查角色是否存在于配置中
    try:
        character_data = await _config_manager.aload_characters()
        catgirl_names = list(character_data.get('猫娘', {}).keys())
        if lanlan_name not in catgirl_names:
            logger.warning(f"角色 '{lanlan_name}' 不在配置中，返回空历史记录")
            return _loc(NO_RECENT_HISTORY, _lang)
    except Exception as e:
        logger.error(f"检查角色配置失败: {e}")
        return _loc(NO_RECENT_HISTORY, _lang)

    history = await recent_history_manager.aget_recent_history(lanlan_name)
    _, _, _, _, name_mapping, _, _, _, _ = await _config_manager.aget_character_data()
    name_mapping['ai'] = lanlan_name
    result = _loc(RECENT_HISTORY_INTRO, _lang).format(name=lanlan_name)
    for i in history:
        if i.type == 'system':
            result += i.content + "\n"
        else:
            texts = [j['text'] for j in i.content if j['type']=='text']
            joined = "\n".join(texts)
            result += f"{name_mapping[i.type]} | {joined}\n"
    return result

@app.get("/search_for_memory/{lanlan_name}/{query}")
async def get_memory(query: str, lanlan_name: str):
    """**Deprecated** — 旧 GET 端点保留只为不破坏老调用方；新调用方走
    POST ``/query_memory/{lanlan_name}`` 拿结构化结果。本端点继续返回
    占位文字以避免老路径回流（语义召回早就在这条 GET 上下线了）。
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    _lang = get_global_language()
    return (
        _loc(MEMORY_RECALL_HEADER, _lang).format(name=lanlan_name)
        + query
        + "\n\n"
        + _loc(MEMORY_RESULTS_HEADER, _lang).format(name=lanlan_name)
        + "\n（语义记忆已下线，暂无相关记忆片段。）"
    )


class QueryMemoryRequest(BaseModel):
    # query / time 都可选，至少给一个有效值即可（time-only 是新支持的用法）。
    # 两者都空时不报错，hybrid_recall 对空 query 短路返回空 results，调用方
    # 把空结果翻成"没有找到相关记忆"——和本端点"绝不让召回失败/空入参把
    # tool call 整死"的设计一致，所以这里不做 422/400 硬校验。
    query: str | None = None
    # 可选时间回溯：填了就把检索限定在该时间窗口。配合 query 时做"语义 +
    # 时间"联合检索（窗口内按 query 排序）；只给 time 时按事件时间返回最
    # 接近的 fact + reflection。格式见 memory.temporal.parse_time_window
    # （整点小时 / 单日 / 整月 / 整年 / 区间）。不填或解析失败则走常规全量
    # 语义检索。
    time: str | None = None


@app.post("/query_memory/{lanlan_name}")
async def query_memory(lanlan_name: str, req: QueryMemoryRequest):
    """混合检索 entry point —— BM25 + cosine embedding 并行召回 + RRF 融合。

    POST body: ``{"query": "<自然语言查询>", "time": "<可选 ISO 时间>"}``

    返回 ``hybrid_recall`` 的结构化结果（见 ``memory.hybrid_recall``
    docstring）。``main_server`` 的 ``recall_memory`` 工具 handler 调
    本端点拿结果，再格式化给模型看。

    路由（query / time 三种组合）：
    - **query + time**：``hybrid_recall(query, time_window=...)`` —— 先按事件
      时间窗口硬过滤候选池，再在窗口内条目上跑语义检索（"那段时间里和
      query 相关的记忆"）。
    - **只有 time**：``recall_by_time`` —— 按事件时间锚点返回离该窗口最接近
      的若干条 fact + reflection，不做语义打分（"那天/那周发生的事"）。
    - **只有 query**：``hybrid_recall(query)`` —— 全量语义检索。
    - time 解析失败时按"没给 time"处理，回落到纯 query 语义检索（不能让
      一个坏 time 把 query 的语义召回也一起吞掉返回空，Codex P2）。

    ⚠️ 候选范围、阈值、budget 都在 ``config.HYBRID_RECALL_*`` 里配置；
    persona 整段不入池（已经常态渲染进 system prompt），facts +
    reflections 走全路径，facts_archive 只入 BM25 池。
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    if fact_store is None or reflection_engine is None:
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    time_spec = (req.time or "").strip()
    query_text = (req.query or "").strip()
    try:
        # Import 移进 try：若 memory.hybrid_recall 自身 import 失败（循环
        # import / 依赖缺失），仍然走下面的兜底返回空 results，避免端点
        # 直接 500 把 tool call 整死。
        time_window = None
        if time_spec:
            from memory.temporal import parse_time_window
            time_window = parse_time_window(time_spec)
            if time_window is None:
                logger.info(
                    "[query_memory] %s: time=%r 无法解析为时间窗口，回落语义检索",
                    lanlan_name, time_spec,
                )
            elif not query_text:
                # 只给 time、没 query → 按时间邻近返回最接近的若干条。
                from memory.hybrid_recall import recall_by_time
                return await recall_by_time(
                    lanlan_name=lanlan_name,
                    time_spec=time_spec,
                    fact_store=fact_store,
                    reflection_engine=reflection_engine,
                )
        # query（+ 可选 time_window）→ 语义检索；time_window 非空即"语义 +
        # 时间"联合检索（窗口内按 query 排序）。
        from memory.hybrid_recall import hybrid_recall
        return await hybrid_recall(
            lanlan_name=lanlan_name,
            query=query_text,
            fact_store=fact_store,
            reflection_engine=reflection_engine,
            config_manager=_config_manager,
            time_window=time_window,
        )
    except Exception as exc:
        # 永不让一次召回失败把 tool call 整死——返回空 results，main_server
        # 那边的 handler 会把空 results 翻译成 "没有找到相关记忆"，模型可以
        # 正常继续。完整 traceback 落 logger.exception（含 type + msg），
        # 响应体只回稳定 error_code，避免把内部细节（异常消息可能夹带敏感
        # 上下文）通过 HTTP body 泄出去。
        logger.exception(
            "[hybrid_recall] %s: 召回失败，返回空结果占位: %s: %s",
            lanlan_name, type(exc).__name__, exc,
        )
        return {
            "results": [], "query": req.query or "",
            "candidates_total": 0, "elapsed_ms": 0.0,
            "error_code": "hybrid_recall_failed",
        }

@app.get("/get_settings/{lanlan_name}")
async def get_settings(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    # 检查角色是否存在于配置中
    try:
        character_data = await _config_manager.aload_characters()
        catgirl_names = list(character_data.get('猫娘', {}).keys())
        if lanlan_name not in catgirl_names:
            logger.warning(f"角色 '{lanlan_name}' 不在配置中，返回空设置")
            return f"{lanlan_name}记得{{}}"
    except Exception as e:
        logger.error(f"检查角色配置失败: {e}")
        return f"{lanlan_name}记得{{}}"

    # Render 前刷新 reflection suppress 状态（冷却期过 → 解除），语义对齐
    # persona render 的 update_suppressions 调用位置
    try:
        await reflection_engine.aupdate_suppressions(lanlan_name)
    except Exception as e:
        logger.debug(f"[MemoryServer] reflection suppress 刷新失败: {e}")
    # 优先使用 persona markdown 渲染（与 /new_dialog 保持一致），回退到旧 settings 格式
    pending_reflections = await reflection_engine.aget_pending_reflections(lanlan_name)
    confirmed_reflections = await reflection_engine.aget_confirmed_reflections(lanlan_name)
    persona_md = await persona_manager.arender_persona_markdown(
        lanlan_name, pending_reflections, confirmed_reflections,
    )
    if persona_md:
        return persona_md
    # 兼容回退（自然语言格式）
    legacy_settings = await asyncio.to_thread(settings_manager.get_settings, lanlan_name)
    return _format_legacy_settings_as_text(legacy_settings, lanlan_name)


@app.get("/get_persona/{lanlan_name}")
async def get_persona(lanlan_name: str):
    """返回完整 persona JSON（供 UI / memory_browser 使用）。"""
    lanlan_name = validate_lanlan_name(lanlan_name)
    return await persona_manager.aget_persona(lanlan_name)


@app.get("/api/memory/funnel/{lanlan_name}")
async def api_memory_funnel(lanlan_name: str, since: str | None = None, until: str | None = None):
    """RFC §3.10 funnel analytics — read-only counts of evidence-pipeline
    transitions in a [since, until] window.

    Query params (both ISO8601, optional):
      - since: window lower bound, default = now - 7 days
      - until: window upper bound, default = now

    Timezone handling: `datetime.fromisoformat` happily accepts both naive
    (`2026-04-22T12:00:00`) and aware (`...Z`, `...+08:00`) values, but
    the underlying event log writes naive local-clock timestamps. We
    normalize both bounds via `to_naive_local` immediately after parse
    — *before* the `since_dt > until_dt` validation — so a client
    passing one aware bound and one naive (or default-naive `now()`)
    bound never trips
    `TypeError: can't compare offset-naive and offset-aware datetimes`
    and surfaces as a 500. `funnel_counts` re-normalizes internally
    too; the second pass is a cheap no-op once both are naive.

    Returns the 10-bucket dict from `funnel_counts`. PR-2 (decay+archive)
    populates `*_archived` buckets; PR-3 (merge-on-promote) populates
    `reflections_merged` / `persona_entries_rewritten`. Until those land
    the corresponding buckets stay at 0.
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    now = datetime.now()
    try:
        since_dt = datetime.fromisoformat(since) if since else now - timedelta(days=7)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid `since` ISO8601: {since!r}")
    try:
        until_dt = datetime.fromisoformat(until) if until else now
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid `until` ISO8601: {until!r}")
    # Normalize BEFORE the inequality check — `now` above is naive but a
    # client-supplied bound may be aware; comparing them directly would
    # raise TypeError → 500. coderabbitai PR #937 round-2.
    from memory.evidence_analytics import funnel_counts, to_naive_local
    since_dt = to_naive_local(since_dt)
    until_dt = to_naive_local(until_dt)
    if since_dt > until_dt:
        raise HTTPException(status_code=400, detail="`since` must be <= `until`")

    # 文件 IO + 行级解析 → 跑 worker，避开 event loop 阻塞
    # (同样的模式见 EventLog 的 a-twins)。
    counts = await asyncio.to_thread(funnel_counts, lanlan_name, since_dt, until_dt)
    return {
        "lanlan_name": lanlan_name,
        "since": since_dt.isoformat(),
        "until": until_dt.isoformat(),
        "counts": counts,
    }


@app.post("/reload")
async def reload_config():
    """重新加载记忆服务器配置（用于新角色创建后）"""
    try:
        success = await reload_memory_components()
        if success:
            return {"status": "success", "message": "配置已重新加载"}
        else:
            return {"status": "error", "message": "配置重新加载失败"}
    except Exception as e:
        logger.error(f"重新加载配置时出错: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

@app.post("/cancel_correction/{lanlan_name}")
async def cancel_correction(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    """中断指定角色的记忆整理任务（用于记忆编辑后立即生效）"""
    global correction_tasks, correction_cancel_flags
    
    if lanlan_name in correction_tasks and not correction_tasks[lanlan_name].done():
        logger.info(f"🛑 收到取消请求，中断 {lanlan_name} 的correction任务")
        
        if lanlan_name in correction_cancel_flags:
            correction_cancel_flags[lanlan_name].set()
        
        correction_tasks[lanlan_name].cancel()
        try:
            await correction_tasks[lanlan_name]
        except asyncio.CancelledError:
            logger.info(f"✅ {lanlan_name} 的correction任务已成功中断")
        except Exception as e:
            logger.warning(f"⚠️ 中断 {lanlan_name} 的correction任务时出现异常: {e}")
        
        return {"status": "cancelled"}
    
    return {"status": "no_task"}

@app.get("/new_dialog/{lanlan_name}")
async def new_dialog(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    _touch_activity()

    # 检查角色是否存在于配置中
    try:
        character_data = await _config_manager.aload_characters()
        catgirl_names = list(character_data.get('猫娘', {}).keys())
        if lanlan_name not in catgirl_names:
            logger.warning(f"角色 '{lanlan_name}' 不在配置中，返回空上下文")
            return PlainTextResponse("")
    except Exception as e:
        logger.error(f"检查角色配置失败: {e}")
        return PlainTextResponse("")

    # 仅对合法角色计数：QPS 观测的目的是评估 C+ 缓存决策，无效请求不构成
    # cacheable 机会，记进来反而污染 per_char 分布。
    _new_dialog_qps_counter[lanlan_name] = _new_dialog_qps_counter.get(lanlan_name, 0) + 1

    # settle_lock 保留：等 /renew /settle 的首轮摘要完成，读到一致数据。
    # review 不持此锁，且写盘是「整体引用替换 + fingerprint patch」原子操作，
    # 与本路径读取无 race；Phase C 已让 review 设计成可与 /process 并行的后台
    # 任务，/new_dialog 不再 cancel 在跑的 review（之前的 cancel 是 Phase A
    # 遗留物，会让 review 在活跃会话里几乎永不完成）。
    async with _get_settle_lock(lanlan_name):
        # 正则表达式：删除所有类型括号及其内容（包括[]、()、{}、<>、【】、（）等）
        brackets_pattern = re.compile(r'(\[.*?\]|\(.*?\)|（.*?）|【.*?】|\{.*?\}|<.*?>)')
        master_name, _, _, _, name_mapping, _, _, _, _ = await _config_manager.aget_character_data()
        name_mapping['ai'] = lanlan_name
        _lang = get_global_language()

        # ── [静态前缀] Persona 长期记忆（变化极少 → 最大化 prefix cache） ──
        # pending + confirmed 反思也注入上下文（分区标注）
        try:
            await reflection_engine.aupdate_suppressions(lanlan_name)
        except Exception as e:
            logger.debug(f"[MemoryServer] reflection suppress 刷新失败: {e}")
        pending_reflections = await reflection_engine.aget_pending_reflections(lanlan_name)
        confirmed_reflections = await reflection_engine.aget_confirmed_reflections(lanlan_name)
        result = _loc(PERSONA_HEADER, _lang).format(name=lanlan_name)
        persona_md = await persona_manager.arender_persona_markdown(
            lanlan_name, pending_reflections, confirmed_reflections,
        )
        if persona_md:
            result += persona_md
        else:
            # 兼容回退：使用旧 settings（自然语言格式）
            # get_settings 内部 open() + json.load()，offload 避免阻塞（冷回退路径，但触发时多文件 IO）
            legacy_settings = await asyncio.to_thread(settings_manager.get_settings, lanlan_name)
            result += _format_legacy_settings_as_text(legacy_settings, lanlan_name) + "\n"

        # ── [动态部分] 内心活动（每次变化） ──
        result += _loc(INNER_THOUGHTS_HEADER, _lang).format(name=lanlan_name)
        result += _loc(INNER_THOUGHTS_DYNAMIC, _lang).format(
            name=lanlan_name,
            time=get_timestamp(),
        )

        for i in await recent_history_manager.aget_recent_history(lanlan_name):
            if isinstance(i.content, str):
                cleaned_content = brackets_pattern.sub('', i.content).strip()
                result += f"{name_mapping[i.type]} | {cleaned_content}\n"
            else:
                texts = [brackets_pattern.sub('', j['text']).strip() for j in i.content if j['type'] == 'text']
                result += f"{name_mapping[i.type]} | " + "\n".join(texts) + "\n"

        # ── 距上次聊天间隔提示（放在最末尾，紧接 CONTEXT_SUMMARY_READY 之前） ──
        try:
            from datetime import datetime as _dt
            last_time = await time_manager.aget_last_conversation_time(lanlan_name)
            if last_time:
                gap = _dt.now() - last_time
                gap_seconds = gap.total_seconds()
                if gap_seconds >= 1800:  # ≥ 30分钟才显示
                    elapsed = _format_elapsed(_lang, gap_seconds)

                    if gap_seconds >= 18000:  # ≥ 5小时：当前时间 + 间隔 + 长间隔提示
                        now_str = _dt.now().strftime("%Y-%m-%d %H:%M")
                        result += _loc(CHAT_GAP_CURRENT_TIME, _lang).format(now=now_str)
                        result += _loc(CHAT_GAP_NOTICE, _lang).format(master=master_name, elapsed=elapsed)
                        result += _loc(CHAT_GAP_LONG_HINT, _lang).format(name=lanlan_name, master=master_name) + "\n"
                    else:
                        result += _loc(CHAT_GAP_NOTICE, _lang).format(master=master_name, elapsed=elapsed) + "\n"
        except Exception as e:
            logger.warning(f"计算聊天间隔失败: {e}")

        # ── 节日/假期上下文（无关消费，始终注入） ──
        try:
            from utils.holiday_cache import get_holiday_context_line
            holiday_name = get_holiday_context_line(_lang)
            if holiday_name:
                result += _loc(CHAT_HOLIDAY_CONTEXT, _lang).format(holiday=holiday_name)
        except Exception as e:
            logger.debug(f"Holiday context injection skipped: {e}")

        return PlainTextResponse(result)

@app.get("/last_conversation_gap/{lanlan_name}")
async def last_conversation_gap(lanlan_name: str):
    """返回距上次对话的间隔秒数，供主服务判断是否触发主动搭话。"""
    lanlan_name = validate_lanlan_name(lanlan_name)
    try:
        last_time = await time_manager.aget_last_conversation_time(lanlan_name)
        if last_time is None:
            return {"gap_seconds": -1}
        gap = (datetime.now() - last_time).total_seconds()
        return {"gap_seconds": gap}
    except Exception as e:
        logger.exception(f"查询对话间隔失败: {e}")
        return JSONResponse({"gap_seconds": -1, "error": "server_error"}, status_code=500)

if __name__ == "__main__":
    import threading
    import time
    import signal
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='Memory Server')
    parser.add_argument('--enable-shutdown', action='store_true', 
                       help='启用响应退出请求功能（仅在终端用户环境使用）')
    args = parser.parse_args()
    
    # 设置全局变量
    enable_shutdown = args.enable_shutdown
    
    # 创建一个后台线程来监控关闭信号
    def monitor_shutdown():
        while not shutdown_event.is_set():
            time.sleep(0.1)
        logger.info("检测到关闭信号，正在关闭memory_server...")
        # 发送SIGTERM信号给当前进程
        os.kill(os.getpid(), signal.SIGTERM)
    
    # 只有在启用关闭功能时才启动监控线程
    if enable_shutdown:
        shutdown_monitor = threading.Thread(target=monitor_shutdown, daemon=True)
        shutdown_monitor.start()
    
    # 启动服务器
    uvicorn.run(app, host="127.0.0.1", port=MEMORY_SERVER_PORT)
