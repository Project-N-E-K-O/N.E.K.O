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
import atexit
import asyncio
import copy
import functools
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.request
import urllib.error
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
# 辅助函数
# ---------------------------------------------------------------------------

def _deep_copy_day(day: dict) -> dict:
    """深拷贝一天的统计数据。"""
    return copy.deepcopy(day)


def _merge_day_stats(target: dict, source: dict):
    """将 source 的统计数据累加到 target 中（原地修改 target）。"""
    for k in ("total_prompt_tokens", "total_completion_tokens", "total_tokens",
              "cached_tokens", "call_count", "error_count"):
        target[k] = target.get(k, 0) + source.get(k, 0)

    # by_model
    t_bm = target.setdefault("by_model", {})
    for model, bucket in source.get("by_model", {}).items():
        if model not in t_bm:
            t_bm[model] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                           "cached_tokens": 0, "call_count": 0}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "call_count"):
            t_bm[model][k] = t_bm[model].get(k, 0) + bucket.get(k, 0)

    # by_call_type
    t_bt = target.setdefault("by_call_type", {})
    for ct, bucket in source.get("by_call_type", {}).items():
        if ct not in t_bt:
            t_bt[ct] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                        "cached_tokens": 0, "call_count": 0}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "call_count"):
            t_bt[ct][k] = t_bt[ct].get(k, 0) + bucket.get(k, 0)


# ---------------------------------------------------------------------------
# 跨进程文件锁（O_CREAT | O_EXCL 方式，跨平台）
# ---------------------------------------------------------------------------

@contextmanager
def _file_lock(lock_path: Path, timeout: float = 10.0):
    """基于文件系统的跨进程互斥锁。

    使用 O_CREAT | O_EXCL 原子创建锁文件，确保同一时刻只有一个进程持有锁。
    锁文件中写入 PID + 时间戳，用于超时后检测过期锁。
    """
    fd = -1
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            # 写入 PID 便于调试
            os.write(fd, f"{os.getpid()},{time.time()}".encode())
            break
        except (FileExistsError, PermissionError, OSError):
            # 检测过期锁（持有超过 30 秒视为进程崩溃后的残留）
            try:
                lock_age = time.time() - os.path.getmtime(str(lock_path))
                if lock_age > 30:
                    try:
                        os.unlink(str(lock_path))
                    except OSError:
                        pass
                    continue
            except OSError:
                pass

            if time.monotonic() >= deadline:
                logger.warning("Token tracker: file lock timeout, force removing stale lock")
                try:
                    os.unlink(str(lock_path))
                except OSError:
                    time.sleep(0.1)
                continue

            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        for _retry in range(3):
            try:
                os.unlink(str(lock_path))
                break
            except OSError:
                if _retry < 2:
                    time.sleep(0.05)


# ---------------------------------------------------------------------------
# 远程遥测上报配置（参考 vLLM DO_NOT_TRACK 机制）
#
# 设计与 vLLM 一致：秘钥和地址硬编码在源码中，无需用户配置环境变量。
# HMAC 不是为了防止逆向（代码本身可读），而是防止随机噪声和简单伪造。
# ---------------------------------------------------------------------------

# ★ 发版前修改：遥测服务器地址。为空则不上报。
_TELEMETRY_SERVER_URL = "http://project-neko.cn:8099"

if _TELEMETRY_SERVER_URL and not _TELEMETRY_SERVER_URL.startswith(("http://", "https://")):
    logger.warning("Token tracker: invalid telemetry URL scheme, disabling remote reporting")
    _TELEMETRY_SERVER_URL = ""

# ★ 发版前修改：HMAC 签名密钥（与 server.py 中的 HMAC_SECRET 保持一致）
_TELEMETRY_HMAC_SECRET = "neko-v1-a3f8b2c1d4e5f6789012345678abcdef"  # noqa: S105

# Opt-out 开关（标准 DO_NOT_TRACK 约定，用户可自行设置）
_DO_NOT_TRACK = any(
    os.getenv(v, "").strip() in ("1", "true", "yes")
    for v in ("NEKO_DO_NOT_TRACK", "DO_NOT_TRACK")
)

# 上报间隔（3 分钟）
# 节流设计：
#   record() → 即时写入内存（零 I/O）
#   save()   → 每 60s 本地落盘，然后调用 _report_to_server()
#   _report_to_server() → 仅当距上次上报 ≥ 180s 时才真正发 HTTP
#   所以每个进程最多每 3 分钟发一次请求。3 个 server 进程 = 60 req/h/device。
#   20k DAU × 60 × 8h = 9.6M req/day ≈ 110 req/s peak → SQLite WAL (~500 w/s) 仍够用
_TELEMETRY_REPORT_INTERVAL = 180

# 上报超时
_TELEMETRY_TIMEOUT = 10  # 秒


def _get_anonymous_device_id() -> str:
    """生成匿名设备指纹。

    算法：SHA256(machine_uuid + install_salt)
    - uuid.getnode(): MAC 地址整数（稳定的硬件标识）
    - install_salt: 安装目录路径的哈希（防跨应用关联）
    - 结果为 64 字符十六进制字符串，不可逆

    参考 vLLM: 只用硬件/系统信息生成匿名 ID，不含用户 PII。
    """
    import uuid as _uuid
    import platform

    try:
        machine_id = str(_uuid.getnode())
    except Exception:
        machine_id = platform.node()

    # 用安装路径作为 salt，同一机器不同安装目录会产生不同 ID
    install_salt = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    raw = f"{machine_id}|{install_salt}|neko-telemetry"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _compute_telemetry_signature(payload_json: str, timestamp: float) -> str:
    """计算遥测上报的 HMAC-SHA256 签名。"""
    body_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    message = f"{timestamp}|{body_hash}"
    return hmac.new(
        _TELEMETRY_HMAC_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------------------
# TokenTracker 单例
# ---------------------------------------------------------------------------

class TokenTracker:
    """线程安全 + 多进程安全的全局 LLM token 用量追踪器。

    设计：
    - 所有进程共享单个 token_usage.json 文件
    - 内存中只追踪"尚未落盘的增量"（delta）
    - save() 使用文件锁做 read-merge-write，保证多进程不丢数据
    - get_stats() 读磁盘 + 合并内存 delta，不做任何文件删除
    """

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

        # 尚未落盘的增量数据（save 成功后清空）
        self._delta_daily: dict = {}
        self._delta_records: deque = deque(maxlen=200)

        # 持久化控制
        self._save_interval = 60  # 秒
        self._dirty = False
        self._save_task: Optional[asyncio.Task] = None

        # 远程遥测上报
        self._device_id: str = ""  # 延迟生成
        self._last_report_time: float = 0.0
        self._report_interval = _TELEMETRY_REPORT_INTERVAL
        self._unsent_daily: dict = {}  # 尚未成功上报到服务器的增量
        self._unsent_records: list = []

        # 首次启动：迁移旧版 per-instance 文件
        self._migrate_legacy_files()

        # 恢复上次未成功上报的远程数据
        self._load_unsent_queue()

        # atexit 兜底：不管进程如何退出（SIGTERM / 异常 / 正常结束），都尝试保存
        # 注意：SIGKILL (kill -9) 无法被拦截，此时最多丢 60s 数据
        atexit.register(self._atexit_save)

    # ---- 存储路径 ----

    @property
    def _storage_path(self) -> Path:
        return self._config_manager.config_dir / "token_usage.json"

    @property
    def _lock_file_path(self) -> Path:
        return self._config_manager.config_dir / ".token_usage.lock"

    @property
    def _storage_dir(self) -> Path:
        return self._config_manager.config_dir

    @property
    def _unsent_queue_path(self) -> Path:
        """远程上报未发送队列的持久化文件。

        进程被 kill 时 _unsent_daily 会丢失（纯内存）。
        通过将队列写到这个文件，重启后可以恢复并重发。
        """
        return self._config_manager.config_dir / ".telemetry_unsent.json"

    # ---- atexit / unsent 持久化 ----

    def _atexit_save(self):
        """atexit 兜底：进程退出前尽最后努力保存。

        覆盖场景：SIGTERM / 未捕获异常 / 正常退出 / sys.exit()
        不覆盖：SIGKILL (kill -9) / 断电 — 此时最多丢 60s 数据
        """
        try:
            self.save()
        except Exception:
            pass

    def _load_unsent_queue(self):
        """启动时加载上次未成功上报的远程数据。"""
        if _DO_NOT_TRACK or not _TELEMETRY_SERVER_URL:
            return
        try:
            p = self._unsent_queue_path
            if not p.exists():
                return
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            loaded_daily = data.get("daily", {})
            loaded_records = data.get("records", [])
            if loaded_daily:
                with self._lock:
                    for day_key, day_val in loaded_daily.items():
                        if day_key not in self._unsent_daily:
                            self._unsent_daily[day_key] = day_val
                        else:
                            _merge_day_stats(self._unsent_daily[day_key], day_val)
                    self._unsent_records.extend(loaded_records)
                    if len(self._unsent_records) > 200:
                        self._unsent_records = self._unsent_records[-200:]
                logger.debug(f"Token tracker: loaded {len(loaded_daily)} days of unsent telemetry from disk")
            # 加载成功后删除文件，避免下次重复加载
            p.unlink(missing_ok=True)
        except Exception as e:
            logger.debug(f"Token tracker: failed to load unsent queue: {e}")

    def _save_unsent_queue(self):
        """将当前未发送的远程数据持久化到磁盘。

        调用时机：
        1. save() 成功后，如果有 unsent 数据等待远程上报
        2. atexit 兜底时（通过 save → _report_to_server → 失败 → 持久化）
        """
        if _DO_NOT_TRACK or not _TELEMETRY_SERVER_URL:
            return
        try:
            with self._lock:
                if not self._unsent_daily:
                    # 无数据，清理残留文件
                    self._unsent_queue_path.unlink(missing_ok=True)
                    return
                data = {
                    "daily": copy.deepcopy(self._unsent_daily),
                    "records": list(self._unsent_records[-200:]),
                    "saved_at": time.time(),
                }
            atomic_write_json(self._unsent_queue_path, data)
        except Exception as e:
            logger.debug(f"Token tracker: failed to persist unsent queue: {e}")

    # ---- 旧版文件迁移 ----

    def _migrate_legacy_files(self):
        """将旧版 token_usage_{instance_id}.json 文件合并到新的单文件中。

        只在首次实例化时执行一次。迁移完成后删除旧文件。
        """
        try:
            legacy_files = list(self._storage_dir.glob("token_usage_*.json"))
            if not legacy_files:
                return

            logger.info(f"Token tracker: migrating {len(legacy_files)} legacy per-instance files")

            with _file_lock(self._lock_file_path):
                # 读取现有的合并文件（如果已存在）
                existing = self._load_file(self._storage_path)
                if not existing:
                    existing = self._empty_file_data()

                for p in legacy_files:
                    try:
                        data = self._load_file(p)
                        if data:
                            for day_key, day_val in data.get("daily_stats", {}).items():
                                if day_key not in existing["daily_stats"]:
                                    existing["daily_stats"][day_key] = day_val
                                else:
                                    _merge_day_stats(existing["daily_stats"][day_key], day_val)
                            existing["recent_records"].extend(data.get("recent_records", []))
                        # 迁移完毕，删除旧文件
                        p.unlink(missing_ok=True)
                    except Exception as e:
                        logger.debug(f"Token tracker: failed to migrate {p.name}: {e}")

                # 去重 recent_records
                existing["recent_records"] = self._dedupe_records(existing["recent_records"])
                existing["last_saved"] = datetime.now().isoformat()

                self._storage_dir.mkdir(parents=True, exist_ok=True)
                atomic_write_json(self._storage_path, existing)

            logger.info("Token tracker: legacy file migration complete")
        except Exception as e:
            logger.warning(f"Token tracker: legacy migration failed (non-critical): {e}")

    # ---- 记录 ----

    def record(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cached_tokens: int = 0,
        call_type: str = "unknown",
        source: str = "",
        success: bool = True,
    ):
        """记录一次 LLM 调用的 token 用量。线程安全。

        数据先写入内存中的 delta，由 periodic save 定期落盘。

        Args:
            prompt_tokens: 总 prompt tokens（含 cached 部分）
            completion_tokens: 生成 tokens
            total_tokens: prompt + completion
            cached_tokens: prompt 中被缓存命中的部分（OpenAI prompt_tokens_details.cached_tokens）
        """
        model = model or "unknown"
        prompt_tokens = prompt_tokens or 0
        completion_tokens = completion_tokens or 0
        total_tokens = total_tokens or 0
        cached_tokens = cached_tokens or 0

        today = date.today().isoformat()

        rec = {
            "ts": time.time(),
            "model": model,
            "pt": prompt_tokens,
            "ct": completion_tokens,
            "tt": total_tokens,
            "cch": cached_tokens,
            "type": call_type,
            "src": source,
            "ok": success,
        }

        with self._lock:
            if today not in self._delta_daily:
                self._delta_daily[today] = self._empty_day()

            day = self._delta_daily[today]
            day["total_prompt_tokens"] += prompt_tokens
            day["total_completion_tokens"] += completion_tokens
            day["total_tokens"] += total_tokens
            day["cached_tokens"] += cached_tokens
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
            b["cached_tokens"] += cached_tokens
            b["call_count"] += 1

            # by_call_type
            bt = day["by_call_type"]
            if call_type not in bt:
                bt[call_type] = self._empty_bucket()
            c = bt[call_type]
            c["prompt_tokens"] += prompt_tokens
            c["completion_tokens"] += completion_tokens
            c["total_tokens"] += total_tokens
            c["cached_tokens"] += cached_tokens
            c["call_count"] += 1

            self._delta_records.append(rec)
            self._dirty = True

    # ---- 查询 ----

    def get_stats(self, days: int = 7) -> dict:
        """返回最近 N 天的用量统计。

        读取磁盘文件 + 合并内存中尚未落盘的 delta，不做任何文件修改。
        """
        # 读磁盘（atomic_write_json 保证文件一致性，无需文件锁）
        disk_data = self._load_file(self._storage_path)
        if not disk_data:
            disk_data = self._empty_file_data()

        merged_daily = disk_data.get("daily_stats", {})
        all_records = disk_data.get("recent_records", [])

        # 合并内存中未落盘的 delta
        with self._lock:
            for day_key, day_delta in self._delta_daily.items():
                if day_key not in merged_daily:
                    merged_daily[day_key] = _deep_copy_day(day_delta)
                else:
                    _merge_day_stats(merged_daily[day_key], day_delta)
            all_records = all_records + list(self._delta_records)

        # 按 days 过滤
        today = date.today()
        daily = {}
        for i in range(days):
            d = (today - timedelta(days=i)).isoformat()
            if d in merged_daily:
                daily[d] = merged_daily[d]

        # 去重 recent_records
        unique_records = self._dedupe_records(all_records)

        return {
            "daily_stats": daily,
            "recent_records": unique_records[-20:],
        }

    def get_today_stats(self) -> dict:
        """返回今日用量统计。"""
        disk_data = self._load_file(self._storage_path)
        if not disk_data:
            disk_data = self._empty_file_data()

        today = date.today().isoformat()
        merged = disk_data.get("daily_stats", {}).get(today, self._empty_day())

        # 合并内存 delta
        with self._lock:
            if today in self._delta_daily:
                _merge_day_stats(merged, self._delta_daily[today])

        return {"date": today, "stats": merged}

    # ---- 持久化 ----

    def save(self):
        """持久化增量数据到磁盘。多进程安全。

        流程：
        1. 线程锁内取出 delta 快照并清空（swap 模式）
        2. 文件锁内做 read-merge-write
        3. 如果写入失败，将 delta 放回内存
        """
        with self._lock:
            if not self._dirty:
                return
            # 取出 delta（swap 模式：先取出，成功后不放回）
            delta_daily = self._delta_daily
            delta_records = list(self._delta_records)
            self._delta_daily = {}
            self._delta_records.clear()
            self._dirty = False

        try:
            self._storage_dir.mkdir(parents=True, exist_ok=True)

            with _file_lock(self._lock_file_path):
                # 读取现有数据
                existing = self._load_file(self._storage_path)
                if not existing:
                    existing = self._empty_file_data()

                # 合并 delta 到 existing
                for day_key, day_delta in delta_daily.items():
                    if day_key not in existing["daily_stats"]:
                        existing["daily_stats"][day_key] = day_delta
                    else:
                        _merge_day_stats(existing["daily_stats"][day_key], day_delta)

                # 合并 recent_records
                existing["recent_records"].extend(delta_records)
                existing["recent_records"] = self._dedupe_records(existing["recent_records"])

                # 清理 90 天前的旧数据
                cutoff = (date.today() - timedelta(days=90)).isoformat()
                old_keys = [k for k in existing["daily_stats"] if k < cutoff]
                for k in old_keys:
                    del existing["daily_stats"][k]

                existing["last_saved"] = datetime.now().isoformat()
                atomic_write_json(self._storage_path, existing)

            # 本地保存成功后，尝试远程上报（在文件锁外，避免阻塞其他进程）
            try:
                self._report_to_server(delta_daily, delta_records)
            except Exception:
                pass  # 远程上报失败不影响本地保存，静默忽略

        except Exception as e:
            logger.warning(f"Failed to save token usage data: {e}")
            # 写入失败，将 delta 放回内存，下次重试
            with self._lock:
                for day_key, day_delta in delta_daily.items():
                    if day_key not in self._delta_daily:
                        self._delta_daily[day_key] = day_delta
                    else:
                        _merge_day_stats(self._delta_daily[day_key], day_delta)
                # 恢复 records（旧的在前，新的在后）
                restored = delta_records + list(self._delta_records)
                self._delta_records.clear()
                self._delta_records.extend(restored[-200:])
                self._dirty = True

    # ---- 远程遥测上报 ----

    def _report_to_server(self, delta_daily: dict, delta_records: list):
        """将增量数据上报到远程遥测服务器。

        防丢数据设计：
        - _unsent_daily 累积在内存中，同时持久化到 .telemetry_unsent.json
        - 进程被 kill 后重启时，_load_unsent_queue() 恢复未发送数据
        - 发送成功后清除 unsent 队列文件
        - 发送失败后放回内存 + 持久化，下次重试
        """
        if _DO_NOT_TRACK or not _TELEMETRY_SERVER_URL:
            return

        # 累积 unsent 数据
        with self._lock:
            for day_key, day_delta in delta_daily.items():
                if day_key not in self._unsent_daily:
                    self._unsent_daily[day_key] = copy.deepcopy(day_delta)
                else:
                    _merge_day_stats(self._unsent_daily[day_key], day_delta)
            self._unsent_records.extend(delta_records)
            if len(self._unsent_records) > 200:
                self._unsent_records = self._unsent_records[-200:]

        # 持久化 unsent 队列（防 kill 丢数据）
        self._save_unsent_queue()

        # 检查上报间隔
        now = time.time()
        if now - self._last_report_time < self._report_interval:
            return

        # 取出待发送数据
        with self._lock:
            if not self._unsent_daily:
                return
            send_daily = self._unsent_daily
            send_records = self._unsent_records
            self._unsent_daily = {}
            self._unsent_records = []

        try:
            if not self._device_id:
                self._device_id = _get_anonymous_device_id()

            try:
                from config import EDITION_INFO
                app_version = EDITION_INFO.get("edition", "unknown") if EDITION_INFO else "unknown"
            except Exception:
                app_version = "unknown"

            payload = {
                "device_id": self._device_id,
                "app_version": app_version,
                "daily_stats": send_daily,
                "recent_records": send_records,
            }
            payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)

            ts = time.time()
            sig = _compute_telemetry_signature(payload_json, ts)

            batch_id = hashlib.sha256(payload_json.encode()).hexdigest()[:32]
            submission = {
                "timestamp": ts,
                "signature": sig,
                "payload": payload,
                "batch_id": batch_id,
            }
            body = json.dumps(submission, ensure_ascii=False).encode("utf-8")

            req = urllib.request.Request(
                f"{_TELEMETRY_SERVER_URL}/api/v1/telemetry",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=_TELEMETRY_TIMEOUT) as resp:
                if resp.status == 200:
                    self._last_report_time = now
                    # 发送成功，删除 unsent 队列文件
                    self._unsent_queue_path.unlink(missing_ok=True)
                    logger.debug("Token tracker: telemetry reported successfully")
                    return

            raise Exception(f"HTTP {resp.status}")

        except Exception as e:
            logger.debug(f"Token tracker: telemetry report failed (non-critical): {e}")
            # 发送失败，放回 unsent 数据 + 持久化
            with self._lock:
                for day_key, day_delta in send_daily.items():
                    if day_key not in self._unsent_daily:
                        self._unsent_daily[day_key] = day_delta
                    else:
                        _merge_day_stats(self._unsent_daily[day_key], day_delta)
                restored = send_records + self._unsent_records
                self._unsent_records = restored[-200:]
            self._save_unsent_queue()

    @staticmethod
    def _load_file(path: Path) -> dict:
        """从文件加载数据，返回空 dict 表示文件无效或不存在。"""
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("version") == 1:
                    return data
        except Exception:
            pass
        return {}

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
                await asyncio.to_thread(self.save)

    # ---- helpers ----

    @staticmethod
    def _empty_day() -> dict:
        return {
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "call_count": 0,
            "error_count": 0,
            "by_model": {},
            "by_call_type": {},
        }

    @staticmethod
    def _empty_bucket() -> dict:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "cached_tokens": 0, "call_count": 0}

    @staticmethod
    def _empty_file_data() -> dict:
        return {"version": 1, "daily_stats": {}, "recent_records": [], "last_saved": ""}

    @staticmethod
    def _dedupe_records(records: list, max_keep: int = 200) -> list:
        """对 recent_records 去重 + 排序 + 截断。"""
        seen = set()
        unique = []
        for r in records:
            key = (r.get("ts"), r.get("model"), r.get("type"), r.get("src"))
            if key not in seen:
                seen.add(key)
                unique.append(r)
        unique.sort(key=lambda x: x.get("ts", 0))
        return unique[-max_keep:]


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
    """从 OpenAI SDK response 提取 usage 并记录。

    提取字段：
    - usage.prompt_tokens: 总 prompt tokens（含 cached）
    - usage.completion_tokens: 生成 tokens
    - usage.total_tokens: 总计
    - usage.prompt_tokens_details.cached_tokens: prompt 缓存命中部分
    """
    try:
        if not hasattr(response, 'usage') or response.usage is None:
            return
        usage = response.usage
        model = getattr(response, 'model', None) or "unknown"

        # 提取 cached_tokens（OpenAI 新版 API 才有 prompt_tokens_details）
        cached_tokens = 0
        details = getattr(usage, 'prompt_tokens_details', None)
        if details is not None:
            cached_tokens = getattr(details, 'cached_tokens', 0) or 0

        TokenTracker.get_instance().record(
            model=model,
            prompt_tokens=getattr(usage, 'prompt_tokens', 0) or 0,
            completion_tokens=getattr(usage, 'completion_tokens', 0) or 0,
            total_tokens=getattr(usage, 'total_tokens', 0) or 0,
            cached_tokens=cached_tokens,
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
