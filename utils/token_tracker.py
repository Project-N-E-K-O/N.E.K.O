# -*- coding: utf-8 -*-
"""
全局 LLM Token 用量追踪模块

通过 monkey-patch OpenAI SDK 的 chat.completions.create（同步 + 异步），
自动拦截所有 LLM 调用（包括 LangChain 底层调用）的 usage 数据。
用 ContextVar 标记调用类型，确保 Nuitka/PyInstaller 兼容。

Usage:
    from utils.token_tracker import TokenTracker, install_hooks, llm_call_context

    # 启动时安装 hooks
    install_hooks()
    TokenTracker.get_instance().start_periodic_save()

    # 在调用模块标记 call_type
    with llm_call_context("conversation"):
        async for chunk in llm.astream(messages):
            ...
"""
import asyncio
import copy
import functools
import json
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

# ---------------------------------------------------------------------------
# ContextVar: 调用类型标记（替代 stack inspection，Nuitka/PyInstaller 兼容）
# ---------------------------------------------------------------------------

_current_call_type: ContextVar[str] = ContextVar('_llm_call_type', default='unknown')


@contextmanager
def llm_call_context(call_type: str):
    """Context manager，在代码块内标记当前 LLM 调用类型。"""
    token = _current_call_type.set(call_type)
    try:
        yield
    finally:
        _current_call_type.reset(token)


def set_call_type(call_type: str):
    """简单设置当前调用类型（适用于不方便 wrap 的场景）。"""
    _current_call_type.set(call_type)


# ---------------------------------------------------------------------------
# 多进程合并辅助函数
# ---------------------------------------------------------------------------

def _deep_copy_day(day: dict) -> dict:
    """深拷贝一天的统计数据。"""
    return copy.deepcopy(day)


def _merge_day_stats(target: dict, source: dict):
    """将 source 的统计数据累加到 target 中（原地修改 target）。"""
    for k in ("total_prompt_tokens", "total_completion_tokens", "total_tokens", "call_count", "error_count"):
        target[k] = target.get(k, 0) + source.get(k, 0)

    # by_model
    t_bm = target.setdefault("by_model", {})
    for model, bucket in source.get("by_model", {}).items():
        if model not in t_bm:
            t_bm[model] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens", "call_count"):
            t_bm[model][k] = t_bm[model].get(k, 0) + bucket.get(k, 0)

    # by_call_type
    t_bt = target.setdefault("by_call_type", {})
    for ct, bucket in source.get("by_call_type", {}).items():
        if ct not in t_bt:
            t_bt[ct] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens", "call_count"):
            t_bt[ct][k] = t_bt[ct].get(k, 0) + bucket.get(k, 0)


# ---------------------------------------------------------------------------
# TokenTracker 单例
# ---------------------------------------------------------------------------

class TokenTracker:
    """线程安全的全局 LLM token 用量追踪器。"""

    _instance: Optional['TokenTracker'] = None
    _init_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'TokenTracker':
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._lock = threading.Lock()
        self._config_manager = get_config_manager()
        self._instance_id = uuid.uuid4().hex[:8]

        # 按日聚合统计（仅本进程本次启动的数据）
        self._daily_stats: dict = {}
        # 近期明细（ring buffer）
        self._recent_records: deque = deque(maxlen=200)

        # 持久化控制
        self._save_interval = 60  # 秒
        self._dirty = False
        self._save_task: Optional[asyncio.Task] = None

    # ---- 存储路径 ----

    @property
    def _storage_path(self) -> Path:
        return self._config_manager.config_dir / f"token_usage_{self._instance_id}.json"

    @property
    def _storage_dir(self) -> Path:
        return self._config_manager.config_dir

    # ---- 记录 ----

    def record(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        call_type: str = "unknown",
        source: str = "",
        success: bool = True,
    ):
        """记录一次 LLM 调用的 token 用量。线程安全。"""
        model = model or "unknown"
        prompt_tokens = prompt_tokens or 0
        completion_tokens = completion_tokens or 0
        total_tokens = total_tokens or 0

        today = date.today().isoformat()

        rec = {
            "ts": time.time(),
            "model": model,
            "pt": prompt_tokens,
            "ct": completion_tokens,
            "tt": total_tokens,
            "type": call_type,
            "src": source,
            "ok": success,
        }

        with self._lock:
            # 确保当日统计存在
            if today not in self._daily_stats:
                self._daily_stats[today] = self._empty_day()

            day = self._daily_stats[today]
            day["total_prompt_tokens"] += prompt_tokens
            day["total_completion_tokens"] += completion_tokens
            day["total_tokens"] += total_tokens
            day["call_count"] += 1
            if not success:
                day["error_count"] += 1

            # by_model
            bm = day["by_model"]
            if model not in bm:
                bm[model] = self._empty_bucket()
            b = bm[model]
            b["prompt_tokens"] += prompt_tokens
            b["completion_tokens"] += completion_tokens
            b["total_tokens"] += total_tokens
            b["call_count"] += 1

            # by_call_type
            bt = day["by_call_type"]
            if call_type not in bt:
                bt[call_type] = self._empty_bucket()
            c = bt[call_type]
            c["prompt_tokens"] += prompt_tokens
            c["completion_tokens"] += completion_tokens
            c["total_tokens"] += total_tokens
            c["call_count"] += 1

            self._recent_records.append(rec)
            self._dirty = True

    # ---- 查询 ----

    def _merge_all_files(self) -> tuple[dict, list]:
        """读取所有 token_usage_*.json 文件并合并，返回 (merged_daily, merged_records)。"""
        merged_daily: dict = {}
        all_records: list = []

        # 先合并本进程的内存数据
        with self._lock:
            for day_key, day_val in self._daily_stats.items():
                merged_daily[day_key] = _deep_copy_day(day_val)
            all_records.extend(self._recent_records)

        # 再合并磁盘上其他进程的文件（包括旧版 token_usage.json）
        try:
            # 兼容旧版单文件格式
            old_file = self._storage_dir / "token_usage.json"
            if old_file.exists():
                data = self._load_file(old_file)
                if data:
                    for day_key, day_val in data.get("daily_stats", {}).items():
                        if day_key not in merged_daily:
                            merged_daily[day_key] = day_val
                        else:
                            _merge_day_stats(merged_daily[day_key], day_val)
                    all_records.extend(data.get("recent_records", []))
                # 迁移完毕后删除旧文件
                try:
                    old_file.unlink(missing_ok=True)
                except Exception:
                    pass

            for p in self._storage_dir.glob("token_usage_*.json"):
                file_id = p.stem.replace("token_usage_", "")
                if file_id == self._instance_id:
                    continue  # 跳过自己的文件，已用内存数据
                data = self._load_file(p)
                if not data:
                    # 清理无效或过期的文件（修改时间超过 24 小时）
                    try:
                        age = time.time() - p.stat().st_mtime
                        if age > 86400:
                            p.unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue
                # 检查文件是否过期（超过 24 小时未更新 = 进程已退出）
                last_saved = data.get("last_saved", "")
                try:
                    saved_time = datetime.fromisoformat(last_saved)
                    if (datetime.now() - saved_time).total_seconds() > 86400:
                        p.unlink(missing_ok=True)
                        continue
                except Exception:
                    pass

                for day_key, day_val in data.get("daily_stats", {}).items():
                    if day_key not in merged_daily:
                        merged_daily[day_key] = day_val
                    else:
                        _merge_day_stats(merged_daily[day_key], day_val)
                all_records.extend(data.get("recent_records", []))
        except Exception as e:
            logger.warning(f"Failed to merge token usage files: {e}")

        # 去重 + 排序 recent_records
        seen = set()
        unique_records = []
        for r in all_records:
            key = (r.get("ts"), r.get("model"), r.get("type"), r.get("src"))
            if key not in seen:
                seen.add(key)
                unique_records.append(r)
        unique_records.sort(key=lambda x: x.get("ts", 0))

        return merged_daily, unique_records[-200:]

    def get_stats(self, days: int = 7) -> dict:
        """返回最近 N 天的用量统计（合并所有进程的数据）。"""
        merged_daily, merged_records = self._merge_all_files()
        today = date.today()
        daily = {}
        for i in range(days):
            d = (today - timedelta(days=i)).isoformat()
            if d in merged_daily:
                daily[d] = merged_daily[d]
        return {
            "daily_stats": daily,
            "recent_records": merged_records[-20:],
        }

    def get_today_stats(self) -> dict:
        """返回今日用量统计（合并所有进程的数据）。"""
        merged_daily, _ = self._merge_all_files()
        today = date.today().isoformat()
        return {
            "date": today,
            "stats": merged_daily.get(today, self._empty_day()),
        }

    # ---- 持久化 ----

    def save(self):
        """持久化当前状态到磁盘。线程安全。

        每个进程写自己的 token_usage_{instance_id}.json 文件，
        避免多进程写同一文件导致的数据覆盖问题。
        """
        with self._lock:
            if not self._dirty:
                return
            data = {
                "version": 1,
                "instance_id": self._instance_id,
                "last_saved": datetime.now().isoformat(),
                "daily_stats": self._daily_stats,
                "recent_records": list(self._recent_records),
            }
            self._dirty = False

        try:
            self._config_manager.config_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self._storage_path, data)
        except Exception as e:
            logger.warning(f"Failed to save token usage data: {e}")

    @staticmethod
    def _load_file(path: Path) -> dict:
        """从单个文件加载数据。"""
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("version") == 1:
                    return data
        except Exception:
            pass
        return {}

    def _prune_old_days(self, max_days: int = 90):
        """清理超过 max_days 的旧数据。"""
        cutoff = (date.today() - timedelta(days=max_days)).isoformat()
        old_keys = [k for k in self._daily_stats if k < cutoff]
        for k in old_keys:
            del self._daily_stats[k]

    # ---- 定时保存 ----

    def start_periodic_save(self):
        """启动后台定时保存任务。需在 asyncio loop 内调用。"""
        if self._save_task is None or self._save_task.done():
            self._save_task = asyncio.create_task(self._periodic_save_loop())
            logger.info("Token tracker periodic save started")

    async def _periodic_save_loop(self):
        while True:
            await asyncio.sleep(self._save_interval)
            if self._dirty:
                self.save()

    # ---- helpers ----

    @staticmethod
    def _empty_day() -> dict:
        return {
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "call_count": 0,
            "error_count": 0,
            "by_model": {},
            "by_call_type": {},
        }

    @staticmethod
    def _empty_bucket() -> dict:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0}


# ---------------------------------------------------------------------------
# OpenAI SDK Monkey-patch
# ---------------------------------------------------------------------------

# Streaming 不兼容 stream_options 的 base_url 缓存
_stream_options_blocklist: set = set()
_blocklist_lock = threading.Lock()


def _get_base_url(self_obj) -> str:
    """从 OpenAI client 实例提取 base_url。"""
    try:
        # self_obj 是 Completions / AsyncCompletions，其 _client 是 OpenAI / AsyncOpenAI
        client = getattr(self_obj, '_client', None)
        if client is None:
            return ""
        base_url = getattr(client, 'base_url', None)
        if base_url is None:
            return ""
        return str(base_url).rstrip('/')
    except Exception:
        return ""


def _record_usage_from_response(response, call_type: str):
    """从 OpenAI SDK response 提取 usage 并记录。"""
    try:
        if not hasattr(response, 'usage') or response.usage is None:
            return
        usage = response.usage
        model = getattr(response, 'model', None) or "unknown"
        TokenTracker.get_instance().record(
            model=model,
            prompt_tokens=getattr(usage, 'prompt_tokens', 0) or 0,
            completion_tokens=getattr(usage, 'completion_tokens', 0) or 0,
            total_tokens=getattr(usage, 'total_tokens', 0) or 0,
            call_type=call_type,
        )
    except Exception:
        pass


def _should_inject_stream_options(base_url: str) -> bool:
    """检查该 base_url 是否在 blocklist 中。"""
    if not base_url:
        return True
    with _blocklist_lock:
        return base_url not in _stream_options_blocklist


def _add_to_blocklist(base_url: str):
    """将不支持 stream_options 的 base_url 加入 blocklist。"""
    if base_url:
        with _blocklist_lock:
            _stream_options_blocklist.add(base_url)
        logger.info(f"Token tracker: added base_url to stream_options blocklist: {base_url[:60]}...")


def install_hooks():
    """
    安装 OpenAI SDK monkey-patch，自动追踪所有 chat.completions.create 调用的 token 用量。
    同时覆盖 LangChain 底层调用（因为 LangChain ChatOpenAI 底层调用 OpenAI SDK）。
    """
    try:
        from openai.resources.chat.completions import Completions, AsyncCompletions
    except ImportError:
        logger.warning("Token tracker: openai package not found, hooks not installed")
        return

    _original_create = Completions.create
    _original_async_create = AsyncCompletions.create

    @functools.wraps(_original_create)
    def patched_create(self, *args, **kwargs):
        call_type = _current_call_type.get('unknown')
        is_stream = kwargs.get('stream', False)

        if is_stream:
            return _handle_sync_stream(self, _original_create, args, kwargs, call_type)

        try:
            result = _original_create(self, *args, **kwargs)
            _record_usage_from_response(result, call_type)
            return result
        except Exception as e:
            TokenTracker.get_instance().record(
                model=kwargs.get('model', 'unknown'),
                prompt_tokens=0, completion_tokens=0, total_tokens=0,
                call_type=call_type, success=False,
            )
            raise

    @functools.wraps(_original_async_create)
    async def patched_async_create(self, *args, **kwargs):
        call_type = _current_call_type.get('unknown')
        is_stream = kwargs.get('stream', False)

        if is_stream:
            return await _handle_async_stream(self, _original_async_create, args, kwargs, call_type)

        try:
            result = await _original_async_create(self, *args, **kwargs)
            _record_usage_from_response(result, call_type)
            return result
        except Exception as e:
            TokenTracker.get_instance().record(
                model=kwargs.get('model', 'unknown'),
                prompt_tokens=0, completion_tokens=0, total_tokens=0,
                call_type=call_type, success=False,
            )
            raise

    Completions.create = patched_create
    AsyncCompletions.create = patched_async_create
    logger.info("Token tracker: OpenAI SDK hooks installed")


# ---------------------------------------------------------------------------
# Streaming wrappers
# ---------------------------------------------------------------------------

def _handle_sync_stream(self_obj, original_fn, args, kwargs, call_type):
    """处理同步 streaming 调用：注入 stream_options + wrap Stream。"""
    base_url = _get_base_url(self_obj)
    injected = False

    # 尝试注入 stream_options
    if _should_inject_stream_options(base_url) and 'stream_options' not in kwargs:
        kwargs['stream_options'] = {"include_usage": True}
        injected = True

    try:
        result = original_fn(self_obj, *args, **kwargs)
        return _SyncStreamWrapper(result, call_type)
    except Exception as e:
        if injected:
            # stream_options 导致报错，去掉后重试
            _add_to_blocklist(base_url)
            kwargs.pop('stream_options', None)
            try:
                result = original_fn(self_obj, *args, **kwargs)
                return _SyncStreamWrapper(result, call_type)
            except Exception:
                TokenTracker.get_instance().record(
                    model=kwargs.get('model', 'unknown'),
                    prompt_tokens=0, completion_tokens=0, total_tokens=0,
                    call_type=call_type, success=False,
                )
                raise
        TokenTracker.get_instance().record(
            model=kwargs.get('model', 'unknown'),
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            call_type=call_type, success=False,
        )
        raise


async def _handle_async_stream(self_obj, original_fn, args, kwargs, call_type):
    """处理异步 streaming 调用：注入 stream_options + wrap AsyncStream。"""
    base_url = _get_base_url(self_obj)
    injected = False

    if _should_inject_stream_options(base_url) and 'stream_options' not in kwargs:
        kwargs['stream_options'] = {"include_usage": True}
        injected = True

    try:
        result = await original_fn(self_obj, *args, **kwargs)
        return _AsyncStreamWrapper(result, call_type)
    except Exception as e:
        if injected:
            _add_to_blocklist(base_url)
            kwargs.pop('stream_options', None)
            try:
                result = await original_fn(self_obj, *args, **kwargs)
                return _AsyncStreamWrapper(result, call_type)
            except Exception:
                TokenTracker.get_instance().record(
                    model=kwargs.get('model', 'unknown'),
                    prompt_tokens=0, completion_tokens=0, total_tokens=0,
                    call_type=call_type, success=False,
                )
                raise
        TokenTracker.get_instance().record(
            model=kwargs.get('model', 'unknown'),
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            call_type=call_type, success=False,
        )
        raise


class _SyncStreamWrapper:
    """Wrap 同步 Stream，在迭代结束后提取 usage。"""

    def __init__(self, stream, call_type: str):
        self._stream = stream
        self._call_type = call_type

    def __iter__(self):
        for chunk in self._stream:
            # 最后一个 chunk（带 usage）
            if hasattr(chunk, 'usage') and chunk.usage is not None:
                _record_usage_from_response(chunk, self._call_type)
            yield chunk

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def __enter__(self):
        if hasattr(self._stream, '__enter__'):
            self._stream.__enter__()
        return self

    def __exit__(self, *args):
        if hasattr(self._stream, '__exit__'):
            return self._stream.__exit__(*args)


class _AsyncStreamWrapper:
    """Wrap 异步 AsyncStream，在迭代结束后提取 usage。"""

    def __init__(self, stream, call_type: str):
        self._stream = stream
        self._call_type = call_type

    def __aiter__(self):
        return self._aiter_and_track()

    async def _aiter_and_track(self):
        async for chunk in self._stream:
            if hasattr(chunk, 'usage') and chunk.usage is not None:
                _record_usage_from_response(chunk, self._call_type)
            yield chunk

    def __getattr__(self, name):
        return getattr(self._stream, name)

    async def __aenter__(self):
        if hasattr(self._stream, '__aenter__'):
            await self._stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        if hasattr(self._stream, '__aexit__'):
            return await self._stream.__aexit__(*args)
