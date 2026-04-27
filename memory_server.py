# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
from memory.outbox import Outbox, OP_EXTRACT_FACTS
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
    IGNORED_REINFORCEMENT_DELTA,
    MEMORY_SERVER_PORT,
    USER_CONFIRM_DELTA,
    USER_FACT_NEGATE_DELTA,
    USER_FACT_REINFORCE_DELTA,
    USER_KEYWORD_REBUT_DELTA,
    USER_REBUT_DELTA,
)
from config.prompts_sys import _loc
from config.prompts_memory import (
    INNER_THOUGHTS_HEADER, INNER_THOUGHTS_BODY,
    CHAT_GAP_NOTICE, CHAT_GAP_LONG_HINT, CHAT_GAP_CURRENT_TIME,
    CHAT_HOLIDAY_CONTEXT,
    MEMORY_RECALL_HEADER, MEMORY_RESULTS_HEADER,
    PERSONA_HEADER, INNER_THOUGHTS_DYNAMIC,
    get_negative_target_check_prompt,
    scan_negative_keywords,
)
from utils.language_utils import get_global_language
from utils.character_name import validate_character_name
from utils.cloudsave_runtime import (
    MaintenanceModeError,
    ROOT_MODE_NORMAL,
    bootstrap_local_cloudsave_environment,
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
# 每角色结算锁：首轮摘要期间阻塞 /new_dialog，确保热切换后读到最新数据
_settle_locks: dict[str, asyncio.Lock] = {}
# 强引用注册表：防止 fire-and-forget task 被 GC
_BACKGROUND_TASKS: set[asyncio.Task] = set()

# ── 空闲维护相关 ────────────────────────────────────────────────────
_last_activity_time: datetime = datetime.now()            # 最后一次对话活动时间
IDLE_CHECK_INTERVAL = 40             # 空闲检查轮询间隔（秒，正常阶段）
IDLE_CHECK_INTERVAL_STARTUP = 10     # 启动阶段高频轮询间隔
IDLE_THRESHOLD = 10                  # 多少秒无活动视为空闲（匹配最低 proactive 间隔）
REVIEW_MIN_INTERVAL = 300            # review（correction）最短间隔（秒）
REVIEW_SKIP_HISTORY_LEN = 8          # 历史不足此数的角色跳过 review / correction

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
_REPLAY_CONCURRENCY = 4
_replay_semaphore: asyncio.Semaphore | None = None  # 懒构造（event loop-bound）


def register_outbox_handler(op_type: str, handler: OutboxHandler) -> None:
    _OUTBOX_HANDLERS[op_type] = handler


async def _run_outbox_op(name: str, op: dict, sem: asyncio.Semaphore | None = None) -> None:
    """跑单条 outbox op 并在成功后 append_done。失败保持 pending 等下次启动补跑。

    `sem`：startup replay 路径传入共享 Semaphore 限制 LLM fan-out；日常单次
    spawn 路径传 None 即不限流。
    """
    op_id = op.get('op_id')
    op_type = op.get('type')
    payload = op.get('payload') or {}
    handler = _OUTBOX_HANDLERS.get(op_type)
    if handler is None:
        logger.warning(f"[Outbox] {name}: 未注册的 op type {op_type}, 跳过 {op_id}")
        return
    acquired = False
    if sem is not None:
        await sem.acquire()
        acquired = True
    try:
        try:
            await handler(name, payload)
        except Exception as e:
            logger.warning(f"[Outbox] {name}/{op_type}/{op_id} 执行失败（保持 pending）: {e}")
            return
        try:
            await outbox.aappend_done(name, op_id)
        except Exception as e:
            # append_done 失败不致命：下次启动重放这个 op，handler 幂等。
            logger.warning(f"[Outbox] {name}/{op_type}/{op_id}: append_done 失败: {e}")
    finally:
        if acquired and sem is not None:
            sem.release()


async def _spawn_outbox_extract_facts(lanlan_name: str, messages: list) -> asyncio.Task:
    """把 extract_facts+feedback 背景任务登记到 outbox 并 spawn。

    登记的 payload 包含 messages_to_dict 序列化后的整轮对话，重启时可重放。
    """
    from utils.llm_client import messages_to_dict

    payload = {'messages': messages_to_dict(messages)}
    try:
        op_id = await outbox.aappend_pending(lanlan_name, OP_EXTRACT_FACTS, payload)
    except Exception as e:
        # Outbox 写失败不能阻塞主流程，降级为一次性任务（与重构前行为一致）
        logger.warning(
            f"[Outbox] {lanlan_name}: append_pending 失败，降级为内存任务: "
            f"{type(e).__name__}: {e}"
        )
        return _spawn_background_task(
            _extract_facts_and_check_feedback(messages, lanlan_name)
        )
    op = {'op_id': op_id, 'type': OP_EXTRACT_FACTS, 'payload': payload}
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

REBUTTAL_CHECK_INTERVAL = 300  # 5 分钟
REBUTTAL_FIRST_RUN_LOOKBACK_HOURS = 1  # 首次启动 / 时钟回拨兜底回扫窗口


def _extract_user_messages_from_rows(rows: list) -> list[str]:
    """从 time_indexed SQL 查询结果中提取用户消息文本。

    rows: [(session_id, message_json), ...]
    message_json 是 langchain SQLChatMessageHistory 存储的 JSON 字符串。
    content 可能是 str 或 list[{type, text}]，与 _extract_user_messages 对齐。
    """
    user_msgs = []
    for _, msg_json in rows:
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


async def _periodic_rebuttal_loop():
    """每 5 分钟检查 confirmed reflections 是否被近期对话反驳。

    通过 time_indexed SQL 查询上次检查之后的所有新对话消息，
    确保不遗漏任何未消费的用户回复。

    游标持久化（P0 修复）：`CURSOR_REBUTTAL_CHECKED_UNTIL` 写入 cursors.json，
    关机→重启后从磁盘读取，消灭"默认只回扫 1 小时导致关机期间反驳丢失"的缺陷。
    """
    while True:
        await asyncio.sleep(REBUTTAL_CHECK_INTERVAL)
        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[Rebuttal] 加载角色列表失败: {e}")
            continue

        now = datetime.now()

        async def _check_one_rebuttal(name: str):
            """单个 catgirl 的反驳检查。各角色互相独立，外层 gather 并行。
            内部对 feedbacks 仍串行 areject_promotion（同 reflection 不能并发处理）。"""
            try:
                confirmed = await reflection_engine.aget_confirmed_reflections(name)
                if not confirmed:
                    # 无 confirmed 时仍需推进游标：否则等到有新 confirmed reflection
                    # 出现后，首轮会把 cursor-now 之间积攒的全部用户消息喂给
                    # check_feedback_for_confirmed，容易把无关历史回复误判为反驳。
                    await cursor_store.aset_cursor(
                        name, CURSOR_REBUTTAL_CHECKED_UNTIL, now,
                    )
                    return

                start_time = await _resolve_rebuttal_start_time(name, now)
                rows = await time_manager.aretrieve_original_by_timeframe(
                    name, start_time, now,
                )
                if not rows:
                    await cursor_store.aset_cursor(
                        name, CURSOR_REBUTTAL_CHECKED_UNTIL, now,
                    )
                    return

                user_msgs = _extract_user_messages_from_rows(rows)
                if not user_msgs:
                    await cursor_store.aset_cursor(
                        name, CURSOR_REBUTTAL_CHECKED_UNTIL, now,
                    )
                    return

                # 复用 check_feedback 判断反驳
                feedbacks = await reflection_engine.check_feedback_for_confirmed(
                    name, confirmed, user_msgs,
                )
                if feedbacks is None:
                    # LLM 调用失败 → 不推进游标，下次重试这批消息
                    logger.warning(f"[Rebuttal] {name}: 反驳检查失败，保留游标待重试")
                    return

                # 成功才推进游标并持久化
                await cursor_store.aset_cursor(
                    name, CURSOR_REBUTTAL_CHECKED_UNTIL, now,
                )
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


AUTO_PROMOTE_CHECK_INTERVAL = 300  # 5 分钟

async def _periodic_auto_promote_loop():
    """定期执行 auto_promote_stale：pending→confirmed→promoted 状态迁移。

    PR-3 (RFC §3.9.1)：`aauto_promote_stale` 现在包含两段：
      1. 锁内 pending → confirmed (score driven)
      2. 锁外 confirmed → promoted via `_apromote_with_merge`（LLM 决策
         合并 / 独立晋升 / 拒绝；带节流防 LLM 失败 DOS）

    Per-character 用 asyncio.gather 并行——每个角色内部仍是顺序操作
    （锁串行），但跨角色可以打满。
    """
    while True:
        await asyncio.sleep(AUTO_PROMOTE_CHECK_INTERVAL)
        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[AutoPromote] 加载角色列表失败: {e}")
            continue

        async def _promote_one(name: str):
            try:
                transitions = await reflection_engine.aauto_promote_stale(name)
                if transitions:
                    logger.info(f"[AutoPromote] {name}: {transitions} 条状态迁移")
            except Exception as e:
                logger.debug(f"[AutoPromote] {name}: 处理失败: {e}")

        if catgirl_names:
            await asyncio.gather(
                *(_promote_one(name) for name in catgirl_names),
                return_exceptions=True,
            )


async def _periodic_idle_maintenance_loop():
    """定期检查系统是否空闲，空闲时自动执行记忆维护任务。

    启动阶段以 IDLE_CHECK_INTERVAL_STARTUP(10s) 高频轮询，尽快捕获启动后的
    首个空闲窗口执行维护（用户上次强制退出导致的未完成任务在这里收尾）。
    首轮维护完成或 recent_memory_auto_review 被禁用后恢复 IDLE_CHECK_INTERVAL(40s)。

    每轮为每个角色依次执行：
    1. 历史记录压缩 — 有需要就跑（history > max_history_length）
    2. Persona 矛盾审视 — 有需要就跑（pending corrections 非空，history >= 8）
    3. 记忆整理 review — review_clean 则跳过；受 REVIEW_MIN_INTERVAL 最短间隔；history < 8 跳过
    """
    startup_phase = True
    while True:
        await asyncio.sleep(IDLE_CHECK_INTERVAL_STARTUP if startup_phase else IDLE_CHECK_INTERVAL)

        # correction 被禁用 → 无需高频轮询
        if startup_phase and not await _ais_review_enabled():
            startup_phase = False

        if not _is_idle():
            continue

        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[IdleMaint] 加载角色列表失败: {e}")
            continue

        review_enabled = await _ais_review_enabled()

        for name in catgirl_names:
            # 每处理一个角色前重新检查空闲，一旦变忙立即退出
            if not _is_idle():
                logger.debug("[IdleMaint] 检测到新活动，中断本轮维护")
                break

            try:
                history = await recent_history_manager.aget_recent_history(name)
                history_len = len(history)

                # ── 子任务1: 历史记录压缩（有需要就跑，不受全局开关控制） ──
                if history_len > recent_history_manager.max_history_length:
                    logger.info(
                        f"[IdleMaint] {name}: 历史记录过长 ({history_len} > "
                        f"{recent_history_manager.max_history_length})，触发压缩"
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
                if fact_dedup_resolver is not None:
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

                # 历史不足 REVIEW_SKIP_HISTORY_LEN 条，或全局开关关闭 → 跳过矛盾审视和 review
                if history_len < REVIEW_SKIP_HISTORY_LEN or not review_enabled:
                    continue

                # ── 子任务2: Persona 矛盾审视（有需要就跑） ──
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
                if not _is_idle():
                    break
                # 已 review 且没有新对话 → 跳过
                if _is_review_clean(name):
                    continue
                # 已有 review 任务在跑 → 跳过
                if name in correction_tasks and not correction_tasks[name].done():
                    continue
                # 最短间隔限制
                last_review = _maint_state.get(name, {}).get('last_review_ts')
                if last_review:
                    try:
                        elapsed = (datetime.now() - datetime.fromisoformat(last_review)).total_seconds()
                        if elapsed < REVIEW_MIN_INTERVAL:
                            continue
                    except (ValueError, TypeError):
                        logger.debug(f"[IdleMaint] {name}: last_review_ts 格式无效，视为未 review 过")
                logger.info(f"[IdleMaint] {name}: 空闲期间执行记忆整理")
                try:
                    cancel_event = asyncio.Event()
                    correction_cancel_flags[name] = cancel_event
                    task = asyncio.create_task(_run_review_in_background(name))
                    correction_tasks[name] = task
                except Exception as e:
                    logger.warning(f"[IdleMaint] {name}: 记忆整理启动失败: {e}")

            except Exception as e:
                logger.debug(f"[IdleMaint] {name}: 处理失败，跳过: {e}")

        # 首轮维护完成 → 恢复正常轮询间隔
        if startup_phase:
            startup_phase = False
            logger.info("[IdleMaint] 启动阶段结束，恢复正常轮询间隔")


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
    """
    from memory.evidence import maybe_mark_sub_zero
    while True:
        await asyncio.sleep(EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS)
        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[ArchiveSweep] 加载角色列表失败: {e}")
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


# ── memory-evidence-rfc §3.4.3: background signal extraction loop ───

_signal_check_state: dict[str, dict] = {}  # {name: {turns_since, last_check_ts}}


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


async def _periodic_signal_extraction_loop():
    """每 EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS 轮询，满足触发条件时对每个
    catgirl 跑 Stage-1 + Stage-2 + signal dispatch（RFC §3.4.3）。"""
    while True:
        await asyncio.sleep(EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS)
        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[SignalLoop] 加载角色列表失败: {e}")
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
                    _signal_check_mark_done(name, now)
                    return

                # 组装成 BaseMessage-like 结构给 extract_facts 使用
                from utils.llm_client import convert_to_messages
                message_dicts = [
                    {'type': 'human', 'data': {'content': m}}
                    for m in user_msgs_text
                ]
                messages = convert_to_messages(json.dumps(message_dicts))

                try:
                    persisted, signals, batch_fact_ids = await fact_store.aextract_facts_and_detect_signals(
                        name, messages,
                        reflection_engine=reflection_engine,
                        persona_manager=persona_manager,
                    )
                except FactExtractionFailed as e:
                    # Stage-1 terminal failure — cursor NOT advanced, next
                    # cycle retries the same message window (§3.4.3).
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
            except Exception as e:
                logger.debug(f"[SignalLoop] {name}: 处理失败: {e}")

        if catgirl_names:
            await asyncio.gather(
                *(_signal_check_one(name) for name in catgirl_names),
                return_exceptions=True,
            )


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
            TokenTracker.get_instance().record_app_start()
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
            _memory_background_tasks_started = True

        # memory-enhancements P2: vector embedding warmup + backfill worker.
        # The worker is optional; startup should continue if vectors are
        # unavailable or its bootstrap fails.
        try:
            from memory.embedding_worker import EmbeddingWarmupWorker
            from memory.fact_dedup import FactDedupResolver
            from config import VECTORS_WARMUP_DELAY_SECONDS

            def _current_catgirl_names() -> list[str]:
                try:
                    data = _config_manager.load_characters()
                    return list((data or {}).get('猫娘', {}).keys())
                except Exception:
                    return list(catgirl_names)

            fact_dedup_resolver = FactDedupResolver(fact_store)

            embedding_warmup_worker = EmbeddingWarmupWorker(
                get_persona_manager=lambda: persona_manager,
                get_reflection_engine=lambda: reflection_engine,
                get_fact_store=lambda: fact_store,
                get_character_names=_current_catgirl_names,
                warmup_delay_seconds=VECTORS_WARMUP_DELAY_SECONDS,
                get_dedup_resolver=lambda: fact_dedup_resolver,
            )
            embedding_warmup_worker.start()
        except Exception as e:
            logger.warning(f"[Memory] embedding worker bootstrap failed: {e}")
            embedding_warmup_worker = None
            fact_dedup_resolver = None

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


async def _run_review_in_background(lanlan_name: str):
    """在后台运行review_history，支持取消"""
    global correction_tasks, correction_cancel_flags
    
    # 获取该角色的取消标志
    cancel_event = correction_cancel_flags.get(lanlan_name)
    if not cancel_event:
        cancel_event = asyncio.Event()
        correction_cancel_flags[lanlan_name] = cancel_event
    
    try:
        # 直接异步调用review_history方法
        success = await recent_history_manager.review_history(lanlan_name, cancel_event)
        if success:
            logger.info(f"✅ {lanlan_name} 的记忆整理任务完成")
            # 仅在 review 实际成功修正并保存时标记 clean + 记录时间
            state = _maint_state.setdefault(lanlan_name, {})
            state['review_clean'] = True
            state['last_review_ts'] = datetime.now().isoformat()
            await _asave_maint_state()
        else:
            logger.info(f"ℹ️ {lanlan_name} 的记忆整理未执行（被跳过或条件不满足）")
    except asyncio.CancelledError:
        logger.info(f"⚠️ {lanlan_name} 的记忆整理任务被取消")
    except Exception as e:
        logger.error(f"❌ {lanlan_name} 的记忆整理任务出错: {e}")
    finally:
        # 清理任务记录
        if lanlan_name in correction_tasks:
            del correction_tasks[lanlan_name]
        # 重置取消标志
        if lanlan_name in correction_cancel_flags:
            correction_cancel_flags[lanlan_name].clear()

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
    auto_transitions = 0
    reflection_result = None
    try:
        auto_transitions = await reflection_engine.aauto_promote_stale(lanlan_name)
    except Exception as e:
        logger.debug(f"[ReflectAPI] {lanlan_name}: auto_promote_stale 失败: {e}")
    try:
        reflection_result = await reflection_engine.reflect(lanlan_name)
    except Exception as e:
        logger.debug(f"[ReflectAPI] {lanlan_name}: reflect 失败: {e}")
    return {
        "reflection": reflection_result,
        "auto_transitions": auto_transitions,
    }


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


async def _extract_facts_and_check_feedback(messages: list, lanlan_name: str):
    """后台异步：事实提取 + 反馈检查 + 复读嗅探。失败静默跳过。

    认知框架：Facts → Reflection(pending→confirmed→promoted) → Persona
    memory-evidence-rfc 接入点：
      - 每轮 tick 给 signal-extraction loop 的 turn counter +1
      - check_feedback 的 confirmed/denied/ignored 三值分别派 evidence 信号
      - 如果 user message 命中 NEGATIVE_KEYWORDS，触发快速 LLM target check
    """
    user_msgs = _extract_user_messages(messages)

    # 本轮算入 signal-extraction 触发计数器（RFC §3.4.3）
    try:
        if user_msgs:
            _signal_check_record_turn(lanlan_name)
    except Exception as e:
        # Best-effort counter bump; a failure here only delays the next
        # signal-extraction cycle — not worth interrupting conversation flow.
        logger.debug(f"[MemoryServer] signal-check turn counter 更新失败: {e}")

    try:
        # 1. 事实提取（legacy flow；真正的 Stage-1+Stage-2 走
        #    _periodic_signal_extraction_loop。这里保留 Stage-1-only 以便
        #    短期行为不变，facts.json 在每轮对话后仍及时更新。）
        await fact_store.extract_facts(messages, lanlan_name)
    except Exception as e:
        logger.warning(f"[MemoryServer] 事实提取失败: {e}")

    try:
        # 2. 全局复读嗅探：扫描 AI 回复中是否重复提及 persona 条目 +
        #    confirmed reflection（§2.6 5h 窗口 suppress 机制，两者正交）
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

                # 让后续 score 扫描把 pending→confirmed 推进
                try:
                    await reflection_engine.aauto_promote_stale(lanlan_name)
                except Exception as e:
                    logger.debug(
                        f"[MemoryServer] {lanlan_name}: auto_promote_stale 失败: {e}"
                    )
    except Exception as e:
        logger.warning(f"[MemoryServer] 反馈检查失败: {e}")

    try:
        # 3.5 负面关键词 hook（§3.4.5）——命中就派个异步小 LLM 任务
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
        resolved = await persona_manager.resolve_corrections(lanlan_name)
        if resolved:
            logger.info(f"[MemoryServer] {lanlan_name}: 审视了 {resolved} 条 persona 矛盾")
    except Exception as e:
        logger.warning(f"[MemoryServer] 矛盾审视失败: {e}")


async def _outbox_extract_facts_handler(lanlan_name: str, payload: dict) -> None:
    """OP_EXTRACT_FACTS 的 outbox handler：从 payload 还原 messages 再跑常规流程。

    幂等性来源：
      - fact_store.extract_facts 内部靠 SHA-256 对事实去重，重复提取不会
        产生重复 fact。
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
    await _extract_facts_and_check_feedback(messages, lanlan_name)


register_outbox_handler(OP_EXTRACT_FACTS, _outbox_extract_facts_handler)


@app.post("/cache/{lanlan_name}")
async def cache_conversation(request: HistoryRequest, lanlan_name: str):
    """轻量级缓存：仅将新消息追加到 recent history，不触发 time_manager / review 等 LLM 操作。
    供 cross_server 在每轮 turn end 时调用，保持 memory_browser 实时可见。"""
    lanlan_name = validate_lanlan_name(lanlan_name)
    _touch_activity()
    try:
        input_history = convert_to_messages(json.loads(request.input_history))
        if not input_history:
            return {"status": "cached", "count": 0}
        if _has_human_messages(input_history):
            await _aclear_review_clean(lanlan_name)
        logger.info(f"[MemoryServer] cache: {lanlan_name} +{len(input_history)} 条消息")
        async with _get_settle_lock(lanlan_name):
            await recent_history_manager.update_history(input_history, lanlan_name, compress=False)
        return {"status": "cached", "count": len(input_history)}
    except Exception as e:
        logger.error(f"[MemoryServer] cache 失败: {e}")
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
        await _spawn_outbox_extract_facts(lanlan_name, input_history)

        # 在后台启动review_history任务
        if lanlan_name in correction_tasks and not correction_tasks[lanlan_name].done():
            # 如果已有任务在运行，取消它
            correction_tasks[lanlan_name].cancel()
            try:
                await correction_tasks[lanlan_name]
            except asyncio.CancelledError:
                pass
        
        # 启动新的review任务
        task = asyncio.create_task(_run_review_in_background(lanlan_name))
        correction_tasks[lanlan_name] = task
        
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
        await _spawn_outbox_extract_facts(lanlan_name, input_history)

        # 在后台启动review_history任务
        if lanlan_name in correction_tasks and not correction_tasks[lanlan_name].done():
            # 如果已有任务在运行，取消它
            correction_tasks[lanlan_name].cancel()
            try:
                await correction_tasks[lanlan_name]
            except asyncio.CancelledError:
                pass
        
        # 启动新的review任务
        task = asyncio.create_task(_run_review_in_background(lanlan_name))
        correction_tasks[lanlan_name] = task
        
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
            await _spawn_outbox_extract_facts(lanlan_name, input_history)

        if lanlan_name in correction_tasks and not correction_tasks[lanlan_name].done():
            correction_tasks[lanlan_name].cancel()
            try:
                await correction_tasks[lanlan_name]
            except asyncio.CancelledError:
                pass
        task = asyncio.create_task(_run_review_in_background(lanlan_name))
        correction_tasks[lanlan_name] = task

        return {"status": "settled"}
    except Exception as e:
        logger.error(f"[MemoryServer] settle 失败: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@app.get("/get_recent_history/{lanlan_name}")
async def get_recent_history(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    # 检查角色是否存在于配置中
    try:
        character_data = await _config_manager.aload_characters()
        catgirl_names = list(character_data.get('猫娘', {}).keys())
        if lanlan_name not in catgirl_names:
            logger.warning(f"角色 '{lanlan_name}' 不在配置中，返回空历史记录")
            return "开始聊天前，没有历史记录。\n"
    except Exception as e:
        logger.error(f"检查角色配置失败: {e}")
        return "开始聊天前，没有历史记录。\n"

    history = await recent_history_manager.aget_recent_history(lanlan_name)
    _, _, _, _, name_mapping, _, _, _, _ = await _config_manager.aget_character_data()
    name_mapping['ai'] = lanlan_name
    result = f"开始聊天前，{lanlan_name}又在脑海内整理了近期发生的事情。\n"
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
    """语义记忆已退环境，返回空结果占位。"""
    lanlan_name = validate_lanlan_name(lanlan_name)
    _lang = get_global_language()
    return (
        _loc(MEMORY_RECALL_HEADER, _lang).format(name=lanlan_name)
        + query
        + "\n\n"
        + _loc(MEMORY_RESULTS_HEADER, _lang).format(name=lanlan_name)
        + "\n（语义记忆已下线，暂无相关记忆片段。）"
    )

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
    global correction_tasks, correction_cancel_flags

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

    # 等待 /renew 或 /settle 的首轮摘要完成，确保读到最新数据
    async with _get_settle_lock(lanlan_name):
        # 中断正在进行的correction任务
        if lanlan_name in correction_tasks and not correction_tasks[lanlan_name].done():
            logger.info(f"🛑 收到new_dialog请求，中断 {lanlan_name} 的correction任务")

            if lanlan_name in correction_cancel_flags:
                correction_cancel_flags[lanlan_name].set()

            correction_tasks[lanlan_name].cancel()
            try:
                await correction_tasks[lanlan_name]
            except asyncio.CancelledError:
                logger.info(f"✅ {lanlan_name} 的correction任务已成功中断")
            except Exception as e:
                logger.warning(f"⚠️ 中断 {lanlan_name} 的correction任务时出现异常: {e}")

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
